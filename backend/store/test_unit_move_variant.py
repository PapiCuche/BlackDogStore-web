"""
INV-UNIT-CORRECT-01 · fase 2 — «Mover a otra variante del mismo modelo» (docs/adr-unit-correction.md §5.2).

The device is the right one but entered under the wrong capacity or colour. It changes product with a pair of
correction lines (out of the origin shelf, into the destination shelf), never an in-place UPDATE that a shelf would not
see. Same model and same line only (UC-D5); same branch; only while its entry is its only history (UC-D4). A label
batch is made for it (UC-D6), since the printed label names the old variant.
"""
from django.test import TestCase

from store import device_catalog
from store import stock_unit_services as units
from store.models import AdminAuditLog, BranchStock, LabelBatch, StockMovement, StockUnit
from store.test_device_commerce import IMEIS, DeviceShop, _client
from store.tests import _p2d_member


class MoveBase(DeviceShop, TestCase):
    def setUp(self):
        self.build('unit-move')

    def one(self, product=None, **extra):
        [unit] = self.receive(product or self.new128, **extra)
        return unit

    def move(self, unit, product, reason='Era de 256 GB'):
        return units.move_unit_to_variant(unit, product=product, reason=reason, actor=self.staff)

    def shelves_match(self, *products):
        for product in products:
            shelf = BranchStock.objects.filter(branch=self.branch, product=product).values_list('quantity', flat=True)
            available = StockUnit.objects.filter(branch=self.branch, product=product, status='available').count()
            self.assertEqual((list(shelf) or [0])[0], available, product.name)


class MoveVariantTest(MoveBase):
    def test_the_device_changes_variant_with_a_pair_of_correction_lines(self):
        unit = self.one(cost='3000.00')
        moved = self.move(unit, self.new256)
        self.assertEqual(moved.product_id, self.new256.pk)
        self.assertEqual((moved.imei, moved.serial_number, moved.label_code), (unit.imei, unit.serial_number,
                                                                            unit.label_code))
        out = StockMovement.objects.get(product=self.new128, movement_type=StockMovement.CORRECTION_NEGATIVE)
        into = StockMovement.objects.get(product=self.new256, movement_type=StockMovement.CORRECTION_POSITIVE)
        for line in (out, into):
            self.assertEqual((line.reference_type, line.reference_id), ('stock_unit_variant', str(unit.pk)))
            self.assertEqual(line.metadata.get('unit_ids'), [unit.pk])
            self.assertEqual(line.metadata.get('from_product_id'), self.new128.pk)
            self.assertEqual(line.metadata.get('to_product_id'), self.new256.pk)
        self.assertEqual(into.metadata.get('pair_movement_id'), out.pk)
        self.assertEqual((self.shelf(self.new128), self.shelf(self.new256)), (0, 1))
        self.shelves_match(self.new128, self.new256)

    def test_a_label_batch_is_made_for_the_moved_device(self):
        unit = self.one()
        self.move(unit, self.new256)
        into = StockMovement.objects.get(product=self.new256, movement_type=StockMovement.CORRECTION_POSITIVE)
        batch = LabelBatch.objects.filter(company=self.company).order_by('-pk').first()
        self.assertIsNotNone(batch)
        self.assertIn(str(into.pk), batch.source_key)

    def test_the_audit_names_ids_never_identifiers(self):
        unit = self.one()
        self.move(unit, self.new256, reason=f'IMEI {unit.imei} era de 256')
        log = AdminAuditLog.objects.filter(action='stock_unit_variant_changed').get()
        self.assertEqual(log.metadata.get('from_product_id'), self.new128.pk)
        self.assertEqual(log.metadata.get('to_product_id'), self.new256.pk)
        self.assertNotIn(unit.imei, str(log.metadata))
        self.assertNotIn(unit.serial_number, str(log.metadata))

    def test_moving_to_the_variant_it_is_already_in_writes_nothing(self):
        unit = self.one()
        movements = StockMovement.objects.count()
        self.assertEqual(self.move(unit, self.new128).product_id, self.new128.pk)
        self.assertEqual(StockMovement.objects.count(), movements)

    def test_a_moved_device_has_history_and_can_no_longer_be_voided(self):
        unit = self.one()
        self.move(unit, self.new256)
        with self.assertRaises(units.StockUnitError) as caught:
            units.void_unit(StockUnit.objects.get(pk=unit.pk), reason='x', reason_code='typo', actor=self.staff)
        self.assertEqual(caught.exception.code, units.NOT_CORRECTABLE)


