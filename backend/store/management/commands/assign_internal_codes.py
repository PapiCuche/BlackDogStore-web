"""
LABEL-01 · give a generated code to the ACTIVE products of one company that have
NO code at all (docs/adr-inventory-labels.md D1).

    python manage.py assign_internal_codes --company <slug>            # dry run: lists, writes nothing
    python manage.py assign_internal_codes --company <slug> --apply    # assigns

Rules (the same policy as every creation path, `product_codes.ensure_code`):
  * a product with ANY code, active or inactive, is left alone — its codes are
    kept and never overwritten;
  * deactivated products are skipped (counted, not coded);
  * each product is coded in its own short transaction (product lock, then the
    series lock), so the till keeps working while it runs;
  * IDEMPOTENT: a second run finds nothing missing and spends no number.

OWNER RULE (2026-10-23): NOT TO BE RUN ON PRODUCTION until LABEL-MIGRATION-01
has imported the historical codes — otherwise products would be given a new code
where an old printed one exists. Nothing runs it automatically.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from store import product_codes
from store.models import Company, Product

_SAMPLE = 20


class Command(BaseCommand):
    help = ('Asigna un código generado a los productos activos de una empresa que no tienen ninguno. '
            'Sin --apply sólo lista. No se ejecuta en producción antes de LABEL-MIGRATION-01.')

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='Slug de la empresa.')
        parser.add_argument('--apply', action='store_true', help='Escribir. Sin esto, sólo lista.')

    def handle(self, *args, **options):
        company = Company.objects.filter(slug=options['company']).first()
        if company is None:
            raise CommandError(f'No existe la empresa «{options["company"]}».')

        without = Product.objects.filter(company=company, barcodes__isnull=True)
        missing = list(without.filter(is_active=True).order_by('pk').values_list('pk', 'name'))
        skipped_inactive = without.filter(is_active=False).count()

        self.stdout.write(f'{company.slug}: {len(missing)} producto(s) activo(s) sin código; '
                          f'{skipped_inactive} desactivado(s) sin código, que no se tocan.')
        for pk, name in missing[:_SAMPLE]:
            self.stdout.write(f'  id={pk} {name}')
        if len(missing) > _SAMPLE:
            self.stdout.write(f'  … y {len(missing) - _SAMPLE} más.')

        if not options['apply']:
            self.stdout.write('Modo de prueba: no se escribió nada. Repite con --apply para asignar.')
            return

        assigned = 0
        for pk, _name in missing:
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=pk)
                result = product_codes.ensure_code(product)
            if result.generated is not None:
                assigned += 1
                self.stdout.write(f'  id={pk} → {result.generated.code}')
        self.stdout.write(self.style.SUCCESS(f'{assigned} código(s) asignado(s).'))
