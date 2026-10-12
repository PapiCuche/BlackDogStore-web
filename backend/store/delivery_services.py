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

from .models import AdminAuditLog, Carrier

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
    if any(ch.isspace() or ord(ch) < 32 for ch in text):
        _refuse(field, 'La dirección no puede llevar espacios.')
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
        if not name:
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
