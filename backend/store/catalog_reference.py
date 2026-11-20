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
LABEL_MAX = 120  # ReferenceOption.label_en / label_es

HEADER_FIELDS = {'schema_version', 'manufacturer', 'family', 'dataset_version', 'official_source_hosts', 'models',
                 'notes'}
MODEL_FIELDS = {'key', 'name', 'line', 'generation', 'announced', 'capacities_gb', 'colors', 'sim',
                'model_numbers', 'sources', 'verified_at', 'completeness', 'missing', 'notes'}
COLOUR_FIELDS = {'key', 'label_en', 'label_es', 'official'}
#: `label_es` / `region_label_es` (optional, PR B): how the SIM configuration and the region group read in Spanish
#: for staff and customers. One key reads alike in every model of the file (`_label_errors`).
SIM_FIELDS = {'key', 'regions', 'config', 'label_es', 'region_label_es'}
NUMBER_FIELDS = {'number', 'regions'}

_SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


def load(path):
    """Read a reference file. Invalid JSON or text raises: CI must fail loudly on a broken file."""
    with open(Path(path), encoding='utf-8') as fh:
        return json.load(fh)


def _text(value):
    return isinstance(value, str) and value.strip() != ''


def _slug(value):
    return isinstance(value, str) and _SLUG.fullmatch(value) is not None


