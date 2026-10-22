"""
INV-AI-01 — what any AI provider has to go through, whichever it is.

OUT: `redact` takes identifiers and contact data out of the text before it
leaves the server (IMEIs, serials, phones, e-mails) and keeps them here, to put
back into the answer. Names are not detected yet: a list that carries a
person's name is the reason the switch stays per company and per import.

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
_IMEI = re.compile(r'(?<!\d)\d{15}(?!\d)')
_SERIAL = re.compile(r'(?i)\b(NS|S/N|SN|serie|serial)(\s*[:#]?\s*)([A-Z0-9]{6,20})\b')
_PHONE = re.compile(r'(?<![\d/])(?:\+?51[\s-]?)?9\d{2}[\s-]?\d{3}[\s-]?\d{3}(?![\d/])')

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
    text = _IMEI.sub(lambda m: keep('IMEI', m.group(0)), text)
    text = _SERIAL.sub(lambda m: m.group(1) + m.group(2) + keep('SERIE', m.group(3)), text)
    text = _PHONE.sub(lambda m: keep('TEL', m.group(0)), text)
    return text, secrets


def _value(kind, value):
    if kind == 'text':
        return value.strip()[:_TEXT_MAX] if isinstance(value, str) and value.strip() else None
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
