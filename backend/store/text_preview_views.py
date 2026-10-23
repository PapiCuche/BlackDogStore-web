"""
INV-TEXT-02 — POST /api/admin/inventory/text-preview/match/: which real variant
each candidate of the local text preview is. Read-only (`products.view`),
company-scoped, nothing written or audited; costs, quantities and identifiers
are refused, never carried.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import text_preview_match
from .admin_views import CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES, _company_context
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
