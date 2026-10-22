"""
INV-AI-01 (ADR docs/adr-inv-ai-structured.md) — what the AI may receive and
what it may answer, built here and only here.

The AI never receives the supplier's text. It receives, per candidate the
local recognizer already produced, a few fields whose VALUES are checked one
by one: closed vocabularies, bounded numbers, an opaque per-request ref. A
value that does not pass is not sent (its field goes to `ambiguous`): a field
being on the allowed list does not make its content safe. Catalogue variant
names (tenant data, untrusted) are reduced to vocabulary tokens before they
travel. The answer is validated against what was sent.

Never sent: text or fragments of it, names, contacts, IMEI, serials, battery,
costs, prices, quantities, supplier, company, branch, user.
"""
import re
import secrets
from typing import NamedTuple

MAX_ITEMS = 300
MAX_VARIANTS_PER_ITEM = 5
MAX_MODEL_WORDS = 6
SCHEMA_VERSION = 1

KINDS = ('device', 'accessory')
LINES = ('new', 'used', 'accessory')
SIMS = ('chip', 'esim')
CAPACITIES = (16, 32, 64, 128, 256, 512, 1024, 2048)
FAMILIES = (
    'iPhone', 'iPad', 'MacBook', 'iMac', 'Mac mini', 'Apple Watch', 'Galaxy', 'Pixel', 'Redmi', 'Xiaomi', 'Poco',
    'Motorola', 'Honor', 'AirPods', 'Apple Pencil', 'AirTag', 'EarPods', 'MagSafe', 'Cargador', 'Cable', 'Funda',
    'Adaptador', 'Teclado', 'Mouse', 'Protector', 'Correa', 'Power Bank',
)
MODEL_WORDS = ('Pro', 'Max', 'Plus', 'Mini', 'FE', 'Ultra', 'SE', 'Air', 'Lite', 'Edge', 'Neo', 'USB-C', 'Lightning')
REGIONS = ('LL/A', 'ZA/A', 'ZP/A', 'CH/A', 'J/A', 'E/A', 'B/A', 'KH/A', 'LZ/A', 'BE/A', 'X/A', 'HN/A', 'QN/A',
           'C/A', 'T/A', 'VC/A')
COLOURS = (
    'negro', 'blanco', 'azul', 'rojo', 'verde', 'amarillo', 'morado', 'rosado', 'rosa', 'dorado', 'plateado', 'plata',
    'gris', 'grafito', 'natural', 'titanio', 'medianoche', 'navy', 'lavanda', 'crema', 'menta', 'ultramarino',
    'titanio desierto', 'titanio natural', 'titanio negro', 'titanio blanco', 'titanio azul', 'azul medianoche',
    'azul alpino', 'azul sierra', 'azul pacífico', 'verde alpino', 'gris espacial', 'oro rosa', 'blanco estelar',
    'luz estelar', 'negro espacial', 'naranja cósmico', 'azul niebla',
)
_FOLD = {value.lower(): value for value in FAMILIES + MODEL_WORDS + REGIONS + COLOURS}
#: A model token that is not a vocabulary word: a 1–2 digit number («15») or a letter plus up to 3 digits («S25»).
_MODEL_CODE = re.compile(r'^(?:[0-9]{1,2}|[A-Z][0-9]{1,3})$')


class StructuredRequest(NamedTuple):
    """The ONLY thing a provider can be given. Built by `build_request`; `refs` never leave the server."""
    payload: dict
    refs: dict


def _pick(value, allowed):
    if not isinstance(value, str):
        return None
    return _FOLD.get(' '.join(value.split()).lower()) if _FOLD.get(' '.join(value.split()).lower()) in allowed else None


def _model_words(value):
    if not isinstance(value, list) or len(value) > MAX_MODEL_WORDS:
        return None
    words = []
    for word in value:
        if not isinstance(word, str):
            return None
        known = _FOLD.get(word.lower())
        if known in MODEL_WORDS:
            words.append(known)
        elif _MODEL_CODE.match(word):
            words.append(word)
        else:
            return None
    return words


def _int_in(value, allowed):
    return value if isinstance(value, int) and not isinstance(value, bool) and value in allowed else None