def _iso(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _official(url, hosts):
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    if parts.scheme != 'https' or not host:
        return False
    return any(host == h or host.endswith('.' + h) for h in hosts)


def _unknown(where, item, allowed, errors):
    for field in sorted(set(item) - allowed):
        errors.append(f'{where}: unknown field {field!r}')


def _duplicates(values):
    """Repeated values among the hashable texts only; wrong types are reported elsewhere."""
    return [v for v, n in Counter(v for v in values if isinstance(v, (str, tuple))).items() if n > 1]


def _entries(where, m, field, errors):
    """The dict entries of a list field; anything else is an error, never an exception."""
    value = m.get(field)
    if not isinstance(value, list):
        errors.append(f'{where}: {field} must be a list')
        return []
    good = [e for e in value if isinstance(e, dict)]
    if len(good) != len(value):
        errors.append(f'{where}: every {field} entry must be an object')
    return good


def _model_errors(m, hosts):
    key = m.get('key')
    where = f"model {key if isinstance(key, str) else '?'!r}"
    errors = []
    _unknown(where, m, MODEL_FIELDS, errors)
    if not _slug(key):
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
    if not (isinstance(caps, list) and all(type(c) is int and c > 0 for c in caps) and caps == sorted(set(caps))):
        errors.append(f'{where}: capacities_gb must be positive integers, unique and ascending')
        caps = []

    colours = _entries(where, m, 'colors', errors)
    for c in colours:
        _unknown(f'{where} colour', c, COLOUR_FIELDS, errors)
        if not _slug(c.get('key')):
            errors.append(f'{where}: colour key must be a lowercase slug')
        for label in ('label_en', 'label_es'):
            if not _text(c.get(label)):
                errors.append(f'{where}: colour {c.get("key")!r} needs {label}')
            elif len(c[label]) > LABEL_MAX:
                errors.append(f'{where}: colour {c.get("key")!r} {label} is longer than {LABEL_MAX} characters')
        official = c.get('official')
        if not (isinstance(official, dict) and _text(official.get('en'))
                and all(official.get(k) is None or isinstance(official.get(k), str) for k in ('es_419', 'es_es'))
                and set(official) <= {'en', 'es_419', 'es_es'}):
            errors.append(f'{where}: colour {c.get("key")!r} needs official {{en, es_419, es_es}} strings')
    for dup in _duplicates(c.get('key') for c in colours):
        errors.append(f'{where}: duplicate colour {dup!r}')

    sims = _entries(where, m, 'sim', errors)
    for s in sims:
        _unknown(f'{where} sim', s, SIM_FIELDS, errors)
        if not (_slug(s.get('key')) and _text(s.get('config')) and _text(s.get('regions'))):
            errors.append(f'{where}: each sim entry needs a slug key, regions and config')
    for s in sims:
        for field in ('label_es', 'region_label_es'):
            if field in s and not _text(s.get(field)):
                errors.append(f'{where}: sim {s.get("key")!r} {field} must be non-empty text when present')
    for dup in _duplicates((s.get('key'), s.get('regions')) for s in sims
                           if isinstance(s.get('key'), str) and isinstance(s.get('regions'), str)):
        errors.append(f'{where}: duplicate sim entry {dup!r}')

    numbers = _entries(where, m, 'model_numbers', errors)
    for n in numbers:
        _unknown(f'{where} model number', n, NUMBER_FIELDS, errors)
        if not _text(n.get('number')):
            errors.append(f'{where}: each model number needs number')
        if n.get('regions') is not None and not _text(n.get('regions')):
            errors.append(f'{where}: model number regions must be text or null')

    sources = m.get('sources')
    if not (isinstance(sources, list) and sources):
        errors.append(f'{where}: sources are required')
    else:
        for url in sources:
            if not _official(url, hosts):
                errors.append(f'{where}: source {url!r} is not an https page of an official host')

    notes = m.get('notes')
    if notes is not None and not (isinstance(notes, str) or (isinstance(notes, list) and all(_text(x) for x in notes))):
        errors.append(f'{where}: notes must be a text or a list of texts')

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
    """Every problem found in a reference file, as texts; [] when it is valid. Never raises, never changes `data`."""
    if not isinstance(data, dict):
        return ['the file must hold a JSON object']
    errors = []
    _unknown('header', data, HEADER_FIELDS, errors)
    version = data.get('schema_version')
    if type(version) is not int or version != SCHEMA_VERSION:
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

    good = [m for m in models if isinstance(m, dict)]
    if len(good) != len(models):
        errors.append('each model must be an object')
    for m in good:
        errors.extend(_model_errors(m, hosts))
    for field in ('key', 'name'):
        for dup in _duplicates(m.get(field) for m in good):
            errors.append(f'duplicate {field} {dup!r}')
    numbers = [n.get('number') for m in good if isinstance(m.get('model_numbers'), list)
               for n in m['model_numbers'] if isinstance(n, dict)]
    for dup in _duplicates(numbers):
        errors.append(f'model number {dup!r} appears more than once')
    errors.extend(_label_errors(good))
    return errors


def _label_errors(models):
    """
    One key, one Spanish label in the WHOLE file (review of #371): a company's option is shared by key across models,
    so a colour key, a SIM key or a region group (grouped by `region_key`, exactly as the loader derives it) that reads
    differently in two models would take whichever came first. Only labels that are present and are text count.
    """
    labels = {}

    def add(kind, key, field, value):
        if isinstance(key, str) and _text(value):
            labels.setdefault((kind, key, field), set()).add(value)

    for m in models:
        for c in (m.get('colors') if isinstance(m.get('colors'), list) else []):
            if isinstance(c, dict):
                add('colour', c.get('key'), 'label_es', c.get('label_es'))
        for e in (m.get('sim') if isinstance(m.get('sim'), list) else []):
            if isinstance(e, dict):
                add('sim', e.get('key'), 'label_es', e.get('label_es'))
                if isinstance(e.get('regions'), str):
                    add('region group', region_key(e['regions']), 'region_label_es', e.get('region_label_es'))
    return [f'{kind} {key!r} has more than one {field}: {", ".join(sorted(values))}'
            for (kind, key, field), values in sorted(labels.items()) if len(values) > 1]


def summary(data):
    """Counts of a file that already passed `validate`."""
    models = data.get('models') or []
    return {
        'manufacturer': data.get('manufacturer'), 'family': data.get('family'),
        'dataset_version': data.get('dataset_version'), 'models': len(models),
        'by_completeness': dict(Counter(m.get('completeness') for m in models)),
        'by_line': dict(Counter(m.get('line') for m in models)),
    }


# ---------------------------------------------------------------------------
# PR B — what a model of a VALID file becomes in the platform tables (pure; no database)
# ---------------------------------------------------------------------------
#
# Role vocabulary: `VariantAttribute.ROLE_*` (capacity, color, sim, region), written here as plain strings so this
# module stays free of the ORM. SIM and region (ADR §4, PR B decision): the file has no region list of its own;
# each `sim` entry says which configuration exists in which regions, in the maker's words. So:
#   * one SIM option per distinct `sim.key` of the model (Spanish label = the entry's `label_es`, else the key in
#     words; the exact configuration texts go to `official`);
#   * one REGION option per distinct region group of the model: the entry's `regions` text up to its first
#     parenthesis (the rest is a citation), with a key derived from it (`region_key`), the full texts in `official`
#     and the Spanish label from the entry's `region_label_es` when the file gives it;
#   * each `sim` entry is one SIM × region pair that exists (`pairs_of`); every other pair of the model is impossible.
# Model numbers keep their own `regions` text, for reference only (the file does not tie a number to a SIM entry).

LABEL_MAX = 60
KEY_MAX = 64


def _slugify(text) -> str:
    import unicodedata

    folded = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', folded).strip('-')


def capacity_key(gb: int) -> str:
    return f'{gb}gb'


def capacity_label(gb: int) -> str:
    return f'{gb // 1024} TB' if gb >= 1024 and gb % 1024 == 0 else f'{gb} GB'


def sim_label(key: str) -> str:
    """The file gives no short name for a SIM configuration: its key, in words («nano sim esim»)."""
    return ' '.join(key.split('-'))


def region_text(regions: str) -> str:
    """The region group as the file names it: the text before its first parenthesis (the rest cites a source)."""
    return ' '.join(str(regions).split('(', 1)[0].split()).rstrip(' ,;:')


def region_key(regions: str) -> str:
    """A stable key for a region group: its slug, cut to 64 with a hash of the whole text when longer."""
    import hashlib

    text = region_text(regions)
    slug = _slugify(text) or 'region'
    if len(slug) <= KEY_MAX:
        return slug
    digest = hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]
    return f'{slug[:KEY_MAX - 9].rstrip("-")}-{digest}'


