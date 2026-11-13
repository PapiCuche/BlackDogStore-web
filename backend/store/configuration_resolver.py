"""
CATALOG-COMMERCIAL-01 · A2 — what a supplier's description IS in THIS company's catalogue
(DEC-CATALOG-ALIAS-01; owner's decisions P3/P5, P6, P7, P8 and P9 of 2026-11-13).

    READ-ONLY. THE DATA DECIDES, NEVER A WORD LIST IN THE CODE.

A description arrives as fields («model_text», «capacity_text», «colour_text», «sim_text», «region_text»,
«line»), the way a supplier wrote them. The resolver answers, per colour, a CONFIGURATION with its status
(ADR §8.3) and everything a person needs to decide: the options read and how (label, folded capacity,
company alias), what is missing, the doubts with their possible readings, the variant that already is
that configuration, and the variants that only fit in part (information, never chosen).

Statuses, in precedence order (the first that applies wins):

    modelo_desconocido  the model is nobody the company has (no model, alias or word rewrite finds it)
    ambigua             two or more valid readings (a compound colour, two models for one name…)
    opcion_desconocida  a text no option or alias of the company is
    fuera_de_paleta     a real option of the company the model's palette does not admit
    incompleta          a role of the palette was not said; nothing is ever assumed
    variante_existente  every role said, inside the palette, and a variant IS it
    combinacion_valida_sin_variante   …and no variant is it yet (the A1b-2 «Crear variante» may make it)
    sin_paleta          the model has no palette and no variant is exactly that: nothing can be validated

R-COL — reading the colour text (owner's P3/P5):
  1. the longest phrase among the labels of the model's PALETTE colours;
  2. then the labels of the company's other colours (`fuera_de_paleta`);
  3. then the company's colour aliases;
  4. a reading that leaves an unknown word glued to a colour, or two VALID readings of the same words,
     is ONE `ambigua` configuration with its readings — a compound colour is never split in silence;
     a phrase that is ONE known colour is never read as two.
  Separators («,», «/», «+», «;», «&», «|» and the conjunctions of a list) always split.

What it never does: write a row, create a model, an option, a variant, an alias, an offer or stock;
choose a variant that fits in part; turn an amount into a price; infer a quantity; look at another
company. The palette and the variants are read once per model and request (`Vocabulary`).
"""
from __future__ import annotations

import re
import unicodedata

from .models import (
    CatalogAlias, ModelOption, Product, ProductModel, ProductVariantOption, VariantAttribute, VariantOption,
)

STATUS_UNKNOWN_MODEL = 'modelo_desconocido'
STATUS_AMBIGUOUS = 'ambigua'
STATUS_UNKNOWN_OPTION = 'opcion_desconocida'
STATUS_OUTSIDE = 'fuera_de_paleta'
STATUS_INCOMPLETE = 'incompleta'
STATUS_EXISTING = 'variante_existente'
STATUS_VALID = 'combinacion_valida_sin_variante'
STATUS_NO_PALETTE = 'sin_paleta'
STATUSES = (STATUS_UNKNOWN_MODEL, STATUS_AMBIGUOUS, STATUS_UNKNOWN_OPTION, STATUS_OUTSIDE, STATUS_INCOMPLETE,
            STATUS_EXISTING, STATUS_VALID, STATUS_NO_PALETTE)
COMPLETE = (STATUS_EXISTING, STATUS_VALID)

CAPACITY = VariantAttribute.ROLE_CAPACITY
COLOUR = VariantAttribute.ROLE_COLOR
SIM = VariantAttribute.ROLE_SIM
REGION = VariantAttribute.ROLE_REGION
OTHER = VariantAttribute.ROLE_OTHER
SINGLE_ROLES = (CAPACITY, SIM, REGION)

