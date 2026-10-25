"""
VIEW C — WHAT IS MISSING, INTERNAL OR INCONSISTENT (ADR §4.3).

Each rule has a STABLE code, a severity and a sentence for a person:

    regularizar   something the law or SUNAT still expects (a receipt, a note,
                  a summary) — described, never done from here
    revisar       something a person should look at (an uncertain send, a TEST
                  payment, a refund still owed)
    info          true and worth knowing, not a fault

THIS VIEW DOES NOT ACT. It has no button to issue, void or send: it names what
is missing and links to the order, whose own screen and capabilities decide.
An internal note has no tax validity, so a sale with only a note is NOT
settled: it is listed here.
"""
from __future__ import annotations

from collections import Counter

from store.models import Order

REGULARISE, REVIEW, INFO = 'regularizar', 'revisar', 'info'

RULES = {
    'NO_DOCUMENT': (REGULARISE, 'Venta pagada sin comprobante ni nota interna.'),
    'INTERNAL_ONLY': (REGULARISE, 'Sólo tiene nota de venta interna, que no tiene validez tributaria: '
                                  'la venta sigue sin comprobante.'),
    'REQUESTED_NOT_ISSUED': (REGULARISE, 'El comprador pidió boleta o factura y no se emitió.'),
    'FISCAL_NOT_SENT': (REGULARISE, 'El comprobante está generado o firmado y no se envió a SUNAT.'),
    'FISCAL_UNCERTAIN': (REVIEW, 'Envío sin constancia: consultar el estado antes de reintentar; '
                                 'no se emite otro.'),
    'FISCAL_REJECTED': (REGULARISE, 'SUNAT rechazó el comprobante y no hay otro vigente.'),
    'BOLETA_NOT_SUMMARISED': (REGULARISE, 'Boleta firmada que todavía no se informó en un resumen diario.'),
    'SUMMARY_PENDING': (REVIEW, 'La boleta está en un resumen que SUNAT aún no aceptó.'),
    'CANCELLED_WITH_LIVE_DOC': (REGULARISE, 'Venta anulada con su comprobante vigente: falta la nota de '
                                            'crédito o la baja.'),
    'REFUND_WITHOUT_NOTE': (REVIEW, 'Se devolvió dinero y el comprobante no tiene una nota de crédito aceptada.'),
    'NOTE_WITHOUT_REFUND': (REVIEW, 'Hay una nota de crédito aceptada sin un reembolso confirmado.'),
    'REFUND_PENDING': (REVIEW, 'Reembolso pendiente de confirmar.'),
    'INTERNAL_AND_FISCAL': (INFO, 'La venta tiene nota interna y comprobante: se cuenta una sola vez.'),
    'TEST_PAYMENT': (REVIEW, 'Pagada con credenciales de prueba: fuera del ingreso (regla provisional P4).'),
    'PAYMENT_ENV_UNKNOWN': (REVIEW, 'No se sabe si el pago fue de prueba o real: se cuenta como ingreso.'),
    'BETA_DOCUMENT': (REVIEW, 'El comprobante es del ambiente de pruebas (BETA): sin validez tributaria.'),
    'AMOUNT_MISMATCH': (REGULARISE, 'El total del comprobante no coincide con el de la venta.'),
    'BREAKDOWN_REBUILT': (INFO, 'Venta anterior al desglose congelado: base e impuesto reconstruidos.'),
    'NO_BRANCH': (INFO, 'Venta histórica sin sucursal.'),
    'SERVICE_NO_DOCUMENT': (REGULARISE, 'Cobro de servicio técnico sin comprobante.'),
    'CDR_HASH_MISMATCH': (REVIEW, 'El texto guardado de la constancia no reproduce su huella original.'),
    'PENDING_PAYMENT': (REVIEW, 'Pedido sin pagar: no es ingreso (regla provisional P2).'),
    'REFUNDED_STATUS': (INFO, 'Estado antiguo «Reembolsado»: se cuenta como venta pagada; el tablero de ventas '
                              'lo deja fuera.'),
}

#: What an ADJUSTMENT row (a refund this period on a sale of an earlier one)
#: brings to view C. Only the money codes: the rest of that sale's codes belong
#: to the period it was paid in, where they are already listed.
ADJUSTMENT_CODES = frozenset({'REFUND_PENDING', 'REFUND_WITHOUT_NOTE', 'NOTE_WITHOUT_REFUND'})

REPAIR_FLAGS = ('SERVICE_NO_DOCUMENT',)

UNSENT = frozenset({'generated_unsigned', 'signed_not_sent'})
UNCERTAIN = frozenset({'sent_no_cdr', 'submission_error'})
NOT_SUMMARISED = frozenset({'pending_summary', 'pending_summary_after_rejection'})
IN_SUMMARY = frozenset({'in_summary_not_sent', 'summary_sent', 'summary_uncertain'})


