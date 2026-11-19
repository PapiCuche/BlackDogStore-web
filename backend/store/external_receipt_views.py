"""
NOTE-NOT-SUBSTITUTE — the receipt of a sale issued outside the system.

    GET  /api/admin/orders/{pk}/external-receipt/             sales.fiscal.view
    POST /api/admin/orders/{pk}/external-receipt/             sales.fiscal.issue
    POST /api/admin/orders/{pk}/external-receipt/{id}/void/   sales.fiscal.issue

Same authority and scope as the system's own receipts (`fiscal_views`): the
fiscal capabilities, no legacy role bridge, and the order resolved through
`visible_orders()` — an order of another company or of a branch the caller
does not operate answers 404, like one that does not exist.

Nothing here talks to SUNAT or claims acceptance (`external_receipts`).
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import external_receipts, receipt_coverage
from .fiscal_views import CAP_FISCAL_ISSUE, CAP_FISCAL_VIEW, _fiscal_order
from .models import ExternalFiscalReceipt
from .tenancy import has_capability
from .throttles import FiscalIssueThrottle, FiscalReadThrottle


def _body(request, order, company) -> dict:
    rows = (ExternalFiscalReceipt.objects.filter(order=order, company=company)
            .select_related('order', 'registered_by', 'voided_by'))
    coverage = receipt_coverage.coverage(order)
    may_issue = has_capability(request.user, company, CAP_FISCAL_ISSUE)
    return {
        'registrations': [external_receipts.payload(row) for row in rows],
        'coverage': coverage,
        # Only a sale pending its receipt takes a registration; the server
        # decides again on POST.
        'can_register': may_issue and coverage['state'] == receipt_coverage.PENDING,
        'can_void': may_issue,
        'document_types': [{'value': v, 'label': l} for v, l in ExternalFiscalReceipt.DOCUMENT_TYPE_CHOICES],
        'channels': [{'value': v, 'label': l} for v, l in ExternalFiscalReceipt.Channel.choices],
        'notice': ('Registra aquí la boleta o factura que emitiste fuera del sistema. El sistema no la '
                   'verifica en SUNAT: la venta queda como «comprobante externo registrado».'),
    }


class AdminOrderExternalReceiptView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [FiscalIssueThrottle()] if self.request.method == 'POST' else [FiscalReadThrottle()]

    def get(self, request, pk):
        company, order, error = _fiscal_order(request, pk, CAP_FISCAL_VIEW)
        if error:
            return error
        return Response(_body(request, order, company))

    def post(self, request, pk):
        company, order, error = _fiscal_order(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error
        try:
            external_receipts.register(order, request.data, actor=request.user, request=request)
        except external_receipts.ExternalReceiptError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        order.refresh_from_db()
        return Response(_body(request, order, company), status=status.HTTP_201_CREATED)


class AdminOrderExternalReceiptVoidView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk, receipt_pk):
        company, order, error = _fiscal_order(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error
        row = ExternalFiscalReceipt.objects.filter(pk=receipt_pk, order=order, company=company).first()
        if row is None:
            return Response({'detail': 'No se encontró ese registro.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            reason = request.data.get('reason') if hasattr(request.data, 'get') else None
            external_receipts.void(row, reason, actor=request.user, request=request)
        except external_receipts.ExternalReceiptError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        order.refresh_from_db()
        return Response(_body(request, order, company))
