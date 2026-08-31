"""
Phase 2E, step 2 of 2 — give every company its series, and attach the history.

THE ONE RULE THIS MIGRATION OBEYS
---------------------------------
NO ISSUED NUMBER CHANGES. Not one character. `NV-000001`, `NV-000002`,
`NV-000015` come out of this migration exactly as they went in, gaps included.

The gaps are the interesting part. Before Phase 2E numbering was global, so a
company that issued the 1st, 2nd and 15th notes on the installation legitimately
holds NV-000001, NV-000002 and NV-000015 — the missing twelve belong to other
companies. It is tempting to "tidy" that into 1, 2, 3. That would rewrite
documents customers and auditors are holding, to fix a cosmetic problem that is
not a problem: an internal correlativo is allowed to have gaps, and is not
allowed to change.

WHAT IT WRITES
--------------
  InternalSequence   one company-level series per company that has any note, or
                     that lacks one (every company ends up with exactly one).
  SalesNote.sequence         the series each note now belongs to.
  SalesNote.sequence_value   its ordinal, parsed from the historical number.
  InternalSequence.next_value  one past the highest ordinal that company holds,
                     so the next issuance cannot reuse a number already out.

WHY EVERY LEGACY NOTE GOES TO A COMPANY-LEVEL SERIES
----------------------------------------------------
Even for a company that later chooses branch scope. The old numbering WAS
company-wide (in fact installation-wide); pretending those notes had always
belonged to a per-branch series would invent a history that never happened, and
would scatter one continuous run of numbers across several series with
overlapping ordinals. The history stays in the series it actually came from;
future branch series start fresh alongside it.

MALFORMED LEGACY NUMBERS
------------------------
A number that does not match `<prefix><digits>` — `NV-ABC`, `MANUAL-1`, anything
hand-edited — keeps its string and gets `sequence_value = NULL`. It is attached
to its company's series so it is still findable, but it contributes NO ordinal.

The alternative, assigning it a made-up ordinal, either collides with a real one
or asserts an order that was never recorded. `next_value` is computed only from
numbers that actually parse, which is the safe direction: a value too high wastes
a number, a value too low reissues one.
"""

import re

from django.db import migrations

# What a company with no notes at all starts with. Kept as a literal here rather
# than imported from store.sequences: a migration has to keep doing what it did
# on the day it ran, even after the module's defaults change.
_DEFAULT_PREFIX = 'NV-'
_DEFAULT_PADDING = 6
_DOCUMENT_SALES_NOTE = 'sales_note'

# `NV-000042` → 42. Anything else → None.
_NUMBER_RE = re.compile(r'^(?P<prefix>[A-Za-z0-9_-]*?)(?P<digits>\d+)$')


def _parse_ordinal(number: str, prefix: str):
    """
    The ordinal inside a historical number, or None.

    Deliberately forgiving about the prefix — a company may have been created
    before anybody thought about prefixes — but strict about the digits: the
    trailing run of digits IS the ordinal, and if there is none there is no
    ordinal to recover.
    """
    if not number:
        return None
    text = str(number)
    if prefix and text.startswith(prefix):
        text = text[len(prefix):]
        return int(text) if text.isdigit() else None
    match = _NUMBER_RE.match(text)
    if match is None:
        return None
    return int(match.group('digits'))


def _infer_prefix(numbers):
    """
    The prefix a company was using, taken from its own history.

    Reading it from the notes rather than assuming `NV-` means a company that had
    been renumbered by hand keeps issuing in the style it already uses. Falls
    back to the default when there is nothing to learn from.
    """
    prefixes = set()
    for number in numbers:
        match = _NUMBER_RE.match(str(number or ''))
        if match is not None:
            prefixes.add(match.group('prefix'))
    if len(prefixes) == 1:
        return prefixes.pop()
    # Mixed or unreadable: do not guess a house style that never existed.
    return _DEFAULT_PREFIX


