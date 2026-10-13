"""
Carriers, a buyer's address book and the delivery record of an order —
DELIVERY-01, the base.

ADDITIVE, AND THAT IS THE WHOLE DESIGN. An `Order` already says how it is
delivered — `delivery_method` and its address columns — and the checkout, the
prices, the documents, the e-mails and the app's contract all read those.
Nothing here changes them. What this module adds sits BESIDE the order:

  * `Carrier` — the carriers a company works with. Its own list.
  * `CustomerAddress` — a buyer's address book in one shop.
  * `OrderDelivery` — the order's delivery as a record of its own: made with
    the order from the order's columns, and from then on where staff write the
    carrier, the agency, the freight, the waybill and where to follow it.

FREIGHT IS INFORMATION. What the carrier charges and who pays it. It is never
added to an order's total, by this module or by anything that reads it.

WHAT A BUYER MAY BE SHOWN is a link, so every address that can become one is
judged here: https, a host, nothing a browser would run.
"""
from __future__ import annotations

import logging
from urllib.parse import quote, urlsplit

from django.db import IntegrityError, transaction

from . import customer_profile
from . import identity_validation as identity
from .models import AdminAuditLog, Carrier, Customer, CustomerAddress, Order, OrderDelivery, SalesChannel

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    """Something that cannot be saved. `errors` is the answer, field by field."""

    def __init__(self, errors: dict):
        super().__init__('delivery refused')
        self.errors = errors


def _refuse(field: str, message: str):
    raise DeliveryError({field: [message]})


# ---------------------------------------------------------------------------
# Addresses that become links
# ---------------------------------------------------------------------------

#: Where the waybill number goes in a carrier's tracking address.
WAYBILL_PLACEHOLDER = '{guia}'


def _https_address(field: str, value, *, max_length: int, placeholder: str = '') -> str:
    """
    `value` as an https address with a host, or '' for none. Anything else is
    refused: another scheme (`javascript:`, `data:`, plain http), no host, a
    user name before the host, a space or a line break.
    """
    if value in (None, ''):
        if value is None:
            _refuse(field, 'Escribe una dirección que empiece por https://.')
        return ''
    if not isinstance(value, str):
        _refuse(field, 'Escribe una dirección que empiece por https://.')
    text = value.strip()
    if not text:
        return ''
    if len(text) > max_length:
        _refuse(field, f'Máximo {max_length} caracteres.')
    if any(ch.isspace() for ch in text) or identity.has_control_characters(text):
        _refuse(field, 'La dirección no puede llevar espacios ni caracteres que no se pueden guardar.')
    # Judged with the placeholder filled by something harmless: what matters is
    # the address a browser would open.
    probe = text.replace(placeholder, 'x') if placeholder else text
    try:
        parts = urlsplit(probe)
        host = parts.hostname
    except ValueError:
        _refuse(field, 'Escribe una dirección que empiece por https://.')
    if not text.lower().startswith('https://') or parts.scheme != 'https' or not host:
        _refuse(field, 'Escribe una dirección que empiece por https://.')
    if '@' in parts.netloc:
        _refuse(field, 'La dirección no puede llevar usuario ni contraseña.')
    return text


def clean_tracking_template(value) -> str:
    """
    A carrier's tracking address: https, and `{guia}` as its only placeholder.

    The waybill is written where `{guia}` is, never in the host: whoever types a
    waybill must not be choosing which site a buyer is sent to. Without the
    placeholder it is simply the carrier's page, where the buyer types it.
    """
    text = _https_address(
        'tracking_url_template', value, max_length=Carrier._meta.get_field('tracking_url_template').max_length,
        placeholder=WAYBILL_PLACEHOLDER,
    )
    if not text:
        return ''
    rest = text.replace(WAYBILL_PLACEHOLDER, '')
    if '{' in rest or '}' in rest:
        _refuse('tracking_url_template', 'El único marcador admitido es {guia}.')
    if WAYBILL_PLACEHOLDER in text.split('/', 3)[2]:
        _refuse('tracking_url_template', 'El marcador {guia} no puede ir en el dominio.')
    return text


def clean_tracking_url(value) -> str:
    """A tracking address somebody typed for one order: https or nothing."""
    from .models import OrderDelivery

    return _https_address('tracking_url', value, max_length=OrderDelivery._meta.get_field('tracking_url').max_length)