def sale_flags(facts, originals) -> list:
    """Every view-C code that applies to one sale, in the order of `RULES`."""
    from .operations import cdr_hash_ok

    order, live = facts.order, facts.live_original
    codes = {facts.states[d.pk]['code'] for d in facts.documents}
    live_code = facts.states[live.pk]['code'] if live is not None else None
    # ADR §4.3: the SUM of the settled credit notes against the SUM of the
    # confirmed refunds. A partial note must not hide a larger refund, and a
    # voided note settles nothing (`voided` is not in SETTLED).
    noted = sum((d.total for d in facts.settled_credit_notes()), 0)
    confirmed = sum((r.amount for r in facts.refunds_confirmed), 0)
    found = set()

    # An order kept out of income as a TEST payment (P4) is not a sale that
    # owes a receipt: it is listed as TEST_PAYMENT, and that is the whole story.
    owes_receipt = not facts.excluded
    if owes_receipt and live is None and not facts.note_issued:
        found.add('NO_DOCUMENT')
    if live is None and facts.note_issued:
        found.add('INTERNAL_ONLY')
    if owes_receipt and order.receipt_type in Order.ReceiptType.values and not originals:
        found.add('REQUESTED_NOT_ISSUED')
    if live_code in UNSENT:
        found.add('FISCAL_NOT_SENT')
    if codes & UNCERTAIN:
        found.add('FISCAL_UNCERTAIN')
    if originals and live is None:
        found.add('FISCAL_REJECTED')
    if codes & NOT_SUMMARISED:
        found.add('BOLETA_NOT_SUMMARISED')
    if codes & IN_SUMMARY:
        found.add('SUMMARY_PENDING')
    if (facts.cancellation is not None and facts.cancellation.fiscal_pending and live is not None
            and live_code != 'voided' and noted < live.total):
        found.add('CANCELLED_WITH_LIVE_DOC')
    if confirmed > noted and live is not None and live_code != 'voided':
        found.add('REFUND_WITHOUT_NOTE')
    if noted > confirmed:
        found.add('NOTE_WITHOUT_REFUND')
    if facts.refunds_pending:
        found.add('REFUND_PENDING')
    if facts.note_issued and live is not None:
        found.add('INTERNAL_AND_FISCAL')
    if facts.test_only:
        found.add('TEST_PAYMENT')
    elif facts.env_unknown:
        found.add('PAYMENT_ENV_UNKNOWN')
    if live is not None and live.environment == 'beta':
        found.add('BETA_DOCUMENT')
    if live is not None and live.total != order.total:
        found.add('AMOUNT_MISMATCH')
    if facts.breakdown_source == 'rebuilt':
        found.add('BREAKDOWN_REBUILT')
    if order.fulfillment_branch_id is None:
        found.add('NO_BRANCH')
    if order.status == Order.Status.REFUNDED:
        found.add('REFUNDED_STATUS')
    if any(cdr_hash_ok(d) is False for d in facts.documents):
        found.add('CDR_HASH_MISMATCH')
    return [code for code in RULES if code in found]


def entry(row: dict, code: str) -> dict:
    severity, message = RULES[code]
    return {
        'key': row['key'], 'code': code, 'severity': severity, 'message': message,
        'kind': row['kind'], 'order_id': row.get('order_id'), 'repair_order_id': row.get('repair_order_id'),
        'branch': row['branch'], 'channel': row['channel'], 'date': row['date'],
        'document': (row.get('primary_document') or {}).get('number'),
    }


def unpaid_entry(order, tz) -> dict:
    from django.utils import timezone

    severity, message = RULES['PENDING_PAYMENT']
    branch = order.fulfillment_branch
    return {
        'key': f'sale:{order.pk}', 'code': 'PENDING_PAYMENT', 'severity': severity, 'message': message,
        'kind': 'sale', 'order_id': order.pk, 'repair_order_id': None,
        'branch': {'id': branch.pk, 'name': branch.name} if branch else None,
        'channel': order.sales_channel, 'date': timezone.localtime(order.created_at, tz).isoformat(),
        'document': None,
    }


def entries(rows: list, unpaid: list, filters, tz) -> list:
    """
    View C over the rows of the period (already filtered) and its unpaid orders.

    An operation brings every code; an adjustment — a refund this period on a
    sale of an earlier one — brings only `ADJUSTMENT_CODES`. A key never lists
    the same code twice, however many rows it has.
    """
    out, seen = [], set()
    for row in rows:
        codes = row['flags'] if row['row_type'] == 'operation' else [
            c for c in row['flags'] if c in ADJUSTMENT_CODES]
        for code in codes:
            if (row['key'], code) in seen:
                continue
            seen.add((row['key'], code))
            out.append(entry(row, code))
    if not filters.kinds or 'sale' in filters.kinds:
        for order in unpaid:
            if filters.channels and order.sales_channel not in filters.channels:
                continue
            if filters.seller_id is not None and order.sold_by_id != filters.seller_id:
                continue
            if filters.documents or filters.operations:
                continue
            out.append(unpaid_entry(order, tz))
    return out


def counts(rows: list) -> dict:
    return dict(Counter(row['code'] for row in rows))

