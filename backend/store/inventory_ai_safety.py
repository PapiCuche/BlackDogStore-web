"""
INV-AI-01 — what any AI provider has to go through, whichever it is.

OUT: `redact` takes identifiers and contact data out of the text before it
leaves the server and keeps them here, to put back into the answer: IMEIs
however they are written (in one piece, with spaces, dots or dashes when they
pass Luhn or follow «IMEI»), serials after the words that introduce them,
DNI/RUC/CE, phones, e-mails and names after «cliente», «contacto», «Sr.»…
A name written with no such word is not detected: redaction lowers the risk,
it does not make sending a customer's data acceptable.

IN: `clean_candidates` keeps only known fields of known types from the answer.
The answer is data, never an instruction: a field it invents is dropped, an
answer that is not the schema is refused, and no field can name an action,
a price to sell at, a company or a permission.
"""
import re

MAX_CHARS = 60_000
MAX_CANDIDATES = 300
TIMEOUT_SECONDS = 15
_TEXT_MAX = 120

_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
#: A run of digit groups joined by single spaces, dots or dashes («13 35 693803 564380 9»).
_DIGIT_RUN = re.compile(r'\d+(?:[ .\-]\d+)*')
_IMEI_WORD = re.compile(r'(?i)imei\s*\d?\s*[:#=]?\s*$')
_SERIAL = re.compile(
    r'(?i)\b(n[uú]mero de serie|nro\.? de serie|N/S|S/N|NS|SN|serie|serial)(\s*[:#=]?\s*)([A-Z0-9]{6,20})\b')
_DOCUMENT = re.compile(r'(?i)\b(DNI|RUC|CE|carn[eé] de extranjer[ií]a)(\s*[:#=]?\s*)(\d{8,12})\b')
_PHONE = re.compile(r'(?<![\d/])(?:\+?51[\s-]?)?9\d{2}[\s-]?\d{3}[\s-]?\d{3}(?![\d/])')
#: A name follows the words that introduce one: «Cliente: Juan Pérez», «Sr. Díaz», «llamar a Rosa».
_NAME = re.compile(
    r'(?i:\b(cliente|contacto|nombre|comprador|vendedor|atiende|llamar a|sr\.?|sra\.?|srta\.?))(\s*:?\s*)'
    r'([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,2})')


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _imei_spans(run: str, before: str) -> list:
    """
    Where, inside one run of digit groups, IMEIs are: one group of 14 to 16
    digits, consecutive groups that make 15 digits passing Luhn, or 14 to 16
    digits right after the word «IMEI». A model number before it («iPhone 13»)
    stays outside.
    """
    groups = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r'\d+', run)]
    spans, i = [], 0
    while i < len(groups):
        found = None
        digits = ''
        for j in range(i, len(groups)):
            digits += groups[j][2]
            if len(digits) > 16:
                break
            single = j == i and 14 <= len(digits) <= 16
            keyword = 14 <= len(digits) <= 16 and i == 0 and _IMEI_WORD.search(before)
            if single or (len(digits) == 15 and _luhn_ok(digits)) or keyword:
                found = (groups[i][0], groups[j][1])
        if found:
            spans.append(found)
            i = next(k for k, group in enumerate(groups) if group[1] == found[1]) + 1
        else:
            i += 1
    return spans


def _redact_imeis(text: str, keep) -> str:
    out, last = [], 0
    for match in _DIGIT_RUN.finditer(text):
        before = text[max(0, match.start() - 12):match.start()]
        for start, end in _imei_spans(match.group(0), before):
            out.append(text[last:match.start() + start])
            out.append(keep('IMEI', match.group(0)[start:end]))
            last = match.start() + end
    out.append(text[last:])
    return ''.join(out)


#: field -> kind. Nothing else survives.
_FIELDS = {
    'brand': 'text', 'model': 'text', 'capacity': 'text', 'ram': 'text', 'color': 'text', 'sim': 'text',
    'region': 'text', 'condition': 'text', 'serial': 'text', 'imei': 'text', 'imei2': 'text',
    'supplier_cost': 'text', 'quantity': 'count', 'battery': 'percent',
}


class BadAnswer(Exception):
    """The provider's answer is not the schema: nothing of it is used."""


def redact(text: str) -> tuple[str, dict]:
    """`(text without identifiers, {placeholder: original})`. The originals never leave the server."""
    secrets, counters = {}, {}

    def keep(kind, value):
        counters[kind] = counters.get(kind, 0) + 1
        placeholder = f'[{kind}_{counters[kind]}]'
        secrets[placeholder] = value
        return placeholder

    text = _EMAIL.sub(lambda m: keep('EMAIL', m.group(0)), text)
    text = _redact_imeis(text, keep)
    text = _SERIAL.sub(lambda m: m.group(1) + m.group(2) + keep('SERIE', m.group(3)), text)
    text = _DOCUMENT.sub(lambda m: m.group(1) + m.group(2) + keep('DOC', m.group(3)), text)
    text = _PHONE.sub(lambda m: keep('TEL', m.group(0)), text)
    text = _NAME.sub(lambda m: m.group(1) + m.group(2) + keep('NOMBRE', m.group(3)), text)
    return text, secrets


def _value(kind, value):
    if kind == 'text':
        # Data, never markup: no angle brackets, bounded.
        text = value.replace('<', '').replace('>', '').strip() if isinstance(value, str) else ''
        return text[:_TEXT_MAX] or None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if kind == 'count':
        return value if value >= 0 else None
    return value if 0 <= value <= 100 else None


def clean_candidates(answer) -> list[dict]:
    """The candidates of `answer`, known fields only, at most `MAX_CANDIDATES`. `BadAnswer` if not the schema."""
    if not isinstance(answer, dict) or not isinstance(answer.get('candidates'), list):
        raise BadAnswer('El proveedor de IA no devolvió una lista de candidatos.')
    rows = []
    for raw in answer['candidates'][:MAX_CANDIDATES]:
        if not isinstance(raw, dict):
            raise BadAnswer('Un candidato no tiene la forma esperada.')
        row = {}
        for field, kind in _FIELDS.items():
            if field in raw:
                value = _value(kind, raw[field])
                if value is not None:
                    row[field] = value
        rows.append(row)
    return rows


def restore(rows: list[dict], secrets: dict) -> list[dict]:
    """Put the identifiers back, here on the server, into the text fields that name their placeholder."""
    if not secrets:
        return rows
    pattern = re.compile('|'.join(re.escape(key) for key in secrets))
    return [{key: pattern.sub(lambda m: secrets[m.group(0)], value) if isinstance(value, str) else value
             for key, value in row.items()} for row in rows]
