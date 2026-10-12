"""
A buyer's OWN record in one shop — CLIENT-SEC-01.

`Customer` is the file a business keeps on a client. Until now only the
business wrote it (the panel, or a checkout filling it in as a side effect).
This is the part of it its subject may read and correct: a name, a phone and
an identity document. Nothing else — not the notes the shop writes, not whether
the shop archived the file, not which account it belongs to.

WHOSE RECORD, IN WHICH SHOP, is never the request's to say. The account is the
signed-in one and the company is the one the surface resolved (the storefront
being looked at, or the path of the app's customer surface). Two shops keep two
records on one person and neither reads the other's.

THE RECORD IS MADE BY THE FIRST SAVE, never by a read: registering does not
make somebody a client of a shop (DEC-CUSTOMER-01), and looking at an empty
form does not either.

THE E-MAIL IS THE ACCOUNT'S and is only shown. Changing it is changing how the
account is recovered, which is not something a profile form decides.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction

from . import accounts
from . import identity_validation as identity
from django.db.models import Q

from .models import AdminAuditLog, Customer, Order, normalize_customer_email

#: The rule of the database that one account is ONE client of a shop.
_OWN_RECORD_RULE = 'unique_customer_per_user_per_company'

#: What a buyer may write. An allowlist: a key that is not here is not read.
WRITABLE = ('first_name', 'last_name', 'phone', 'document_type', 'document_number')
_NAME_MAX = Customer._meta.get_field('first_name').max_length


#: What a buyer is told when the document of their file is not theirs to change.
DOCUMENT_LOCKED = 'El documento de tu ficha lo cambia la tienda. Comunícate con ella si necesitas corregirlo.'
DOCUMENT_STAYS = 'El documento no se puede quitar desde aquí. Si cambió, escribe el nuevo.'


#: What an account is told before it has proved its mailbox.
DOCUMENT_NEEDS_PROOF = 'Verifica tu correo antes de guardar tu documento. Puedes hacerlo aquí mismo, en «Mi cuenta».'
#: How many times a day one account may try to change its document — in every
#: shop and through either door, and whether or not the change was accepted.
DOCUMENT_CHANGES_PER_DAY = 3
DOCUMENT_BUDGET_SPENT = 'Cambiaste tu documento varias veces hoy. Inténtalo mañana o comunícate con la tienda.'
_DOCUMENT_BUDGET_SECONDS = 24 * 60 * 60


def _spend_document_change(user) -> bool:
    """
    Count one attempt to change a document. False when today's are spent.

    WHY A BUDGET OF ITS OWN. Saving a document answers «can this document be
    kept?», and «no» is all it takes to learn that a number is on file in a
    shop — and «yes» takes the number. The profile's own limit is sized for
    correcting a phone; this one is sized for how often a person's document
    really changes. Counted BEFORE the answer is known, so asking costs the
    same whatever is learned. In this process's memory, like every limit here.
    """
    from django.core.cache import cache

    key = f'profile-document-changes:{user.pk}'
    cache.add(key, 0, _DOCUMENT_BUDGET_SECONDS)
    try:
        return cache.incr(key) <= DOCUMENT_CHANGES_PER_DAY
    except ValueError:
        # The key expired between the two lines: start the day again.
        cache.set(key, 1, _DOCUMENT_BUDGET_SECONDS)
        return True


def document_locked(record) -> bool:
    """
    Whether the document of this record is frozen for its subject.

    A document is what the shop knows a client by: its review of orders
    compares against it (`order_risk`), and so does the claim of a repair. Its
    subject may correct a typo while nobody has relied on it. Once the shop HAS
    — it made the file itself, or it has a paid order on it — a change is the
    shop's to make, looking at the person.
    """
    if record is None or record.pk is None:
        return False
    if record.created_by_id is not None and record.created_by_id != record.user_id:
        return True                     # filed by staff, not by its own subject
    theirs = Q(customer=record)
    if record.user_id is not None:
        theirs |= Q(user_id=record.user_id)
    return Order.objects.filter(theirs, company_id=record.company_id, status=Order.Status.PAID).exists()


class ProfileError(Exception):
    """A save that was refused. `errors` is the answer, field by field."""

    def __init__(self, errors: dict):
        super().__init__('profile refused')
        self.errors = errors


def own_record(company, user):
    """This account's record in this shop, or None. Archived or not: it is theirs."""
    return Customer.objects.filter(company=company, user=user).first()


