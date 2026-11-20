"""
CATALOG-REFERENCE-01 · PR B — load a reference file into the PLATFORM tables (`docs/adr-catalog-reference.md` §4).

    python manage.py load_reference_catalog --file backend/catalog_reference/<maker>-<family>.json [--dry-run]

  * the file is checked first (`catalog_reference.validate`): an invalid file is refused whole, nothing written;
  * rows are matched by key (model `key`; option `role` + `key`; model number) and written only when a field
    differs, so loading the same file twice reports 0 changes;
  * NOTHING is deleted. A model, option or model number of the same maker and family that is no longer in the file
    gets `in_dataset=False` (the importer stops offering it) and is reported as a candidate to retire; a person
    decides. Coming back into the file sets it back;
  * `dry_run` runs the same code inside a transaction that is rolled back: the same report, nothing written;
  * the caller audits (`AdminAuditLog` without company: platform data, no tenant's).

No tenant row is read or written here: a company sees nothing of this until it imports (`catalog_reference_import`).
"""
import datetime

from django.db import transaction

from . import catalog_reference as ref
from .models import ReferenceModel, ReferenceModelNumber, ReferenceOption


class InvalidReference(ValueError):
    """The file does not validate; `errors` says why."""

    def __init__(self, errors):
        super().__init__('; '.join(errors[:20]))
        self.errors = list(errors)


class _Rollback(Exception):
    pass


def _date(value):
    return datetime.date.fromisoformat(value) if value else None


def _counter():
    return {'created': 0, 'updated': 0, 'unchanged': 0}


def _upsert(manager, lookup, values, counter):
    """Create or update one row by `lookup`; write only when a value differs. Returns the row."""
    row = manager.filter(**lookup).first()
    if row is None:
        row = manager.create(**lookup, **values)
        counter['created'] += 1
        return row
    changed = [field for field, value in values.items() if getattr(row, field) != value]
    if changed:
        for field in changed:
            setattr(row, field, values[field])
        row.save(update_fields=changed + (['updated_at'] if hasattr(row, 'updated_at') else []))
        counter['updated'] += 1
    else:
        counter['unchanged'] += 1
    return row


def _model_values(data, model, position):
    entries = [{'key': e['key'], 'regions': e['regions'], 'config': e['config'],
                'region_key': ref.region_key(e['regions'])} for e in model.get('sim') or []]
    return {
        'manufacturer': data['manufacturer'], 'family': data['family'], 'name': model['name'],
        'line': model['line'], 'generation': model['generation'], 'announced': _date(model.get('announced')),
        'sort_order': position, 'completeness': model['completeness'], 'missing': list(model.get('missing') or []),
        'sources': list(model['sources']), 'verified_at': _date(model['verified_at']),
        'dataset_version': data['dataset_version'], 'notes': model.get('notes') or '', 'sim_entries': entries,
        'in_dataset': True,
    }


def _load(data) -> dict:
    report = {'models': _counter(), 'options': _counter(), 'model_numbers': _counter(), 'retired': [],
              'retired_options': [], 'retired_model_numbers': []}
    seen_models = set()
    for position, model in enumerate(data['models']):
        row = _upsert(ReferenceModel.objects, {'key': model['key']}, _model_values(data, model, position),
                      report['models'])
        seen_models.add(row.pk)
        seen_options = set()
        for option in ref.options_of(model):
            values = {k: option[k] for k in ('label_en', 'label_es', 'official', 'capacity_gb', 'sort_order')}
            found = _upsert(ReferenceOption.objects, {'model': row, 'role': option['role'], 'key': option['key']},
                            {**values, 'in_dataset': True}, report['options'])
            seen_options.add(found.pk)
        for stale in ReferenceOption.objects.filter(model=row, in_dataset=True).exclude(pk__in=seen_options):
            stale.in_dataset = False
            stale.save(update_fields=['in_dataset'])
            report['retired_options'].append(f'{row.key}:{stale.role}:{stale.key}')
        seen_numbers = set()
        for n, number in enumerate(model.get('model_numbers') or []):
            found = _upsert(ReferenceModelNumber.objects, {'model': row, 'number': number['number']},
                            {'regions': number.get('regions') or '', 'sort_order': n, 'in_dataset': True},
                            report['model_numbers'])
            seen_numbers.add(found.pk)
        for stale in ReferenceModelNumber.objects.filter(model=row, in_dataset=True).exclude(pk__in=seen_numbers):
            stale.in_dataset = False
            stale.save(update_fields=['in_dataset'])
            report['retired_model_numbers'].append(f'{row.key}:{stale.number}')
    # Only this file's maker and family: another file never retires these rows.
    for stale in (ReferenceModel.objects.filter(manufacturer=data['manufacturer'], family=data['family'],
                                                in_dataset=True).exclude(pk__in=seen_models).order_by('key')):
        stale.in_dataset = False
        stale.save(update_fields=['in_dataset', 'updated_at'])
        report['retired'].append(stale.key)
    return report


def load(data, *, dry_run=False) -> dict:
    """Validate `data` (a parsed reference file) and write it; `InvalidReference` when it does not validate."""
    errors = ref.validate(data)
    if errors:
        raise InvalidReference(errors)
    result = {}
    try:
        with transaction.atomic():
            result = _load(data)
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    return {**result, 'dry_run': bool(dry_run), 'manufacturer': data['manufacturer'], 'family': data['family'],
            'dataset_version': data['dataset_version']}