def tracking_url_for(carrier, waybill) -> str:
    """
    Where this waybill is followed at this carrier, or '' when it cannot be
    said. The waybill is written URL-encoded, every character of it: it is
    typed by a person, and it must not be able to change the address around it.
    """
    template = getattr(carrier, 'tracking_url_template', '') or ''
    number = (waybill or '').strip() if isinstance(waybill, str) else ''
    if not template or not number:
        return ''
    return template.replace(WAYBILL_PLACEHOLDER, quote(number, safe=''))


# ---------------------------------------------------------------------------
# Carriers
# ---------------------------------------------------------------------------

_CARRIER_FIELDS = ('name', 'kind', 'tracking_url_template', 'is_active')


def carrier_payload(carrier) -> dict:
    return {
        'id': carrier.pk,
        'name': carrier.name,
        'kind': carrier.kind,
        'kind_label': carrier.get_kind_display(),
        'tracking_url_template': carrier.tracking_url_template,
        'is_active': carrier.is_active,
    }


def _carrier_values(company, data, *, current=None) -> dict:
    """The carrier fields this request sets, judged. Only what was sent."""
    values = {}
    if 'name' in data or current is None:
        name = data.get('name')
        name = name.strip() if isinstance(name, str) else ''
        if not name or identity.has_control_characters(name):
            _refuse('name', 'Indica el nombre del transportista.')
        if len(name) > Carrier._meta.get_field('name').max_length:
            _refuse('name', 'Máximo 80 caracteres.')
        taken = Carrier.objects.filter(company=company, name__iexact=name)
        if current is not None:
            taken = taken.exclude(pk=current.pk)
        if taken.exists():
            _refuse('name', 'Ya tienes un transportista con ese nombre.')
        values['name'] = name
    if 'kind' in data or current is None:
        kind = data.get('kind')
        if kind not in Carrier.Kind.values:
            _refuse('kind', 'Indica si entrega en agencia o a domicilio.')
        values['kind'] = kind
    if 'tracking_url_template' in data:
        values['tracking_url_template'] = clean_tracking_template(data.get('tracking_url_template'))
    if 'is_active' in data:
        if not isinstance(data.get('is_active'), bool):
            _refuse('is_active', 'Indica verdadero o falso.')
        values['is_active'] = data['is_active']
    return values


def create_carrier(*, company, actor, data, request=None):
    data = data if hasattr(data, 'get') else {}
    try:
        with transaction.atomic():
            carrier = Carrier.objects.create(company=company, **_carrier_values(company, data))
            AdminAuditLog.log(
                actor=actor, action='carrier_created', target_type='carrier', target_id=carrier.pk,
                metadata={'fields': sorted(f for f in _CARRIER_FIELDS if f in data)},
                request=request, company=company,
            )
    except IntegrityError:
        # Two people adding the same name at once: the database kept one.
        _refuse('name', 'Ya tienes un transportista con ese nombre.')
    return carrier


def update_carrier(*, carrier, company, actor, data, request=None):
    """Change a carrier. Orders that already left keep the name they left with."""
    data = data if hasattr(data, 'get') else {}
    try:
        with transaction.atomic():
            carrier = Carrier.objects.select_for_update().get(pk=carrier.pk, company=company)
            values = _carrier_values(company, data, current=carrier)
            changed = sorted(field for field, value in values.items() if getattr(carrier, field) != value)
            if not changed:
                return carrier
            for field in changed:
                setattr(carrier, field, values[field])
            carrier.save(update_fields=[*changed, 'updated_at'])
            AdminAuditLog.log(
                actor=actor, action='carrier_updated', target_type='carrier', target_id=carrier.pk,
                metadata={'changed_fields': changed}, request=request, company=company,
            )
    except IntegrityError:
        _refuse('name', 'Ya tienes un transportista con ese nombre.')
    return carrier


# ---------------------------------------------------------------------------
# A buyer's address book
# ---------------------------------------------------------------------------

#: How many ACTIVE entries one buyer may keep in one shop.
MAX_ACTIVE_ADDRESSES = 10
#: Every row one buyer holds in one shop, kept or removed. A removed entry is
#: switched off, not deleted, so without a ceiling adding and removing would
#: grow the table for ever. At the ceiling a new entry is written over the
#: oldest removed row instead of adding one: nobody is ever refused for it.
MAX_ADDRESS_ROWS = 200
#: What a file the shop archived answers to a write. It says nothing of why:
#: the shop's decision about a client is not that client's to learn here.
_BOOK_NOT_SAVED = 'No pudimos guardar esta dirección. Escríbenos y te ayudamos.'

