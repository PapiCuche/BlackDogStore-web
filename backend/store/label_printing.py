"""
LABEL-04 — a label batch on the label printer, through the print agent.

A batch (made when goods come in, `label_batches`) becomes the SAME stickers the panel and the PDF
draw (`label_views.build_label_plans`), grouped by roll size: one print job per size, because a printer
has one roll loaded. Only ready labels print; the commercial device label never shows an IMEI or a
serial here (no one asked for the sensitive one).

Sizes (review of #367): the copies of a sticker are ONE bitmap printed n times, and a size with many
different stickers is cut into PARTS (`PrintJob.label_part`) of at most `JOB_BYTES`, under the agent's
1 MiB limit. The labels are COUNTED before anything is drawn, so a batch past a limit costs one query.
"""
from __future__ import annotations

from django.db.models import Sum

from . import labels
from .labels import tspl
from .models import CompanyLabelSettings, StockUnit

#: More stickers than this in one batch never print on their own: a big import waits for a person
#: (it would take a whole roll on its own).
AUTO_LABEL_LIMIT = 100
#: More than this is not sent from the panel at once (a roll is about 1000 labels).
MANUAL_LABEL_LIMIT = 1000
#: The most a job weighs: under the agent's `MAX_PAYLOAD_BYTES` (1 MiB), with room to spare.
JOB_BYTES = 600_000


class NothingToPrint(Exception):
    """The job has no ready sticker any more (sold, sent for review, a part past the end): cancel it."""


def _options(company) -> labels.DeviceOptions:
    profile = (CompanyLabelSettings.objects.filter(company=company)
               .values_list('device_symbol', flat=True).first())
    return labels.DeviceOptions(profile=profile or labels.DeviceOptions().profile, sensitive_allowed=False)


def label_count(batch) -> int:
    """How many stickers the batch asks for (its ready items' copies), without drawing any."""
    return batch.items.filter(needs_review=False).aggregate(total=Sum('copies'))['total'] or 0


def batch_groups(batch) -> dict[str, list]:
    """
    {'30x20': [(sticker, copies), …]} — every ready sticker of the batch, by roll size. A unit that is
    no longer in the batch's branch (moved, returned elsewhere) is left out, never an error.
    """
    from .label_views import build_label_plans

    wanted: dict[int, int] = {}
    unit_ids: list[int] = []
    for item in batch.items.filter(needs_review=False).order_by('pk'):
        if item.unit_id:
            unit_ids.append(item.unit_id)
        else:
            wanted[item.product_id] = wanted.get(item.product_id, 0) + item.copies
    if unit_ids:
        here = set(StockUnit.objects.filter(
            company=batch.company, branch=batch.branch, pk__in=unit_ids).values_list('pk', flat=True))
        unit_ids = [pk for pk in unit_ids if pk in here]
    if not wanted and not unit_ids:
        return {}
    plans, _identifiers = build_label_plans(
        batch.company, batch.branch, list(wanted.items()), unit_ids, _options(batch.company))
    by_size: dict[str, list] = {}
    for plan in plans:
        if not plan.ready:
            continue
        for sticker in plan.stickers:
            by_size.setdefault(tspl.size_key(sticker.width_mm, sticker.height_mm), []).append(
                (sticker, plan.copies))
    return by_size


def split_parts(groups, budget=None) -> list[list]:
    """The groups cut into parts of at most `budget` bytes; a sticker bigger than that goes alone."""
    budget = JOB_BYTES if budget is None else budget
    parts: list[list] = []
    weight = 0
    for group in groups:
        size = tspl.bitmap_bytes(group[0])
        if not parts or weight + size > budget:
            parts.append([])
            weight = 0
        parts[-1].append(group)
        weight += size
    return parts


def render_job(job) -> bytes:
    """The TSPL of a label print job: its part of the stickers of ITS size, nothing else."""
    parts = split_parts(batch_groups(job.label_batch).get(job.label_size, []))
    if job.label_part >= len(parts):
        raise NothingToPrint(f'El lote {job.label_batch_id} ya no tiene etiquetas de {job.label_size} mm listas.')
    return tspl.render(parts[job.label_part])
