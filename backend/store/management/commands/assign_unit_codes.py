"""
LABEL-02 · give the internal `label_code` to the devices of one company that were
received before it existed (docs/adr-inventory-labels.md D4).

    python manage.py assign_unit_codes --company <slug>            # dry run: counts, writes nothing
    python manage.py assign_unit_codes --company <slug> --apply    # assigns

  * only devices WITHOUT a code; a code already assigned is never changed;
  * every device, whatever its status: a sold or written-off device keeps its
    row, and a device that comes back keeps its code;
  * one short transaction per device; IDEMPOTENT: a second run finds none.

Run AFTER the deploy that adds the column. Nothing runs it automatically.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from store import unit_codes
from store.models import Company, StockUnit


class Command(BaseCommand):
    help = ('Asigna el código interno a los equipos de una empresa que no lo tienen. '
            'Sin --apply sólo cuenta.')

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='Slug de la empresa.')
        parser.add_argument('--apply', action='store_true', help='Escribir. Sin esto, sólo cuenta.')

    def handle(self, *args, **options):
        company = Company.objects.filter(slug=options['company']).first()
        if company is None:
            raise CommandError(f'No existe la empresa «{options["company"]}».')

        missing = list(StockUnit.objects.filter(company=company, label_code__isnull=True)
                       .order_by('pk').values_list('pk', flat=True))
        try:
            prefix = unit_codes.unit_prefix(company)
        except unit_codes.UnitCodeError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f'{company.slug}: {len(missing)} equipo(s) sin código interno (prefijo {prefix}).')
        if not options['apply']:
            self.stdout.write('Modo de prueba: no se escribió nada. Repite con --apply para asignar.')
            return

        assigned = 0
        for pk in missing:
            with transaction.atomic():
                unit = StockUnit.objects.select_for_update().select_related('company').get(pk=pk)
                if unit.label_code:
                    continue
                try:
                    unit_codes.assign_label_code(unit, prefix=prefix)
                except unit_codes.UnitCodeError as exc:
                    raise CommandError(f'equipo id={pk}: {exc}') from None
                assigned += 1
        self.stdout.write(self.style.SUCCESS(f'{assigned} código(s) interno(s) asignado(s).'))