#: What a buyer may write of an entry. An allowlist: whose it is, which shop it
#: belongs to, whether it is active and whether it is the default are not here.
ADDRESS_FIELDS = (
    'label', 'kind', 'recipient_name', 'recipient_document_type', 'recipient_document_number',
    'recipient_phone', 'address_line', 'district', 'city', 'department', 'reference',
    'carrier', 'agency_name', 'agency_locality',
)
_DOOR_FIELDS = ('address_line', 'district', 'city', 'department', 'reference')
_AGENCY_FIELDS = ('agency_name', 'agency_locality')
_NO_SUCH_CARRIER = 'Elige una agencia de la lista.'


def address_payload(entry) -> dict:
    """An entry as its owner reads it. A DECLARED shape."""
    return {
        'id': entry.pk,
        'label': entry.label,
        'kind': entry.kind,
        'recipient_name': entry.recipient_name,
        'recipient_document_type': entry.recipient_document_type,
        'recipient_document_number': entry.recipient_document_number,
        'recipient_phone': entry.recipient_phone,
        'address_line': entry.address_line,
        'district': entry.district,
        'city': entry.city,
        'department': entry.department,
        'reference': entry.reference,
        'carrier': entry.carrier_id,
        'carrier_name': entry.carrier.name if entry.carrier_id else '',
        'agency_name': entry.agency_name,
        'agency_locality': entry.agency_locality,
        'is_default': entry.is_default,
    }


def owned_addresses(company, user):
    """
    The ACTIVE entries of this account's book in this shop — the one boundary of
    every address route. An entry of another buyer, of another shop, or one that
    was removed is not in it, so it is not found rather than found and refused.
    """
    return (
        CustomerAddress.objects
        .filter(company=company, customer__company=company, customer__user=user, is_active=True)
        .select_related('carrier')
        .order_by('-is_default', 'pk')
    )


def book_payload(company, user) -> dict:
    return {
        'results': [address_payload(entry) for entry in owned_addresses(company, user)],
        'limit': MAX_ACTIVE_ADDRESSES,
        # The agencies a buyer can choose from: the shop's own, the active ones.
        'agency_carriers': [
            {'id': carrier.pk, 'name': carrier.name}
            for carrier in Carrier.objects.filter(company=company, kind=Carrier.Kind.AGENCY, is_active=True)
        ],
    }


def _text(data, field, current, *, max_length, required=False) -> str:
    if field not in data:
        value = current
    else:
        value = data[field]
        if value is None:
            value = ''
        if not isinstance(value, str):
            _refuse(field, 'Escribe un texto.')
        value = value.strip()
        if identity.has_control_characters(value):
            _refuse(field, 'Hay un carácter que no se puede guardar.')
    if len(value) > max_length:
        _refuse(field, f'Máximo {max_length} caracteres.')
    if required and not value:
        _refuse(field, 'Este dato es necesario.')
    return value


def _recipient(data, current) -> dict:
    """Who receives: a name, and a phone and a document judged like the buyer's own."""
    length = CustomerAddress._meta.get_field
    values = {
        'recipient_name': _text(data, 'recipient_name', current.recipient_name,
                                max_length=length('recipient_name').max_length, required=True),
        'recipient_phone': current.recipient_phone,
        'recipient_document_type': current.recipient_document_type,
        'recipient_document_number': current.recipient_document_number,
    }
    try:
        if 'recipient_phone' in data:
            values['recipient_phone'] = identity.clean_phone(data['recipient_phone'] if data['recipient_phone'] is not None else '')
        if 'recipient_document_type' in data or 'recipient_document_number' in data:
            kind, number = identity.clean_document(
                data.get('recipient_document_type', current.recipient_document_type) or '',
                data.get('recipient_document_number', current.recipient_document_number) or '',
            )
            values['recipient_document_type'], values['recipient_document_number'] = kind, number
    except identity.IdentityError as refused:
        # The validator names the buyer's own fields; here they are the recipient's.
        raise DeliveryError({f'recipient_{refused.field}': [refused.message]}) from None
    return values


