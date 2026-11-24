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

import datetime
import hashlib
import re
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from . import device_identity, inventory_services, money_input
from .inventory_services import (
    InsufficientStockError, InvalidMovementError, InventoryError, create_stock_movement,
)
from .models import AdminAuditLog, BranchStock, Product, StockMovement, StockUnit

MAX_BATCH = 200
MAX_REASON = 300
#: INV-LOAD-01 — `StockUnit.notes` holds 300 characters. More is refused with this sentence, never cut in silence.
MAX_NOTES = 300
NOTES_TOO_LONG = f'Las notas internas del equipo admiten hasta {MAX_NOTES} caracteres.'
#: INV-LOAD-01 — the oldest entry date a person may state: older is a typo («1920»), not stock.
OLDEST_ENTRY = datetime.date(2000, 1, 1)


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
    """
    The cost of one device, or None.

    B1-0b-1 — a TEXT a person typed («Registrar equipos», the guided registration, the device rows of an on-order
    reception, a TEXT cell of the device upload, the cost correction) is read by `money_input.amount_text`, B1-0's
    policy: «5,500»/«5.500» are ambiguous and refused, never 5.50 or 5.5000; «1800,2000» or «1800/2000» are not one
    cost. Before it was handed to `Decimal`, which read «5.500» as 5.50.

    A NUMBER is not a text. A `Decimal` or an `int` the system holds (an internal cost of four decimals, a line's
    cost carried to its devices) is read as before, rounded to the cent. A float (a JSON number or a sheet number
    cell) takes at most two decimals: never rounded in silence. What is stored is never re-read.
    """
    # Blank text is «no amount», as `cost_services.states_a_cost` reads it (COST-WRITE-AUTHORITY).
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise StockUnitError(f'{label} no es un importe válido.', field=field)
    if isinstance(value, str):
        try:
            value = money_input.amount_text(value, f'{label[:1].lower()}{label[1:]}')
        except money_input.AmountTextError as exc:
            # Only the codes a form acts on travel (`invalid` stays code-less, as every malformed cost before).
            code = exc.code if exc.code in money_input.ACTIONABLE_CODES else None
            raise StockUnitError(exc.detail, field=field, code=code) from None
    elif isinstance(value, float):
        value = repr(value)
        try:
            exact = Decimal(value)
            if exact.is_finite() and abs(exact) < Decimal('100000000') and exact != exact.quantize(Decimal('0.01')):
                raise StockUnitError(f'{label} lleva a lo sumo dos decimales.', field=field)
        except (InvalidOperation, ValueError):
            raise StockUnitError(f'{label} no es un importe válido.', field=field) from None
    try:
        amount = Decimal(str(value))
        if amount.is_finite() and abs(amount) >= Decimal('100000000'):
            raise InvalidOperation
        amount = amount.quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise StockUnitError(f'{label} no es un importe válido.', field=field) from None
    if amount == 0:
        amount = Decimal('0.00')                # «-0» is zero, never a signed text to re-read
    # NaN survives `quantize` and then cannot be compared: it is not an amount.
    if not amount.is_finite() or amount < 0 or amount >= Decimal('100000000'):
        raise StockUnitError(f'{label} no es un importe válido.', field=field)
    return amount


_SALE_PRICE = re.compile(r'[0-9]{1,8}(\.[0-9]{1,2})?')
SALE_PRICE_INVALID = 'El precio de venta debe ser un importe mayor que cero, con hasta 2 decimales.'