#: What a person can do with a doubt (P7). The resolver never does any of them.
ACTIONS_UNKNOWN = ['add_to_palette', 'alias_of', 'keep_pending']
ACTIONS_OUTSIDE = ['add_to_palette', 'keep_pending']
ACTIONS_MODEL = ['alias_of', 'keep_pending']

#: Colours one description may name; a longer list is a row error, never a long computation.
MAX_COLOURS = 12
#: Variants that only fit in part, shown as information (never chosen).
MAX_CANDIDATES = 5
#: Options stripped from the end of a model name before it is given up (D-A2-16).
MAX_NAME_STRIPS = 6

#: A list's grammar, not a vocabulary: punctuation and conjunctions always split a colour text.
SEPARATOR_WORDS = frozenset({'y', 'e', 'and'})
_TOKEN = re.compile(r'[^\W_]+|[^\w\s]')
_GLUED = re.compile(r'^([^\W\d_]+)(\d+)$')
_DIGITS = re.compile(r'^\d+$')
_UNITS = ('gb', 'tb')


def fold(text) -> str:
    """Lower case, accents folded (NFKD), whitespace collapsed — the same rule as model names."""
    text = unicodedata.normalize('NFKD', str(text or ''))
    return ' '.join(''.join(ch for ch in text if not unicodedata.combining(ch)).lower().split())


def tokens(text) -> list[tuple[str, str, int, int]]:
    """(original, folded, start, end) of each word or punctuation mark of `text`, as written (NFC)."""
    original = unicodedata.normalize('NFC', str(text or ''))
    out = []
    for match in _TOKEN.finditer(original):
        folded = fold(match.group())
        if folded:
            out.append((match.group(), folded, match.start(), match.end()))
    return out


def phrase_key(text) -> str:
    """The key a label, an alias and a supplier's text share: folded tokens joined by one space."""
    return ' '.join(token[1] for token in tokens(text))


def compact(key: str) -> str:
    return key.replace(' ', '')


def _is_boundary(folded: str) -> bool:
    return folded in SEPARATOR_WORDS or not any(ch.isalnum() for ch in folded)


