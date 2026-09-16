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

from django.db import IntegrityError, transaction

from .models import (
    Customer,
    normalize_customer_email,
    normalize_customer_phone,
    normalize_document_number,
)


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
        and therefore always has. If no record matches, CREATE one: somebody who
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
            existing = match_by_document(company, document_type, document_number)

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
                    existing = match_by_document(
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


def _stamp(order, customer):
    order.customer = customer
    order.save(update_fields=['customer'])


def _create_from_order(order, company, *, actor=None):
    """
    A new CRM record built from what checkout validated.

    `customer_type` is inferred from the document: a RUC is issued to a business,
    a DNI or CE to a person. That is a fact about the document, not a guess about
    the buyer.
    """
    document_type = order.document_type or ''
    is_business = document_type == 'ruc'

    name = (order.customer_name or '').strip()
    first_name, _, last_name = name.partition(' ')

    customer = Customer(
        company=company,
        user=order.user,
        customer_type=Customer.TYPE_BUSINESS if is_business else Customer.TYPE_PERSON,
        business_name=name if is_business else '',
        first_name='' if is_business else first_name,
        last_name='' if is_business else last_name.strip(),
        document_type=document_type,
        document_number=normalize_document_number(order.document_number),
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
