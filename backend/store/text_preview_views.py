"""
INV-TEXT-02 — POST /api/admin/inventory/text-preview/match/: which real variant
each candidate of the local text preview is. Read-only (`products.view`),
company-scoped, nothing written or audited; costs, quantities and identifiers
are refused, never carried.

INV-TEXT-03 — POST …/text-preview/offer-draft/ (and …/file/): the import draft
of supplier offers as a DRY RUN of the official preview (`text_preview_draft`).
`products.manage` like that preview; a complete list needs the whole company in
scope; a supplier or product of another company is not found (404). Nothing is
written, staged or audited; no AI.

INV-FAST-B1 — POST …/text-preview/catalog-proposal/ (what the rows lack: models, options, variants; read-only,
`products.view`) and …/catalog-proposal/apply/ (creates ONLY what was approved, atomically and idempotently;
`products.manage` + `inventory.adjust`, because a created variant is counted by unit). See `catalog_proposal`.

INV-FAST-B2 — POST …/text-preview/units/preview/: the draft rows as JSON, staged by the OFFICIAL device importer
(`unit_receive_draft`). `inventory.adjust`; applying is the official `…/units/import/<id>/apply/`.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from django.core.exceptions import ValidationError as DjangoValidationError

from . import (
    catalog_proposal, device_catalog, product_codes, stock_unit_services, text_preview_draft, text_preview_match,
    unit_receive_draft, xlsx_reader,
)
from .admin_views import (
    CAP_INVENTORY_ADJUST, CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_ADJUST_INVENTORY_ROLES,
    _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES, _company_context,
)
from .throttles import AdminImportThrottle, AdminOrdersThrottle, AdminProductWriteThrottle


class AdminTextPreviewMatchView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        body = request.data if isinstance(request.data, dict) else {}
        try:
            matches = text_preview_match.match(company, body.get('candidates'))
        except text_preview_match.BadCandidate as exc:
            return Response({'detail': str(exc), 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'matches': matches})


class AdminTextPreviewOfferDraftView(APIView):
    """The draft's answer: what the official preview would stage, and nothing staged."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]
    as_file = False

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        from . import supplier_offer_import, xlsx_reader
        from .cost_services import user_may_view_costs
        from .tenancy import has_company_wide_scope

        try:
            draft = text_preview_draft.clean(company, request.data,
                                             with_cost=user_may_view_costs(request.user, company))
        except text_preview_draft.BadDraft as exc:
            return Response({'detail': str(exc), 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)
        except text_preview_draft.NotFound:
            return Response({'detail': 'No encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        if draft['complete'] and not has_company_wide_scope(request.user, company):
            # As the official preview: a complete list is the whole company's.
            return Response({'detail': 'Una lista completa es de toda la empresa: tu acceso está limitado a '
                                       'algunas sucursales.'}, status=status.HTTP_403_FORBIDDEN)
        if self.as_file:
            from .import_views import _xlsx

            return _xlsx(text_preview_draft.workbook_bytes(company, draft), text_preview_draft.FILENAME)
        try:
            payload = text_preview_draft.dry_run(company, request.user, draft)
        except (supplier_offer_import.OfferImportError, xlsx_reader.XlsxError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class AdminTextPreviewOfferDraftFileView(AdminTextPreviewOfferDraftView):
    """The same draft as the official template, to upload in «Ofertas de proveedores» (which previews and applies)."""
    as_file = True


class AdminCatalogProposalView(APIView):
    """What the pasted rows lack in this company's catalogue. Nothing is written."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        body = request.data if isinstance(request.data, dict) else {}
        try:
            return Response(catalog_proposal.propose(company, body.get('rows')))
        except catalog_proposal.BadProposal as exc:
            return Response(exc.body(), status=exc.status)


class AdminCatalogProposalApplyView(APIView):
    """Create the approved part of a proposal. All of it or none; a second approval creates nothing."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminProductWriteThrottle]

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        # A created variant is counted by unit: the same authority as the guided registration.
        _company, error = _company_context(request, CAP_INVENTORY_ADJUST, _LEGACY_ADJUST_INVENTORY_ROLES)
        if error:
            return error
        body = request.data if isinstance(request.data, dict) else {}
        try:
            result = catalog_proposal.apply(
                company=company, rows=body.get('rows'), approve=body.get('approve'), prices=body.get('prices'),
                category_id=body.get('category_id'), without_imei=body.get('without_imei'),
                idempotency_key=request.headers.get('Idempotency-Key') or body.get('idempotency_key') or '',
                actor=request.user, request=request,
            )
        except catalog_proposal.BadProposal as exc:
            return Response(exc.body(), status=exc.status)
        except device_catalog.CatalogError as exc:
            return Response(exc.body(), status=404 if exc.code == 'not_found' else 400)
        except product_codes.ProductCodeError as exc:
            return Response(exc.body('code'), status=exc.status)
        except (stock_unit_services.StockUnitError, DjangoValidationError) as exc:
            return Response({'detail': str(exc.messages[0] if hasattr(exc, 'messages') else exc), 'code': 'invalid'},
                            status=400)
        return Response(result)


class AdminUnitReceiveDraftView(APIView):
    """The reviewed list as the official device preview: a staged job, each row with the key it came with."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminImportThrottle]
    http_method_names = ['post', 'options']

    def post(self, request):
        from . import unit_import_services
        from .import_views import _job_payload, _context as stock_context, CAP_STOCK
        from .inventory_views import may_view_costs
        from .tenancy import visible_branches

        company, error = stock_context(request, CAP_STOCK)
        if error:
            return error
        not_found = Response({'detail': 'No encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        costs = may_view_costs(request)
        body = request.data if isinstance(request.data, dict) else {}
        branches = {b.pk: b for b in visible_branches(request.user, company)}
        try:
            branch_id = int(body.get('branch'))
        except (TypeError, ValueError):
            return Response({'detail': 'Indica la sucursal del ingreso.', 'code': 'invalid'},
                            status=status.HTTP_400_BAD_REQUEST)
        # Not this company's, or not one of theirs: the same answer as the official preview.
        if branch_id not in branches:
            return not_found
        try:
            draft = unit_receive_draft.clean(company, body, with_cost=costs)
        except unit_receive_draft.BadDraft as exc:
            return Response({'detail': str(exc), 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)
        except unit_receive_draft.NotFound:
            return not_found
        try:
            job = unit_receive_draft.stage(
                company=company, actor=request.user, branch=branches[branch_id],
                reachable_branch_ids=set(branches), draft=draft)
        except (xlsx_reader.XlsxError, unit_import_services.UnitImportError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payload = _job_payload(job, rows=True, limit=len(draft['rows']), include_cost=costs)
        keys = {index + 2: item['key'] for index, item in enumerate(draft['rows'])}
        by_key = {item['key']: draft['products'][item['product_id']] for item in draft['rows']}
        for item in payload['rows']:
            item['key'] = keys.get(item['row'])
            # A device under a variant without a price registers fine but cannot be sold or published until priced.
            product = by_key.get(item['key'])
            if product is not None and product.price <= 0:
                item['warnings'] = [*item['warnings'], 'Sin precio: la variante no tiene precio de venta; '
                                    'configúralo antes de venderla o publicarla.']
        payload['cost_column_ignored'] = bool(draft['cost_asked'] and not costs)
        return Response(payload, status=status.HTTP_201_CREATED)
