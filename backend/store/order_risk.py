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
    SIGNAL_VELOCITY_CONTACT: 'El mismo teléfono, correo o documento en pedidos de distintos compradores en 24 horas.',
    SIGNAL_DOCUMENT_MISMATCH: 'El documento del pedido no es el del perfil del cliente.',
    SIGNAL_ARCHIVED_CUSTOMER: 'El cliente está archivado en la tienda.',
}

#: From which score an order is `medium`, and from which it is `high`.
MEDIUM_SCORE = 30
HIGH_SCORE = 60

#: This many orders from one origin within the window, this one included.
VELOCITY_IP_ORDERS = 3
VELOCITY_IP_WINDOW = timedelta(hours=1)

#: This many DIFFERENT buyers sharing a phone, an address or a document within
#: the window, this one included.
VELOCITY_CONTACT_BUYERS = 3
VELOCITY_CONTACT_WINDOW = timedelta(hours=24)
#: How many of the company's latest orders that look reads at most.
_VELOCITY_CONTACT_SCAN = 500


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
    return hmac.new(_origin_key(), f'{company.pk}:{address}'.encode('utf-8'), hashlib.sha256).hexdigest()


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
    needs_review = level == Level.HIGH or SIGNAL_ARCHIVED_CUSTOMER in signals
    return OrderRiskAssessment.objects.create(
        company=company, order=order, level=level, score=score, signals=signals, ip_hash=origin,
        review_status=ReviewStatus.PENDING if needs_review else ReviewStatus.NOT_REQUIRED,
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
    if _same_origin_too_often(company, origin):
        noticed.add(SIGNAL_VELOCITY_IP)
    if _contact_shared_by_buyers(order, company, document):
        noticed.add(SIGNAL_VELOCITY_CONTACT)
    if own is not None and own.document_number and (own.document_type, own.document_number) != document:
        noticed.add(SIGNAL_DOCUMENT_MISMATCH)
    if _archived_here(company, own, document):
        noticed.add(SIGNAL_ARCHIVED_CUSTOMER)
    return [code for code in SIGNALS if code in noticed]


def _has_paid_before(order, company, user) -> bool:
    """An earlier PAID order of this account — or, for a guest, of the client the shop matched."""
    if user is not None:
        whose = Q(user=user) | Q(customer__user=user)
    elif order.customer_id is not None:
        whose = Q(customer_id=order.customer_id)
    else:
        return False
    return Order.objects.filter(whose, company=company, status=Order.Status.PAID).exclude(pk=order.pk).exists()


def _reaches_review_amount(order, company) -> bool:
    settings_row = get_company_settings(company)
    amount = getattr(settings_row, 'risk_review_amount', None)
    # No amount named: the company has not asked for this rule.
    return amount is not None and order.total >= amount


def _same_origin_too_often(company, origin) -> bool:
    if not origin:
        return False
    earlier = OrderRiskAssessment.objects.filter(
        company=company, ip_hash=origin, created_at__gte=timezone.now() - VELOCITY_IP_WINDOW,
    ).count()
    return earlier + 1 >= VELOCITY_IP_ORDERS


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


def _contact_shared_by_buyers(order, company, document) -> bool:
    mine = _contact_keys(order.customer_phone, order.customer_email, *document)
    if not mine:
        return False
    recent = (
        Order.objects
        .filter(company=company, sales_channel=SalesChannel.ONLINE,
                created_at__gte=timezone.now() - VELOCITY_CONTACT_WINDOW)
        .exclude(pk=order.pk).order_by('-created_at')
        .values('pk', 'user_id', 'cart_session_key', 'customer_phone', 'customer_email',
                'document_type', 'document_number')[:_VELOCITY_CONTACT_SCAN]
    )
    buyers = {_buyer_key({'pk': order.pk, 'user_id': order.user_id, 'cart_session_key': order.cart_session_key})}
    for row in recent:
        theirs = _contact_keys(row['customer_phone'], row['customer_email'], row['document_type'], row['document_number'])
        if mine & theirs:
            buyers.add(_buyer_key(row))
    return len(buyers) >= VELOCITY_CONTACT_BUYERS


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
