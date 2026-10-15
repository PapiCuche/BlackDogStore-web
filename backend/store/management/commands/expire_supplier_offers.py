"""
DC3 — switch off every supplier offer whose last day has passed.

The shop never sells against an expired offer even before this runs: what may
sell is decided by the dates (`supply_services.valid_offers`). This only keeps
the panel honest — an expired offer shows as inactive, with an audit line
(`supplier_offer_expired`) — and is idempotent: an offer already off is not
touched. It never changes what was committed: orders already paid keep their
offer and their quantity.
"""
from django.core.management.base import BaseCommand

from store.supply_services import expire_offers


class Command(BaseCommand):
    help = 'Desactiva las ofertas de proveedor cuya vigencia terminó (idempotente, auditado).'

    def handle(self, *args, **options):
        self.stdout.write(f'Ofertas de proveedor vencidas desactivadas: {expire_offers()}.')
