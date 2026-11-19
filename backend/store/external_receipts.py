"""
NOTE-NOT-SUBSTITUTE — register a boleta or factura issued OUTSIDE the system.

The system's electronic invoicing is off in production, so each sale gets its
receipt through another SUNAT channel. This module lets a person with
`sales.fiscal.issue` say WHICH receipt that was, so the owner can reconcile the
sales pending their receipt (`receipt_coverage`) against SUNAT SOL.

What it never does: issue, sign, send, consult SUNAT, print or e-mail anything,
or set a SUNAT state. A registration is a statement typed by a person; the sale
reads «comprobante externo registrado», never «aceptado por SUNAT».
docs/nota-de-venta-verificacion-tributaria.md.

RULES
-----
  * only a current paid sale that owes a receipt (`receipt_coverage.owing`),
    and only while the system holds no settled receipt of its own;
  * one live registration per sale, and one per company + type + series +
    number (also held by the database);
  * a number the system itself issued for the company in production is refused
    (a BETA test number is not a receipt);
  * a factura never carries a B series, a boleta never an F one;
  * the issue date is not in the future;
  * a mistake is VOIDED with a reason (never edited, never deleted), and the
    sale goes back to pending.
"""
from __future__ import annotations

import re
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    AdminAuditLog, ExternalFiscalReceipt, FiscalDocument, FiscalDocumentType, FiscalEnvironment, Order,
)

SERIES_RE = re.compile(r'^[A-Z0-9]{4}$')
MAX_NUMBER = 99_999_999
REFERENCE_MIN, REFERENCE_MAX = 3, 500
VOID_REASON_MIN = 10

_TYPE_NAME = {FiscalDocumentType.INVOICE: 'Factura', FiscalDocumentType.RECEIPT: 'Boleta'}
#: A series starting with the other type's letter is a typing mistake.
_WRONG_INITIAL = {FiscalDocumentType.INVOICE: 'B', FiscalDocumentType.RECEIPT: 'F'}


class ExternalReceiptError(Exception):
    """A registration or a void that cannot be done as asked. Views answer 400."""


def type_name(document_type: str) -> str:
    return _TYPE_NAME.get(document_type, document_type)


def live_for(order):
    return ExternalFiscalReceipt.objects.filter(
        order=order, company_id=order.company_id, status=ExternalFiscalReceipt.STATUS_REGISTERED,
    ).first()


def payload(row: ExternalFiscalReceipt) -> dict:
    paid_day = timezone.localdate(row.order.paid_at) if row.order.paid_at else None
    return {
        'id': row.id,
        'document_type': row.document_type,
        'document_type_label': row.get_document_type_display(),
        'series': row.series,
        'number': row.number,
        'document_id': row.document_id,
        'issue_date': row.issue_date.isoformat(),
        'channel': row.channel,
        'channel_label': row.get_channel_display(),
        'reference': row.reference,
        'status': row.status,
        'status_label': row.get_status_display(),
        # Said, not judged: issued after the day the sale was charged. The
        # receipt was due at payment or delivery (RCP art. 5); the accountant
        # decides what that means.
        'issued_after_payment': bool(paid_day and row.issue_date > paid_day),
        'registered_by': getattr(row.registered_by, 'username', None),
        'registered_at': row.registered_at.isoformat() if row.registered_at else None,
        'voided_by': getattr(row.voided_by, 'username', None),
        'voided_at': row.voided_at.isoformat() if row.voided_at else None,
        'void_reason': row.void_reason,
    }


def _clean(data: dict) -> dict:
    document_type = str(data.get('document_type') or '').strip()
    if document_type not in _TYPE_NAME:
        raise ExternalReceiptError('Elige el tipo: factura (01) o boleta (03).')
    series = str(data.get('series') or '').strip().upper()
    if not SERIES_RE.match(series):
        raise ExternalReceiptError('La serie tiene cuatro letras o números (por ejemplo E001, EB01, F001, B001).')
    if series.startswith(_WRONG_INITIAL[document_type]):
        raise ExternalReceiptError(
            f'Una {type_name(document_type).lower()} no lleva una serie que empiece por '
            f'{_WRONG_INITIAL[document_type]}: revisa el tipo o la serie.')
    raw_number = str(data.get('number') or '').strip()
    if not raw_number.isdigit() or not 1 <= int(raw_number) <= MAX_NUMBER:
        raise ExternalReceiptError('El número es un entero de 1 a 99999999, sin la serie.')
    try:
        issue_date = date.fromisoformat(str(data.get('issue_date') or '').strip())
    except ValueError:
        raise ExternalReceiptError('Escribe la fecha de emisión (AAAA-MM-DD).') from None
    if issue_date > timezone.localdate():
        raise ExternalReceiptError('La fecha de emisión no puede ser futura.')
    channel = str(data.get('channel') or '').strip()
    if channel not in ExternalFiscalReceipt.Channel.values:
        raise ExternalReceiptError('Elige el medio por el que se emitió.')
    reference = str(data.get('reference') or '').strip()
    if not REFERENCE_MIN <= len(reference) <= REFERENCE_MAX:
        raise ExternalReceiptError(
            'Escribe una referencia verificable (consulta en SOL, enlace, archivo o identificador), '
            f'de {REFERENCE_MIN} a {REFERENCE_MAX} caracteres.')
    return {'document_type': document_type, 'series': series, 'number': int(raw_number),
            'issue_date': issue_date, 'channel': channel, 'reference': reference}


