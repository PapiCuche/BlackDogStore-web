"""
SERIALIZED-STOCK — the only code that creates or changes a `StockUnit`.

    THE INVARIANT

        BranchStock.quantity  ==  count(units AVAILABLE in that branch)

    for every serialized product, at every commit.

It holds because nothing here changes a unit's availability without writing the
matching Kardex line through `inventory_services.create_stock_movement` in the
same transaction — and because that writer refuses to move a serialized product
for anybody who does not hand it the units. There is no second inventory: a
unit entering is a purchase entry, a unit sold is a sale exit, a unit written
off is a damage exit.

LOCK ORDER: the `BranchStock` row first (taken by the writer), units second.
Every path here goes through the writer before it touches a unit's status, so
two operations on the same shelf queue instead of deadlocking.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import device_identity
from .inventory_services import (
    InsufficientStockError, InvalidMovementError, InventoryError, create_stock_movement,
)
from .models import AdminAuditLog, BranchStock, Product, StockMovement, StockUnit

MAX_BATCH = 200
MAX_REASON = 300


class StockUnitError(InventoryError):
    """
    A rule about units was broken. Views render it as 400.

    `line` is the 1-based device of a batch; `field` names the input that is
    wrong (`serial_number`, `imei`, `imei2`, `condition`, `cost`,
    `reason`) so a form can say it next to that input instead
    of in a banner the person has to map back by reading.
    """

    def __init__(self, message: str, *, line: int | None = None, field: str | None = None,
                 code: str | None = None, status: int = 400):
        super().__init__(message)
        self.line = line
        self.field = field
        # LABEL-02: a rule that is a CONFLICT with something else of the company
        # (`product_code_conflict`) answers 409 with its machine-readable code.
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _money(value, label: str, field: str) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise StockUnitError(f'{label} no es un importe válido.', field=field)
    # NaN survives `quantize` and then cannot be compared: it is not an amount.
    if not amount.is_finite() or amount < 0 or amount >= Decimal('100000000'):
        raise StockUnitError(f'{label} no es un importe válido.', field=field)
    return amount


def _condition(value) -> str:
    condition = str(value or StockUnit.Condition.NEW)
    if condition not in StockUnit.Condition.values:
        raise StockUnitError('Condición desconocida.', field='condition')
    return condition


def clean_battery(value) -> int | None:
    """Battery health in % as measured. Empty is «no informado» — never a guess."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise StockUnitError('La batería se indica en % (0 a 100).', field='battery_health')
    try:
        number = Decimal(str(value).strip().rstrip('%').strip())
    except (InvalidOperation, ValueError):
        raise StockUnitError('La batería se indica en % (0 a 100).', field='battery_health') from None
    if not number.is_finite() or number != number.to_integral_value() or not 0 <= number <= 100:
        raise StockUnitError('La batería se indica en % (0 a 100), sin decimales.', field='battery_health')
    return int(number)


def clean_grade(company, value):
    """A grade of THIS company's scale, by id or code. Another company's answers like none."""
    from .models import CosmeticGrade

    if value in (None, ''):
        return None
    grades = CosmeticGrade.objects.filter(company=company, is_active=True)
    text = str(value).strip()
    found = grades.filter(pk=int(text)).first() if text.isdigit() else None
    if found is None:
        found = grades.filter(code__iexact=text).first() or grades.filter(label__iexact=text).first()
    if found is None:
        raise StockUnitError('Estado estético desconocido.', field='cosmetic_grade')
    return found


def clean_public_notes(value) -> str:
    text = ' '.join(str(value or '').split())
    if len(text) > 500:
        raise StockUnitError('Los detalles para el comprador admiten hasta 500 caracteres.', field='public_notes')
    return text


def _reason(value, *, required: bool = True) -> str:
    reason = ' '.join(str(value or '').split())
    if required and not reason:
        raise StockUnitError('Indica el motivo.', field='reason')
    if len(reason) > MAX_REASON:
        raise StockUnitError(f'El motivo admite hasta {MAX_REASON} caracteres.', field='reason')
    return reason


