"""
DC2 — every web hold whose time ran out goes back to the shelf.

The shop also does this whenever it reads a shelf; this sweeps the ones nobody
read. Safe to run every few minutes from cron: a hold is only released when
it is still a web hold, still expired, inside its own transaction.

`--all-web` (DC2 review): EVERY web hold, expired or not — the step before
going back to a release that predates DC2 (docs/despliegue-produccion.md §7).
Each release leaves its Kardex line and its audit row. Staff reservations are
never touched.
"""
from django.core.management.base import BaseCommand

from store.unit_hold_services import release_all_web, release_expired


class Command(BaseCommand):
    help = 'Devuelve al estante los equipos apartados en carritos web cuyo plazo venció (o todos, con --all-web).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all-web', action='store_true',
            help='Libera TODOS los apartados web, vencidos o no (antes de volver a la versión anterior).',
        )

    def handle(self, *args, **options):
        released = release_all_web() if options['all_web'] else release_expired()
        self.stdout.write(f'Equipos devueltos al estante: {released}.')
