"""
INV-INTAKE-GUARD-01 (independent review) — the rule holds on EVERY door that brings new stock in, not only in
«Recibir mercadería».

  * DEVICES. Every device enters through `stock_unit_services.receive_units` («Registrar equipo», the receipt, the
    Excel import, the supplier reception, «Registrar artículo»). Its row cleaning (`_clean_row`) now refuses a device
    whose stated state contradicts its variant's line (`condition_line_mismatch`), and gives a device with no stated
    condition in a used variant the condition «used».
  * QUANTITIES. `inventory_services.create_stock_movement`, the only Kardex writer, refuses a PURCHASE_ENTRY or an
    INITIAL_STOCK of a used variant that is not counted by device (`used_requires_units`): the movements screen, the
    v1 API, the opening stock of «Registrar artículo», the supplier reception and the Excel stock load alike.
  * WHAT KEEPS WORKING. Exits, corrections, manual entries (the product's «ajustar inventario»), returns and
    transfers of stock that is ALREADY there: these guards are about new intake only.
"""
from django.test import TestCase

from store import device_catalog
from store import inventory_services as inventory
from store import stock_unit_services as units_service
from store.models import (
    AdminAuditLog, BranchStock, CosmeticGrade, Product, StockMovement, StockUnit,
)
from store.test_registration import IMEIS, _client
from store.tests import _m7_stock, _p2d_branch, _p2d_member, _p3_company, _prod

UNITS = '/api/admin/inventory/units/'
MOVEMENTS = '/api/admin/inventory/movements/'


class GuardBase(TestCase):
    def setUp(self):
        self.company = _p3_company('guardia-puertas', 'Guardia Puertas')
        self.branch = self.company.default_inventory_branch
        self.other_branch = _p2d_branch(self.company, 'Norte Guardia')
        self.staff, _ = _p2d_member(self.company, 'guardia_staff', [
            'company.view', 'inventory.view', 'inventory.adjust', 'products.view', 'products.manage'])
        self.model = device_catalog.create_model(company=self.company, name='iPhone 14 Guardia')
        capacity = device_catalog.create_attribute(company=self.company, name='Capacidad')
        self.gb128 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='128 GB')
        self.gb256 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='256 GB')
        self.new_serial = self.variant('iPhone 14 128 GB', 'g14-128', Product.CONDITION_NEW, self.gb128, serial=True)
        self.used_serial = self.variant('iPhone 14 128 GB Seminuevo', 'g14-128-semi', Product.CONDITION_USED,
                                        self.gb128, serial=True)
        # A used variant counted by quantity: data from before the guard.
        self.used_qty = self.variant('iPhone 14 256 GB Seminuevo', 'g14-256-semi', Product.CONDITION_USED,
                                     self.gb256)
        self.accessory = _prod(self.company, 'Cargador Guardia', 'cargador-guardia', price='50.00', inventory=0)
        self.grade = CosmeticGrade.objects.filter(company=self.company, is_active=True).order_by('pk').first()

    def variant(self, name, slug, line, option, *, serial=False):
        product = _prod(self.company, name, slug, price='3000.00', inventory=0)
        Product.objects.filter(pk=product.pk).update(is_serialized=serial, requires_imei=serial)
        product.refresh_from_db()
        return device_catalog.set_variant(product, model_id=self.model.pk, condition_line=line, option_ids=[option.pk])

    def snapshot(self):
        return (StockUnit.objects.count(), StockMovement.objects.count(),
                sorted(BranchStock.objects.values_list('branch_id', 'product_id', 'quantity')),
                AdminAuditLog.objects.filter(action__in=('stock_entry_created', 'stock_initial_recorded')).count())

    def shelf(self, product, branch=None):
        return inventory.branch_quantity(branch or self.branch, product)


