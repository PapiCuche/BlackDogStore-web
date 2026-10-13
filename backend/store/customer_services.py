"""
Customer resolution and duplicate detection — SaaS Phase 4.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
------------------------------------------
Two people are never merged on a guess.

Deciding that "Juan Pérez, juan@gmail.com" and "Juan Perez, juanp@gmail.com" are
the same person is a coin flip, and losing the flip means one client can read
another client's purchase history, address and internal notes from inside their
own file. That is a privacy breach produced by a convenience feature, so this
module does not offer the feature.

What it offers instead:

  * MATCHING is deterministic. Only keys strong enough to be an identity are
    allowed to attach a sale to an existing person: the account they logged in
    with, or the document number they presented. Both are exact-match.

  * DUPLICATE DETECTION is advisory. A shared email or phone raises a SUGGESTION
    for a human to look at, and never merges anything. Families share an inbox;
    an office shares a landline; a receptionist's mobile ends up on twenty
    records. Those are not the same client and the software must not decide they
    are.

Merging two existing customers is deliberately NOT implemented — see
docs/saas-multiempresa.md. It has to move orders, and in later phases devices,
repair orders and warranties. A merge that moves some of those and not the
others is worse than no merge at all.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from .models import (
    AdminAuditLog,
    Customer,
    Order,
    RepairOrder,
    normalize_customer_email,
    normalize_customer_phone,
    normalize_document_number,
)


logger = logging.getLogger(__name__)


class CustomerError(Exception):
    """A customer could not be resolved or created."""


class DuplicateCustomerError(CustomerError):
    """A customer with this exact document already exists in this company."""

    def __init__(self, existing):
        self.existing = existing
        super().__init__(
            f'Ya existe un cliente con ese documento en esta empresa '
            f'(#{existing.pk}).'
        )


# ---------------------------------------------------------------------------
# Deterministic matching
# ---------------------------------------------------------------------------

def match_by_user(company, user):
    """The company's CRM record for this account, if it has one."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return Customer.objects.filter(company=company, user=user).first()


def match_by_document(company, document_type, document_number):
    """
    The company's record holding this exact document.

    Exact, normalised, and scoped to the company. A document is the strongest
    key available at a counter: the client is holding it.
    """
    number = normalize_document_number(document_number)
    if not number or not document_type:
        return None
    return Customer.objects.filter(
        company=company, document_type=document_type, document_number=number,
    ).first()


def resolve_customer(company, *, user=None, document_type='', document_number=''):
    """
    The existing customer this identity belongs to, or None.

    Order matters. The account is checked first because it is the only key the
    person authenticated against; the document second because it is the only key
    a walk-in client can prove. Nothing else is consulted.
    """
    return (
        match_by_user(company, user)
        or match_by_document(company, document_type, document_number)
    )


# ---------------------------------------------------------------------------
# Advisory duplicate detection
# ---------------------------------------------------------------------------

def find_possible_duplicates(company, *, email='', phone='', exclude_pk=None, limit=5):
    """
    Records that MIGHT be the same client — for a human to judge, not to act on.

    Returned as a warning alongside a successful create. Blocking on a shared
    email would make the common, legitimate case (a couple, a small office)
    impossible to file, and blocking is not needed anyway: nothing downstream
    treats these as the same person.
    """
    email = normalize_customer_email(email)
    phone = normalize_customer_phone(phone)
    if not email and not phone:
        return []

    from django.db.models import Q

    criteria = Q()
    if email:
        criteria |= Q(email=email)
    if phone:
        criteria |= Q(phone=phone)

    qs = Customer.objects.filter(company=company).filter(criteria)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return list(qs[:limit])


def is_established(record) -> bool:
    """
    Whether THE SHOP stands behind this record: staff filed it, or it has a
    paid order or a repair.

    Everything else is provisional — a record an unpaid checkout made, or one
    an account declared for itself in «Mi cuenta». Making one costs nothing
    and proves nothing about whose the document on it is, so a provisional
    record is never where a document sends anybody: not a guest's next order
    (`_guest_record`), not staff filing the person in front of them
    (`free_document_for_staff`).

    An account's own paid order in this shop counts for its record even when
    the order was never linked to it.
    """
    if record.created_by_id is not None and record.created_by_id != record.user_id:
        return True                     # filed by staff, not by its own subject
    theirs = Q(customer=record)
    if record.user_id is not None:
        theirs |= Q(user_id=record.user_id)
    if Order.objects.filter(theirs, company_id=record.company_id, status=Order.Status.PAID).exists():
        return True
    return RepairOrder.objects.filter(customer=record).exists()


