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
from .models import AdminAuditLog, Customer, normalize_customer_email

#: The rule of the database that one account is ONE client of a shop.
_OWN_RECORD_RULE = 'unique_customer_per_user_per_company'

#: What a buyer may write. An allowlist: a key that is not here is not read.
WRITABLE = ('first_name', 'last_name', 'phone', 'document_type', 'document_number')
_NAME_MAX = Customer._meta.get_field('first_name').max_length


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
        # Whether «send the verification again» would do anything for this
        # account. That action serves an account registered and never confirmed
        # — one that cannot sign in yet — so for whoever reads this it is «no».
        'email_verification_available': accounts.is_unverified(user),
        'first_name': record.first_name if record else (user.first_name or '')[:_NAME_MAX],
        'last_name': record.last_name if record else (user.last_name or '')[:_NAME_MAX],
        'phone': record.phone if record else '',
        'document_type': record.document_type if record else '',
        'document_number': record.document_number if record else '',
    }


def _name(data, field, current) -> str:
    if field not in data:
        return current
    value = data[field]
    if not isinstance(value, str):
        raise ProfileError({field: ['Escribe un texto.']})
    value = value.strip()
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
        wanted = _wanted(record, data)

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