def clean_sale_price(value) -> Decimal | None:
    """
    INV-RECEIVE-PRICE-03 — THE price of one used device, the same reading on every path that writes it (the ficha,
    «Registrar equipos», the unit import, the receipt): ASCII digits with at most two decimals and GREATER THAN ZERO.
    Blank or null is «sin precio» (the device waits; it is sold nowhere until priced). Zero is not a price: the till
    refuses it and the shop must not offer it. A float, a boolean or a third decimal is refused, never rounded.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, Decimal):
        text = format(value, 'f') if value.is_finite() else 'x'
    elif isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise StockUnitError(SALE_PRICE_INVALID, field='sale_price')
    if not _SALE_PRICE.fullmatch(text):
        raise StockUnitError(SALE_PRICE_INVALID, field='sale_price')
    amount = Decimal(text).quantize(Decimal('0.01'))
    if amount <= 0:
        raise StockUnitError(SALE_PRICE_INVALID, field='sale_price')
    return amount


def has_usable_price(price) -> bool:
    """Whether a stored `sale_price` can be charged: present, finite and greater than zero (the till's own test)."""
    return price is not None and price.is_finite() and price > 0


def _condition(value) -> str:
    condition = str(value or StockUnit.Condition.NEW)
    if condition not in StockUnit.Condition.values:
        raise StockUnitError('Condición desconocida.', field='condition')
    return condition


# ---------------------------------------------------------------------------
# INV-INTAKE-GUARD-01 — a device's stated state against its variant's line
# ---------------------------------------------------------------------------
#
# The line of a variant (`Product.condition_line`) is what the web sells it as: a NEW variant at its own price, a USED
# one device by device. A device that contradicts its line would be sold as what it is not. These are INTAKE guards
# (the receipt and the unit import): nothing already in stock is read or changed by them.

CONDITION_LINE_MISMATCH = 'condition_line_mismatch'


def line_condition(product, row: dict) -> dict:
    """
    The row with the condition its line implies when the row states none: «used» in a USED variant (before this, an
    absent condition was stored «new» even there). A stated condition is never rewritten: it is judged by
    `condition_line_problem`. Returns a copy; the caller's row is untouched.
    """
    if product.condition_line != Product.CONDITION_USED or str((row or {}).get('condition') or '').strip():
        return row
    return {**row, 'condition': StockUnit.Condition.USED}


def condition_line_problem(product, *, condition: str, graded: bool) -> tuple[str, str] | None:
    """
    `(field, message)` when a device stated as `condition` (and `graded`: it carries a cosmetic grade) contradicts the
    line of `product`; None when it may enter. Pure: no database.

        USED variant        a «new» device is refused (it would be sold as used, one by one, at a used price).
        NEW model variant   a «used» device, or any graded one, is refused (the web would sell it as new, at the new
                            price). A product outside the catalogue of models keeps its rules: its line is not a
                            commercial decision anybody took.
    """
    if product.condition_line == Product.CONDITION_USED:
        if condition == StockUnit.Condition.NEW:
            return 'condition', (
                f'«{product.name}» es una variante seminueva y este equipo dice «Nuevo». Recíbelo en la variante '
                'nueva del mismo modelo o indica su estado real.')
        return None
    if not product.product_model_id:
        return None
    if condition == StockUnit.Condition.USED:
        return 'condition', (
            f'«{product.name}» es una variante nueva y este equipo es usado: la tienda lo vendería como nuevo, al '
            'precio de nuevo. Recíbelo en la variante seminueva del mismo modelo.')
    if graded:
        return 'cosmetic_grade', (
            f'«{product.name}» es una variante nueva y este equipo trae estado estético, que es de un equipo '
            'seminuevo. Recíbelo en la variante seminueva del mismo modelo o quita el estado estético.')
    return None


def condition_line_warning(product, *, condition: str) -> str:
    """What to say (never refuse) of an open-box or refurbished device entering a NEW model variant."""
    if (product.condition_line != Product.CONDITION_USED and product.product_model_id
            and condition in (StockUnit.Condition.OPEN_BOX, StockUnit.Condition.REFURBISHED)):
        label = StockUnit.Condition(condition).label
        return (f'equipo «{label}» en una variante nueva: la tienda lo ofrecerá como nuevo, al precio de la '
                'variante. Si no es así, recíbelo en la variante seminueva.')
    return ''


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


def clean_notes(value) -> str:
    """INV-LOAD-01 — a device's internal notes, whitespace collapsed. Too long is an error on `notes`, never a cut."""
    text = ' '.join(str(value or '').split())
    if len(text) > MAX_NOTES:
        raise StockUnitError(NOTES_TOO_LONG, field='notes')
    return text


_DAY_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y')


def clean_received_on(value) -> datetime.date | None:
    """
    INV-LOAD-01 — the day a device entered the shop, as a person states it: a date (or the date of a datetime), or a
    text «AAAA-MM-DD» / «DD/MM/AAAA». Empty is None (today). Never in the future, never before `OLDEST_ENTRY`.
    """
    if value in (None, ''):
        return None
    if isinstance(value, datetime.datetime):
        day = (timezone.localtime(value) if timezone.is_aware(value) else value).date()
    elif isinstance(value, datetime.date):
        day = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # An Excel date cell arrives as its ISO text, with a time: «2026-10-01T00:00:00».
        text = re.split(r'[T ]', text, maxsplit=1)[0]
        day = None
        for pattern in _DAY_FORMATS:
            try:
                day = datetime.datetime.strptime(text, pattern).date()
                break
            except ValueError:
                continue
        if day is None:
            raise StockUnitError('Fecha de ingreso no válida: escríbela como AAAA-MM-DD o DD/MM/AAAA.',
                                 field='received_on')
    else:
        raise StockUnitError('Fecha de ingreso no válida.', field='received_on')
    if day > timezone.localdate():
        raise StockUnitError('La fecha de ingreso no puede ser futura.', field='received_on')
    if day < OLDEST_ENTRY:
        raise StockUnitError(
            f'La fecha de ingreso no puede ser anterior al {OLDEST_ENTRY.strftime("%d/%m/%Y")}.',
            field='received_on')
    return day


def received_at_for(day) -> datetime.datetime:
    """The moment stored for a device that entered on `day`: now for today (or none), else that day at noon."""
    if day is None or day == timezone.localdate():
        return timezone.now()
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(12, 0)))


