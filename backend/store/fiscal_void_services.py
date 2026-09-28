"""
Emitir una Comunicación de Baja (RA) sobre comprobantes NO OTORGADOS.

QUÉ HACE Y QUÉ NO TOCA
----------------------
Da de baja la NUMERACIÓN de comprobantes que nunca se otorgaron (artículo 14 de la
RS 097-2012, sustituido en bloque por la RS 114-2019). No es una nota —una nota
corrige un comprobante que sigue vigente— y **no borra nada nuestro**: el
comprobante original conserva su XML firmado, su CDR, su serie, su número y su
historia. Que quedó dado de baja es un hecho NUEVO, y vive en la fila de
pertenencia de una RA aceptada, no reescribiendo el original.

Por eso aquí NO se toca `FiscalDocument.status`: preguntar «¿está dado de baja?» se
responde mirando si tiene una inclusión en una RA aceptada. Así se puede seguir
contestando, dentro de un año, «aceptado el X, dado de baja el Y» — que es lo que
una auditoría necesita, y lo que un estado sobreescrito habría destruido.

FISCAL ≠ DINERO ≠ INVENTARIO ≠ OPERACIÓN
----------------------------------------
Dar de baja no reembolsa, no repone stock, no crea movimientos y no cancela la
Order ni el Payment. Esas integraciones son de otra fase, con su propia
autorización.

DOS PUERTAS QUE FALLAN CERRADO
------------------------------
1. El PLAZO se cuenta desde `cdr_accepted_at` (artículo 14.1.b: «hasta el sétimo
   día calendario contado a partir del día calendario siguiente de haber recibido la
   respectiva CDR con estado de aceptada»). Sin esa fecha no hay plazo demostrable y
   no se emite.
2. El OTORGAMIENTO: sólo se da de baja lo NO otorgado. `granted_at` nulo significa
   DESCONOCIDO, no «no otorgado», así que también niega. Hoy nada registra la
   entrega —el mostrador imprime y entrega sin dejar rastro—, de modo que en la
   práctica esta puerta niega casi todo: es deliberado. Es la diferencia entre
   negarse y anular la numeración de un comprobante que el cliente ya tiene.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from lxml import etree

from .fiscal import packaging, schema, signing
from .fiscal.provider import ProviderOutcome, SummaryOutcome, TicketStatus
from .fiscal.void import VoidData, VoidLine, VoidStructureError, build_void_xml
from .fiscal_services import FiscalError
from .fiscal_summary_services import STALE_SUBMISSION_MINUTES
from .models import (
    FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalGrantMethod,
    FiscalVoidCommunication, FiscalVoidCommunicationDocument, FiscalVoidStatus,
)

#: Artículo 14.1.b: hasta el SÉPTIMO día calendario contado a partir del día
#: siguiente de haber recibido la CDR aceptada.
VOID_PLAZO_DAYS = 7

#: Los tipos que se dan de baja por ESTE canal. La boleta no está: se anula
#: informándola en el Resumen Diario, que es otro documento y otro plazo
#: (artículo 14.2.b). Que falte aquí es la puerta, no un olvido.
VOIDABLE_DOCUMENT_TYPES = (
    FiscalDocumentType.INVOICE,
    FiscalDocumentType.CREDIT_NOTE,
    FiscalDocumentType.DEBIT_NOTE,
)

#: Estados desde los que una RA se puede enviar.
_SUBMITTABLE_STATES = (
    FiscalVoidStatus.SIGNED,
    FiscalVoidStatus.SUBMISSION_ERROR,
)

#: Veredicto del CDR-Baja traducido al dominio.
_CDR_OUTCOME_TO_STATUS = {
    ProviderOutcome.ACCEPTED: FiscalVoidStatus.ACCEPTED,
    ProviderOutcome.ACCEPTED_WITH_OBSERVATION:
        FiscalVoidStatus.ACCEPTED_WITH_OBSERVATION,
    ProviderOutcome.REJECTED: FiscalVoidStatus.REJECTED,
}


class FiscalVoidInProgress(Exception):
    """Ya hay un envío de esta comunicación en curso. No es un error del usuario."""


@dataclass
class VoidPollResult:
    """Qué pasó al consultar el ticket. `action` es la evidencia para la auditoría."""

    void_communication: FiscalVoidCommunication
    action: str
    sunat_code: str = ''
    safe_message: str = ''


# --- otorgamiento -----------------------------------------------------------
#
# Estas dos funciones hablan de la ENTREGA del comprobante, no de la baja. Viven
# aquí porque es la baja quien necesita el dato y quien lo introdujo; cuando el
# checkout y el punto de venta empiecen a registrar entregas por su cuenta, su sitio
# natural será el módulo de emisión. Queda dicho para que se mueva a conciencia.

def record_grant(document: FiscalDocument, *, actor, method: str,
                 evidence: dict | None = None) -> FiscalDocument:
    """
    Registra que el comprobante SE OTORGÓ: se entregó o se puso a disposición del
    adquirente (artículo 15). Se escribe una sola vez.

    Otorgar cierra la puerta de la baja, y para siempre: el artículo 14 sólo admite
    dar de baja la numeración de lo NO otorgado.
    """
    if document.granted_at is not None:
        return document
    if document.not_granted_at is not None:
        raise FiscalError(
            f'{document.document_id} tiene una atestación de NO otorgamiento; no se '
            f'puede registrar su entrega sin resolver esa contradicción.')
    if method not in dict(FiscalGrantMethod.choices):
        raise FiscalError(f'Canal de otorgamiento no reconocido: {method!r}.')

    document.granted_at = timezone.now()
    document.granted_method = method
    document.granted_by = actor if getattr(actor, 'pk', None) else None
    document.granted_evidence = evidence or {}
    document.save(update_fields=[
        'granted_at', 'granted_method', 'granted_by', 'granted_evidence',
        'updated_at'])
    return document


def attest_not_granted(document: FiscalDocument, *, actor, reason: str
                       ) -> FiscalDocument:
    """
    Registra la ATESTACIÓN de que el comprobante NO se otorgó.

    POR QUÉ HACE FALTA UN HECHO Y NO BASTA UNA AUSENCIA
    ---------------------------------------------------
    El artículo 14 exige que el documento no haya sido otorgado, pero hoy nada
    registra las entregas: el mostrador imprime y entrega sin dejar rastro. Si la
    ausencia de evidencia contara como «no otorgado», se podría anular la numeración
    de un comprobante que el cliente tiene en la mano. Y si bastara un booleano en la
    petición, el cliente autorizaría su propia baja.

    Así que la negativa se declara y se firma: queda el autor, el momento y el
    motivo. Es la palabra de una persona, registrada por el backend y auditable — no
    una deducción del sistema ni un dato del cuerpo de una petición.
    """
    if document.granted_at is not None:
        raise FiscalError(
            f'{document.document_id} consta OTORGADO el '
            f'{timezone.localtime(document.granted_at).date().isoformat()}: no se '
            f'puede atestiguar que no se otorgó.')
    if not (reason or '').strip():
        raise FiscalError(
            'La atestación exige un motivo: una baja sin motivo registrado no se '
            'puede auditar después.')
    if document.not_granted_at is not None:
        return document

    document.not_granted_at = timezone.now()
    document.not_granted_by = actor if getattr(actor, 'pk', None) else None
    document.not_granted_reason = reason.strip()[:200]
    document.save(update_fields=[
        'not_granted_at', 'not_granted_by', 'not_granted_reason', 'updated_at'])
    return document


# --- elegibilidad -----------------------------------------------------------

def void_deadline(document: FiscalDocument):
    """
    El ÚLTIMO día en que se admite la baja de este comprobante, o `None` si no se
    puede determinar (sin fecha de CDR aceptada no hay plazo demostrable).
    """
    if document.cdr_accepted_at is None:
        return None
    recibido = timezone.localtime(document.cdr_accepted_at).date()
    return recibido + timedelta(days=VOID_PLAZO_DAYS)


def void_send_blocked_reason(void: FiscalVoidCommunication, *, today=None):
    """
    Por qué NO se puede transmitir esta comunicación hoy, o `None` si sí se puede.

    EL PLAZO ES DEL ENVÍO, NO DE LA GENERACIÓN. El artículo 14.1.b dice que el
    emisor «debe ENVIAR a la SUNAT la comunicación de baja a más tardar hasta el
    sétimo día calendario...». Comprobarlo sólo al crear dejaba la puerta abierta:
    `/void/` firma sin enviar, y un `SUBMISSION_ERROR` es reintentable, así que una
    RA firmada en plazo podía transmitirse semanas después —con su `cbc:IssueDate` y
    su nombre de archivo ya rancios, porque quedan congelados al crearla—.
    Refrescarlos no es posible sin emitir otra comunicación, así que si el plazo de
    algún comprobante venció, ésta no sale.

    Vive en UNA función y la usan el envío y la bandera `can_submit` de la API: que
    la puerta y el botón se calculen en dos sitios distintos es exactamente cómo se
    abrió este agujero.
    """
    today = today or timezone.localdate()
    for row in void.lines.select_related('document').all():
        limite = void_deadline(row.document)
        if limite is None:
            return (f'No consta cuándo se recibió la CDR aceptada de '
                    f'{row.document.document_id}: el plazo no es demostrable. '
                    f'REQUIERE REVISIÓN OPERATIVA/TRIBUTARIA.')
        if today > limite:
            return (f'El plazo para dar de baja {row.document.document_id} venció el '
                    f'{limite.isoformat()}. Una comunicación firmada fuera de plazo '
                    f'no se transmite: su fecha de generación está congelada y no se '
                    f'puede refrescar sin emitir otra.')
    return None


def check_void_eligible(document: FiscalDocument, *, today=None) -> None:
    """
    Todas las condiciones del artículo 14, ANTES de reservar un correlativo.

    Levanta `FiscalError` con el motivo señalado. Falla CERRADO ante lo que no se
    puede demostrar: un «no sé» nunca se lee como un «sí».
    """
    today = today or timezone.localdate()

    if document.document_type not in VOIDABLE_DOCUMENT_TYPES:
        raise FiscalError(
            f'Un comprobante tipo {document.document_type} no se da de baja por '
            f'este canal. Una boleta (y sus notas) se anula informándola en el '
            f'Resumen Diario (artículo 14.2.b).')

    # Una NOTA hereda el canal del comprobante que corrige. Un 07/08 de una BOLETA
    # va por el Resumen, igual que la boleta —artículo 14.2.b—, así que no entra
    # aquí. El tipo por sí solo no lo distingue: hay que mirar el original.
    if (document.document_type in (FiscalDocumentType.CREDIT_NOTE,
                                   FiscalDocumentType.DEBIT_NOTE)
            and document.original_document_id
            and document.original_document.document_type
            == FiscalDocumentType.RECEIPT):
        raise FiscalError(
            f'{document.document_id} es una nota de BOLETA: se anula informándola '
            f'en el Resumen Diario, no por una comunicación de baja.')

    if not document.signed_xml:
        raise FiscalError('El comprobante no está firmado: no hay nada que dar de baja.')

    # Sólo se comunica la baja de algo que SUNAT ya tiene. El artículo 14.1.d cierra
    # la puerta al revés: lo que nunca se envió no se da de baja por esta vía.
    if not document.is_accepted:
        raise FiscalError(
            f'{document.document_id} no tiene CDR aceptada: la comunicación de baja '
            f'exige que SUNAT ya lo haya aceptado.')

    # PUERTA DEL PLAZO (artículo 14.1.b). Sin fecha demostrable de recepción de la
    # CDR aceptada no se inventa una: se niega y se pide revisión.
    limite = void_deadline(document)
    if limite is None:
        raise FiscalError(
            f'{document.document_id} está aceptado pero no consta CUÁNDO se recibió '
            f'su CDR aceptada, así que el plazo no es demostrable. REQUIERE REVISIÓN '
            f'OPERATIVA/TRIBUTARIA; no se infiere de otra fecha.')
    if today > limite:
        raise FiscalError(
            f'El plazo para dar de baja {document.document_id} venció el '
            f'{limite.isoformat()} (siete días calendario desde el día siguiente de '
            f'recibir la CDR aceptada). No se emite la baja, y NO se sustituye por '
            f'una nota de crédito automáticamente: si el comprobante no fue '
            f'otorgado, REQUIERE REVISIÓN OPERATIVA/TRIBUTARIA.')

    # PUERTA DEL OTORGAMIENTO. El artículo 14 sólo admite dar de baja documentos NO
    # OTORGADOS, y eso hay que DEMOSTRARLO, no deducirlo de un silencio.
    if document.granted_at is not None:
        raise FiscalError(
            f'{document.document_id} fue OTORGADO el '
            f'{timezone.localtime(document.granted_at).date().isoformat()}; sólo se '
            f'da de baja la numeración de documentos no otorgados.')
    if document.not_granted_at is None:
        # Ni otorgado ni atestiguado: DESCONOCIDO. Y un desconocido no autoriza
        # nada. Hoy nada registra las entregas —el mostrador imprime y entrega sin
        # dejar rastro—, así que tomar este silencio por un «no se entregó»
        # permitiría anular la numeración de un comprobante que el cliente tiene en
        # la mano. Hace falta la atestación firmada (`attest_not_granted`).
        raise FiscalError(
            f'No consta que {document.document_id} no haya sido otorgado. La baja '
            f'exige una atestación registrada de NO otorgamiento, con su autor y su '
            f'motivo: la ausencia de evidencia de entrega no es prueba de que no se '
            f'entregó.')

    # Una nota aceptada en su contra y una baja son caminos excluyentes: si ya se
    # corrigió con una nota, el comprobante existe y se otorgó. No se toca la nota.
    if FiscalDocument.objects.filter(
            original_document=document,
            document_type__in=(FiscalDocumentType.CREDIT_NOTE,
                               FiscalDocumentType.DEBIT_NOTE),
            status__in=(FiscalDocumentStatus.ACCEPTED,
                        FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION),
    ).exists():
        raise FiscalError(
            f'{document.document_id} tiene una nota ACEPTADA en su contra: se '
            f'corrigió con una nota, no se da de baja. La nota no se invalida.')

    if FiscalVoidCommunicationDocument.objects.filter(
            document=document, superseded=False).exists():
        raise FiscalError(
            f'{document.document_id} ya está incluido en una comunicación de baja '
            f'viva. No se incluye en dos.')


# --- creación ---------------------------------------------------------------

def _live_request(company, environment, request_key, expected_ids=None):
    """
    La comunicación viva que ya atendió esta clave, si la hay.

    Y COMPRUEBA QUE SEA LA MISMA PETICIÓN. La clave sola no identifica nada: si se
    reutiliza con OTROS comprobantes, devolver la comunicación anterior daría un
    «listo» que nombra un comprobante distinto del que se pidió dar de baja —y el
    segundo nunca se daría de baja, sin que nadie se enterara—. Ante ese choque se
    levanta, que es lo honesto: la clave está usada para otra cosa.
    """
    ya = FiscalVoidCommunication.objects.filter(
        company=company, environment=environment, request_key=request_key,
    ).exclude(status=FiscalVoidStatus.REJECTED).order_by('-pk').first()
    if ya is None or expected_ids is None:
        return ya

    suyos = set(ya.lines.values_list('document_id', flat=True))
    if suyos != set(expected_ids):
        raise FiscalError(
            f'La clave de idempotencia {request_key!r} ya se usó para otra baja '
            f'({ya.identifier}), sobre comprobantes distintos de los pedidos. Use '
            f'una clave nueva: reutilizarla habría dado por hecha una baja que no '
            f'se pidió, y dejado sin dar de baja la que sí.')
    return ya


def create_void_communication(company, *, targets, environment=None,
                              reference_date=None, request_key=''
                              ) -> FiscalVoidCommunication:
    """
    Crea (sin firmar) una Comunicación de Baja sobre `targets`.

    `targets` es una secuencia de `(FiscalDocument, motivo)`. Puede llevar más de
    uno —el artículo 14.1.b lo admite— SIEMPRE que todos compartan el día en que se
    generaron o emitieron; de ahí que `reference_date` sea una sola fecha.

    IDEMPOTENTE POR `request_key`: dos clics, o dos workers, con la misma clave
    devuelven LA MISMA comunicación. Sin ella no se puede distinguir un reintento de
    una segunda baja legítima, y dar de baja dos veces la misma numeración es
    exactamente lo que no debe poder pasar por un doble clic.

    La red no entra aquí. El correlativo se reserva DENTRO de la transacción, y sólo
    después de que todas las puertas de elegibilidad hayan pasado: una baja que se
    va a negar no gasta un número que no se recicla.
    """
    from .fiscal_config import resolve_environment

    targets = list(targets)
    if not targets:
        raise FiscalError('Una comunicación de baja sin comprobantes no baja nada.')

    environment = environment or resolve_environment()

    esperados = [document.pk for document, _motivo in targets]
    if request_key:
        ya = _live_request(company, environment, request_key, esperados)
        if ya is not None:
            return ya

    for document, _motivo in targets:
        if document.company_id != company.pk:
            # El alcance sale del comprobante autorizado, nunca de quien llama.
            raise FiscalError(
                'Una comunicación de baja no mezcla comprobantes de empresas '
                'distintas.')
        check_void_eligible(document)

    # Un solo día para toda la comunicación (artículo 14.1.b). Se toma la fecha
    # LEGAL del comprobante —la del XML firmado, vía `issued_at` congelado— y se
    # exige que coincidan; mezclar días produciría un rechazo de SUNAT.
    dias = {timezone.localtime(d.issued_at).date() for d, _ in targets}
    if len(dias) > 1:
        raise FiscalError(
            f'Una comunicación de baja sólo agrupa comprobantes generados o '
            f'emitidos el MISMO día; llegaron {sorted(dias)}.')
    reference_date = reference_date or dias.pop()

    issue_date = timezone.localdate()

    # Reserva del correlativo: max()+1 bajo bloqueo, con la restricción única como
    # red y un reintento si dos procesos coinciden. Mismo criterio que el Resumen.
    for _ in range(5):
        try:
            with transaction.atomic():
                usados = list(
                    FiscalVoidCommunication.objects.select_for_update().filter(
                        company=company, environment=environment,
                        issue_date=issue_date,
                    ).values_list('correlativo', flat=True))
                correlativo = (max(usados) + 1) if usados else 1
                void = FiscalVoidCommunication.objects.create(
                    company=company, environment=environment,
                    identifier=packaging.void_identifier(issue_date, correlativo),
                    correlativo=correlativo, issue_date=issue_date,
                    reference_date=reference_date, request_key=request_key,
                    status=FiscalVoidStatus.GENERATED)
                for line_id, (document, motivo) in enumerate(targets, 1):
                    FiscalVoidCommunicationDocument.objects.create(
                        void_communication=void, document=document,
                        line_id=line_id, void_reason=(motivo or '').strip())
                return void
        except IntegrityError:
            # DOS choques distintos comparten excepción, y confundirlos daría un
            # error engañoso:
            #
            # - Idempotencia: otro proceso ganó la carrera con ESTA misma clave. No
            #   hay nada que reintentar; se devuelve su comunicación.
            # - Correlativo: dos procesos pidieron el mismo número. Ahí sí se
            #   reintenta con el siguiente.
            #
            # Sin esta distinción, una carrera de idempotencia agotaría los cinco
            # intentos y saldría como «no se pudo reservar un correlativo», que es
            # falso y manda a investigar el sitio equivocado.
            if request_key:
                ya = _live_request(company, environment, request_key, esperados)
                if ya is not None:
                    return ya
            continue
    raise FiscalError(
        'No se pudo reservar un correlativo de comunicación de baja; reintente.')


# --- firma ------------------------------------------------------------------

def _issuer_ruc(void: FiscalVoidCommunication) -> str:
    row = void.lines.select_related('document').order_by('line_id').first()
    if row is None:
        raise FiscalError('La comunicación de baja no tiene comprobantes.')
    return row.document.issuer_tax_id


def _to_data(void: FiscalVoidCommunication) -> VoidData:
    rows = list(void.lines.select_related('document').order_by('line_id'))
    if not rows:
        raise FiscalError('La comunicación de baja no tiene comprobantes.')
    primero = rows[0].document
    return VoidData(
        identifier=void.identifier,
        issue_date=void.issue_date,
        reference_date=void.reference_date,
        supplier_ruc=primero.issuer_tax_id,
        supplier_name=primero.issuer_legal_name,
        lines=tuple(
            VoidLine(line_id=row.line_id,
                     document_type=row.document.document_type,
                     document_serial=row.document.series,
                     document_number=row.document.number,
                     reason=row.void_reason)
            for row in rows),
    )


def sign_void_communication(void: FiscalVoidCommunication, *, key_pem: bytes,
                            cert_pem: bytes) -> FiscalVoidCommunication:
    """
    Construye el XML, lo firma y lo valida contra el XSD **OFICIAL**.

    Re-firmar es no-op: el XML firmado ES la comunicación, y regenerarlo cambiaría
    una evidencia que puede estar ya enviada.
    """
    if void.signed_xml:
        return void

    try:
        data = _to_data(void)
        xml_sin_firmar = build_void_xml(data)
    except VoidStructureError as exc:
        raise FiscalError(str(exc)) from exc

    root = etree.fromstring(xml_sin_firmar)
    signed = signing.sign_invoice(root, key_pem=key_pem, cert_pem=cert_pem)
    xml = etree.tostring(signed, xml_declaration=True, encoding='UTF-8')

    # Aquí SÍ hay esquema oficial: se valida contra él, no contra una imitación.
    try:
        schema.validate_voided_documents(xml)
    except schema.SchemaError as exc:
        raise FiscalError(str(exc)) from exc

    void.signed_xml = xml.decode('utf-8')
    void.signed_xml_sha256 = hashlib.sha256(xml).hexdigest()
    void.status = FiscalVoidStatus.SIGNED
    void.save(update_fields=[
        'signed_xml', 'signed_xml_sha256', 'status', 'updated_at'])
    return void


# --- envío y consulta -------------------------------------------------------

def submit_void_communication(void: FiscalVoidCommunication, provider
                              ) -> FiscalVoidCommunication:
    """
    Envía la comunicación por `sendSummary` y PERSISTE el ticket antes de nada.

    Claim → red → finalize: se marca «en curso» bajo bloqueo, se suelta el bloqueo,
    se llama a SUNAT y se guarda el ticket bajo bloqueo. Dos envíos simultáneos no
    crean dos tickets. Un envío TRANSMITIDO SIN TICKET queda INCIERTO y no se
    reenvía por el flujo normal: podría duplicar la baja en SUNAT.
    """
    if not void.signed_xml:
        raise FiscalError('La comunicación de baja no está firmada.')
    if void.is_accepted:
        return void

    # El plazo, OTRA VEZ, aquí. No es redundante: el artículo 14.1.b lo pone sobre el
    # envío, y entre crear y enviar puede pasar cualquier cosa (un timeout, una noche,
    # una semana). Comprobarlo sólo al crear permitía transmitir una baja vencida.
    bloqueo = void_send_blocked_reason(void)
    if bloqueo:
        raise FiscalError(bloqueo)

    limite = timezone.now() - timedelta(minutes=STALE_SUBMISSION_MINUTES)
    with transaction.atomic():
        fresh = FiscalVoidCommunication.objects.select_for_update().get(pk=void.pk)
        if fresh.is_accepted:
            return fresh
        if fresh.ticket:
            # Ya está en la cola de SUNAT: no se reenvía, se consulta.
            return fresh
        if fresh.status == FiscalVoidStatus.SUBMISSION_UNKNOWN:
            raise FiscalError(
                f'El envío de {fresh.identifier} quedó con resultado INCIERTO: '
                f'puede existir un ticket que no recibimos. No se reenvía a ciegas; '
                f'exige verificar en SUNAT y decidir la retransmisión.')
        if fresh.status not in _SUBMITTABLE_STATES:
            raise FiscalError(
                f'Una comunicación en estado «{fresh.get_status_display()}» no se '
                f'envía.')
        if fresh.submitting_since and fresh.submitting_since >= limite:
            raise FiscalVoidInProgress(
                f'Ya hay un envío en curso para {fresh.identifier}. Espere.')
        fresh.submitting_since = timezone.now()
        fresh.save(update_fields=['submitting_since', 'updated_at'])

    name = packaging.void_name(
        _issuer_ruc(fresh), fresh.issue_date, fresh.correlativo)
    zip_bytes = packaging.build_zip(name, fresh.signed_xml.encode('utf-8'))

    result = provider.send_summary(filename=f'{name}.ZIP', zip_bytes=zip_bytes)

    with transaction.atomic():
        fresh = FiscalVoidCommunication.objects.select_for_update().get(pk=void.pk)
        fresh.submitting_since = None
        if fresh.ticket:  # otro envío ganó la carrera
            fresh.save(update_fields=['submitting_since', 'updated_at'])
            return fresh
        if result.outcome == SummaryOutcome.TICKET:
            fresh.ticket = result.ticket
            fresh.status = FiscalVoidStatus.SUBMITTED
        elif result.outcome == SummaryOutcome.TRANSPORT_ERROR:
            # Demostrablemente NO transmitido: es seguro reintentar.
            fresh.status = FiscalVoidStatus.SUBMISSION_ERROR
            fresh.sunat_response_code = result.status_code
            fresh.sunat_response_message = result.safe_message
        else:
            # Se transmitió y no sabemos qué pasó. No reintentable a ciegas.
            fresh.status = FiscalVoidStatus.SUBMISSION_UNKNOWN
            fresh.sunat_response_code = result.status_code
            fresh.sunat_response_message = result.safe_message
        fresh.save(update_fields=[
            'submitting_since', 'ticket', 'status', 'sunat_response_code',
            'sunat_response_message', 'updated_at'])
    return fresh


def poll_void_communication(void: FiscalVoidCommunication, provider
                            ) -> VoidPollResult:
    """
    Consulta el ticket (`getStatus`) y aplica el CDR-Baja si el proceso terminó.

    `98` (en proceso) la deja enviada: hay que volver a consultar. Un fallo de red
    NO pierde el ticket.
    """
    if not void.ticket:
        raise FiscalError(
            'La comunicación de baja no tiene ticket: no se ha enviado o el envío '
            'quedó incierto. Envíela antes de consultar.')
    if void.is_accepted or void.status == FiscalVoidStatus.REJECTED:
        return VoidPollResult(void, 'already_terminal',
                              sunat_code=void.sunat_response_code)

    result = provider.get_ticket_status(ticket=void.ticket)

    if result.status == TicketStatus.PROCESSING:
        return VoidPollResult(void, 'processing', sunat_code=result.status_code)
    if result.status in (TicketStatus.TRANSPORT_ERROR, TicketStatus.UNKNOWN_RESPONSE):
        return VoidPollResult(void, result.status.value,
                              sunat_code=result.status_code,
                              safe_message=result.safe_message)

    cdr = result.cdr
    if cdr is None or not cdr.cdr_xml:
        return VoidPollResult(void, 'no_cdr', sunat_code=result.status_code)
    return _apply_void_cdr(void, cdr)


def _apply_void_cdr(void: FiscalVoidCommunication, cdr) -> VoidPollResult:
    from .fiscal.cdr import CdrParseError, cdr_matches_document, parse_cdr

    if cdr.outcome not in _CDR_OUTCOME_TO_STATUS:
        return VoidPollResult(void, 'cdr_inconclusive',
                              sunat_code=cdr.response_code)

    # BINDING: el CDR-Baja debe corresponder a ESTA comunicación —su ReferenceID es
    # el RA, no un comprobante—. Uno de otra no se aplica jamás.
    try:
        rep = parse_cdr(cdr.cdr_xml)
    except CdrParseError:
        return VoidPollResult(void, 'cdr_unreadable', sunat_code=cdr.response_code)
    if not cdr_matches_document(
            rep, document_id=void.identifier, issuer_tax_id=_issuer_ruc(void)):
        return VoidPollResult(void, 'cdr_mismatch', sunat_code=cdr.response_code)

    new_status = _CDR_OUTCOME_TO_STATUS[cdr.outcome]
    cdr_bytes = cdr.cdr_xml

    with transaction.atomic():
        fresh = FiscalVoidCommunication.objects.select_for_update().get(pk=void.pk)
        if fresh.is_accepted or fresh.status == FiscalVoidStatus.REJECTED:
            return VoidPollResult(fresh, 'already_terminal',
                                  sunat_code=fresh.sunat_response_code)
        fresh.status = new_status
        fresh.sunat_response_code = cdr.response_code
        fresh.sunat_response_message = cdr.safe_message
        fresh.cdr_xml = cdr_bytes.decode('utf-8', 'replace')
        fresh.cdr_sha256 = hashlib.sha256(cdr_bytes).hexdigest()
        fresh.save(update_fields=[
            'status', 'sunat_response_code', 'sunat_response_message',
            'cdr_xml', 'cdr_sha256', 'updated_at'])
        if new_status == FiscalVoidStatus.REJECTED:
            # El rechazo es de la COMUNICACIÓN entera: se liberan sus comprobantes
            # para poder intentarlo en otra. NO se da por dado de baja nada, y NO se
            # borra la intención: la fila queda, marcada como superada.
            fresh.lines.update(superseded=True)

    return VoidPollResult(fresh, 'reconciled', sunat_code=cdr.response_code,
                          safe_message=cdr.safe_message)


# --- lectura ----------------------------------------------------------------

def is_voided(document: FiscalDocument) -> bool:
    """
    ¿Quedó dada de baja la numeración de este comprobante?

    Se responde por la RELACIÓN, no por un estado sobreescrito en el comprobante:
    tiene una inclusión no superada en una comunicación ACEPTADA. Así el original
    sigue diciendo que fue aceptado el día que lo fue, y la baja es un hecho
    posterior que se suma — que es lo que permite auditar ambas cosas.
    """
    return FiscalVoidCommunicationDocument.objects.filter(
        document=document, superseded=False,
        void_communication__status__in=(
            FiscalVoidStatus.ACCEPTED,
            FiscalVoidStatus.ACCEPTED_WITH_OBSERVATION),
    ).exists()
