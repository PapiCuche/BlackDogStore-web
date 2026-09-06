"""
La superficie interna de la factura electrónica.

QUÉ SALE Y QUÉ NO
-----------------
El JSON de detalle NO lleva el XML. Un comprobante firmado son varios kilobytes
de base64 y una firma criptográfica: meterlo en la respuesta de una lista lo
convertiría en algo que se copia, se recorta y se pega. Los artefactos tienen su
propia ruta, con su cabecera y su nombre.

Tampoco salen la Clave SOL, el certificado, la clave privada, el sobre SOAP ni
la cabecera WS-Security. No hay ningún camino desde aquí hasta ellos: viven en la
configuración del servidor y sólo los ve `fiscal_config`.

EL BACKEND MANDA
----------------
`can_submit`, `can_retry` y `can_download_pdf` viajan en la respuesta para que la
interfaz sepa qué ofrecer, pero **no son la autoridad**: cada acción vuelve a
comprobar el estado y el permiso. Ocultar un botón es cortesía, no seguridad.
"""

from __future__ import annotations

import logging

from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .fiscal_config import FiscalConfigError, resolve_provider
from .fiscal_services import (
    FiscalError, FiscalSubmissionInProgress, get_or_create_fiscal_document,
    sign_fiscal_document, submit_fiscal_document,
)
from .inventory_views import _company_context
from .models import AdminAuditLog, FiscalDocument, FiscalDocumentStatus, Order
from .throttles import FiscalIssueThrottle, FiscalReadThrottle

logger = logging.getLogger(__name__)

#: Ver el estado de un comprobante y descargar sus archivos.
CAP_FISCAL_VIEW = 'sales.fiscal.view'
#: Emitir, firmar, enviar y reintentar. Declarar algo ante SUNAT.
CAP_FISCAL_ISSUE = 'sales.fiscal.issue'

#: NINGÚN ROL HEREDADO CONCEDE ESTO. El concepto no existía antes de esta fase,
#: así que no hay un `admin` histórico que «siempre pudo facturar». La lista
#: vacía es deliberada y no un olvido.
_NO_LEGACY_BRIDGE: tuple[str, ...] = ()


