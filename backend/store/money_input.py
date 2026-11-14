"""
B1-0 / B1-0b-1 — ONE reading for an amount a PERSON typed as text (owner's decision D-B1-1).

Before B1-0 several doors replaced every comma with a dot, or handed the text to `Decimal`, so «5,500» became 5.50
and «5.500» became 5.5000: a price or a cost a hundred or a thousand times lower, without a word. The policy:

  * «5500», «5500.00», «5500,00», «5,50» — one separator followed by 1 or 2 digits is the decimal one; with
    4 or more digits after it («1800,2000», «5.5000») it is refused: never decimals in silence;
  * «5,500» / «5.500» — one separator followed by exactly 3 digits is AMBIGUOUS (5500 or 5.50): refused
    (`ambiguous_amount`), never guessed;
  * «5,500.00», «5.500,00», «1.234.567» — the thousands separator only in exact groups of three, the last
    different separator is the decimal one; any other grouping is not an amount;
  * two amounts in one text are refused (`several_amounts`); a currency symbol, a letter, an exponent, NaN,
    Infinity or a sign are not an amount; spaces as thousands separator are refused naming both readings.

ONLY TEXT A PERSON TYPED goes through here. A value the system already holds as a number (a `Decimal`, an `int`,
an internal cost of four decimals, a spreadsheet NUMBER cell) is a number, not a text: callers keep reading it as
before. What is stored is never re-read.
"""
from __future__ import annotations

import re

#: An amount written as text: digits, at most one kind of decimal separator, thousands in groups of three.
_AMOUNT_TEXT = re.compile(r'[0-9.,]+')
#: Two amounts in one cell («1800/2000», «5400-5500», «4390 4500»): refused, never one of them picked.
_SEVERAL_AMOUNTS = re.compile(r'[0-9][0-9.,]*\s*(?:[-/;|]|\s)\s*[0-9]')
_GROUPED = re.compile(r'[0-9]{1,3}(?:X[0-9]{3})+')
#: «1 234,56» (space, no-break space or thin space between thousands): one amount, refused with its own reason.
_SPACE_GROUPED = re.compile(r'[0-9]{1,3}(?:[    ][0-9]{3})+(?:[.,][0-9]{1,2})?')
#: Above any real price or cost, below anything a text could hold.
MAX_AMOUNT_TEXT = 40


class AmountTextError(ValueError):
    """Why a typed text is not an amount: `code` is `invalid`, `ambiguous_amount` or `several_amounts`."""

    def __init__(self, detail: str, code: str = 'invalid'):
        super().__init__(detail)
        self.detail = detail
        self.code = code


def _capital(label: str) -> str:
    return f'{label[0].upper()}{label[1:]}' if label else label


def amount_text(text: str, label: str) -> str:
    """The plain decimal text («5500.00», «5.50», «1234567») of an amount typed by a person, or `AmountTextError`.

    `label` names the amount in a sentence, in lower case («el costo», «el precio comercial»).
    """
    def invalid(code='invalid', detail=None):
        return AmountTextError(detail or f'{_capital(label)} no es un importe válido.', code)

    said = str(text).strip()
    if len(said) > MAX_AMOUNT_TEXT:
        raise invalid()
    if _SPACE_GROUPED.fullmatch(said):
        whole = ''.join(ch for ch in said if ch.isdigit() or ch in '.,')
        parts = said.split()
        raise invalid(detail=f'«{said}» no es claro: puede ser un solo importe con espacios de miles ({whole}) o '
                             f'{len(parts)} importes distintos. Escribe {label} sólo con números, sin espacios.')
    if _SEVERAL_AMOUNTS.search(said):
        raise invalid('several_amounts', f'«{said}» tiene más de un importe: escribe sólo {label}.')
    if not _AMOUNT_TEXT.fullmatch(said) or not said[0].isdigit() or not said[-1].isdigit():
        raise invalid(detail=f'{_capital(label)} no es un importe válido: escribe sólo el número, '
                             'sin moneda ni signos.')
    dots, commas = said.count('.'), said.count(',')
    if dots and commas:
        decimal_sep = ',' if said.rfind(',') > said.rfind('.') else '.'
        thousands = '.' if decimal_sep == ',' else ','
        whole, _, decimals = said.rpartition(decimal_sep)
        if decimal_sep in whole or not 1 <= len(decimals) <= 2 or not _GROUPED.fullmatch(whole.replace(thousands, 'X')):
            raise invalid()
        return whole.replace(thousands, '') + '.' + decimals
    separator = '.' if dots else (',' if commas else '')
    if not separator:
        return said
    if said.count(separator) > 1:
        if not _GROUPED.fullmatch(said.replace(separator, 'X')):
            raise invalid()
        return said.replace(separator, '')
    head, _, tail = said.partition(separator)
    if len(tail) == 3 and len(head) <= 3 and not head.startswith('0'):
        raise invalid('ambiguous_amount',
                      f'«{said}» es ambiguo: puede ser {head}{tail} o {head}.{tail}. Escribe {label} sin separador '
                      f'de miles ({head}{tail}) o con dos decimales.')
    if len(tail) > 2:
        # Review of B1-0: «1800,2000» is two prices without a space, «4390,4500» too; never read as decimals.
        raise invalid(detail=f'«{said}» no es un importe válido: lleva a lo sumo dos decimales; si son dos '
                             f'importes, escribe sólo {label}.')
    return head + '.' + tail