class MoveRefusedTest(MoveBase):
    def assert_refused(self, unit, product, *, field=None, code=None):
        movements = StockMovement.objects.count()
        with self.assertRaises(units.StockUnitError) as caught:
            self.move(unit, product)
        if field:
            self.assertEqual(caught.exception.field, field)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertEqual(StockUnit.objects.get(pk=unit.pk).product_id, unit.product_id)

    def test_another_model_is_refused(self):
        other_model = device_catalog.create_model(company=self.company, name='Teléfono Y')
        from store.models import Product
        from store.tests import _prod
        product = _prod(self.company, 'Teléfono Y 128 GB Negro', 'ty-128-negro', price='2000.00', inventory=0)
        Product.objects.filter(pk=product.pk).update(is_serialized=True, requires_imei=True)
        product = device_catalog.set_variant(product, model_id=other_model.pk, condition_line='new',
                                             option_ids=[self.gb128.pk, self.black.pk])
        self.assert_refused(self.one(), product, field='product')

    def test_another_line_is_refused(self):
        self.assert_refused(self.one(), self.used128, field='product')

    def test_a_product_counted_by_quantity_is_refused(self):
        from store.tests import _prod
        cable = _prod(self.company, 'Cable', 'cable-move', price='20.00', inventory=0)
        self.assert_refused(self.one(), cable, field='product')

    def test_a_device_with_history_is_refused(self):
        unit = self.one()
        units.reserve(unit, reason='Para Ana', actor=self.staff)
        units.release(unit, actor=self.staff)
        self.assert_refused(unit, self.new256, code=units.NOT_CORRECTABLE)

    def test_a_reserved_device_is_refused(self):
        unit = self.one()
        units.reserve(unit, reason='Para Ana', actor=self.staff)
        self.assert_refused(unit, self.new256, code=units.NOT_CORRECTABLE)

    def test_a_serial_already_in_the_destination_is_refused(self):
        [there] = self.receive(self.new256)
        [unit] = units.receive_units(branch=self.branch, product=self.new128, actor=self.staff, reason='Compra',
                                     units=[{'serial_number': there.serial_number, 'imei': IMEIS[30]}])
        self.assert_refused(unit, self.new256, field='serial_number')

    def test_a_destination_that_needs_an_imei_refuses_a_device_without_one(self):
        from store.models import Product
        Product.objects.filter(pk__in=[self.new128.pk]).update(requires_imei=False)
        self.new128.refresh_from_db()
        [unit] = units.receive_units(branch=self.branch, product=self.new128, actor=self.staff, reason='Compra',
                                     units=[{'serial_number': 'SINIMEI001'}])
        self.assert_refused(unit, self.new256, field='imei')


class MoveApiTest(MoveBase):
    def url(self, unit):
        return f'/api/admin/inventory/units/{unit.pk}/move-variant/'

    def test_staff_moves_through_the_panel(self):
        unit = self.one()
        res = _client(self.staff).post(self.url(unit), {'product': self.new256.pk, 'reason': 'Era de 256'},
                                       format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['product_id'], self.new256.pk)

    def test_a_product_of_another_company_is_not_found(self):
        unit = self.one()
        other = DeviceShop()
        other.build('unit-move-otra')
        res = _client(self.staff).post(self.url(unit), {'product': other.new256.pk, 'reason': 'x'}, format='json')
        self.assertEqual(res.status_code, 404, res.content)

    def test_without_inventory_adjust_it_is_forbidden(self):
        viewer, _ = _p2d_member(self.company, 'unit_move_viewer', ['company.view', 'inventory.view'])
        res = _client(viewer).post(self.url(self.one()), {'product': self.new256.pk, 'reason': 'x'}, format='json')
        self.assertEqual(res.status_code, 403, res.content)

    def test_the_variants_it_may_move_to_are_listed(self):
        unit = self.one()
        res = _client(self.staff).get(self.url(unit))
        self.assertEqual(res.status_code, 200, res.content)
        ids = [p['id'] for p in res.json()['results']]
        self.assertEqual(ids, [self.new256.pk])


