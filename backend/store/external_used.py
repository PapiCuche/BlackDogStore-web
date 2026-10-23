"""
INV-USED-EXT-01 (docs/adr-inv-used-ext.md) — external identified used devices.

    A CARD IS ONE DEVICE A SUPPLIER STILL HAS. IT IS NOT A STOCK UNIT.

The card hangs off the supplier's ONE offer for that used variant (the price,
the agreed cost, the window, the lead time are the offer's). The offer's
`confirmed_quantity` counts the live cards — available, reserved or received —
and only this module moves it, under the offer's row lock:

    create      confirmed + 1                         card available
    withdraw    confirmed − 1                         available → withdrawn
    payment     committed + 1 (`commit_order_lines`)   available → reserved (this line)
    reception   (nothing on the offer)                reserved → received (names its StockUnit)
    failure     committed − 1, confirmed − 1 (D3)      reserved → withdrawn
    release     committed − 1                         reserved → available

The generic used offer (no cards) still never sells: `supply_services` asks for
a live card before an offer of a used variant counts.

What the supplier REPORTED (serial, IMEI, battery, grade) is pending
verification and internal (owner D3). No response here carries a whole
identifier: hints only. At reception the server compares, blind (owner D4).
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from . import device_identity
from .models import (
    AdminAuditLog, CosmeticGrade, ExternalUsedCard, Order, Product, StockUnit, Supplier, SupplierOffer,
    SupplyReceptionIncident,
)

LIVE = (ExternalUsedCard.Status.AVAILABLE, ExternalUsedCard.Status.RESERVED)
OFFER_TERMS = ('commercial_price', 'agreed_cost', 'valid_from', 'valid_until', 'lead_time_days')


class CardError(Exception):
    """A card rule refused something. `code` says which; `detail` is for the person."""

    _STATUS = {'not_found': 404, 'not_available': 409, 'already_resolved': 409}

    def __init__(self, detail: str, *, code: str = 'invalid', field: str = ''):
        super().__init__(detail)
        self.detail, self.code, self.field = detail, code, field

    @property
    def http_status(self) -> int:
        return self._STATUS.get(self.code, 400)

    def body(self) -> dict:
        return {'detail': self.detail, 'code': self.code, **({'field': self.field} if self.field else {})}


def _audit(action, *, company, actor, target_type, target_id, request=None, **metadata):
    AdminAuditLog.log(actor=actor, action=action, target_type=target_type, target_id=target_id,
                      metadata=metadata, request=request, company=company)


def hint(value) -> str:
    from .device_catalog import mask_imei

    text = str(value or '')
    return mask_imei(text) if text.isdigit() else (f'•••• {text[-2:]}' if len(text) >= 6 else ('••••' if text else ''))


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def _identity(product, fields) -> dict:
    try:
        serial = device_identity.clean_serial(fields.get('reported_serial'))
        imei = device_identity.clean_imei(fields.get('reported_imei'))
        imei2 = device_identity.clean_imei(fields.get('reported_imei2'), label='El IMEI 2')
    except device_identity.DeviceIdentityError as exc:
        raise CardError(str(exc), field='reported_imei') from None
    if not serial and not imei:
        raise CardError('Una ficha nombra su equipo: indica el IMEI o el número de serie que informó el proveedor.',
                        field='reported_imei')
    if product.requires_imei and not imei:
        raise CardError('Esta variante lleva IMEI: indica el que informó el proveedor.', field='reported_imei')
    if imei2 and (not imei or imei2 == imei):
        raise CardError('El IMEI 2 va con un IMEI 1 distinto.', field='reported_imei2')
    return {'reported_serial': serial, 'reported_imei': imei, 'reported_imei2': imei2}


def _battery(value):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise CardError('La batería es un porcentaje de 0 a 100.', field='reported_battery')
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise CardError('La batería es un porcentaje de 0 a 100.', field='reported_battery') from None
    if not 0 <= number <= 100:
        raise CardError('La batería es un porcentaje de 0 a 100.', field='reported_battery')
    return number


def _given(fields) -> dict:
    return {name: fields[name] for name in OFFER_TERMS if fields.get(name) not in (None, '')}


def _same_terms(offer, terms: dict, *, may_view_costs: bool) -> None:
    from . import supply_services as supply

    checks = {
        'commercial_price': lambda v: supply.clean_money(v, 'commercial_price', 'el precio comercial') == offer.commercial_price,
        'valid_from': lambda v: supply.clean_date(v, 'valid_from', 'el inicio') == offer.valid_from,
        'valid_until': lambda v: supply.clean_date(v, 'valid_until', 'el fin') == offer.valid_until,
        'lead_time_days': lambda v: supply.clean_int(v, 'lead_time_days', 'el plazo') == offer.lead_time_days,
    }
    if may_view_costs:
        checks['agreed_cost'] = lambda v: supply.clean_money(v, 'agreed_cost', 'el costo') == offer.agreed_cost
    for name, same in checks.items():
        if name not in terms:
            continue
        try:
            equal = same(terms[name])
        except supply.SupplyError as exc:
            raise CardError(exc.detail, field=name) from None
        if not equal:
            code = 'price_is_per_offer' if name == 'commercial_price' else 'offer_terms_differ'
            raise CardError('Este proveedor ya tiene una oferta vigente de esta variante: el precio, el costo, la '
                            'vigencia y el plazo son los de esa oferta. Cámbialos en la oferta o deja estos campos '
                            'vacíos.', code=code, field=name)


def _running_offer(company, supplier, product, *, start, end):
    """The supplier's active offer of this used variant over those days (one at a time, as for any variant)."""
    return (SupplierOffer.objects.select_for_update()
            .filter(company=company, supplier=supplier, product=product, is_active=True,
                    valid_from__lte=end, valid_until__gte=start)
            .order_by('valid_from', 'pk').first())