def register(order, data: dict, *, actor, request=None) -> ExternalFiscalReceipt:
    """Record the external receipt of `order`. `ExternalReceiptError` says why not."""
    from . import receipt_coverage

    fields = _clean(data)
    with transaction.atomic():
        # The order lock first, like every writer of a sale's documents.
        locked = Order.objects.select_for_update(of=('self',)).filter(pk=order.pk).first()
        if locked is None or not receipt_coverage.owing(Order.objects.filter(pk=order.pk)).exists():
            raise ExternalReceiptError(
                'Sólo se registra el comprobante de una venta pagada y vigente que lo debe '
                '(no de un pedido sin cobrar, cancelado o cobrado con claves de pruebas).')
        if receipt_coverage.settled_system_receipts().filter(order=order, company_id=order.company_id).exists():
            raise ExternalReceiptError('Esta venta ya tiene un comprobante electrónico aceptado en el sistema.')
        current = live_for(order)
        if current is not None:
            raise ExternalReceiptError(
                f'Esta venta ya tiene registrado {type_name(current.document_type).lower()} '
                f'{current.document_id}. Anúlalo con su motivo antes de registrar otro.')
        # Only a PRODUCTION number is a real receipt: a BETA test may have used
        # the same series and number as a provider's real one.
        if FiscalDocument.objects.filter(
                company_id=order.company_id, document_type=fields['document_type'],
                series=fields['series'], number=fields['number'],
                environment=FiscalEnvironment.PRODUCTION).exists():
            raise ExternalReceiptError('Ese número es de un comprobante emitido por el sistema: no se registra aquí.')
        clash = ExternalFiscalReceipt.objects.filter(
            company_id=order.company_id, document_type=fields['document_type'], series=fields['series'],
            number=fields['number'], status=ExternalFiscalReceipt.STATUS_REGISTERED).first()
        if clash is not None:
            raise ExternalReceiptError(
                f'{type_name(fields["document_type"])} {clash.document_id} ya está registrada en el pedido '
                f'N.º {clash.order_id}.')
        try:
            with transaction.atomic():
                row = ExternalFiscalReceipt.objects.create(
                    company_id=order.company_id, order=order, registered_by=actor, **fields)
        except IntegrityError:
            # A concurrent registration won the race on one of the two unique rules.
            raise ExternalReceiptError(
                'Otra persona acaba de registrar ese comprobante o uno para esta venta. Vuelve a cargar el pedido.'
            ) from None
        AdminAuditLog.log(
            actor=actor, action='external_receipt_registered', target_type='external_fiscal_receipt',
            target_id=row.pk, request=request, company=order.company,
            metadata={'order_id': order.pk, 'document_type': row.document_type, 'series': row.series,
                      'number': row.number, 'issue_date': row.issue_date.isoformat(), 'channel': row.channel},
        )
    return row


def void(row: ExternalFiscalReceipt, reason, *, actor, request=None) -> ExternalFiscalReceipt:
    """Void a registration typed by mistake. The sale goes back to pending."""
    reason = str(reason or '').strip()
    if len(reason) < VOID_REASON_MIN:
        raise ExternalReceiptError(f'Explica el motivo de la anulación (al menos {VOID_REASON_MIN} caracteres).')
    with transaction.atomic():
        locked = ExternalFiscalReceipt.objects.select_for_update().get(pk=row.pk)
        if locked.status != ExternalFiscalReceipt.STATUS_REGISTERED:
            raise ExternalReceiptError('Ese registro ya está anulado.')
        locked.status = ExternalFiscalReceipt.STATUS_VOID
        locked.voided_by = actor
        locked.voided_at = timezone.now()
        locked.void_reason = reason[:1000]
        locked.save(update_fields=['status', 'voided_by', 'voided_at', 'void_reason'])
        AdminAuditLog.log(
            actor=actor, action='external_receipt_voided', target_type='external_fiscal_receipt',
            target_id=locked.pk, request=request, company=locked.company,
            metadata={'order_id': locked.order_id, 'document_type': locked.document_type,
                      'series': locked.series, 'number': locked.number, 'reason': locked.void_reason[:200]},
        )
    return locked
