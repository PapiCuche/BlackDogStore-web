"""
IMPORT-WARRANTY-AUTHORITY — «Garantía (días)» of a used device in a stock file follows the price's authority.

The device sheet refuses a warranty change without `products.manage` (USED-READY-01: the warranty is a commercial
promise to the buyer). The importer only ignored the PRICE without that permission and stored the warranty anyway,
while docs/carga-stock-real.md §1 promises both are ignored with a warning. Now the file says the same as the sheet.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from store.models import BulkImportJob, StockUnit
from store.test_device_commerce import DeviceShop
from store.test_stock_units import imei
from store.tests import _p2d_member

IMEIS = [imei(f'35693803588{n:03d}') for n in range(10)]
WARNING = 'Garantía (días) ignorada: sólo quien administra productos fija la garantía.'


class ImportWarrantyAuthorityTest(DeviceShop, TestCase):
    H = ['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo',
         'Motivo / referencia', 'Modelo', 'Capacidad', 'Color', 'Línea', 'Estado estético', 'Batería %',
         'Garantía (días)', 'Precio de venta']

    def setUp(self):
        self.build('garantia-import')
        self.stocker, _ = _p2d_member(self.company, 'warranty_stocker',
                                      ['company.view', 'inventory.view', 'inventory.adjust'])
        self.manager, _ = _p2d_member(self.company, 'warranty_manager',
                                      ['company.view', 'inventory.view', 'inventory.adjust', 'products.view',
                                       'products.manage'])

    def row(self, n, warranty, price=''):
        return ['', '', '', f'SERGAR{n:02d}', IMEIS[n], '', 'Usado', '', 'Compra', 'Teléfono X', '128 GB', 'Azul',
                'Seminuevo', '', '', warranty, price]

    def preview(self, warranties, actor, price=''):
        from store import unit_import_services
        from store.test_cost_imports import upload
        made = upload(self.H, [self.row(i, w, price) for i, w in enumerate(warranties)], title='Equipos')
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
        self.assertNotIn('warranty_days', row.normalized_data)

    # --- the APPLIER is asked again: a job previewed by a manager is no way in for a stocker -----------------------

    def apply(self, job, actor):
        client = APIClient()
        client.force_authenticate(user=actor)
        return client.post(f'/api/admin/inventory/units/import/{job.pk}/apply/', {}, format='json')

    def assert_refused_whole(self, res, job):
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'prices_forbidden')
        self.assertFalse(StockUnit.objects.filter(company=self.company).exists(), 'nothing is written')
        job.refresh_from_db()
        self.assertEqual(job.status, BulkImportJob.PREVIEWED)

    def test_a_warranty_staged_by_a_manager_is_refused_when_a_stocker_applies(self):
        job = self.preview(['90'], self.manager)
        self.assertEqual(int(job.rows.get().normalized_data['warranty_days']), 90)
        self.assert_refused_whole(self.apply(job, self.stocker), job)

    def test_a_zero_warranty_staged_by_a_manager_is_refused_too(self):
        # 0 = «sold without warranty»: also a commercial term, also the catalogue's.
        job = self.preview(['0'], self.manager)
        self.assert_refused_whole(self.apply(job, self.stocker), job)

    def test_a_sale_price_staged_by_a_manager_is_refused_when_a_stocker_applies(self):
        job = self.preview([''], self.manager, price='1500.00')
        self.assertIn('sale_price', job.rows.get().normalized_data)
        self.assert_refused_whole(self.apply(job, self.stocker), job)

    def test_the_service_refuses_the_stocker_too(self):
        from store import unit_import_services
        job = self.preview(['90'], self.manager)
        with self.assertRaises(unit_import_services.UnitImportError) as caught:
            unit_import_services.apply_units(job=job, actor=self.stocker)
        self.assertEqual(caught.exception.code, unit_import_services.PRICES_FORBIDDEN)
        row_number = job.rows.get().row_number
        self.assertIn(f'Filas con garantía o precio: {row_number}.', str(caught.exception))
        self.assertFalse(StockUnit.objects.filter(company=self.company).exists())

    def test_a_manager_applies_the_warranty_and_price_they_staged(self):
        job = self.preview(['90'], self.manager, price='1500.00')
        res = self.apply(job, self.manager)
        self.assertEqual(res.status_code, 200, res.content)
        unit = StockUnit.objects.get(serial_number='SERGAR00')
        self.assertEqual((unit.warranty_days, str(unit.sale_price)), (90, '1500.00'))

    def test_a_stocker_applies_a_job_without_commercial_terms(self):
        job = self.preview([''], self.manager)
        res = self.apply(job, self.stocker)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(StockUnit.objects.get(serial_number='SERGAR00').warranty_days)


UNITS = '/api/admin/inventory/units/'


class ReceiveUnitsWarrantyAuthorityTest(DeviceShop, TestCase):
    """«Registrar equipos» (POST /units/) and `receive_units` itself: a warranty follows the price's authority."""

    def setUp(self):
        self.build('garantia-recibir')
        self.stocker, _ = _p2d_member(self.company, 'warranty_recv_stocker',
                                      ['company.view', 'inventory.view', 'inventory.adjust'])

    def post(self, user, **extra):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.post(UNITS, {
            'branch': self.branch.pk, 'product_id': self.used128.pk, 'reason': 'Compra a cliente',
            'units': [{'serial_number': 'RCVGAR0001', 'imei': IMEIS[5], 'condition': 'used', **extra}],
        }, format='json')

    def test_a_stocker_stating_a_warranty_is_refused_whole_like_a_price(self):
        for value in (30, '30', 0):
            with self.subTest(value=value):
                res = self.post(self.stocker, warranty_days=value)
                self.assertEqual(res.status_code, 403, res.content)
                body = res.json()
                self.assertEqual((body['code'], body['field']), ('prices_forbidden', 'warranty_days'))
                self.assertFalse(StockUnit.objects.filter(company=self.company).exists(), 'nothing is written')

    def test_a_stocker_with_an_empty_warranty_registers_the_device(self):
        for n, value in enumerate(('', None, '  '), start=1):
            with self.subTest(value=value):
                res = self.post(self.stocker, serial_number=f'RCVGARV{n:03d}', imei=IMEIS[n + 1],
                                warranty_days=value)
                self.assertEqual(res.status_code, 201, res.content)
                self.assertIsNone(StockUnit.objects.get(serial_number=f'RCVGARV{n:03d}').warranty_days)

    def test_products_manage_keeps_the_warranty(self):
        res = self.post(self.staff, warranty_days=30)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(StockUnit.objects.get(serial_number='RCVGAR0001').warranty_days, 30)

    def test_receive_units_refuses_a_warranty_unless_told_the_caller_may_set_it(self):
        from store import stock_unit_services as units
        row = {'serial_number': 'RCVGAR0002', 'imei': IMEIS[6], 'condition': 'used', 'warranty_days': 60}
        with self.assertRaises(units.StockUnitError) as caught:
            units.receive_units(branch=self.branch, product=self.used128, units=[row], actor=self.staff,
                                reason='Compra')
        self.assertEqual((caught.exception.code, caught.exception.field, caught.exception.line),
                         ('prices_forbidden', 'warranty_days', 1))
        self.assertFalse(StockUnit.objects.filter(company=self.company).exists())
        [unit] = units.receive_units(branch=self.branch, product=self.used128, units=[row], actor=self.staff,
                                     reason='Compra', may_set_warranty=True)
        self.assertEqual(unit.warranty_days, 60)


