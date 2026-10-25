"""
LABEL-02 — how a device is identified, kept apart from how a product is
(docs/adr-inventory-labels.md D4).

Three things that never mix:

  the commercial code   a `ProductBarcode` of the model or variant (LABEL-01);
  the unit's identity   its `StockUnit` row;
  what is scanned       one of that row's identifiers: `imei`, `imei2`,
                        `serial_number`, or the internal `label_code`.

THE TILL LOOKS UP PRODUCTS FIRST. A product code equal to a device's IMEI or
serial would therefore steal that device's scan. The rule holds from both sides:
`product_codes` refuses a product code that is a unit identifier, and this
module refuses a unit identifier that is a product code (`product_code_holding`,
called by `stock_unit_services._clean_row`, which every reception and the bulk
upload's preview go through).

Comparisons are case-insensitive where the stored forms allow a difference:
IMEIs are digits, serials and label codes are stored upper-cased, product codes
keep the capitals they were given (so they are matched with `iexact`).
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Q

from .models import AdminAuditLog, CompanyLabelSettings, ProductBarcode, StockUnit


def product_code_holding(company_id, value: str):
    """The `ProductBarcode` of the company (any product, active or not) whose code is `value`, or None."""
    if not value:
        return None
    return (ProductBarcode.objects.filter(company_id=company_id, code__iexact=value)
            .select_related('product').first())


def is_unit_identifier(company, code: str) -> bool:
    """
    Whether `code` is a device's IMEI, second IMEI, serial or internal `label_code` in `company`.

    IMEIs are stored as digits and serials upper-cased (`device_identity`), so
    comparing the IMEI exactly and the serial and label code upper-cased IS the case-insensitive
    comparison, and it keeps the (company, …) indexes usable. Every unit counts,
    sold or not: `find_unit` resolves them all.
    """
    upper = code.upper()
    return StockUnit.objects.filter(company=company).filter(
        Q(imei=code) | Q(imei2=code) | Q(serial_number=upper) | Q(label_code=upper),
    ).exists()


# ---------------------------------------------------------------------------
# label_code — the internal code of a device (D4)
# ---------------------------------------------------------------------------
#
# Shape: `<prefix><9 random digits><Luhn check digit>`, e.g. `U4827193056`.
#   * random, not a counter: a code on a sticker says nothing about how many
#     devices a shop takes in, and needs no shared row to lock;
#   * never derived from the IMEI or the serial;
#   * the prefix (1–4 letters, `CompanyLabelSettings.unit_code_prefix`) keeps it
#     from ever looking like an IMEI, a GTIN or a product code; the Luhn digit
#     lets the till tell a mistyped code from a real one;
#   * Code 128 width at 0.25 mm/module with quiet zones: `U` + 10 digits is
#     33 mm, which fits the 50x25 mm device label; a 4-letter prefix is ≈ 41 mm.

RANDOM_DIGITS = 9
MAX_ATTEMPTS = 5
_LABEL_CODE = re.compile(r'^([A-Z]{1,4})(\d{10})$')

#: LABEL-02 capabilities (docs/adr-inventory-labels.md D6).
CAP_LABELS_PRINT = 'inventory.labels.print'
CAP_LABELS_SENSITIVE = 'inventory.labels.sensitive'


class UnitCodeError(Exception):
    """No free internal code could be drawn. Callers turn it into their own refusal."""


def luhn_check_digit(body: str) -> int:
    """The digit that makes `body + digit` pass Luhn (the IMEI's own check)."""
    total = 0
    for position, char in enumerate(reversed(body)):
        value = int(char)
        if position % 2 == 0:            # these are doubled once the check digit is appended
            value *= 2
            value -= 9 if value > 9 else 0
        total += value
    return (10 - total % 10) % 10


def looks_like_label_code(text: str) -> bool:
    """A prefix of letters, ten digits, and a Luhn digit that agrees. Any capitals."""
    match = _LABEL_CODE.fullmatch(str(text or '').strip().upper())
    if not match:
        return False
    digits = match.group(2)
    return luhn_check_digit(digits[:-1]) == int(digits[-1])


def _random_body() -> str:
    return f'{secrets.randbelow(10 ** RANDOM_DIGITS):0{RANDOM_DIGITS}d}'


def settings_for(company) -> CompanyLabelSettings:
    """The company's label settings, or an UNSAVED row with the defaults. Reading writes nothing."""
    row = CompanyLabelSettings.objects.filter(company=company).first()
    return row if row is not None else CompanyLabelSettings(company=company)


def _code_taken(company_id, code: str) -> bool:
    return (StockUnit.objects.filter(company_id=company_id)
            .filter(Q(label_code=code) | Q(serial_number=code)).exists()
            or product_code_holding(company_id, code) is not None)


def assign_label_code(unit, *, prefix: str | None = None) -> str:
    """
    Give `unit` its internal code if it has none, and return the code it has.

    NEVER OVERWRITES: the write is conditional on `label_code IS NULL`. A draw
    that is taken — another device's code or serial, or any product code of the
    company — is drawn again, up to MAX_ATTEMPTS, and so is one lost to the
    unique index in a race (inside a savepoint). Must run in a transaction.
    """
    if unit.label_code:
        return unit.label_code
    prefix = prefix or settings_for(unit.company).unit_code_prefix
    for _ in range(MAX_ATTEMPTS):
        body = _random_body()
        code = f'{prefix}{body}{luhn_check_digit(body)}'
        if _code_taken(unit.company_id, code):
            continue
        try:
            with transaction.atomic():
                written = StockUnit.objects.filter(pk=unit.pk, label_code__isnull=True).update(label_code=code)
        except IntegrityError:
            continue
        if not written:                                  # somebody coded it meanwhile: theirs stands
            unit.label_code = StockUnit.objects.values_list('label_code', flat=True).get(pk=unit.pk)
            return unit.label_code
        unit.label_code = code
        return code
    raise UnitCodeError(
        f'No se pudo asignar un código interno al equipo tras {MAX_ATTEMPTS} intentos. Vuelve a intentarlo.'
    )


# ---------------------------------------------------------------------------
# Settings, and the two keys of an IMEI on paper (D6)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceLabelAccess:
    """
    What a person may put on a device label in a company.

    `profile`             the company's `device_symbol` (`internal` | `imei1`)
    `sensitive_allowed`   the person holds `inventory.labels.sensitive`

    An IMEI or a serial reaches paper only with BOTH (`may_print_identifiers`).
    LABEL-03's `labels.DeviceOptions(profile, sensitive_allowed)` takes these two.
    """

    profile: str
    sensitive_allowed: bool

    @property
    def may_print_identifiers(self) -> bool:
        return self.profile == CompanyLabelSettings.DEVICE_SYMBOL_IMEI1 and self.sensitive_allowed


def device_label_access(user, company) -> DeviceLabelAccess:
    from .tenancy import has_capability

    return DeviceLabelAccess(
        profile=settings_for(company).device_symbol,
        sensitive_allowed=bool(has_capability(user, company, CAP_LABELS_SENSITIVE)),
    )


def audit_sensitive_print(*, actor, company, unit_ids, purpose: str, request=None, channel: str = 'pdf'):
    """
    D6 — every label that shows an IMEI or a serial leaves this line: WHICH
    devices (their ids), never the identifiers themselves. The caller serves the
    PDF with `Cache-Control: no-store`.
    """
    ids = sorted({int(pk) for pk in unit_ids})
    return AdminAuditLog.log(
        actor=actor, action='label_sensitive_printed', target_type='stock_unit',
        target_id=ids[0] if len(ids) == 1 else '',
        metadata={'unit_ids': ids, 'count': len(ids), 'purpose': purpose, 'channel': channel},
        request=request, company=company,
    )


_SETTINGS_FIELDS = ('unit_code_prefix', 'device_symbol')


def update_settings(company, data: dict, *, actor, request=None) -> CompanyLabelSettings:
    """
    Change the company's label settings (`company.manage`, decided by the caller).

    Raises `django.core.exceptions.ValidationError` with the field. Leaves
    `AdminAuditLog('label_settings_updated')` with the fields that changed.
    """
    with transaction.atomic():
        row, _created = CompanyLabelSettings.objects.select_for_update().get_or_create(company=company)
        before = {field: getattr(row, field) for field in _SETTINGS_FIELDS}
        for field in _SETTINGS_FIELDS:
            if field in data:
                setattr(row, field, data[field])
        changed = sorted(field for field in _SETTINGS_FIELDS if getattr(row, field) != before[field])
        if changed:
            row.save()
            AdminAuditLog.log(
                actor=actor, action='label_settings_updated', target_type='company_label_settings',
                target_id=row.pk,
                metadata={'changed_fields': changed,
                          'old': {f: before[f] for f in changed}, 'new': {f: getattr(row, f) for f in changed}},
                request=request, company=company,
            )
    return row
