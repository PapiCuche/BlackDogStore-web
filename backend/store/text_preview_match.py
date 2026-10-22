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
ALLOWED_FIELDS = {'key', 'kind', 'model', 'capacityGb', 'ramGb', 'colour', 'sim', 'region', 'line'}
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


def _answer(products, reason_none) -> dict:
    products = list(products[:MAX_OPTIONS + 1])
    if not products:
        return {'status': 'none', 'reason': reason_none, 'options': []}
    status = 'exact' if len(products) == 1 else 'ambiguous'
    return {'status': status, 'reason': '' if status == 'exact' else 'Varias variantes coinciden: elige una.',
            'options': [_option_payload(product) for product in products[:MAX_OPTIONS]]}


def _capacity_label(company, gb):
    """The company's own label for that capacity («256 GB», «256GB», «1 TB»), or None."""
    from .models import VariantOption

    words = [f'{gb} GB', f'{gb}GB'] + ([f'{gb // 1024} TB', f'{gb // 1024}TB'] if gb % 1024 == 0 else [])
    option = VariantOption.objects.filter(company=company, label__iregex=r'^(%s)$' % '|'.join(words)).first()
    return option.label if option else None


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
        return _answer([exact], '')
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
        return _answer(exact, '')
    fitting = Product.objects.filter(company=company)
    for word in name.split():
        fitting = fitting.filter(name__icontains=word)
    found = _answer(fitting.order_by('name', 'pk'), 'No existe un producto con ese nombre.')
    if found['status'] == 'exact':  # a partial name is a suggestion, never a sure match
        found['status'], found['reason'] = 'ambiguous', 'Coincidencia parcial del nombre: confírmala.'
    return found


def match(company, candidates) -> dict:
    if not isinstance(candidates, list) or not candidates or len(candidates) > MAX_CANDIDATES:
        raise BadCandidate(f'Envía entre 1 y {MAX_CANDIDATES} candidatos.')
    cleaned = [clean(candidate) for candidate in candidates]
    return {c['key']: (match_accessory(company, c) if c['kind'] == 'accessory' or c['line'] == 'accessory'
                       else match_device(company, c)) for c in cleaned}