def _identifier(cleaner, value, field: str) -> str:
    try:
        return cleaner(value)
    except device_identity.DeviceIdentityError as exc:
        raise StockUnitError(str(exc), field=field) from None


def _clean_row(product, row: dict) -> dict:
    """One unit's identifiers and details, cleaned. Absent identifiers are None."""
    row = row if isinstance(row, dict) else {}
    serial = _identifier(device_identity.clean_serial, row.get('serial_number'), 'serial_number')
    imei = _identifier(device_identity.clean_imei, row.get('imei'), 'imei')
    imei2 = _identifier(
        lambda value: device_identity.clean_imei(value, label='El segundo IMEI'),
        row.get('imei2'), 'imei2',
    )

    if not serial:
        raise StockUnitError('Cada equipo lleva su número de serie.', field='serial_number')
    if product.requires_imei and not imei:
        raise StockUnitError(
            f'"{product.name}" lleva IMEI: indícalo para cada equipo.', field='imei',
        )
    if imei2 and not imei:
        raise StockUnitError(
            'El segundo IMEI acompaña al primero: indica primero el IMEI principal.', field='imei',
        )
    if imei2 and imei2 == imei:
        raise StockUnitError('El segundo IMEI es distinto del primero.', field='imei2')
    _refuse_a_product_code(product.company_id, {'imei': imei, 'imei2': imei2, 'serial_number': serial})
    _refuse_another_devices_label_code(product.company_id, serial)

    cleaned = {
        'serial_number': serial,
        'imei': imei or None,
        'imei2': imei2 or None,
        'condition': _condition(row.get('condition')),
        'cost': _money(row.get('cost'), 'El costo', 'cost'),
        'notes': ' '.join(str(row.get('notes') or '').split())[:300],
    }
    # DC2 — what the shop tells buyers about the device. Optional, and only
    # present in the result when the row brought it (an old caller's rows are
    # cleaned exactly as before).
    if row.get('battery_health') not in (None, ''):
        cleaned['battery_health'] = clean_battery(row.get('battery_health'))
    if row.get('cosmetic_grade') not in (None, ''):
        cleaned['cosmetic_grade'] = clean_grade(product.company, row.get('cosmetic_grade'))
    if row.get('public_notes'):
        cleaned['public_notes'] = clean_public_notes(row.get('public_notes'))
    if row.get('sale_price') not in (None, ''):
        cleaned['sale_price'] = _money(row.get('sale_price'), 'El precio de venta', 'sale_price')
    return cleaned


def _refuse_a_product_code(company_id, identifiers: dict):
    """
    LABEL-02 · the reverse of LABEL-01's rule. A device whose IMEI, second IMEI or
    serial is already a product code of the company is refused (409): the till
    looks up products first, so that code would never reach the device. The
    product side refuses the same pair (`product_codes.check_supplied`).
    """
    from . import unit_codes

    for field, value in identifiers.items():
        holder = unit_codes.product_code_holding(company_id, value)
        if holder is not None:
            raise StockUnitError(
                f'«{value}» ya es un código del producto «{holder.product.name}» en esta empresa. '
                'Un equipo no puede usarlo como IMEI o serie: la caja encontraría el producto y no el equipo. '
                'Revisa el dato o quita ese código del producto.',
                field=field, code='product_code_conflict', status=409,
            )


def _refuse_another_devices_label_code(company_id, serial: str):
    """
    #178 review — a serial equal to another device's internal `label_code` is
    refused (409): `find_unit` reads the label code first, so a scan of this
    serial would land on the other device. Serials are stored upper-cased, like
    label codes, so the comparison is exact.
    """
    if serial and StockUnit.objects.filter(company_id=company_id, label_code=serial).exists():
        raise StockUnitError(
            f'«{serial}» es el código interno de otro equipo de esta empresa. Revisa la serie: '
            'la caja encontraría el otro equipo.',
            field='serial_number', code='label_code_conflict', status=409,
        )


def clean_unit(product, row: dict) -> dict:
    """The rules of ONE device, for a caller that stages before it writes (the bulk upload)."""
    return _clean_row(product, row)