def _infer_padding(numbers, prefix):
    """The digit width already in use, so new numbers line up with the old."""
    widths = set()
    for number in numbers:
        text = str(number or '')
        if prefix and text.startswith(prefix):
            text = text[len(prefix):]
        if text.isdigit():
            widths.add(len(text))
    if len(widths) == 1:
        width = widths.pop()
        if 1 <= width <= 12:
            return width
    return _DEFAULT_PADDING


def create_sequences_and_attach_notes(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    InternalSequence = apps.get_model('store', 'InternalSequence')
    SalesNote = apps.get_model('store', 'SalesNote')

    notes_by_company = {}
    for note in SalesNote.objects.select_related('order').iterator():
        company_id = getattr(note.order, 'company_id', None)
        if company_id is None:
            # Cannot happen after Phase 2C made Order.company required, but a
            # backfill that assumes its predecessor ran is how data gets lost.
            continue
        notes_by_company.setdefault(company_id, []).append(note)

    for company in Company.objects.all().iterator():
        notes = notes_by_company.get(company.pk, [])
        numbers = [n.number for n in notes]

        prefix = _infer_prefix(numbers) if numbers else _DEFAULT_PREFIX
        padding = _infer_padding(numbers, prefix) if numbers else _DEFAULT_PADDING

        sequence, _created = InternalSequence.objects.get_or_create(
            company=company,
            branch=None,
            document_type=_DOCUMENT_SALES_NOTE,
            defaults={'prefix': prefix, 'padding': padding, 'next_value': 1},
        )

        highest = 0
        for note in notes:
            ordinal = _parse_ordinal(note.number, sequence.prefix)
            note.sequence_id = sequence.pk
            note.sequence_value = ordinal
            # `number` is deliberately NOT in update_fields. The issued string is
            # the one thing this migration must not touch.
            SalesNote.objects.filter(pk=note.pk).update(
                sequence_id=sequence.pk, sequence_value=ordinal,
            )
            if ordinal is not None and ordinal > highest:
                highest = ordinal

        # One past the highest ordinal actually issued. Never below it: a
        # counter that rewinds hands out a number somebody already has on paper.
        next_value = max(highest + 1, sequence.next_value, 1)
        if next_value != sequence.next_value:
            InternalSequence.objects.filter(pk=sequence.pk).update(next_value=next_value)


def unmigrate(apps, schema_editor):
    """
    Reverse: detach the notes and drop the series this migration created.

    `SalesNote.number` is untouched — it was never written by this migration, and
    it is what the previous version of the code reads. Note that 0029 refuses to
    reverse past this point, so the schema (and therefore the removed global
    unique) stays; this exists so the DATA half can be undone and re-run.
    """
    InternalSequence = apps.get_model('store', 'InternalSequence')
    SalesNote = apps.get_model('store', 'SalesNote')

    SalesNote.objects.update(sequence_id=None, sequence_value=None)
    InternalSequence.objects.all().delete()


def verify_no_duplicate_ordinals(apps, schema_editor):
    """
    Refuse to finish if any series ended up with two notes on one ordinal.

    The constraint added in 0029 would catch this on the next write, which is
    later and further from the cause. Failing here names the series while the
    operator is still looking at the migration output.
    """
    from django.db.models import Count

    SalesNote = apps.get_model('store', 'SalesNote')

    clashes = (
        SalesNote.objects
        .filter(sequence__isnull=False, sequence_value__isnull=False)
        .values('sequence_id', 'sequence_value')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
    )
    offenders = list(clashes[:20])
    if offenders:
        raise RuntimeError(
            'Dos notas de venta comparten el mismo ordinal dentro de una misma '
            'serie, así que la numeración histórica no era única dentro de su '
            f'empresa: {offenders}\\n\\n'
            'La migración se detiene en lugar de elegir cuál conserva el número. '
            'Corrija los números duplicados y vuelva a ejecutarla.'
        )


def noop(apps, schema_editor):
    """Nothing to undo: the verification writes nothing."""


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0029_internal_sequences'),
    ]

    operations = [
        migrations.RunPython(create_sequences_and_attach_notes, unmigrate),
        migrations.RunPython(verify_no_duplicate_ordinals, noop),
    ]
