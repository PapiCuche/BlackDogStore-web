"""
Which online orders deserve a second look — CLIENT-SEC-01.

WHAT THIS IS: a list of plain rules, each with a name and a weight, run once
when an order is made on the web or in the app. What they notice becomes an
`OrderRiskAssessment`: the CODES of the rules, a score, a level, and whether a
person should look before the order leaves.

WHAT THIS IS NOT:

  * A JUDGE OF PEOPLE. It never refuses a checkout, never touches a payment and
    never cancels anything. A buyer is not told any of it, and is never asked
    for a photograph of a document or of their face.
  * A VERDICT. `pending` asks a person to look (`review`); what that person
    decides is written down and changes nothing else. Only a company that
    turned `hold_orders_pending_review` on waits for the answer before sending.
  * A PLACE FOR PERSONAL DATA. The row keeps which rule fired, never the phone,
    the address or the document that made it fire; and a keyed hash of where
    the order came from, never the address itself.

PROPORTIONAL ON PURPOSE. What most honest buyers look like — a first purchase
without an account — is `low` and asks for nobody. A person is asked to look
when several things line up, or when the shop has archived this client.

EVERYTHING IS ASKED INSIDE THE ORDER'S COMPANY. No rule counts another
company's orders, clients or addresses.

IT CANNOT COST A SALE. `assess` runs in a savepoint of its own and swallows
whatever goes wrong: the order is then simply not assessed (no row), which is
a state every reader already handles — sales at the till and orders older than
this module have no row either.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import accounts
from .client_ip import get_client_ip
from .commerce_notifications import emit_order_review_needed
from .company_settings import get_company_settings
from .identity_validation import phone_key
from .models import (
    Customer, Order, OrderRiskAssessment, SalesChannel,
    normalize_customer_email, normalize_document_number,
)

logger = logging.getLogger(__name__)

Level = OrderRiskAssessment.Level
ReviewStatus = OrderRiskAssessment.ReviewStatus

# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

SIGNAL_GUEST_CHECKOUT = 'guest_checkout'
SIGNAL_EMAIL_UNVERIFIED = 'email_unverified'
SIGNAL_FIRST_ORDER = 'first_order'
SIGNAL_HIGH_AMOUNT = 'high_amount'
SIGNAL_VELOCITY_IP = 'velocity_ip'
SIGNAL_VELOCITY_CONTACT = 'velocity_contact'
SIGNAL_DOCUMENT_MISMATCH = 'document_mismatch'
SIGNAL_ARCHIVED_CUSTOMER = 'archived_customer'

#: Every rule, in the order a row lists them.
SIGNALS = (
    SIGNAL_GUEST_CHECKOUT, SIGNAL_EMAIL_UNVERIFIED, SIGNAL_FIRST_ORDER, SIGNAL_HIGH_AMOUNT,
    SIGNAL_VELOCITY_IP, SIGNAL_VELOCITY_CONTACT, SIGNAL_DOCUMENT_MISMATCH, SIGNAL_ARCHIVED_CUSTOMER,
)

#: What each rule adds to the score. The first three describe most honest
#: buyers, so no two of them reach `MEDIUM_SCORE` together.
WEIGHTS = {
    SIGNAL_GUEST_CHECKOUT: 10,
    SIGNAL_EMAIL_UNVERIFIED: 15,
    SIGNAL_FIRST_ORDER: 10,
    SIGNAL_HIGH_AMOUNT: 30,
    SIGNAL_VELOCITY_IP: 30,
    SIGNAL_VELOCITY_CONTACT: 30,
    SIGNAL_DOCUMENT_MISMATCH: 25,
    SIGNAL_ARCHIVED_CUSTOMER: 40,
}

#: What the panel says for each rule. The reason, never the value behind it.
LABELS = {
    SIGNAL_GUEST_CHECKOUT: 'Compra sin cuenta.',
    SIGNAL_EMAIL_UNVERIFIED: 'La cuenta no ha verificado su correo.',
    SIGNAL_FIRST_ORDER: 'Es la primera compra pagada de este cliente.',
    SIGNAL_HIGH_AMOUNT: 'El total alcanza el monto de revisión de la tienda.',
    SIGNAL_VELOCITY_IP: 'Varios pedidos desde el mismo origen en la última hora.',
    SIGNAL_VELOCITY_CONTACT: 'El mismo teléfono, correo o documento en varios pedidos en 24 horas.',
    SIGNAL_DOCUMENT_MISMATCH: 'El documento del pedido no es el del perfil del cliente.',
    SIGNAL_ARCHIVED_CUSTOMER: 'El cliente está archivado en la tienda.',
}

#: From which score an order is `medium`, and from which it is `high`.
MEDIUM_SCORE = 30
HIGH_SCORE = 60

#: This many DIFFERENT buyers from one origin within the window, this one
#: included. Buyers, not orders: somebody whose card is declined and tries
#: again is one buyer, however many orders that leaves behind.
VELOCITY_IP_BUYERS = 3
VELOCITY_IP_WINDOW = timedelta(hours=1)
#: …or this many PAID ORDERS from one origin within the window, this one
#: included, whoever placed them: one browser that keeps buying is as much
#: «how often» as three that buy once. Paid ones only, so the orders a
#: declined card leaves behind are still one order.
VELOCITY_IP_ORDERS = 6

#: This many DIFFERENT buyers sharing a phone, an address or a document within
#: the window, this one included.
VELOCITY_CONTACT_BUYERS = 3
VELOCITY_CONTACT_WINDOW = timedelta(hours=24)
#: …or this many PAID ORDERS carrying one of them within the window, this one
#: included, whoever placed them.
VELOCITY_CONTACT_ORDERS = 6
#: How many of the company's latest orders that look reads at most.
_VELOCITY_CONTACT_SCAN = 500

# WHOSE ORDERS THE TWO VELOCITY RULES COUNT. This order, always. ANY OTHER
# order — another buyer's or this buyer's own — only once it was PAID. An order nobody paid for costs
# nothing to make: counted, anybody could mark a stranger's next order by
# leaving two unpaid ones with the stranger's phone on them, or fill a shop's
# review queue from one browser. A paid order cost its buyer money.


def level_for(score: int) -> str:
    if score >= HIGH_SCORE:
        return Level.HIGH
    if score >= MEDIUM_SCORE:
        return Level.MEDIUM
    return Level.LOW


# ---------------------------------------------------------------------------
# Where an order came from
# ---------------------------------------------------------------------------

def _origin_key() -> bytes:
    # Derived, not the secret itself: one use, one key.
    return hashlib.sha256(b'order-risk-origin|' + settings.SECRET_KEY.encode('utf-8')).digest()


def origin_hash(company, request) -> str:
    """
    A keyed hash of the address this request came from, or '' when there is none.

    Good for one question — «the same origin as that other order of this
    shop?» — and useless for any other: without the key it cannot be turned
    back into an address, and it is bound to the company, so the same address
    is a different value in every shop and two shops cannot compare buyers.
    """
    address = get_client_ip(request) if request is not None else None
    if not address:
        return ''
    return hmac.new(_origin_key(), f'{company.pk}:{_origin_of(address)}'.encode('utf-8'), hashlib.sha256).hexdigest()


def _origin_of(address: str) -> str:
    """
    What counts as «the same origin». An IPv4 address is itself. An IPv6 one is
    its /64: a household is given the whole /64 and its devices change address
    inside it, so the full address would be a different origin every time.
    """
    import ipaddress

    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        return address
    if parsed.version == 6:
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.ip_network(f'{parsed}/64', strict=False).network_address)
    return str(parsed)


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------

def for_order(order):
    """The assessment of this order, or None: it was not assessed."""
    return OrderRiskAssessment.objects.filter(order=order).first()


def assess(order, request=None):
    """
    Assess an order that was just made online. The row, or None.

    NEVER RAISES, and never breaks the transaction it is called in: it runs in
    a savepoint, and anything that goes wrong is logged — by its kind, without
    the words of the error, which may quote what the buyer typed — and leaves
    the order unassessed.
    """
    try:
        with transaction.atomic():
            return _assess(order, request)
    except Exception as failed:  # noqa: BLE001 — see docstring
        logger.error(
            'order risk assessment failed for order %s (%s); the order stays unassessed',
            getattr(order, 'pk', None), type(failed).__name__,
        )
        return None


def _assess(order, request):
    company = order.company
    if company is None or OrderRiskAssessment.objects.filter(order=order).exists():
        return None
    origin = origin_hash(company, request)
    signals = _signals(order, company, origin)
    score = sum(WEIGHTS[code] for code in signals)
    level = level_for(score)
    # NOBODY IS ASKED TO LOOK YET. What was noticed is written down now; a
    # review starts to wait when the order is PAID (`review_when_paid`). An
    # order that is never paid never reaches the queue or the bell.
    return OrderRiskAssessment.objects.create(
        company=company, order=order, level=level, score=score, signals=signals, ip_hash=origin,
        review_status=ReviewStatus.NOT_REQUIRED,
    )


def needs_review(row) -> bool:
    """Whether what was noticed about an order asks for a person: a high level, or an archived client."""
    return row.level == Level.HIGH or SIGNAL_ARCHIVED_CUSTOMER in (row.signals or [])


def review_when_paid(order):
    """
    An order has just been PAID: if what was noticed about it asks for a
    person, its review starts to wait now and whoever may decide is told. The
    row when that happened, else None.

    Called where a payment is confirmed — the signed notification and the
    answer of the gateway alike. NEVER RAISES and never breaks that
    transaction: a savepoint, and a failure logged by its kind. A payment is
    confirmed whether or not its review could be opened.

    Once: a row that is already waiting, or that somebody decided, is left as
    it is, so a replayed notification changes nothing.
    """
    try:
        with transaction.atomic():
            return _review_when_paid(order)
    except Exception as failed:  # noqa: BLE001 — see docstring
        logger.error(
            'order review could not be opened for paid order %s (%s)',
            getattr(order, 'pk', None), type(failed).__name__,
        )
        return None


def _review_when_paid(order):
    if order.status != Order.Status.PAID:
        return None
    row = OrderRiskAssessment.objects.select_for_update().filter(order=order).first()
    if row is None or row.review_status != ReviewStatus.NOT_REQUIRED:
        return None
    _look_again_at_velocity(order, row)
    if not needs_review(row):
        return None
    row.review_status = ReviewStatus.PENDING
    row.save(update_fields=['review_status', 'updated_at'])
    # Whoever may decide is told, in the panel. It cannot fail this.
    emit_order_review_needed(order, row)
    return row


def _look_again_at_velocity(order, row) -> None:
    """
    «How often» asked a second time, now that the order is paid.

    When an order is made, the orders made beside it are mostly unpaid and so
    not counted; by the time it is paid, they have been. Without this second
    look, several buyers who all order and then all pay were never noticed.

    It only ADDS: what was noticed when the order was made stays noticed, even
    if the hour has passed since. The score and the level follow the signals.

    In a savepoint of its own inside the caller's: if this look fails, the
    order is judged by what was already written — a review that was due is
    still opened — and the failure is logged by its kind.
    """
    try:
        with transaction.atomic():
            company = order.company
            document = (order.document_type or '', normalize_document_number(order.document_number))
            noticed = set(row.signals or [])
            if _origin_is_busy(order, company, row.ip_hash):
                noticed.add(SIGNAL_VELOCITY_IP)
            if _contact_is_busy(order, company, document):
                noticed.add(SIGNAL_VELOCITY_CONTACT)
            signals = [code for code in SIGNALS if code in noticed]
            if signals == list(row.signals or []):
                return
            score = sum(WEIGHTS[code] for code in signals)
            OrderRiskAssessment.objects.filter(pk=row.pk).update(
                signals=signals, score=score, level=level_for(score), updated_at=timezone.now(),
            )
            row.signals, row.score, row.level = signals, score, level_for(score)
    except Exception as failed:  # noqa: BLE001 — see docstring
        logger.error(
            'velocity could not be looked at again for paid order %s (%s)',
            getattr(order, 'pk', None), type(failed).__name__,
        )


def _signals(order, company, origin) -> list[str]:
    """The codes of the rules that noticed something, in the order of `SIGNALS`."""
    user = order.user
    own = Customer.objects.filter(company=company, user=user).first() if user is not None else None
    document = (order.document_type or '', normalize_document_number(order.document_number))

    noticed = set()
    if user is None:
        noticed.add(SIGNAL_GUEST_CHECKOUT)
    elif accounts.email_verified_at(user) is None:
        noticed.add(SIGNAL_EMAIL_UNVERIFIED)
    if not _has_paid_before(order, company, user):
        noticed.add(SIGNAL_FIRST_ORDER)
    if _reaches_review_amount(order, company):
        noticed.add(SIGNAL_HIGH_AMOUNT)
    if _origin_is_busy(order, company, origin):
        noticed.add(SIGNAL_VELOCITY_IP)
    if _contact_is_busy(order, company, document):
        noticed.add(SIGNAL_VELOCITY_CONTACT)
    if own is not None and own.document_number and (own.document_type, own.document_number) != document:
        noticed.add(SIGNAL_DOCUMENT_MISMATCH)
    if _archived_here(company, own, document):
        noticed.add(SIGNAL_ARCHIVED_CUSTOMER)
    return [code for code in SIGNALS if code in noticed]


def _has_paid_before(order, company, user) -> bool:
    """An earlier PAID order of this account — or, for a guest, of the client the shop matched."""
    paid = Order.objects.filter(company=company, status=Order.Status.PAID).exclude(pk=order.pk)
    if user is not None:
        # Two questions, each on its own index, rather than one OR across a join:
        # bought with the account, or filed by the shop under the account's record.
        return paid.filter(user=user).exists() or paid.filter(customer__user=user).exists()
    if order.customer_id is not None:
        return paid.filter(customer_id=order.customer_id).exists()
    return False


def _reaches_review_amount(order, company) -> bool:
    settings_row = get_company_settings(company)
    amount = getattr(settings_row, 'risk_review_amount', None)
    # No amount named: the company has not asked for this rule.
    return amount is not None and order.total >= amount


def _origin_is_busy(order, company, origin) -> bool:
    """
    Whether too much came from this order's origin in the window: this order
    plus the PAID orders that came from it — by how many different buyers, or
    by how many orders.
    """
    if not origin:
        return False
    others = list(
        OrderRiskAssessment.objects
        .filter(company=company, ip_hash=origin, created_at__gte=timezone.now() - VELOCITY_IP_WINDOW,
                order__status=Order.Status.PAID)
        .exclude(order_id=order.pk)
        .values('order_id', 'order__user_id', 'order__cart_session_key')
    )
    buyers = {_buyer_key({'pk': order.pk, 'user_id': order.user_id, 'cart_session_key': order.cart_session_key})}
    for row in others:
        buyers.add(_buyer_key({
            'pk': row['order_id'], 'user_id': row['order__user_id'], 'cart_session_key': row['order__cart_session_key'],
        }))
    return len(buyers) >= VELOCITY_IP_BUYERS or 1 + len(others) >= VELOCITY_IP_ORDERS


def _buyer_key(row) -> tuple:
    """
    Who placed an order, as far as «different buyers» goes: an account is
    itself; a guest is their browser's cart, so one person retrying a payment
    is one buyer; a guest order without a cart is nobody else's.
    """
    if row['user_id'] is not None:
        return ('account', row['user_id'])
    if row['cart_session_key']:
        return ('guest', row['cart_session_key'])
    return ('order', row['pk'])


def _contact_keys(phone, email, document_type, document_number) -> set:
    """The comparable form of each contact detail an order carries. Empty ones are left out."""
    keys = set()
    if phone_key(phone):
        keys.add(('phone', phone_key(phone)))
    if normalize_customer_email(email):
        keys.add(('email', normalize_customer_email(email)))
    number = normalize_document_number(document_number)
    if document_type and number:
        keys.add(('document', document_type, number))
    return keys


def _contact_is_busy(order, company, document) -> bool:
    """The same question about this order's phone, address and document."""
    mine = _contact_keys(order.customer_phone, order.customer_email, *document)
    if not mine:
        return False
    recent = (
        Order.objects
        .filter(company=company, sales_channel=SalesChannel.ONLINE, status=Order.Status.PAID,
                created_at__gte=timezone.now() - VELOCITY_CONTACT_WINDOW)
        .exclude(pk=order.pk).order_by('-created_at')
        .values('pk', 'user_id', 'cart_session_key', 'customer_phone', 'customer_email',
                'document_type', 'document_number')[:_VELOCITY_CONTACT_SCAN]
    )
    buyers = {_buyer_key({'pk': order.pk, 'user_id': order.user_id, 'cart_session_key': order.cart_session_key})}
    orders = 1
    for row in recent:
        theirs = _contact_keys(row['customer_phone'], row['customer_email'], row['document_type'], row['document_number'])
        if mine & theirs:
            buyers.add(_buyer_key(row))
            orders += 1
    return len(buyers) >= VELOCITY_CONTACT_BUYERS or orders >= VELOCITY_CONTACT_ORDERS


