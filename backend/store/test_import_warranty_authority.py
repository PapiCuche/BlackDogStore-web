"""
IMPORT-WARRANTY-AUTHORITY — «Garantía (días)» of a used device in a stock file follows the price's authority.

The device sheet refuses a warranty change without `products.manage` (USED-READY-01: the warranty is a commercial
promise to the buyer). The importer only ignored the PRICE without that permission and stored the warranty anyway,
while docs/carga-stock-real.md §1 promises both are ignored with a warning. Now the file says the same as the sheet.
"""
from django.test import TestCase

from store.models import StockUnit
from store.test_device_commerce import DeviceShop
from store.test_stock_units import imei
from store.tests import _p2d_member

IMEIS = [imei(f'35693803588{n:03d}') for n in range(10)]
WARNING = 'Garantía (días) ignorada: sólo quien administra productos fija la garantía.'


class ImportWarrantyAuthorityTest(DeviceShop, TestCase):
    H = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
         'Motivo / referencia', 'Modelo', 'Capacidad', 'Color', 'Línea', 'Estado estético', 'Batería %',
         'Garantía (días)']

    def setUp(self):
        self.build('garantia-import')
        self.stocker, _ = _p2d_member(self.company, 'warranty_stocker',
                                      ['company.view', 'inventory.view', 'inventory.adjust'])
        self.manager, _ = _p2d_member(self.company, 'warranty_manager',
                                      ['company.view', 'inventory.view', 'inventory.adjust', 'products.view',
                                       'products.manage'])

    def row(self, n, warranty):
        return ['', '', '', f'SERGAR{n:02d}', IMEIS[n], '', 'Usado', '', 'Compra', 'Teléfono X', '128 GB', 'Azul',
                'Seminuevo', '', '', warranty]

    def preview(self, warranties, actor):
        from store import unit_import_services
        from store.test_cost_imports import upload
        made = upload(self.H, [self.row(i, w) for i, w in enumerate(warranties)], title='Equipos')
        return unit_import_services.preview_units(
            company=self.company, actor=actor, upload=made, filename='equipos.xlsx',
            reachable_branch_ids=[self.branch.pk], default_branch=self.branch.pk, default_reason='Compra')

    def test_without_products_manage_the_warranty_is_ignored_with_a_warning(self):
        job = self.preview(['90'], self.stocker)
        [row] = list(job.rows.order_by('row_number'))
        self.assertEqual(row.errors, [])
        self.assertNotIn('warranty_days', row.normalized_data)
        self.assertIn(WARNING, row.warnings)

    def test_without_products_manage_the_registered_unit_keeps_the_company_policy(self):
        from store import unit_import_services
        job = self.preview(['0'], self.stocker)
        unit_import_services.apply_units(job=job, actor=self.stocker)
        unit = StockUnit.objects.get(serial_number='SERGAR00')
        self.assertIsNone(unit.warranty_days)

    def test_with_products_manage_the_warranty_is_kept(self):
        job = self.preview(['90'], self.manager)
        [row] = list(job.rows.order_by('row_number'))
        self.assertEqual(row.errors, [])
        self.assertNotIn(WARNING, row.warnings)
        self.assertEqual(int(row.normalized_data['warranty_days']), 90)

    def test_an_empty_warranty_says_nothing(self):
        job = self.preview([''], self.stocker)
        [row] = list(job.rows.order_by('row_number'))
        self.assertNotIn(WARNING, row.warnings)
