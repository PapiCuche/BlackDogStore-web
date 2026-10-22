"""
The inventory downloads. INVENTORY-UNIFIED-01, part 2.

    GET /api/admin/inventory/exports/<kind>/?mode=&branches=&category=&type=&stock=&condition=&visibility=&is_active=

`kind`: `products` · `stock` · `units` · `full`. What each file holds is in
`store.inventory_exports`; this is who may ask and for what.

TWO AXES, AS EVERYWHERE IN INVENTORY
------------------------------------
  · the catalogue (`products`) is the company's: `products.view`;
  · stock and devices (`stock`, `units`) are a BRANCH's: `inventory.view`, and
    only the branches the caller operates in (`tenancy.visible_branches`);
  · `full` holds all three, so it needs both.

Reading is what these capabilities are for. `products.manage` and
`inventory.adjust` do not open a download on their own. DC1: whether any cost
column carries data is decided by `costs.view` and nothing else — it used to
borrow `inventory.adjust`, while the unit list showed the cost to anybody with
`inventory.view`; now both answer to the same capability.

The company is the caller's (`admin_views._company_context`). A branch or a
category named in the query is looked up INSIDE that company and inside the
caller's branches; one that is another tenant's, or out of reach, answers 404
exactly like one that does not exist.
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import inventory_exports
from .admin_views import CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES, _company_context
from .inventory_views import CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, may_view_costs
from .models import AdminAuditLog, Category
from .tenancy import visible_branches
from .throttles import AdminExportThrottle, ExportThrottled

_NOT_FOUND = 'No encontrado.'

#: kind -> what the caller must hold, each with the roles that stand for it on
#: the legacy bridge.
_CATALOGUE = (CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
_WAREHOUSE = (CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES)
_REQUIRED = {
    'products': (_CATALOGUE,),
    'stock': (_WAREHOUSE,),
    'units': (_WAREHOUSE,),
    'full': (_CATALOGUE, _WAREHOUSE),
    # INV-EXP-02: own stock, devices and supplier availability, for reading only.
    'integral': (_CATALOGUE, _WAREHOUSE),
}


def _bad(detail):
    return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)


def _not_found():
    return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)


class AdminInventoryExportsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    # Its own budget, and a small one: a download builds a whole workbook in the
    # one server process. It does not spend the budget of the loads.
    throttle_classes = [AdminExportThrottle]
    http_method_names = ['get', 'options']

    def throttled(self, request, wait):
        # In Spanish, and saying when to come back: a person reads this.
        raise ExportThrottled(wait)

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if isinstance(exc, ExportThrottled):
            # `Retry-After` is already on the response; the body repeats the
            # seconds so the panel does not have to parse a sentence.
            response.data = {'code': 'throttled', 'detail': str(exc.detail), 'retry_after': exc.wait}
        return response

    def get(self, request, kind):
        if kind not in inventory_exports.KINDS:
            return _not_found()

        company = None
        for capability, legacy_roles in _REQUIRED[kind]:
            company, error = _company_context(request, capability, legacy_roles)
            if error:
                return error

        params = request.query_params
        # INV-EXP-02: the integral report has one mode, so it is its default.
        default = inventory_exports.MODE_REPORT if kind == 'integral' else inventory_exports.MODE_REIMPORT
        mode = (params.get('mode') or default).strip().lower()
        if mode not in inventory_exports.MODES:
            return _bad('Parámetro "mode" inválido.')
        if kind == 'full' and mode != inventory_exports.MODE_REIMPORT:
            return _bad('El inventario completo sólo se descarga para volver a cargar.')
        if kind == 'integral' and mode != inventory_exports.MODE_REPORT:
            return _bad('El reporte integral es sólo para consultar: no se vuelve a cargar.')

        filters = inventory_exports.ExportFilters()
        for name, allowed in inventory_exports.FILTER_CHOICES.items():
            value = (params.get(name) or '').strip().lower()
            if value and value not in allowed:
                return _bad(f'Parámetro "{name}" inválido.')
            setattr(filters, name, value)

        # INV-EXP-01: `line=new,used` — a list of known lines, each once; anything else is refused.
        raw_lines = (params.get('line') or '').strip().lower()
        if raw_lines:
            parts = raw_lines.split(',')
            if any(part not in inventory_exports.LINES for part in parts) or len(set(parts)) != len(parts):
                return _bad('Parámetro "line" inválido.')
            filters.lines = tuple(parts)

        raw_category = (params.get('category') or '').strip()
        if raw_category:
            if not raw_category.isascii() or not raw_category.isdigit() or len(raw_category) > 9:
                return _bad('Parámetro "category" inválido.')
            # Of THIS company. Another tenant's is simply not there.
            filters.category = Category.objects.filter(company=company, pk=int(raw_category)).first()
            if filters.category is None:
                return _not_found()

        if kind != 'products':
            allowed = list(visible_branches(request.user, company))
            raw_branches = (params.get('branches') or '').strip()
            if raw_branches:
                parts = [part.strip() for part in raw_branches.split(',') if part.strip()]
                if not parts or any(not part.isascii() or not part.isdigit() or len(part) > 9 for part in parts):
                    return _bad('Parámetro "branches" inválido.')
                wanted = {int(part) for part in parts}
                filters.branches = [branch for branch in allowed if branch.pk in wanted]
                # Not this company's, or not one of theirs: the same answer.
                if len(filters.branches) != len(wanted):
                    return _not_found()
            else:
                filters.branches = allowed
            if not filters.branches:
                return _bad('No tienes acceso a ninguna sucursal.')

        # DC1 — what a thing cost is for who holds `costs.view`.
        filters.include_cost = may_view_costs(request)

        try:
            payload, rows = inventory_exports.build(kind, mode, company, filters)
        except inventory_exports.ExportError as exc:
            return Response(exc.payload(), status=status.HTTP_400_BAD_REQUEST)

        # What was downloaded, by whom, with which filters and how many rows.
        # Never a cell: a serial or a cost has no business in the audit trail.
        AdminAuditLog.log(
            actor=request.user,
            action='inventory_exported',
            target_type='inventory_export',
            metadata={
                'kind': kind, 'mode': mode, 'filters': filters.audit(),
                'rows': rows, 'cost_included': filters.include_cost,
            },
            request=request,
            company=company,
        )

        response = HttpResponse(
            payload,
            content_type=(
                inventory_exports.ZIP_CONTENT_TYPE if kind == 'full' else inventory_exports.XLSX_CONTENT_TYPE
            ),
        )
        response['Content-Disposition'] = f'attachment; filename="{inventory_exports.FILENAMES[(kind, mode)]}"'
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