def _archived_here(company, own, document) -> bool:
    """Whether the account's record, or the record holding the order's document, is archived in this shop."""
    if own is not None and not own.is_active:
        return True
    document_type, number = document
    if not document_type or not number:
        return False
    return Customer.objects.filter(
        company=company, document_type=document_type, document_number=number, is_active=False,
    ).exists()


# ---------------------------------------------------------------------------
# The one thing a pending review may hold back — if the company says so
# ---------------------------------------------------------------------------

#: What staff are told when an order cannot leave yet.
REVIEW_PENDING_DETAIL = (
    'Este pedido tiene una revisión pendiente. Apruébala o recházala antes de marcarlo como listo para retiro, '
    'enviado o entregado.'
)


def review_holds_dispatch(order, company) -> bool:
    """
    Whether this order must wait for its review before it is sent or handed over.

    Only in a company that turned `hold_orders_pending_review` on, and only
    while the review is waiting: approving OR rejecting lets the order go — what
    the company asked for is that somebody looked. An order that was not
    assessed, or whose assessment asks for nobody, is never held.

    Asked of the database, by whoever is about to move the order and holds its
    row locked (`order_fulfillment_services`).
    """
    from .models import CompanySettings

    if not CompanySettings.objects.filter(company=company, hold_orders_pending_review=True).exists():
        return False
    return OrderRiskAssessment.objects.filter(
        order=order, company=company, review_status=ReviewStatus.PENDING, order__status=Order.Status.PAID,
    ).exists()