def clean_item(item: dict) -> dict:
    """One candidate as it may travel: each value checked; one that fails is left out and named in `ambiguous`."""
    out, ambiguous = {}, []
    checks = (
        ('kind', lambda v: v if v in KINDS else None),
        ('line', lambda v: v if v in LINES else None),
        ('family', lambda v: _pick(v, FAMILIES)),
        ('model_words', _model_words),
        ('capacity_gb', lambda v: _int_in(v, CAPACITIES)),
        ('ram_gb', lambda v: _int_in(v, range(1, 33))),
        ('sim', lambda v: v if v in SIMS else None),
        ('region', lambda v: _pick(v, REGIONS)),
        ('colour', lambda v: _pick(v, COLOURS)),
    )
    for field, check in checks:
        if field not in item or item[field] in (None, '', []):
            continue
        value = check(item[field])
        if value is None:
            ambiguous.append(field)
        else:
            out[field] = value
    for field in item.get('ambiguous') or ():
        if field in dict(checks) and field not in ambiguous:
            ambiguous.append(field)
    if ambiguous:
        out['ambiguous'] = sorted(ambiguous)
    return out


def clean_variant_name(name) -> list:
    """A catalogue variant name (tenant data, untrusted) as vocabulary tokens only; anything else dropped."""
    if not isinstance(name, str):
        return []
    tokens, words, i = [], re.split(r'\s+', name.strip())[:12], 0
    while i < len(words):
        pair = _FOLD.get(f'{words[i]} {words[i + 1]}'.lower()) if i + 1 < len(words) else None
        if pair:  # «Titanio desierto», «Mac mini»: the phrase, not its first word
            tokens.append(pair)
            i += 2
            continue
        raw = words[i]
        known = _FOLD.get(raw.lower())
        if known:
            tokens.append(known)
        elif _MODEL_CODE.match(raw) or re.fullmatch(r'[0-9]{2,4}(?:GB|TB)', raw, re.I):
            tokens.append(raw.upper() if raw[-2:].isalpha() else raw)
        i += 1
    return tokens


def build_request(items: list, variants: dict | None = None) -> StructuredRequest:
    """
    `items`: dicts from the local recognizer (any extra key is ignored, never forwarded).
    `variants`: {item index: [catalogue variant names]} the server already filtered for THIS company.
    """
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise ValueError(f'Entre 1 y {MAX_ITEMS} candidatos.')
    nonce = secrets.token_hex(3)
    refs, out = {}, []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError('Cada candidato es un objeto.')
        ref = f'c{index + 1}-{nonce}'
        refs[ref] = index
        entry = {'ref': ref, **clean_item(item)}
        names = (variants or {}).get(index) or []
        if names:
            entry['variants'] = [{'index': i, 'tokens': clean_variant_name(name)}
                                 for i, name in enumerate(names[:MAX_VARIANTS_PER_ITEM])]
        out.append(entry)
    return StructuredRequest({'schema_version': SCHEMA_VERSION, 'items': out}, refs)


class BadAnswer(Exception):
    """The answer is not the schema, or names what was not sent: nothing of it is used."""


def validate_answer(answer, request: StructuredRequest) -> list:
    """
    `[{index, model_words, variant_index, confidence}]` for the refs that were sent. A result for an unknown
    ref, a variant index that was not offered, or words outside the vocabulary are dropped; a non-schema
    answer raises `BadAnswer`. Nothing here can name an action, a price, a quantity, a company or a permission.
    """
    if not isinstance(answer, dict) or not isinstance(answer.get('results'), list):
        raise BadAnswer('La respuesta no es una lista de resultados.')
    sent = {item['ref']: item for item in request.payload['items']}
    proposals, seen = [], set()
    for result in answer['results'][:MAX_ITEMS]:
        if not isinstance(result, dict) or result.get('ref') not in sent or result['ref'] in seen:
            continue
        seen.add(result['ref'])
        item = sent[result['ref']]
        words = _model_words(result.get('model_words')) if result.get('model_words') is not None else None
        index = result.get('variant_index')
        offered = len(item.get('variants') or [])
        if not (index is None or (isinstance(index, int) and not isinstance(index, bool) and 0 <= index < offered)):
            index = None
        confidence = result.get('confidence') if result.get('confidence') in ('high', 'low') else 'low'
        proposals.append({'index': request.refs[result['ref']], 'model_words': words, 'variant_index': index,
                          'confidence': confidence})
    return proposals