class Vocabulary:
    """
    One company's catalogue as the resolver reads it, loaded ONCE per request or file pass: the
    attributes by role, every option by its folded label, the aliases, the model names; the palette and
    the variants of a model are read the first time a description names it. Never shared across
    requests: a palette or an alias saved meanwhile is seen by the next one.
    """

    def __init__(self, company):
        self.company = company
        attributes = list(VariantAttribute.objects.filter(company=company).order_by('sort_order', 'name', 'pk'))
        self.attributes = {a.pk: a for a in attributes}
        self.role_of_attribute = self.roles_of(attributes)
        self.options = {o.pk: o for o in VariantOption.objects.filter(company=company).order_by('pk')}
        self.label_index: dict[str, list] = {}
        self.role_index: dict[str, dict[str, list]] = {}
        self.capacity_index: dict[str, list] = {}
        for option in self.options.values():
            key = phrase_key(option.label)
            role = self.role(option)
            self.label_index.setdefault(key, []).append(option)
            self.role_index.setdefault(role, {}).setdefault(key, []).append(option)
            if role == CAPACITY:
                self.capacity_index.setdefault(compact(key), []).append(option)
        self.option_aliases: dict[str, CatalogAlias] = {}
        self.model_aliases: dict[str, CatalogAlias] = {}
        self.model_words: dict[str, CatalogAlias] = {}
        for alias in CatalogAlias.objects.filter(company=company).order_by('pk'):
            {CatalogAlias.SCOPE_OPTION: self.option_aliases, CatalogAlias.SCOPE_MODEL: self.model_aliases,
             CatalogAlias.SCOPE_MODEL_WORD: self.model_words}[alias.scope].setdefault(alias.alias_key, alias)
        # Models by the name's fold (DEC-CATALOG-NAME-01, as `find_model`: decided by folding the NAME, first
        # by pk if a legacy collision exists) and by the token key the word rewrite produces.
        self.models = {}
        self.models_by_name: dict[str, ProductModel] = {}
        self.models_by_phrase: dict[str, ProductModel] = {}
        for model in ProductModel.objects.filter(company=company).only('pk', 'name', 'company_id').order_by('pk'):
            self.models[model.pk] = model
            self.models_by_name.setdefault(fold(model.name), model)
            self.models_by_phrase.setdefault(phrase_key(model.name), model)
        self._palettes: dict[int, dict] = {}
        self._variants: dict[tuple, list] = {}

    @staticmethod
    def roles_of(attributes) -> dict[int, str]:
        """{attribute id: role}. A company that assigned no colour role yet: its colour attribute by its KIND."""
        roles = {a.pk: a.role for a in attributes}
        if not any(a.role == COLOUR for a in attributes):
            for attribute in attributes:
                if attribute.role == OTHER and attribute.kind == VariantAttribute.KIND_COLOR:
                    roles[attribute.pk] = COLOUR
        return roles

    @staticmethod
    def role_for(attribute) -> str:
        """The role of one attribute, without reading its siblings (display and the alias text rules)."""
        if attribute.role != OTHER:
            return attribute.role
        return COLOUR if attribute.kind == VariantAttribute.KIND_COLOR else OTHER

    # -- options ----------------------------------------------------------------
    def role(self, option) -> str:
        return self.role_of_attribute.get(option.attribute_id, OTHER)

    def option_alias(self, key):
        alias = self.option_aliases.get(key)
        if alias is not None and alias.option_id in self.options:
            return alias
        return None

    def lookup_any(self, text) -> list[tuple]:
        """Every option `text` may be, whatever its role: (option, via, alias). For readers without roles."""
        key = phrase_key(text)
        if not key:
            return []
        found = [(o, 'label', None) for o in self.label_index.get(key, [])]
        if not found:
            found = [(o, 'capacity', None) for o in self.capacity_index.get(compact(key), [])]
        if not found:
            alias = self.option_alias(key)
            if alias is not None:
                found = [(self.options[alias.option_id], 'alias', alias)]
        return found

    # -- models -----------------------------------------------------------------
    def palette(self, model_id) -> dict[int, set[int]]:
        if model_id not in self._palettes:
            palette: dict[int, set[int]] = {}
            for attribute_id, option_id in (ModelOption.objects.filter(model_id=model_id, company=self.company)
                                            .values_list('option__attribute_id', 'option_id')):
                palette.setdefault(attribute_id, set()).add(option_id)
            self._palettes[model_id] = palette
        return self._palettes[model_id]

    def variants(self, model_id, line) -> list[dict]:
        """The variants of a model in a line, with their option ids. Two queries, once per (model, line)."""
        slot = (model_id, line)
        if slot not in self._variants:
            products = list(Product.objects.filter(company=self.company, product_model_id=model_id,
                                                   condition_line=line)
                            .only('pk', 'name', 'variant_key', 'draft_origin', 'sale_enabled_at', 'is_active')
                            .order_by('name', 'pk'))
            links: dict[int, set[int]] = {}
            for product_id, option_id in (ProductVariantOption.objects
                                          .filter(company=self.company, product__in=[p.pk for p in products])
                                          .values_list('product_id', 'option_id')):
                links.setdefault(product_id, set()).add(option_id)
            self._variants[slot] = [{'product': p, 'option_ids': links.get(p.pk, set())} for p in products]
        return self._variants[slot]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def _unglue(folded_tokens: list[str]) -> list[str]:
    """«fon9» → «fon», «9»: a letters-then-digits word written without its space."""
    out = []
    for token in folded_tokens:
        glued = _GLUED.match(token)
        out.extend(glued.groups() if glued else [token])
    return out


