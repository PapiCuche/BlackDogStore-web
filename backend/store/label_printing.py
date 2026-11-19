"""
LABEL-04 — a label batch on the label printer, through the print agent.

A batch (made when goods come in, `label_batches`) becomes the SAME stickers the panel and the PDF
draw (`label_views.build_label_plans`), grouped by roll size: one print job per size, because a printer
has one roll loaded. Only ready labels print; the commercial device label never shows an IMEI or a
serial here (no one asked for the sensitive one).
"""
from __future__ import annotations

from . import labels
from .labels import tspl
from .models import CompanyLabelSettings

#: More stickers than this in one batch never print on their own: a big import waits for a person
#: (it would take a whole roll on its own).
AUTO_LABEL_LIMIT = 100


def _options(company) -> labels.DeviceOptions:
    profile = (CompanyLabelSettings.objects.filter(company=company)
               .values_list('device_symbol', flat=True).first())
    return labels.DeviceOptions(profile=profile or labels.DeviceOptions().profile, sensitive_allowed=False)


def batch_stickers(batch) -> dict[str, list]:
    """{'30x20': [sticker, …]} — every ready sticker of the batch, with its copies, by roll size."""
    from .label_views import build_label_plans

    wanted: dict[int, int] = {}
    unit_ids: list[int] = []
    for item in batch.items.filter(needs_review=False).order_by('pk'):
        if item.unit_id:
            unit_ids.append(item.unit_id)
        else:
            wanted[item.product_id] = wanted.get(item.product_id, 0) + item.copies
    if not wanted and not unit_ids:
        return {}
    plans, _identifiers = build_label_plans(
        batch.company, batch.branch, list(wanted.items()), unit_ids, _options(batch.company))
    by_size: dict[str, list] = {}
    for plan in plans:
        if not plan.ready:
            continue
        for _copy in range(plan.copies):
            for sticker in plan.stickers:
                by_size.setdefault(tspl.size_key(sticker.width_mm, sticker.height_mm), []).append(sticker)
    return by_size


def render_job(job) -> bytes:
    """The TSPL of a label print job: the stickers of its batch of ITS size, nothing else."""
    stickers = batch_stickers(job.label_batch).get(job.label_size, [])
    if not stickers:
        raise ValueError(f'El lote {job.label_batch_id} ya no tiene etiquetas de {job.label_size} mm listas.')
    return tspl.render(stickers)