from store.test_supply_reception import NO_COST_CAPS, ReceptionShop  # noqa: E402


class SupplyReceiptWarrantyAuthorityTest(ReceptionShop, TestCase):
    """
    «Recibir del proveedor» (POST /orders/<id>/supply/<line>/receive/): its rows reach `receive_units` as sent.

    A used variant is never sold on order (`supply_services.set_sale_mode`), so the warranty of a NEW device is refused
    by the line rule (`not_used_line`) to whoever holds `products.manage`; without it the authority answers first. The
    line switched to used after the sale (the ORM, an old order) is still refused by the authority first: the rows
    reach `receive_units` as sent, so this door must never be the one that forgets to ask.
    """

    def setUp(self):
        self.build('garantia-proveedor')
        # Receives and manages orders, but does not manage products (nor see costs).
        self.receiver = self.no_costs
        assert 'products.manage' not in NO_COST_CAPS

    def used_after_the_sale(self):
        from store.models import Product
        order = self.paid_order(self.phone, 1)
        Product.objects.filter(pk=self.phone.pk).update(condition_line=Product.CONDITION_USED)
        return order

    def receive(self, user, order, **extra):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.post(self.url(order), {'units': [
            {'serial_number': 'PRVGAR0001', 'imei': IMEIS[7], **extra}]}, format='json')

    def assert_refused(self, res, order):
        self.assertEqual(res.status_code, 403, res.content)
        body = res.json()
        self.assertEqual((body['code'], body.get('line'), body.get('unit_field')),
                         ('prices_forbidden', 1, 'warranty_days'))
        self.assertFalse(StockUnit.objects.filter(product=self.phone).exists(), 'nothing is written')
        self.assertEqual(order.items.get().supply_status, 'pending')

    def test_a_warranty_from_somebody_without_products_manage_is_refused_and_nothing_moves(self):
        order = self.used_after_the_sale()
        self.assert_refused(self.receive(self.receiver, order, condition='used', warranty_days=90), order)

    def test_on_a_new_device_too_the_authority_answers_first(self):
        order = self.paid_order(self.phone, 1)
        self.assert_refused(self.receive(self.receiver, order, warranty_days=90), order)

    def test_without_a_warranty_the_same_person_receives_it(self):
        order = self.paid_order(self.phone, 1)
        res = self.receive(self.receiver, order, warranty_days='')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(StockUnit.objects.get(product=self.phone).warranty_days)

    def test_products_manage_on_a_new_device_still_hears_the_line_rule(self):
        order = self.paid_order(self.phone, 1)
        res = self.receive(self.staff, order, warranty_days=90)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('unit_field'), 'warranty_days')
        self.assertFalse(StockUnit.objects.filter(product=self.phone).exists())
