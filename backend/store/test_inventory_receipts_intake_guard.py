"""
INV-INTAKE-GUARD-01 — what may enter a device variant through «Recibir mercadería».

Two holes the audit of 2026-11-17 found (B3 and B4):

  * a variant of a device model could be stocked BY QUANTITY, and once on the shelf it can never be counted by device
    again. A used-line variant received by quantity loses what makes it sellable: each device's own grade, battery and
    price. So a quantity line of a USED variant is now REFUSED (`used_requires_units`); a quantity line of a NEW model
    variant that could still be counted by device stays a WARNING, explicit and coded (`quantity_locks_tracking`).
  * nothing checked that a device's stated state matches its variant's line: a «Usado» device entered a NEW variant
    and the web sold it as new, at the new price. Now a used device (condition «used», or any cosmetic grade) into a
    NEW model variant, and a «new» device into a USED variant, are refused (`condition_line_mismatch`). «Caja
    abierta» and «Reacondicionado» in a NEW variant are allowed with a warning (owner's decision pending).

Every check runs in `check()`, which the preview answers with AND `confirm()` runs again, so a stale preview cannot
bypass it. A refusal writes nothing: no unit, no Kardex line, no stock, no switch to «por equipo». Nothing already in
stock changes: these are intake guards only.
"""
from decimal import Decimal

from store import device_catalog
from store.models import CosmeticGrade, Product, ProductModel, StockMovement, StockUnit
from store.test_inventory_receipts import ReceiptBase, unit
from store.test_registration import IMEIS
from store.tests import _prod


class IntakeGuardBase(ReceiptBase):
    def setUp(self):
        super().setUp()
        self.model = device_catalog.create_model(company=self.company, name='iPhone 15 Guardia')
        self.capacity = capacity = device_catalog.create_attribute(company=self.company, name='Capacidad')
        gb128 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='128 GB')
        gb256 = device_catalog.create_option(company=self.company, attribute_id=capacity.pk, label='256 GB')
        # Born as the palette makes them: NOT counted by device, nothing on any shelf.
        self.new_variant = self.variant('iPhone 15 128 GB', 'ip15-guard-128', Product.CONDITION_NEW, gb128)
        self.used_variant = self.variant('iPhone 15 128 GB Seminuevo', 'ip15-guard-128-semi', Product.CONDITION_USED,
                                         gb128)
        # Counted by device already.
        self.new_serial = self.variant('iPhone 15 256 GB', 'ip15-guard-256', Product.CONDITION_NEW, gb256, serial=True)
        self.used_serial = self.variant('iPhone 15 256 GB Seminuevo', 'ip15-guard-256-semi', Product.CONDITION_USED,
                                        gb256, serial=True)
        self.grade = CosmeticGrade.objects.filter(company=self.company, is_active=True).order_by('pk').first()
        self.assertIsNotNone(self.grade)

    def variant(self, name, slug, line, option, *, serial=False):
        product = _prod(self.company, name, slug, price='3000.00', inventory=0)
        Product.objects.filter(pk=product.pk).update(is_serialized=serial, requires_imei=serial)
        product.refresh_from_db()
        return device_catalog.set_variant(product, model_id=self.model.pk, condition_line=line, option_ids=[option.pk])

    def line_of(self, data, index=0):
        return data['lines'][index]

    def assert_refused_everywhere(self, body, code):
        """Refused by the preview AND by a confirmation sent without it, and nothing at all was written."""
        before = self.snapshot()
        movements = list(StockMovement.objects.values_list('pk', flat=True))
        data = self.preview(self.clerk, body, expect=200).json()
        self.assertFalse(data['ok'], data)
        self.assertTrue(any(code in line.get('codes', []) for line in data['lines']), data)
        res = self.confirm(self.clerk, body, expect=400)
        self.assertEqual(res.json()['code'], 'invalid_lines')
        self.assertTrue(any(code in line.get('codes', []) for line in res.json()['lines']), res.json())
        self.assertEqual(self.snapshot(), before)
        # The Kardex is untouched: not one movement more.
        self.assertEqual(list(StockMovement.objects.values_list('pk', flat=True)), movements)
        return data