def create_card(*, company, actor, may_view_costs: bool, request=None, **fields) -> ExternalUsedCard:
    from . import supplier_offer_import
    from . import supply_services as supply

    supplier = Supplier.objects.filter(company=company, pk=supply._pk(fields.get('supplier_id'))).first()
    product = Product.objects.filter(company=company, pk=supply._pk(fields.get('product_id'))).first()
    if supplier is None or product is None:
        raise CardError('Proveedor o producto no encontrado.', code='not_found')
    if product.condition_line != Product.CONDITION_USED or not product.is_serialized:
        raise CardError('Sólo una variante seminueva con número de serie lleva fichas de equipos externos.',
                        field='product_id')
    identity = _identity(product, fields)
    grade = None
    if fields.get('reported_grade') not in (None, ''):
        grade = CosmeticGrade.objects.filter(company=company, code=str(fields['reported_grade'])).first()
        if grade is None:
            raise CardError('Ese estado estético no existe en esta empresa.', field='reported_grade')
    notes = ' '.join(str(fields.get('notes') or '').split())
    if len(notes) > 300:
        raise CardError('La nota es demasiado larga.', field='notes')
    owned = Q(imei=identity['reported_imei']) | Q(imei2=identity['reported_imei']) if identity['reported_imei'] \
        else Q(product=product, serial_number=identity['reported_serial'])
    if StockUnit.objects.filter(company=company).filter(owned).exists():
        raise CardError('Ese equipo ya está registrado en el inventario de la tienda.', code='already_ours',
                        field='reported_imei')
    terms = _given(fields)
    today = supply.today()
    try:
        start = supply.clean_date(terms['valid_from'], 'valid_from', 'el inicio') if 'valid_from' in terms else today
        end = supply.clean_date(terms['valid_until'], 'valid_until', 'el fin') if 'valid_until' in terms else start
    except supply.SupplyError as exc:
        raise CardError(exc.detail, field=exc.field) from None
    try:
        with transaction.atomic():
            supplier_offer_import.lock_pairs(company.pk, [(supplier.pk, product.pk)])
            offer = _running_offer(company, supplier, product, start=start, end=end)
            if offer is None:
                try:
                    offer = supply.create_offer(
                        company=company, actor=actor, request=request, may_view_costs=may_view_costs,
                        external_card=True, supplier_id=supplier.pk, product_id=product.pk, confirmed_quantity=0,
                        **terms)
                except supply.SupplyError as exc:
                    raise CardError(exc.detail, field=exc.field) from None
                offer = SupplierOffer.objects.select_for_update().get(pk=offer.pk)
            else:
                _same_terms(offer, terms, may_view_costs=may_view_costs)
            card = ExternalUsedCard.objects.create(
                company=company, offer=offer, reported_battery=_battery(fields.get('reported_battery')),
                reported_grade=grade, notes=notes, created_by=actor, **identity)
            SupplierOffer.objects.filter(pk=offer.pk).update(
                confirmed_quantity=F('confirmed_quantity') + 1, availability_confirmed=True,
                updated_at=timezone.now())
    except IntegrityError:
        raise CardError('Ese equipo ya tiene una ficha vigente.', code='duplicate', field='reported_imei') from None
    _audit('external_used_card_created', company=company, actor=actor, target_type='external_used_card',
           target_id=card.pk, request=request, offer_id=offer.pk, product_id=product.pk,
           imei_hint=hint(card.reported_imei))
    return card