class MoveReviewTest(MoveBase):
    """Review of phase 2: totals, costs, used devices, voided serials, labels in the answer, odd input."""

    def test_both_products_totals_and_the_cost_of_each_line(self):
        from decimal import Decimal
        from store.models import Product
        unit = self.one(cost='3000.00')
        before = (Product.objects.get(pk=self.new128.pk).inventory, Product.objects.get(pk=self.new256.pk).inventory)
        self.move(unit, self.new256)
        after = (Product.objects.get(pk=self.new128.pk).inventory, Product.objects.get(pk=self.new256.pk).inventory)
        self.assertEqual(after, (before[0] - 1, before[1] + 1))
        for line in StockMovement.objects.filter(reference_type='stock_unit_variant'):
            self.assertEqual(line.unit_cost, Decimal('3000.00'), line.movement_type)

    def test_a_used_device_keeps_its_price_and_grade(self):
        from store import device_catalog as catalog
        used256 = self.variant('Teléfono X 256 GB Azul usado', 'tx-256-azul-usado', '1.00',
                               [self.gb256, self.blue], line='used')
        [unit] = self.receive(self.used128, cosmetic_grade=self.grade.code, sale_price='1500.00')
        moved = self.move(unit, used256)
        self.assertEqual((moved.sale_price, moved.cosmetic_grade_id), (unit.sale_price, unit.cosmetic_grade_id))
        self.assertIsNotNone(catalog)

    def test_a_voided_device_with_the_same_serial_in_the_destination_does_not_block(self):
        [gone] = self.receive(self.new256)
        units.void_unit(gone, reason='Duplicado', reason_code='duplicate', actor=self.staff)
        [unit] = units.receive_units(branch=self.branch, product=self.new128, actor=self.staff, reason='Compra',
                                     units=[{'serial_number': gone.serial_number, 'imei': IMEIS[31]}])
        self.assertEqual(self.move(unit, self.new256).product_id, self.new256.pk)

    def test_the_answer_carries_the_new_label_batch_with_the_device(self):
        from store.models import LabelBatchItem
        unit = self.one()
        res = _client(self.staff).post(f'/api/admin/inventory/units/{unit.pk}/move-variant/',
                                       {'product': self.new256.pk, 'reason': 'Era de 256'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertIs(body['label_error'], False)
        self.assertIsNotNone(body['label_batch'])
        items = LabelBatchItem.objects.filter(batch_id=body['label_batch']['id'])
        self.assertEqual([(i.unit_id, i.product_id) for i in items], [(unit.pk, self.new256.pk)])

    def test_an_odd_product_value_is_not_found(self):
        unit = self.one()
        for raw in ('²', 'abc', True, None):
            with self.subTest(raw):
                res = _client(self.staff).post(f'/api/admin/inventory/units/{unit.pk}/move-variant/',
                                               {'product': raw, 'reason': 'x'}, format='json')
                self.assertEqual(res.status_code, 404, res.content)

    def test_the_targets_leave_out_other_lines_quantity_products_and_other_companies(self):
        from store.tests import _prod
        _prod(self.company, 'Cable', 'cable-targets', price='20.00', inventory=0)
        other = DeviceShop()
        other.build('unit-move-targets-otra')
        ids = [p.pk for p in units.variant_targets(self.one())]
        self.assertEqual(ids, [self.new256.pk])
