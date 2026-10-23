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
#: Model numbers, a CLOSED list (review: free codes like «X351» could carry digits out): 1–20 («iPhone 15»),
#: Galaxy S20–S30 and A00–A59. Nothing else numeric travels except capacity and RAM.
MODEL_CODES = frozenset([str(n) for n in range(1, 21)] + [f'S{n}' for n in range(20, 31)]
                        + [f'A{n:02d}' for n in range(0, 60)])
_REF = re.compile(r'c[0-9]{1,3}-[0-9a-f]{6}')
_SEAL = object()


class StructuredRequest(NamedTuple):
    """
    The ONLY thing a provider can be given. Built and sealed by `build_request`. The seal says where it came
    from, not that it is safe: `propose` re-validates every value (`payload_is_clean`), which is the control.
    `refs` never leave the server.
    """
    payload: dict
    refs: dict
    seal: object = None


def is_sealed(request) -> bool:
    return isinstance(request, StructuredRequest) and request.seal is _SEAL


def _pick(value, allowed):
    if not isinstance(value, str):
        return None
    return _FOLD.get(' '.join(value.split()).lower()) if _FOLD.get(' '.join(value.split()).lower()) in allowed else None


def _model_words(value):
    if not isinstance(value, list) or len(value) > MAX_MODEL_WORDS:
        return None
    words, numeric = [], 0
    for position, word in enumerate(value):
        if not isinstance(word, str):
            return None
        known = _FOLD.get(word.lower())
        if known in MODEL_WORDS:
            words.append(known)
        elif word in MODEL_CODES and position == 0 and not numeric:
            # Third review: ONE model number, first — a generation, never digits spelled out over many tokens.
            numeric += 1
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
    named = item.get('ambiguous')
    for field in named if isinstance(named, list) else ():
        if isinstance(field, str) and field in dict(checks) and field not in ambiguous:
            ambiguous.append(field)
    if ambiguous:
        out['ambiguous'] = sorted(ambiguous)
    return out


def item_from_preview(candidate: dict) -> dict:
    """
    A candidate of the local recognizer, AFTER the #142 server cleaner (`text_preview_match.clean`), as a
    `clean_item` input. It reads only the cleaner's fields (kind, line, model, capacity, colour, identifier)
    and invents nothing: the model is split into a family and its words by the closed vocabularies; one word
    outside them (a quantity like «x3», a name) makes the words ambiguous, an unknown first word makes the
    whole model ambiguous, and a model the cleaner flagged as holding an identifier is not read at all. Every
    value it gives is already vocabulary; `build_request` and `payload_is_clean` check them again.
    """
    if not isinstance(candidate, dict):
        raise ValueError('Cada candidato es un objeto.')
    item, ambiguous = {}, []
    for field in ('kind', 'line'):
        if isinstance(candidate.get(field), str):
            item[field] = candidate[field]
    capacity = candidate.get('capacity')
    if capacity is not None:
        if _int_in(capacity, CAPACITIES) is None:
            ambiguous.append('capacity_gb')
        else:
            item['capacity_gb'] = capacity
    colour = candidate.get('colour')
    if isinstance(colour, str) and colour.strip():
        if _pick(colour, COLOURS) is None:
            ambiguous.append('colour')
        else:
            item['colour'] = _pick(colour, COLOURS)
    model = candidate.get('model')
    tokens = model.split() if isinstance(model, str) else []
    if candidate.get('identifier') is not False and tokens:
        # Flagged by the cleaner, or not said: the model is not read at all.
        ambiguous += ['family', 'model_words']
    elif tokens:
        two = _pick(' '.join(tokens[:2]), FAMILIES) if len(tokens) > 1 else None
        family, rest = (two, tokens[2:]) if two else (_pick(tokens[0], FAMILIES), tokens[1:])
        if family is None:
            ambiguous += ['family', 'model_words']
        else:
            item['family'] = family
            words = _model_words(rest)
            if words is None:
                ambiguous.append('model_words')
            elif words:
                item['model_words'] = words
    if ambiguous:
        item['ambiguous'] = sorted(ambiguous)
    return item


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
        elif raw in MODEL_CODES and tokens and tokens[-1] in FAMILIES \
                and words[i - 1].lower() == tokens[-1].split()[-1].lower() \
                and not any(t in MODEL_CODES for t in tokens):
            # ONE model number, right after its family («iPhone 16»), never a stray number of the name.
            tokens.append(raw)
        elif _SIZE.fullmatch(raw.upper()):
            tokens.append(raw.upper())
        i += 1
    return tokens


