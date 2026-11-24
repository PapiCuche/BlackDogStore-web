"""
INV-FAST-B1 — assisted catalogue creation from a pasted list.

    PROPOSE WRITES NOTHING. APPLY CREATES ONLY WHAT THE OPERATOR APPROVED, ONCE.

`propose(company, rows)` reads the same rows the text preview matches (model,
capacity, colour, SIM, region, line) and says what this company's catalogue lacks: models,
options (capacity / colour values) and variants (a variant is a PRODUCT). It
never invents an attribute: a capacity goes to the company's capacity attribute
and a colour to its colour attribute, found by what they ARE (a name from the
text recogniser's closed vocabulary, or `kind == colour`), and a row whose
attribute is missing or ambiguous is left out with the reason, never guessed.

SIM AND REGION (owner's decision 2026-11-16)
    · read against the company attribute WITH THAT ROLE (`VariantAttribute.role`), by folded label and
      then by the company's aliases (`configuration_resolver.resolve_option`); ambiguous → the row is left
      out with the reason, never picked;
    · a company without that attribute never gets one: the row is left out, saying where to configure it;
    · a SIM or region option the company lacks is proposed (`needs_approval`) but created ONLY when the
      operator names it in `approve.options` — else `option_not_approved`, and nothing is written.

`apply(...)` creates the approved part through the catalogue's own writers
(`device_catalog.create_model` / `create_option` / `set_variant`,
`product_codes.ensure_code`, `stock_unit_services.set_serialized`) in ONE
transaction, serialised per company by a lock on the company row. The plan is
recomputed after the lock, so the answer to «does it exist?» is the database's
and a second approval of the same list finds everything there and creates
nothing. References are stable names, not row numbers or ids, so the same
approval means the same thing before and after it ran.

WHAT A CREATED VARIANT IS
    · hidden from the web (`is_published_online=False`; so is a created model),
    · active and counted by unit (`is_serialized`), IMEI required unless every
      row of it said it carries none,
    · priced with the row's suggested price, or `0.00` when there is none — the
      same placeholder the product import writes — which `publication_problems`
      flags as `no_price`, so it cannot be bulk-published until somebody prices it,
    · given a generated product code (LABEL-01), so it can be labelled.

Costs, IMEIs and serials never travel here, and the answer carries none.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal

from django.db import transaction

from . import device_catalog, product_codes, stock_unit_services, text_preview_match
from .catalog_constants import CAPACITY_NAME as _CAPACITY_NAME  # kept under its old name for readers
from .models import (
    AdminAuditLog, Category, Company, Product, ProductModel, VariantAttribute, VariantOption,
)

MAX_ROWS = text_preview_match.MAX_CANDIDATES
MAX_PRICE = Decimal('99999999.99')
PLACEHOLDER_PRICE = Decimal('0.00')
#: A colour created from a list has no real swatch yet: neutral grey, to adjust in the catalogue.
PLACEHOLDER_SWATCH = device_catalog.NEUTRAL_SWATCH
#: Hints beside the match fields. Neither is an identifier or a cost.
EXTRA_FIELDS = {'price'}
#: SIM and region: the row field -> the attribute ROLE it is read against.
ROLE_FIELDS = {'sim': VariantAttribute.ROLE_SIM, 'region': VariantAttribute.ROLE_REGION}
ROLE_WORDS = {VariantAttribute.ROLE_SIM: 'SIM', VariantAttribute.ROLE_REGION: 'región'}
#: A1b-2: the suffix lives with the naming (`device_catalog.variant_name`); kept here for readers of this module.
USED_SUFFIX = device_catalog.USED_SUFFIX


class BadProposal(ValueError):
    """The request is not what this endpoint reads (400)."""

    def __init__(self, message, *, code='invalid', status=400):
        super().__init__(message)
        self.code = code
        self.status = status

    def body(self) -> dict:
        return {'detail': str(self), 'code': self.code}


# ---------------------------------------------------------------------------
# Numbers and ids from the request: validated, never trusted to a cast or a query
# ---------------------------------------------------------------------------

_PRICE = re.compile(r'\d{1,8}(?:\.\d{1,2})?', re.ASCII)
MAX_ID = 2 ** 31 - 1          # what a PostgreSQL integer key can hold


def parse_price(value) -> Decimal:
    """
    A sale price from JSON: a positive number up to 99999999.99 with at most two decimals, ASCII digits only.
    Anything else (NaN, Infinity, exponents, booleans, containers, huge or empty text, a third decimal) is a 400.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise BadProposal('Precio inválido.')
    text = str(value).strip()
    if not _PRICE.fullmatch(text):
        raise BadProposal('Precio inválido: usa un número mayor que 0 con hasta dos decimales.')
    price = Decimal(text).quantize(Decimal('0.01'))
    if not 0 < price <= MAX_PRICE:
        raise BadProposal('El precio debe ser mayor que 0.')
    return price


