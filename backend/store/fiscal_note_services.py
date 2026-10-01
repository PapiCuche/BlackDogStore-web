"""
Emitir una Nota de Crédito (07) o de Débito (08) sobre un comprobante original.

QUÉ ES UNA NOTA AQUÍ
--------------------
Un `FiscalDocument` más (tipo 07/08) que APUNTA al original (`original_document`)
con su motivo (Catálogo 09/10). Reutiliza toda la tubería de un comprobante:
serie, correlativo, firma, XSD, envío, CDR, reconciliación. Lo propio es la
relación con el original y el motivo.

EL ORIGINAL ES INMUTABLE (§15)
------------------------------
Emitir una nota NO toca el `signed_xml`, el CDR ni los totales del original. La
nota es un documento nuevo. Para una ANULACIÓN TOTAL (Catálogo 09 código 01 ó 06)
la nota refleja los MISMOS importes y líneas del original, que se leen de su XML
firmado —no de la Order, que pudo cambiar—.

FISCAL ≠ DINERO ≠ STOCK (§42/§43/§44)
-------------------------------------
Emitir una nota no devuelve dinero, no mueve stock, no cancela la Order ni el
Payment. Esos dominios se integran, explícitamente, en otra fase.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from lxml import etree

from .fiscal import builder, rules, schema, signing
from .fiscal.data import Line, NoteData, Party
from .fiscal.representation import parse_signed_invoice_for_representation
from .fiscal_services import FiscalError, _amount_in_words, _reserve
from .models import (
    FISCAL_NOTE_TYPES, FISCAL_ORIGINAL_TYPES, FiscalDocument,
    FiscalDocumentStatus, FiscalDocumentType,
)

#: Anulación total / devolución total: la nota refleja el original entero.
FULL_REVERSAL_CREDIT_REASONS = ('01', '06')


def _original_is_eligible(original: FiscalDocument) -> None:
    """El original debe existir y estar en un estado que admita una nota (§10)."""
    if original.document_type not in FISCAL_ORIGINAL_TYPES:
        raise FiscalError('Sólo una factura o una boleta admiten notas.')
    if not original.signed_xml:
        raise FiscalError('El comprobante original no está firmado.')

    # UNA NOTA Y UNA BAJA SON EXCLUYENTES, Y LA EXCLUSIÓN VA EN LAS DOS DIRECCIONES.
    #
    # La baja ya se niega cuando hay una nota aceptada en contra del original
    # (ERP-FISCAL-5B). Faltaba lo simétrico: emitir una nota sobre una numeración que
    # está dada de baja —o en camino de estarlo— afirmaría corregir un comprobante
    # que ante SUNAT dejó de estar vigente. Se mira `superseded=False`, el mismo
    # predicado que usa el lado de la baja: una comunicación RECHAZADA libera sus
    # filas, así que lo que queda vivo es lo aceptado y lo que está en vuelo. Ante
    # una baja en vuelo se falla CERRADO: si luego se acepta, la nota sobraría.
    from .models import FiscalVoidCommunicationDocument

    if FiscalVoidCommunicationDocument.objects.filter(
            document=original, superseded=False).exists():
        raise FiscalError(
            f'{original.document_id} está incluido en una comunicación de baja viva '
            f'o aceptada: no se le emite una nota. Corregir con una nota y dar de '
            f'baja la numeración son caminos excluyentes.')
    if original.document_type == FiscalDocumentType.INVOICE:
        # Una factura debe estar ACEPTADA por SUNAT (tiene CDR): sobre una que no
        # existe para SUNAT no se emite una nota. SIGNED no basta (§10).
        if not original.is_accepted:
            raise FiscalError(
                'La factura original aún no está aceptada por SUNAT; no se le '
                'puede emitir una nota.')
    else:  # boleta
        # NC-BOL / ND-BOL — PENDIENTE, y se falla CERRADO.
        #
        # Una nota de boleta NO se envía por `sendBill`: se informa por el Resumen
        # Diario (igual que la boleta que corrige), y el Resumen todavía no sabe
        # llevar líneas de nota (07/08). Emitir una que no se puede transmitir
        # sería entregar un callejón sin salida: un correlativo gastado en un
        # documento que nunca llega a SUNAT. Mientras la extensión del Resumen no
        # exista, esta fase no emite notas de boleta. (NC-FAC y ND-FAC sí.)
        raise FiscalError(
            'Una nota de una boleta se informa por el Resumen Diario, que aún no '
            'transmite líneas de nota. NC/ND de boleta queda para una fase '
            'posterior; esta fase emite notas de factura.')


def create_fiscal_note(original: FiscalDocument, *, note_type: str,
                       reason_code: str, reason_description: str,
                       request_key: str = '',
                       taxable_amount: Decimal | None = None,
                       tax_amount: Decimal | None = None) -> tuple[FiscalDocument, bool]:
    """
    Crea (sin firmar) una nota sobre `original`. Idempotente por `request_key`.

    Sin `taxable_amount`/`tax_amount`, la nota es una ANULACIÓN TOTAL: refleja los
    importes del original. Con importes explícitos (p. ej. una ND que aumenta el
    valor), la nota los usa. La red no entra aquí (igual que en la factura).
    """
    if note_type not in FISCAL_NOTE_TYPES:
        raise FiscalError(f'Tipo de nota no soportado: {note_type!r}.')

    # El MOTIVO se comprueba contra su catálogo AQUÍ, antes de reservar nada. Si se
    # dejara para la firma —como estaba—, un motivo inventado gastaba un correlativo
    # que no vuelve antes de que nadie lo rechazara (§ reserva no ocurre en un
    # camino que falla). El catálogo depende del tipo: 09 para la NC, 10 para la ND.
    catalog = rules.CATALOG_09 if note_type == FiscalDocumentType.CREDIT_NOTE \
        else rules.CATALOG_10
    which = '09' if note_type == FiscalDocumentType.CREDIT_NOTE else '10'
    if reason_code not in catalog:
        raise FiscalError(
            f'Motivo {reason_code!r} no está en el Catálogo N.º {which}.')

    _original_is_eligible(original)

    # Idempotencia: una misma clave sobre el mismo original da la MISMA nota.
    if request_key:
        existing = FiscalDocument.objects.filter(
            original_document=original, note_request_key=request_key,
        ).exclude(status=FiscalDocumentStatus.REJECTED).order_by('-pk').first()
        if existing is not None:
            return existing, False

    # Importes por TIPO, no por lo que traiga el cuerpo:
    #
    # - NOTA DE CRÉDITO: en esta fase es SIEMPRE una anulación total, así que
    #   refleja los importes del original. Un `taxable_amount`/`tax_amount` del
    #   cuerpo se IGNORA: dejar que el cliente fijara el importe de una NC de
    #   anulación abriría la puerta a una nota que no cuadra con lo que anula, y una
    #   NC parcial (con AllowanceCharge) es otra fase (§24). La vista tampoco los
    #   reenvía para una NC; esto es la defensa en profundidad.
    # - NOTA DE DÉBITO: es un cargo NUEVO; toma su base e IGV de quien la pide. Sin
    #   importe explícito, no hay cargo que declarar.
    if note_type == FiscalDocumentType.CREDIT_NOTE:
        taxable = original.taxable_amount
        tax = original.tax_amount
        total = original.total
    else:
        if taxable_amount is None or tax_amount is None:
            raise FiscalError(
                'Una nota de débito necesita el importe del cargo (base e IGV).')
        taxable = taxable_amount
        tax = tax_amount
        total = taxable + tax

    from .fiscal_config import resolve_note_series
    series = resolve_note_series(
        original.company, branch=original.order.fulfillment_branch,
        note_type=note_type, original_type=original.document_type)

    try:
        with transaction.atomic():
            number = _reserve(series)
            note = FiscalDocument.objects.create(
                order=original.order, company=original.company, series_ref=series,
                document_type=note_type, series=series.series, number=number,
                issued_at=timezone.now(), environment=series.environment,
                issuer_tax_id=original.issuer_tax_id,
                issuer_legal_name=original.issuer_legal_name,
                issuer_trade_name=original.issuer_trade_name,
                issuer_address=original.issuer_address,
                customer_doc_type=original.customer_doc_type,
                customer_doc_number=original.customer_doc_number,
                customer_legal_name=original.customer_legal_name,
                currency=original.currency,
                taxable_amount=taxable, tax_amount=tax, total=total,
                tax_rate=original.tax_rate, status=FiscalDocumentStatus.GENERATED,
                original_document=original, note_reason_code=reason_code,
                note_reason_description=reason_description,
                note_request_key=request_key)
    except IntegrityError:
        # Carrera de idempotencia: otra petición con la misma clave ganó.
        if request_key:
            existing = FiscalDocument.objects.filter(
                original_document=original, note_request_key=request_key,
            ).exclude(status=FiscalDocumentStatus.REJECTED).order_by('-pk').first()
            if existing is not None:
                return existing, False
        raise
    return note, True


def _note_lines(note: FiscalDocument) -> tuple[Line, ...]:
    """
    Las líneas de la nota, elegidas por el TIPO —la intención—, no por si los
    importes coinciden con los del original:

    - NOTA DE CRÉDITO (anulación total): las líneas del ORIGINAL, leídas de su XML
      firmado (inmutable), incluida su TASA declarada (`cbc:Percent`). Reflejar el
      original al pie de la letra exige tomar su porcentaje tal cual (18.00), no
      recomputarlo desde impuesto/base —eso produciría 18.06 en un precio no
      redondo y contradiría la línea que la nota debe espejar (REVIEW 5A)—.
    - NOTA DE DÉBITO (cargo explícito): una sola línea gravada por el importe dado.

    Antes se decidía por igualdad de importes, lo que confundía una ND cuyo importe
    casualmente igualaba al original con una anulación total (REVIEW 5A).
    """
    if note.document_type == FiscalDocumentType.CREDIT_NOTE:
        rep = parse_signed_invoice_for_representation(note.original_document.signed_xml)
        lines = []
        for rl in rep.lines:
            # La tasa del original, verbatim. Si el original no la declarara
            # (documento antiguo o ilegible), se cae al cálculo como último recurso.
            percent = rl.tax_percent if rl.tax_percent > 0 else (
                Decimal('0') if rl.line_amount == 0
                else (rl.tax_amount / rl.line_amount * 100).quantize(Decimal('0.01')))
            lines.append(Line(
                description=rl.description, quantity=rl.quantity, unit_code='NIU',
                unit_price=rl.unit_price, unit_price_with_tax=rl.unit_price_with_tax,
                line_amount=rl.line_amount, tax_amount=rl.tax_amount,
                tax_percent=percent))
        if lines:
            return tuple(lines)

    # Nota de débito (o una NC sin líneas legibles en el original): una línea
    # gravada por el importe de la nota.
    unit = (note.taxable_amount).quantize(Decimal('0.0000000001'))
    percent = (Decimal('0') if note.taxable_amount == 0
               else (note.tax_amount / note.taxable_amount * 100).quantize(
                   Decimal('0.01')))
    return (Line(
        description=note.note_reason_description[:250] or 'AJUSTE',
        quantity=Decimal('1'), unit_code='NIU', unit_price=unit,
        unit_price_with_tax=note.total, line_amount=note.taxable_amount,
        tax_amount=note.tax_amount, tax_percent=percent),)


def _note_to_data(note: FiscalDocument) -> NoteData:
    issued = timezone.localtime(note.issued_at)
    return NoteData(
        document_type=note.document_type, serie=note.series,
        correlativo=note.number, issue_date=issued.date(),
        issue_time=issued.time().replace(microsecond=0),
        currency=note.currency,
        supplier=Party(doc_type='6', doc_number=note.issuer_tax_id,
                       legal_name=note.issuer_legal_name,
                       trade_name=note.issuer_trade_name,
                       address_line=note.issuer_address),
        customer=Party(doc_type=note.customer_doc_type,
                       doc_number=note.customer_doc_number,
                       legal_name=note.customer_legal_name),
        lines=_note_lines(note),
        taxable_amount=note.taxable_amount, tax_amount=note.tax_amount,
        total=note.total,
        amount_in_words=_amount_in_words(note.total, note.currency),
        original_id=note.original_document.document_id,
        original_type=note.original_document.document_type,
        reason_code=note.note_reason_code,
        reason_description=note.note_reason_description)


def sign_fiscal_note(note: FiscalDocument, *, key_pem: bytes,
                     cert_pem: bytes) -> FiscalDocument:
    """Construye el XML de la nota, lo firma, lo valida y lo guarda."""
    if note.signed_xml:
        return note

    data = _note_to_data(note)
    try:
        rules.validate_note(data)
    except rules.FiscalRuleError as exc:
        raise FiscalError(str(exc)) from exc

    root = etree.fromstring(builder.build_note_xml(data))
    signed = signing.sign_invoice(root, key_pem=key_pem, cert_pem=cert_pem)
    xml = etree.tostring(signed, xml_declaration=True, encoding='UTF-8')

    validate = (schema.validate_credit_note
                if note.document_type == FiscalDocumentType.CREDIT_NOTE
                else schema.validate_debit_note)
    try:
        validate(xml)
    except schema.SchemaError as exc:
        raise FiscalError(str(exc)) from exc

    note.signed_xml = xml.decode('utf-8')
    note.signed_xml_sha256 = hashlib.sha256(xml).hexdigest()
    note.digest_value = signing.digest_value(signed)
    note.status = FiscalDocumentStatus.SIGNED
    note.save(update_fields=[
        'signed_xml', 'signed_xml_sha256', 'digest_value', 'status', 'updated_at'])
    return note