# ---------------------------------------------------------------------------
# What staff read
# ---------------------------------------------------------------------------

#: How a member of staff with no name on file is shown.
STAFF_WITHOUT_NAME = 'Personal de la tienda'


def describe(row, *, can_review: bool = False):
    """
    The assessment as staff read it, or None for an order that has none.

    The level, the reasons in plain words, and who decided what. NOT the origin
    hash: it answers a question only this module asks.

    `can_review` is whether THIS reader may decide — said by the server, so no
    screen keeps its own copy of the rule — and it is only ever true while the
    review is waiting.
    """
    if row is None:
        return None
    reviewer = row.reviewed_by
    return {
        'level': row.level,
        'level_label': row.get_level_display(),
        'score': row.score,
        'signals': [
            {'code': code, 'label': LABELS[code]} for code in row.signals if code in LABELS
        ],
        'review_status': row.review_status,
        'review_status_label': row.get_review_status_display(),
        # A name when the person has one; never the login, which is not a name to show.
        'reviewed_by': (reviewer.get_full_name().strip() or STAFF_WITHOUT_NAME) if reviewer is not None else None,
        'reviewed_at': row.reviewed_at,
        'review_note': row.review_note,
        'assessed_at': row.created_at,
        'can_review': bool(can_review) and row.review_status == ReviewStatus.PENDING,
    }