def _agency_carrier(company, raw):
    """The carrier an agency entry names: one of THIS shop's active agencies, or a refusal."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        _refuse('carrier', _NO_SUCH_CARRIER)
    carrier = Carrier.objects.filter(company=company, pk=raw, kind=Carrier.Kind.AGENCY, is_active=True).first()
    if carrier is None:
        # Another shop's, a courier, a switched-off one, one that never was: one answer.
        _refuse('carrier', _NO_SUCH_CARRIER)
    return carrier


def _address_values(company, data, current) -> dict:
    """Every field of an entry as it would be after this save, judged by its kind."""
    length = lambda field: CustomerAddress._meta.get_field(field).max_length  # noqa: E731
    kind = data.get('kind', current.kind)
    if kind not in CustomerAddress.Kind.values:
        _refuse('kind', 'Indica si es una dirección o una agencia.')
    values = {'kind': kind, 'label': _text(data, 'label', current.label, max_length=length('label'))}
    values.update(_recipient(data, current))

    if kind == CustomerAddress.Kind.ADDRESS:
        for field in ('address_line', 'district', 'city'):
            values[field] = _text(data, field, getattr(current, field), max_length=length(field), required=True)
        for field in ('department', 'reference'):
            values[field] = _text(data, field, getattr(current, field), max_length=length(field))
        # A door is not an agency: what an agency entry had is dropped.
        values.update(carrier=None, agency_name='', agency_locality='')
    else:
        for field in _AGENCY_FIELDS:
            values[field] = _text(data, field, getattr(current, field), max_length=length(field), required=True)
        # The carrier is judged when it is the carrier that changes. An entry
        # whose agency the shop no longer offers keeps naming it — its label or
        # its recipient can still be corrected — but no entry can be GIVEN one.
        raw = data['carrier'] if 'carrier' in data else current.carrier_id
        kept = isinstance(raw, int) and not isinstance(raw, bool) and raw == current.carrier_id
        values['carrier'] = current.carrier if kept else _agency_carrier(company, raw)
        values.update({field: '' for field in _DOOR_FIELDS})
        values['department'] = _text(data, 'department', current.department, max_length=length('department'))
    return values


def _log_address(action, entry, company, user, request, metadata=None):
    AdminAuditLog.log(
        actor=user, action=action, target_type='customer_address', target_id=entry.pk,
        metadata=metadata or {}, request=request, company=company,
    )


def _in_book(work):
    """
    Run `work()` in one transaction, once more if a double tap made the buyer's
    record a moment ago (the same race the profile handles).
    """
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                return work()
        except IntegrityError as refused:
            if attempt == 1 and 'unique_customer_per_user_per_company' in str(refused):
                continue
            raise


def create_address(*, company, user, data, request=None):
    """Add an entry to this account's book in this shop. The first one is the default."""
    data = data if hasattr(data, 'get') else {}

    def work():
        try:
            customer = customer_profile.ensure_record(company=company, user=user, request=request)
        except customer_profile.ProfileError as refused:
            raise DeliveryError(refused.errors) from None
        if not customer.is_active:
            raise DeliveryError({'detail': _BOOK_NOT_SAVED})
        active = CustomerAddress.objects.filter(customer=customer, is_active=True)
        if active.count() >= MAX_ACTIVE_ADDRESSES:
            raise DeliveryError({
                'detail': f'Puedes guardar hasta {MAX_ACTIVE_ADDRESSES} direcciones. Quita una para agregar otra.',
            })
        values = _address_values(company, data, CustomerAddress(company=company, customer=customer))
        entry = CustomerAddress(company=company, customer=customer)
        if CustomerAddress.objects.filter(customer=customer).count() >= MAX_ADDRESS_ROWS:
            # At the ceiling no row is added: the new entry takes the place of
            # the OLDEST one the buyer removed (with ten active at most, there
            # always is one). Every field is written over; nothing of what the
            # row used to say is left on it.
            entry = (
                CustomerAddress.objects.select_for_update()
                .filter(customer=customer, is_active=False).order_by('pk').first()
            )
            entry.is_active = True
        for field, value in values.items():
            setattr(entry, field, value)
        entry.is_default = not active.filter(is_default=True).exists()
        entry.save()
        _log_address('customer_address_created', entry, company, user, request,
                     {'fields': sorted(f for f in ADDRESS_FIELDS if f in data)})
        return entry

    return _in_book(work)


def _locked_entry(company, user, pk):
    """The buyer's record locked, then their active entry `pk` — or None."""
    customer = Customer.objects.select_for_update().filter(company=company, user=user).first()
    if customer is None:
        return None, None
    entry = (
        CustomerAddress.objects.select_for_update(of=('self',))
        .filter(company=company, customer=customer, pk=pk, is_active=True).first()
    )
    return customer, entry


