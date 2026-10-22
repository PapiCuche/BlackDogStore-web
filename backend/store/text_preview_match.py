"""
INV-TEXT-02 — which REAL variant of this company a recognized candidate is.

Read-only: one product, several to choose from (never picked for the person),
or none and why. The same rules as the weekly file's model columns
(`device_catalog.resolve_variant_spec` / `find_variant`): model by its exact
name, options by their labels, the line (new or used) apart. Accessories by
their product name. Costs, quantities and identifiers are not part of it.
"""
from django.db.models import Count

from . import device_catalog
from .models import Product, ProductVariantOption

MAX_CANDIDATES = 300
MAX_OPTIONS = 5
#: Only what the match uses. RAM, SIM and region are not catalogue options yet: refused, not ignored.
ALLOWED_FIELDS = {'key', 'kind', 'model', 'capacityGb', 'colour', 'line'}
_LINES = {'new': Product.CONDITION_NEW, 'used': Product.CONDITION_USED}


class BadCandidate(ValueError):
    """A candidate that is not the shape this endpoint reads (400)."""


def _text(value, limit):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > limit:
        raise BadCandidate('Texto inválido.')
    return ' '.join(value.split())


def clean(candidate) -> dict:
    if not isinstance(candidate, dict):
        raise BadCandidate('Cada candidato es un objeto.')
    extra = set(candidate) - ALLOWED_FIELDS
    if extra:
        # Costs, quantities and identifiers never travel here.
        raise BadCandidate(f'Campos no admitidos: {", ".join(sorted(extra))[:80]}.')
    key = _text(candidate.get('key'), 40)
    if not key:
        raise BadCandidate('Falta «key».')
    capacity = candidate.get('capacityGb')
    if capacity is not None and (isinstance(capacity, bool) or not isinstance(capacity, int) or not 0 < capacity <= 8192):
        raise BadCandidate('Capacidad inválida.')
    kind = candidate.get('kind') or 'device'
    line = candidate.get('line') or 'new'
    if kind not in ('device', 'accessory') or line not in ('new', 'used', 'accessory'):
        raise BadCandidate('Tipo o línea inválidos.')
    return {'key': key, 'kind': kind, 'line': line, 'capacity': capacity,
            'model': _text(candidate.get('model'), 120), 'colour': _text(candidate.get('colour'), 60)}


def _option_payload(product) -> dict:
    return {'product_id': product.pk, 'name': product.name, 'condition_line': product.condition_line,
            'options': [option.label for option in device_catalog.variant_options_of(product)]}


def _answer(products, reason_none, *, exact=False) -> dict:
    """`exact` only for THE variant (same model, line and every option); anything that merely fits is to choose."""
    products = list(products[:MAX_OPTIONS + 1])
    if not products:
        return {'status': 'none', 'reason': reason_none, 'options': []}
    status = 'exact' if exact and len(products) == 1 else 'ambiguous'
    reason = '' if status == 'exact' else (
        'Varias variantes coinciden: elige una.' if len(products) > 1
        else 'Coincide en parte (faltan opciones en la lista): confírmala.')
    return {'status': status, 'reason': reason, 'options': [_option_payload(product) for product in products[:MAX_OPTIONS]]}


_CAPACITY_ATTRIBUTES = ('capacidad', 'almacenamiento', 'capacity', 'storage')


def _capacity_label(company, gb):
    """The company's own label for that capacity («256 GB», «256GB», «1 TB») in ITS capacity attribute, or None."""
    from .models import VariantOption

    words = [f'{gb} GB', f'{gb}GB'] + ([f'{gb // 1024} TB', f'{gb // 1024}TB'] if gb % 1024 == 0 else [])
    for option in (VariantOption.objects.filter(company=company).select_related('attribute')
                   .filter(attribute__name__iregex=r'^(capacidad|almacenamiento|capacity|storage)$')):
        if option.label.lower() in {word.lower() for word in words}:
            return option.label
    return None


def match_device(company, candidate) -> dict:
    labels = []
    if candidate['capacity']:
        label = _capacity_label(company, candidate['capacity'])
        if label is None:
            return {'status': 'none', 'reason': f'No existe la opción «{candidate["capacity"]} GB» en el catálogo.',
                    'options': []}
        labels.append(label)
    if candidate['colour']:
        labels.append(candidate['colour'])
    try:
        spec = device_catalog.resolve_variant_spec(
            company, model_name=candidate['model'],
            line_text='seminuevo' if candidate['line'] == 'used' else 'nuevo', labels=labels)
    except device_catalog.CatalogError as exc:
        return {'status': 'none', 'reason': str(exc), 'options': []}
    exact = device_catalog.find_variant(company, spec)
    if exact is not None:
        # Final review: without a capacity a device is never sure — an option-less product only «fits».
        return _answer([exact], '', exact=bool(candidate['capacity']))
    fitting = Product.objects.filter(company=company, product_model_id=spec['model_id'],
                                     condition_line=spec['condition_line'])
    if spec['option_ids']:
        holders = (ProductVariantOption.objects.filter(company_id=company.pk, option_id__in=spec['option_ids'])
                   .values('product_id').annotate(n=Count('option_id', distinct=True))
                   .filter(n=len(spec['option_ids'])).values('product_id'))
        fitting = fitting.filter(pk__in=holders)
    return _answer(fitting.order_by('name', 'pk'), 'No existe esa variante del modelo.')


def match_accessory(company, candidate) -> dict:
    name = candidate['model']
    if not name:
        return {'status': 'none', 'reason': 'Sin nombre de producto.', 'options': []}
    exact = Product.objects.filter(company=company, name__iexact=name).order_by('pk')
    if exact.exists():
        return _answer(exact, '', exact=True)
    fitting = Product.objects.filter(company=company)
    for word in name.split():
        fitting = fitting.filter(name__icontains=word)
    found = _answer(fitting.order_by('name', 'pk'), 'No existe un producto con ese nombre.')
    if len(found['options']) == 1:  # a partial name is a suggestion, never a sure match
        found['reason'] = 'Coincidencia parcial del nombre: confírmala.'
    return found


def match(company, candidates) -> dict:
    if not isinstance(candidates, list) or not candidates or len(candidates) > MAX_CANDIDATES:
        raise BadCandidate(f'Envía entre 1 y {MAX_CANDIDATES} candidatos.')
    cleaned = [clean(candidate) for candidate in candidates]
    if len({c['key'] for c in cleaned}) != len(cleaned):
        raise BadCandidate('Hay claves repetidas.')
    # The same model/options asked many times is looked up once (review: query cost per request).
    seen, out = {}, {}
    for c in cleaned:
        accessory = c['kind'] == 'accessory' or c['line'] == 'accessory'
        signature = (accessory, c['model'].lower(), c['capacity'], c['colour'].lower(), c['line'])
        if signature not in seen:
            seen[signature] = match_accessory(company, c) if accessory else match_device(company, c)
        out[c['key']] = seen[signature]
    return out