def _model_candidates(vocabulary, text) -> tuple[dict, str]:
    """{model: via} for `text` WITHOUT stripping options; ('exact' first: an exact name is never re-read)."""
    exact = vocabulary.models_by_name.get(fold(text))
    if exact is not None:
        return {exact: 'exact'}, 'exact'
    key = phrase_key(text)
    found: dict = {}
    words = key.split(' ') if key else []
    spaced = vocabulary.models_by_phrase.get(key) or vocabulary.models_by_phrase.get(' '.join(_unglue(words)))
    if spaced is not None:
        return {spaced: 'spacing'}, 'exact'
    alias = vocabulary.model_aliases.get(key)
    if alias is not None and alias.model_id in vocabulary.models:
        found[vocabulary.models[alias.model_id]] = 'alias'
    for candidate in (words, _unglue(words)):
        rewritten = [vocabulary.model_words[w].replacement if w in vocabulary.model_words else w for w in candidate]
        if rewritten != candidate:
            model = vocabulary.models_by_phrase.get(' '.join(rewritten))
            if model is not None:
                found.setdefault(model, 'model_word')
    if len(found) == 1:
        via = next(iter(found.values()))
        return found, via
    return found, 'ambiguous' if found else 'unknown'


def _option_phrase(vocabulary, folded: tuple) -> tuple | None:
    """(option, via, alias) when the folded words are ONE option of the company (any role), else None."""
    found = vocabulary.lookup_any(' '.join(folded))
    distinct = {option.pk for option, _via, _alias in found}
    return found[0] if len(distinct) == 1 else None


def resolve_model(vocabulary, text) -> dict:
    """
    The model `text` names: `exact` (the folded name, or the same words spaced alike), `alias` (a whole-name
    alias), `model_word` (word aliases rewrite it into a name), `name_with_options` (options written inside
    the name were stripped from its end — to REVIEW, never a new model), `ambiguous` (two models), `unknown`.
    """
    name = ' '.join(str(text or '').split())
    out = {'text': name, 'status': 'unknown', 'via': None, 'model_id': None, 'model_name': None,
           'candidates': [], 'extracted': []}
    if not name:
        return out
    found, via = _model_candidates(vocabulary, name)
    if len(found) > 1:
        out.update(status='ambiguous', candidates=[{'id': m.pk, 'name': m.name} for m in sorted(found, key=lambda m: m.pk)])
        return out
    if len(found) == 1:
        model = next(iter(found))
        out.update(status=via, via=found[model], model_id=model.pk, model_name=model.name)
        return out
    # D-A2-16: options written at the END of the name («… (eSIM LLA)»). Only text that IS an option of the
    # company is stripped; an unknown word is never dropped (it could be the difference between two models).
    words = tokens(name)
    extracted = []
    for _ in range(MAX_NAME_STRIPS + 1):
        while words and _is_boundary(words[-1][1]):
            words.pop()
        if not words:
            break
        prefix = name[:words[-1][3]]
        if extracted:
            found, via = _model_candidates(vocabulary, prefix)
            if len(found) == 1:
                model = next(iter(found))
                out.update(status='name_with_options', via=found[model], model_id=model.pk, model_name=model.name,
                           extracted=list(reversed(extracted)))
                return out
            if len(found) > 1:
                out.update(status='ambiguous',
                           candidates=[{'id': m.pk, 'name': m.name} for m in sorted(found, key=lambda m: m.pk)])
                return out
        stripped = None
        for size in (3, 2, 1):
            if len(words) <= size:
                continue
            phrase = tuple(w[1] for w in words[-size:])
            hit = _option_phrase(vocabulary, phrase)
            if hit is not None:
                option, via_option, alias = hit
                stripped = size
                extracted.append({'role': vocabulary.role(option), 'text': name[words[-size][2]:words[-1][3]],
                                  'option_id': option.pk, 'label': option.label, 'via': via_option,
                                  'alias_id': alias.pk if alias else None})
                break
        if stripped is None:
            break
        del words[-stripped:]
    return out


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