def payload(company, user) -> dict:
    """
    What the buyer sees of themselves here. A DECLARED shape: a column added to
    `Customer` does not reach this answer by being added.

    Without a record yet, the account's own name is offered to start from.
    """
    record = own_record(company, user)
    return {
        'email': user.email,
        'email_verified_at': accounts.email_verified_at(user),
        # Whether this account has an address nobody has proved yet, and can
        # prove it now with a code (`account/verify-email/…`).
        'email_verification_available': accounts.can_verify_own_email(user),
        'first_name': record.first_name if record else (user.first_name or '')[:_NAME_MAX],
        'last_name': record.last_name if record else (user.last_name or '')[:_NAME_MAX],
        'phone': record.phone if record else '',
        'document_type': record.document_type if record else '',
        'document_number': record.document_number if record else '',
        # Whether the document is the shop's to change. The page says so and
        # does not offer the field; the save refuses it all the same.
        'document_locked': document_locked(record),
    }


def _name(data, field, current) -> str:
    if field not in data:
        return current
    value = data[field]
    if not isinstance(value, str):
        raise ProfileError({field: ['Escribe un texto.']})
    value = value.strip()
    if identity.has_control_characters(value):
        raise ProfileError({field: ['Escribe sólo letras: hay un carácter que no se puede guardar.']})
    if len(value) > _NAME_MAX:
        raise ProfileError({field: [f'Máximo {_NAME_MAX} caracteres.']})
    return value


def _wanted(record, data) -> dict:
    """The five fields as they would be after this save, judged."""
    wanted = {
        'first_name': _name(data, 'first_name', record.first_name),
        'last_name': _name(data, 'last_name', record.last_name),
        'phone': record.phone,
        'document_type': record.document_type,
        'document_number': record.document_number,
    }
    try:
        if 'phone' in data:
            wanted['phone'] = identity.clean_phone(data['phone'])
        if 'document_type' in data or 'document_number' in data:
            wanted['document_type'], wanted['document_number'] = identity.clean_document(
                data.get('document_type', record.document_type),
                data.get('document_number', record.document_number),
            )
    except identity.IdentityError as refused:
        raise ProfileError({refused.field: [refused.message]}) from None
    # A person is somebody by a name. A business is somebody by its registered
    # name, which this form does not write: its contact's name may stay empty.
    if record.customer_type != Customer.TYPE_BUSINESS and not wanted['first_name'] and not wanted['last_name']:
        raise ProfileError({'first_name': ['Indica al menos un nombre o apellido.']})
    return wanted


def update(*, company, user, data, request=None) -> dict:
    """
    Save what the buyer changed, and answer with the profile as it is now.

    All or nothing: a refused field leaves the record — and whether there is a
    record — exactly as it was.
    """
    data = data if hasattr(data, 'get') else {}
    for attempt in (1, 2):
        try:
            return _save(company, user, data, request)
        except IntegrityError as refused:
            # Two saves of a first profile at once (a double tap): the other
            # one made the record a moment ago. Save onto it, once.
            if attempt == 1 and _OWN_RECORD_RULE in str(refused):
                continue
            # Otherwise somebody took that document between the look and the
            # save, and the database refused the second one. The same answer
            # as a moment later.
            raise ProfileError({'document_number': [identity.DOCUMENT_REFUSED]}) from None