def update_address(*, company, user, pk, data, request=None):
    """Correct an entry. None when it is not this buyer's active entry here."""
    data = data if hasattr(data, 'get') else {}
    with transaction.atomic():
        customer, entry = _locked_entry(company, user, pk)
        if entry is None:
            return None
        if not customer.is_active:
            # A new address written over an old one is a new address.
            raise DeliveryError({'detail': _BOOK_NOT_SAVED})
        values = _address_values(company, data, entry)
        changed = sorted(
            field for field, value in values.items()
            if (entry.carrier_id if field == 'carrier' else getattr(entry, field)) != (
                getattr(value, 'pk', None) if field == 'carrier' else value)
        )
        if changed:
            for field in changed:
                setattr(entry, field, values[field])
            entry.save()
            _log_address('customer_address_updated', entry, company, user, request, {'changed_fields': changed})
    return entry


def set_default_address(*, company, user, pk, request=None):
    """
    Make this entry the default one. The old default is cleared and the new one
    set in ONE transaction, with the buyer's record locked: two requests at
    once end with one default, whichever came last.
    """
    with transaction.atomic():
        customer, entry = _locked_entry(company, user, pk)
        if entry is None:
            return None
        if not entry.is_default:
            CustomerAddress.objects.filter(customer=customer, is_default=True).exclude(pk=entry.pk).update(is_default=False)
            entry.is_default = True
            entry.save(update_fields=['is_default', 'updated_at'])
            _log_address('customer_address_default_set', entry, company, user, request)
    return entry


def deactivate_address(*, company, user, pk, request=None) -> bool:
    """
    Take an entry out of the book. It is switched off, not deleted: an order
    may say it came from it. If it was the default, the oldest entry left is.
    """
    with transaction.atomic():
        customer, entry = _locked_entry(company, user, pk)
        if entry is None:
            return False
        was_default = entry.is_default
        entry.is_active = False
        entry.is_default = False
        entry.save(update_fields=['is_active', 'is_default', 'updated_at'])
        if was_default:
            heir = CustomerAddress.objects.filter(customer=customer, is_active=True).order_by('pk').first()
            if heir is not None:
                heir.is_default = True
                heir.save(update_fields=['is_default', 'updated_at'])
        _log_address('customer_address_deactivated', entry, company, user, request)
    return True


# ---------------------------------------------------------------------------
# The delivery record of an order
# ---------------------------------------------------------------------------

#: The record's mode for each delivery method an order can have. The order's
#: own `delivery_method` stays what the checkout wrote; this is how the record
#: names the same fact.
MODE_BY_METHOD = {
    Order.DeliveryMethod.PICKUP_STORE: OrderDelivery.Mode.PICKUP_STORE,
    Order.DeliveryMethod.DELIVERY_AREQUIPA: OrderDelivery.Mode.LOCAL_DELIVERY,
    Order.DeliveryMethod.NATIONAL_SHIPPING: OrderDelivery.Mode.NATIONAL_ADDRESS,
}


def record_for(order):
    """The delivery record of this order, or None: it has none."""
    return OrderDelivery.objects.select_related('carrier', 'updated_by').filter(order=order).first()


def is_online(order) -> bool:
    """Whether this order can have a delivery record at all: a sale at the till has no delivery."""
    return order.sales_channel == SalesChannel.ONLINE


def _from_order(order) -> OrderDelivery:
    """
    The record an order starts with, read from the ORDER'S OWN COLUMNS: the mode
    from its delivery method, the recipient from its buyer, the address as typed.
    Unsaved. An order that says nothing about its delivery starts with no mode.
    """
    return OrderDelivery(
        company=order.company, order=order, mode=MODE_BY_METHOD.get(order.delivery_method, ''),
        recipient_name=order.customer_name or '', recipient_phone=order.customer_phone or '',
        recipient_document_type=order.document_type or '', recipient_document_number=order.document_number or '',
        address_line=order.address_line or '', district=order.district or '', city=order.city or '',
        reference=order.reference or '',
    )


def snapshot_for_order(order):
    """
    Make the delivery record of an order that was just made online. The record,
    or None.

    NEVER RAISES, and never breaks the transaction it is called in: it runs in
    a savepoint, and anything that goes wrong is logged by its kind — not with
    the words of the error, which may quote an address — and leaves the order
    without a record, which every reader already handles.
    """
    try:
        with transaction.atomic():
            return _snapshot(order)
    except Exception as failed:  # noqa: BLE001 — see docstring
        logger.error(
            'delivery record failed for order %s (%s); the order stays without one',
            getattr(order, 'pk', None), type(failed).__name__,
        )
        return None