#: The audit entry of a provisional record giving its document up.
DOCUMENT_RELEASED = 'customer_document_released'


def _release_document(holder, *, company, actor, taken_by: str, request=None) -> bool:
    """
    Take the document off a PROVISIONAL record, and write down that it happened.
    False when the record could not be saved, and then nothing was changed.

    The record stays — its orders, its contact, its account if it has one — and
    nobody is told. FIELD NAMES ONLY in the entry: which record gave up which
    part of itself, and to whom (staff, or an order that was paid for).

    A record's save judges the WHOLE record, so one that was already invalid
    for another reason (no name at all) cannot be saved even to take a document
    off it. That is not this caller's to repair: the document stays where it
    is, and each caller has its own answer for that.
    """
    kept = holder.document_type, holder.document_number
    holder.document_type = ''
    holder.document_number = ''
    try:
        holder.save(update_fields=['document_type', 'document_number', 'updated_at'])
    except ValidationError:
        holder.document_type, holder.document_number = kept
        return False
    AdminAuditLog.log(
        actor=actor, action=DOCUMENT_RELEASED, target_type='customer', target_id=holder.pk,
        metadata={'changed_fields': ['document_number', 'document_type'], 'taken_by': taken_by},
        request=request, company=company,
    )
    return True


def free_document_for_staff(company, document_type, document_number, *, actor, request=None) -> None:
    """
    Staff are about to file this document on a record. Refuse it, or free it.

    AN ESTABLISHED record holding it answers what `assert_document_available`
    always answered: `DuplicateCustomerError`, with the record, so the screen
    offers to open it. A PROVISIONAL one gives the document up: at the counter
    the person is there with the document in hand, and a record nobody stands
    behind must not be where the shop files them — its contact would be told
    about their repairs. Call it in the transaction that saves the record the
    document is going to, BEFORE that save and only when the document is
    changing (the record itself still holds its old one), so it is both or
    neither.
    """
    number = normalize_document_number(document_number)
    if not number or not document_type:
        return
    holder = Customer.objects.select_for_update().filter(
        company=company, document_type=document_type, document_number=number,
    ).first()
    if holder is None:
        return
    if is_established(holder):
        raise DuplicateCustomerError(holder)
    if not _release_document(holder, company=company, actor=actor, taken_by='staff', request=request):
        # It could not give the document up: staff are shown the record, as before.
        raise DuplicateCustomerError(holder)


def assert_document_available(company, document_type, document_number, *, exclude_pk=None):
    """
    Refuse a document that already identifies somebody else here.

    This one DOES block, because a document is an identity claim: two records
    with the same document in one company are not two clients, they are one
    client entered twice, and letting both exist means half the history goes to
    each. The database constraint says the same thing; this exists to say it as
    a 409 with the offending record attached, rather than as a 500.
    """
    number = normalize_document_number(document_number)
    if not number or not document_type:
        return
    qs = Customer.objects.filter(
        company=company, document_type=document_type, document_number=number,
    )
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    existing = qs.first()
    if existing is not None:
        raise DuplicateCustomerError(existing)


# ---------------------------------------------------------------------------
# Checkout integration
# ---------------------------------------------------------------------------

