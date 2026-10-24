"""
Bar patterns and widths: Code 128, and EAN-13 / UPC-A in their own symbology.

THE ASSUMED PRINTER. The Advance ADV-9010N has no public technical sheet
(ADR §2.11, `BLOCKED: HARDWARE`). Every layout therefore assumes the usual
class of thermal head: **203 dpi**, and a narrowest bar of **2 dots**
(2 / 203 in = 0,2502 mm). That is the readable minimum this package defends:
`MIN_READABLE_MODULE_MM` = 0,25 mm. A layout may ask for a wider bar, never a
narrower one, and a code that does not fit at that module is REFUSED — the
package never draws it thinner to make it fit.

THE QUIET ZONE is 10 modules on each side (the Code 128 minimum), and it is
part of the width every check uses. EAN-13 / UPC-A keep their own 11 + 7 and
9 + 9 modules.

A PATTERN is a list of widths in modules, alternating bar, space, bar…
starting with a bar. The PDF draws it; LABEL-04 can turn the same list into
whole dots for a thermal bitmap without asking reportlab again.
"""
from __future__ import annotations

#: The assumed head and the narrowest bar it can print reliably (see above).
DEFAULT_DPI = 203
DEFAULT_MIN_MODULE_DOTS = 2
MIN_READABLE_MODULE_MM = 0.25

CODE128 = 'code128'
EAN13 = 'ean13'
UPCA = 'upca'
SYMBOLOGIES = (CODE128, EAN13, UPCA)

#: Modules of quiet zone, (left, right), per symbology.
QUIET_MODULES = {CODE128: (10, 10), EAN13: (11, 7), UPCA: (9, 9)}


class BarcodeError(ValueError):
    """The value cannot be encoded in that symbology."""


def dots_to_mm(dots: int, dpi: int) -> float:
    return dots * 25.4 / dpi


# --- Code 128 -----------------------------------------------------------

def _code128_pattern(value: str) -> list[int]:
    from reportlab.graphics.barcode.code128 import Code128

    if not value or any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
        raise BarcodeError('Code 128 sólo admite ASCII imprimible.')
    symbol = Code128(value, quiet=0, humanReadable=0)
    symbol.validate()
    symbol.encode()
    symbol.decompose()
    # `decomposed` is one letter per element: upper case a bar, lower case a
    # space, and the letter its width in modules (A = 1).
    widths = []
    expect_bar = True
    for letter in symbol.decomposed:
        if letter.isupper() != expect_bar:
            raise BarcodeError('Patrón Code 128 inesperado.')
        widths.append(ord(letter.upper()) - ord('A') + 1)
        expect_bar = not expect_bar
    return widths


# --- EAN-13 / UPC-A -----------------------------------------------------

_L = ('3211', '2221', '2122', '1411', '1132', '1231', '1114', '1312', '1213', '3112')
_PARITY = ('LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL')


def gs1_check_ok(digits: str) -> bool:
    """Whether the last digit of a GTIN is its check digit."""
    if not digits.isdigit() or len(digits) < 2:
        return False
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def _ean13_pattern(digits: str) -> list[int]:
    if len(digits) != 13 or not gs1_check_ok(digits):
        raise BarcodeError('No es un EAN-13 válido.')
    parity = _PARITY[int(digits[0])]
    # Every digit is (space, bar, space, bar) on the left and (bar, space, bar,
    # space) on the right; G is L read backwards. Widths alone carry it.
    widths = [1, 1, 1]
    for d, side in zip(digits[1:7], parity):
        element = _L[int(d)]
        widths += [int(w) for w in (element if side == 'L' else element[::-1])]
    widths += [1, 1, 1, 1, 1]
    for d in digits[7:]:
        widths += [int(w) for w in _L[int(d)]]
    widths += [1, 1, 1]
    return widths


def pattern(value: str, symbology: str) -> list[int]:
    """The bar/space widths, in modules, without quiet zones."""
    if symbology == CODE128:
        return _code128_pattern(value)
    if symbology == EAN13:
        return _ean13_pattern(value)
    if symbology == UPCA:
        if len(value) != 12:
            raise BarcodeError('No es un UPC-A válido.')
        return _ean13_pattern('0' + value)   # a UPC-A is an EAN-13 led by 0
    raise BarcodeError(f'Simbología no soportada: {symbology}')


def total_modules(value: str, symbology: str) -> int:
    left, right = QUIET_MODULES[symbology]
    return sum(pattern(value, symbology)) + left + right


def symbol_width_mm(value: str, symbology: str, module_mm: float) -> float:
    """The printed width, quiet zones included."""
    return total_modules(value, symbology) * module_mm


def drawable_symbology(value: str, stored_hint: str) -> str:
    """
    How a stored code is drawn. EAN-13 / UPC-A only when the shop SAID so (the
    import only says so when the check digit holds) AND it still holds; any
    other code — an internal one above all — is Code 128, so an internal code
    is never presented as a GTIN (ADR §1.1.2).
    """
    if stored_hint == EAN13 and len(value) == 13 and gs1_check_ok(value):
        return EAN13
    if stored_hint == UPCA and len(value) == 12 and gs1_check_ok(value):
        return UPCA
    return CODE128
