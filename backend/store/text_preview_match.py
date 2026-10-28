"""
INV-TEXT-02 — which REAL variant of this company a recognized candidate is.

Read-only: one product, several to choose from (never picked for the person),
or none and why. The same rules as the weekly file's model columns
(`device_catalog.resolve_variant_spec` / `find_variant`): model by its exact
name, options by their labels, the line (new or used) apart. Accessories by
their product name. Costs, quantities and identifiers are not part of it.
"""
import re
import unicodedata

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
    # Composed (NFC): a name typed decomposed is the same name, and finds the catalogue's.
    return ' '.join(unicodedata.normalize('NFC', value).split())


_UNIT = re.compile(r'\d{1,4}(?:gb|tb|w|mm|m|mah)', re.I)
_LABEL = re.compile(r'\b(?:imei[12]?|s/n|n/s|serial)\b', re.I)
_SERIAL_LABEL = re.compile(
    r'(?<![A-Za-z])(n\.?s\.?|sn|serie)(?![A-Za-z])\s*[:#=]?\s*([A-Za-z0-9][A-Za-z0-9/-]{3,39})', re.I)


def _digit_run_is_identifier(run: str) -> bool:
    """Same rule as the browser: a group of 7+, 12+ digits in all, or a phone (9 digits starting with 9)."""
    groups = re.split(r'[ .,/-]', run)
    digits = ''.join(groups)
    return any(len(g) >= 7 for g in groups) or len(digits) >= 12 \
        or (len(digits) == 9 and digits.startswith('9') and all(len(g) >= 2 for g in groups))


def _looks_like_code(token: str) -> bool:
    bare = token.replace('-', '')
    return bool(re.fullmatch(r'[A-Za-z0-9]{8,}', bare)) and any(c.isdigit() for c in bare) \
        and sum(c.isalpha() for c in bare) >= 2 and not _UNIT.fullmatch(token) \
        and not re.fullmatch(r'[A-Za-z]+-\d{1,2}', token)


def has_identifier(model: str) -> bool:
    """
    Pre-merge review: an IMEI, serial, phone or document hidden in the model. The same rules as the browser's
    `identifierTokens`/`looksLikeIdentifier`, so a real name («Funda Snoopy», «Cable Thunderbolt-4», «iPad Air
    10.9 2022», «Apple Watch Serie 45mm») is searched and an identifier is not.
    """
    if _LABEL.search(model):
        return True
    for label, value in _SERIAL_LABEL.findall(model):
        # A unit after «serie» is a name («Serie 45mm»); after «SN»/«N.S.» it is still a serial.
        if not (label.lower() == 'serie' and _UNIT.fullmatch(value)):
            return True
    if any(_digit_run_is_identifier(run) for run in re.findall(r'\d+(?:[ .,/-]\d+)*', model)):
        return True
    return any(_looks_like_code(token) for token in model.split())


IDENTIFIER_REASON = 'El modelo lleva un IMEI, una serie u otro identificador: quítalo para buscar.'


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
    model = _text(candidate.get('model'), 120)
    return {'key': key, 'kind': kind, 'line': line, 'capacity': capacity,
            # An identifier hidden in an allowed field: that row is not searched, and never echoed back.
            'identifier': has_identifier(model), 'model': model, 'colour': _text(candidate.get('colour'), 60)}


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
        if c['identifier']:
            # One row like this never fails the whole list (review: «Funda Snoopy» used to 400 every row).
            out[c['key']] = {'status': 'none', 'reason': IDENTIFIER_REASON, 'options': []}
            continue
        accessory = c['kind'] == 'accessory' or c['line'] == 'accessory'
        signature = (accessory, c['model'].lower(), c['capacity'], c['colour'].lower(), c['line'])
        if signature not in seen:
            seen[signature] = match_accessory(company, c) if accessory else match_device(company, c)
        out[c['key']] = seen[signature]
    return out
