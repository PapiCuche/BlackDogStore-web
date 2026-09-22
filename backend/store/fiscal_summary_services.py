"""
Armar, firmar, enviar y reconciliar un Resumen Diario de Boletas (RC).

DÓNDE ESTÁ LA FRONTERA (igual que en la factura)
------------------------------------------------
`store.fiscal.summary` no conoce Django: recibe datos planos y devuelve XML. Este
módulo traduce las boletas persistidas a ese XML, reserva el correlativo del
resumen, firma, envía y guarda lo que SUNAT responde. La red va SIEMPRE fuera de
transacción, y el correlativo se reserva bajo bloqueo con la restricción única de
respaldo (§16).

UN TICKET NO ES UNA ACEPTACIÓN (§6/§7). `sendSummary` devuelve un ticket que
prueba recepción para procesar; el veredicto llega con `getStatus(ticket)`. El
ticket se PERSISTE antes de consultar (§8): es la única referencia al proceso.

EL RESUMEN SE ACEPTA O SE RECHAZA ENTERO (§52). SUNAT no hace procesamiento
parcial de un RC. Un rechazo no anula las boletas: libera sus filas para que
puedan informarse en un resumen nuevo.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from lxml import etree

from .fiscal import packaging, signing
from .fiscal.provider import ProviderOutcome, SummaryOutcome, TicketStatus
from .fiscal.summary import SummaryData, SummaryLine, build_summary_xml
from .fiscal_services import FiscalError
from .models import (
    FiscalDailySummary, FiscalDailySummaryDocument, FiscalDocument,
    FiscalDocumentStatus, FiscalDocumentType, FiscalSummaryStatus,
)

#: Cuántas líneas caben en un resumen (Anexo N.º 6): 500. Más de eso se reparte.
MAX_SUMMARY_LINES = 500

#: Un envío marcado «en curso» más viejo que esto se da por abandonado, para no
#: bloquear un resumen para siempre si el proceso murió a mitad (como en el envío).
STALE_SUBMISSION_MINUTES = 30

#: El veredicto del CDR, traducido al estado del resumen.
_CDR_OUTCOME_TO_STATUS = {
    ProviderOutcome.ACCEPTED: FiscalSummaryStatus.ACCEPTED,
    ProviderOutcome.ACCEPTED_WITH_OBSERVATION:
        FiscalSummaryStatus.ACCEPTED_WITH_OBSERVATION,
    ProviderOutcome.REJECTED: FiscalSummaryStatus.REJECTED,
}

#: Boletas que pueden entrar en un resumen: FIRMADAS. Una boleta que no llegó a
#: firmarse no tiene evidencia (XML/QR) y no debe informarse (REVIEW A/B).
_ELIGIBLE_BOLETA_STATES = (
    FiscalDocumentStatus.SIGNED,
)


@dataclass(frozen=True)
class SummaryPollResult:
    summary: FiscalDailySummary
    action: str
    sunat_code: str = ''
    safe_message: str = ''


def select_eligible_boletas(company, reference_date: date, environment: str
                            ) -> list[FiscalDocument]:
    """
    Las boletas de esa empresa/ambiente emitidas en `reference_date` que aún no
    fueron informadas. Orden ESTABLE por pk (§60), para que dos generaciones den
    el mismo reparto.

    «Emitidas en `reference_date`» se mide por la fecha de la fila (`issued_at`),
    que en el flujo normal —emitir al cobrar— coincide con la fecha legal del XML.
    Ya informadas = con una fila de inclusión NO superada (§13).
    """
    ya_informadas = FiscalDailySummaryDocument.objects.filter(
        superseded=False).values_list('document_id', flat=True)
    return list(
        FiscalDocument.objects.filter(
            company=company, environment=environment,
            document_type=FiscalDocumentType.RECEIPT,
            status__in=_ELIGIBLE_BOLETA_STATES,
            issued_at__date=reference_date,
        )
        .exclude(pk__in=ya_informadas)
        .order_by('pk')
    )


def generate_daily_summaries(company, reference_date: date, environment: str, *,
                             issue_date: date | None = None
                             ) -> list[FiscalDailySummary]:
    """
    Arma uno o más resúmenes (bloques de 500, §61) con las boletas elegibles.

    Determinista: el orden por pk hace que el reparto en bloques sea reproducible.
    El correlativo se reserva bajo bloqueo y la restricción única lo respalda ante
    concurrencia (§16); al re-seleccionar tras un conflicto, las boletas que otro
    proceso ya tomó quedan excluidas.
    """
    # `issue_date` es la fecha de GENERACIÓN del resumen (hoy), no la de las
    # boletas. `cbc:IssueDate` = generación; `cbc:ReferenceDate` = emisión de las
    # boletas. Se exige `issue_date >= reference_date` en `SummaryData.check`.
    issue_date = issue_date or timezone.localdate()
    summaries: list[FiscalDailySummary] = []
    # Se corta por FALTA DE PROGRESO, no por número total de iteraciones: un
    # volumen grande y legítimo (muchos bloques de 500) NO debe truncarse. Sólo
    # una racha de conflictos sin crear nada indica un problema real.
    sin_progreso = 0
    while sin_progreso < 20:
        eligible = select_eligible_boletas(company, reference_date, environment)
        if not eligible:
            break
        chunk = eligible[:MAX_SUMMARY_LINES]
        try:
            summaries.append(_create_summary(
                company, environment, reference_date, issue_date, chunk))
            sin_progreso = 0
        except IntegrityError:
            # Carrera: otro proceso tomó el correlativo o alguna boleta. Se
            # re-selecciona (excluye lo tomado) y se reintenta el bloque.
            sin_progreso += 1
            continue

    if not summaries:
        raise FiscalError(
            f'No hay boletas elegibles para el resumen del {reference_date}.')
    return summaries


def _create_summary(company, environment, reference_date, issue_date, boletas):
    with transaction.atomic():
        existing = list(
            FiscalDailySummary.objects.select_for_update().filter(
                company=company, environment=environment,
                reference_date=reference_date,
            ).values_list('correlativo', flat=True))
        correlativo = (max(existing) + 1) if existing else 1
        summary = FiscalDailySummary.objects.create(
            company=company,
            identifier=packaging.summary_identifier(reference_date, correlativo),
            correlativo=correlativo, reference_date=reference_date,
            issue_date=issue_date, environment=environment,
            status=FiscalSummaryStatus.GENERATED)
        for line_id, doc in enumerate(boletas, 1):
            FiscalDailySummaryDocument.objects.create(
                summary=summary, document=doc, line_id=line_id,
                condition_code='1')
        return summary


def _build_summary_data(summary: FiscalDailySummary) -> SummaryData:
    rows = list(summary.lines.select_related('document').order_by('line_id'))
    if not rows:
        raise FiscalError('El resumen no tiene boletas.')
    issuer = rows[0].document  # todas comparten emisor: es de UNA empresa
    # Defensa en profundidad: el invariante «un resumen, un emisor» se afirma, no
    # se supone. La creación ya lo garantiza (scope por empresa); si alguna vez no,
    # se falla cerrado en vez de emitir un resumen con emisores mezclados.
    if any(row.document.issuer_tax_id != issuer.issuer_tax_id for row in rows):
        raise FiscalError('Un resumen no puede mezclar emisores distintos.')
    lines = tuple(
        SummaryLine(
            line_id=row.line_id, document_type=row.document.document_type,
            document_id=row.document.document_id,
            customer_doc_type=row.document.customer_doc_type or '0',
            customer_doc_number=row.document.customer_doc_number or '0',
            condition_code=row.condition_code,
            total=row.document.total,
            taxable_amount=row.document.taxable_amount,
            exempt_amount=Decimal('0.00'), unaffected_amount=Decimal('0.00'),
            tax_amount=row.document.tax_amount)
        for row in rows
    )
    return SummaryData(
        identifier=summary.identifier, issue_date=summary.issue_date,
        reference_date=summary.reference_date,
        supplier_ruc=issuer.issuer_tax_id,
        supplier_name=issuer.issuer_legal_name, lines=lines)


def sign_daily_summary(summary: FiscalDailySummary, *, key_pem: bytes,
                       cert_pem: bytes) -> FiscalDailySummary:
    """
    Construye el XML del resumen desde sus boletas (inmutables) y lo firma.

    Idempotente: si ya está firmado, se devuelve. Tras firmar, el `signed_xml` es
    la evidencia y no se reconstruye (§51).
    """
    if summary.signed_xml:
        return summary
    if summary.status not in (FiscalSummaryStatus.GENERATED,):
        raise FiscalError(
            f'Un resumen en estado «{summary.get_status_display()}» no se firma.')

    data = _build_summary_data(summary)
    xml = build_summary_xml(data)
    signed = signing.sign_invoice(
        etree.fromstring(xml), key_pem=key_pem, cert_pem=cert_pem)
    signed_bytes = etree.tostring(signed, xml_declaration=True, encoding='UTF-8')

    summary.signed_xml = signed_bytes.decode('utf-8')
    summary.signed_xml_sha256 = hashlib.sha256(signed_bytes).hexdigest()
    summary.status = FiscalSummaryStatus.SIGNED
    summary.save(update_fields=[
        'signed_xml', 'signed_xml_sha256', 'status', 'updated_at'])
    return summary


class FiscalSummaryInProgress(FiscalError):
    """Ya hay un envío en curso para este resumen. No se llama otra vez."""


def submit_daily_summary(summary: FiscalDailySummary, provider
                         ) -> FiscalDailySummary:
    """
    Envía el resumen con `sendSummary` y PERSISTE el ticket antes de nada (§8).

    Claim → red → finalize (§48/§50): se marca «en curso» bajo bloqueo, se suelta
    el bloqueo, se llama a SUNAT, y se guarda el ticket bajo bloqueo. Dos envíos
    simultáneos NO crean dos tickets: el segundo ve la marca y se retira. Un fallo
    de red deja el resumen reintentable, sin ticket inventado (§49).
    """
    if not summary.signed_xml:
        raise FiscalError('El resumen no está firmado.')
    if summary.is_accepted:
        return summary

    # CLAIM bajo bloqueo.
    limite = timezone.now() - timedelta(minutes=STALE_SUBMISSION_MINUTES)
    with transaction.atomic():
        fresh = FiscalDailySummary.objects.select_for_update().get(pk=summary.pk)
        if fresh.is_accepted:
            return fresh
        if fresh.ticket:
            # Ya tiene ticket: está en la cola de SUNAT. No se reenvía; se consulta.
            return fresh
        if fresh.submitting_since and fresh.submitting_since >= limite:
            raise FiscalSummaryInProgress(
                f'Ya hay un envío en curso para {fresh.identifier}. Espere.')
        fresh.submitting_since = timezone.now()
        fresh.save(update_fields=['submitting_since', 'updated_at'])

    name = packaging.summary_name(
        _issuer_ruc(fresh), fresh.reference_date, fresh.correlativo)
    zip_bytes = packaging.build_zip(name, fresh.signed_xml.encode('utf-8'))

    result = provider.send_summary(filename=f'{name}.ZIP', zip_bytes=zip_bytes)

    # FINALIZE bajo bloqueo.
    with transaction.atomic():
        fresh = FiscalDailySummary.objects.select_for_update().get(pk=summary.pk)
        fresh.submitting_since = None
        if fresh.ticket:  # otro envío ganó la carrera
            fresh.save(update_fields=['submitting_since', 'updated_at'])
            return fresh
        if result.outcome == SummaryOutcome.TICKET:
            fresh.ticket = result.ticket
            fresh.status = FiscalSummaryStatus.SUBMITTED
        else:
            # Transporte incierto o respuesta sin ticket: reintentable, sin ticket.
            fresh.status = FiscalSummaryStatus.SUBMISSION_ERROR
            fresh.sunat_response_code = result.status_code
            fresh.sunat_response_message = result.safe_message
        fresh.save(update_fields=[
            'submitting_since', 'ticket', 'status', 'sunat_response_code',
            'sunat_response_message', 'updated_at'])
    return fresh


def _issuer_ruc(summary: FiscalDailySummary) -> str:
    row = summary.lines.select_related('document').order_by('line_id').first()
    if row is None:
        raise FiscalError('El resumen no tiene boletas.')
    return row.document.issuer_tax_id


def poll_daily_summary(summary: FiscalDailySummary, provider) -> SummaryPollResult:
    """
    Consulta el ticket (`getStatus`) y aplica el CDR si el proceso terminó.

    `98` (en proceso) deja el resumen SUBMITTED (hay que volver a consultar). Un
    fallo de red NO pierde el ticket: se queda SUBMITTED, reintentable (§25). El
    CDR se aplica al RESUMEN entero (§52): un rechazo libera sus boletas para un
    resumen nuevo; una aceptación no cambia el estado individual de las boletas
    (su condición de «informada en resumen aceptado» la lleva la fila de inclusión).
    """
    if not summary.ticket:
        raise FiscalError(
            'El resumen no tiene ticket: no se ha enviado o el envío quedó '
            'incierto. Envíelo antes de consultar.')
    if summary.is_accepted or summary.status == FiscalSummaryStatus.REJECTED:
        return SummaryPollResult(summary, 'already_terminal',
                                 sunat_code=summary.sunat_response_code)

    result = provider.get_ticket_status(ticket=summary.ticket)

    if result.status == TicketStatus.PROCESSING:
        return SummaryPollResult(summary, 'processing', sunat_code=result.status_code)
    if result.status in (TicketStatus.TRANSPORT_ERROR, TicketStatus.UNKNOWN_RESPONSE):
        # No terminal: el ticket sigue vivo, se reintenta luego. No se pierde.
        return SummaryPollResult(summary, result.status.value,
                                 sunat_code=result.status_code,
                                 safe_message=result.safe_message)

    # COMPLETED / ERROR: debe traer un CDR. Sin él, no se concluye.
    cdr = result.cdr
    if cdr is None or not cdr.cdr_xml:
        return SummaryPollResult(summary, 'no_cdr', sunat_code=result.status_code)
    return _apply_summary_cdr(summary, cdr)


def _apply_summary_cdr(summary: FiscalDailySummary, cdr) -> SummaryPollResult:
    from .fiscal.cdr import CdrParseError, cdr_matches_document, parse_cdr

    if cdr.outcome not in _CDR_OUTCOME_TO_STATUS:
        # Llegó un CDR pero su código no resuelve a terminal: no terminal.
        return SummaryPollResult(summary, 'cdr_inconclusive',
                                 sunat_code=cdr.response_code)
    # BINDING (§42): el CDR debe corresponder a ESTE resumen (su ReferenceID es el
    # RC, no una boleta). Uno de otro resumen no se aplica.
    try:
        rep = parse_cdr(cdr.cdr_xml)
    except CdrParseError:
        return SummaryPollResult(summary, 'cdr_unreadable',
                                 sunat_code=cdr.response_code)
    if not cdr_matches_document(
            rep, document_id=summary.identifier,
            issuer_tax_id=_issuer_ruc(summary)):
        return SummaryPollResult(summary, 'cdr_mismatch',
                                 sunat_code=cdr.response_code)

    new_status = _CDR_OUTCOME_TO_STATUS[cdr.outcome]
    cdr_bytes = cdr.cdr_xml

    with transaction.atomic():
        fresh = FiscalDailySummary.objects.select_for_update().get(pk=summary.pk)
        if fresh.is_accepted or fresh.status == FiscalSummaryStatus.REJECTED:
            return SummaryPollResult(fresh, 'already_terminal',
                                     sunat_code=fresh.sunat_response_code)
        fresh.status = new_status
        fresh.sunat_response_code = cdr.response_code
        fresh.sunat_response_message = cdr.safe_message
        fresh.cdr_xml = cdr_bytes.decode('utf-8', 'replace')
        fresh.cdr_sha256 = hashlib.sha256(cdr_bytes).hexdigest()
        fresh.save(update_fields=[
            'status', 'sunat_response_code', 'sunat_response_message',
            'cdr_xml', 'cdr_sha256', 'updated_at'])
        if new_status == FiscalSummaryStatus.REJECTED:
            # El rechazo es del RESUMEN entero (§52). Se liberan sus boletas para
            # poder informarlas en un resumen nuevo; NO se anulan las boletas.
            fresh.lines.update(superseded=True)

    return SummaryPollResult(
        fresh, 'reconciled', sunat_code=cdr.response_code,
        safe_message=cdr.safe_message)