def link_order_to_customer(order, *, actor=None):
    """
    Attach a freshly created order to a CRM record, creating one if needed.

    POLICY, stated explicitly because §48 requires a choice:

      * AUTHENTICATED buyer → resolve or create `Customer(company, user)`. The
        account is proof of identity and the link is unambiguous.

      * ANONYMOUS buyer → match on the document, which checkout always validates
        and therefore always has — but ONLY a record with no account behind it
        (`_guest_record`); a document that is on somebody's account leaves the
        order unlinked. If no record matches, CREATE one: somebody who
        just gave a validated document, a phone and an address, and is about to
        pay, is a client of this business. Leaving them out would mean the CRM is
        missing precisely the people who bought something.

      * ANY failure → leave `order.customer` NULL and let the sale proceed.

    That last point is the important one. A CRM problem must never cost a sale,
    and the snapshot fields on the order preserve everything needed to link it by
    hand afterwards. Returning None here is a supported outcome, not an error
    path — see §49.
    """
    company = order.company
    if company is None:
        return None

    user = order.user
    document_type = order.document_type or ''
    document_number = normalize_document_number(order.document_number)

    try:
        # THE ACCOUNT IS THE IDENTITY; A DOCUMENT TYPED AT CHECKOUT IS NOT.
        #
        # ERP-1 · P0-1. This function used to resolve by account OR document for
        # everyone and, worse, ADOPT a record's empty account link when the
        # document matched. That let any signed-in buyer claim a stranger's CRM
        # file — and with it their repair history, saldos and the right to
        # approve or reject their quotes — just by typing a DNI that checkout
        # only validates for SHAPE. `Order.customer.user` is the authorisation
        # key (`tenancy.customer_owned_orders`, `service_services`.
        # `customer_owned_repair_orders`), so writing it from an unverified
        # document was a horizontal account takeover.
        #
        # The policy above is now the code: an authenticated sale is matched by
        # its account ALONE, and the document is consulted only when there is no
        # account behind the sale. The account link is never adopted from a
        # document match; unifying a walk-in record with an online account is a
        # deliberate staff action, not a side effect of typing the right number.
        if user is not None:
            existing = match_by_user(company, user)
        else:
            existing, somebody_elses = _guest_record(company, document_type, document_number)
            if somebody_elses:
                # The document is on a record with an ACCOUNT behind it. The
                # sale goes on unlinked, with its snapshot: no second record
                # under one document, and nothing for the buyer to notice.
                return None

        if existing is not None:
            # Never overwritten with the checkout data: the client may have
            # corrected their details in the CRM, and a new sale is not a reason
            # to revert them.
            _stamp(order, existing)
            return existing

        customer = _create_from_order(order, company, actor=actor)
        _stamp(order, customer)
        return customer

    except IntegrityError:
        # Either a concurrent checkout won the unique constraint, or — for an
        # authenticated sale — the typed document already belongs to someone
        # else's record. Re-read by the SAME key we matched on. Never fall back
        # to the document for an authenticated sale: that would grab the very
        # record we just refused to touch. If nothing is ours, leave the order
        # unlinked with its snapshot intact rather than attach the wrong person.
        try:
            with transaction.atomic():
                if user is not None:
                    existing = match_by_user(company, user)
                else:
                    # The same question as above, asked again: a record that
                    # gained an account in between is not adopted either.
                    existing, _somebody_elses = _guest_record(
                        company, document_type, document_number,
                    )
            if existing is not None:
                _stamp(order, existing)
                return existing
        except Exception:
            pass
        return None
    except Exception:
        # Deliberately broad. Whatever went wrong in the CRM, the sale continues
        # and the order keeps its snapshot. See §49.
        return None


def _guest_record(company, document_type, document_number):
    """
    `(record, somebody_elses)` for a sale with NO account behind it.

    A GUEST'S DOCUMENT OPENS ONLY A RECORD THAT NOBODY'S LOGIN OPENS, AND
    THAT THE SHOP STANDS BEHIND (CLIENT-SEC-01, amending DEC-CUSTOMER-01). Checkout checks a document
    for its shape, never for whose it is. `Order.customer.user` is ownership
    (`tenancy.customer_owned_orders`) and where the buyer's notices are sent,
    so matching a guest's typed document to a record WITH an account handed
    that account the guest's order, its notices and its history — and anybody
    could put a stranger's document on their own record first.

    So: a record with no account that is ESTABLISHED (`is_established`: a
    walk-in the shop filed, a guest who paid, somebody with a repair) is
    matched exactly as before. A record with an account is not: the answer is
    «somebody else's», and the caller leaves the order unlinked. Joining an
    online account to a counter record stays a deliberate act (the repair
    claim, with the document AND the tracking link; or staff).

    And a record that only unpaid checkouts made is not matched either. Such a
    checkout is free and anonymous: whoever left one with a stranger's document
    and their own contact would otherwise receive that stranger's next order
    in their record. The order waits unlinked, and is filed when it is paid
    (`file_paid_guest_order`).
    """
    holder = match_by_document(company, document_type, document_number)
    if holder is None:
        return None, False
    if holder.user_id is not None or not is_established(holder):
        return None, True
    return holder, False


