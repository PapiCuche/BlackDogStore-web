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

from .fiscal_config import (
    FiscalConfigError, resolve_consult_provider, resolve_provider,
)
from .fiscal_services import (
    FiscalError, FiscalSubmissionInProgress, get_or_create_fiscal_document,
    reconcile_fiscal_document, sign_fiscal_document, submit_fiscal_document,
)
from .inventory_views import _company_context
from .models import (
    AdminAuditLog, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType,
)
from .tenancy import visible_orders
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

    H4.1.2: y uno de una sucursal que quien llama no opera, también. Tener
    `sales.fiscal.issue` es poder emitir, no poder emitir en CUALQUIER sucursal:
    el pedido se resuelve con visible_orders() ANTES de tocar serie, correlativo
    o firma, así que fuera de alcance no se gasta ningún número.
    """
    company, error = _company_context(request, capability, _NO_LEGACY_BRIDGE)
    if error:
        detail = error.data.get('detail')
        if detail and 'permiso' in str(detail).lower():
            error.data['detail'] = 'No tienes permisos sobre comprobantes electrónicos.'
        return None, None, error

    order = visible_orders(request.user, company).filter(pk=pk).first()
    if order is None:
        return None, None, Response(
            {'detail': 'No se encontró el pedido.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return company, order, None


def _fiscal_document(request, pk, capability):
    """
    El comprobante, con el mismo criterio de tenant Y DE SUCURSAL que la venta.

    Un comprobante no tiene alcance propio: es de su pedido. Si quien llama no
    puede ver el pedido, el comprobante —su XML, su CDR, su PDF, su envío— no
    existe para él (H4.1.2).
    """
    company, error = _company_context(request, capability, _NO_LEGACY_BRIDGE)
    if error:
        return None, error
    document = (
        FiscalDocument.objects.filter(
            company=company, pk=pk,
            order__in=visible_orders(request.user, company),
        )
        .select_related('order', 'series_ref').first()
    )
    if document is None:
        return None, Response(
            {'detail': 'No se encontró el comprobante.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return document, None


def _document_observations(document: FiscalDocument) -> list[dict]:
    """
    Las observaciones del CDR, cada una con su código (§36/§37). Derivadas del
    XML almacenado; sin columna nueva. Un CDR ilegible no rompe la respuesta:
    devuelve una lista vacía, porque esto es una pista, no la evidencia.
    """
    if not document.cdr_xml:
        return []
    from .fiscal.cdr import CdrParseError, parse_cdr

    try:
        rep = parse_cdr(document.cdr_xml)
    except CdrParseError:
        return []
    return [{'code': o.code, 'text': o.text} for o in rep.observations]


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
        # Si es una nota (07/08): a qué comprobante corrige y por qué. Nulo/vacío
        # en un comprobante normal, de modo que añadir estas claves no altera lo
        # que la interfaz ya leía.
        'original_document_id': document.original_document_id,
        'note_reason_code': document.note_reason_code,
        'note_reason_description': document.note_reason_description,
        # El código y el mensaje de SUNAT ya vienen saneados por el adaptador.
        'response_code': document.sunat_response_code,
        'response_message': document.sunat_response_message,
        'is_accepted': document.is_accepted,
        'has_xml': bool(document.signed_xml),
        'has_cdr': bool(document.cdr_xml),
        'attempts': document.attempts.count(),
        # Observaciones del CDR con SU CÓDIGO conservado (§37): derivadas del XML,
        # no de una columna. Una observación aceptada trae datos reparables; el
        # código dice QUÉ reparar y no debe perderse tras «aceptado con
        # observaciones».
        'observations': _document_observations(document),
        # Pistas para la interfaz. El backend las vuelve a comprobar.
        'can_submit': puede_enviar,
        'can_retry': puede_reintentar,
        # Reconciliar cierra un envío incierto (SUBMISSION_ERROR). El backend
        # vuelve a comprobar estado y permiso: ocultarlo es cortesía, no seguridad.
        'can_reconcile': document.status == FiscalDocumentStatus.SUBMISSION_ERROR,
        # Se puede imprimir desde que está firmado: antes no hay valor resumen
        # y por tanto no hay QR. El papel dice el estado real, así que
        # «pendiente de envío» no se confunde con «aceptada».
        'can_download_pdf': document.status in (
            FiscalDocumentStatus.SIGNED, FiscalDocumentStatus.SUBMITTED,
            FiscalDocumentStatus.ACCEPTED,
            FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION,
            FiscalDocumentStatus.REJECTED,
            FiscalDocumentStatus.SUBMISSION_ERROR,
        ),
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
            except FiscalError as exc:
                # ERP-FISCAL-1E. Regla fiscal incumplida al firmar (p. ej. un
                # descuadre de redondeo, VEN-02A): 400 de dominio, no 500.
                return Response(
                    {'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST,
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


def _parse_note_amount(raw) -> "Decimal | None":
    """Un importe del cuerpo, si viene. Vacío = ausente. Basura = error."""
    from decimal import Decimal, InvalidOperation

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise FiscalError('Importe de la nota inválido.')


class _AdminFiscalNoteView(APIView):
    """
    Emitir una nota sobre un comprobante YA EMITIDO, dentro del tenant y de la
    sucursal de quien llama.

    LA IDENTIDAD SALE DEL ORIGINAL, NO DEL CUERPO (§40/§46/§47)
    ----------------------------------------------------------
    El `pk` de la URL es el comprobante ORIGINAL, resuelto con el mismo criterio
    de tenant y de sucursal que cualquier otra acción fiscal (`_fiscal_document`).
    La empresa, la sucursal, la serie de la nota, el adquirente y —en una
    anulación total— los importes se DERIVAN de ese original. Del cuerpo sólo se
    aceptan el motivo, su descripción, una clave de idempotencia y, para una nota
    de débito, el importe del cargo. Nada que identifique un comprobante ajeno.

    Emitir NO habla con SUNAT: deja la nota numerada y firmada. Enviarla es la
    llamada `submit/` de siempre —una nota es un `FiscalDocument`—, de modo que un
    fallo de red no se confunde con un fallo al emitir.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalIssueThrottle]
    #: '07' o '08'. Lo fija la subclase; NUNCA llega del cliente.
    note_type: str = ''

    def post(self, request, pk):
        from .fiscal_note_services import create_fiscal_note, sign_fiscal_note

        original, error = _fiscal_document(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error

        body = request.data if isinstance(request.data, dict) else {}
        reason_code = str(body.get('reason_code', '')).strip()
        reason_description = str(body.get('reason_description', '')).strip()
        request_key = str(body.get('request_key', '')).strip()[:64]
        if not reason_code or not reason_description:
            return Response(
                {'detail': 'Una nota necesita un motivo y su descripción.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # El importe del cuerpo sólo lo lee la NOTA DE DÉBITO (un cargo nuevo). Una
        # nota de crédito es, en esta fase, una anulación total: sus importes salen
        # del original y un `taxable_amount`/`tax_amount` del cuerpo NO se reenvía
        # —así el importe de una NC nunca depende del cliente (anti-IDOR, §40/§47)—.
        taxable = tax = None
        if self.note_type == FiscalDocumentType.DEBIT_NOTE:
            try:
                taxable = _parse_note_amount(body.get('taxable_amount'))
                tax = _parse_note_amount(body.get('tax_amount'))
            except FiscalError as exc:
                return Response(
                    {'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            if (taxable is None) != (tax is None):
                return Response(
                    {'detail': 'Para un importe explícito indique base e impuesto '
                               'juntos.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            note, created = create_fiscal_note(
                original, note_type=self.note_type, reason_code=reason_code,
                reason_description=reason_description, request_key=request_key,
                taxable_amount=taxable, tax_amount=tax,
            )
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except FiscalConfigError as exc:
            return Response(
                {'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        if created:
            AdminAuditLog.log(
                actor=request.user, action='fiscal_note_created',
                target_type='fiscal_document', target_id=note.pk,
                metadata={
                    'identifier': note.document_id,
                    'note_type': note.document_type,
                    'original_id': original.pk,
                    'original_identifier': original.document_id,
                    'reason_code': reason_code,
                    'series_id': note.series_ref_id,
                },
                request=request, company=note.company,
            )

        if not note.signed_xml:
            try:
                from .fiscal_config import resolve_credentials

                credentials = resolve_credentials(note.company)
                note = sign_fiscal_note(
                    note, key_pem=credentials['key_pem'],
                    cert_pem=credentials['cert_pem'])
            except FiscalConfigError as exc:
                return Response(
                    {'detail': str(exc)},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE)
            except FiscalError as exc:
                return Response(
                    {'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception:
                logger.exception('Fiscal note signing failed for %s', note.pk)
                return Response(
                    {'detail': 'No se pudo firmar la nota.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            AdminAuditLog.log(
                actor=request.user, action='fiscal_note_signed',
                target_type='fiscal_document', target_id=note.pk,
                metadata={'identifier': note.document_id,
                          'xml_sha256': note.signed_xml_sha256},
                request=request, company=note.company,
            )

        return Response(
            document_payload(note),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AdminFiscalDocumentCreditNoteView(_AdminFiscalNoteView):
    """POST /api/admin/fiscal-documents/{pk}/credit-notes/ — Nota de Crédito (07)."""

    note_type = FiscalDocumentType.CREDIT_NOTE


class AdminFiscalDocumentDebitNoteView(_AdminFiscalNoteView):
    """POST /api/admin/fiscal-documents/{pk}/debit-notes/ — Nota de Débito (08)."""

    note_type = FiscalDocumentType.DEBIT_NOTE


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


class AdminFiscalDocumentReconcileView(APIView):
    """
    POST /api/admin/fiscal-documents/{pk}/reconcile/ — cerrar un envío incierto.

    Cuando un envío quedó en `SUBMISSION_ERROR` (un timeout, un corte: no sabemos
    si SUNAT lo recibió), esto consulta el CDR del comprobante YA EMITIDO
    (`getStatusCdr`) y, si SUNAT ya tiene un veredicto, lo aplica. NO reenvía, NO
    reserva otro correlativo, NO crea otro documento.

    POR QUÉ EXIGE `sales.fiscal.issue` Y NO `.view` (§40)
    ----------------------------------------------------
    Reconciliar no es leer: puede llevar un comprobante a ACEPTADO o RECHAZADO
    —su estado tributario— y sale a un servicio externo. Es del mismo tenor que
    emitir y reintentar, no de consultar. Ver es mirar; reconciliar es declarar
    que el asunto quedó zanjado con SUNAT.

    EL IDENTIFICADOR LO PONE EL BACKEND (§42/§44)
    ---------------------------------------------
    El RUC, el tipo, la serie y el número se derivan del `FiscalDocument` local
    autorizado, NUNCA del cuerpo de la petición: aceptar esos datos del cliente
    sería consultar un comprobante ajeno (IDOR) o inyectar una consulta arbitraria.
    El cuerpo de la petición se ignora.
    """

    permission_classes = [permissions.IsAuthenticated]
    #: Sale a la red igual que el envío: mismo cubo, no uno nuevo (§45).
    throttle_classes = [FiscalIssueThrottle]

    def post(self, request, pk):
        document, error = _fiscal_document(request, pk, CAP_FISCAL_ISSUE)
        if error:
            return error

        try:
            provider = resolve_consult_provider(document.company)
        except FiscalConfigError as exc:
            return Response(
                {'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        old_status = document.status
        try:
            outcome = reconcile_fiscal_document(document, provider)
        except FiscalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action='fiscal_document_reconciled',
            target_type='fiscal_document', target_id=document.pk,
            metadata={
                'identifier': document.document_id,
                'environment': document.environment,
                # La evidencia cruda, sin normalizar (§20/§39): qué pidió el
                # operador, qué dijo SUNAT y a qué llevó.
                'reconcile_action': outcome.action,
                'old_status': old_status,
                'new_status': outcome.document.status,
                # El veredicto que trajo la consulta. En un conflicto (§28) NO es
                # el estado que queda guardado: se registra para poder auditar la
                # discrepancia por nombre, no sólo por el código crudo.
                'sunat_verdict': outcome.new_status,
                'response_code': outcome.sunat_code,
            },
            request=request, company=document.company,
        )
        return Response(document_payload(outcome.document))


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


#: CUÁNDO SE PUEDE IMPRIMIR, y es conservador a propósito.
#:
#: Antes de firmar no hay `DigestValue`, así que no hay QR y no hay
#: representación impresa posible. Después de firmar sí puede imprimirse, pero el
#: papel dice el estado real: «pendiente de envío» no es «aceptada».
#:
#: Un RECHAZADO también se imprime, y a propósito: el papel dice que fue
#: rechazado y que no tiene validez tributaria. Ocultarlo dejaría al operador sin
#: forma de enseñarle al cliente qué pasó.
_PRINTABLE_STATES = (
    FiscalDocumentStatus.SIGNED,
    FiscalDocumentStatus.SUBMITTED,
    FiscalDocumentStatus.ACCEPTED,
    FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION,
    FiscalDocumentStatus.REJECTED,
    FiscalDocumentStatus.SUBMISSION_ERROR,
)


class AdminFiscalDocumentPdfView(APIView):
    """
    GET /api/admin/fiscal-documents/{pk}/pdf/ — la representación impresa.

    `?formato=ticket80` devuelve el rollo de 80 mm. Sin parámetro, el A4.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [FiscalReadThrottle]

    FORMATS = ('a4', 'ticket80')

    def get(self, request, pk):
        document, error = _fiscal_document(request, pk, CAP_FISCAL_VIEW)
        if error:
            return error

        if document.status not in _PRINTABLE_STATES:
            return Response(
                {'detail': 'El comprobante todavía no se puede imprimir: '
                           'sin firma no hay valor resumen y sin valor resumen '
                           'no hay código QR.'},
                status=status.HTTP_409_CONFLICT,
            )

        wanted = (request.query_params.get('formato') or 'a4').strip().lower()
        if wanted not in self.FORMATS:
            return Response(
                {'detail': f'Formato no reconocido. Use: {", ".join(self.FORMATS)}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .fiscal_pdf_services import (
            FiscalPdfError, generate_fiscal_pdf, generate_fiscal_ticket_pdf,
        )

        try:
            content = (generate_fiscal_ticket_pdf(document) if wanted == 'ticket80'
                       else generate_fiscal_pdf(document))
        except FiscalPdfError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except Exception:
            logger.exception('Fiscal PDF failed for %s', document.pk)
            return Response(
                {'detail': 'No se pudo generar la representación impresa.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        import re

        sufijo = '-ticket80' if wanted == 'ticket80' else ''
        name = re.sub(
            r'[^A-Za-z0-9._-]', '',
            f'{document.issuer_tax_id}-{document.document_type}-'
            f'{document.series}-{document.number}{sufijo}.pdf',
        )
        response = HttpResponse(content, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{name}"'
        response['Cache-Control'] = 'no-store'
        return response
