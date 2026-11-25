"""
SCAN-TO-STOCK-01 — what a scanned code IS, before anything is looked up (docs/adr-scan-to-stock.md).

A clerk registering a device reads several barcodes off its box, its screen and its labels: the IMEIs, the serial,
the commercial code of the box (UPC/EAN), sometimes an EID or a MEID, sometimes the shop's own internal label. A
keyboard-wedge scanner types each one and presses Enter (or Tab). This module answers, for ONE code, which of those
it is — or that it cannot tell.

    A PURE FUNCTION. No database, no request, no clock. The same text always gives the same reading.

RULES OF THE ENGINE
  * NEVER BY TIMING. Whether the keys came fast or slow says how the code arrived, not what it is.
  * NO MANUFACTURER. The shapes below are the international ones (3GPP IMEI, GSMA EID, 3GPP2 MEID, GS1 GTIN), true of
    every maker. Nothing here names a brand, and no model is ever inferred from an IMEI: the TAC is not a reliable
    source of the model here, so it is never read.
  * NOT SURE → SAY SO. `certain=False` with `alternatives`: what the clerk may say the code is. The screen asks; it
    never guesses.
  * AN EID OR A MEID IS NEVER AN IMEI, not even «an IMEI without its last digit»: `imei` is never an alternative of a
    code that failed to be one.
  * A COMMERCIAL CODE NAMES A PRODUCT, NEVER A UNIT. `gtin` is never stored on a device.

DATA-DRIVEN. The printed names a QR or a typed text may carry («IMEI2: …», «EID …», «S/N: …») are a table
(`LABEL_HINTS`): adding one is a row, not a branch of code. The lengths are the international ones (`_digits`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import device_identity

IMEI, EID, MEID, IMEISV, GTIN, LABEL_CODE, SERIAL, UNKNOWN = (
    'imei', 'eid', 'meid', 'imeisv', 'gtin', 'label_code', 'serial', 'unknown')
KINDS = (IMEI, EID, MEID, IMEISV, GTIN, LABEL_CODE, SERIAL, UNKNOWN)
#: What a clerk may say a doubtful code is. Only these two are written on a device; `gtin` picks a product.
CHOICES = (IMEI, SERIAL, GTIN)
#: The identifier the screen is waiting for (`expected`). Only an IMEI slot makes fifteen Luhn digits a sure IMEI.
SLOTS = ('imei', 'imei2', 'serial')
IMEI_SLOTS = ('imei', 'imei2')

#: Longer than any identifier this screen takes: a URL, a sentence, a whole QR payload.
MAX_LENGTH = 64

_SEPARATED_DIGITS = re.compile(r'^[0-9][0-9 .\-/]*$')
_CODE = re.compile(r'^[A-Z0-9][A-Z0-9\-/._]*$')
_HEX14 = re.compile(r'^(?=.*[A-F])[0-9A-F]{14}$')

#: A printed name before the value, and what it says the value is. Order matters: «IMEI2» before «IMEI».
#: The separator (`:`, `#`, `=` or a space) is required: «SN» glued to a value may be the start of a serial.
LABEL_HINTS: tuple[tuple[str, str], ...] = (
    (r'IMEI\s*2', 'imei2'),
    (r'IMEI\s*1', 'imei'),
    (r'IMEI', 'imei'),
    (r'MEID', MEID),
    (r'EID', EID),
    (r'S/N|SN|SERIAL\s*(?:NO\.?|NUMBER)?|N[º°O]?\.?\s*DE\s*SERIE|SERIE', SERIAL),
    (r'UPC|EAN|GTIN', GTIN),
)
_HINT = re.compile(
    r'^(?P<name>' + '|'.join(f'(?:{pattern})' for pattern, _ in LABEL_HINTS) + r')\s*[:#=]\s*(?P<value>.+)$'
    r'|^(?P<name2>' + '|'.join(f'(?:{pattern})' for pattern, _ in LABEL_HINTS) + r')\s+(?P<value2>\S.*)$',
)

#: Another printed name inside a value: «S/N: X IMEI: Y» is two fields, not one serial.
_ANOTHER_NAME = re.compile(r'(?:IMEI|MEID|EID|UPC|EAN|GTIN|S/N)\s*\d?\s*[:#=]')

DETAIL = {
    IMEI: 'IMEI válido.',
    EID: 'Es el EID (identificador de la eSIM, 32 dígitos): no es un IMEI ni se registra en el equipo.',
    MEID: 'Parece un MEID: no es un IMEI. Escanea el IMEI (15 dígitos).',
    IMEISV: 'Parece un IMEISV (16 dígitos): no es el IMEI. Escanea el IMEI (15 dígitos).',
    GTIN: 'Es un código comercial (UPC/EAN): identifica el producto o la variante, nunca un equipo.',
    LABEL_CODE: 'Es una etiqueta interna de la tienda.',
    SERIAL: 'Número de serie.',
    UNKNOWN: 'No se reconoce este código como un identificador de equipo.',
}
PROBLEM_DETAIL = {
    'imei_or_serial': 'Tiene la forma de un IMEI (15 dígitos con su dígito de control), pero no se esperaba un IMEI: '
                      '¿es el IMEI o la serie?',
    'gtin_or_meid': 'Catorce dígitos con dígito de control GS1: parece un código comercial (GTIN-14). También podría '
                    'ser un MEID, que nunca es un IMEI. ¿Qué es?',
    'imei_check_digit': 'Tiene 15 dígitos pero no supera el dígito de control de un IMEI: vuelve a escanearlo. '
                        'Si es la serie, indícalo.',
    'placeholder': 'Eso no es un identificador. Si el equipo no lo tiene, deja el campo vacío.',
    'empty': 'No se leyó nada.',
    'not_a_code': 'No se reconoce este código como un identificador de equipo.',
}


class ScanError(Exception):
    """What the clerk said a code is cannot be: `field` names the slot, the message says why."""

    def __init__(self, message: str, *, field: str = ''):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Reading:
    raw: str
    value: str
    kind: str
    certain: bool
    alternatives: tuple[str, ...] = ()
    #: From a printed name: '', 'imei', 'imei2', 'serial', 'gtin', 'eid', 'meid'.
    hint: str = ''
    problem: str = ''
    detail: str = field(default='')

    def as_dict(self) -> dict:
        return {'value': self.value, 'kind': self.kind, 'certain': self.certain,
                'alternatives': list(self.alternatives), 'hint': self.hint, 'problem': self.problem,
                'detail': self.detail}


def gs1_check_digit(body: str) -> int:
    """The GS1 check digit of `body` (UPC-A, EAN-8, EAN-13 and GTIN-14 alike): weights 3,1,3… from the right."""
    total = sum(int(char) * (3 if position % 2 == 0 else 1) for position, char in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def _gs1_valid(digits: str) -> bool:
    return len(digits) in (8, 12, 13, 14) and gs1_check_digit(digits[:-1]) == int(digits[-1])


def _imei_valid(digits: str) -> bool:
    try:
        return device_identity.clean_imei(digits) == digits
    except device_identity.DeviceIdentityError:
        return False


def _reading(raw, value, kind, *, certain, alternatives=(), hint='', problem=''):
    detail = PROBLEM_DETAIL.get(problem) or DETAIL[kind]
    return Reading(raw=raw, value=value, kind=kind, certain=certain, alternatives=tuple(alternatives), hint=hint,
                   problem=problem, detail=detail)


def _digits(raw: str, digits: str, hint: str, expected: str = '') -> Reading:
    """
    A code of digits only, by its length and its check digit.

    Fifteen Luhn digits are a SURE IMEI only when the label says IMEI or the screen waits for an IMEI (review B3):
    one numeric serial in ten passes Luhn as well, so otherwise the clerk is asked.
    """
    n = len(digits)
    if n == 15:
        if _imei_valid(digits):
            if hint in (IMEI, 'imei2') or expected in IMEI_SLOTS:
                return _reading(raw, digits, IMEI, certain=True, hint=hint)
            return _reading(raw, digits, IMEI, certain=False, alternatives=(IMEI, SERIAL), hint=hint,
                            problem='imei_or_serial')
        return _reading(raw, digits, UNKNOWN, certain=False, alternatives=(SERIAL,), hint=hint,
                        problem='imei_check_digit')
    if n == 32:
        return _reading(raw, digits, EID, certain=True, hint=hint)
    if n == 16:
        return _reading(raw, digits, IMEISV, certain=False, alternatives=(SERIAL,), hint=hint)
    if n == 18:
        return _reading(raw, digits, MEID, certain=False, alternatives=(SERIAL,), hint=hint)
    if n == 14:
        # Review B4: digits with a GS1 digit are a GTIN-14 first; a MEID is only ever named, never an IMEI.
        if _gs1_valid(digits):
            return _reading(raw, digits, GTIN, certain=False, alternatives=(GTIN, SERIAL), hint=hint,
                            problem='gtin_or_meid')
        return _reading(raw, digits, MEID, certain=False, alternatives=(SERIAL,), hint=hint)
    if n in (12, 13) and _gs1_valid(digits):
        return _reading(raw, digits, GTIN, certain=True, alternatives=(SERIAL,), hint=hint)
    if n == 8 and _gs1_valid(digits):
        return _reading(raw, digits, GTIN, certain=False, alternatives=(GTIN, SERIAL), hint=hint)
    return _serial(raw, digits, hint, certain=False)


def _serial(raw: str, text: str, hint: str, *, certain: bool) -> Reading:
    try:
        serial = device_identity.clean_serial(text)
    except device_identity.DeviceIdentityError:
        placeholder = device_identity._is_placeholder(text)
        return _reading(raw, text, UNKNOWN, certain=False, hint=hint,
                        problem='placeholder' if placeholder else 'not_a_code')
    return _reading(raw, serial, SERIAL, certain=certain, alternatives=() if certain else (SERIAL,), hint=hint)


def _named(raw: str, name: str, value: str) -> Reading | None:
    """A code that carries its printed name: the name decides, the value is still checked by its own rule."""
    upper = name.upper()
    hint = next((kind for pattern, kind in LABEL_HINTS if re.fullmatch(pattern, upper)), '')
    if not hint:
        return None
    digits = re.sub(r'[\s.\-/]', '', value) if _SEPARATED_DIGITS.match(value) else value
    if hint in (IMEI, 'imei2'):
        if digits.isdigit() and len(digits) == 15:
            reading = _digits(raw, digits, hint, hint)
            return reading if reading.kind == IMEI else _reading(
                raw, digits, UNKNOWN, certain=False, hint=hint, problem='imei_check_digit')
        return _reading(raw, digits, UNKNOWN, certain=False, hint=hint, problem='not_a_code')
    # Review B6: a named EID or MEID is still held to its shape.
    if hint == EID:
        if digits.isdigit() and len(digits) == 32:
            return _reading(raw, digits, EID, certain=True, hint=hint)
        return _reading(raw, digits, UNKNOWN, certain=False, hint=hint, problem='not_a_code')
    if hint == MEID:
        if (digits.isdigit() and len(digits) in (14, 18)) or _HEX14.match(digits):
            return _reading(raw, digits, MEID, certain=True, hint=hint)
        return _reading(raw, digits, UNKNOWN, certain=False, hint=hint, problem='not_a_code')
    if hint == SERIAL:
        # Review B5: a serial is one token. Spaces or another printed name (a multi-field QR) are never glued into one.
        if re.search(r'[\s;,|]', value) or _ANOTHER_NAME.search(value):
            return _reading(raw, value, UNKNOWN, certain=False, hint=hint, problem='not_a_code')
        return _serial(raw, value, hint, certain=True)
    if hint == GTIN:
        if digits.isdigit() and _gs1_valid(digits):
            return _reading(raw, digits, GTIN, certain=True, hint=hint)
        return _reading(raw, digits, UNKNOWN, certain=False, hint=hint, problem='not_a_code')
    return None


def classify(code, expected: str = '') -> Reading:
    """
    What `code` is. Never raises: a code that is nothing known is `unknown`, with its `problem`.

    `expected`: the slot the screen waits for (`SLOTS`), or ''. It only makes fifteen Luhn digits a sure IMEI.
    """
    raw = str(code if code is not None else '')
    text = raw.strip()
    if not text:
        return _reading(raw, '', UNKNOWN, certain=False, problem='empty')
    if len(text) > MAX_LENGTH:
        return _reading(raw, '', UNKNOWN, certain=False, problem='not_a_code')
    upper = text.upper()

    match = _HINT.match(upper)
    if match:
        name = match.group('name') or match.group('name2')
        value = (match.group('value') or match.group('value2') or '').strip()
        named = _named(raw, name, value)
        if named is not None:
            return named

    if _SEPARATED_DIGITS.match(upper):
        return _digits(raw, re.sub(r'[\s.\-/]', '', upper), '', expected)
    from . import unit_codes

    # The shop's own label before a MEID: a 4-letter prefix of A–F and ten digits is also fourteen «hex» characters.
    if unit_codes.looks_like_label_code(upper):
        return _reading(raw, upper, LABEL_CODE, certain=True, alternatives=(SERIAL,))
    if _HEX14.match(upper):
        return _reading(raw, upper, MEID, certain=False, alternatives=(SERIAL,))
    if not _CODE.match(upper):
        return _reading(raw, '', UNKNOWN, certain=False, problem='not_a_code')
    has_letter = any(char.isalpha() for char in upper)
    has_digit = any(char.isdigit() for char in upper)
    return _serial(raw, upper, '', certain=has_letter and has_digit and len(upper) >= 5)


def as_identifier(code, choice: str) -> str:
    """
    The value to write when the clerk says what a doubtful code is (`imei` or `serial`), checked by the very rules
    the reception applies (`device_identity`). `gtin` is not written on a device: it is refused here.
    """
    if choice not in (IMEI, SERIAL):
        raise ScanError('Elige si el código es el IMEI o la serie del equipo.', field='choice')
    reading = classify(code)
    if choice == IMEI:
        if reading.kind != IMEI:
            raise ScanError(reading.detail if reading.kind != UNKNOWN or reading.problem else
                            'Eso no es un IMEI: son 15 dígitos con su dígito de control.', field='imei')
        return reading.value
    # A named IMEI, an EID or a sure commercial code is never a serial. Fifteen Luhn digits without a name may be one:
    # the clerk said so (review B3), and the reception records it (`serial_confirmed`).
    if reading.kind == EID or (reading.kind in (IMEI, GTIN) and reading.certain):
        raise ScanError(f'{reading.detail} No es la serie.', field='serial_number')
    text = reading.value or str(code or '').strip()
    try:
        serial = device_identity.clean_serial(text)
    except device_identity.DeviceIdentityError as exc:
        raise ScanError(str(exc), field='serial_number') from None
    if not serial:
        raise ScanError(PROBLEM_DETAIL['empty'], field='serial_number')
    return serial