def file_paid_guest_order(order):
    """
    A guest's order has just been PAID and has no record: give it one. The
    record, or None.

    Called where a payment is confirmed. NEVER RAISES and never breaks that
    transaction: a savepoint, and a failure logged by its kind (never by its
    words, which may quote what the buyer typed). A failure leaves `order` as
    it was, in memory too: what the payment does next — the notice — must not
    write down a record that was rolled back.

    Paying is what an unpaid checkout lacks: it costs money and leaves a trail.
    So a paid order is filed under its document —

      * with the established record that holds it, as at checkout;
      * with a provisional record that holds it when that record carries this
        order's own NAME, PHONE AND ADDRESS: the same person, trying again
        after a declined card. All three: an address alone anybody can type;
      * otherwise the provisional record gives the document up (audited) and
        the order gets a record of its own, made from what was typed on it.

    A record with an account is never a guest's, paid or not — and is not
    touched. The order still gets a record of its own, WITHOUT the document:
    it is somebody's purchase, and a record is where its notices come from.

    A race lost to somebody filing the same document at that moment (staff, or
    another payment) is the database refusing the second record: tried once
    more, and then the order finds what the other one made.
    """
    before = order.customer_id
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                return _file_paid_guest_order(order)
        except Exception as failed:  # noqa: BLE001 — see docstring
            order.customer_id = before
            if attempt == 1 and isinstance(failed, IntegrityError):
                continue
            logger.error(
                'paid guest order %s could not be filed under a client record (%s)',
                getattr(order, 'pk', None), type(failed).__name__,
            )
            return None


def _file_paid_guest_order(order):
    company = order.company
    if (
        company is None or order.customer_id is not None or order.user_id is not None
        or order.status != Order.Status.PAID
    ):
        return None
    document_type = order.document_type or ''
    number = normalize_document_number(order.document_number)
    if not document_type or not number:
        return None
    holder = Customer.objects.select_for_update().filter(
        company=company, document_type=document_type, document_number=number,
    ).first()
    if holder is None:
        customer = _create_from_order(order, company)
    elif holder.user_id is not None:
        customer = _create_from_order(order, company, with_document=False)
    elif is_established(holder) or _same_person(holder, order):
        customer = holder
    else:
        customer = _create_from_order(
            order, company,
            with_document=_release_document(holder, company=company, actor=None, taken_by='paid_order'),
        )
    _stamp(order, customer)
    return customer


def _same_person(record, order) -> bool:
    """
    Whether a provisional record was made by THIS buyer's own earlier checkout:
    the same name, phone and e-mail address as the order, each of them there.
    Compared as a person would read them — case and spacing say nothing.
    """
    def said(text):
        return ' '.join((text or '').split()).casefold()

    name = said(order.customer_name)
    phone = normalize_customer_phone(order.customer_phone)
    email = normalize_customer_email(order.customer_email)
    theirs = said(record.business_name or f'{record.first_name} {record.last_name}')
    return bool(name and phone and email) and (name, phone, email) == (theirs, record.phone, record.email)


def _stamp(order, customer):
    order.customer = customer
    order.save(update_fields=['customer'])


def _create_from_order(order, company, *, actor=None, with_document=True):
    """
    A new CRM record built from what checkout validated.

    `customer_type` is inferred from the document: a RUC is issued to a business,
    a DNI or CE to a person. That is a fact about the document, not a guess about
    the buyer. `with_document=False` makes the record without it — the order's
    document is somebody else's record's to hold.
    """
    document_type = order.document_type or ''
    is_business = document_type == 'ruc'

    name = (order.customer_name or '').strip()
    first_name, _, last_name = name.partition(' ')
    document_number = normalize_document_number(order.document_number)
    if order.user_id is not None or not with_document:
        # AN ACCOUNT'S RECORD NEVER TAKES ITS DOCUMENT FROM A CHECKOUT
        # (CLIENT-SEC-01). A checkout checks a document for its shape, costs
        # nothing and can be repeated; the document of an account's record is
        # written in «Mi cuenta», where the proof of the mailbox, the conflict
        # check and the day's budget are. The ORDER keeps what was typed (its
        # own snapshot), and a business is still filed as one, by its name.
        # A guest's record takes it as always: no login opens that record.
        document_type, document_number = '', ''

    customer = Customer(
        company=company,
        user=order.user,
        customer_type=Customer.TYPE_BUSINESS if is_business else Customer.TYPE_PERSON,
        business_name=name if is_business else '',
        first_name='' if is_business else first_name,
        last_name='' if is_business else last_name.strip(),
        document_type=document_type,
        document_number=document_number,
        phone=normalize_customer_phone(order.customer_phone),
        email=normalize_customer_email(order.customer_email),
        address_line=order.address_line or '',
        district=order.district or '',
        city=order.city or '',
        created_by=actor,
    )
    with transaction.atomic():
        customer.save()
    return customer