def clean_reason(value) -> str:
    """The reason of a reception, as `receive_units` will accept it."""
    return _reason(value)


def _already_in_stock(company, product, cleaned: dict) -> tuple[str, str]:
    """(why this unit cannot enter, which field says so) — or ('', ''). Either IMEI column counts for both."""
    if StockUnit.objects.filter(
        company=company, product=product, serial_number=cleaned['serial_number'],
    ).exists():
        return (
            f'El número de serie {cleaned["serial_number"]} ya está registrado para este producto.',
            'serial_number',
        )
    for field in ('imei', 'imei2'):
        value = cleaned[field]
        if value and StockUnit.objects.filter(company=company).filter(
            Q(imei=value) | Q(imei2=value),
        ).exists():
            return 'Ese IMEI ya está registrado en otro equipo de la empresa.', field
    return '', ''


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------

@transaction.atomic
def set_serialized(product, *, serialized: bool, requires_imei: bool | None = None,
                   actor=None, request=None) -> Product:
    """
    Decide whether a product is counted by units. ONLY WHILE IT HAS NO STOCK.

    Turning it on over loose units would leave a quantity with no devices
    behind it; turning it off over devices would leave devices nobody counts.
    Either way the invariant is false from the first second, so both are
    refused and the shelf has to be emptied (or the units written off) first.
    """
    locked = Product.objects.select_for_update().get(pk=product.pk)
    if bool(serialized) != locked.is_serialized:
        in_stock = BranchStock.objects.filter(product=locked, quantity__gt=0).exists()
        live_units = StockUnit.objects.filter(
            product=locked, status__in=(StockUnit.Status.AVAILABLE, StockUnit.Status.RESERVED),
        ).exists()
        if in_stock or live_units:
            raise StockUnitError(
                'Este producto tiene existencias: no se puede cambiar cómo se controla. '
                'Deja el stock en cero primero.'
            )
        locked.is_serialized = bool(serialized)
    if requires_imei is not None:
        locked.requires_imei = bool(requires_imei) and locked.is_serialized
    if not locked.is_serialized:
        locked.requires_imei = False
    locked.save(update_fields=['is_serialized', 'requires_imei', 'updated_at'])
    AdminAuditLog.log(
        actor=actor, action='product_serialization_changed', target_type='product',
        target_id=locked.pk,
        metadata={'is_serialized': locked.is_serialized, 'requires_imei': locked.requires_imei},
        request=request, company=locked.company,
    )
    return locked


# ---------------------------------------------------------------------------
# Entering
# ---------------------------------------------------------------------------

class ReceivedUnits(list):
    """The units a reception created, and the Kardex line that brought them in (`.movement`)."""

    movement = None


