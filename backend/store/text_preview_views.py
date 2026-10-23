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
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import text_preview_draft, text_preview_match
from .admin_views import (
    CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES,
    _company_context,
)
from .throttles import AdminOrdersThrottle


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