def _snapshot(order):
    if order.company_id is None or not is_online(order) or not order.delivery_method:
        return None
    if OrderDelivery.objects.filter(order=order).exists():
        return None
    record = _from_order(order)
    record.save()
    return record


# ---------------------------------------------------------------------------
# What staff read and write of an order's delivery
# ---------------------------------------------------------------------------

Mode = OrderDelivery.Mode

#: The two modes staff may move an order between: to a door, or to an agency.
#: An order to be collected at the shop, or delivered locally, stays what it is.
_SWITCHABLE = (Mode.NATIONAL_ADDRESS, Mode.AGENCY_PICKUP)

#: What staff may write. An allowlist: the address the buyer typed, the order
#: itself and who edited are not here.
STAFF_FIELDS = (
    'mode', 'recipient_name', 'recipient_document_type', 'recipient_document_number', 'recipient_phone',
    'carrier', 'agency_name', 'agency_locality', 'freight_amount', 'freight_paid_by',
    'waybill_number', 'tracking_code', 'tracking_url', 'shipped_at',
)
_NO_SUCH_STAFF_CARRIER = 'Elige un transportista activo de la empresa.'


class NotAnOnlineOrder(Exception):
    """A sale at the till has no delivery to record."""

    detail = 'Las ventas de caja no llevan registro de entrega.'
    code = 'not_online'


def allowed_modes(mode: str) -> list[str]:
    """The modes staff may leave an order in, given the one it has."""
    if mode in _SWITCHABLE or not mode:
        return [str(m) for m in _SWITCHABLE]
    return [mode]


def staff_payload(order, record=None, *, can_edit: bool = False) -> dict:
    """
    An order's delivery as staff read it — the record, or the same shape empty
    for an order that has none. `can_edit` is whether THIS reader may write it,
    and it is never true for a sale at the till.
    """
    online = is_online(order)
    blank = record is None
    record = record if record is not None else OrderDelivery()
    from .order_risk import STAFF_WITHOUT_NAME

    editor = record.updated_by if record.updated_by_id else None
    return {
        'exists': not blank,
        'can_edit': bool(can_edit) and online,
        'mode': record.mode,
        'mode_label': record.get_mode_display() if record.mode else '',
        'allowed_modes': allowed_modes(record.mode) if online else [],
        'recipient_name': record.recipient_name,
        'recipient_document_type': record.recipient_document_type,
        'recipient_document_number': record.recipient_document_number,
        'recipient_phone': record.recipient_phone,
        'address_line': record.address_line,
        'district': record.district,
        'city': record.city,
        'department': record.department,
        'reference': record.reference,
        'carrier': record.carrier_id,
        # The name the order left with, not today's.
        'carrier_name': record.carrier_name,
        'agency_name': record.agency_name,
        'agency_locality': record.agency_locality,
        'freight_amount': None if record.freight_amount is None else f'{record.freight_amount:.2f}',
        'freight_paid_by': record.freight_paid_by,
        'waybill_number': record.waybill_number,
        'tracking_code': record.tracking_code,
        'tracking_url': record.tracking_url,
        # Whether a person typed that link (it stays) or it follows carrier and waybill.
        'tracking_url_typed': record.tracking_url_typed,
        'shipped_at': record.shipped_at,
        # A name when the person has one; never the login, which is not a name to show.
        'updated_by': (editor.get_full_name().strip() or STAFF_WITHOUT_NAME) if editor is not None else None,
        'updated_at': None if blank else record.updated_at,
    }


def _freight(raw):
    from decimal import Decimal, InvalidOperation

    if raw is None or raw == '':
        return None
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        _refuse('freight_amount', 'Escribe un monto.')
    try:
        amount = Decimal(str(raw).strip().replace(',', '.'))
    except InvalidOperation:
        _refuse('freight_amount', 'Escribe un monto.')
    if not amount.is_finite() or amount < 0:
        _refuse('freight_amount', 'El flete no puede ser negativo.')
    # The size first: quantizing «1e30» is an arithmetic error, not an answer.
    if amount >= Decimal('100000000'):
        _refuse('freight_amount', 'Ese monto es demasiado grande.')
    if amount != amount.quantize(Decimal('0.01')):
        _refuse('freight_amount', 'Escribe un monto con dos decimales como máximo.')
    return amount.quantize(Decimal('0.01'))