def _fiscal_order(request, pk, capability):
    """
    La venta sobre la que actúa esta petición, dentro del tenant de quien llama.

    Un pedido de otra empresa responde exactamente igual que uno inexistente:
    un 403 confirmaría que ese identificador existe.
    """
    company, error = _company_context(request, capability, _NO_LEGACY_BRIDGE)
    if error:
        detail = error.data.get('detail')
        if detail and 'permiso' in str(detail).lower():
            error.data['detail'] = 'No tienes permisos sobre comprobantes electrónicos.'
        return None, None, error

    order = Order.objects.filter(company=company, pk=pk).first()
    if order is None:
        return None, None, Response(
            {'detail': 'No se encontró el pedido.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return company, order, None


def _fiscal_document(request, pk, capability):
    """El comprobante, con el mismo criterio de tenant que la venta."""
    company, error = _company_context(request, capability, _NO_LEGACY_BRIDGE)
    if error:
        return None, error
    document = (
        FiscalDocument.objects.filter(company=company, pk=pk)
        .select_related('order', 'series_ref').first()
    )
    if document is None:
        return None, Response(
            {'detail': 'No se encontró el comprobante.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return document, None


def document_payload(document: FiscalDocument) -> dict:
    """
    Los metadatos del comprobante. Sin XML, sin CDR, sin secretos.

    Los importes van como CADENAS. Son `Decimal` en la base de datos y siguen
    siéndolo hasta la pantalla: entregarlos como números JSON invita al navegador
    a hacer aritmética que no coincidirá con el papel.
    """
    puede_enviar = document.status in (
        FiscalDocumentStatus.GENERATED, FiscalDocumentStatus.SIGNED,
    )
    puede_reintentar = document.status == FiscalDocumentStatus.SUBMISSION_ERROR
    return {
        'id': document.pk,
        'order_id': document.order_id,
        'document_type': document.document_type,
        'document_type_label': document.get_document_type_display(),
        'identifier': document.document_id,
        'series': document.series,
        'number': document.number,
        'environment': document.environment,
        'environment_label': document.get_environment_display(),
        'status': document.status,
        'status_label': document.get_status_display(),
        'issued_at': document.issued_at,
        'currency': document.currency,
        'taxable_amount': str(document.taxable_amount),
        'tax_amount': str(document.tax_amount),
        'total': str(document.total),
        'customer_doc_number': document.customer_doc_number,
        'customer_legal_name': document.customer_legal_name,
        # El código y el mensaje de SUNAT ya vienen saneados por el adaptador.
        'response_code': document.sunat_response_code,
        'response_message': document.sunat_response_message,
        'is_accepted': document.is_accepted,
        'has_xml': bool(document.signed_xml),
        'has_cdr': bool(document.cdr_xml),
        'attempts': document.attempts.count(),
        # Pistas para la interfaz. El backend las vuelve a comprobar.
        'can_submit': puede_enviar,
        'can_retry': puede_reintentar,
        'can_download_pdf': document.is_accepted,
    }


class AdminOrderFiscalDocumentView(APIView):
    """
    GET  /api/admin/orders/{pk}/fiscal-document/ — el comprobante de esta venta.
    POST /api/admin/orders/{pk}/fiscal-document/ — emitirlo. Idempotente.

    Emitir NO toca el pago ni el inventario, y NO habla con SUNAT: deja el
    documento numerado y firmado. Enviarlo es otra llamada, para que un fallo de
    red no se confunda con un fallo al emitir.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [FiscalIssueThrottle()] if self.request.method == 'POST' \
            else [FiscalReadThrottle()]

    def get(self, request, pk):
        _company, order, error = _fiscal_order(request, pk, CAP_FISCAL_VIEW)
        if error:
            return error
        document = (
            FiscalDocument.objects.filter(order=order).order_by('-pk').first()
        )
        if document is None:
            return Response(
                {'detail': 'Esta venta todavía no tiene comprobante electrónico.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(document_payload(document))

    def post(self, request, pk):
        company, order, error = _fiscal_order(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error

        try:
            document, created = get_or_create_fiscal_document(order)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if created:
            AdminAuditLog.log(
                actor=request.user, action='fiscal_document_created',
                target_type='fiscal_document', target_id=document.pk,
                metadata={
                    'identifier': document.document_id,
                    'order_id': order.pk,
                    'environment': document.environment,
                    'series_id': document.series_ref_id,
                },
                request=request, company=company,
            )

        # Firmar aquí y no en el envío: si el certificado falla, el usuario se
        # entera al emitir y no cuando ya hay un correlativo esperando.
        if not document.signed_xml:
            try:
                from .fiscal_config import resolve_credentials

                credentials = resolve_credentials(company)
                document = sign_fiscal_document(
                    document, key_pem=credentials['key_pem'],
                    cert_pem=credentials['cert_pem'],
                )
            except FiscalConfigError as exc:
                return Response(
                    {'detail': str(exc)},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            except Exception:
                logger.exception('Fiscal signing failed for %s', document.pk)
                return Response(
                    {'detail': 'No se pudo firmar el comprobante.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            AdminAuditLog.log(
                actor=request.user, action='fiscal_document_signed',
                target_type='fiscal_document', target_id=document.pk,
                metadata={
                    'identifier': document.document_id,
                    # El hash, NO el XML: una bitácora no es un archivo de
                    # documentos, y un comprobante entero por entrada la haría
                    # ilegible además de pesada.
                    'xml_sha256': document.signed_xml_sha256,
                },
                request=request, company=company,
            )

        return Response(
            document_payload(document),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AdminFiscalDocumentSubmitView(APIView):
    """
    POST /api/admin/fiscal-documents/{pk}/submit/ — enviar o reintentar.

    El MISMO documento, la misma serie, el mismo correlativo y el mismo XML. Un
    reintento no emite nada nuevo: sólo añade un intento.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk):
        document, error = _fiscal_document(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error

        if document.is_accepted:
            # Reenviar algo ya aceptado sólo consigue que SUNAT lo rechace por
            # duplicado. Se responde con el estado en vez de con un error.
            return Response(document_payload(document))

        try:
            provider = resolve_provider(document.company)
        except FiscalConfigError as exc:
            return Response(
                {'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            document = submit_fiscal_document(document, provider)
        except FiscalSubmissionInProgress as exc:
            # 409: no es un error del usuario, es que ya hay uno en marcha.
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action=f'fiscal_document_{document.status}',
            target_type='fiscal_document', target_id=document.pk,
            metadata={
                'identifier': document.document_id,
                'environment': document.environment,
                'response_code': document.sunat_response_code,
                'attempts': document.attempts.count(),
                'cdr_sha256': document.cdr_sha256,
            },
            request=request, company=document.company,
        )
        return Response(document_payload(document))


def _artifact_response(content: str, filename: str) -> HttpResponse:
    """
    Un artefacto para descargar, con el nombre saneado.

    El nombre se construye de datos que ya validó `packaging.document_name`, pero
    se filtra otra vez aquí: una cabecera HTTP con un salto de línea dentro es
    inyección de cabeceras, y la garantía tiene que estar del lado que escribe.
    """
    import re

    safe = re.sub(r'[^A-Za-z0-9._-]', '', filename) or 'documento.xml'
    response = HttpResponse(content, content_type='application/xml; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{safe}"'
    response['Cache-Control'] = 'no-store'
    return response


class AdminFiscalDocumentXmlView(APIView):
    """
    GET /api/admin/fiscal-documents/{pk}/xml/ — el XML FIRMADO.

    Sólo el firmado. El borrador sin firmar no es un comprobante y publicarlo
    invitaría a confundirlo con uno.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalReadThrottle]

    def get(self, request, pk):
        document, error = _fiscal_document(request, pk, CAP_FISCAL_VIEW)
        if error:
            return error
        if not document.signed_xml:
            return Response(
                {'detail': 'El comprobante todavía no está firmado.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        name = (
            f'{document.issuer_tax_id}-{document.document_type}-'
            f'{document.series}-{document.number}.XML'
        )
        return _artifact_response(document.signed_xml, name)


class AdminFiscalDocumentCdrView(APIView):
    """GET /api/admin/fiscal-documents/{pk}/cdr/ — la constancia de SUNAT."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalReadThrottle]

    def get(self, request, pk):
        document, error = _fiscal_document(request, pk, CAP_FISCAL_VIEW)
        if error:
            return error
        if not document.cdr_xml:
            return Response(
                {'detail': 'Este comprobante todavía no tiene constancia de SUNAT.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        name = (
            f'R-{document.issuer_tax_id}-{document.document_type}-'
            f'{document.series}-{document.number}.XML'
        )
        return _artifact_response(document.cdr_xml, name)