def resolve_option(vocabulary, role, text, *, bare_number=False) -> dict | None:
    """
    `text` as an option of `role`: by folded label, by capacity written without its space («256gb»), by a
    bare number in the capacity field, by a company alias. {'status': 'found'|'unknown'|'ambiguous', …}.
    None for an empty text.
    """
    said = ' '.join(str(text or '').split())
    key = phrase_key(said)
    if not key:
        return None
    out = {'role': role, 'text': said, 'status': 'unknown', 'option': None, 'via': None, 'alias': None,
           'candidates': []}
    by_role = vocabulary.role_index.get(role, {})
    hits = [(o, 'label', None) for o in by_role.get(key, [])]
    if not hits and role == CAPACITY:
        hits = [(o, 'capacity', None) for o in vocabulary.capacity_index.get(compact(key), [])]
        if not hits and bare_number and _DIGITS.match(compact(key)):
            hits = [(o, 'number', None) for unit in _UNITS
                    for o in vocabulary.capacity_index.get(compact(key) + unit, [])]
    if not hits:
        alias = vocabulary.option_alias(key)
        if alias is not None and vocabulary.role(vocabulary.options[alias.option_id]) == role:
            hits = [(vocabulary.options[alias.option_id], 'alias', alias)]
    distinct = {}
    for option, via, alias in hits:
        distinct.setdefault(option.pk, (option, via, alias))
    if len(distinct) == 1:
        option, via, alias = next(iter(distinct.values()))
        out.update(status='found', option=option, via=via, alias=alias)
    elif distinct:
        out.update(status='ambiguous', candidates=[o for o, _v, _a in distinct.values()])
    return out


# ---------------------------------------------------------------------------
# R-COL
# ---------------------------------------------------------------------------

def _colour_tables(vocabulary, model_id) -> list[dict]:
    """Phrase → (option, via, alias) for the three tiers of R-COL, in order."""
    palette = vocabulary.palette(model_id) if model_id else {}
    in_palette = {pk for ids in palette.values() for pk in ids}
    tier1, tier2, tier3 = {}, {}, {}
    for key, options in vocabulary.role_index.get(COLOUR, {}).items():
        phrase = tuple(key.split(' '))
        for option in options:
            target = tier1 if option.pk in in_palette else tier2
            target.setdefault(phrase, (option, 'label', None))
    for key, alias in vocabulary.option_aliases.items():
        option = vocabulary.options.get(alias.option_id)
        if option is not None and vocabulary.role(option) == COLOUR:
            tier3.setdefault(tuple(key.split(' ')), (option, 'alias', alias))
    return [tier1, tier2, tier3]