# A moment an order can have left the shop: this century, and not further ahead
# than a date somebody mistyped by a year. Outside it the value is refused — a
# date near the end of the calendar is stored by the database and then read by
# nobody (it has no moment in UTC that Python can hold).
_SHIPPED_NOT_BEFORE = (2000, 1, 1)
_SHIPPED_AHEAD = 365                     # days
_NO_SUCH_MOMENT = 'Indica una fecha y hora válidas.'


def _shipped_at(raw):
    from datetime import datetime, timedelta, timezone as utc_zone

    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    if raw is None or raw == '':
        return None
    try:
        moment = raw if isinstance(raw, datetime) else parse_datetime(raw) if isinstance(raw, str) else None
    except ValueError:
        moment = None
    if moment is None:
        _refuse('shipped_at', _NO_SUCH_MOMENT)
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    earliest = datetime(*_SHIPPED_NOT_BEFORE, tzinfo=utc_zone.utc)
    if moment < earliest or moment > timezone.now() + timedelta(days=_SHIPPED_AHEAD):
        _refuse('shipped_at', 'Esa fecha no puede ser la de un envío: revisa el año.')
    return moment


def _staff_values(company, data, record) -> dict:
    """Every staff field as it would be after this save, judged. Only what was sent changes."""
    length = lambda field: OrderDelivery._meta.get_field(field).max_length  # noqa: E731
    values = {field: getattr(record, field) for field in STAFF_FIELDS if field != 'carrier'}
    values['carrier'] = record.carrier
    values['carrier_name'] = record.carrier_name
    values['tracking_url_typed'] = record.tracking_url_typed

    if 'mode' in data:
        mode = data['mode']
        if mode not in Mode.values or (mode != record.mode and mode not in allowed_modes(record.mode)):
            _refuse('mode', 'Esta entrega sólo puede cambiar entre envío a domicilio y recojo en agencia.')
        values['mode'] = mode

    if 'recipient_name' in data:
        values['recipient_name'] = _text(data, 'recipient_name', '', max_length=length('recipient_name'), required=True)
    try:
        if 'recipient_phone' in data:
            values['recipient_phone'] = identity.clean_phone(data['recipient_phone'] if data['recipient_phone'] is not None else '')
        if 'recipient_document_type' in data or 'recipient_document_number' in data:
            values['recipient_document_type'], values['recipient_document_number'] = identity.clean_document(
                data.get('recipient_document_type', record.recipient_document_type) or '',
                data.get('recipient_document_number', record.recipient_document_number) or '',
            )
    except identity.IdentityError as refused:
        raise DeliveryError({f'recipient_{refused.field}': [refused.message]}) from None

    if 'carrier' in data:
        raw = data['carrier']
        if raw is None:
            values['carrier'], values['carrier_name'] = None, ''
        else:
            if isinstance(raw, bool) or not isinstance(raw, int):
                _refuse('carrier', _NO_SUCH_STAFF_CARRIER)
            carrier = Carrier.objects.filter(company=company, pk=raw, is_active=True).first()
            if carrier is None:
                # Another company's, a switched-off one, one that never was: one answer.
                _refuse('carrier', _NO_SUCH_STAFF_CARRIER)
            # The NAME is copied: the record says what the order left with.
            values['carrier'], values['carrier_name'] = carrier, carrier.name
    # Collected at an agency means a carrier that has agencies. Asked of a
    # carrier being set or of a mode being changed, not of history: an order
    # that already left keeps its carrier even if the carrier changed since.
    judged = 'carrier' in data or 'mode' in data
    if judged and values['mode'] == Mode.AGENCY_PICKUP and values['carrier'] is not None \
            and values['carrier'].kind != Carrier.Kind.AGENCY:
        _refuse('carrier', 'Para un recojo en agencia elige un transportista con agencias.')

    for field in ('agency_name', 'agency_locality', 'waybill_number', 'tracking_code'):
        values[field] = _text(data, field, getattr(record, field), max_length=length(field))
    if 'freight_amount' in data:
        values['freight_amount'] = _freight(data['freight_amount'])
    if 'freight_paid_by' in data:
        paid_by = data['freight_paid_by'] or ''
        if paid_by and paid_by not in OrderDelivery.FreightPaidBy.values:
            _refuse('freight_paid_by', 'Indica quién paga el flete.')
        values['freight_paid_by'] = paid_by
    if 'shipped_at' in data:
        values['shipped_at'] = _shipped_at(data['shipped_at'])

    # Where to follow it. Either somebody TYPED the address, and then only
    # typing it again changes it; or it is DERIVED from the carrier's own page
    # and the waybill, and then it moves when one of them does and is empty
    # when one is missing or the carrier has no page. Which of the two it is is
    # kept on the record: without it a corrected waybill erased a typed link,
    # and a changed carrier left the buyer following the old one's page.
    carriers_own = tracking_url_for(values['carrier'], values['waybill_number'])
    retyped = 'tracking_url' in data
    if retyped:
        typed = clean_tracking_url(data['tracking_url'] if data['tracking_url'] is not None else '')
        # The link the record already has, SENT BACK as it was shown, is not
        # somebody typing a link: a client that returns the whole record with
        # a new waybill would otherwise freeze the old page under the new one.
        retyped = typed != record.tracking_url
    if retyped:
        # The carrier's own page typed back is not a link of somebody's own.
        values['tracking_url_typed'] = bool(typed) and typed != carriers_own
        if values['tracking_url_typed']:
            values['tracking_url'] = typed
    moved = (
        getattr(values['carrier'], 'pk', None) != record.carrier_id
        or values['waybill_number'] != record.waybill_number
    )
    if not values['tracking_url_typed'] and (retyped or moved):
        values['tracking_url'] = carriers_own
    return values