def build_request(company, items: list, variant_ids: dict | None = None) -> StructuredRequest:
    """
    `items`: dicts from the local recognizer (any extra key is ignored, never forwarded).
    `variant_ids`: {item index: [product ids]} — looked up HERE, in `company` only; another company's id is
    simply not there (review: the caller can no longer hand over names of any origin).
    """
    from .models import Product

    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise ValueError(f'Entre 1 y {MAX_ITEMS} candidatos.')
    # Any other shape is simply no candidates (review: `{0: None}` or a list used to crash).
    variant_ids = {index: list(ids)[:MAX_VARIANTS_PER_ITEM] for index, ids in variant_ids.items()
                   if isinstance(ids, (list, tuple))} if isinstance(variant_ids, dict) else {}
    wanted = {pk for ids in variant_ids.values() for pk in ids if isinstance(pk, int) and not isinstance(pk, bool)}
    names = dict(Product.objects.filter(company=company, pk__in=wanted).values_list('pk', 'name')) if wanted else {}
    nonce = secrets.token_hex(3)
    refs, out = {}, []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError('Cada candidato es un objeto.')
        ref = f'c{index + 1}-{nonce}'
        refs[ref] = index
        entry = {'ref': ref, **clean_item(item)}
        ids = [pk for pk in variant_ids.get(index, [])
               if isinstance(pk, int) and not isinstance(pk, bool) and pk in names]
        if ids:
            entry['variants'] = [{'index': i, 'tokens': clean_variant_name(names[pk])} for i, pk in enumerate(ids)]
        out.append(entry)
    return StructuredRequest({'schema_version': SCHEMA_VERSION, 'items': out}, refs, _SEAL)


_SIZE = re.compile(r'(?:16|32|64|128|256|512)GB|[12]TB')


def _variant_tokens_ok(tokens) -> bool:
    """What `clean_variant_name` can give: vocabulary, sizes, and ONE model code right after a family."""
    if not isinstance(tokens, list) or len(tokens) > 12 or not all(isinstance(t, str) for t in tokens):
        return False
    vocabulary = set(FAMILIES + MODEL_WORDS + REGIONS + COLOURS)
    codes = [i for i, t in enumerate(tokens) if t in MODEL_CODES]
    if len(codes) > 1 or any(i == 0 or tokens[i - 1] not in FAMILIES for i in codes):
        return False
    return all(t in vocabulary or t in MODEL_CODES or _SIZE.fullmatch(t) for t in tokens)


_ITEM_KEYS = {'ref', 'kind', 'line', 'family', 'model_words', 'capacity_gb', 'ram_gb', 'sim', 'region', 'colour',
              'ambiguous', 'variants'}


def payload_is_clean(payload) -> bool:
    """
    The whole payload, checked again value by value — what `propose` asks before anything leaves: the known
    keys only, each value one `clean_item` keeps unchanged, refs of their shape, variant tokens of the
    vocabulary. A forged or tampered request fails here.
    """
    if not isinstance(payload, dict) or set(payload) != {'schema_version', 'items'} \
            or payload['schema_version'] != SCHEMA_VERSION or not isinstance(payload['items'], list) \
            or not 0 < len(payload['items']) <= MAX_ITEMS:
        return False
    for item in payload['items']:
        if not isinstance(item, dict) or not set(item) <= _ITEM_KEYS or not isinstance(item.get('ref'), str) \
                or not _REF.fullmatch(item['ref']):
            return False
        fields = {k: v for k, v in item.items() if k not in ('ref', 'variants')}
        if clean_item({k: v for k, v in fields.items() if k != 'ambiguous'} | {'ambiguous': fields.get('ambiguous')}) \
                != fields:
            return False
        if 'variants' not in item:
            continue
        variants = item['variants']
        if not isinstance(variants, list) or not 0 < len(variants) <= MAX_VARIANTS_PER_ITEM:
            return False
        for position, variant in enumerate(variants):
            if not isinstance(variant, dict) or set(variant) != {'index', 'tokens'} \
                    or type(variant['index']) is not int or variant['index'] != position \
                    or not _variant_tokens_ok(variant['tokens']):
                return False
    return True


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
        if not isinstance(result, dict) or not isinstance(result.get('ref'), str) \
                or result['ref'] not in sent or result['ref'] in seen:
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
