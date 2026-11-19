"""
NOTE-NOT-SUBSTITUTE — does this sale have its official receipt?

The owner's requirement before the receipts release: the system must never hand
the sales note over as a substitute for the boleta or factura (TUO del Código
Tributario, art. 174 num. 1), and the staff must know, sale by sale, when the
receipt still has to be issued somewhere else. Legal basis and audit:
`docs/nota-de-venta-verificacion-tributaria.md`.

WHAT THIS MODULE SAYS, AND WHAT IT NEVER SAYS
---------------------------------------------
It READS. It issues nothing, signs nothing, sends nothing and never changes a
fiscal document. It answers one question for the staff screens: is there
EVIDENCE in the system that this sale has its electronic receipt?

  system    a boleta or factura issued by the system in PRODUCTION and settled
            (accepted with its CDR, or reported by an accepted daily summary)
            and not voided. A BETA document is a test: it is never evidence.
  pending   a current paid sale with no such evidence. The warning tells the
            staff to issue it through SUNAT (SOL or another channel) at payment
            or delivery, whichever comes first (RCP art. 5).
  not_due   nothing was charged (pending payment, failed, expired). An order is
            not delivered before it is paid (`order_shortfall.not_paid_detail`),
            so the receipt is not due yet.
  cancelled the order was cancelled; what it still owes (a credit note, a
            refund) is the accounting centre's view C.
  test_payment  a web order whose every authorised charge used the gateway's
            TEST keys: no real money, no receipt (the H-11 rule of
            `fiscal_services._refuse_rehearsal_payment`).

It never says «emitido» or «aceptado» without a settled production document:
the sales note, a BETA receipt or a requested `receipt_type` are not evidence.
"""
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q, QuerySet

from .models import (
    FISCAL_ORIGINAL_TYPES, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalEnvironment,
    FiscalSummaryStatus, FiscalVoidStatus, Order, PaymentTransaction, SalesChannel,
)

SYSTEM, PENDING, NOT_DUE, CANCELLED, TEST_PAYMENT = 'system', 'pending', 'not_due', 'cancelled', 'test_payment'

#: The staff warning when the sale has no receipt and the system's electronic
#: invoicing is OFF (production today: «SUNAT · Facturación electrónica:
#: desactivada» in `ops_status` / the healthcheck). Never printed on a note.
PENDING_INTEGRATION_OFF = (
    'Esta venta no tiene comprobante electrónico emitido por el sistema: emite la boleta o factura '
    'por SUNAT (SOL u otro medio) al cobrar o entregar. La facturación electrónica del sistema está '
    'desactivada; la nota de venta no reemplaza al comprobante.'
)
#: The same warning while the integration is ON: this version operates only
#: SUNAT's BETA (test) environment, whose documents have no tax validity.
PENDING_INTEGRATION_BETA = (
    'Esta venta no tiene un comprobante electrónico aceptado por SUNAT en el sistema: emite la boleta '
    'o factura por SUNAT (SOL u otro medio) al cobrar o entregar. La emisión del sistema opera sólo en el '
    'entorno de pruebas de SUNAT, sin validez tributaria; la nota de venta no reemplaza al comprobante.'
)
TEST_PAYMENT_NOTICE = (
    'Cobrada con las claves de pruebas de la pasarela: no es un cobro real y no lleva comprobante.'
)

_ACCEPTED = (FiscalDocumentStatus.ACCEPTED, FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION)
_SUMMARY_ACCEPTED = (FiscalSummaryStatus.ACCEPTED, FiscalSummaryStatus.ACCEPTED_WITH_OBSERVATION)
_VOID_ACCEPTED = (FiscalVoidStatus.ACCEPTED, FiscalVoidStatus.ACCEPTED_WITH_OBSERVATION)


def settled_system_receipts() -> QuerySet:
    """
    Original boletas and facturas that are EVIDENCE of a receipt: production,
    settled, not voided. One definition, for one sale and for a list.
    """
    return (
        FiscalDocument.objects.filter(
            document_type__in=FISCAL_ORIGINAL_TYPES, environment=FiscalEnvironment.PRODUCTION,
        )
        .filter(
            Q(status__in=_ACCEPTED)
            | Q(document_type=FiscalDocumentType.RECEIPT,
                summary_inclusions__superseded=False,
                summary_inclusions__summary__status__in=_SUMMARY_ACCEPTED)
        )
        .exclude(
            void_inclusions__superseded=False,
            void_inclusions__void_communication__status__in=_VOID_ACCEPTED,
        )
    )


def _test_only_q() -> Q:
    """A web order charged only with TEST keys (and charged at all)."""
    authorised = PaymentTransaction.objects.filter(
        order_id=OuterRef('pk'), status=PaymentTransaction.Status.AUTHORIZED)
    return Q(sales_channel=SalesChannel.ONLINE) & Exists(authorised) & ~Exists(
        authorised.exclude(environment='test'))


def owing(orders: QuerySet) -> QuerySet:
    """The current paid sales of `orders` that owe a receipt (test-key charges left out)."""
    return (
        orders.filter(paid=True, status=Order.Status.PAID)
        .exclude(_test_only_q())
    )


def pending(orders: QuerySet) -> QuerySet:
    """The sales of `orders` with NO evidence of their receipt: «ventas sin comprobante»."""
    settled = settled_system_receipts().filter(order_id=OuterRef('pk'), company_id=OuterRef('company_id'))
    return owing(orders).exclude(Exists(settled))


def integration_enabled() -> bool:
    from .fiscal_config import fiscal_enabled

    try:
        return bool(fiscal_enabled())
    except Exception:  # an unreadable console row is «not issuing», never a 500 on a sale screen
        return False


def pending_warning(enabled: bool | None = None) -> str:
    enabled = integration_enabled() if enabled is None else enabled
    return PENDING_INTEGRATION_BETA if enabled else PENDING_INTEGRATION_OFF


def _state(order) -> str:
    if order.status == Order.Status.CANCELLED:
        return CANCELLED
    if not order.paid or order.status != Order.Status.PAID:
        return NOT_DUE
    scoped = Order.objects.filter(pk=order.pk)
    if not owing(scoped).exists():
        return TEST_PAYMENT
    if not pending(scoped).exists():
        return SYSTEM
    return PENDING


def coverage(order) -> dict:
    """
    What the staff screens say about this sale's receipt.

    `{state, integration_enabled, warning, receipt}`: `warning` is '' unless a
    person has something to do; `receipt` names the settled system document
    («B001-12») and is '' otherwise.
    """
    state = _state(order)
    enabled = integration_enabled()
    receipt = ''
    if state == SYSTEM:
        document = settled_system_receipts().filter(order=order, company_id=order.company_id).order_by('-pk').first()
        receipt = document.document_id if document else ''
    warning = {PENDING: pending_warning(enabled), TEST_PAYMENT: TEST_PAYMENT_NOTICE}.get(state, '')
    return {'state': state, 'integration_enabled': enabled, 'warning': warning, 'receipt': receipt}
