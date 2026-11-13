"""
CATALOG-COMMERCIAL-01 · A2 — the ONLY writer of `CatalogAlias` (DEC-CATALOG-ALIAS-01, owner's P6).

    ONE ALIAS, ONE MEANING PER COMPANY AND SCOPE. AN ALIAS NEVER HIDES A REAL NAME.

Rules held here (and, where it can, by the database):

  * unique (company, scope, alias_key)                         `duplicate`  (+ UNIQUE in the database)
  * an option alias is never the folded label of an option     `conflict_with_label`
    of the company (any role, its own target included); a
    model alias never the name of a model; a model word never
    a word of the company's model names (so an exact name is
    never re-read through an alias)
  * no chains: a word's replacement is never another word       `chain`
    alias, and a word alias is never another's replacement
  * a model alias carries no option at its end («… (eSIM)»)     `contains_options`
  * text: 1–60 characters, not only digits, not a capacity      `invalid`
    («256gb» is read by normalisation, never by an alias), a
    colour alias without list separators; a model word is ONE
    word, and so is its replacement
  * a target of another company answers like one that does      `not_found`
    not exist; at most MAX_ALIASES_PER_COMPANY per company      `too_many`

Every write is audited (`catalog_alias_created/updated/deleted`) with the company. The other side of
the rule — an option label or a model name that would take an alias's text — is refused by
`device_catalog` (`conflict_with_alias`) through `alias_holding`.

`vocabulary_payload` is what B1 and the panel read: the company's model words, models and options by
role with their aliases and the palettes that hold each option. No cost, price, stock or product.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from django.db import IntegrityError, transaction

from .configuration_resolver import (
    COLOUR, SEPARATOR_WORDS, Vocabulary, _is_boundary, _is_separator, _model_candidates, compact, fold,
    phrase_key, tokens,
)
from .models import AdminAuditLog, CatalogAlias, ModelOption, ProductModel, VariantAttribute, VariantOption

MAX_ALIASES_PER_COMPANY = 2000
MAX_LENGTH = 60
SCOPES = dict(CatalogAlias.SCOPE_CHOICES)
_CAPACITY_LIKE = re.compile(r'^\d+(?:[.,]\d+)?(?:[a-z]{1,2})?$')


def _error(message, *, field, code='invalid'):
    from .device_catalog import CatalogError

    return CatalogError(message, field=field, code=code)


def _audit(action, *, company, actor, request, row, **extra):
    AdminAuditLog.log(
        actor=actor if getattr(actor, 'pk', None) else None, action=action, target_type='catalog_alias',
        target_id=row.pk, request=request, company=company,
        metadata={'scope': row.scope, 'alias': row.alias, 'alias_key': row.alias_key, 'model_id': row.model_id,
                  'option_id': row.option_id, 'replacement': row.replacement, **extra},
    )


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def _clean_scope(scope) -> str:
    scope = str(scope or '').strip()
    if scope not in SCOPES:
        raise _error('Ámbito desconocido: palabra de modelo, modelo u opción.', field='scope')
    return scope


def _clean_text(value, *, field='alias', label='el alias') -> tuple[str, str]:
    if value is not None and not isinstance(value, str):
        raise _error(f'Escribe {label} como texto.', field=field)
    text = ' '.join(str(value or '').split())
    if not text:
        raise _error(f'Indica {label}.', field=field)
    if any(unicodedata.category(ch).startswith('C') for ch in text):
        raise _error(f'{label[0].upper()}{label[1:]} lleva caracteres de control.', field=field)
    if len(text) > MAX_LENGTH:
        raise _error(f'{label[0].upper()}{label[1:]} admite hasta {MAX_LENGTH} caracteres.', field=field)
    key = phrase_key(text)
    if not key or len(key) > MAX_LENGTH:
        raise _error(f'{label[0].upper()}{label[1:]} no tiene letras ni números que se puedan leer.', field=field)
    if _CAPACITY_LIKE.match(compact(key)):
        raise _error('Un número o una capacidad («256gb») se leen solos, sin alias.', field=field)
    return text, key


def _clean_id(value, field):
    if isinstance(value, bool) or value in (None, ''):
        raise _error('Indica a qué apunta el alias.', field=field)
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal() and int(value) > 0:
        return int(value)
    raise _error('Identificador no válido.', field=field)


def _one_word(key, field, label):
    words = key.split(' ')
    if len(words) != 1 or _is_boundary(words[0]):
        raise _error(f'{label} es una sola palabra, sin espacios ni signos.', field=field)
    return words[0]


def _target(company, scope, *, model_id=None, option_id=None, replacement=None):
    """(model, option, replacement) of the scope, inside `company` (another company's id is `not_found`)."""
    if scope == CatalogAlias.SCOPE_MODEL:
        model = ProductModel.objects.filter(company=company, pk=_clean_id(model_id, 'model_id')).first()
        if model is None:
            raise _error('Modelo no encontrado.', field='model_id', code='not_found')
        return model, None, ''
    if scope == CatalogAlias.SCOPE_OPTION:
        option = (VariantOption.objects.filter(company=company, pk=_clean_id(option_id, 'option_id'))
                  .select_related('attribute').first())
        if option is None:
            raise _error('Opción no encontrada.', field='option_id', code='not_found')
        return None, option, ''
    _text, key = _clean_text(replacement, field='replacement', label='el reemplazo')
    return None, None, _one_word(key, 'replacement', 'El reemplazo')


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _model_words(company) -> set[str]:
    words = set()
    for name in ProductModel.objects.filter(company=company).values_list('name', flat=True):
        words.update(phrase_key(name).split(' '))
    return words


def _check(company, scope, key, *, text, model, option, replacement, exclude=None):
    others = CatalogAlias.objects.filter(company=company)
    if exclude is not None:
        others = others.exclude(pk=exclude.pk)
    taken = others.filter(scope=scope, alias_key=key).first()
    if taken is not None:
        raise _error(f'«{taken.alias}» ya es un alias de esta empresa con ese texto.', field='alias', code='duplicate')

    if scope == CatalogAlias.SCOPE_OPTION:
        for found in VariantOption.objects.filter(company=company).select_related('attribute'):
            if phrase_key(found.label) == key:
                why = ('es su propia etiqueta: se lee sin alias' if found.pk == option.pk
                       else f'es la opción «{found.label}» ({found.attribute.name})')
                raise _error(f'«{text}» {why}.', field='alias', code='conflict_with_label')
        role = Vocabulary.role_for(option.attribute)
        if role == COLOUR and any(_is_separator(word) for word in key.split(' ')):
            raise _error('Un alias de color no lleva separadores («y», «,», «/»): cada color es un alias.',
                         field='alias')
        return

    if scope == CatalogAlias.SCOPE_MODEL:
        for found in ProductModel.objects.filter(company=company).only('pk', 'name'):
            if fold(found.name) == fold(text) or phrase_key(found.name) == key:
                raise _error(f'«{text}» es el nombre del modelo «{found.name}»: se lee sin alias.',
                             field='alias', code='conflict_with_label')
        vocabulary = Vocabulary(company)
        found, via = _model_candidates(vocabulary, text)
        if via == 'exact':
            # The resolver reads the exact name, or the same words spaced alike, before any alias: an alias
            # with that text could never be used (review finding 5).
            other = next(iter(found))
            raise _error(f'«{text}» ya se lee como el modelo «{other.name}»: un alias no lo cambia.',
                         field='alias', code='conflict_with_label')
        rewritten = {m.pk for m, how in found.items() if how == 'model_word'}
        if rewritten and model.pk not in rewritten:
            other = next(m for m in found if m.pk in rewritten)
            raise _error(f'Las palabras de modelo de la empresa ya leen «{text}» como «{other.name}»: este alias '
                         'la volvería ambigua.', field='alias', code='conflict_with_alias')
        words = tokens(text)
        while words and _is_boundary(words[-1][1]):
            words.pop()
        for size in (3, 2, 1):
            if len(words) > size and vocabulary.lookup_any(' '.join(w[1] for w in words[-size:])):
                raise _error(f'«{text}» termina con una opción ({" ".join(w[0] for w in words[-size:])}): un alias '
                             'de modelo es sólo el nombre; las opciones van en sus columnas.',
                             field='alias', code='contains_options')
        return

    word = _one_word(key, 'alias', 'Una palabra de modelo')
    if word == replacement:
        raise _error('La palabra y su reemplazo son iguales.', field='replacement')
    if word in _model_words(company):
        raise _error(f'«{text}» ya es una palabra de los nombres de modelo de esta empresa: un alias no la '
                     'reemplaza.', field='alias', code='conflict_with_label')
    words = others.filter(scope=CatalogAlias.SCOPE_MODEL_WORD)
    if words.filter(alias_key=replacement).exists() or words.filter(replacement=word).exists():
        raise _error('Ese reemplazo encadena con otra palabra de modelo: cada palabra se reemplaza una sola vez. '
                     'Apunta las dos a la palabra final.', field='replacement', code='chain')
    # A whole-model alias that this word would read as ANOTHER model becomes ambiguous (review finding 11).
    names = {phrase_key(m.name): m for m in ProductModel.objects.filter(company=company).only('pk', 'name')}
    for model_alias in others.filter(scope=CatalogAlias.SCOPE_MODEL).select_related('model'):
        rewritten = ' '.join(replacement if w == word else w for w in model_alias.alias_key.split(' '))
        target = names.get(rewritten)
        if rewritten != model_alias.alias_key and target is not None and target.pk != model_alias.model_id:
            raise _error(f'Con esta palabra, el alias «{model_alias.alias}» de «{model_alias.model.name}» se leería '
                         f'también como «{target.name}»: cambia ese alias primero.', field='alias',
                         code='conflict_with_alias')


def alias_holding(company, scope, text, *, exclude=None):
    """The alias of `company` in `scope` whose key is `text`'s, or None (`device_catalog`'s side of the rule)."""
    key = phrase_key(text)
    if not key or company is None:
        return None
    rows = CatalogAlias.objects.filter(company=company, scope=scope, alias_key=key)
    if exclude is not None:
        rows = rows.exclude(pk=exclude.pk)
    return rows.first()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def _save(row):
    try:
        with transaction.atomic():
            row.save()
    except IntegrityError as exc:
        if 'unique_catalog_alias_per_scope' in str(exc):
            raise _error(f'«{row.alias}» ya es un alias de esta empresa con ese texto.', field='alias',
                         code='duplicate') from None
        raise _error('El alias no cumple las reglas de su ámbito.', field='alias') from None


def create_alias(*, company, scope, alias, model_id=None, option_id=None, replacement=None, actor=None,
                 request=None) -> CatalogAlias:
    scope = _clean_scope(scope)
    text, key = _clean_text(alias)
    model, option, replacement = _target(company, scope, model_id=model_id, option_id=option_id,
                                         replacement=replacement)
    _check(company, scope, key, text=text, model=model, option=option, replacement=replacement)
    if CatalogAlias.objects.filter(company=company).count() >= MAX_ALIASES_PER_COMPANY:
        raise _error(f'Esta empresa ya tiene {MAX_ALIASES_PER_COMPANY} alias: borra los que no use.',
                     field='alias', code='too_many')
    row = CatalogAlias(company=company, scope=scope, alias=text, alias_key=key, model=model, option=option,
                       replacement=replacement, created_by=actor if getattr(actor, 'pk', None) else None)
    _save(row)
    _audit('catalog_alias_created', company=company, actor=actor, request=request, row=row)
    return row


def update_alias(row, *, alias=None, model_id=None, option_id=None, replacement=None, actor=None,
                 request=None) -> CatalogAlias:
    company = row.company
    before = {'alias': row.alias, 'model_id': row.model_id, 'option_id': row.option_id,
              'replacement': row.replacement}
    text, key = _clean_text(row.alias if alias is None else alias)
    model, option, new_replacement = _target(
        company, row.scope,
        model_id=row.model_id if model_id is None else model_id,
        option_id=row.option_id if option_id is None else option_id,
        replacement=row.replacement if replacement is None else replacement)
    _check(company, row.scope, key, text=text, model=model, option=option, replacement=new_replacement, exclude=row)
    row.alias, row.alias_key, row.model, row.option, row.replacement = text, key, model, option, new_replacement
    _save(row)
    _audit('catalog_alias_updated', company=company, actor=actor, request=request, row=row, before=before)
    return row


def delete_alias(row, *, actor=None, request=None) -> None:
    company, pk = row.company, row.pk
    with transaction.atomic():
        row.delete()
    row.pk = pk
    _audit('catalog_alias_deleted', company=company, actor=actor, request=request, row=row)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def payload(row, *, labels=None) -> dict:
    """One alias for the panel. `labels`: the company's folded option labels and model names (for `shadowed`)."""
    option, model = row.option, row.model
    attribute = option.attribute if option is not None else None
    shadowed = False
    if labels is not None:
        shadowed = row.alias_key in labels.get(row.scope, set())
    return {
        'id': row.pk, 'scope': row.scope, 'alias': row.alias, 'alias_key': row.alias_key,
        'model_id': row.model_id, 'model_name': model.name if model is not None else None,
        'option_id': row.option_id, 'option_label': option.label if option is not None else None,
        'attribute_id': attribute.pk if attribute is not None else None,
        'attribute_name': attribute.name if attribute is not None else None,
        'role': Vocabulary.role_for(attribute) if attribute is not None else None,
        'replacement': row.replacement, 'shadowed': shadowed,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def _labels(company) -> dict[str, set[str]]:
    """What would shadow an alias today: option labels, model names and model-name words, folded."""
    names = list(ProductModel.objects.filter(company=company).values_list('name', flat=True))
    words = set()
    for name in names:
        words.update(phrase_key(name).split(' '))
    return {
        CatalogAlias.SCOPE_OPTION: {phrase_key(label) for label in
                                    VariantOption.objects.filter(company=company).values_list('label', flat=True)},
        CatalogAlias.SCOPE_MODEL: {phrase_key(name) for name in names},
        CatalogAlias.SCOPE_MODEL_WORD: words,
    }


def list_aliases(company, *, scope='', q='') -> list[dict]:
    rows = CatalogAlias.objects.filter(company=company).select_related('model', 'option__attribute')
    if scope:
        rows = rows.filter(scope=_clean_scope(scope))
    query = phrase_key(q)
    labels = _labels(company)
    out = []
    for row in rows.order_by('scope', 'alias_key', 'pk')[:MAX_ALIASES_PER_COMPANY]:
        item = payload(row, labels=labels)
        if query and query not in row.alias_key and query not in phrase_key(item['option_label'] or '') \
                and query not in phrase_key(item['model_name'] or '') and query not in row.replacement:
            continue
        out.append(item)
    return out


def vocabulary_payload(company) -> tuple[dict, str]:
    """(body, etag) — the company's supplier vocabulary. No cost, price, stock or product, nothing of another company."""
    aliases = list(CatalogAlias.objects.filter(company=company).order_by('scope', 'alias_key', 'pk'))
    by_model, by_option = {}, {}
    model_words = []
    for row in aliases:
        if row.scope == CatalogAlias.SCOPE_MODEL_WORD:
            model_words.append({'id': row.pk, 'alias': row.alias, 'replacement': row.replacement})
        elif row.scope == CatalogAlias.SCOPE_MODEL:
            by_model.setdefault(row.model_id, []).append({'id': row.pk, 'alias': row.alias})
        else:
            by_option.setdefault(row.option_id, []).append({'id': row.pk, 'alias': row.alias})
    palettes = {}
    for model_id, option_id in ModelOption.objects.filter(company=company).values_list('model_id', 'option_id'):
        palettes.setdefault(option_id, []).append(model_id)
    models = [{'id': m.pk, 'name': m.name, 'key': phrase_key(m.name), 'aliases': by_model.get(m.pk, [])}
              for m in ProductModel.objects.filter(company=company).only('pk', 'name').order_by('name', 'pk')]
    options = {role: [] for role in VariantAttribute.ROLE_ORDER}
    attributes = list(VariantAttribute.objects.filter(company=company).order_by('sort_order', 'name', 'pk'))
    vocabulary_roles = Vocabulary.roles_of(attributes)
    for attribute in attributes:
        for option in attribute.options.all().order_by('sort_order', 'label', 'pk'):
            options[vocabulary_roles[attribute.pk]].append({
                'id': option.pk, 'label': option.label, 'key': phrase_key(option.label),
                'attribute_id': attribute.pk, 'swatch_hex': option.swatch_hex,
                'aliases': by_option.get(option.pk, []), 'model_ids': sorted(palettes.get(option.pk, [])),
            })
    body = {'model_words': model_words, 'models': models, 'options': options,
            'separator_words': sorted(SEPARATOR_WORDS)}
    etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32] + '"'
    return body, etag