def clean_public_notes(value) -> str:
    text = ' '.join(str(value or '').split())
    if len(text) > 500:
        raise StockUnitError('Los detalles para el comprador admiten hasta 500 caracteres.', field='public_notes')
    return text


# ---------------------------------------------------------------------------
# USED-READY-01 — what the shop says about ONE used device beyond battery and grade
# ---------------------------------------------------------------------------
#
# Only a device of the USED line carries them (a new device is sold with its variant's terms). All four are public:
# the buyer reads them on the device's card. Optional on every door; an absent key changes nothing.

USED_ONLY_FIELDS = ('warranty_days', 'functional_state', 'defects', 'accessories')
MAX_WARRANTY_DAYS = 3650
MAX_DEFECTS = 500
MAX_ACCESSORIES = 300
NOT_USED_LINE = 'not_used_line'
FAULTS_UNDESCRIBED = 'Un equipo «con fallas» dice cuáles: completa «Defectos».'
_WARRANTY_DAYS = re.compile(r'[0-9]{1,4}', re.ASCII)
_FUNCTIONAL_WORDS = {
    'full': StockUnit.FunctionalState.FULL, 'funciona': StockUnit.FunctionalState.FULL,
    'funciona al 100': StockUnit.FunctionalState.FULL, 'funciona al 100 %': StockUnit.FunctionalState.FULL,
    'sin fallas': StockUnit.FunctionalState.FULL, 'sin fallas declaradas': StockUnit.FunctionalState.FULL,
    'with_faults': StockUnit.FunctionalState.WITH_FAULTS, 'con fallas': StockUnit.FunctionalState.WITH_FAULTS,
    'con fallas declaradas': StockUnit.FunctionalState.WITH_FAULTS,
}