def withdraw_card(card, *, actor, reason, request=None) -> ExternalUsedCard:
    reason = ' '.join(str(reason or '').split())[:200]
    if not reason:
        raise CardError('Indica el motivo.', field='reason')
    with transaction.atomic():
        SupplierOffer.objects.select_for_update().filter(pk=card.offer_id).first()
        locked = ExternalUsedCard.objects.select_for_update().get(pk=card.pk)
        if locked.status != ExternalUsedCard.Status.AVAILABLE:
            raise CardError('Sólo se retira una ficha disponible: esta ya está vendida, recibida o retirada.',
                            code='not_available')
        ExternalUsedCard.objects.filter(pk=locked.pk).update(
            status=ExternalUsedCard.Status.WITHDRAWN, withdrawn_reason=reason, updated_at=timezone.now())
        SupplierOffer.objects.filter(pk=locked.offer_id).update(
            confirmed_quantity=F('confirmed_quantity') - 1, updated_at=timezone.now())
    _audit('external_used_card_withdrawn', company=card.company, actor=actor, target_type='external_used_card',
           target_id=card.pk, request=request, offer_id=card.offer_id, reason=reason)
    card.refresh_from_db()
    return card


def card_payload(card, *, with_cost: bool) -> dict:
    offer = card.offer
    data = {
        'id': card.pk, 'offer_id': offer.pk, 'supplier_id': offer.supplier_id, 'supplier_name': offer.supplier.name,
        'product_id': offer.product_id, 'product_name': offer.product.name, 'status': card.status,
        'imei_hint': hint(card.reported_imei), 'serial_hint': hint(card.reported_serial),
        'reported_battery': card.reported_battery,
        'reported_grade': card.reported_grade.label if card.reported_grade_id else '',
        'pending_verification': card.status != ExternalUsedCard.Status.RECEIVED,
        'notes': card.notes, 'commercial_price': str(offer.commercial_price),
        'valid_from': offer.valid_from.isoformat(), 'valid_until': offer.valid_until.isoformat(),
        'lead_time_days': offer.lead_time_days,
        'order_id': card.order_item.order_id if card.order_item_id else None,
        'withdrawn_reason': card.withdrawn_reason,
    }
    if with_cost:
        data['agreed_cost'] = str(offer.agreed_cost) if offer.agreed_cost is not None else None
    return data