@transaction.atomic
def receive_units(*, branch, product, units, actor=None, reason: str = '',
                  movement_type: str = StockMovement.PURCHASE_ENTRY, request=None) -> ReceivedUnits:
    """
    Units arrive. ALL OF THEM OR NONE: one bad line refuses the batch, and the
    error names the line.

    One Kardex entry for the batch, with the ids of the units it brought in.
    """
    if product.company_id != branch.company_id:
        raise StockUnitError('El producto no pertenece a la empresa de esta sucursal.')
    if not product.is_serialized:
        raise StockUnitError(f'"{product.name}" no se controla por número de serie.')
    rows = list(units or [])
    if not rows:
        raise StockUnitError('Indica al menos un equipo.')
    if len(rows) > MAX_BATCH:
        raise StockUnitError(f'Se reciben hasta {MAX_BATCH} equipos por vez.')
    if movement_type not in (StockMovement.PURCHASE_ENTRY, StockMovement.INITIAL_STOCK,
                             StockMovement.MANUAL_ENTRY):
        raise StockUnitError('Tipo de entrada no válido para equipos.')
    reason = _reason(reason)

    cleaned_rows, seen_serials, seen_imeis = [], set(), set()
    for index, row in enumerate(rows, start=1):
        try:
            cleaned = _clean_row(product, row)
            imeis = [value for value in (cleaned['imei'], cleaned['imei2']) if value]
            if cleaned['serial_number'] in seen_serials:
                raise StockUnitError(
                    'Este número de serie está repetido en la misma carga.', field='serial_number',
                )
            if seen_imeis.intersection(imeis):
                raise StockUnitError('Este IMEI está repetido en la misma carga.', field='imei')
            duplicate, where = _already_in_stock(branch.company, product, cleaned)
            if duplicate:
                raise StockUnitError(duplicate, field=where)
        except StockUnitError as exc:
            raise StockUnitError(str(exc), line=index, field=exc.field, code=exc.code,
                                 status=exc.status) from None
        seen_serials.add(cleaned['serial_number'])
        seen_imeis.update(imeis)
        cleaned_rows.append(cleaned)

    now = timezone.now()
    try:
        with transaction.atomic():
            created = [
                StockUnit.objects.create(
                    company_id=branch.company_id, branch=branch, product=product,
                    status=StockUnit.Status.AVAILABLE, received_at=now, created_by=actor, **cleaned,
                )
                for cleaned in cleaned_rows
            ]
    except IntegrityError:
        # Two receptions of the same device at once: the constraint decided.
        raise StockUnitError('Uno de estos equipos acaba de ser registrado por otra persona.') from None

    # LABEL-02 — every device gets its stable internal code in this same
    # transaction (D4): a rolled-back reception leaves no code behind.
    from . import unit_codes

    try:
        prefix = unit_codes.unit_prefix(branch.company)
    except unit_codes.UnitCodeError as exc:
        raise StockUnitError(str(exc), field='label_code') from None
    for unit in created:
        try:
            unit_codes.assign_label_code(unit, prefix=prefix)
        except unit_codes.UnitCodeError as exc:
            raise StockUnitError(str(exc)) from None

    movement = create_stock_movement(
        branch=branch, product_id=product.pk, movement_type=movement_type,
        quantity=len(created), reason=reason, actor=actor,
        reference_type='stock_units', units=created,
    )
    AdminAuditLog.log(
        actor=actor, action='stock_units_received', target_type='product', target_id=product.pk,
        # How many and where. Not the identifiers: the units are the record.
        metadata={'product_id': product.pk, 'branch_id': branch.pk, 'count': len(created)},
        request=request, company=branch.company,
    )
    received = ReceivedUnits(created)
    received.movement = movement
    return received


# ---------------------------------------------------------------------------
# One unit
# ---------------------------------------------------------------------------

def _move(unit, *, expected, to_status, movement_type, reason, actor, action, request=None,
          metadata=None, extra_fields=None):
    """Change ONE unit's availability and write its Kardex line, together."""
    current = StockUnit.objects.select_related('branch', 'product', 'company').get(pk=unit.pk)
    try:
        create_stock_movement(
            branch=current.branch, product_id=current.product_id, movement_type=movement_type,
            quantity=1, reason=reason, actor=actor, reference_type='stock_unit',
            reference_id=current.pk, metadata=metadata, units=[current],
            expected_unit_status=expected,
        )
    except InvalidMovementError as exc:
        raise StockUnitError(str(exc)) from None
    fields = {'status': to_status, **(extra_fields or {})}
    StockUnit.objects.filter(pk=current.pk).update(updated_at=timezone.now(), **fields)
    AdminAuditLog.log(
        actor=actor, action=action, target_type='stock_unit', target_id=current.pk,
        metadata={'product_id': current.product_id, 'branch_id': current.branch_id, 'reason': reason[:120]},
        request=request, company=current.company,
    )
    return StockUnit.objects.get(pk=current.pk)


@transaction.atomic
def write_off(unit, *, reason, actor=None, request=None) -> StockUnit:
    """Damaged, lost, stolen: out of stock for good, with the reason."""
    return _move(
        unit, expected=StockUnit.Status.AVAILABLE, to_status=StockUnit.Status.WRITTEN_OFF,
        movement_type=StockMovement.DAMAGED_EXIT, reason=_reason(reason), actor=actor,
        action='stock_unit_written_off', request=request,
    )


