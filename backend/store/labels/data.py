"""
The `LabelData` contract: what the package receives, already resolved.

The caller (a view, later a batch) reads the models; this package never does.
The contract is CLOSED: `from_mapping` refuses any key it does not know, so a
cost, a supplier or a customer handed over by mistake is an error, not a field
that some layout might one day print.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

PROFILE_INTERNAL = 'internal'
PROFILE_IMEI1 = 'imei1'
PROFILES = (PROFILE_INTERNAL, PROFILE_IMEI1)

CONDITION_NEW = 'new'
CONDITION_USED = 'used'


class LabelDataError(ValueError):
    """Data that does not satisfy the contract."""


def _price(value):
    if value is None or value == '':
        return None
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LabelDataError('Precio inválido.') from exc
    if price < 0:
        raise LabelDataError('Precio negativo.')
    return price


def _text(value) -> str:
    return '' if value is None else ' '.join(str(value).split())


@dataclass(frozen=True)
class LabelCode:
    """One active code of a product, as `ProductBarcode` keeps it."""

    value: str
    symbology: str = 'unknown'
    is_primary: bool = False

    def __post_init__(self):
        if not isinstance(self.value, str) or not self.value.strip() or self.value != self.value.strip():
            raise LabelDataError('Un código vacío o con espacios en los bordes.')


class _Mapping:
    @classmethod
    def from_mapping(cls, mapping: dict):
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(set(mapping) - known)
        if unknown:
            raise LabelDataError(f'Datos no admitidos en una etiqueta: {", ".join(unknown)}')
        return cls(**mapping)

    def as_mapping(self) -> dict:
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}


@dataclass(frozen=True)
class AccessoryData(_Mapping):
    """A product sold by quantity. `copies` = the units the shelf needs."""

    product_id: int
    name: str
    price: Decimal | None
    currency: str
    codes: tuple[LabelCode, ...] = ()
    variant: str = ''
    copies: int = 1

    def __post_init__(self):
        object.__setattr__(self, 'name', _text(self.name))
        object.__setattr__(self, 'variant', _text(self.variant))
        object.__setattr__(self, 'price', _price(self.price))
        object.__setattr__(self, 'codes', tuple(self.codes))
        if not isinstance(self.copies, int) or isinstance(self.copies, bool) or self.copies < 1:
            raise LabelDataError('Las copias son un entero de 1 o más.')
        if not all(isinstance(c, LabelCode) for c in self.codes):
            raise LabelDataError('Los códigos tienen que ser LabelCode.')


@dataclass(frozen=True)
class DeviceData(_Mapping):
    """One `StockUnit`. Always one label: a device has no copies."""

    unit_id: int
    product_id: int
    model: str
    price: Decimal | None
    currency: str
    condition_line: str = CONDITION_NEW
    condition_label: str = ''
    attributes: tuple[tuple[str, str], ...] = ()
    battery_health: int | None = None
    imei: str | None = None
    imei2: str | None = None
    serial: str | None = None
    #: The caller says whether the serial is unique in the WHOLE company and
    #: is no product code (D4.2). Unknown = False: then it is never the symbol.
    serial_is_unambiguous: bool = False
    #: `StockUnit.label_code` (LABEL-02). None until that field exists.
    label_code: str | None = None
    grade: str = ''

    def __post_init__(self):
        object.__setattr__(self, 'model', _text(self.model))
        object.__setattr__(self, 'condition_label', _text(self.condition_label))
        object.__setattr__(self, 'grade', _text(self.grade))
        object.__setattr__(self, 'price', _price(self.price))
        object.__setattr__(self, 'attributes', tuple(
            (_text(name), _text(value)) for name, value in self.attributes if _text(value)))
        for name in ('imei', 'imei2', 'serial', 'label_code'):
            value = getattr(self, name)
            object.__setattr__(self, name, (str(value).strip() or None) if value is not None else None)
        if self.condition_line not in (CONDITION_NEW, CONDITION_USED):
            raise LabelDataError('Línea de condición desconocida.')
        if self.battery_health is not None and not (0 <= int(self.battery_health) <= 100):
            raise LabelDataError('Batería fuera de rango.')

    @property
    def is_used(self) -> bool:
        # ADR D5: `Product.condition_line = used`. Whether «reacondicionado» and
        # «caja abierta» count is the owner's open question P7.
        return self.condition_line == CONDITION_USED


@dataclass(frozen=True)
class DeviceOptions:
    """
    The two keys of an IMEI on paper (D6). `profile` is the company's
    (`CompanyLabelSettings.device_symbol`, LABEL-02); `sensitive_allowed` is the
    caller holding `inventory.labels.sensitive`. Both, or no IMEI and no serial.
    """

    profile: str = PROFILE_INTERNAL
    sensitive_allowed: bool = False

    def __post_init__(self):
        if self.profile not in PROFILES:
            raise LabelDataError('Perfil de símbolo desconocido.')

    @property
    def may_print_identifiers(self) -> bool:
        return self.profile == PROFILE_IMEI1 and self.sensitive_allowed