class RegisterDevicesTest(GuardBase):
    """«Registrar equipo» (`POST /api/admin/inventory/units/`) and the service under it."""

    def post(self, product, *rows):
        return _client(self.staff).post(UNITS, {
            'branch': self.branch.pk, 'product_id': product.pk, 'reason': 'Compra', 'units': list(rows)}, format='json')

    def test_a_used_device_into_a_new_model_variant_is_refused_and_nothing_is_written(self):
        before = self.snapshot()
        res = self.post(self.new_serial, {'serial_number': 'GREG00001', 'imei': IMEIS[0], 'condition': 'used'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual((res.json()['code'], res.json()['field'], res.json()['line']),
                         ('condition_line_mismatch', 'condition', 1))
        self.assertIn('seminueva', res.json()['detail'])
        self.assertEqual(self.snapshot(), before)

    def test_a_graded_device_into_a_new_model_variant_is_refused(self):
        res = self.post(self.new_serial, {'serial_number': 'GREG00002', 'imei': IMEIS[1], 'condition': 'refurbished',
                                          'cosmetic_grade': self.grade.pk})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual((res.json()['code'], res.json()['field']), ('condition_line_mismatch', 'cosmetic_grade'))

    def test_a_new_device_into_a_used_variant_is_refused(self):
        before = self.snapshot()
        res = self.post(self.used_serial, {'serial_number': 'GREG00003', 'imei': IMEIS[2], 'condition': 'new'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'condition_line_mismatch')
        self.assertEqual(self.snapshot(), before)

    def test_a_device_without_a_condition_takes_its_variants_line(self):
        self.assertEqual(self.post(self.used_serial, {'serial_number': 'GREG00004', 'imei': IMEIS[3]}).status_code, 201)
        self.assertEqual(self.post(self.new_serial, {'serial_number': 'GREG00005', 'imei': IMEIS[4]}).status_code, 201)
        self.assertEqual(dict(StockUnit.objects.filter(serial_number__startswith='GREG').values_list(
            'serial_number', 'condition')), {'GREG00004': 'used', 'GREG00005': 'new'})

    def test_the_service_refuses_whatever_door_calls_it(self):
        with self.assertRaises(units_service.StockUnitError) as caught:
            units_service.receive_units(branch=self.branch, product=self.new_serial, actor=self.staff,
                                        reason='Compra', units=[{'serial_number': 'GREG00006', 'imei': IMEIS[5],
                                                                 'condition': 'used'}])
        self.assertEqual(caught.exception.code, 'condition_line_mismatch')
        self.assertFalse(StockUnit.objects.filter(serial_number='GREG00006').exists())

    def test_a_product_outside_the_catalogue_of_models_keeps_its_rules(self):
        loose = _prod(self.company, 'Equipo suelto', 'equipo-suelto', price='10.00', inventory=0)
        Product.objects.filter(pk=loose.pk).update(is_serialized=True)
        loose.refresh_from_db()
        self.assertEqual(self.post(loose, {'serial_number': 'GREG00007', 'condition': 'used'}).status_code, 201)


class QuantityIntakeTest(GuardBase):
    """A used variant counted by quantity: no NEW stock enters by quantity; what is there still moves."""

    def setUp(self):
        super().setUp()
        _m7_stock(self.branch, self.used_qty, 4)

    def move(self, product, movement_type, quantity=1):
        return _client(self.staff).post(MOVEMENTS, {
            'product_id': product.pk, 'movement_type': movement_type, 'quantity': quantity,
            'reason': 'Prueba', 'branch': self.branch.pk}, format='json')

    def test_a_manual_purchase_entry_by_quantity_on_a_used_variant_is_refused_and_writes_nothing(self):
        before = self.snapshot()
        res = self.move(self.used_qty, 'purchase_entry', 2)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'used_requires_units')
        self.assertIn('por equipo', res.json()['detail'])
        self.assertEqual(self.snapshot(), before)

    def test_the_v1_api_refuses_it_too(self):
        before = self.snapshot()
        res = _client(self.staff).post(f'/api/v1/internal/{self.company.slug}/inventory/adjustments/', {
            'product_slug': self.used_qty.slug, 'branch_id': self.branch.pk, 'movement_type': 'purchase_entry',
            'quantity': 1, 'reason': 'Prueba'}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'used_requires_units')
        self.assertEqual(self.snapshot(), before)

    def test_an_initial_stock_of_a_used_variant_is_refused(self):
        with self.assertRaises(inventory.InventoryError) as caught:
            inventory.apply_initial_stock(branch=self.other_branch, product=self.used_qty, quantity=2,
                                          actor=self.staff)
        self.assertEqual(caught.exception.code, 'used_requires_units')
        self.assertEqual(self.shelf(self.used_qty, self.other_branch), 0)

    def test_exits_corrections_manual_entries_and_returns_keep_working(self):
        for movement_type in ('manual_exit', 'correction_negative', 'damaged_exit', 'correction_positive',
                              'manual_entry', 'return_entry'):
            with self.subTest(movement_type=movement_type):
                self.assertEqual(self.move(self.used_qty, movement_type).status_code, 201)
        self.assertEqual(self.shelf(self.used_qty), 4)

    def test_the_products_inventory_adjustment_keeps_working(self):
        res = _client(self.staff).post(f'/api/admin/products/{self.used_qty.pk}/inventory-adjust/',
                                       {'delta': 2, 'reason': 'Conteo', 'branch': self.branch.pk}, format='json')
        self.assertIn(res.status_code, (200, 201), res.content)
        self.assertEqual(self.shelf(self.used_qty), 6)

    def test_a_transfer_of_existing_used_stock_keeps_working(self):
        transfer = inventory.create_stock_transfer(company=self.company, source_branch=self.branch,
                                                   destination_branch=self.other_branch, actor=self.staff)
        inventory.set_transfer_item(transfer, product=self.used_qty, quantity=2)
        inventory.dispatch_transfer(transfer, actor=self.staff)
        inventory.receive_transfer(transfer, actor=self.staff)
        self.assertEqual((self.shelf(self.used_qty), self.shelf(self.used_qty, self.other_branch)), (2, 2))

    def test_new_variants_and_accessories_still_enter_by_quantity(self):
        self.assertEqual(self.move(self.accessory, 'purchase_entry', 3).status_code, 201)
        new_qty = self.variant('iPhone 14 512 GB', 'g14-512', Product.CONDITION_NEW,
                               device_catalog.create_option(company=self.company,
                                                            attribute_id=self.gb128.attribute_id, label='512 GB'))
        self.assertEqual(self.move(new_qty, 'purchase_entry', 1).status_code, 201)


class ReturnedDeviceTest(GuardBase):
    def test_a_sold_device_that_comes_back_is_not_an_intake_even_if_its_data_predates_the_guard(self):
        [unit] = units_service.receive_units(branch=self.branch, product=self.new_serial, actor=self.staff,
                                             reason='Compra', units=[{'serial_number': 'GRET00001', 'imei': IMEIS[6]}])
        # A «used» device in a new variant, from before the guard, that was sold.
        StockUnit.objects.filter(pk=unit.pk).update(condition='used', status=StockUnit.Status.SOLD)
        BranchStock.objects.filter(branch=self.branch, product=self.new_serial).update(quantity=0)
        returned = units_service.return_unit(unit, reason='Devolución', actor=self.staff)
        self.assertEqual((returned.status, returned.condition), (StockUnit.Status.AVAILABLE, 'used'))
        self.assertEqual(self.shelf(self.new_serial), 1)


class StockLoadTest(GuardBase):
    """The Excel stock load: its «inventario inicial» is an intake; its adjustment to a target is a correction."""

    HEADERS = ['ID', 'CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN 1 - 11416']

    def preview(self, mode):
        from store import stock_import_services
        from store.models import BulkImportJob
        from store.test_cost_imports import upload
        job = stock_import_services.preview_stock(
            company=self.company, actor=self.staff, upload=upload(self.HEADERS, [['', '', '', self.used_qty.name, 3]],
                                                                  title='Inventario'),
            filename='stock.xlsx', branch_map={4: self.other_branch.pk}, mode=getattr(BulkImportJob, mode))
        return job, list(job.rows.order_by('row_number'))

    def test_an_initial_load_of_a_used_variant_is_a_row_error(self):
        job, [row] = self.preview('MODE_INITIAL')
        self.assertTrue(any('seminueva' in e for e in row.errors), row.errors)

    def test_an_adjustment_to_a_target_is_a_correction_and_still_loads(self):
        job, [row] = self.preview('MODE_RECONCILE')
        self.assertEqual(row.errors, [])
