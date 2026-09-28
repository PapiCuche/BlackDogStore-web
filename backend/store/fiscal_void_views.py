"""
La superficie interna de la Comunicación de Baja y del otorgamiento.

QUÉ ENTRA POR EL CUERPO, Y QUÉ NO
---------------------------------
El `pk` de la URL es el comprobante o la comunicación YA AUTORIZADOS, resueltos
dentro del tenant de quien llama. La empresa, el ambiente, la serie, el número y el
RUC se DERIVAN de ahí; del cuerpo sólo se aceptan el motivo, la clave de
idempotencia y el canal de entrega. Nada que identifique un comprobante ajeno.

En particular NO se acepta un «no otorgado» del cuerpo: eso dejaría que quien llama
autorizara su propia baja. La negativa se registra con una acción administrativa
propia, auditada y atribuida a una persona (`/not-granted/`).

LAS BANDERAS NO SON LA AUTORIDAD
--------------------------------
`can_submit`, `can_poll` y `can_recover` viajan para que la interfaz sepa qué
ofrecer, pero cada acción vuelve a comprobar estado y permiso. Un envío con
resultado INCIERTO no ofrece `can_submit`: ofrece `can_recover`, porque reenviarlo a
ciegas podría duplicar la baja en SUNAT.
"""

from __future__ import annotations

import logging

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .fiscal_config import FiscalConfigError, resolve_provider
from .fiscal_services import FiscalError
from .fiscal_views import (
    CAP_FISCAL_ISSUE, CAP_FISCAL_VIEW, _NO_LEGACY_BRIDGE, _fiscal_document,
)
from .fiscal_void_services import (
    FiscalVoidInProgress, attest_not_granted, create_void_communication,
    poll_void_communication, record_grant, sign_void_communication,
    submit_void_communication, void_deadline,
)
from .inventory_views import _company_context
from .models import (
    AdminAuditLog, FiscalGrantMethod, FiscalVoidCommunication, FiscalVoidStatus,
)
from .throttles import FiscalIssueThrottle, FiscalReadThrottle

logger = logging.getLogger(__name__)


def void_payload(void: FiscalVoidCommunication) -> dict:
    """Los metadatos de la comunicación. Sin el XML, sin el CDR, sin secretos."""
    can_submit = void.status in (
        FiscalVoidStatus.SIGNED, FiscalVoidStatus.SUBMISSION_ERROR)
    can_poll = bool(void.ticket) and void.status == FiscalVoidStatus.SUBMITTED
    can_recover = void.status == FiscalVoidStatus.SUBMISSION_UNKNOWN
    return {
        'id': void.pk,
        'identifier': void.identifier,
        'correlativo': void.correlativo,
        'reference_date': void.reference_date,
        'issue_date': void.issue_date,
        'environment': void.environment,
        'status': void.status,
        'status_label': void.get_status_display(),
        'ticket': void.ticket,
        'documents': [
            {'document_id': row.document.document_id,
             'document_type': row.document.document_type,
             'reason': row.void_reason,
             'superseded': row.superseded}
            for row in void.lines.select_related('document').order_by('line_id')
        ],
        'response_code': void.sunat_response_code,
        'response_message': void.sunat_response_message,
        'is_accepted': void.is_accepted,
        'has_xml': bool(void.signed_xml),
        'has_cdr': bool(void.cdr_xml),
        'can_submit': can_submit,
        'can_poll': can_poll,
        # Resultado incierto: investigar en SUNAT, NO reenviar.
        'can_recover': can_recover,
        'created_at': void.created_at,
    }


def _void(request, pk, capability):
    """La comunicación, dentro del tenant de quien llama. Ajena = inexistente."""
    company, error = _company_context(request, capability, _NO_LEGACY_BRIDGE)
    if error:
        return None, None, error
    void = FiscalVoidCommunication.objects.filter(company=company, pk=pk).first()
    if void is None:
        return None, None, Response(
            {'detail': 'No se encontró la comunicación de baja.'},
            status=status.HTTP_404_NOT_FOUND)
    return company, void, None