def ensure_record(*, company, user, request=None):
    """
    This account's record in this shop, LOCKED — made now if there is none.

    For what hangs from the record (the address book, DELIVERY-01): the first
    entry of a book makes its owner a client of the shop, with the account's
    name and nothing else. An account with no name is asked for it first: a
    record has to be somebody.

    Call it inside a transaction; the lock is what serialises whatever the
    caller does next to this buyer's rows.
    """
    record = Customer.objects.select_for_update().filter(company=company, user=user).first()
    if record is not None:
        return record
    record = Customer(
        company=company, user=user, customer_type=Customer.TYPE_PERSON,
        first_name=(user.first_name or '').strip()[:_NAME_MAX],
        last_name=(user.last_name or '').strip()[:_NAME_MAX],
        email=normalize_customer_email(user.email), created_by=user,
    )
    if not record.first_name and not record.last_name:
        raise ProfileError({'detail': 'Completa tu nombre en «Mi cuenta» antes de guardar una dirección.'})
    record.save()
    AdminAuditLog.log(
        actor=user, action='customer_profile_updated', target_type='customer', target_id=record.pk,
        metadata={'created': True, 'changed_fields': sorted(
            field for field in ('first_name', 'last_name') if getattr(record, field)
        )},
        request=request, company=company,
    )
    return record


def _save(company, user, data, request) -> dict:
    """One attempt, in one transaction: the record locked, judged, saved and written down."""
    with transaction.atomic():
        record = Customer.objects.select_for_update().filter(company=company, user=user).first()
        created = record is None
        if created:
            record = Customer(
                company=company, user=user, customer_type=Customer.TYPE_PERSON,
                first_name=(user.first_name or '')[:_NAME_MAX],
                last_name=(user.last_name or '')[:_NAME_MAX],
                email=normalize_customer_email(user.email), created_by=user,
            )
        # A FILE THE SHOP ARCHIVED IS NOT CHANGED BY ITS SUBJECT — not a field
        # of it. Answered like a save that changed nothing, judged or not: the
        # shop's decision about a client is not that client's to learn here,
        # nor to work around by rewriting what the decision was made on.
        if not created and not record.is_active:
            return payload(company, user)

        wanted = _wanted(record, data)

        if (wanted['document_type'], wanted['document_number']) != (record.document_type, record.document_number):
            if document_locked(record):
                raise ProfileError({'document_number': [DOCUMENT_LOCKED]})
            # Replaced, never erased: a file that had a document keeps having one.
            if record.document_number and not wanted['document_number']:
                raise ProfileError({'document_number': [DOCUMENT_STAYS]})
            # Only an account with a proved mailbox writes a document (F2):
            # the answer below says something about the shop's other clients.
            # Said before looking, so it is the same for a free and a taken one.
            if accounts.email_verified_at(user) is None:
                raise ProfileError({'document_number': [DOCUMENT_NEEDS_PROOF]})
            if not _spend_document_change(user):
                raise ProfileError({'document_number': [DOCUMENT_BUDGET_SPENT]})

        # A document identifies ONE client of a shop. Whose it already is —
        # somebody with an account, a walk-in, an archived file — is not
        # this person's to learn: the same sentence for all of them.
        if wanted['document_number'] and Customer.objects.filter(
            company=company, document_type=wanted['document_type'],
            document_number=wanted['document_number'],
        ).exclude(pk=record.pk).exists():
            raise ProfileError({'document_number': [identity.DOCUMENT_REFUSED]})

        changed = sorted(
            field for field in WRITABLE
            if created and wanted[field] or not created and getattr(record, field) != wanted[field]
        )
        if not created and not changed:
            return payload(company, user)
        for field in WRITABLE:
            setattr(record, field, wanted[field])
        record.save()
        AdminAuditLog.log(
            actor=user,
            action='customer_profile_updated',
            target_type='customer',
            target_id=record.pk,
            # FIELD NAMES ONLY. Who changed which part of their file, and
            # when — never the phone or the document itself.
            metadata={'created': created, 'changed_fields': changed},
            request=request,
            company=company,
        )
    return payload(company, user)
