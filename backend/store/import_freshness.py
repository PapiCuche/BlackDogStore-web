"""
IMPORT-STALE-01 — whether a bulk-import preview may still be applied.

A preview is a photograph of the stock and the catalogue taken when the file
was staged. Applying it later writes what the operator saw THEN over whatever
happened since. Owner's decision A: an old preview, or one the shop has moved
away from, is refused, and the file is uploaded again.

TWO CHECKS, BOTH AT APPLY, BOTH UNDER THE JOB'S ROW LOCK
--------------------------------------------------------
    EXPIRY     `created_at` + `IMPORT_PREVIEW_TTL_SECONDS`. Every import kind.
               No new column: the job already records when it was staged.
    STALENESS  Per kind, against what the preview recorded (`normalized_data`):
               stock compares the shelf, read under its row lock, with
               `current_preview`; products compare `Product.updated_at` with
               the job's `created_at`. Units re-validate every identifier under
               lock already (`receive_units`), and supplier offers carry their
               own `stale` checks.

Both refusals are 409 Conflict with a machine-readable `code`, so the screen
offers «Subir el archivo otra vez» instead of a generic error.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

PREVIEW_EXPIRED = 'preview_expired'
PREVIEW_STALE = 'preview_stale'
#: The refusals answered with 409 and «Subir el archivo otra vez».
OUTDATED_CODES = frozenset({PREVIEW_EXPIRED, PREVIEW_STALE})

#: How many names a refusal spells out; the rest is a count.
NAMED = 5


def ttl() -> timedelta:
    return timedelta(seconds=settings.IMPORT_PREVIEW_TTL_SECONDS)


def is_expired(job, *, now=None) -> bool:
    if job.created_at is None:
        return False            # not saved yet: nothing to have aged
    return (now or timezone.now()) - job.created_at >= ttl()


def expired_message() -> str:
    minutes = max(1, settings.IMPORT_PREVIEW_TTL_SECONDS // 60)
    return (
        f'Esta vista previa caducó: tiene más de {minutes} minutos y el inventario o el '
        f'catálogo pueden haber cambiado desde entonces. No se aplicó nada. Sube el archivo '
        f'otra vez para revisarlo con los datos de ahora.'
    )


def refuse_if_expired(job, error_class) -> None:
    """Raise `error_class(message, code='preview_expired')` when the preview is past its TTL."""
    if is_expired(job):
        raise error_class(expired_message(), code=PREVIEW_EXPIRED)


def stale_message(names: list[str], *, what: str) -> str:
    """`what` completes «… cambió desde la vista previa en N producto(s)»: «El stock» / «El catálogo»."""
    shown = ', '.join(f'«{name or "sin nombre"}»' for name in names[:NAMED])
    more = '' if len(names) <= NAMED else f' y {len(names) - NAMED} más'
    return (
        f'{what} cambió desde la vista previa en {len(names)} producto(s): {shown}{more}. '
        f'No se aplicó nada. Sube el archivo otra vez para revisarlo con los datos de ahora.'
    )
