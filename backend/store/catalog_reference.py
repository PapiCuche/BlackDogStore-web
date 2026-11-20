"""CATALOG-REFERENCE-01: the manufacturers' reference catalog (docs/adr-catalog-reference.md).

The files in `backend/catalog_reference/` are factual data checked against the maker's official pages:
models, capacities, colours, SIM configurations and model numbers. No price, stock, image or
commercial text. This module only reads and checks them; it never touches the database and names
no maker, model or colour (AGENTS §2): every name lives in the data file.
"""
import datetime
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
LINES = ('standard', 'plus', 'pro', 'pro_max', 'mini', 'se', 'air', 'e', 'original', 'other')
COMPLETENESS = ('complete', 'partial', 'pending_review')

HEADER_FIELDS = {'schema_version', 'manufacturer', 'family', 'dataset_version', 'official_source_hosts', 'models',
                 'notes'}
MODEL_FIELDS = {'key', 'name', 'line', 'generation', 'announced', 'capacities_gb', 'colors', 'sim',
                'model_numbers', 'sources', 'verified_at', 'completeness', 'missing', 'notes'}
COLOUR_FIELDS = {'key', 'label_en', 'label_es', 'official'}
SIM_FIELDS = {'key', 'regions', 'config'}
NUMBER_FIELDS = {'number', 'regions'}

_SLUG = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


def load(path):
    with open(Path(path), encoding='utf-8') as fh:
        return json.load(fh)


def _text(value):
    return isinstance(value, str) and value.strip() != ''


def _iso(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _official(url, hosts):
    parts = urlsplit(url) if isinstance(url, str) else None
    if not parts or parts.scheme != 'https' or not parts.hostname:
        return False
    return any(parts.hostname == h or parts.hostname.endswith('.' + h) for h in hosts)


def _unknown(where, item, allowed, errors):
    for field in sorted(set(item) - allowed):
        errors.append(f'{where}: unknown field {field!r}')


def _model_errors(m, hosts):
    where = f"model {m.get('key', '?')!r}"
    errors = []
    _unknown(where, m, MODEL_FIELDS, errors)
    if not (isinstance(m.get('key'), str) and _SLUG.match(m['key'])):
        errors.append(f'{where}: key must be a lowercase slug')
    if not _text(m.get('name')):
        errors.append(f'{where}: name is required')
    if m.get('line') not in LINES:
        errors.append(f'{where}: line must be one of {", ".join(LINES)}')
    if not _text(m.get('generation')):
        errors.append(f'{where}: generation is required')
    if m.get('announced') is not None and not _iso(m.get('announced')):
        errors.append(f'{where}: announced must be YYYY-MM-DD or null')
    if not _iso(m.get('verified_at')):
        errors.append(f'{where}: verified_at must be YYYY-MM-DD')

    caps = m.get('capacities_gb')
    if not (isinstance(caps, list) and all(isinstance(c, int) and not isinstance(c, bool) and c > 0 for c in caps)
            and caps == sorted(set(caps))):
        errors.append(f'{where}: capacities_gb must be positive integers, unique and ascending')
        caps = []

    colours = m.get('colors') if isinstance(m.get('colors'), list) else []
    for c in colours:
        _unknown(f'{where} colour', c, COLOUR_FIELDS, errors)
        if not (isinstance(c.get('key'), str) and _SLUG.match(c['key'])):
            errors.append(f'{where}: colour key must be a lowercase slug')
        for label in ('label_en', 'label_es'):
            if not _text(c.get(label)):
                errors.append(f'{where}: colour {c.get("key")!r} needs {label}')
    for key, n in Counter(c.get('key') for c in colours).items():
        if n > 1:
            errors.append(f'{where}: duplicate colour {key!r}')

    sims = m.get('sim') if isinstance(m.get('sim'), list) else []
    for s in sims:
        _unknown(f'{where} sim', s, SIM_FIELDS, errors)
        if not (isinstance(s.get('key'), str) and _SLUG.match(s['key'])) or not _text(s.get('config')) \
                or not _text(s.get('regions')):
            errors.append(f'{where}: each sim entry needs key, regions and config')
    for key, n in Counter((s.get('key'), s.get('regions')) for s in sims).items():
        if n > 1:
            errors.append(f'{where}: duplicate sim entry {key!r}')

    numbers = m.get('model_numbers') if isinstance(m.get('model_numbers'), list) else []
    for n in numbers:
        _unknown(f'{where} model number', n, NUMBER_FIELDS, errors)
        if not _text(n.get('number')):
            errors.append(f'{where}: each model number needs number')

    sources = m.get('sources')
    if not (isinstance(sources, list) and sources):
        errors.append(f'{where}: sources are required')
    else:
        for url in sources:
            if not _official(url, hosts):
                errors.append(f'{where}: source {url!r} is not an https page of an official host')

    missing = m.get('missing')
    if not isinstance(missing, list) or not all(_text(x) for x in missing):
        errors.append(f'{where}: missing must be a list of texts')
        missing = []
    completeness = m.get('completeness')
    if completeness not in COMPLETENESS:
        errors.append(f'{where}: completeness must be one of {", ".join(COMPLETENESS)}')
    elif completeness == 'complete':
        if missing:
            errors.append(f'{where}: complete model cannot list missing fields')
        if not (caps and colours and sims and numbers):
            errors.append(f'{where}: complete model needs capacities, colours, sim and model numbers')
    elif not missing:
        errors.append(f'{where}: a {completeness} model must say what is missing')
    return errors


def validate(data):
    """Every problem found in a reference file, as texts; [] when it is valid. Never changes `data`."""
    if not isinstance(data, dict):
        return ['the file must hold a JSON object']
    errors = []
    _unknown('header', data, HEADER_FIELDS, errors)
    if data.get('schema_version') != SCHEMA_VERSION:
        errors.append(f'schema_version must be {SCHEMA_VERSION}')
    for field in ('manufacturer', 'family', 'dataset_version'):
        if not _text(data.get(field)):
            errors.append(f'{field} is required')
    hosts = data.get('official_source_hosts')
    if not (isinstance(hosts, list) and hosts and all(_text(h) for h in hosts)):
        errors.append('official_source_hosts is required')
        hosts = []
    models = data.get('models')
    if not (isinstance(models, list) and models):
        return errors + ['models must be a non-empty list']

    for m in models:
        errors.extend(_model_errors(m, hosts) if isinstance(m, dict) else ['each model must be an object'])
    for field, label in (('key', 'key'), ('name', 'name')):
        for value, n in Counter(m.get(field) for m in models if isinstance(m, dict)).items():
            if n > 1:
                errors.append(f'duplicate {label} {value!r}')
    owners = Counter(n.get('number') for m in models if isinstance(m, dict)
                     for n in (m.get('model_numbers') or []) if isinstance(n, dict))
    for number, n in owners.items():
        if n > 1:
            errors.append(f'model number {number!r} appears more than once')
    return errors


def summary(data):
    models = data.get('models') or []
    return {
        'manufacturer': data.get('manufacturer'), 'family': data.get('family'),
        'dataset_version': data.get('dataset_version'), 'models': len(models),
        'by_completeness': dict(Counter(m.get('completeness') for m in models)),
        'by_line': dict(Counter(m.get('line') for m in models)),
    }
