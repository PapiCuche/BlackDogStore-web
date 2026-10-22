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

_INVISIBLE = re.compile('[\u200b\u200c\u200d\u2060\ufeff\u00ad]')
_DASHES = re.compile('[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]')
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_EMAIL_SPOKEN = re.compile(
    r'(?i)[\w.+-]+\s*(?:\[at\]|\(at\)|\bat\b|\barroba\b)\s*[\w-]+'
    r'(?:\s*(?:\.|\[dot\]|\(dot\)|\bdot\b|\bpunto\b)\s*[\w-]+)+')
#: A run of digit groups joined by one separator each: space of any kind, newline, dot, dash or slash.
_DIGIT_RUN = re.compile(r'\d+(?:[\s.\-/]\d+)*')
_IMEI_WORD = re.compile(r'(?i)imei\s*\d?\s*[:#=]?\s*$')
#: How an IMEI is usually written in groups (TAC 8 = 2+6, serial 6, check 1).
_IMEI_GROUPS = {(2, 6, 6), (2, 6, 6, 1), (8, 6), (8, 6, 1), (2, 6, 7), (8, 7)}
_SERIAL = re.compile(
    r'(?i:\b(n[uú]mero de serie|nro\.? de serie|N/S|S/N|NS|SN|serie|serial))(\s*[:#=]?\s*)'
    r'([A-Za-z0-9]{3,20}(?:[ \-](?!(?:IMEI|NS|SN|DNI|RUC)\b)[A-Z0-9]{2,10}){0,3})')
_DOCUMENT = re.compile(
    r'(?i:\b(DNI|RUC|CE|carn[eé] de extranjer[ií]a))(\s*[:#=]?\s*)(\d{1,3}(?:[.\s]\d{3}){2}|\d{8,12})')
_BARE_RUC = re.compile(r'(?<!\d)(?:10|15|16|17|20)\d{9}(?!\d)')
_PHONE = re.compile(r'(?<!\d)(?:\+?51[\s.\-]?)?9(?:[\s.\-]?\d){8}(?!\d)')
_LANDLINE = re.compile(r'(?<!\d)01[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)')
#: A name follows the words that introduce one, whatever its case: «Cliente: juan pérez», «SR. DÍAZ».
_NAME = re.compile(
    r'(?i:\b(cliente|contacto|nombre|comprador|vendedor|atiende|llamar a|sr\.?|sra\.?|srta\.?)(?![\w]))'
    r'([ \t]*:?[ \t]*)([^\W\d_]+(?:[ \t]+[^\W\d_]+){0,2})')


def normalize(text: str) -> str:
    """One way of writing: compatibility forms folded (NFKC), invisible characters out, every dash a «-»."""
    import unicodedata

    return _DASHES.sub('-', _INVISIBLE.sub('', unicodedata.normalize('NFKC', text)))


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
    digits; consecutive groups that make 15 digits passing Luhn; the usual
    groupings of 14 or 15 digits (2+6+6, 8+6, …); or 14 to 16 digits right
    after the word «IMEI». A model number before it («iPhone 13») stays outside.
    """
    groups = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r'\d+', run)]
    spans, i = [], 0
    while i < len(groups):
        found_at = None
        digits = ''
        for j in range(i, len(groups)):
            digits += groups[j][2]
            if len(digits) > 16:
                break
            shape = tuple(len(group[2]) for group in groups[i:j + 1])
            single = j == i and 14 <= len(digits) <= 16
            keyword = 14 <= len(digits) <= 16 and i == 0 and _IMEI_WORD.search(before)
            if single or (len(digits) == 15 and _luhn_ok(digits)) or shape in _IMEI_GROUPS or keyword:
                found_at = j
        if found_at is not None:
            spans.append((groups[i][0], groups[found_at][1]))
            i = found_at + 1
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
    'region': 'text', 'condition': 'text', 'serial': 'serial', 'imei': 'imei', 'imei2': 'imei',
    'supplier_cost': 'cost', 'quantity': 'count', 'battery': 'percent',
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

    def serial(m):
        value = m.group(3)
        if len(re.sub(r'[^A-Za-z0-9]', '', value)) < 6:
            return m.group(0)
        return m.group(1) + m.group(2) + keep('SERIE', value)

    text = normalize(text)
    text = _EMAIL.sub(lambda m: keep('EMAIL', m.group(0)), text)
    text = _EMAIL_SPOKEN.sub(lambda m: keep('EMAIL', m.group(0)), text)
    text = _redact_imeis(text, keep)
    text = _SERIAL.sub(serial, text)
    text = _DOCUMENT.sub(lambda m: m.group(1) + m.group(2) + keep('DOC', m.group(3)), text)
    text = _BARE_RUC.sub(lambda m: keep('DOC', m.group(0)), text)
    text = _PHONE.sub(lambda m: keep('TEL', m.group(0)), text)
    text = _LANDLINE.sub(lambda m: keep('TEL', m.group(0)), text)
    text = _NAME.sub(lambda m: m.group(1) + m.group(2) + keep('NOMBRE', m.group(3)), text)
    return text, secrets


_COST = re.compile(r'^\d{1,7}(?:[.,]\d{1,2})?$')
_IMEI_VALUE = re.compile(r'^(?:\[IMEI_\d+\]|\d{15})$')
_SERIAL_VALUE = re.compile(r'^(?:\[SERIE_\d+\]|[A-Za-z0-9][A-Za-z0-9 \-]{2,39})$')
MAX_QUANTITY = 100_000


def _value(kind, value):
    if kind in ('text', 'cost', 'imei', 'serial'):
        # Data, never markup: no angle brackets, bounded.
        text = value.replace('<', '').replace('>', '').strip() if isinstance(value, str) else ''
        text = text[:_TEXT_MAX]
        if kind == 'cost':
            return text if _COST.match(text) else None
        if kind == 'imei':
            return text if _IMEI_VALUE.match(text) else None
        if kind == 'serial':
            return text if _SERIAL_VALUE.match(text) else None
        return text or None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if kind == 'count':
        return value if 0 <= value <= MAX_QUANTITY else None
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


#: Which placeholders go back into which field. Anything else (a name, a phone, an
#: e-mail, a document) is never put back: a field that carries one is dropped.
_RESTORABLE = {'imei': 'IMEI', 'imei2': 'IMEI', 'serial': 'SERIE'}
_PLACEHOLDER = re.compile(r'\[([A-Z]+)_\d+\]')


def restore(rows: list[dict], secrets: dict) -> list[dict]:
    """Put identifiers back, here on the server, only where they belong; drop a field holding any other."""
    restored = []
    for row in rows:
        out = {}
        for key, value in row.items():
            if isinstance(value, str) and _PLACEHOLDER.search(value):
                kinds = {m.group(1) for m in _PLACEHOLDER.finditer(value)}
                if kinds != {_RESTORABLE.get(key)} or any(m.group(0) not in secrets
                                                          for m in _PLACEHOLDER.finditer(value)):
                    continue
                value = _PLACEHOLDER.sub(lambda m: secrets[m.group(0)], value)
                if key in ('imei', 'imei2'):
                    value = re.sub(r'\D', '', value)
                    if len(value) != 15:
                        continue
            out[key] = value
        restored.append(out)
    return restored
