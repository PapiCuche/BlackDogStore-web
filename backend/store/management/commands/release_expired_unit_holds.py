"""
DC2 — every web hold whose time ran out goes back to the shelf.

The shop also does this whenever it reads a shelf; this sweeps the ones nobody
read. Safe to run every few minutes from cron: a hold is only released when
it is still a web hold, still expired, inside its own transaction.
"""
from django.core.management.base import BaseCommand

from store.unit_hold_services import release_expired


class Command(BaseCommand):
    help = 'Devuelve al estante los equipos apartados en carritos web cuyo plazo venció.'

    def handle(self, *args, **options):
        released = release_expired()
        self.stdout.write(f'Equipos devueltos al estante: {released}.')