def parse_id(value, *, label='identificador'):
    """A database id from JSON: None/'' -> None; an integer (or its ASCII digits) in 1..MAX_ID; else a 400."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise BadProposal(f'{label.capitalize()} inválido.')
    text = str(value).strip()
    if not re.fullmatch(r'\d{1,10}', text, re.ASCII) or not 1 <= int(text) <= MAX_ID:
        raise BadProposal(f'{label.capitalize()} inválido.')
    return int(text)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def clean_rows(rows) -> list[dict]:
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise BadProposal(f'Envía entre 1 y {MAX_ROWS} filas.')
    cleaned = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise BadProposal('Cada fila es un objeto.')
        try:
            # A cost, quantity, IMEI or serial is an unknown field: refused, never carried.
            base = text_preview_match.clean({k: v for k, v in raw.items() if k not in EXTRA_FIELDS})
        except text_preview_match.BadCandidate as exc:
            raise BadProposal(str(exc)) from None
        price = raw.get('price')
        price = None if price is None or (isinstance(price, str) and not price.strip()) else parse_price(price)
        cleaned.append({**base, 'price': price})
    if len({row['key'] for row in cleaned}) != len(cleaned):
        raise BadProposal('Hay claves repetidas.')
    return cleaned


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _norm(text) -> str:
    """The key two spellings of one name share: composed (NFC), spaces collapsed, case folded."""
    return ' '.join(unicodedata.normalize('NFC', str(text or '')).split()).casefold()


def _label(text: str) -> str:
    """A value as the catalogue shows it: «negro» -> «Negro», but «Azul Marino» stays as typed."""
    text = ' '.join(str(text).split())
    text = unicodedata.normalize('NFC', text)
    return text[:1].upper() + text[1:] if text.islower() else text


def _capacity_label(gb: int) -> str:
    return f'{gb // 1024} TB' if gb >= 1024 and gb % 1024 == 0 else f'{gb} GB'


def capacity_spellings(gb: int) -> set[str]:
    """How a company may already write a capacity, folded by `_norm`: «256 GB», «256GB»; «1 TB», «1TB»."""
    return {f'{gb} gb', f'{gb}gb'} | ({f'{gb // 1024} tb', f'{gb // 1024}tb'} if gb % 1024 == 0 else set())


def _unique(attributes):
    """The only attribute of a queryset, or None when there is none or several."""
    found = list(attributes[:2])
    return found[0] if len(found) == 1 else None


class _Plan:
    """One reading of the rows against the database. Pure: it writes nothing."""

    def __init__(self, company, rows):
        self.company = company
        self.models: dict[str, dict] = {}
        self.options: dict[str, dict] = {}
        self.variants: dict[str, dict] = {}
        self.rows: list[dict] = []
        # A1b: the attribute with the capacity ROLE first; by its name only when the company assigned none.
        self._capacity_attribute = (
            VariantAttribute.objects.filter(company=company, role=VariantAttribute.ROLE_CAPACITY).first()
            or _unique(VariantAttribute.objects.filter(company=company, name__iregex=_CAPACITY_NAME)))
        self._colour_attribute = _unique(VariantAttribute.objects.filter(
            company=company, kind=VariantAttribute.KIND_COLOR))
        # Compared by `_norm`, not by the database: «Teléfono Y» typed composed or decomposed, or «TELÉFONO y»,
        # is ONE model, and the first created (lowest pk) is the one every later row finds.
        self._models_by_name: dict[str, ProductModel] = {}
        # A1b: models by the writer's own key (`normalize_model_name`), so what the plan calls «existing» is
        # exactly what `create_model` would refuse as a duplicate; options keep `_norm` (accents kept).
        for found in ProductModel.objects.filter(company=company).order_by('pk'):
            self._models_by_name.setdefault(device_catalog.normalize_model_name(found.name), found)
        self._options_by_label: dict[str, list[VariantOption]] = {}
        for found in VariantOption.objects.filter(company=company).select_related('attribute').order_by('pk'):
            self._options_by_label.setdefault(_norm(found.label), []).append(found)
        self._vocabulary = None
        # SIM and region: ONLY the attribute with that role (unique per company). Never by name, never created.
        self._role_attributes = {
            attribute.role: attribute for attribute in VariantAttribute.objects.filter(
                company=company, role__in=ROLE_FIELDS.values())}
        for row in rows:
            read = self._read(row)
            review = self.models.get(read['model_ref'] or '', {}).get('review')
            if review:
                read['warnings'] = [*read['warnings'], review]
            self.rows.append(read)
        self._finish()

    # -- one row ----------------------------------------------------------
    def _skip(self, row, reason):
        return {'key': row['key'], 'variant_ref': None, 'model_ref': None, 'reason': reason, 'warnings': []}

    def _model(self, row):
        name = ' '.join(unicodedata.normalize('NFC', row['model']).split())
        ref = f'model:{_norm(name)}'
        if ref not in self.models:
            existing = self._models_by_name.get(device_catalog.normalize_model_name(name))
            self.models[ref] = {
                'ref': ref, 'name': existing.name if existing else name,
                'status': 'existing' if existing else 'missing',
                'model_id': existing.pk if existing else None, 'row_keys': [],
            }
            if existing is None:
                review = self._review(name)
                if review:
                    self.models[ref]['review'] = review
        self.models[ref]['row_keys'].append(row['key'])
        return ref

    def _vocab(self):
        from .configuration_resolver import Vocabulary

        if self._vocabulary is None:
            self._vocabulary = Vocabulary(self.company)
        return self._vocabulary

    def _review(self, name):
        """
        A2 (D-A2-16): a «missing» model the company's vocabulary reads as one it HAS — through an alias, or
        with options written inside the name. Only a warning: `apply` is unchanged (S8), nothing is created
        unless a person approves it, and the warning says why it should not be.
        """
        from .configuration_resolver import resolve_model

        found = resolve_model(self._vocab(), name)
        if found['status'] in ('exact', 'alias', 'model_word'):
            return (f'«{name[:60]}» se lee como «{found["model_name"]}» con el vocabulario de la empresa: '
                    'revísalo antes de crear un modelo nuevo.')
        if found['status'] == 'name_with_options':
            options = ', '.join(f'«{item["label"]}»' for item in found['extracted'])
            return (f'«{name[:60]}» parece «{found["model_name"]}» con {options} dentro del nombre: revísalo antes '
                    'de crear un modelo nuevo.')
        return ''

    def _capacity_option(self, gb):
        attribute = self._capacity_attribute
        if attribute is None:
            return None, ('El catálogo no tiene un único atributo de capacidad (almacenamiento): '
                          'créalo en Catálogo › Modelos.')
        wanted = capacity_spellings(gb)
        for option in VariantOption.objects.filter(company=self.company, attribute=attribute).order_by('pk'):
            if _norm(option.label) in wanted:
                return self._option(attribute, option.label, option), ''
        return self._option(attribute, _capacity_label(gb), None), ''

    def _colour_option(self, text):
        # Review: a colour text is never read as a SIM or a region option («eSIM» typed as the colour).
        found = [o for o in self._options_by_label.get(_norm(text), []) if o.attribute.role not in ROLE_FIELDS.values()]
        if len(found) > 1:
            return None, f'«{text[:40]}» es opción de varios atributos: no se puede decidir.'
        if found:
            return self._option(found[0].attribute, found[0].label, found[0]), ''
        if self._colour_attribute is None:
            return None, ('El catálogo no tiene un único atributo de color: créalo en Catálogo › Modelos '
                          f'para crear «{text[:40]}».')
        return self._option(self._colour_attribute, _label(text), None), ''

    def _role_option(self, role, text):
        """A SIM or region text as an option of the company attribute with that role. (ref, '') or (None, reason)."""
        from .configuration_resolver import resolve_option

        word = ROLE_WORDS[role]
        attribute = self._role_attributes.get(role)
        if attribute is None:
            return None, (f'El catálogo no tiene un atributo de {word}: configúralo en Catálogo › Modelos '
                          f'para leer «{text[:40]}».')
        found = resolve_option(self._vocab(), role, text)
        if found is None:
            return None, f'No se pudo leer la {word} «{text[:40]}».'
        if found['status'] == 'ambiguous':
            labels = ', '.join(f'«{option.label}»' for option in found['candidates'][:4])
            return None, (f'«{text[:40]}» puede ser {labels} ({word}): no se puede decidir. '
                          'Corrige la fila o ajusta las opciones en Catálogo › Modelos.')
        if found['status'] == 'found':
            return self._option(attribute, found['option'].label, found['option']), ''
        return self._option(attribute, _label(text), None), ''

    def _option(self, attribute, label, existing):
        ref = f'option:{attribute.pk}:{_norm(label)}'
        explicit = attribute.role in ROLE_FIELDS.values()
        self.options.setdefault(ref, {
            'ref': ref, 'attribute_id': attribute.pk, 'attribute_name': attribute.name, 'label': label,
            'status': 'existing' if existing else 'missing', 'option_id': existing.pk if existing else None,
            'role': attribute.role,
            # A missing SIM / region option is created only when the operator names it in `approve.options`.
            'needs_approval': explicit and existing is None,
        })
        return ref

    def _read(self, row):
        if row['kind'] == 'accessory' or row['line'] == 'accessory':
            return self._skip(row, 'Un accesorio no lleva variantes: se busca por su nombre de producto.')
        if row['identifier']:
            return self._skip(row, text_preview_match.IDENTIFIER_REASON)
        if not row['model']:
            return self._skip(row, 'Falta el modelo.')
        model_ref = self._model(row)
        if not row['capacity']:
            return {**self._skip(row, 'Falta la capacidad: sin ella no se propone una variante '
                                      '(escríbela en la fila).'), 'model_ref': model_ref}
        refs, reason = [], ''
        ref, reason = self._capacity_option(row['capacity'])
        if ref is None:
            return {**self._skip(row, reason), 'model_ref': model_ref}
        refs.append(ref)
        warnings = []
        if row['colour'] and text_preview_match.has_identifier(row['colour']):
            # An IMEI, serial or phone hidden in the colour is never proposed nor stored as an option.
            return {**self._skip(row, 'El color lleva un IMEI, una serie u otro identificador: quítalo para proponer.'),
                    'model_ref': model_ref}
        if row['colour']:
            ref, reason = self._colour_option(row['colour'])
            if ref is None:
                return {**self._skip(row, reason), 'model_ref': model_ref}
            if ref in refs:
                return {**self._skip(row, 'Dos valores del mismo atributo en la misma fila.'), 'model_ref': model_ref}
            refs.append(ref)
        else:
            warnings.append('Sin color: la variante se propone sólo con la capacidad.')
        ignored = {}
        for field, role in ROLE_FIELDS.items():
            # Review: format characters (a zero-width space) are not a value; an invisible text is no SIM or region.
            text = ''.join(ch for ch in str(row.get(field) or '') if unicodedata.category(ch) != 'Cf').strip()
            if not text:
                continue
            if text_preview_match.has_identifier(text):
                # An IMEI, serial or phone hidden in the SIM or the region is never proposed nor stored as an option.
                return {**self._skip(row, f'La {ROLE_WORDS[role]} lleva un IMEI, una serie u otro identificador: '
                                          'quítalo para proponer.'), 'model_ref': model_ref}
            if self._role_attributes.get(role) is None:
                # Review: a company without an attribute of that role (the pilot today) still gets the row proposed,
                # WITHOUT that option — as before this change — and is told how to separate it. Never an attribute.
                warnings.append(f'La {ROLE_WORDS[role]} «{text[:40]}» no distingue la variante: la empresa no tiene un '
                                f'atributo de {ROLE_WORDS[role]}. Configúralo en Catálogo › Modelos para separarlas.')
                ignored[role] = text
                continue
            ref, reason = self._role_option(role, text)
            if ref is None:
                return {**self._skip(row, reason), 'model_ref': model_ref}
            if ref in refs:
                return {**self._skip(row, 'Dos valores del mismo atributo en la misma fila.'), 'model_ref': model_ref}
            refs.append(ref)
        refs.sort()
        line = Product.CONDITION_USED if row['line'] == 'used' else Product.CONDITION_NEW
        variant_ref = f'variant:{model_ref}|{line}|{"+".join(refs)}'
        variant = self.variants.setdefault(variant_ref, {
            'ref': variant_ref, 'model_ref': model_ref, 'line': line, 'option_refs': refs, 'row_keys': [],
            'status': 'missing', 'product_id': None, 'suggested_name': '', 'suggested_price': None,
        })
        variant['row_keys'].append(row['key'])
        # Review HIGH: what a row says that cannot separate the variant (no attribute with that role). Two rows of ONE
        # proposed variant that say different things (eSIM and Chip) are two products: never created as one.
        for role, text in ignored.items():
            variant.setdefault('_ignored', {}).setdefault(role, {})[_norm(text)] = text
        if variant['suggested_price'] is None and row['price'] is not None:
            variant['suggested_price'] = row['price']
        return {'key': row['key'], 'variant_ref': variant_ref, 'model_ref': model_ref, 'reason': '',
                'warnings': warnings}

    # -- once all rows are read ---------------------------------------------
    def _finish(self):
        attributes = {a.pk: a for a in VariantAttribute.objects.filter(company=self.company)}
        for variant in self.variants.values():
            mixed = [(role, sorted(values.values())) for role, values in (variant.get('_ignored') or {}).items()
                     if len(values) > 1]
            if mixed:
                said = '; '.join(f'{ROLE_WORDS[role]} {", ".join(values)}' for role, values in mixed)
                variant['mixed'] = (f'Estas filas traen datos distintos que el catálogo no separa ({said}): serían productos '
                                    'distintos creados como uno. Configura el atributo en Catálogo › Modelos o crea las '
                                    'variantes a mano.')
            model = self.models[variant['model_ref']]
            # A1b-2: ONE naming for every path (`device_catalog.variant_name`): the model, the labels in
            # role order (capacity → colour → SIM → region → other, then the company's own order), the
            # used-line suffix. The endpoint «Crear variante» names the same combination exactly alike.
            options = [self.options[ref] for ref in variant['option_refs']]
            variant['suggested_name'] = device_catalog.variant_name(
                model['name'], [(attributes[o['attribute_id']], o['label']) for o in options],
                condition_line=variant['line'])
            # A device carries an IMEI unless the operator says this variant does not (`without_imei`, in apply).
            variant['requires_imei'] = True
            if model['model_id'] and all(o['option_id'] for o in options):
                found = device_catalog.find_variant(self.company, {
                    'model_id': model['model_id'], 'condition_line': variant['line'],
                    'variant_key': device_catalog.variant_key(o['option_id'] for o in options)})
                if found is not None and found.archived_at is not None:
                    # VARIANT-SAFE-01: never reused, never created again (it holds the combination).
                    from .variant_lifecycle import archived_refusal

                    variant.update(status='archived', product_id=found.pk, suggested_name=found.name,
                                   archived=archived_refusal(found))
                elif found is not None:
                    variant.update(status='existing', product_id=found.pk, suggested_name=found.name)

    def counts(self) -> dict:
        def n(items, status):
            return sum(1 for item in items.values() if item['status'] == status)

        resolved = sum(1 for row in self.rows if row['variant_ref'])
        return {
            'models_missing': n(self.models, 'missing'), 'models_existing': n(self.models, 'existing'),
            'options_missing': n(self.options, 'missing'), 'options_existing': n(self.options, 'existing'),
            'variants_missing': n(self.variants, 'missing'), 'variants_existing': n(self.variants, 'existing'),
            'rows_resolved': resolved, 'rows_unresolved': len(self.rows) - resolved,
        }

    def payload(self) -> dict:
        def public(variant):
            data = {k: v for k, v in variant.items() if not k.startswith('_')}
            data['suggested_price'] = str(variant['suggested_price']) if variant['suggested_price'] is not None else None
            # What approving it would create, in words for the review panel.
            data['creates'] = {
                'model': self.models[variant['model_ref']]['status'] == 'missing',
                'options': [self.options[ref]['ref'] for ref in variant['option_refs']
                            if self.options[ref]['status'] == 'missing'],
            }
            return data

        return {
            'plan': {'models': list(self.models.values()),
                     'options': [{k: v for k, v in o.items() if not k.startswith('_')} for o in self.options.values()],
                     'variants': [public(v) for v in self.variants.values()]},
            'rows': self.rows, 'counts': self.counts(),
        }


def propose(company, rows) -> dict:
    return _Plan(company, clean_rows(rows)).payload()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _refs(approve) -> tuple[set, set, set]:
    if not isinstance(approve, dict):
        raise BadProposal('Indica qué se aprueba: «approve» con «models» y «variants».')
    out = []
    # `options`: the SIM / region options the operator approves creating (optional; capacity and colour need none).
    for field in ('models', 'variants', 'options'):
        value = approve.get(field) or []
        if not isinstance(value, list) or any(not isinstance(v, str) or len(v) > 400 for v in value):
            raise BadProposal(f'«{field}» es una lista de referencias.')
        out.append(set(value))
    return out[0], out[1], out[2]


def _prices(prices, plan) -> dict:
    if prices in (None, {}):
        return {}
    if not isinstance(prices, dict):
        raise BadProposal('«prices» es un objeto referencia -> precio.')
    out = {}
    for ref, value in prices.items():
        if ref not in plan.variants:
            raise BadProposal('Precio de una variante que no está en la lista.', code='unknown_ref')
        out[ref] = parse_price(value)
    return out


def _fingerprint(rows, approve, prices, category_id, without_imei=()) -> str:
    models, variants, options = approve
    data = {
        # SIM and region join a row only when said, so a request without them keeps the fingerprint it had.
        'rows': [[r['key'], r['model'], r['capacity'], r['colour'], r['line'], str(r['price'])]
                 + ([r.get('sim') or '', r.get('region') or ''] if r.get('sim') or r.get('region') else [])
                 for r in rows],
        'models': sorted(models), 'variants': sorted(variants),
        'prices': sorted((k, str(v)) for k, v in prices.items()), 'category': category_id,
        'without_imei': sorted(without_imei),
    }
    if options:
        data['options'] = sorted(options)
    return request_fingerprint(data)


# ---------------------------------------------------------------------------
# Idempotency, shared with every «propose / apply» of the catalogue (CATALOG-REFERENCE-01 · PR B reuses it)
# ---------------------------------------------------------------------------

def request_fingerprint(data) -> str:
    """The hash of what a request asks (JSON-able `data`): the same request, the same fingerprint."""
    body = json.dumps(data, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()


def clean_idempotency_key(value) -> str:
    return ' '.join(str(value or '').split())[:120]


def replay(company, action, key, fingerprint):
    """
    The answer an earlier apply with this `Idempotency-Key` gave (`replayed: True`), read from its audit entry
    (`action`, this company); None when there is none. The same key with another request is a 409. Call it under
    the company lock, so two requests with one key never both apply.
    """
    if not key:
        return None
    prior = AdminAuditLog.objects.filter(company=company, action=action, metadata__idempotency_key=key).first()
    if prior is None:
        return None
    if prior.metadata.get('fingerprint') != fingerprint:
        raise BadProposal('Esa clave de idempotencia ya se usó con otra solicitud.',
                          code='idempotency_conflict', status=409)
    return {**prior.metadata['result'], 'replayed': True}


def _new_slug(company, name):
    from .import_services import unique_slug

    return unique_slug(company, name, taken=set())


def _create_variant(company, variant, *, model, options, price, category, actor, request) -> Product:
    product = Product(
        company=company, name=variant['suggested_name'], slug=_new_slug(company, variant['suggested_name']),
        price=price, category=category or model.category, is_active=True, is_published_online=False,
    )
    product.full_clean()
    product.save()
    product = device_catalog.set_variant(
        product, model_id=model.pk, condition_line=variant['line'], option_ids=[o.pk for o in options],
        actor=actor, request=request,
    )
    product_codes.ensure_code(product, actor=actor, request=request)
    product = stock_unit_services.set_serialized(
        product, serialized=True, requires_imei=variant['requires_imei'], actor=actor, request=request)
    AdminAuditLog.log(
        actor=actor, action='product_created', target_type='product', target_id=product.pk, request=request,
        company=company,
        metadata={'product_name': product.name, 'slug': product.slug, 'price': str(product.price),
                  'is_published_online': False, 'via': 'catalog_proposal'},
    )
    return product


def apply(*, company, rows, approve, prices=None, category_id=None, without_imei=None, idempotency_key='', actor,
          request=None) -> dict:
    """Create what was approved. Returns the answer body; raises `BadProposal` / `CatalogError`."""
    rows = clean_rows(rows)
    models_ok, variants_ok, options_ok = _refs(approve)
    key = clean_idempotency_key(idempotency_key)
    with transaction.atomic():
        # One approval at a time per company: the plan below is read AFTER this lock. NO KEY UPDATE (review of #371):
        # rows that only reference the company (an audit entry written under a model lock) are never blocked by it,
        # so a screen holding a model and this approval waiting for that model cannot deadlock.
        Company.objects.select_for_update(no_key=True).get(pk=company.pk)
        category = None
        category_id = parse_id(category_id, label='categoría')
        if category_id is not None:
            category = Category.objects.filter(company=company, pk=category_id).first()
            if category is None:
                raise device_catalog.CatalogError('Categoría no encontrada.', field='category', code='not_found')
        plan = _Plan(company, rows)
        price_for = _prices(prices, plan)
        # IMEI is required by default. The ONLY way to create an IMEI-optional variant is the operator naming it here.
        if without_imei is None:
            without_imei = []
        if not isinstance(without_imei, list) or any(not isinstance(v, str) for v in without_imei):
            raise BadProposal('«without_imei» es una lista de referencias de variante.')
        optional = set(without_imei)
        if optional - set(plan.variants):
            raise BadProposal('Una referencia de «without_imei» no está en la lista.', code='unknown_ref')
        fingerprint = _fingerprint(rows, (models_ok, variants_ok, options_ok), price_for, category.pk if category else None, optional)
        prior = replay(company, 'catalog_proposal_applied', key, fingerprint)
        if prior is not None:
            return prior
        for ref in models_ok:
            if ref not in plan.models:
                raise BadProposal('Una referencia de modelo no está en la lista.', code='unknown_ref')
        for ref in variants_ok:
            if ref not in plan.variants:
                raise BadProposal('Una referencia de variante no está en la lista.', code='unknown_ref')
            if plan.variants[ref].get('mixed'):
                raise BadProposal(plan.variants[ref]['mixed'], code='mixed_identity')
            if plan.variants[ref].get('archived'):
                raise BadProposal(plan.variants[ref]['archived'], code='variant_archived', status=409)
            model = plan.models[plan.variants[ref]['model_ref']]
            if model['status'] == 'missing' and model['ref'] not in models_ok:
                raise BadProposal(f'La variante «{plan.variants[ref]["suggested_name"]}» necesita que se apruebe '
                                  f'el modelo «{model["name"]}».', code='model_not_approved')
        for ref in options_ok:
            if ref not in plan.options:
                raise BadProposal('Una referencia de opción no está en la lista.', code='unknown_ref')
        for ref in sorted(variants_ok):
            if plan.variants[ref]['status'] != 'missing':
                continue
            for option_ref in plan.variants[ref]['option_refs']:
                option = plan.options[option_ref]
                if option['status'] == 'missing' and option['needs_approval'] and option_ref not in options_ok:
                    raise BadProposal(
                        f'La variante «{plan.variants[ref]["suggested_name"]}» necesita crear la opción '
                        f'«{option["label"]}» de «{option["attribute_name"]}»: márcala para aprobarla.',
                        code='option_not_approved')

        created = {'models': [], 'options': [], 'variants': []}
        existing = {'models': [], 'variants': []}
        made_models: dict[str, ProductModel] = {}
        made_options: dict[str, VariantOption] = {}

        for ref in sorted(models_ok):
            item = plan.models[ref]
            if item['status'] == 'existing':
                existing['models'].append({'ref': ref, 'model_id': item['model_id'], 'name': item['name']})
                continue
            model = device_catalog.create_model(
                company=company, name=item['name'], category_id=category.pk if category else None,
                is_published_online=False, actor=actor, request=request)
            made_models[ref] = model
            created['models'].append({'ref': ref, 'model_id': model.pk, 'name': model.name})

        results = []
        for ref in sorted(variants_ok):
            variant = plan.variants[ref]
            model_item = plan.models[variant['model_ref']]
            if variant['status'] == 'existing':
                existing['variants'].append({'ref': ref, 'product_id': variant['product_id'],
                                             'name': variant['suggested_name']})
                results.append({'ref': ref, 'product_id': variant['product_id'], 'name': variant['suggested_name'],
                                'created': False})
                continue
            model = made_models.get(model_item['ref']) or ProductModel.objects.get(
                company=company, pk=model_item['model_id'])
            options = []
            for option_ref in variant['option_refs']:
                item = plan.options[option_ref]
                if option_ref not in made_options:
                    if item['status'] == 'existing':
                        made_options[option_ref] = VariantOption.objects.get(company=company, pk=item['option_id'])
                    else:
                        attribute = VariantAttribute.objects.get(company=company, pk=item['attribute_id'])
                        made_options[option_ref] = device_catalog.create_option(
                            company=company, attribute_id=attribute.pk, label=item['label'],
                            swatch_hex=PLACEHOLDER_SWATCH if attribute.kind == VariantAttribute.KIND_COLOR else '',
                            actor=actor, request=request)
                        created['options'].append({'ref': option_ref, 'option_id': made_options[option_ref].pk,
                                                   'label': item['label'], 'attribute_id': attribute.pk})
                options.append(made_options[option_ref])
            price = price_for.get(ref) or variant['suggested_price'] or PLACEHOLDER_PRICE
            variant = {**variant, 'requires_imei': ref not in optional}
            product = _create_variant(company, variant, model=model, options=options, price=price,
                                      category=category, actor=actor, request=request)
            created['variants'].append({'ref': ref, 'product_id': product.pk, 'name': product.name})
            results.append({'ref': ref, 'product_id': product.pk, 'name': product.name, 'created': True})

        body = {'created': created, 'existing': existing, 'variants': results, 'replayed': False}
        AdminAuditLog.log(
            actor=actor, action='catalog_proposal_applied', target_type='company', target_id=company.pk,
            request=request, company=company,
            metadata={'company_id': company.pk, 'idempotency_key': key, 'fingerprint': fingerprint,
                      'models_created': len(created['models']), 'options_created': len(created['options']),
                      'variants_created': len(created['variants']), 'variants_existing': len(existing['variants']),
                      'result': body},
        )
    return body