@transaction.atomic
def reserve(unit, *, reason, actor=None, request=None) -> StockUnit:
    """Set aside for somebody: it stops being sellable, so it leaves the count."""
    return _move(
        unit, expected=StockUnit.Status.AVAILABLE, to_status=StockUnit.Status.RESERVED,
        movement_type=StockMovement.MANUAL_EXIT, reason=f'Apartado: {_reason(reason)}'[:MAX_REASON],
        actor=actor, action='stock_unit_reserved', request=request,
    )


@transaction.atomic
def release(unit, *, actor=None, request=None) -> StockUnit:
    """
    A reservation ends: the device is back on the shelf.

    DC2 review — a WEB hold too, so the till can sell a device a cart left
    behind: freed for good (its buyer no longer holds it), with the staff
    member in the audit row and the hold it was in the Kardex line. Not while
    an order that may still be paid carries it and its time has not run out:
    that buyer may be paying right now. Shelf first, then the device — the
    module's one lock order — so the answer is read under the lock.
    """
    from . import inventory_services, unit_hold_services

    inventory_services._locked_branch_stocks(unit.branch, [unit.product])
    current = StockUnit.objects.select_for_update().get(pk=unit.pk)
    metadata = None
    if current.status == StockUnit.Status.RESERVED and current.hold_key:
        # Owner, 2026-10-15: nor past its time while the gateway may still
        # confirm that order's payment (a person who looked at the gateway's
        # panel may still release one nobody could settle).
        if ((current.hold_order_id and current.hold_until and current.hold_until > timezone.now()
                and unit_hold_services._order_is_live(current.hold_order_id))
                or unit_hold_services.payment_keeps_hold(current, include_unsettled=False)):
            raise StockUnitError(
                'Un comprador está pagando este equipo en la web (pedido '
                f'#{current.hold_order_id}). Podrás liberarlo cuando ese pago venza.',
                field='status',
            )
        metadata = {'web_hold': True, 'hold_order_id': current.hold_order_id}
    return _move(
        unit, expected=StockUnit.Status.RESERVED, to_status=StockUnit.Status.AVAILABLE,
        movement_type=StockMovement.MANUAL_ENTRY, reason='Apartado liberado',
        actor=actor, action='stock_unit_released', request=request, metadata=metadata,
        extra_fields={'hold_key': '', 'hold_until': None, 'hold_price': None, 'hold_order': None,
                      'hold_origin': '', 'hold_started_at': None},
    )


@transaction.atomic
def return_unit(unit, *, reason, actor=None, request=None) -> StockUnit:
    """
    A sold device comes back: the SAME row, available again.

    SERIAL-PICK-01: if it had been identified for an order and comes back
    BEFORE that order left the shop, it stops being the device of that order —
    the order names nothing again and cannot leave until somebody identifies
    the one that does. A device that left keeps its record: that sale named it.
    """
    from . import unit_identification

    current = StockUnit.objects.get(pk=unit.pk)
    returned = _move(
        unit, expected=StockUnit.Status.SOLD, to_status=StockUnit.Status.AVAILABLE,
        movement_type=StockMovement.RETURN_ENTRY, reason=_reason(reason), actor=actor,
        action='stock_unit_returned', request=request,
        metadata={'returned_from_order': current.order_id},
        extra_fields={'sold_at': None, 'order': None},
    )
    unit_identification.released_by_return(returned, current.order_id, actor=actor, request=request)
    return returned


