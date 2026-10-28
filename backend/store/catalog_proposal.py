"""
INV-FAST-B1 — assisted catalogue creation from a pasted list.

    PROPOSE WRITES NOTHING. APPLY CREATES ONLY WHAT THE OPERATOR APPROVED, ONCE.

`propose(company, rows)` reads the same rows the text preview matches (model,
capacity, colour, line) and says what this company's catalogue lacks: models,
options (capacity / colour values) and variants (a variant is a PRODUCT). It
never invents an attribute: a capacity goes to the company's capacity attribute
and a colour to its colour attribute, found by what they ARE (a name from the
text recogniser's closed vocabulary, or `kind == colour`), and a row whose
attribute is missing or ambiguous is left out with the reason, never guessed.

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
from decimal import Decimal, InvalidOperation

from django.db import transaction

from . import device_catalog, product_codes, stock_unit_services, text_preview_match
from .models import (
    AdminAuditLog, Category, Company, Product, ProductModel, VariantAttribute, VariantOption,
)

MAX_ROWS = text_preview_match.MAX_CANDIDATES
MAX_PRICE = Decimal('99999999.99')
PLACEHOLDER_PRICE = Decimal('0.00')
#: A colour created from a list has no real swatch yet: neutral grey, to adjust in the catalogue.
PLACEHOLDER_SWATCH = '#9CA3AF'
#: Hints beside the match fields. Neither is an identifier or a cost.
EXTRA_FIELDS = {'hasImei', 'price'}
_CAPACITY_NAME = r'^(capacidad|almacenamiento|capacity|storage)$'
USED_SUFFIX = 'Seminuevo'


class BadProposal(ValueError):
    """The request is not what this endpoint reads (400)."""

    def __init__(self, message, *, code='invalid', status=400):
        super().__init__(message)
        self.code = code
        self.status = status

    def body(self) -> dict:
        return {'detail': str(self), 'code': self.code}


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
        has_imei = raw.get('hasImei')
        if has_imei is not None and not isinstance(has_imei, bool):
            raise BadProposal('«hasImei» es verdadero o falso.')
        price = raw.get('price')
        if price in (None, ''):
            price = None
        else:
            try:
                price = Decimal(str(price)).quantize(Decimal('0.01'))
            except (InvalidOperation, ValueError):
                raise BadProposal('Precio inválido.') from None
            if not 0 < price <= MAX_PRICE:
                raise BadProposal('El precio debe ser mayor que 0.')
        cleaned.append({**base, 'has_imei': has_imei, 'price': price})
    if len({row['key'] for row in cleaned}) != len(cleaned):
        raise BadProposal('Hay claves repetidas.')
    return cleaned


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _label(text: str) -> str:
    """A value as the catalogue shows it: «negro» -> «Negro», but «Azul Marino» stays as typed."""
    text = ' '.join(str(text).split())
    return text[:1].upper() + text[1:] if text.islower() else text


def _capacity_label(gb: int) -> str:
    return f'{gb // 1024} TB' if gb >= 1024 and gb % 1024 == 0 else f'{gb} GB'


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
        self._capacity_attribute = _unique(VariantAttribute.objects.filter(
            company=company, name__iregex=_CAPACITY_NAME))
        self._colour_attribute = _unique(VariantAttribute.objects.filter(
            company=company, kind=VariantAttribute.KIND_COLOR))
        for row in rows:
            self.rows.append(self._read(row))
        self._finish()

    # -- one row ----------------------------------------------------------
    def _skip(self, row, reason):
        return {'key': row['key'], 'variant_ref': None, 'model_ref': None, 'reason': reason, 'warnings': []}

    def _model(self, row):
        name = row['model']
        ref = f'model:{name.lower()}'
        if ref not in self.models:
            existing = ProductModel.objects.filter(company=self.company, name__iexact=name).order_by('pk').first()
            self.models[ref] = {
                'ref': ref, 'name': existing.name if existing else name,
                'status': 'existing' if existing else 'missing',
                'model_id': existing.pk if existing else None, 'row_keys': [],
            }
        self.models[ref]['row_keys'].append(row['key'])
        return ref

    def _capacity_option(self, gb):
        attribute = self._capacity_attribute
        if attribute is None:
            return None, ('El catálogo no tiene un único atributo de capacidad (almacenamiento): '
                          'créalo en Catálogo › Modelos.')
        wanted = {f'{gb} gb', f'{gb}gb'} | ({f'{gb // 1024} tb', f'{gb // 1024}tb'} if gb % 1024 == 0 else set())
        for option in VariantOption.objects.filter(company=self.company, attribute=attribute):
            if option.label.lower() in wanted:
                return self._option(attribute, option.label, option), ''
        return self._option(attribute, _capacity_label(gb), None), ''

    def _colour_option(self, text):
        found = list(VariantOption.objects.filter(company=self.company, label__iexact=text)
                     .select_related('attribute').order_by('pk')[:2])
        if len(found) > 1:
            return None, f'«{text[:40]}» es opción de varios atributos: no se puede decidir.'
        if found:
            return self._option(found[0].attribute, found[0].label, found[0]), ''
        if self._colour_attribute is None:
            return None, ('El catálogo no tiene un único atributo de color: créalo en Catálogo › Modelos '
                          f'para crear «{text[:40]}».')
        return self._option(self._colour_attribute, _label(text), None), ''

    def _option(self, attribute, label, existing):
        ref = f'option:{attribute.pk}:{label.lower()}'
        self.options.setdefault(ref, {
            'ref': ref, 'attribute_id': attribute.pk, 'attribute_name': attribute.name, 'label': label,
            '_sort': (attribute.sort_order, attribute.name.lower(), attribute.pk),
            'status': 'existing' if existing else 'missing', 'option_id': existing.pk if existing else None,
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
        if row['colour']:
            ref, reason = self._colour_option(row['colour'])
            if ref is None:
                return {**self._skip(row, reason), 'model_ref': model_ref}
            if ref in refs:
                return {**self._skip(row, 'Dos valores del mismo atributo en la misma fila.'), 'model_ref': model_ref}
            refs.append(ref)
        else:
            warnings.append('Sin color: la variante se propone sólo con la capacidad.')
        refs.sort()
        line = Product.CONDITION_USED if row['line'] == 'used' else Product.CONDITION_NEW
        variant_ref = f'variant:{model_ref}|{line}|{"+".join(refs)}'
        variant = self.variants.setdefault(variant_ref, {
            'ref': variant_ref, 'model_ref': model_ref, 'line': line, 'option_refs': refs, 'row_keys': [],
            'status': 'missing', 'product_id': None, 'suggested_name': '', 'suggested_price': None,
            '_imei_hints': [],
        })
        variant['row_keys'].append(row['key'])
        variant['_imei_hints'].append(row['has_imei'])
        if variant['suggested_price'] is None and row['price'] is not None:
            variant['suggested_price'] = row['price']
        return {'key': row['key'], 'variant_ref': variant_ref, 'model_ref': model_ref, 'reason': '',
                'warnings': warnings}

    # -- once all rows are read ---------------------------------------------
    def _finish(self):
        for variant in self.variants.values():
            model = self.models[variant['model_ref']]
            # The name reads in the catalogue's own attribute order, not in the order of the text.
            options = sorted((self.options[ref] for ref in variant['option_refs']), key=lambda o: o['_sort'])
            parts = [model['name'], *(o['label'] for o in options)]
            if variant['line'] == Product.CONDITION_USED:
                parts.append(USED_SUFFIX)
            variant['suggested_name'] = ' '.join(parts)[:255]
            variant['requires_imei'] = any(hint is not False for hint in variant['_imei_hints'])
            if model['model_id'] and all(o['option_id'] for o in options):
                found = device_catalog.find_variant(self.company, {
                    'model_id': model['model_id'], 'condition_line': variant['line'],
                    'variant_key': device_catalog.variant_key(o['option_id'] for o in options)})
                if found is not None:
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

def _refs(approve) -> tuple[set, set]:
    if not isinstance(approve, dict):
        raise BadProposal('Indica qué se aprueba: «approve» con «models» y «variants».')
    out = []
    for field in ('models', 'variants'):
        value = approve.get(field) or []
        if not isinstance(value, list) or any(not isinstance(v, str) or len(v) > 400 for v in value):
            raise BadProposal(f'«{field}» es una lista de referencias.')
        out.append(set(value))
    return out[0], out[1]


def _prices(prices, plan) -> dict:
    if prices in (None, {}):
        return {}
    if not isinstance(prices, dict):
        raise BadProposal('«prices» es un objeto referencia -> precio.')
    out = {}
    for ref, value in prices.items():
        if ref not in plan.variants:
            raise BadProposal('Precio de una variante que no está en la lista.', code='unknown_ref')
        try:
            price = Decimal(str(value)).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            raise BadProposal('Precio inválido.') from None
        if not 0 < price <= MAX_PRICE:
            raise BadProposal('El precio debe ser mayor que 0.')
        out[ref] = price
    return out


def _fingerprint(rows, approve, prices, category_id) -> str:
    models, variants = approve
    body = json.dumps({
        'rows': [[r['key'], r['model'], r['capacity'], r['colour'], r['line'], r['has_imei'],
                  str(r['price'])] for r in rows],
        'models': sorted(models), 'variants': sorted(variants),
        'prices': sorted((k, str(v)) for k, v in prices.items()), 'category': category_id,
    }, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()


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


def apply(*, company, rows, approve, prices=None, category_id=None, idempotency_key='', actor, request=None) -> dict:
    """Create what was approved. Returns the answer body; raises `BadProposal` / `CatalogError`."""
    rows = clean_rows(rows)
    models_ok, variants_ok = _refs(approve)
    key = ' '.join(str(idempotency_key or '').split())[:120]
    with transaction.atomic():
        # One approval at a time per company: the plan below is read AFTER this lock.
        Company.objects.select_for_update().get(pk=company.pk)
        category = None
        if category_id not in (None, ''):
            category = Category.objects.filter(company=company, pk=category_id).first()
            if category is None:
                raise device_catalog.CatalogError('Categoría no encontrada.', field='category', code='not_found')
        plan = _Plan(company, rows)
        price_for = _prices(prices, plan)
        fingerprint = _fingerprint(rows, (models_ok, variants_ok), price_for, category.pk if category else None)
        if key:
            prior = AdminAuditLog.objects.filter(
                company=company, action='catalog_proposal_applied', metadata__idempotency_key=key).first()
            if prior is not None:
                if prior.metadata.get('fingerprint') != fingerprint:
                    raise BadProposal('Esa clave de idempotencia ya se usó con otra solicitud.',
                                      code='idempotency_conflict', status=409)
                return {**prior.metadata['result'], 'replayed': True}
        for ref in models_ok:
            if ref not in plan.models:
                raise BadProposal('Una referencia de modelo no está en la lista.', code='unknown_ref')
        for ref in variants_ok:
            if ref not in plan.variants:
                raise BadProposal('Una referencia de variante no está en la lista.', code='unknown_ref')
            model = plan.models[plan.variants[ref]['model_ref']]
            if model['status'] == 'missing' and model['ref'] not in models_ok:
                raise BadProposal(f'La variante «{plan.variants[ref]["suggested_name"]}» necesita que se apruebe '
                                  f'el modelo «{model["name"]}».', code='model_not_approved')

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