def list_fields(company, order_ids) -> dict:
    """`{order id: (level, review status)}` for the assessed ones among these orders of `company`."""
    return {
        order_id: (level, review_status)
        for order_id, level, review_status in OrderRiskAssessment.objects.filter(
            company=company, order_id__in=list(order_ids),
        ).values_list('order_id', 'level', 'review_status')
    }


def annotate_rows(company, rows) -> None:
    """Give each row of an order list its `risk_level` and `review_status`; None for the unassessed."""
    known = list_fields(company, [row['id'] for row in rows])
    for row in rows:
        row['risk_level'], row['review_status'] = known.get(row['id'], (None, None))


def filter_by_review(orders, raw):
    """
    Narrow an order queryset to one review state (`?review=pending`).

    A value that is not a state is ignored, like a date nobody can read: a typo
    should not empty the screen.
    """
    value = (raw or '').strip()
    if value not in ReviewStatus.values:
        return orders
    orders = orders.filter(risk_assessment__review_status=value)
    if value == ReviewStatus.PENDING:
        # The queue is of orders somebody paid for.
        orders = orders.filter(status=Order.Status.PAID)
    return orders


# ---------------------------------------------------------------------------
# The decision of a person
# ---------------------------------------------------------------------------

DECISION_APPROVE = 'approve'
DECISION_REJECT = 'reject'
#: What a reviewer may answer, and the state each answer leaves.
DECISIONS = {
    DECISION_APPROVE: ReviewStatus.APPROVED,
    DECISION_REJECT: ReviewStatus.REJECTED,
}
NOTE_MAX = OrderRiskAssessment._meta.get_field('review_note').max_length


