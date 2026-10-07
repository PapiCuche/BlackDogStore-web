"""
Send the purchase conversions that are waiting: the ones a provider did not take
the first time, and — where `MEASUREMENT_SEND_INLINE=0` — all of them.

    python manage.py send_pending_conversions

Safe to run every minute and safe to run twice at once: a row is claimed before
it is sent, and its event id is the same on every attempt, so the provider
discards a repeat. It also forgets, after a week, the browser identifiers of
orders whose conversions never got anywhere.
"""
from django.core.management.base import BaseCommand

from store.measurement import conversions


class Command(BaseCommand):
    help = 'Envía las conversiones de compra pendientes a los proveedores de medición. Idempotente.'

    def handle(self, *args, **options):
        ids = list(conversions.due().values_list('pk', flat=True)[:200])
        conversions.attempt(ids)
        forgotten = conversions.forget_old_contexts()
        if ids or forgotten:
            self.stdout.write(f'conversiones procesadas: {len(ids)} · contextos caducados borrados: {forgotten}')