@transaction.atomic
def update_unit(unit, *, actor=None, request=None, cost_reason=None, require_cost_reason=False,
                **fields) -> StockUnit:
    """
    Details that do not move stock. Identifiers and status are not here.

    A1 — «Corregir costo». `require_cost_reason` (the panel's path) makes a
    change of cost need a reason; the reason is audited, the amounts are not
    (the audit trail is read by more people than `costs.view`).
    """
    if cost_reason is not None and not isinstance(cost_reason, str):
        # A list would reach the audit trail as "['…']".
        raise StockUnitError('El motivo de la corrección del costo debe ser un texto.', field='cost_reason')
    locked = StockUnit.objects.select_for_update().get(pk=unit.pk)
    changed = []
    reason = ' '.join((cost_reason or '').split())[:300]
    if 'condition' in fields:
        locked.condition = _condition(fields['condition'])
        changed.append('condition')
    if 'cost' in fields:
        cost = _money(fields['cost'], 'El costo', 'cost')
        if cost != locked.cost:
            # DC1 — A SOLD UNIT'S COST IS HISTORY. Its sale line was valued at
            # it; rewriting it now would make the margin of that sale say
            # something it did not say when it was made.
            if locked.status == StockUnit.Status.SOLD:
                raise StockUnitError(
                    f'El equipo {locked.serial_number} ya se vendió: su costo no se puede cambiar.',
                    field='cost',
                )
            # Nor a written-off one's: it left the stock for good; its cost is history too.
            if locked.status == StockUnit.Status.WRITTEN_OFF:
                raise StockUnitError(
                    f'El equipo {locked.serial_number} se dio de baja: su costo no se puede cambiar.',
                    field='cost',
                )
            if require_cost_reason and not reason:
                raise StockUnitError(
                    'Indica el motivo de la corrección del costo.', field='cost_reason',
                )
            locked.cost = cost
            changed.append('cost')
    if 'notes' in fields:
        locked.notes = ' '.join(str(fields['notes'] or '').split())[:300]
        changed.append('notes')
    # DC2 — what the shop says about this device.
    if 'battery_health' in fields:
        locked.battery_health = clean_battery(fields['battery_health'])
        changed.append('battery_health')
    if 'cosmetic_grade' in fields:
        locked.cosmetic_grade = clean_grade(locked.company, fields['cosmetic_grade'])
        changed.append('cosmetic_grade')
    if 'public_notes' in fields:
        locked.public_notes = clean_public_notes(fields['public_notes'])
        changed.append('public_notes')
    if 'sale_price' in fields:
        price = _money(fields['sale_price'], 'El precio de venta', 'sale_price')
        if price != locked.sale_price:
            # Like its cost: the price a sold device left at is that sale's.
            if locked.status == StockUnit.Status.SOLD:
                raise StockUnitError(
                    f'El equipo {locked.serial_number} ya se vendió: su precio no se puede cambiar.',
                    field='sale_price',
                )
            locked.sale_price = price
            changed.append('sale_price')
    if changed:
        locked.save(update_fields=[*changed, 'updated_at'])
        AdminAuditLog.log(
            actor=actor, action='stock_unit_updated', target_type='stock_unit', target_id=locked.pk,
            metadata={
                'changed': changed,
                **({'cost_reason': reason} if 'cost' in changed and reason else {}),
            },
            request=request, company=locked.company,
        )
    return locked


# ---------------------------------------------------------------------------
# Selling
# ---------------------------------------------------------------------------
#
# There is no function here, on purpose. A sale takes its units inside
# `inventory_services.create_stock_movement`, under the shelf lock, in the same
# transaction as the Kardex line.
#
# SERIAL-PICK-01 — WHICH units:
#   · at the till, the ones the sale NAMES (`sell_units=True`): somebody
#     identified each device, by scanner or by hand. Two tills naming the same
#     device queue on that lock, and the second finds it sold;
#   · for a web payment of a product the web sells BY UNIT (DC2: a
#     serialized variant of a model, new or used), the devices the buyer's
#     cart held (`unit_hold_services`) — the one chosen is the one sold;
#   · for a web payment of any other serialized product, the oldest AVAILABLE
#     ones are SET ASIDE (`assign_units_for_sale=True`) so the shelf count is
#     right. Nobody has identified them: that happens when the order is handed
#     over (`unit_identification.identify_for_order`), and may swap them.


__all__ = [
    'InsufficientStockError', 'StockUnitError', 'receive_units',
    'release', 'reserve', 'return_unit', 'set_serialized', 'update_unit', 'write_off',
]
