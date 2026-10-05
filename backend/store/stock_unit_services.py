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
    `price_override`, `reason`) so a form can say it next to that input instead
    of in a banner the person has to map back by reading.
    """

    def __init__(self, message: str, *, line: int | None = None, field: str | None = None):
        super().__init__(message)
        self.line = line
        self.field = field


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

    return {
        'serial_number': serial,
        'imei': imei or None,
        'imei2': imei2 or None,
        'condition': _condition(row.get('condition')),
        'cost': _money(row.get('cost'), 'El costo', 'cost'),
        'price_override': _money(row.get('price_override'), 'El precio del equipo', 'price_override'),
        'notes': ' '.join(str(row.get('notes') or '').split())[:300],
    }


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

@transaction.atomic
def receive_units(*, branch, product, units, actor=None, reason: str = '',
                  movement_type: str = StockMovement.PURCHASE_ENTRY, request=None) -> list[StockUnit]:
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
            raise StockUnitError(str(exc), line=index, field=exc.field) from None
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

    create_stock_movement(
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
    return created


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
    return _move(
        unit, expected=StockUnit.Status.RESERVED, to_status=StockUnit.Status.AVAILABLE,
        movement_type=StockMovement.MANUAL_ENTRY, reason='Apartado liberado',
        actor=actor, action='stock_unit_released', request=request,
    )


@transaction.atomic
def return_unit(unit, *, reason, actor=None, request=None) -> StockUnit:
    """A sold device comes back: the SAME row, available again."""
    current = StockUnit.objects.get(pk=unit.pk)
    return _move(
        unit, expected=StockUnit.Status.SOLD, to_status=StockUnit.Status.AVAILABLE,
        movement_type=StockMovement.RETURN_ENTRY, reason=_reason(reason), actor=actor,
        action='stock_unit_returned', request=request,
        metadata={'returned_from_order': current.order_id},
        extra_fields={'sold_at': None, 'order': None},
    )


@transaction.atomic
def update_unit(unit, *, actor=None, request=None, **fields) -> StockUnit:
    """Details that do not move stock. Identifiers and status are not here."""
    locked = StockUnit.objects.select_for_update().get(pk=unit.pk)
    changed = []
    if 'condition' in fields:
        locked.condition = _condition(fields['condition'])
        changed.append('condition')
    if 'cost' in fields:
        locked.cost = _money(fields['cost'], 'El costo', 'cost')
        changed.append('cost')
    if 'price_override' in fields:
        locked.price_override = _money(fields['price_override'], 'El precio del equipo', 'price_override')
        changed.append('price_override')
    if 'notes' in fields:
        locked.notes = ' '.join(str(fields['notes'] or '').split())[:300]
        changed.append('notes')
    if changed:
        locked.save(update_fields=[*changed, 'updated_at'])
        AdminAuditLog.log(
            actor=actor, action='stock_unit_updated', target_type='stock_unit', target_id=locked.pk,
            metadata={'changed': changed}, request=request, company=locked.company,
        )
    return locked


# ---------------------------------------------------------------------------
# Selling
# ---------------------------------------------------------------------------
#
# There is no function here, on purpose. A sale takes its units inside
# `inventory_services.create_stock_movement(assign_units_for_sale=True)`, under
# the shelf lock: the oldest AVAILABLE units of the fulfilling branch, marked
# sold in the same transaction as the Kardex line. Two tills asking for the last
# device queue on that lock, and the second finds none.


__all__ = [
    'InsufficientStockError', 'StockUnitError', 'receive_units',
    'release', 'reserve', 'return_unit', 'set_serialized', 'update_unit', 'write_off',
]