def split_colours(vocabulary, model_id, text) -> list[dict]:
    """
    The colours `text` names, in order. Each: {'text', 'status': 'found'|'unknown'|'ambiguous', 'option',
    'via', 'alias', 'readings'}. `readings` (ambiguous only) lists every way of reading the words, each a
    list of {text, option_id, label} — a label None is a word the company does not know.
    """
    words = tokens(text)
    if not words:
        return []
    tables = _colour_tables(vocabulary, model_id)
    every = {}
    for table in tables:
        for phrase, hit in table.items():
            every.setdefault(phrase, hit)
    n = len(words)
    owner = [None] * n          # index of the piece that holds each word
    pieces = []                 # {'start', 'end', 'hit'}
    boundary = [_is_boundary(w[1]) for w in words]

    def take(start, end, hit):
        pieces.append({'start': start, 'end': end, 'hit': hit})
        for j in range(start, end):
            owner[j] = len(pieces) - 1

    for table in tables:
        longest = max((len(p) for p in table), default=0)
        i = 0
        while i < n:
            if owner[i] is not None or boundary[i]:
                i += 1
                continue
            for size in range(min(longest, n - i), 0, -1):
                span = range(i, i + size)
                if any(owner[j] is not None for j in span) or boundary[i + size - 1]:
                    continue
                hit = table.get(tuple(words[j][1] for j in span))
                if hit is not None:
                    take(i, i + size, hit)
                    i += size
                    break
            else:
                i += 1

    # The tiered reading, kept for the «word by word» reading of an ambiguous group.
    tiered = [pieces[o] if o is not None else None for o in owner]

    # Union-find over words: a group is ONE configuration.
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for index, piece in enumerate(pieces):
        for j in range(piece['start'], piece['end']):
            union(j, piece['start'])

    # Another reading of the same words, as a longer phrase of any tier (a label or an alias).
    longest = max((len(p) for p in every), default=0)
    for size in range(longest, 1, -1):
        for i in range(0, n - size + 1):
            phrase = tuple(words[j][1] for j in range(i, i + size))
            hit = every.get(phrase)
            if hit is None:
                continue
            span = set(range(i, i + size))
            held = {owner[j] for j in span}
            if held == {owner[i]} and owner[i] is not None and \
                    (pieces[owner[i]]['start'], pieces[owner[i]]['end']) == (i, i + size):
                continue    # it IS the piece already read
            crossing = any(p is not None and not set(range(pieces[p]['start'], pieces[p]['end'])) <= span
                           for p in held)
            unknown_inside = any(owner[j] is None and not boundary[j] for j in span)
            if not crossing and unknown_inside:
                # The tiered reading leaves a word unknown; this phrase reads them all: the one VALID reading.
                pieces.append({'start': i, 'end': i + size, 'hit': hit})
                for j in span:
                    owner[j] = len(pieces) - 1
                    union(j, i)
            else:
                # Two valid readings (or overlapping ones): one ambiguous configuration.
                for j in span:
                    union(j, i)

    # An unknown word glued to a colour (no separator between them) joins it: never split in silence.
    for j in range(n):
        if owner[j] is not None or boundary[j]:
            continue
        for k in (j - 1, j + 1):
            if 0 <= k < n and not boundary[k]:
                union(j, k)

    groups: dict[int, list[int]] = {}
    for j in range(n):
        if not boundary[j] or owner[j] is not None:
            groups.setdefault(find(j), []).append(j)
    out = []
    for members in sorted(groups.values(), key=min):
        members.sort()
        start, end = members[0], members[-1]
        said = unicodedata.normalize('NFC', str(text))[words[start][2]:words[end][3]]
        held = []
        for j in members:
            if owner[j] is not None and (not held or held[-1] != owner[j]):
                held.append(owner[j])
        unknown = [j for j in members if owner[j] is None and not boundary[j]]
        if len(held) == 1 and not unknown and (pieces[held[0]]['start'], pieces[held[0]]['end'] - 1) == (start, end):
            option, via, alias = pieces[held[0]]['hit']
            out.append({'text': said, 'status': 'found', 'option': option, 'via': via, 'alias': alias, 'readings': []})
            continue
        if not held and len(unknown) == 1:
            out.append({'text': said, 'status': 'unknown', 'option': None, 'via': None, 'alias': None, 'readings': []})
            continue
        out.append({'text': said, 'status': 'ambiguous', 'option': None, 'via': None, 'alias': None,
                    'readings': _readings(words, members, tiered, every, said)})
    return out


def _part(text, hit):
    option = hit[0] if hit else None
    return {'text': text, 'option_id': option.pk if option else None, 'label': option.label if option else None}


def _readings(words, members, tiered, every, said) -> list[list[dict]]:
    """An ambiguous group read as ONE colour, and as the tiered reading gave it (piece by piece, word by word)."""
    one = [_part(said, every.get(tuple(words[j][1] for j in members if not _is_boundary(words[j][1]))))]
    split, last = [], None
    for j in members:
        if _is_boundary(words[j][1]):
            continue
        piece = tiered[j]
        if piece is None:
            split.append(_part(words[j][0], None))
        elif piece is not last:
            split.append(_part(' '.join(w[0] for w in words[piece['start']:piece['end']]), piece['hit']))
        last = piece
    readings = [one]
    if [p['text'] for p in split] != [one[0]['text']]:
        readings.append(split)
    return readings


