"""
INV-INTAKE-GUARD-01 — the Excel device import refuses a device that contradicts its variant's line, row by row.

The same rule as «Recibir mercadería» (`stock_unit_services.condition_line_problem`): a «Usado» device (or any graded
one) into a NEW model variant, or a «Nuevo» device into a USED variant, is a ROW ERROR, and a file with errors
registers nothing. An empty «Condición» in a used variant takes the line («Usado»), as the receipt does. The apply
re-checks it: a variant whose line changed after the preview refuses the whole file, with nothing written.
"""
from django.test import TestCase

from store import unit_import_services
from store.models import BulkImportJob, Product, StockMovement, StockUnit
from store.test_device_commerce import DeviceShop
from store.test_registration import IMEIS


class UnitImportIntakeGuardTest(DeviceShop, TestCase):
    HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
               'Motivo / referencia', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea',
               'Estado estético', 'Batería %', 'Precio de venta']

    def setUp(self):
        self.build('guard-import')

    def units_file(self, rows):
        from store.test_cost_imports import upload
        job = unit_import_services.preview_units(
            company=self.company, actor=self.staff, upload=upload(self.HEADERS, rows, title='Equipos'),
            filename='equipos.xlsx', reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk,
            default_reason='Compra',
        )
        return job, list(job.rows.order_by('row_number'))

    def row(self, serial, imei, condition, product, grade=''):
        return ['', product.name, '', serial, imei, '', condition, '', 'Compra', '', '', '', '', '', '', grade, '', '']

    def test_a_used_device_into_a_new_variant_is_a_row_error(self):
        job, [row] = self.units_file([self.row('GIMPU0001', IMEIS[0], 'Usado', self.new128)])
        self.assertEqual(row.action, 'error')
        self.assertTrue(any('variante nueva' in e and 'seminueva' in e for e in row.errors), row.errors)

    def test_a_graded_device_into_a_new_variant_is_a_row_error(self):
        job, [row] = self.units_file([self.row('GIMPG0001', IMEIS[1], 'Reacondicionado', self.new128, 'Excelente')])
        self.assertTrue(any('estado estético' in e for e in row.errors), row.errors)

    def test_a_new_device_into_a_used_variant_is_a_row_error(self):
        job, [row] = self.units_file([self.row('GIMPN0001', IMEIS[2], 'Nuevo', self.used128)])
        self.assertEqual(row.action, 'error')
        self.assertTrue(any('variante seminueva' in e for e in row.errors), row.errors)

    def test_a_file_with_a_refused_row_registers_nothing_and_leaves_the_kardex_untouched(self):
        units, movements = StockUnit.objects.count(), StockMovement.objects.count()
        job, rows = self.units_file([
            self.row('GIMPOK001', IMEIS[3], 'Nuevo', self.new128),
            self.row('GIMPKO001', IMEIS[4], 'Usado', self.new128),
        ])
        self.assertEqual([r.action for r in rows], ['create', 'error'])
        with self.assertRaises(unit_import_services.UnitImportError):
            unit_import_services.apply_units(job=job, actor=self.staff)
        self.assertEqual((StockUnit.objects.count(), StockMovement.objects.count()), (units, movements))

    def test_rows_in_their_own_line_load_and_an_empty_condition_takes_the_used_line(self):
        job, rows = self.units_file([
            self.row('GIMPOK101', IMEIS[5], 'Nuevo', self.new128),
            self.row('GIMPOK102', IMEIS[6], 'Usado', self.used128, 'Excelente'),
            self.row('GIMPOK103', IMEIS[7], '', self.used128),
            self.row('GIMPOK104', IMEIS[8], '', self.new128),
        ])
        self.assertEqual([r.errors for r in rows], [[], [], [], []])
        unit_import_services.apply_units(job=job, actor=self.staff)
        conditions = dict(StockUnit.objects.filter(serial_number__startswith='GIMPOK1')
                          .values_list('serial_number', 'condition'))
        self.assertEqual(conditions, {'GIMPOK101': 'new', 'GIMPOK102': 'used', 'GIMPOK103': 'used',
                                      'GIMPOK104': 'new'})

    def test_open_box_into_a_new_variant_loads_with_a_warning(self):
        job, [row] = self.units_file([self.row('GIMPOB001', IMEIS[9], 'Caja abierta', self.new128)])
        self.assertEqual(row.errors, [])
        self.assertTrue(any('como nuevo' in w for w in row.warnings), row.warnings)

    def test_a_line_changed_after_the_preview_refuses_the_whole_apply(self):
        job, [row] = self.units_file([self.row('GIMPST001', IMEIS[10], 'Nuevo', self.new256)])
        self.assertEqual(row.errors, [])
        Product.objects.filter(pk=self.new256.pk).update(condition_line=Product.CONDITION_USED)
        units, movements = StockUnit.objects.count(), StockMovement.objects.count()
        with self.assertRaises(unit_import_services.UnitImportError):
            unit_import_services.apply_units(job=job, actor=self.staff)
        self.assertEqual((StockUnit.objects.count(), StockMovement.objects.count()), (units, movements))
        job.refresh_from_db()
        self.assertNotEqual(job.status, BulkImportJob.APPLIED)

    def test_a_job_previewed_under_the_old_rules_says_the_rules_changed(self):
        # Before INV-INTAKE-GUARD-01 an empty «Condición» was staged «new», even for a used variant.
        job, [row] = self.units_file([self.row('GIMPOLD01', IMEIS[11], '', self.used128)])
        self.assertEqual(row.errors, [])
        data = dict(row.normalized_data)
        data.pop('checked_line', None)
        data['condition'] = 'new'
        type(row).objects.filter(pk=row.pk).update(normalized_data=data)
        units, movements = StockUnit.objects.count(), StockMovement.objects.count()
        with self.assertRaises(unit_import_services.UnitImportError) as caught:
            unit_import_services.apply_units(job=job, actor=self.staff)
        self.assertIn('Las reglas de condición cambiaron desde la previsualización: vuelve a subir el archivo',
                      str(caught.exception))
        self.assertNotIn('catálogo', str(caught.exception))
        self.assertEqual((StockUnit.objects.count(), StockMovement.objects.count()), (units, movements))

    def test_a_line_changed_after_the_preview_says_the_catalogue_changed(self):
        job, [row] = self.units_file([self.row('GIMPST002', IMEIS[12], 'Nuevo', self.new256)])
        Product.objects.filter(pk=self.new256.pk).update(condition_line=Product.CONDITION_USED)
        with self.assertRaises(unit_import_services.UnitImportError) as caught:
            unit_import_services.apply_units(job=job, actor=self.staff)
        self.assertIn('catálogo cambió', str(caught.exception))
