"""
INV-LOAD-02 — `manage.py check_unit_stock [--company <slug>]`: does every shelf of devices hold its devices?

For each company × branch × product that sells by device: `BranchStock.quantity` against the AVAILABLE devices. It is
READ-ONLY (it repairs nothing) and its EXIT CODE is the gate: 0 when every shelf agrees, 2 when one differs. The
runbook of the real stock load (`docs/carga-stock-real.md`) runs it before loading, after each file and after an
annulment, and expects 0 each time.

It prints company slugs, branch and product names (catalogue text) and counts: never a serial, an IMEI or a cost, so
its output may go into an operation log.
"""
from django.core.management.base import BaseCommand, CommandError

from store.models import Company
from store.unit_stock_check import mismatches


class Command(BaseCommand):
    help = ('Compara, por empresa, sucursal y producto con número de serie, el stock (BranchStock) con los equipos '
            'disponibles. Sólo lectura. Sale con 0 si todo cuadra y con 2 si hay diferencias.')

    def add_arguments(self, parser):
        parser.add_argument('--company', help='Slug de la empresa (por defecto, todas).')

    def handle(self, *args, **options):
        company = None
        if options.get('company'):
            company = Company.objects.filter(slug=options['company']).first()
            if company is None:
                raise CommandError(f'No existe la empresa «{options["company"]}».')
        result = mismatches(company=company)
        found = result['mismatches']
        for row in found:
            self.stdout.write(
                f'empresa {row["company"]} · sucursal «{row["branch"]}» · «{row["product"]}» (producto '
                f'{row["product_id"]}): stock {row["quantity"]} · disponibles {row["available"]} · diferencia '
                f'{row["difference"]:+d}'
            )
        self.stdout.write(f'{result["checked"]} estante(s) de equipos comprobado(s); {len(found)} diferencia(s).')
        if found:
            raise CommandError(
                f'{len(found)} estante(s) no cuadran: el stock no es igual a los equipos disponibles. No se corrigió '
                f'nada; revisa el Kardex de cada uno antes de tocarlo.', returncode=2)