def update_order_delivery(*, order, company, actor, data, request=None):
    """
    Write what staff know about an order's delivery, and answer with the record.

    The caller has established that `actor` may manage orders in `company` and
    found `order` among the ones they can see. An order with no record gets one
    here, from its own columns, if it was made online; a sale at the till has
    no delivery (`NotAnOnlineOrder`).

    IT WRITES THE RECORD AND NOTHING ELSE. Not the order's delivery method or
    address, not its total — freight is information — and not its dispatch
    state: «sent» is still said where it always was.
    """
    data = data if hasattr(data, 'get') else {}
    if not is_online(order):
        raise NotAnOnlineOrder()
    with transaction.atomic():
        # The order's row is the lock: two people writing one delivery at once
        # take turns, and the second one sees what the first one wrote.
        Order.objects.select_for_update(of=('self',)).filter(pk=order.pk).first()
        record = OrderDelivery.objects.select_related('carrier').filter(order=order, company=company).first()
        created = record is None
        if created:
            record = _from_order(order)
        values = _staff_values(company, data, record)
        changed = sorted(
            field for field, value in values.items()
            if (record.carrier_id if field == 'carrier' else getattr(record, field)) != (
                getattr(value, 'pk', None) if field == 'carrier' else value)
        )
        if created or changed:
            for field in changed:
                setattr(record, field, values[field])
            record.updated_by = actor
            record.save()
            AdminAuditLog.log(
                actor=actor, action='order_delivery_updated', target_type='order', target_id=order.pk,
                # FIELD NAMES ONLY: who changed which part of a delivery — never
                # the recipient, the waybill or the amount.
                metadata={'order_id': order.pk, 'created': created, 'changed_fields': changed},
                request=request, company=company,
            )
        # Read back BEFORE the commit: what was written is what the next reader
        # gets, and a row that cannot be read is never left behind.
        return record_for(order)


# ---------------------------------------------------------------------------
# What the buyer sees
# ---------------------------------------------------------------------------

def buyer_payload(record):
    """
    What the owner of an order is shown of its delivery, or None when it has no
    record: which carrier, which agency and where, the waybill, and where to
    follow it. A DECLARED shape, and a short one on purpose.

    NOT the freight (the shop's arrangement with the carrier), NOT who receives
    (that may be somebody else, with their own document), NOT who wrote it, and
    not the carrier's pickup code. And the tracking address is only ever given
    when it is https: a browser will make it a link.
    """
    if record is None:
        return None
    url = record.tracking_url or ''
    return {
        'carrier_name': record.carrier_name,
        'agency_name': record.agency_name,
        'agency_locality': record.agency_locality,
        'waybill_number': record.waybill_number,
        'tracking_url': url if url.startswith('https://') else '',
    }


def buyer_payload_for(order):
    """`buyer_payload` of an order whose record was loaded with it (`select_related('delivery_record')`)."""
    return buyer_payload(getattr(order, 'delivery_record', None))
