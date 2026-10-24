"""
Which code a label carries, and at what bar width (ADR D4, D5).

ACCESSORY: the primary code if it fits; otherwise the SHORTEST active code
that fits, and the label says so. None fits → review. A code is never
generated, cut or drawn thinner to make it fit (§1.1.1, §1.1.4).

DEVICE, in this order, keeping the first that fits:
  1. the IMEI 1 — only when identifiers may be printed (profile `imei1` + the
     sensitive permission; for the technical label, the permission);
  2. the serial — same condition, only `A–Z0–9`, and only if the caller vouches
     that it is unambiguous in the whole company and no product code;
  3. the internal `label_code` (LABEL-02).
With the `internal` profile a commercial label carries only (3).

FIT: the symbol, quiet zones included, must fit inside the label width minus
the barcode margins at `min_module_dots`. The widest whole number of dots up
to `max_module_dots` that still fits is used: wider bars, never narrower.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import code128
from .layout import Layout

_SERIAL_SAFE = re.compile(r'^[A-Z0-9]+$')


@dataclass(frozen=True)
class Symbol:
    kind: str            # 'product_code' | 'imei' | 'serial' | 'label_code'
    value: str
    symbology: str
    module_dots: int
    dpi: int
    pattern: tuple[int, ...]

    @property
    def module_mm(self) -> float:
        return code128.dots_to_mm(self.module_dots, self.dpi)

    @property
    def quiet(self) -> tuple[int, int]:
        return code128.QUIET_MODULES[self.symbology]

    @property
    def modules(self) -> int:
        return sum(self.pattern) + sum(self.quiet)

    @property
    def width_mm(self) -> float:
        return self.modules * self.module_mm

    @property
    def sensitive(self) -> bool:
        return self.kind in ('imei', 'serial')


def available_mm(layout: Layout) -> float:
    return layout.width_mm - 2 * layout.barcode_margin_mm


def fit(kind: str, value: str, symbology: str, layout: Layout) -> tuple[Symbol | None, float]:
    """The symbol at the widest readable module that fits, or (None, width at the minimum)."""
    bars = tuple(code128.pattern(value, symbology))
    modules = sum(bars) + sum(code128.QUIET_MODULES[symbology])
    room = available_mm(layout)
    at_minimum = modules * code128.dots_to_mm(layout.min_module_dots, layout.dpi)
    for dots in range(layout.max_module_dots, layout.min_module_dots - 1, -1):
        if modules * code128.dots_to_mm(dots, layout.dpi) <= room + 1e-9:
            return Symbol(kind, value, symbology, dots, layout.dpi, bars), at_minimum
    return None, at_minimum


def _mm(value: float) -> str:
    return f'{value:.1f}'.replace('.', ',')


def _refusal(layout: Layout, tried: list[tuple[str, float]]) -> str:
    detail = ', '.join(f'{value} ({_mm(width)} mm)' for value, width in tried)
    module = f'{layout.module_mm:.2f}'.replace('.', ',')
    return (f'No cabe en {layout.size_label} a {module} mm por barra '
            f'(espacio {_mm(available_mm(layout))} mm): {detail}.')


def choose_accessory(codes, layout: Layout) -> tuple[Symbol | None, list[str], list[str]]:
    """(symbol, reasons for review, warnings)."""
    if not codes:
        return None, ['El producto no tiene ningún código de barras activo.'], []
    ordered = sorted(codes, key=lambda c: not c.is_primary)
    candidates, tried, undrawable = [], [], []
    for code in ordered:
        symbology = code128.drawable_symbology(code.value, code.symbology)
        try:
            symbol, width = fit('product_code', code.value, symbology, layout)
        except code128.BarcodeError:
            undrawable.append(code.value)
            continue
        if symbol is None:
            tried.append((code.value, width))
        else:
            candidates.append((code, symbol))
    if not candidates:
        reasons = [_refusal(layout, tried)] if tried else []
        if undrawable:
            reasons.append(f'No se puede dibujar como código de barras: {", ".join(undrawable)}.')
        return None, reasons, []
    primary = next((s for c, s in candidates if c.is_primary), None)
    if primary is not None:
        return primary, [], []
    shortest = min(candidates, key=lambda cs: (cs[1].width_mm, cs[0].value))[1]
    warnings = []
    if any(c.is_primary for c in codes):
        warnings.append(f'El código principal no cabe en {layout.size_label}; se usa {shortest.value}.')
    return shortest, [], warnings


def imei_is_valid(value: str | None) -> bool:
    from ..device_identity import luhn_check_digit

    return bool(value) and len(value) == 15 and value.isdigit() and luhn_check_digit(value[:14]) == value[14]


def serial_is_apt(data) -> bool:
    return bool(data.serial) and bool(_SERIAL_SAFE.match(data.serial)) and data.serial_is_unambiguous


def device_candidates(data, *, may_identify: bool) -> list[tuple[str, str]]:
    found = []
    if may_identify:
        if imei_is_valid(data.imei):
            found.append(('imei', data.imei))
        if serial_is_apt(data):
            found.append(('serial', data.serial))
    if data.label_code:
        found.append(('label_code', data.label_code))
    return found


def choose_device(data, layout: Layout, *, may_identify: bool) -> tuple[Symbol | None, list[str]]:
    """(symbol, reasons). The first candidate (D4 order) that fits."""
    candidates = device_candidates(data, may_identify=may_identify)
    if not candidates:
        if data.label_code is None and not may_identify:
            return None, ['El equipo aún no tiene código interno de etiqueta (llega con LABEL-02) '
                          'y esta etiqueta no puede llevar su IMEI ni su serie.']
        return None, ['El equipo no tiene un identificador que se pueda imprimir.']
    tried = []
    for kind, value in candidates:
        try:
            symbol, width = fit(kind, value, code128.CODE128, layout)
        except code128.BarcodeError:
            continue
        if symbol is not None:
            return symbol, []
        # Never echo an identifier the label may not show.
        tried.append((value if may_identify or kind == 'label_code' else kind, width))
    return None, [_refusal(layout, tried)]