class UsedQuantityLineTest(IntakeGuardBase):
    def test_a_quantity_line_of_a_used_variant_is_refused_in_the_preview_and_in_the_confirmation(self):
        data = self.assert_refused_everywhere(self.body(self.qty(self.used_variant, 3)), 'used_requires_units')
        line = self.line_of(data)
        self.assertEqual(line['status'], 'error')
        self.assertTrue(any('por equipo' in e and 'seminuev' in e for e in line['errors']), line['errors'])
        self.used_variant.refresh_from_db()
        self.assertFalse(self.used_variant.is_serialized)            # the refusal switched nothing

    def test_a_used_variant_that_already_holds_stock_by_quantity_is_still_refused_and_told_why(self):
        # Data from before this guard: it stays as it is, but no more enters by quantity.
        from store.tests import _m7_stock
        _m7_stock(self.centro, self.used_variant, 2)
        before_shelf = self.shelf(self.used_variant)
        data = self.assert_refused_everywhere(self.body(self.qty(self.used_variant, 1)), 'used_requires_units')
        self.assertTrue(any('stock' in e or 'existencias' in e for e in self.line_of(data)['errors']), data)
        self.assertEqual(self.shelf(self.used_variant), before_shelf)

    def test_the_same_used_variant_by_device_is_received(self):
        self.confirm(self.clerk, self.body(self.dev(self.used_variant, unit('GUSED0001', IMEIS[0], condition='used'))),
                     expect=201)
        self.used_variant.refresh_from_db()
        self.assertTrue(self.used_variant.is_serialized)
        self.assertEqual(StockUnit.objects.get(product=self.used_variant).condition, 'used')


class UnitConditionAgainstLineTest(IntakeGuardBase):
    def test_a_used_device_into_a_new_variant_is_refused(self):
        for product in (self.new_serial, self.new_variant):
            with self.subTest(product=product.name):
                data = self.assert_refused_everywhere(
                    self.body(self.dev(product, unit('GUSEDNEW1', IMEIS[1], condition='used'))),
                    'condition_line_mismatch')
                [issue] = self.line_of(data)['unit_issues']
                self.assertEqual((issue['unit'], issue['field'], issue['kind'], issue['code']),
                                 (0, 'condition', 'error', 'condition_line_mismatch'))
                self.assertIn('seminuev', issue['message'])
        self.new_variant.refresh_from_db()
        self.assertFalse(self.new_variant.is_serialized)

    def test_a_device_with_a_cosmetic_grade_is_used_and_is_refused_in_a_new_variant(self):
        data = self.assert_refused_everywhere(
            self.body(self.dev(self.new_serial, unit('GGRADE001', IMEIS[2], condition='refurbished',
                                                     cosmetic_grade=self.grade.pk))),
            'condition_line_mismatch')
        [issue] = self.line_of(data)['unit_issues']
        self.assertEqual(issue['field'], 'cosmetic_grade')

    def test_a_new_device_into_a_used_variant_is_refused(self):
        for product in (self.used_serial, self.used_variant):
            with self.subTest(product=product.name):
                data = self.assert_refused_everywhere(
                    self.body(self.dev(product, unit('GNEWUSED1', IMEIS[3], condition='new'))),
                    'condition_line_mismatch')
                [issue] = self.line_of(data)['unit_issues']
                self.assertEqual((issue['field'], issue['code']), ('condition', 'condition_line_mismatch'))

    def test_one_bad_device_refuses_the_whole_receipt(self):
        body = self.body(
            self.dev(self.new_serial, unit('GMIX00001', IMEIS[4])),
            self.dev(self.used_serial, unit('GMIX00002', IMEIS[5], condition='used'),
                     unit('GMIX00003', IMEIS[6], condition='new')),
            self.qty(self.case, 5),
        )
        data = self.assert_refused_everywhere(body, 'condition_line_mismatch')
        self.assertEqual([line['status'] for line in data['lines']], ['ok', 'error', 'ok'])
        self.assertEqual([i['unit'] for i in data['lines'][1]['unit_issues']], [1])

    def test_a_device_in_its_own_line_is_received(self):
        self.confirm(self.clerk, self.body(
            self.dev(self.new_serial, unit('GOKNEW001', IMEIS[7])),
            self.dev(self.used_serial, unit('GOKUSED01', IMEIS[8], condition='used', cosmetic_grade=self.grade.pk,
                                            battery_health=90)),
        ), expect=201)
        self.assertEqual(StockUnit.objects.get(serial_number='GOKNEW001').condition, 'new')
        used = StockUnit.objects.get(serial_number='GOKUSED01')
        self.assertEqual((used.condition, used.cosmetic_grade_id, used.battery_health), ('used', self.grade.pk, 90))

    def test_a_used_variant_device_without_a_stated_condition_takes_the_line(self):
        # Before this guard an absent condition was stored «new», even in a used variant.
        row = {'serial': 'GNOCOND01', 'imei': IMEIS[9]}
        data = self.preview(self.clerk, self.body(self.dev(self.used_serial, row)), expect=200).json()
        self.assertTrue(data['ok'], data)
        self.confirm(self.clerk, self.body(self.dev(self.used_serial, row)), expect=201)
        self.assertEqual(StockUnit.objects.get(serial_number='GNOCOND01').condition, 'used')

    def test_open_box_and_refurbished_enter_a_new_variant_with_a_warning(self):
        for n, condition in enumerate(('open_box', 'refurbished')):
            with self.subTest(condition=condition):
                row = unit(f'GOPEN0{n}01', IMEIS[10 + n], condition=condition)
                data = self.preview(self.clerk, self.body(self.dev(self.new_serial, row)), expect=200).json()
                self.assertTrue(data['ok'], data)
                self.assertTrue(any('como nuevo' in w for w in self.line_of(data)['warnings']), data)
                self.confirm(self.clerk, self.body(self.dev(self.new_serial, row)), expect=201)

    def test_a_stale_preview_does_not_bypass_the_confirmation(self):
        gb512 = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='512 GB')
        lone = self.variant('iPhone 15 512 GB', 'ip15-guard-512', Product.CONDITION_NEW, gb512, serial=True)
        body = self.body(self.dev(lone, unit('GSTALE001', IMEIS[12], condition='new')))
        self.assertTrue(self.preview(self.clerk, body, expect=200).json()['ok'])
        # The variant changes line between the preview and the confirmation.
        Product.objects.filter(pk=lone.pk).update(condition_line=Product.CONDITION_USED)
        before = self.snapshot()
        res = self.confirm(self.clerk, body, expect=400)
        self.assertIn('condition_line_mismatch', res.json()['lines'][0]['codes'])
        self.assertEqual(self.snapshot(), before)

    def test_a_product_outside_the_catalogue_of_models_keeps_its_rules(self):
        # `self.phone` has no model: a used device still enters it, as before (owner's data is not second-guessed).
        self.confirm(self.clerk, self.body(self.dev(self.phone, unit('GLEGACY01', IMEIS[13], condition='used'))),
                     expect=201)


