"""
Layouts: the size, the margins, the printer assumptions and the ordered fields.

The three built-ins are CODE CONSTANTS, not tenant data, and carry no shop:
no name, no attribute name, no logo. They are the owner's confirmed sizes
(ADR §1.1.4): accessory 30×20 mm, phone 50×25 mm; and a larger internal
technical label, 60×40 mm, until the owner fixes its size (ADR §10, P8/P10).

A later `LabelTemplate` row (LABEL-03b) becomes a layout through
`Layout.with_overrides(...)`: it may change the size, margins, dpi, the module
(never below the readable one) and the fields (only from the registry of its
purpose). Every override is validated; nothing here trusts a template.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from . import code128
from .fields import ACCESSORY, DEVICE_COMMERCIAL, DEVICE_TECHNICAL, PURPOSES, FieldSpec, is_allowed, spec


class LayoutError(ValueError):
    """A layout (or a template override) that cannot be printed as asked."""


@dataclass(frozen=True)
class Layout:
    key: str
    purpose: str
    name: str
    width_mm: float
    height_mm: float
    fields: tuple[FieldSpec, ...]
    margin_mm: float = 1.0
    #: Side margin of the barcode row only: its quiet zone already is white.
    barcode_margin_mm: float = 1.0
    barcode_height_mm: float = 6.0
    code_text_pt: float = 5.0
    logo_max_w_mm: float = 8.0
    logo_max_h_mm: float = 8.0
    dpi: int = code128.DEFAULT_DPI
    min_module_dots: int = code128.DEFAULT_MIN_MODULE_DOTS
    max_module_dots: int = 3
    #: Text that does not fit goes on to a second sticker (technical only).
    continuation: bool = False

    def __post_init__(self):
        self.validate()

    @property
    def module_mm(self) -> float:
        return code128.dots_to_mm(self.min_module_dots, self.dpi)

    @property
    def size_label(self) -> str:
        def n(v):
            return f'{v:g}'.replace('.', ',')
        return f'{n(self.width_mm)}×{n(self.height_mm)}'

    def has(self, key: str) -> bool:
        return any(f.key == key for f in self.fields)

    def validate(self):
        if self.purpose not in PURPOSES:
            raise LayoutError(f'Propósito desconocido: {self.purpose}')
        if not (10 <= self.width_mm <= 200 and 10 <= self.height_mm <= 200):
            raise LayoutError('La etiqueta tiene que medir entre 10 y 200 mm por lado.')
        for name in ('margin_mm', 'barcode_margin_mm'):
            value = getattr(self, name)
            if value < 0 or value * 2 >= min(self.width_mm, self.height_mm):
                raise LayoutError(f'Margen inválido: {name}.')
        if self.barcode_height_mm < 3 or self.code_text_pt < 4:
            raise LayoutError('El código necesita al menos 3 mm de barras y 4 pt de texto.')
        if not (100 <= self.dpi <= 600):
            raise LayoutError('Resolución fuera de rango.')
        if self.min_module_dots < 1 or self.max_module_dots < self.min_module_dots:
            raise LayoutError('Ancho de barra inválido.')
        if self.module_mm + 1e-9 < code128.MIN_READABLE_MODULE_MM:
            raise LayoutError(
                f'Una barra de {self.module_mm:.3f} mm no se lee: el mínimo es '
                f'{code128.MIN_READABLE_MODULE_MM} mm.')
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise LayoutError('Un campo repetido.')
        for field in self.fields:
            if not is_allowed(self.purpose, field.key):
                raise LayoutError(f'El campo «{field.key}» no se admite en una etiqueta {self.purpose}.')
            if field.floor_pt < 4 or field.size_pt < field.floor_pt or field.max_lines < 1:
                raise LayoutError(f'Tamaño de texto inválido en «{field.key}».')
        if 'barcode' not in keys:
            raise LayoutError('Toda etiqueta lleva su código de barras.')

    def with_overrides(self, **changes) -> 'Layout':
        if 'purpose' in changes and changes['purpose'] != self.purpose:
            raise LayoutError('Una plantilla no cambia el propósito de su diseño.')
        try:
            return dataclasses.replace(self, **changes)
        except TypeError as exc:
            raise LayoutError(str(exc)) from exc


ACCESSORY_30X20 = Layout(
    key='accessory_30x20', purpose=ACCESSORY, name='Accesorio 30×20 mm',
    width_mm=30, height_mm=20, margin_mm=1.0, barcode_margin_mm=0.8,
    barcode_height_mm=5.5, code_text_pt=5.0, logo_max_w_mm=7.0, logo_max_h_mm=6.0,
    fields=(
        spec('logo'),
        spec('name', size_pt=6.5, min_size_pt=4.5, max_lines=2, bold=True),
        spec('variant', size_pt=5.0, min_size_pt=4.5, optional=True),
        spec('price', size_pt=8.5, min_size_pt=7.0, bold=True),
        spec('barcode'),
        spec('code_text'),
    ),
)

DEVICE_COMMERCIAL_50X25 = Layout(
    key='device_commercial_50x25', purpose=DEVICE_COMMERCIAL, name='Teléfono 50×25 mm',
    width_mm=50, height_mm=25, margin_mm=1.5, barcode_margin_mm=2.0,
    barcode_height_mm=7.0, code_text_pt=6.0, logo_max_w_mm=10.0, logo_max_h_mm=9.0,
    fields=(
        spec('logo'),
        spec('model', size_pt=8.0, min_size_pt=6.0, max_lines=2, bold=True),
        spec('attributes', size_pt=6.5, min_size_pt=5.0, max_lines=2),
        spec('condition', size_pt=6.0, min_size_pt=5.0),
        spec('battery', size_pt=6.0, min_size_pt=5.0, inline=True),
        spec('price', size_pt=10.0, min_size_pt=8.0, bold=True),
        spec('barcode'),
        spec('code_text'),
    ),
)

DEVICE_TECHNICAL_60X40 = Layout(
    key='device_technical_60x40', purpose=DEVICE_TECHNICAL, name='Técnica 60×40 mm',
    width_mm=60, height_mm=40, margin_mm=2.0, barcode_margin_mm=2.0,
    barcode_height_mm=8.0, code_text_pt=6.5, continuation=True,
    fields=(
        spec('model', size_pt=7.5, min_size_pt=6.0, max_lines=2, bold=True),
        spec('attributes', size_pt=6.0, min_size_pt=5.0, max_lines=2),
        spec('condition', size_pt=6.0, min_size_pt=5.0),
        spec('grade', size_pt=6.0, min_size_pt=5.0, inline=True),
        spec('battery', size_pt=6.0, min_size_pt=5.0, inline=True),
        spec('imei', size_pt=6.5, min_size_pt=5.5, max_lines=2),
        spec('imei2', size_pt=6.5, min_size_pt=5.5, max_lines=2),
        spec('serial', size_pt=6.5, min_size_pt=5.5, max_lines=4),
        spec('label_code', size_pt=6.5, min_size_pt=5.5),
        spec('barcode'),
        spec('code_text'),
    ),
)

BUILTIN_LAYOUTS = {
    layout.key: layout for layout in (ACCESSORY_30X20, DEVICE_COMMERCIAL_50X25, DEVICE_TECHNICAL_60X40)
}
