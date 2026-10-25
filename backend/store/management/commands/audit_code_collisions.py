"""
LABEL-02 · READ-ONLY. Which product codes already equal a device identifier
(docs/adr-inventory-labels.md §9.4, D4).

The till looks up products first, so a `ProductBarcode` whose code is a device's
IMEI, second IMEI, serial or internal code takes that device's scan; and it reads
a device's internal code before its serial, so a serial equal to ANOTHER device's
internal code lands on that other device. Both are listed. New ones are
refused from both sides since LABEL-01/02; this lists the ones the data may
already hold, for a person to decide (remove the product code, or correct the
device). It changes nothing.

Prints ids and the field that collides — never the IMEI or the serial itself:
a terminal and a log are read by more people than the inventory.

    python manage.py audit_code_collisions [--company <slug>]
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models.functions import Upper

from store.models import Company, ProductBarcode, StockUnit

FIELDS = ('imei', 'imei2', 'serial_number', 'label_code')


class Command(BaseCommand):
    help = 'Lista los códigos de producto que coinciden con un IMEI, serie o código interno de equipo. Sólo lectura.'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='Slug de la empresa. Sin él, todas.')

    def handle(self, *args, **options):
        companies = Company.objects.order_by('slug')
        if options.get('company'):
            companies = companies.filter(slug=options['company'])
            if not companies.exists():
                raise CommandError(f'No existe la empresa «{options["company"]}».')

        total = 0
        for company in companies:
            codes = {}
            for pk, product_id, upper in (ProductBarcode.objects.filter(company=company)
                                          .annotate(upper=Upper('code'))
                                          .values_list('pk', 'product_id', 'upper')):
                codes.setdefault(upper, []).append((pk, product_id))
            found = []
            for field in FIELDS:
                rows = (StockUnit.objects.filter(company=company)
                        .annotate(upper=Upper(field)).filter(upper__in=list(codes))
                        .values_list('pk', 'upper'))
                for unit_id, upper in rows:
                    for barcode_id, product_id in codes[upper]:
                        found.append((unit_id, field, barcode_id, product_id, None))
            # #178 review — device against device: a serial that is ANOTHER
            # device's internal code (find_unit reads the label code first).
            labels = dict(StockUnit.objects.filter(company=company, label_code__isnull=False)
                          .values_list('label_code', 'pk'))
            if labels:
                for unit_id, serial in (StockUnit.objects.filter(company=company)
                                        .annotate(upper=Upper('serial_number')).filter(upper__in=list(labels))
                                        .values_list('pk', 'upper')):
                    if labels[serial] != unit_id:
                        found.append((unit_id, 'serial_number', None, None, labels[serial]))
            if not found:
                continue
            total += len(found)
            self.stdout.write(f'{company.slug}: {len(found)} choque(s)')
            for unit_id, field, barcode_id, product_id, other_unit in sorted(found, key=lambda row: (row[0], row[1])):
                if other_unit is not None:
                    self.stdout.write(f'  unit_id={unit_id} campo={field} es el código interno de unit_id={other_unit}')
                else:
                    self.stdout.write(
                        f'  unit_id={unit_id} campo={field} barcode_id={barcode_id} product_id={product_id}')
        self.stdout.write(f'{total} choque(s) en total. No se cambió nada.')