# ---------------------------------------------------------------------------
# Selling (called by `supply_services`, with the offer row locked)
# ---------------------------------------------------------------------------

def is_card_offer_product(product) -> bool:
    return product.condition_line == Product.CONDITION_USED


def available_cards(offer):
    return ExternalUsedCard.objects.filter(offer=offer, status=ExternalUsedCard.Status.AVAILABLE)


def reserve_for_line(offer, line) -> None:
    """The payment committed `line` against `offer` (locked): its devices are these cards from now on."""
    cards = list(available_cards(offer).select_for_update().order_by('pk')[:line.quantity])
    if len(cards) < line.quantity:
        # The caller checked under the same lock: reaching here means the invariant broke. Refuse loudly.
        raise RuntimeError('Una oferta de seminuevos sin fichas suficientes llegó a comprometerse.')
    ExternalUsedCard.objects.filter(pk__in=[card.pk for card in cards]).update(
        status=ExternalUsedCard.Status.RESERVED, order_item=line, updated_at=timezone.now())


def release_cards(line, *, flat: bool) -> None:
    """The line stopped waiting (its offer locked): a supplier failure withdraws its cards, a release frees them."""
    cards = ExternalUsedCard.objects.select_for_update().filter(order_item=line,
                                                                 status=ExternalUsedCard.Status.RESERVED)
    if flat:
        cards.update(status=ExternalUsedCard.Status.WITHDRAWN, withdrawn_reason='No se pudo abastecer',
                     updated_at=timezone.now())
    else:
        cards.update(status=ExternalUsedCard.Status.AVAILABLE, order_item=None, updated_at=timezone.now())


# ---------------------------------------------------------------------------
# Reception (owner D4)
# ---------------------------------------------------------------------------

def _normal(row) -> dict:
    """What a person typed, as the comparison reads it. A value the cleaners refuse is left to them."""
    row = row if isinstance(row, dict) else {}
    serial = ''.join(str(row.get('serial_number') or '').split()).upper()
    imei = ''.join(ch for ch in str(row.get('imei') or '') if ch.isdigit())
    return {'serial_number': serial, 'imei': imei}


def _fits(card, row) -> bool:
    """Every identifier the supplier reported is the one that arrived."""
    if card.reported_imei and row['imei'] != card.reported_imei:
        return False
    if card.reported_serial and row['serial_number'] != card.reported_serial:
        return False
    return True


def open_incident_for(line):
    return SupplyReceptionIncident.objects.filter(order_item=line, status=SupplyReceptionIncident.Status.OPEN).first()


def check_reception(line, rows) -> list:
    """
    Called by `receive_line` BEFORE it writes anything, with the order and line
    locked. Returns the line's reserved cards in the order of `rows`, or raises:
    `incident_open` while an incident stands, `identity_mismatch` when what
    arrived is not what was sold.
    """
    from .supply_services import SupplyError

    cards = list(ExternalUsedCard.objects.select_for_update()
                 .filter(order_item=line, status=ExternalUsedCard.Status.RESERVED).order_by('pk'))
    if not cards:
        return []
    standing = open_incident_for(line)
    if standing is not None:
        error = SupplyError('Hay una incidencia abierta en esta recepción: la debe resolver un responsable antes de '
                            'recibir el equipo.', code='incident_open')
        error.incident_id = standing.pk
        raise error
    normal = [_normal(row) for row in rows]
    matched, left = [], list(cards)
    for row in normal:
        card = next((c for c in left if _fits(c, row)), None)
        if card is None:
            break
        matched.append(card)
        left.remove(card)
    if len(matched) != len(normal) or left:
        error = IdentityMismatch('El equipo recibido no es el que se vendió: no se registró nada y se abrió una '
                                 'incidencia para que la revise un responsable.')
        error.card_ids = [card.pk for card in cards]
        error.received = normal
        raise error
    return matched


