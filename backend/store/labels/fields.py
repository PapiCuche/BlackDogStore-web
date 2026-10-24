"""
The closed registry of what a label may print, per purpose (ADR D5).

A template — the built-in ones here, the `LabelTemplate` rows that come later —
chooses fields ONLY from this registry. Cost, margin, supplier and customer
are not in it: there is no key a template could name to print them.
The identifiers of a device (IMEI 2, serial, internal code as text) exist only
for `device_technical`. On a commercial label the IMEI can appear only as the
SYMBOL (barcode + its text), and only with the company's `imei1` profile and
the sensitive permission (D6) — decided in `symbols`, not here.

`attribute:<name>` prints one variant attribute by the COMPANY'S name for it
(«Capacidad», «Color», «SIM»…). The built-in layouts never name one: the
attribute names are tenant data. They print `attributes`, every attribute the
product has, in the company's order. An attribute the product lacks is not
printed and never invented.
"""
from __future__ import annotations

from dataclasses import dataclass

ACCESSORY = 'accessory'
DEVICE_COMMERCIAL = 'device_commercial'
DEVICE_TECHNICAL = 'device_technical'
PURPOSES = (ACCESSORY, DEVICE_COMMERCIAL, DEVICE_TECHNICAL)

#: Not graphic text: the logo box, the bars and the text under the bars.
GRAPHIC_KEYS = frozenset({'logo', 'barcode', 'code_text'})

_DEVICE_COMMERCIAL = frozenset({
    'logo', 'model', 'attributes', 'attribute:*', 'condition', 'battery', 'price',
    'barcode', 'code_text',
})

ALLOWED: dict[str, frozenset[str]] = {
    ACCESSORY: frozenset({'logo', 'name', 'variant', 'price', 'barcode', 'code_text'}),
    DEVICE_COMMERCIAL: _DEVICE_COMMERCIAL,
    DEVICE_TECHNICAL: (_DEVICE_COMMERCIAL - {'price'})
    | frozenset({'imei', 'imei2', 'serial', 'grade', 'label_code'}),
}

#: Never on any label, whatever a template says. The registry above already
#: lacks them; this list is what the tests and the template validation name.
FORBIDDEN = frozenset({
    'cost', 'unit_cost', 'average_cost', 'reference_cost', 'margin', 'supplier',
    'supplier_name', 'customer', 'customer_name', 'customer_email', 'order',
})

#: Fields that identify one physical device. A label carrying one is sensitive.
SENSITIVE_TEXT_KEYS = frozenset({'imei', 'imei2', 'serial'})


@dataclass(frozen=True)
class FieldSpec:
    """One line (or group of lines) of a layout, in the order printed."""

    key: str
    size_pt: float = 6.0
    min_size_pt: float | None = None
    max_lines: int = 1
    bold: bool = False
    #: «Sólo si cabe»: dropped (and said) when the label is full.
    optional: bool = False
    #: Shares the line of the previous field, joined with « · ».
    inline: bool = False

    @property
    def floor_pt(self) -> float:
        return self.min_size_pt if self.min_size_pt is not None else self.size_pt


def spec(key: str, **options) -> FieldSpec:
    return FieldSpec(key=key, **options)


def is_allowed(purpose: str, key: str) -> bool:
    allowed = ALLOWED.get(purpose, frozenset())
    if key in FORBIDDEN:
        return False
    if key.startswith('attribute:'):
        return 'attribute:*' in allowed and bool(key.split(':', 1)[1].strip())
    return key in allowed