# ---------------------------------------------------------------------------
# One description
# ---------------------------------------------------------------------------

def _reading(role, text, option, via, alias) -> dict:
    return {'role': role, 'text': text, 'option_id': option.pk, 'label': option.label, 'via': via,
            'alias_id': alias.pk if alias else None}


def _doubt(role, text, reason, *, readings=None, actions=None) -> dict:
    default = {'unknown': ACTIONS_UNKNOWN, 'ambiguous': ACTIONS_UNKNOWN, 'outside': ACTIONS_OUTSIDE}
    return {'role': role, 'text': text, 'reason': reason, 'readings': readings or [],
            'actions': list(actions or default.get(reason, ['keep_pending']))}


def _base(model) -> dict:
    return {'status': '', 'complete': False, 'colour_text': '', 'option_ids': [], 'options': [], 'product_id': None,
            'product_name': None, 'draft_pending': False, 'missing': [], 'doubts': [], 'candidates': []}


def resolve_configuration(vocabulary, *, model_text='', line='', capacity_text='', colour_text='', sim_text='',
                          region_text='') -> dict:
    """
    One description → {'model', 'line', 'configurations', 'readings', 'error'}. One configuration per
    colour read (one when no colour is said). See the module docstring for the statuses.
    """
    from .device_catalog import CatalogError, clean_line, variant_key

    model = resolve_model(vocabulary, model_text)
    row = {'model': model, 'line': None, 'configurations': [], 'readings': [], 'error': ''}
    try:
        row['line'] = clean_line(line)
    except CatalogError as exc:
        row['error'] = str(exc)
        return row
    if model['status'] == 'unknown':
        row['configurations'].append({**_base(model), 'status': STATUS_UNKNOWN_MODEL, 'doubts': [
            _doubt('model', model['text'], 'unknown', actions=ACTIONS_MODEL)]})
        return row
    if model['status'] == 'ambiguous':
        row['configurations'].append({**_base(model), 'status': STATUS_AMBIGUOUS, 'doubts': [
            _doubt('model', model['text'], 'ambiguous', actions=ACTIONS_MODEL,
                   readings=[[{'text': model['text'], 'model_id': c['id'], 'label': c['name']}]
                             for c in model['candidates']])]})
        return row

    model_id = model['model_id']
    palette = vocabulary.palette(model_id)
    palette_roles = {}
    for attribute_id in palette:
        role = vocabulary.role_of_attribute.get(attribute_id, OTHER)
        palette_roles[attribute_id] = role

    # The single-option roles, then whatever the model name carried (D-A2-16), never over what was said.
    said = {CAPACITY: capacity_text, SIM: sim_text, REGION: region_text}
    singles = {role: resolve_option(vocabulary, role, said[role], bare_number=role == CAPACITY) for role in SINGLE_ROLES}
    name_colours, conflicts = [], []
    for item in model['extracted']:
        option = vocabulary.options[item['option_id']]
        role = item['role']
        if role == COLOUR:
            name_colours.append(item['text'])
        elif role in singles and singles[role] is None:
            singles[role] = {'role': role, 'text': item['text'], 'status': 'found', 'option': option,
                             'via': item['via'], 'alias': None, 'candidates': []}
        elif role in singles and singles[role]['option'] is not None and singles[role]['option'].pk == option.pk:
            continue
        else:
            conflicts.append(_doubt(role, item['text'], 'ambiguous', readings=[
                [{'text': item['text'], 'option_id': option.pk, 'label': option.label}]]))
    colour = ' '.join(str(colour_text or '').split())
    if name_colours:
        colour = ', '.join(name_colours + ([colour] if colour else []))
    colours = split_colours(vocabulary, model_id, colour) if colour else []
    if len(colours) > MAX_COLOURS:
        row['error'] = f'La descripción nombra más de {MAX_COLOURS} colores: divídela.'
        return row

    for role, found in singles.items():
        if found is not None and found['status'] == 'found':
            row['readings'].append(_reading(role, found['text'], found['option'], found['via'], found['alias']))
    for piece in colours:
        if piece['status'] == 'found':
            row['readings'].append(_reading(COLOUR, piece['text'], piece['option'], piece['via'], piece['alias']))

    in_palette = {pk for ids in palette.values() for pk in ids}
    for piece in (colours or [None]):
        config = {**_base(model), 'colour_text': piece['text'] if piece else ''}
        chosen, doubts = [], list(conflicts)
        entries = [(role, singles[role]) for role in SINGLE_ROLES]
        entries.insert(1, (COLOUR, None if piece is None else {
            'role': COLOUR, 'text': piece['text'], 'status': piece['status'], 'option': piece['option'],
            'via': piece['via'], 'alias': piece['alias'], 'readings': piece['readings']}))
        roles_said = set()
        for role, found in entries:
            if found is None:
                continue
            roles_said.add(role)
            if found['status'] == 'unknown':
                doubts.append(_doubt(role, found['text'], 'unknown'))
            elif found['status'] == 'ambiguous':
                readings = found.get('readings') or [[{'text': found['text'], 'option_id': o.pk, 'label': o.label}]
                                                     for o in found.get('candidates', [])]
                doubts.append(_doubt(role, found['text'], 'ambiguous', readings=readings))
            else:
                option = found['option']
                chosen.append(option)
                config['options'].append(_reading(role, found['text'], option, found['via'], found['alias']))
                if palette and option.pk not in in_palette:
                    doubts.append(_doubt(role, found['text'], 'outside', readings=[
                        [{'text': found['text'], 'option_id': option.pk, 'label': option.label}]]))
        config['option_ids'] = sorted(o.pk for o in chosen)
        config['doubts'] = doubts
        if palette:
            covered = {o.attribute_id for o in chosen}
            doubted = {d['role'] for d in doubts}
            missing = []
            for attribute_id, role in palette_roles.items():
                if attribute_id in covered or role in doubted:
                    continue
                name = role if role != OTHER else f'other:{vocabulary.attributes[attribute_id].name}'
                if name not in missing:
                    missing.append(name)
            order = {r: n for n, r in enumerate(VariantAttribute.ROLE_ORDER)}
            config['missing'] = sorted(missing, key=lambda r: order.get(r.split(':')[0], len(order)))
        reasons = {d['reason'] for d in doubts}
        if 'ambiguous' in reasons:
            status = STATUS_AMBIGUOUS
        elif 'unknown' in reasons:
            status = STATUS_UNKNOWN_OPTION
        elif 'outside' in reasons:
            status = STATUS_OUTSIDE
        elif config['missing']:
            status = STATUS_INCOMPLETE
        else:
            key = variant_key(config['option_ids'])
            product = next((v['product'] for v in vocabulary.variants(model_id, row['line'])
                            if v['product'].variant_key == key), None)
            if product is not None:
                status = STATUS_EXISTING
                config.update(product_id=product.pk, product_name=product.name,
                              draft_pending=product.is_pending_draft)
            else:
                status = STATUS_VALID if palette else STATUS_NO_PALETTE
        if status in (STATUS_INCOMPLETE, STATUS_NO_PALETTE):
            wanted = set(config['option_ids'])
            fitting = [v['product'] for v in vocabulary.variants(model_id, row['line']) if wanted <= v['option_ids']]
            config['candidates'] = [{'product_id': p.pk, 'name': p.name, 'draft_pending': p.is_pending_draft}
                                    for p in fitting[:MAX_CANDIDATES]]
        config['status'] = status
        config['complete'] = status in COMPLETE
        row['configurations'].append(config)
    return row