def region_label(regions: str) -> str:
    """The region group as a label of at most 60 characters (cut at a comma, with «…», when longer)."""
    text = region_text(regions)
    if len(text) <= LABEL_MAX:
        return text
    cut = text[:LABEL_MAX - 1]
    if ', ' in cut:
        cut = cut.rsplit(', ', 1)[0]
    return cut.rstrip(' ,') + '…'


def options_of(model) -> list[dict]:
    """
    Every option of one model of a valid file: {role, key, label_en, label_es, official, capacity_gb, sort_order},
    in reading order (capacities, colours, SIM, regions; each in the file's order).
    """
    out = []
    for n, gb in enumerate(model.get('capacities_gb') or []):
        label = capacity_label(gb)
        out.append({'role': 'capacity', 'key': capacity_key(gb), 'label_en': label, 'label_es': label,
                    'official': {}, 'capacity_gb': gb, 'sort_order': n})
    for n, colour in enumerate(model.get('colors') or []):
        out.append({'role': 'color', 'key': colour['key'], 'label_en': colour['label_en'],
                    'label_es': colour['label_es'], 'official': dict(colour.get('official') or {}),
                    'capacity_gb': None, 'sort_order': n})
    sims, regions, sim_es, region_es = {}, {}, {}, {}
    for entry in model.get('sim') or []:
        sims.setdefault(entry['key'], []).append(entry['config'])
        group = region_key(entry['regions'])
        regions.setdefault(group, []).append(entry['regions'])
        if entry.get('label_es'):
            sim_es.setdefault(entry['key'], entry['label_es'].strip())
        if entry.get('region_label_es'):
            region_es.setdefault(group, entry['region_label_es'].strip())
    for n, (key, configs) in enumerate(sims.items()):
        label = sim_label(key)
        out.append({'role': 'sim', 'key': key, 'label_en': label, 'label_es': sim_es.get(key, label)[:LABEL_MAX],
                    'official': {'config': list(dict.fromkeys(configs))}, 'capacity_gb': None, 'sort_order': n})
    for n, (key, texts) in enumerate(regions.items()):
        label = region_label(texts[0])
        out.append({'role': 'region', 'key': key, 'label_en': label, 'label_es': region_es.get(key, label)[:LABEL_MAX],
                    'official': {'regions': list(dict.fromkeys(texts))}, 'capacity_gb': None, 'sort_order': n})
    return out


def pairs_of(model) -> set[tuple[str, str]]:
    """The SIM × region pairs that exist for one model: one per `sim` entry, as (sim key, region key)."""
    return {(entry['key'], region_key(entry['regions'])) for entry in model.get('sim') or []}
