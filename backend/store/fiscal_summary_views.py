"""
La superficie interna del Resumen Diario de Boletas. Backend, sin frontend aún.

QUÉ HACE Y QUÉ NO
-----------------
Listar, ver, generar, enviar y consultar resúmenes. NO acepta una lista de ids de
documentos desde el cliente (§59): el backend SELECCIONA las boletas elegibles por
un criterio determinista (empresa + fecha + tipo + firmadas + no informadas). El
cliente sólo dice para qué fecha, no qué documentos.

Generar y enviar CAMBIAN estado y salen a SUNAT: exigen `sales.fiscal.issue`
(§56). Listar y ver son de consulta: `sales.fiscal.view`.
"""

from __future__ import annotations

import logging
from datetime import date

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .fiscal_config import (
    FiscalConfigError, resolve_credentials, resolve_environment, resolve_provider,
)
from .fiscal_services import FiscalError
from .fiscal_summary_services import (
    FiscalSummaryInProgress, generate_daily_summaries, poll_daily_summary,
    sign_daily_summary, submit_daily_summary,
)
from .fiscal_views import CAP_FISCAL_ISSUE, CAP_FISCAL_VIEW, _NO_LEGACY_BRIDGE
from .inventory_views import _company_context
from .models import AdminAuditLog, FiscalDailySummary
from .throttles import FiscalIssueThrottle, FiscalReadThrottle

logger = logging.getLogger(__name__)


def summary_payload(summary: FiscalDailySummary) -> dict:
    """Los metadatos del resumen. Sin el XML, sin el CDR, sin secretos."""
    return {
        'id': summary.pk,
        'identifier': summary.identifier,
        'correlativo': summary.correlativo,
        'reference_date': summary.reference_date,
        'issue_date': summary.issue_date,
        'environment': summary.environment,
        'status': summary.status,
        'status_label': summary.get_status_display(),
        'ticket': summary.ticket,
        'lines': summary.lines.count(),
        'response_code': summary.sunat_response_code,
        'response_message': summary.sunat_response_message,
        'is_accepted': summary.is_accepted,
        'has_xml': bool(summary.signed_xml),
        'has_cdr': bool(summary.cdr_xml),
        'created_at': summary.created_at,
    }


def _summary(request, pk, capability):
    """El resumen, dentro del tenant de quien llama. Ajeno = inexistente."""
    company, error = _company_context(request, capability, _NO_LEGACY_BRIDGE)
    if error:
        return None, None, error
    summary = FiscalDailySummary.objects.filter(company=company, pk=pk).first()
    if summary is None:
        return None, None, Response(
            {'detail': 'No se encontró el resumen.'},
            status=status.HTTP_404_NOT_FOUND)
    return company, summary, None


class AdminFiscalSummaryListView(APIView):
    """
    GET  /api/admin/fiscal-summaries/        — los resúmenes de la empresa.
    POST /api/admin/fiscal-summaries/        — generar (y firmar) para una fecha.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [FiscalIssueThrottle()] if self.request.method == 'POST' \
            else [FiscalReadThrottle()]

    def get(self, request):
        company, error = _company_context(request, CAP_FISCAL_VIEW, _NO_LEGACY_BRIDGE)
        if error:
            return error
        summaries = (
            FiscalDailySummary.objects.filter(company=company)
            .order_by('-reference_date', '-correlativo')[:200]
        )
        return Response([summary_payload(s) for s in summaries])

    def post(self, request):
        company, error = _company_context(request, CAP_FISCAL_ISSUE, _NO_LEGACY_BRIDGE)
        if error:
            return error

        raw = (request.data or {}).get('reference_date')
        if not raw:
            return Response(
                {'detail': 'Falta la fecha de referencia (reference_date).'},
                status=status.HTTP_400_BAD_REQUEST)
        try:
            reference_date = date.fromisoformat(str(raw))
        except ValueError:
            return Response(
                {'detail': 'La fecha de referencia debe ser AAAA-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            environment = resolve_environment()
            summaries = generate_daily_summaries(company, reference_date, environment)
            credentials = resolve_credentials(company)
            signed = [
                sign_daily_summary(
                    s, key_pem=credentials['key_pem'],
                    cert_pem=credentials['cert_pem'])
                for s in summaries
            ]
        except FiscalConfigError as exc:
            return Response(
                {'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        for s in signed:
            AdminAuditLog.log(
                actor=request.user, action='fiscal_summary_generated',
                target_type='fiscal_daily_summary', target_id=s.pk,
                metadata={'identifier': s.identifier, 'lines': s.lines.count(),
                          'reference_date': str(s.reference_date)},
                request=request, company=company)
        return Response([summary_payload(s) for s in signed],
                        status=status.HTTP_201_CREATED)


class AdminFiscalSummaryDetailView(APIView):
    """GET /api/admin/fiscal-summaries/{pk}/ — un resumen."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalReadThrottle]

    def get(self, request, pk):
        _company, summary, error = _summary(request, pk, CAP_FISCAL_VIEW)
        if error:
            return error
        return Response(summary_payload(summary))


class AdminFiscalSummarySubmitView(APIView):
    """POST /api/admin/fiscal-summaries/{pk}/submit/ — enviar (sendSummary)."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk):
        company, summary, error = _summary(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error
        try:
            provider = resolve_provider(company)
        except FiscalConfigError as exc:
            return Response(
                {'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            summary = submit_daily_summary(summary, provider)
        except FiscalSummaryInProgress as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action='fiscal_summary_submitted',
            target_type='fiscal_daily_summary', target_id=summary.pk,
            metadata={'identifier': summary.identifier, 'status': summary.status,
                      'ticket': summary.ticket, 'response_code': summary.sunat_response_code},
            request=request, company=company)
        return Response(summary_payload(summary))


class AdminFiscalSummaryStatusView(APIView):
    """POST /api/admin/fiscal-summaries/{pk}/status/ — consultar el ticket."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk):
        company, summary, error = _summary(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error
        try:
            provider = resolve_provider(company)
        except FiscalConfigError as exc:
            return Response(
                {'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            outcome = poll_daily_summary(summary, provider)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action='fiscal_summary_polled',
            target_type='fiscal_daily_summary', target_id=summary.pk,
            metadata={'identifier': outcome.summary.identifier,
                      'poll_action': outcome.action,
                      'status': outcome.summary.status,
                      'response_code': outcome.sunat_code},
            request=request, company=company)
        return Response(summary_payload(outcome.summary))
