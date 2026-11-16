"""
INV-DRAFT-01 — delete every draft of «Recibir mercadería» whose expiry has passed.

A draft holds serials and IMEI, so it is not kept past `RECEIPT_DRAFT_TTL_SECONDS` after its last save (24 h by
default). The screen never gets an expired one back even before this runs (it is deleted when read); this sweeps
the ones nobody came back for. Idempotent: a second run deletes nothing. It never touches stock, units, the Kardex
or a live draft.
"""
from django.core.management.base import BaseCommand

from store.receipt_drafts import purge


class Command(BaseCommand):
    help = 'Borra los borradores de «Recibir mercadería» vencidos (idempotente).'

    def handle(self, *args, **options):
        self.stdout.write(f'Borradores de recepción vencidos borrados: {purge()}.')
