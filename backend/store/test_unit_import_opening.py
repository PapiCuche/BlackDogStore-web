"""
INV-OPENING-01 (Excel) — the device import says what the entry IS in the Kardex, like «Recibir mercadería».

    purchase (default)  devices bought from a supplier          → PURCHASE_ENTRY (as before)
    opening             devices the shop already had at start   → INITIAL_STOCK

An opening row is refused, as a ROW ERROR, when its product already has a Kardex history in that branch or holds stock
no movement explains (the same rule as the receipt and the stock import, §31). The apply asks again: a shelf that got
a history after the preview refuses the whole file, with nothing written. Jobs staged before this keep «purchase».
"""
from django.test import TestCase

from store import unit_import_services
from store.models import BranchStock, StockMovement
from store.test_device_commerce import DeviceShop
from store.test_registration import IMEIS


class UnitImportOpeningTest(DeviceShop, TestCase):
    HEADERS = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
               'Motivo / referencia', 'Modelo', 'Capacidad', 'Color', 'SIM', 'Región', 'Línea',
               'Estado estético', 'Batería %', 'Precio de venta']

    def setUp(self):
        self.build('opening-import')

    def units_file(self, rows, entry_kind=None):
        from store.test_cost_imports import upload
        extra = {} if entry_kind is None else {'entry_kind': entry_kind}
        job = unit_import_services.preview_units(
            company=self.company, actor=self.staff, upload=upload(self.HEADERS, rows, title='Equipos'),
            filename='equipos.xlsx', reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk,
            default_reason='Conteo inicial', **extra,
        )
        return job, list(job.rows.order_by('row_number'))

    def row(self, serial, imei, product):
        return ['', product.name, '', serial, imei, '', 'Nuevo', '', '', '', '', '', '', '', '', '', '', '']

    def has_history(self, product):
        return (StockMovement.objects.filter(branch=self.branch, product=product).exists()
                or BranchStock.objects.filter(branch=self.branch, product=product, quantity__gt=0).exists())

    def clean_new_variant(self):
        for product in (self.new128, self.new256):
            if not self.has_history(product):
                return product
        self.skipTest('the shared shop has no new variant with a clean shelf')

    def test_without_entry_kind_the_import_is_still_a_purchase(self):
        product = self.clean_new_variant()
        job, [row] = self.units_file([self.row('OPNX0001', IMEIS[0], product)])
        self.assertEqual(row.errors, [])
        unit_import_services.apply_units(job=job, actor=self.staff)
        self.assertEqual(StockMovement.objects.filter(product=product).get().movement_type,
                         StockMovement.PURCHASE_ENTRY)

    def test_an_opening_file_writes_initial_stock(self):
        product = self.clean_new_variant()
        job, rows = self.units_file([self.row('OPNX0101', IMEIS[1], product),
                                     self.row('OPNX0102', IMEIS[2], product)], entry_kind='opening')
        self.assertEqual([r.errors for r in rows], [[], []])
        self.assertEqual(job.options_snapshot.get('entry_kind'), 'opening')
        unit_import_services.apply_units(job=job, actor=self.staff)
        self.assertEqual(list(StockMovement.objects.filter(product=product).values_list('movement_type', flat=True)),
                         [StockMovement.INITIAL_STOCK])

    def test_an_opening_row_on_a_shelf_with_history_is_a_row_error(self):
        product = self.clean_new_variant()
        first, _ = self.units_file([self.row('OPNX0201', IMEIS[3], product)])
        unit_import_services.apply_units(job=first, actor=self.staff)
        job, [row] = self.units_file([self.row('OPNX0202', IMEIS[4], product)], entry_kind='opening')
        self.assertEqual(row.action, 'error')
        self.assertTrue(any('inventario inicial' in e and 'compra' in e for e in row.errors), row.errors)

    def test_history_written_after_the_preview_refuses_the_whole_file(self):
        product = self.clean_new_variant()
        job, [row] = self.units_file([self.row('OPNX0301', IMEIS[5], product)], entry_kind='opening')
        self.assertEqual(row.errors, [])
        meanwhile, _ = self.units_file([self.row('OPNX0302', IMEIS[6], product)])
        unit_import_services.apply_units(job=meanwhile, actor=self.staff)
        movements = StockMovement.objects.count()
        with self.assertRaises(unit_import_services.UnitImportError):
            unit_import_services.apply_units(job=job, actor=self.staff)
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertFalse(StockMovement.objects.filter(movement_type=StockMovement.INITIAL_STOCK).exists())

    def test_an_unknown_entry_kind_is_refused(self):
        product = self.clean_new_variant()
        with self.assertRaises(unit_import_services.UnitImportError):
            self.units_file([self.row('OPNX0401', IMEIS[7], product)], entry_kind='gift')