def clean_warranty_days(value) -> int | None:
    """Days of THIS device's warranty. Empty = the company's policy; 0 = sold without warranty."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    message = f'La garantía se indica en días: un número entero de 0 a {MAX_WARRANTY_DAYS}, o vacío.'
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise StockUnitError(message, field='warranty_days')
    text = str(value).strip()
    if not _WARRANTY_DAYS.fullmatch(text) or int(text) > MAX_WARRANTY_DAYS:
        raise StockUnitError(message, field='warranty_days')
    return int(text)


def clean_functional_state(value) -> str:
    """'' (not stated), «full» or «with_faults» — by code or by the words of the screen and the template."""
    import unicodedata

    if value is None:
        return ''
    if not isinstance(value, str):
        raise StockUnitError('Estado funcional desconocido.', field='functional_state')
    folded = ''.join(ch for ch in unicodedata.normalize('NFKD', value) if not unicodedata.combining(ch))
    folded = ' '.join(folded.lower().split())
    if not folded:
        return ''
    found = _FUNCTIONAL_WORDS.get(folded) or _FUNCTIONAL_WORDS.get(folded.replace('%', '').strip())
    if found is None:
        raise StockUnitError('Estado funcional desconocido: «Sin fallas» o «Con fallas».',
                             field='functional_state')
    return str(found)


def _clean_text(value, *, limit: int, field: str, label: str) -> str:
    if value is not None and not isinstance(value, (str, int)):
        raise StockUnitError(f'{label}: escribe un texto.', field=field)
    text = ' '.join(str(value or '').split())
    if len(text) > limit:
        raise StockUnitError(f'{label} admite hasta {limit} caracteres.', field=field)
    return text


def clean_defects(value) -> str:
    return _clean_text(value, limit=MAX_DEFECTS, field='defects', label='«Defectos»')


def clean_accessories(value) -> str:
    return _clean_text(value, limit=MAX_ACCESSORIES, field='accessories', label='«Accesorios»')


_USED_CLEANERS = {
    'warranty_days': clean_warranty_days, 'functional_state': clean_functional_state,
    'defects': clean_defects, 'accessories': clean_accessories,
}


def clean_used_details(product, values: dict) -> dict:
    """
    The used-only fields present in `values`, cleaned. A field stated for a device that is not of the USED line is
    refused (`not_used_line`): a new device is sold with its variant's terms. `None` / blank is «not stated».
    """
    cleaned = {}
    for name in USED_ONLY_FIELDS:
        if name not in values:
            continue
        cleaned[name] = _USED_CLEANERS[name](values[name])
        stated = cleaned[name] not in (None, '')
        if stated and product.condition_line != Product.CONDITION_USED:
            raise StockUnitError(
                f'«{product.name}» no es seminuevo: la garantía propia, el estado funcional, los defectos y los '
                'accesorios son de los equipos seminuevos.', field=name, code=NOT_USED_LINE)
    return cleaned


def refuse_undescribed_faults(functional_state: str, defects: str) -> None:
    if functional_state == StockUnit.FunctionalState.WITH_FAULTS and not defects:
        raise StockUnitError(FAULTS_UNDESCRIBED, field='defects')


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
    """
    One unit's identifiers and details, cleaned. Absent identifiers are None.

    INV-INTAKE-GUARD-01 — every device that ENTERS passes here (`receive_units`, and `clean_unit` for the callers
    that stage first), so the line rule holds on every door: a device without a stated condition takes its variant's
    line, and one that contradicts it is refused (`condition_line_mismatch`).
    """
    row = line_condition(product, row if isinstance(row, dict) else {})
    serial = _identifier(device_identity.clean_serial, row.get('serial_number'), 'serial_number')
    imei = _identifier(device_identity.clean_imei, row.get('imei'), 'imei')
    imei2 = _identifier(
        lambda value: device_identity.clean_imei(value, label='El segundo IMEI'),
        row.get('imei2'), 'imei2',
    )

    # SERIAL-OPTIONAL-01 — a product that carries an IMEI is identified by it (unique in the company): its serial
    # is optional. Any other product has only its serial.
    if product.requires_imei and not imei:
        raise StockUnitError(
            f'"{product.name}" lleva IMEI: indícalo para cada equipo.', field='imei',
        )
    if not serial and not product.requires_imei:
        raise StockUnitError('Cada equipo lleva su número de serie.', field='serial_number')
    if imei2 and not imei:
        raise StockUnitError(
            'El segundo IMEI acompaña al primero: indica primero el IMEI principal.', field='imei',
        )
    if imei2 and imei2 == imei:
        raise StockUnitError('El segundo IMEI es distinto del primero.', field='imei2')
    _refuse_a_product_code(product.company_id, {'imei': imei, 'imei2': imei2, 'serial_number': serial})
    _refuse_another_devices_label_code(product.company_id, serial)

    cleaned = {
        'serial_number': serial or None,
        'imei': imei or None,
        'imei2': imei2 or None,
        'condition': _condition(row.get('condition')),
        'cost': _money(row.get('cost'), 'El costo', 'cost'),
        'notes': clean_notes(row.get('notes')),
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
    price = clean_sale_price(row.get('sale_price'))
    if price is not None:
        cleaned['sale_price'] = price
    # USED-READY-01 — optional, only when the row brought them. NEVER the inspection or the publication: every device
    # enters pending and unpublished, whoever receives it and through whichever door.
    used = {name: value for name, value in clean_used_details(product, row).items() if value not in (None, '')}
    refuse_undescribed_faults(used.get('functional_state', ''), used.get('defects', ''))
    cleaned.update(used)
    problem = condition_line_problem(product, condition=cleaned['condition'],
                                     graded=cleaned.get('cosmetic_grade') is not None)
    if problem:
        raise StockUnitError(problem[1], field=problem[0], code=CONDITION_LINE_MISMATCH)
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
    # A device without a serial is not «the same serial» as another one without it: `filter(serial_number=None)`
    # would read IS NULL.
    # INV-UNIT-CORRECT-01: a VOIDED row keeps its identifiers as the record and occupies none of them.
    live = StockUnit.objects.exclude(status=StockUnit.Status.VOIDED)
    if cleaned['serial_number'] and live.filter(
        company=company, product=product, serial_number=cleaned['serial_number'],
    ).exists():
        return (
            f'El número de serie {cleaned["serial_number"]} ya está registrado para este producto.',
            'serial_number',
        )
    for field in ('imei', 'imei2'):
        value = cleaned[field]
        if value and live.filter(company=company).filter(
            Q(imei=value) | Q(imei2=value),
        ).exists():
            return 'Ese IMEI ya está registrado en otro equipo de la empresa.', field
    return '', ''


def serial_elsewhere(company, product, serial: str) -> str:
    """
    INV-SERIAL-01 — the name of ANOTHER product of `company` that already has a device with this serial, or ''.
    The database keys a serial per product, so this is a WARNING for the person (the owner decides whether it should
    refuse): a device without an IMEI could otherwise enter twice under two variants.
    """
    if not serial:
        return ''
    return (StockUnit.objects.filter(company=company, serial_number=serial).exclude(product=product)
            .exclude(status=StockUnit.Status.VOIDED)
            .order_by('pk').values_list('product__name', flat=True).first()) or ''


def find_conflict(company, product, cleaned: dict) -> tuple[str, str]:
    """
    INV-OPS-01 — whether a CLEANED row is already in stock: (message, field) or ('', ''). The very question
    `receive_units` asks again under its own lock, for a caller that wants the answer without writing.
    """
    return _already_in_stock(company, product, cleaned)


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------

def holds_stock(product) -> bool:
    """
    Whether anything of `product` exists: on a shelf, as a live device, or on its way between two branches (a
    dispatched transfer has left the source shelf and not reached the destination yet). How a product is counted
    changes only when this is False (INV-S5-01 review: stock in transit was missed).
    """
    from .models import StockTransfer, StockTransferItem

    if BranchStock.objects.filter(product=product, quantity__gt=0).exists():
        return True
    if StockUnit.objects.filter(
            product=product, status__in=(StockUnit.Status.AVAILABLE, StockUnit.Status.RESERVED,
                                         StockUnit.Status.IN_TRANSIT)).exists():
        return True
    return StockTransferItem.objects.filter(
        product=product, transfer__status=StockTransfer.STATUS_IN_TRANSIT).exists()


def lock_products(products) -> None:
    """
    INV-LOAD-01 re-review — hold `products` (pk order) before any of their shelves, for a caller that will switch them
    with `set_serialized` after locking shelves: DEC-CATALOG-PALETTE-LOCK-01 is Product → shelf (registration, the POS,
    a purchase receipt). Must run inside the caller's transaction.
    """
    ids = sorted({product.pk for product in products})
    if ids:
        list(Product.objects.select_for_update().filter(pk__in=ids).order_by('pk').values_list('pk', flat=True))


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
        if holds_stock(locked):
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

def _lock_identifiers(company_id, product_id, cleaned_rows):
    """
    One physical identifier, one queue. The database only forbids a repeated `imei` or a repeated `imei2` BY
    COLUMN, and the duplicate check below is an unlocked read: two receptions at once, one carrying X as `imei`
    and the other as `imei2`, both read «free» and both wrote it. So, before any lookup, the batch takes a
    transaction advisory lock per identifier — an IMEI (either column: one key) of the company, a serial of the
    product — in a sorted order (two batches sharing identifiers never wait on each other in a circle). The
    loser reads the winner's row and refuses with the usual duplicate error. Held until the transaction ends.
    """
    if connection.vendor != 'postgresql':
        return
    keys = set()
    for cleaned in cleaned_rows:
        if cleaned['serial_number']:
            keys.add(f'unit-serial|{company_id}|{product_id}|{cleaned["serial_number"]}')
        for field in ('imei', 'imei2'):
            if cleaned[field]:
                keys.add(f'unit-imei|{company_id}|{cleaned[field]}')
    ids = sorted({int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True) for key in keys})
    with connection.cursor() as cursor:
        for lock_id in ids:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])


class ReceivedUnits(list):
    """The units a reception created, and the Kardex line that brought them in (`.movement`)."""

    movement = None


@transaction.atomic
def receive_units(*, branch, product, units, actor=None, reason: str = '',
                  movement_type: str = StockMovement.PURCHASE_ENTRY, request=None, supplier=None,
                  received_on=None) -> ReceivedUnits:
    """
    Units arrive. ALL OF THEM OR NONE: one bad line refuses the batch, and the
    error names the line.

    One Kardex entry for the batch, with the ids of the units it brought in.

    `supplier` (CATALOG-REFERENCE-01 · PR F, D-F-3): who sold the batch, written on every unit. Only a supplier
    of the branch's company; one of another company is refused like one that does not exist.

    `received_on` (INV-LOAD-01): the day the batch entered the shop, when it is not today — the real stock of a shop
    that starts here came in long ago. Validated by `clean_received_on` (never future, never before 2000).
    """
    if product.company_id != branch.company_id:
        raise StockUnitError('El producto no pertenece a la empresa de esta sucursal.')
    if supplier is not None and supplier.company_id != branch.company_id:
        raise StockUnitError('Proveedor no encontrado.', field='supplier', code='not_found', status=404)
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
    received_at = received_at_for(clean_received_on(received_on))

    # First every row is CLEANED (pure reads), so that the identifiers of the whole batch can be locked
    # before any of them is looked up; the verdicts are then given in row order, exactly as before.
    prepared = []
    for index, row in enumerate(rows, start=1):
        try:
            prepared.append((index, _clean_row(product, row), None))
        except StockUnitError as exc:
            prepared.append((index, None, exc))
    # LOCK ORDER (module docstring): the shelf first, the identifiers second. INV-LOAD-01 review — the shelf used to be
    # taken only by the writer, after the identifiers, the inverse of a caller that holds the shelf to decide an opening.
    inventory_services._locked_branch_stocks(branch, [product])
    _lock_identifiers(branch.company_id, product.pk, [c for _, c, _ in prepared if c is not None])

    cleaned_rows, seen_serials, seen_imeis = [], set(), set()
    for index, cleaned, failure in prepared:
        try:
            if failure is not None:
                raise failure
            imeis = [value for value in (cleaned['imei'], cleaned['imei2']) if value]
            if cleaned['serial_number'] and cleaned['serial_number'] in seen_serials:
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
        if cleaned['serial_number']:
            seen_serials.add(cleaned['serial_number'])
        seen_imeis.update(imeis)
        cleaned_rows.append(cleaned)

    try:
        with transaction.atomic():
            created = [
                StockUnit.objects.create(
                    company_id=branch.company_id, branch=branch, product=product,
                    status=StockUnit.Status.AVAILABLE, received_at=received_at, created_by=actor, supplier=supplier,
                    **cleaned,
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
        metadata={'product_id': product.pk, 'branch_id': branch.pk, 'count': len(created),
                  **({'supplier_id': supplier.pk} if supplier is not None else {})},
        request=request, company=branch.company,
    )
    received = ReceivedUnits(created)
    received.movement = movement
    return received


# ---------------------------------------------------------------------------
# One unit
# ---------------------------------------------------------------------------

def _move(unit, *, expected, to_status, movement_type, reason, actor, action, request=None,
          metadata=None, extra_fields=None, reference_type='stock_unit', audit=None):
    """
    Change ONE unit's availability and write its Kardex line, together.

    `audit` (INV-UNIT-CORRECT-01): what the audit row says INSTEAD of the free reason — a reason typed by a person
    may carry an IMEI or a serial, and the audit trail never does (I12). `movement_id` is added to it.
    """
    current = StockUnit.objects.select_related('branch', 'product', 'company').get(pk=unit.pk)
    try:
        movement = create_stock_movement(
            branch=current.branch, product_id=current.product_id, movement_type=movement_type,
            quantity=1, reason=reason, actor=actor, reference_type=reference_type,
            reference_id=current.pk, metadata=metadata, units=[current],
            expected_unit_status=expected,
        )
    except InvalidMovementError as exc:
        raise StockUnitError(str(exc)) from None
    fields = {'status': to_status, **(extra_fields or {})}
    StockUnit.objects.filter(pk=current.pk).update(updated_at=timezone.now(), **fields)
    said = ({**audit, 'movement_id': getattr(movement, 'pk', None)} if audit is not None
            else {'reason': reason[:120]})
    AdminAuditLog.log(
        actor=actor, action=action, target_type='stock_unit', target_id=current.pk,
        metadata={'product_id': current.product_id, 'branch_id': current.branch_id, **said},
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


# ---------------------------------------------------------------------------
# INV-UNIT-CORRECT-01 · «Anular ingreso» (docs/adr-unit-correction.md §5.1)
# ---------------------------------------------------------------------------

#: UC-D3 — why an entry is voided: a closed list, plus the person's own words.
VOID_REASONS = {
    'typo': 'Error de digitación (IMEI o serie)',
    'wrong_variant': 'Variante equivocada',
    'wrong_branch': 'Sucursal equivocada',
    'duplicate': 'Ingreso duplicado',
    'not_arrived': 'El equipo no llegó',
}
NOT_CORRECTABLE = 'unit_not_correctable'
_ENTRY_TYPES = (StockMovement.PURCHASE_ENTRY, StockMovement.INITIAL_STOCK, StockMovement.MANUAL_ENTRY)


def entry_movement_if_clean(unit):
    """
    UC-D4 (strict) — the Kardex line that brought `unit` in, when it is the ONLY line that names it and no sale
    record names it; otherwise None. A device whose entry line cannot be found is not clean either (ADR §4).
    """
    from .models import OrderUnit

    # Read within the device's CURRENT product (an indexed column), not the whole company: every line that moved
    # it after its entry — sale, hold, return, transfer, a change of variant — leaves one on the product it is in now,
    # so a device that changed product shows a non-entry line here and is refused, as it must be.
    lines = list(StockMovement.objects.filter(company_id=unit.company_id, product_id=unit.product_id,
                                              metadata__unit_ids__contains=[unit.pk]).order_by('pk')[:2])
    if len(lines) != 1 or lines[0].movement_type not in _ENTRY_TYPES:
        return None
    if OrderUnit.objects.filter(unit_id=unit.pk).exists():
        return None
    return lines[0]


def _not_correctable(why: str) -> StockUnitError:
    return StockUnitError(f'Este equipo no se puede anular: {why}', code=NOT_CORRECTABLE, status=409)


@transaction.atomic
def void_unit(unit, *, reason, reason_code, actor=None, request=None) -> StockUnit:
    """
    The device should never have entered. It leaves with a CORRECTION_NEGATIVE line (its own cost, decided by the
    Kardex), stays as a VOIDED row WITH its identifiers, and frees them for the right device. Only while its entry is
    its only history. Idempotent by state: a voided device is answered as it is.
    """
    from . import inventory_services

    inventory_services._locked_branch_stocks(unit.branch, [unit.product])
    current = StockUnit.objects.select_for_update().get(pk=unit.pk)
    if current.status == StockUnit.Status.VOIDED:
        return current          # already done: the record, whatever was asked this time
    code = str(reason_code or '').strip()
    if code not in VOID_REASONS:
        raise StockUnitError('Elige el motivo de la anulación.', field='reason_code')
    text = _reason(reason)
    if current.branch_id != unit.branch_id or current.product_id != unit.product_id:
        raise StockUnitError('El equipo cambió mientras se anulaba: vuelve a abrirlo.', status=409, code=NOT_CORRECTABLE)
    if current.status != StockUnit.Status.AVAILABLE:
        raise _not_correctable(f'está {current.get_status_display().lower()}.')
    entry = entry_movement_if_clean(current)
    if entry is None:
        raise _not_correctable('tiene movimientos después de su ingreso (o no se encuentra su ingreso). '
                               'Un equipo con historial no se anula.')
    movement = _move(
        current, expected=StockUnit.Status.AVAILABLE, to_status=StockUnit.Status.VOIDED,
        movement_type=StockMovement.CORRECTION_NEGATIVE,
        reason=f'Anulación de ingreso ({VOID_REASONS[code]}): {text}'[:MAX_REASON], actor=actor,
        action='stock_unit_voided', request=request, reference_type='stock_unit_void',
        metadata={'voids_movement_id': entry.pk, 'voided_unit_id': current.pk, 'reason_code': code},
        audit={'voids_movement_id': entry.pk, 'reason_code': code},
        extra_fields={'voided_at': timezone.now(), 'voided_by': actor if getattr(actor, 'pk', None) else None,
                      'void_reason': text[:300]},
    )
    return movement


def variant_targets(unit):
    """UC-D5 — the variants `unit` may move to: same company, same model, same line, by serial; never its own."""
    product = unit.product
    if not product.product_model_id:
        return Product.objects.none()
    return (Product.objects.filter(company_id=unit.company_id, product_model_id=product.product_model_id,
                                   condition_line=product.condition_line, is_serialized=True)
            .exclude(pk=product.pk).order_by('name', 'pk'))


@transaction.atomic
def move_unit_to_variant(unit, *, product, reason, actor=None, request=None) -> StockUnit:
    """
    INV-UNIT-CORRECT-01 §5.2 — the right device under the wrong variant (capacity, colour) of the same model and line.
    A CORRECTION_NEGATIVE on the origin shelf and a CORRECTION_POSITIVE on the destination, both naming the device,
    with the product changed between them under the same locks; a label batch for the destination (UC-D6).
    Only while its entry is its only history (UC-D4). Idempotent by state: already there, nothing is written.
    """
    from . import inventory_services, label_batches

    if product.company_id != unit.company_id:
        raise StockUnitError('Ese producto no es de esta empresa.', field='product', status=404)
    text = _reason(reason)
    inventory_services._locked_branch_stocks(unit.branch, [unit.product, product])
    current = StockUnit.objects.select_for_update(of=('self',)).select_related('product', 'company').get(pk=unit.pk)
    if current.product_id == product.pk:
        return current
    # Phase 2 review: both product rows, in id order, before either Kardex line updates them (each line ends with
    # an UPDATE of its product's total). Two moves A→B and B→A in different branches would otherwise wait on each
    # other in a circle. The destination's rules are read from the LOCKED row.
    locked_products = {p.pk: p for p in Product.objects.select_for_update().filter(
        pk__in=[current.product_id, product.pk]).order_by('pk')}
    product = locked_products[product.pk]
    if current.branch_id != unit.branch_id or current.product_id != unit.product_id:
        raise StockUnitError('El equipo cambió mientras se corregía: vuelve a abrirlo.', status=409,
                             code=NOT_CORRECTABLE)
    origin = current.product
    if not variant_targets(current).filter(pk=product.pk).exists():
        raise StockUnitError(
            'Elige otra variante del mismo modelo y de la misma línea (nuevo o seminuevo), controlada por serie.',
            field='product',
        )
    if current.status != StockUnit.Status.AVAILABLE:
        raise _not_correctable(f'está {current.get_status_display().lower()}.')
    entry = entry_movement_if_clean(current)
    if entry is None:
        raise _not_correctable('tiene movimientos después de su ingreso (o no se encuentra su ingreso). '
                               'Un equipo con historial no se corrige aquí.')
    from .models import StockTransfer, StockTransferUnit

    if StockTransferUnit.objects.filter(unit_id=current.pk, transfer__status=StockTransfer.STATUS_DRAFT).exists():
        # SERIAL-TRANSFER-01: its line in that draft names THIS variant; moving it would break the dispatch.
        raise _not_correctable('está en una transferencia en borrador. Quítalo de la transferencia primero.')
    if product.requires_imei and not current.imei:
        raise StockUnitError(f'"{product.name}" lleva IMEI y este equipo no tiene.', field='imei')
    if not current.serial_number and not product.requires_imei:
        raise StockUnitError(f'"{product.name}" pide número de serie y este equipo no tiene.', field='serial_number')
    if current.serial_number and StockUnit.objects.filter(
            company_id=current.company_id, product=product, serial_number=current.serial_number,
    ).exclude(status=StockUnit.Status.VOIDED).exists():
        raise StockUnitError(f'Esa variante ya tiene un equipo con la serie {current.serial_number}.',
                             field='serial_number')

    common = {'from_product_id': origin.pk, 'to_product_id': product.pk, 'entry_movement_id': entry.pk}
    line_reason = f'Cambio de variante: {origin.name} → {product.name}. {text}'[:MAX_REASON]
    try:
        with transaction.atomic():      # a savepoint: a lost race on the serial leaves the outer transaction usable
            out, moved, into = _move_lines(current, origin, product, line_reason, common, actor)
    except InvalidMovementError as exc:
        raise StockUnitError(str(exc)) from None
    except IntegrityError:
        raise StockUnitError('Esa variante ya tiene un equipo con esa serie.', field='serial_number') from None
    # UC-D6 — returned with the device so the screen says it (or that it failed: FAILED, never silent).
    batch = label_batches.create_for_movement(into, units=[moved], actor=actor)
    AdminAuditLog.log(
        actor=actor, action='stock_unit_variant_changed', target_type='stock_unit', target_id=current.pk,
        # Ids only: the reason a person typed may carry an IMEI (I12).
        metadata={'product_id': product.pk, 'branch_id': current.branch_id, **common,
                  'movement_ids': [out.pk, into.pk]},
        request=request, company=current.company,
    )
    result = StockUnit.objects.get(pk=current.pk)
    result.label_batch = batch
    return result


def _move_lines(current, origin, product, line_reason, common, actor):
    """The pair of correction lines of a change of variant, with the product changed between them."""
    out = create_stock_movement(
        branch=current.branch, product_id=origin.pk, movement_type=StockMovement.CORRECTION_NEGATIVE,
        quantity=1, reason=line_reason, actor=actor, reference_type='stock_unit_variant',
        reference_id=current.pk, metadata=dict(common), units=[current],
        expected_unit_status=StockUnit.Status.AVAILABLE,
    )
    StockUnit.objects.filter(pk=current.pk).update(product=product, updated_at=timezone.now())
    moved = StockUnit.objects.get(pk=current.pk)
    into = create_stock_movement(
        branch=current.branch, product_id=product.pk, movement_type=StockMovement.CORRECTION_POSITIVE,
        quantity=1, reason=line_reason, actor=actor, reference_type='stock_unit_variant',
        reference_id=current.pk, metadata={**common, 'pair_movement_id': out.pk}, units=[moved],
        expected_unit_status=StockUnit.Status.AVAILABLE,
    )
    return out, moved, into


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
    # USED-READY-01 — a used device that comes back is checked again before it is sold again.
    from . import used_unit_services

    if used_unit_services.require_reinspection(returned.pk, why='returned', actor=actor, request=request):
        returned = StockUnit.objects.get(pk=returned.pk)
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
    if locked.status == StockUnit.Status.VOIDED and fields:
        # INV-UNIT-CORRECT-01: a voided row is a record of what should never have entered. Nothing of it changes.
        raise StockUnitError(f'El equipo {locked.identifier} se anuló: ya no se edita.', status=409,
                             code=NOT_CORRECTABLE)
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
                    f'El equipo {locked.identifier} ya se vendió: su costo no se puede cambiar.',
                    field='cost',
                )
            # Nor a written-off or a voided one's: it left the stock for good; its cost is history too.
            if locked.status == StockUnit.Status.VOIDED:
                raise StockUnitError(
                    f'El equipo {locked.identifier} se anuló: su costo no se puede cambiar.', field='cost',
                )
            if locked.status == StockUnit.Status.WRITTEN_OFF:
                raise StockUnitError(
                    f'El equipo {locked.identifier} se dio de baja: su costo no se puede cambiar.',
                    field='cost',
                )
            # TRANSFER-VALUATION-01 — the line that left and the line that arrives describe the same devices at
            # the same cost: a device's cost is corrected before it leaves or after it arrives, never on the road.
            if locked.status == StockUnit.Status.IN_TRANSIT:
                raise StockUnitError(
                    f'El equipo {locked.identifier} está en tránsito: corrige su costo cuando se reciba en destino.',
                    field='cost', status=409,
                )
            if require_cost_reason and not reason:
                raise StockUnitError(
                    'Indica el motivo de la corrección del costo.', field='cost_reason',
                )
            locked.cost = cost
            changed.append('cost')
    if 'notes' in fields:
        locked.notes = clean_notes(fields['notes'])
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
        price = clean_sale_price(fields['sale_price'])
        if price != locked.sale_price:
            # Like its cost: the price a sold device left at is that sale's.
            if locked.status == StockUnit.Status.SOLD:
                raise StockUnitError(
                    f'El equipo {locked.identifier} ya se vendió: su precio no se puede cambiar.',
                    field='sale_price',
                )
            locked.sale_price = price
            changed.append('sale_price')
    # USED-READY-01 — warranty, functional state, defects, accessories: a used device only.
    used = clean_used_details(locked.product, {k: fields[k] for k in USED_ONLY_FIELDS if k in fields})
    for name, value in used.items():
        if getattr(locked, name) != value:
            setattr(locked, name, value)
            changed.append(name)
    if used:
        refuse_undescribed_faults(locked.functional_state, locked.defects)
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
    'move_unit_to_variant', 'release', 'reserve', 'return_unit', 'set_serialized', 'update_unit', 'void_unit',
    'write_off',
]