class ReviewError(Exception):
    """A review that cannot be decided. `http_status` and `code` are the answer's."""

    http_status = 400
    code = 'review_refused'

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail

    def body(self) -> dict:
        return {'detail': self.detail, 'code': self.code}


class ReviewNotFound(ReviewError):
    http_status = 404
    code = 'not_assessed'


class ReviewNotPending(ReviewError):
    http_status = 409
    code = 'review_not_pending'


def review(*, order, company, actor, decision: str, note: str = '', request=None):
    """
    Write down what a person decided about an order that was waiting for it.

    The caller has established that `actor` may manage orders in `company` and
    found `order` among the ones they can see. What is decided HERE is whether
    there is anything left to decide: ONE decision per order. A second one —
    the same or the opposite, by the same person or another — is refused, and
    the first stands with its author.

    ADVICE. It changes the assessment and nothing else: not the order's
    payment, not its dispatch, and it cancels and refunds nothing. A company
    that holds orders until they are reviewed lets them go on EITHER answer —
    what it waits for is that somebody looked.

    The note stays on the assessment, for whoever reads the order. It is not
    copied to the audit entry: staff write names and phone numbers in it.
    """
    from .models import AdminAuditLog

    if decision not in DECISIONS:
        raise ReviewError('Indica si apruebas o rechazas la revisión.')
    with transaction.atomic():
        row = (
            OrderRiskAssessment.objects.select_for_update()
            .filter(order=order, company=company).first()
        )
        if row is None:
            raise ReviewNotFound('Este pedido no tiene una evaluación que revisar.')
        if row.review_status != ReviewStatus.PENDING:
            raise ReviewNotPending('La revisión de este pedido no está pendiente: ya se decidió o no hacía falta.')
        row.review_status = DECISIONS[decision]
        row.reviewed_by = actor
        row.reviewed_at = timezone.now()
        row.review_note = (note or '').strip()[:NOTE_MAX]
        row.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'review_note', 'updated_at'])
        AdminAuditLog.log(
            actor=actor,
            action='order_risk_reviewed',
            target_type='order',
            target_id=order.pk,
            company=company,
            # What was decided and about what KIND of order. Nothing of the
            # buyer, and not the note.
            metadata={
                'order_id': order.pk,
                'decision': row.review_status,
                'level': row.level,
                'signals': list(row.signals),
                'has_note': bool(row.review_note),
            },
            request=request,
        )
    return row