class IdentityMismatch(Exception):
    """Raised by `check_reception`; becomes a `SupplyError('identity_mismatch')` once the incident is open."""

    card_ids: list
    received: list


def mark_received(cards, units) -> None:
    for card, unit in zip(cards, units):
        ExternalUsedCard.objects.filter(pk=card.pk).update(
            status=ExternalUsedCard.Status.RECEIVED, received_unit=unit, updated_at=timezone.now())


def open_incident(*, line, actor, card_ids, received, request=None) -> SupplyReceptionIncident | None:
    """
    Its OWN transaction: the refused reception wrote nothing, the incident stays. One open per line.

    None when, by the time the order is locked again, the line no longer waits
    for these devices (another reception, with the right device, landed in
    between): there is nothing left to block.
    """
    from .models import OrderItem

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=line.order_id)
        locked = OrderItem.objects.select_for_update().get(pk=line.pk)
        still_waiting = (locked.supply_status == OrderItem.SUPPLY_PENDING and ExternalUsedCard.objects.filter(
            order_item=locked, status=ExternalUsedCard.Status.RESERVED).exists())
        if not still_waiting:
            return None
        standing = open_incident_for(line)
        if standing is not None:
            return standing
        try:
            with transaction.atomic():
                incident = SupplyReceptionIncident.objects.create(
                    company_id=order.company_id, order=order, order_item_id=line.pk, offer_id=line.supplier_offer_id,
                    expected_card_ids=list(card_ids), received=list(received), opened_by=actor)
        except IntegrityError:
            return open_incident_for(line)
        _audit('supply_reception_incident_opened', company=order.company, actor=actor, target_type='order',
               target_id=order.pk, request=request, incident_id=incident.pk, line_id=line.pk,
               card_ids=list(card_ids), devices=len(received))
    return incident


def reject_incident(incident, *, actor, reason, request=None) -> SupplyReceptionIncident:
    """
    The only resolution today: what arrived is not accepted (it goes back to the
    supplier). The card stays the line's; nothing enters stock; no document is
    touched. Accepting another device is BLOCKED on the owner's procedure.
    """
    reason = ' '.join(str(reason or '').split())[:300]
    if not reason:
        raise CardError('Indica el motivo.', field='reason')
    with transaction.atomic():
        locked = SupplyReceptionIncident.objects.select_for_update().get(pk=incident.pk)
        if locked.status != SupplyReceptionIncident.Status.OPEN:
            raise CardError('Esta incidencia ya se resolvió.', code='already_resolved')
        SupplyReceptionIncident.objects.filter(pk=locked.pk).update(
            status=SupplyReceptionIncident.Status.REJECTED, resolved_by=actor, resolved_at=timezone.now(),
            resolution_reason=reason)
        _audit('supply_reception_incident_rejected', company=locked.company, actor=actor, target_type='order',
               target_id=locked.order_id, request=request, incident_id=locked.pk, line_id=locked.order_item_id,
               reason=reason)
    incident.refresh_from_db()
    return incident


def incident_payload(incident) -> dict:
    line = incident.order_item
    return {
        'id': incident.pk, 'order_id': incident.order_id, 'line_id': line.pk, 'product_name': line.product.name,
        'status': incident.status, 'opened_at': incident.opened_at.isoformat(),
        'opened_by': getattr(incident.opened_by, 'username', '') if incident.opened_by_id else '',
        'expected': [{'card_id': card.pk, 'imei_hint': hint(card.reported_imei), 'serial_hint': hint(card.reported_serial)}
                     for card in ExternalUsedCard.objects.filter(pk__in=incident.expected_card_ids).order_by('pk')],
        'received': [{'imei_hint': hint(row.get('imei')), 'serial_hint': hint(row.get('serial_number'))}
                     for row in incident.received],
        'resolved_at': incident.resolved_at.isoformat() if incident.resolved_at else None,
        'resolution_reason': incident.resolution_reason,
    }