class AccessoryAndNewVariantQuantityTest(IntakeGuardBase):
    def test_an_accessory_quantity_line_is_received_without_warnings(self):
        data = self.preview(self.clerk, self.body(self.qty(self.case, 4)), expect=200).json()
        line = self.line_of(data)
        self.assertEqual((line['status'], line['warnings'], line['codes']), ('ok', [], []))
        self.confirm(self.clerk, self.body(self.qty(self.case, 4)), expect=201)
        self.assertEqual(self.shelf(self.case), 4)

    def test_a_new_device_variant_by_quantity_is_warned_explicitly_and_coded(self):
        data = self.preview(self.clerk, self.body(self.qty(self.new_variant, 2)), expect=200).json()
        line = self.line_of(data)
        self.assertEqual(line['status'], 'ok')
        self.assertEqual(line['codes'], ['quantity_locks_tracking'])
        self.assertTrue(any('ya no se podrá' in w for w in line['warnings']), line['warnings'])
        # A deliberate choice is still received.
        self.confirm(self.clerk, self.body(self.qty(self.new_variant, 2)), expect=201)
        self.assertEqual(self.shelf(self.new_variant), 2)
        self.new_variant.refresh_from_db()
        self.assertFalse(self.new_variant.is_serialized)

    def test_a_new_device_variant_by_device_is_received_and_switched(self):
        data = self.preview(self.clerk, self.body(self.dev(self.new_variant, unit('GNEWVAR01', IMEIS[14]))),
                            expect=200).json()
        self.assertTrue(data['ok'], data)
        self.assertNotIn('quantity_locks_tracking', self.line_of(data)['codes'])
        self.confirm(self.clerk, self.body(self.dev(self.new_variant, unit('GNEWVAR01', IMEIS[14]))), expect=201)
        self.new_variant.refresh_from_db()
        self.assertTrue(self.new_variant.is_serialized)

    def test_a_new_variant_with_stock_by_quantity_receives_more_by_quantity_without_the_warning(self):
        # It can no longer change: the warning would be noise. Existing stock is not second-guessed.
        from store.tests import _m7_stock
        _m7_stock(self.centro, self.new_variant, 3)
        data = self.preview(self.clerk, self.body(self.qty(self.new_variant, 1)), expect=200).json()
        self.assertEqual((self.line_of(data)['status'], self.line_of(data)['codes']), ('ok', []))


class TenancyUnchangedTest(IntakeGuardBase):
    def test_a_foreign_used_variant_is_not_found_before_any_guard_speaks(self):
        foreign_model = ProductModel.objects.create(company=self.other, name='Ajeno', slug='ajeno-guardia')
        foreign = _prod(self.other, 'Ajeno Seminuevo', 'ajeno-guard-semi', price='1.00', inventory=0)
        Product.objects.filter(pk=foreign.pk).update(product_model=foreign_model,
                                                     condition_line=Product.CONDITION_USED, price=Decimal('1.00'))
        res = self.preview(self.clerk, self.body(self.qty(foreign, 1)), expect=404)
        self.assertNotIn('codes', res.json())
