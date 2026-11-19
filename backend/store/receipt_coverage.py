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
  external  no live production receipt of the system (none, or only rejected
            ones), and a person REGISTERED the one issued outside the system
            (`ExternalFiscalReceipt`, SUNAT SOL or another channel). Said as
            «comprobante externo registrado», never «aceptado»: the system did
            not check it with SUNAT. The accounting centre (view C) and its
            document tags read the same rule.
  refunded  the legacy status REFUNDED: charged, then refunded. Its receipt and
            credit note are reviewed in the accounting centre.
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
            `fiscal_services._refuse_rehearsal_payment`). The SAME rule as the
            accounting centre (`accounting.defaults.EXCLUDE_TEST_PAYMENTS`, P4):
            only AUTHORIZED charges count — a DUPLICATE charge is money to give
            back, not the sale's payment — and a charge of unknown environment
            is counted as real.

It never says «emitido» or «aceptado» without a settled production document:
the sales note, a BETA receipt or a requested `receipt_type` are not evidence.
"""
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q, QuerySet

from .models import (
    FISCAL_ORIGINAL_TYPES, ExternalFiscalReceipt, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalEnvironment,
    FiscalSummaryStatus, FiscalVoidStatus, Order, PaymentTransaction, SalesChannel,
)

SYSTEM, EXTERNAL, PENDING, NOT_DUE, CANCELLED, REFUNDED, TEST_PAYMENT = (
    'system', 'external', 'pending', 'not_due', 'cancelled', 'refunded', 'test_payment')

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
REFUNDED_NOTICE = (
    'Venta cobrada y luego reembolsada (estado antiguo): revisa en el centro contable si le falta su '
    'comprobante o su nota de crédito.'
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
    from .accounting import defaults

    if not defaults.EXCLUDE_TEST_PAYMENTS:
        return Q(pk__in=[])
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


def _settled_q() -> Exists:
    return Exists(settled_system_receipts().filter(order_id=OuterRef('pk'), company_id=OuterRef('company_id')))


def live_production_originals() -> QuerySet:
    """System boletas/facturas of production that SUNAT did not reject (settled or still on their way)."""
    return FiscalDocument.objects.filter(
        document_type__in=FISCAL_ORIGINAL_TYPES, environment=FiscalEnvironment.PRODUCTION,
    ).exclude(status=FiscalDocumentStatus.REJECTED)


def _external_q() -> Q:
    """A live external registration, and no live production receipt of the system beside it."""
    return Exists(ExternalFiscalReceipt.objects.filter(
        order_id=OuterRef('pk'), company_id=OuterRef('company_id'),
        status=ExternalFiscalReceipt.STATUS_REGISTERED)) & ~Exists(
        live_production_originals().filter(order_id=OuterRef('pk'), company_id=OuterRef('company_id')))


def pending(orders: QuerySet) -> QuerySet:
    """
    The sales of `orders` with NO receipt in the system: neither a settled one of
    its own nor one registered as issued outside. «Ventas sin comprobante».
    """
    return owing(orders).exclude(_settled_q()).exclude(_external_q())


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
    if order.status == Order.Status.REFUNDED:
        return REFUNDED
    if not order.paid or order.status != Order.Status.PAID:
        return NOT_DUE
    scoped = Order.objects.filter(pk=order.pk)
    if not owing(scoped).exists():
        return TEST_PAYMENT
    if scoped.filter(_settled_q()).exists():
        return SYSTEM
    if scoped.filter(_external_q()).exists():
        return EXTERNAL
    return PENDING


def _external_line(order) -> tuple[str, str]:
    row = ExternalFiscalReceipt.objects.filter(
        order=order, company_id=order.company_id, status=ExternalFiscalReceipt.STATUS_REGISTERED,
    ).first()
    if row is None:
        return '', ''
    kind = 'Factura' if row.document_type == FiscalDocumentType.INVOICE else 'Boleta'
    return row.document_id, (
        f'Comprobante externo registrado: {kind} {row.document_id} ({row.get_channel_display()}), '
        f'emitido el {row.issue_date:%d/%m/%Y}. El sistema no verifica su aceptación en SUNAT.')


def coverage(order) -> dict:
    """
    What the staff screens say about this sale's receipt.

    `{state, integration_enabled, warning, receipt}`: `warning` is '' unless a
    person has something to do or read; `receipt` names the settled system
    document or the registered external one («B001-12», «EB01-7»), '' otherwise.
    """
    state = _state(order)
    enabled = integration_enabled()
    receipt = ''
    if state == SYSTEM:
        document = settled_system_receipts().filter(order=order, company_id=order.company_id).order_by('-pk').first()
        receipt = document.document_id if document else ''
    warning = {PENDING: pending_warning(enabled), TEST_PAYMENT: TEST_PAYMENT_NOTICE,
               REFUNDED: REFUNDED_NOTICE}.get(state, '')
    if state == EXTERNAL:
        receipt, warning = _external_line(order)
    return {'state': state, 'integration_enabled': enabled, 'warning': warning, 'receipt': receipt}