class AdminFiscalDocumentGrantView(APIView):
    """
    POST /api/admin/fiscal-documents/{pk}/grant/ — registrar que se OTORGÓ.

    Otorgar cierra la puerta de la baja, y a propósito: el artículo 14 sólo admite
    dar de baja la numeración de lo no otorgado.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk):
        document, error = _fiscal_document(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error

        body = request.data if isinstance(request.data, dict) else {}
        method = str(body.get('method', '')).strip()
        if method not in dict(FiscalGrantMethod.choices):
            return Response(
                {'detail': f'Indique el canal de entrega. Válidos: '
                           f'{", ".join(dict(FiscalGrantMethod.choices))}.'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            document = record_grant(document, actor=request.user, method=method)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action='fiscal_document_granted',
            target_type='fiscal_document', target_id=document.pk,
            metadata={'identifier': document.document_id, 'method': method},
            request=request, company=document.company)
        return Response({
            'identifier': document.document_id,
            'granted_at': document.granted_at,
            'granted_method': document.granted_method,
        })


class AdminFiscalDocumentNotGrantedView(APIView):
    """
    POST /api/admin/fiscal-documents/{pk}/not-granted/ — ATESTIGUAR que NO se otorgó.

    Es una declaración de una persona, registrada y auditada, no una bandera del
    cuerpo: quien la firma responde por ella. Sin esto ninguna baja sería elegible,
    porque la ausencia de evidencia de entrega no prueba que no se entregó.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk):
        document, error = _fiscal_document(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error

        body = request.data if isinstance(request.data, dict) else {}
        reason = str(body.get('reason', '')).strip()
        if not reason:
            return Response(
                {'detail': 'La atestación exige un motivo: una baja sin motivo '
                           'registrado no se puede auditar después.'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            document = attest_not_granted(
                document, actor=request.user, reason=reason)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action='fiscal_document_attested_not_granted',
            target_type='fiscal_document', target_id=document.pk,
            metadata={'identifier': document.document_id, 'reason': reason},
            request=request, company=document.company)
        return Response({
            'identifier': document.document_id,
            'not_granted_at': document.not_granted_at,
            'not_granted_reason': document.not_granted_reason,
            'void_deadline': void_deadline(document),
        })


class AdminFiscalDocumentVoidView(APIView):
    """
    POST /api/admin/fiscal-documents/{pk}/void/ — emitir la Comunicación de Baja.

    Emitir NO habla con SUNAT: deja la comunicación numerada y firmada. Enviarla es
    otra llamada, para que un fallo de red no se confunda con un fallo al emitir.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk):
        document, error = _fiscal_document(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error

        body = request.data if isinstance(request.data, dict) else {}
        reason = str(body.get('reason', '')).strip()
        request_key = str(body.get('request_key', '')).strip()[:64]
        if not reason:
            return Response(
                {'detail': 'La baja exige un motivo (hasta 100 caracteres).'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            void = create_void_communication(
                document.company, targets=[(document, reason)],
                request_key=request_key)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except FiscalConfigError as exc:
            return Response({'detail': str(exc)},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        creada = not void.signed_xml
        if creada:
            AdminAuditLog.log(
                actor=request.user, action='fiscal_void_created',
                target_type='fiscal_void_communication', target_id=void.pk,
                metadata={'identifier': void.identifier,
                          'target': document.document_id, 'reason': reason},
                request=request, company=void.company)
            try:
                from .fiscal_config import resolve_credentials

                creds = resolve_credentials(void.company)
                void = sign_void_communication(
                    void, key_pem=creds['key_pem'], cert_pem=creds['cert_pem'])
            except FiscalConfigError as exc:
                return Response({'detail': str(exc)},
                                status=status.HTTP_503_SERVICE_UNAVAILABLE)
            except FiscalError as exc:
                return Response({'detail': str(exc)},
                                status=status.HTTP_400_BAD_REQUEST)
            except Exception:
                logger.exception('Firma de la baja falló para %s', void.pk)
                return Response(
                    {'detail': 'No se pudo firmar la comunicación de baja.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            AdminAuditLog.log(
                actor=request.user, action='fiscal_void_signed',
                target_type='fiscal_void_communication', target_id=void.pk,
                metadata={'identifier': void.identifier,
                          'xml_sha256': void.signed_xml_sha256},
                request=request, company=void.company)

        return Response(
            void_payload(void),
            status=status.HTTP_201_CREATED if creada else status.HTTP_200_OK)


class AdminFiscalVoidDetailView(APIView):
    """GET /api/admin/fiscal-void-communications/{pk}/ — su estado."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalReadThrottle]

    def get(self, request, pk):
        _company, void, error = _void(request, pk, CAP_FISCAL_VIEW)
        if error:
            return error
        return Response(void_payload(void))


class AdminFiscalVoidSubmitView(APIView):
    """
    POST /api/admin/fiscal-void-communications/{pk}/submit/ — enviar por sendSummary.

    Devuelve un TICKET, no una aceptación: la baja se confirma consultando.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk):
        _company, void, error = _void(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error

        try:
            provider = resolve_provider(void.company)
        except FiscalConfigError as exc:
            return Response({'detail': str(exc)},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            void = submit_void_communication(void, provider)
        except FiscalVoidInProgress as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action=f'fiscal_void_{void.status}',
            target_type='fiscal_void_communication', target_id=void.pk,
            metadata={'identifier': void.identifier, 'ticket': void.ticket,
                      'response_code': void.sunat_response_code},
            request=request, company=void.company)
        return Response(void_payload(void))


class AdminFiscalVoidStatusView(APIView):
    """
    POST /api/admin/fiscal-void-communications/{pk}/status/ — consultar el ticket.

    Exige `sales.fiscal.issue` y no `.view`: puede dejar la baja ACEPTADA o
    RECHAZADA —su estado tributario— y sale a un servicio externo. Ver es mirar;
    consultar es zanjar el asunto con SUNAT.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk):
        _company, void, error = _void(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error

        try:
            provider = resolve_provider(void.company)
        except FiscalConfigError as exc:
            return Response({'detail': str(exc)},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            result = poll_void_communication(void, provider)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action='fiscal_void_polled',
            target_type='fiscal_void_communication',
            target_id=result.void_communication.pk,
            metadata={'identifier': result.void_communication.identifier,
                      'poll_action': result.action,
                      'response_code': result.sunat_code},
            request=request, company=result.void_communication.company)
        return Response(void_payload(result.void_communication))
