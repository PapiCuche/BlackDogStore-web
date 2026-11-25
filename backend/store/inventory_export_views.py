"""
The inventory downloads. INVENTORY-UNIFIED-01, part 2.

    GET /api/admin/inventory/exports/<kind>/?mode=&branches=&category=&type=&stock=&condition=&visibility=&is_active=

`kind`: `products` · `stock` · `units` · `full` · `integral` · `units_new` · `units_used`. What each file holds is
in `store.inventory_exports`; this is who may ask and for what.

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

STOCK-TEMPLATE-EXPORT (owner's decision, 2026-11-24): IMEIs and serial numbers leave the server WHOLE only for
a caller who may load stock (`inventory.adjust`, the authority behind the device load and its templates). Anybody
else who reads inventory gets them hidden but for the last characters — in the device report, the file to reload,
the ZIP and the integral report alike. `units_new` / `units_used` are the two stock templates filled with the
shelf: `mode=masked` (the default) for review, `mode=full` for operations, refused (403) without `inventory.adjust`.
Every download, full or masked, leaves its audit line with who, the company, the branches and the row count.

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
from .inventory_views import (
    CAP_INVENTORY_ADJUST, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_ADJUST_ROLES, _LEGACY_INVENTORY_VIEW_ROLES,
    may_view_costs,
)
from .models import AdminAuditLog, Category, Product
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
    # STOCK-TEMPLATE-EXPORT: the shelf as the two stock templates. `mode=full` also needs `may_load_stock`.
    'units_new': (_WAREHOUSE,),
    'units_used': (_WAREHOUSE,),
}

FULL_IDENTIFIERS_FORBIDDEN = (
    'Los IMEI y las series completos sólo los descarga quien puede mover inventario. Descarga la copia de revisión '
    '(identificadores ocultos) o pide ese permiso.'
)


def may_load_stock(request) -> bool:
    """Whether this caller holds `inventory.adjust` here: the one who may take IMEIs and serials out whole."""
    _company, error = _company_context(request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES)
    return error is None


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
        template_line = inventory_exports.TEMPLATE_KINDS.get(kind)
        # INV-EXP-02: the integral report has one mode, so it is its default. A stock template's is the review copy.
        default = (inventory_exports.MODE_MASKED if template_line else
                   inventory_exports.MODE_REPORT if kind == 'integral' else inventory_exports.MODE_REIMPORT)
        mode = (params.get('mode') or default).strip().lower()
        if mode not in (inventory_exports.TEMPLATE_MODES if template_line else inventory_exports.MODES):
            return _bad('Parámetro "mode" inválido.')
        full_identifiers = may_load_stock(request)
        if template_line and mode == inventory_exports.MODE_FULL and not full_identifiers:
            return Response({'code': 'full_identifiers_forbidden', 'detail': FULL_IDENTIFIERS_FORBIDDEN},
                            status=status.HTTP_403_FORBIDDEN)
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
        if template_line:
            # The stock template fixes its line: the same line written again is harmless, another one is refused.
            own = inventory_exports.LINE_USED if template_line == Product.CONDITION_USED else inventory_exports.LINE_NEW
            if filters.lines and filters.lines != (own,):
                return _bad('Esta plantilla ya fija la línea: no lleva el parámetro "line".')
            filters.lines = (own,)
            if filters.stock == inventory_exports.STOCK_OUT:
                return _bad('La plantilla de stock sólo lleva los equipos que están en tienda.')

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
        # STOCK-TEMPLATE-EXPORT — whole identifiers only for `inventory.adjust`; a review copy never has them.
        filters.full_identifiers = full_identifiers and (not template_line or mode == inventory_exports.MODE_FULL)
        if template_line:
            # D-F-3 — the template's own rule for «Proveedor».
            from .stock_unit_views import may_see_suppliers

            filters.include_supplier = may_see_suppliers(request)

        try:
            payload, rows = inventory_exports.build(kind, mode, company, filters)
        except inventory_exports.ExportError as exc:
            return Response(exc.payload(), status=status.HTTP_400_BAD_REQUEST)

        # What was downloaded, by whom, with which filters and how many rows.
        # Never a cell: a serial or a cost has no business in the audit trail.
        metadata = {
            'kind': kind, 'mode': mode, 'filters': filters.audit(),
            'rows': rows, 'cost_included': filters.include_cost,
        }
        if kind in inventory_exports.UNIT_KINDS:
            # STOCK-TEMPLATE-EXPORT — whether IMEIs and serials left whole: `filters.branches` says from where.
            metadata['identifiers'] = 'full' if filters.full_identifiers else 'masked'
        if template_line:
            metadata['supplier_included'] = filters.include_supplier
        AdminAuditLog.log(
            actor=request.user,
            action='inventory_exported',
            target_type='inventory_export',
            metadata=metadata,
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
