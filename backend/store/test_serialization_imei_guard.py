"""
SERIALIZATION-IMEI-GUARD-01 — «lleva IMEI» on a product already counted by device.

POST /api/admin/inventory/units/products/<pk>/serialization/ only needs `inventory.adjust`. Before this:

  * `{is_serialized: true}` WITHOUT `requires_imei` reset «lleva IMEI» to False on a product that had it;
  * `set_serialized` changed `requires_imei` without looking at the devices already registered.

Now, on a product that is ALREADY serialized:

  * an omitted `requires_imei` keeps the current value;
  * changing it (on or off) needs `products.manage` — 403 otherwise, nothing written;
  * turning it ON is refused (409, with the reason and a count, never an identifier) while a device of the product
    (any state but voided) has no valid IMEI;
  * an accepted change is audited with the value before and after.

The first switch of a product with no stock (the panel's «activar control por equipo») is unchanged.

Review of #444 — the same rule on every door, without a history column:

  * turning «lleva IMEI» OFF needs `products.manage` however it is asked: `requires_imei=false` on a serialized
    product, or un-serializing a product that carries it (`is_serialized=false` turns it off by consequence). So a
    product that had it only ever loses it by the catalogue's decision, and a later first switch that leaves it off is
    keeping that decision, not changing it (the receipt, the registration and the POS only ever make a first switch);
  * turning it ON on a product already counted by device needs `products.manage`; on a first switch it does not (the
    receipt, the registration, the POS and the panel do it with «Mover inventario», as before);
  * turning it ON — first switch or not — is refused while a device of the product (any state but voided, e.g. one
    sold during an earlier serialized period) has no valid IMEI;
  * `set_serialized(..., may_change_requires_imei=False)` by default: every caller says it;
  * `receive_units` reads «lleva IMEI» again under the product row its Kardex line holds: a switch that commits while
    devices are being received cannot leave devices without IMEI under a product that says they carry one.
"""
from store import stock_unit_services as units
from store.models import AdminAuditLog, Product, StockUnit
from store.models import BranchStock
from store.test_inventory_receipts import ReceiptBase, unit
from store.test_registration import RegistrationFixture, _client
from store.test_stock_units import imei
from store.tests import _p2d_member, _prod

#: Luhn-valid synthetic IMEIs, a range no other suite uses.
IMEIS = [imei(f'35693803692{n:03d}') for n in range(0, 20)]
ACTION = 'product_serialization_changed'


def url(product):
    return f'/api/admin/inventory/units/products/{product.pk}/serialization/'


class GuardBase(ReceiptBase):
    def setUp(self):
        super().setUp()
        self.manager, _ = _p2d_member(self.company, 'imei_manager',
                                      ['inventory.view', 'inventory.adjust', 'products.manage'])
        self.foreign_manager, _ = _p2d_member(self.other, 'imei_foreign',
                                              ['inventory.view', 'inventory.adjust', 'products.manage'])
        self._imeis = iter(IMEIS)

    def post(self, user, product, body):
        return _client(user).post(url(product), body, format='json')

    def reread(self, product):
        return Product.objects.get(pk=product.pk)

    def laptop_unit(self, serial, imei_value=''):
        """A device of the laptop (no IMEI required): with no IMEI unless one is given."""
        row = {'serial_number': serial, **({'imei': imei_value} if imei_value else {})}
        return units.receive_units(branch=self.centro, product=self.laptop, units=[row], actor=self.manager,
                                   reason='Compra')[0]

    def audits(self):
        return AdminAuditLog.objects.filter(action=ACTION).order_by('pk')


class OmittedKeepsTest(GuardBase):
    def test_an_omitted_requires_imei_keeps_it_on(self):
        res = self.post(self.clerk, self.phone, {'is_serialized': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['requires_imei'])
        self.assertTrue(self.reread(self.phone).requires_imei)

    def test_an_omitted_requires_imei_keeps_it_off(self):
        res = self.post(self.clerk, self.laptop, {'is_serialized': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(self.reread(self.laptop).requires_imei)

    def test_the_same_value_is_no_change_and_needs_no_catalogue_authority(self):
        res = self.post(self.clerk, self.phone, {'is_serialized': True, 'requires_imei': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(self.reread(self.phone).requires_imei)

    def test_a_requires_imei_that_is_not_a_boolean_is_a_400(self):
        res = self.post(self.manager, self.laptop, {'is_serialized': True, 'requires_imei': 'false'})
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('field'), 'requires_imei')
        self.assertFalse(self.reread(self.laptop).requires_imei)


class AuthorityTest(GuardBase):
    def test_turning_it_off_without_products_manage_is_403(self):
        before = self.audits().count()
        res = self.post(self.clerk, self.phone, {'is_serialized': True, 'requires_imei': False})
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json().get('field'), 'requires_imei')
        self.assertTrue(self.reread(self.phone).requires_imei)
        self.assertEqual(self.audits().count(), before)

    def test_turning_it_on_without_products_manage_is_403(self):
        res = self.post(self.clerk, self.laptop, {'is_serialized': True, 'requires_imei': True})
        self.assertEqual(res.status_code, 403, res.content)
        self.assertFalse(self.reread(self.laptop).requires_imei)

    def test_turning_it_off_with_products_manage_is_audited(self):
        res = self.post(self.manager, self.phone, {'is_serialized': True, 'requires_imei': False})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(self.reread(self.phone).requires_imei)
        audit = self.audits().last()
        self.assertEqual(audit.actor, self.manager)
        self.assertEqual(audit.company, self.company)
        self.assertEqual(audit.metadata.get('requires_imei'), False)
        self.assertEqual(audit.metadata.get('requires_imei_before'), True)

    def test_without_inventory_adjust_nothing_is_reached(self):
        res = self.post(self.viewer, self.phone, {'is_serialized': True, 'requires_imei': False})
        self.assertEqual(res.status_code, 403, res.content)
        self.assertTrue(self.reread(self.phone).requires_imei)

    def test_the_first_switch_of_an_empty_product_is_unchanged(self):
        empty = _prod(self.company, 'Watch Guard', 'watch-guard', inventory=0)
        res = self.post(self.clerk, empty, {'is_serialized': True, 'requires_imei': True})
        self.assertEqual(res.status_code, 200, res.content)
        empty = self.reread(empty)
        self.assertEqual((empty.is_serialized, empty.requires_imei), (True, True))


class UnitsWithoutImeiTest(GuardBase):
    def assert_refused(self, res):
        self.assertEqual(res.status_code, 409, res.content)
        body = res.json()
        self.assertEqual(body.get('code'), units.UNITS_WITHOUT_IMEI)
        self.assertEqual(body.get('field'), 'requires_imei')
        self.assertIn('IMEI', body.get('detail', ''))
        self.assertFalse(self.reread(self.laptop).requires_imei)

    def test_a_device_without_imei_refuses_turning_it_on(self):
        self.laptop_unit('GRD-0001')
        before = self.audits().count()
        self.assert_refused(self.post(self.manager, self.laptop, {'is_serialized': True, 'requires_imei': True}))
        self.assertEqual(self.audits().count(), before)

    def test_a_device_with_an_invalid_imei_refuses_turning_it_on(self):
        device = self.laptop_unit('GRD-0101', next(self._imeis))
        StockUnit.objects.filter(pk=device.pk).update(imei='12345')        # an old, unchecked value
        self.assert_refused(self.post(self.manager, self.laptop, {'is_serialized': True, 'requires_imei': True}))

    def test_a_sold_device_without_imei_still_refuses(self):
        device = self.laptop_unit('GRD-0201')
        StockUnit.objects.filter(pk=device.pk).update(status=StockUnit.Status.SOLD)
        self.assert_refused(self.post(self.manager, self.laptop, {'is_serialized': True, 'requires_imei': True}))

    def test_the_refusal_never_names_a_serial(self):
        self.laptop_unit('GRD-SECRET-9')
        res = self.post(self.manager, self.laptop, {'is_serialized': True, 'requires_imei': True})
        self.assertNotIn('GRD-SECRET-9', res.content.decode())

    def test_a_voided_device_does_not_block(self):
        device = self.laptop_unit('GRD-0301')
        units.void_unit(device, reason='Se digitó mal', reason_code='typo', actor=self.manager)
        res = self.post(self.manager, self.laptop, {'is_serialized': True, 'requires_imei': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(self.reread(self.laptop).requires_imei)

    def test_every_device_with_a_valid_imei_lets_it_turn_on_and_it_is_audited(self):
        self.laptop_unit('GRD-0401', next(self._imeis))
        res = self.post(self.manager, self.laptop, {'is_serialized': True, 'requires_imei': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(self.reread(self.laptop).requires_imei)
        audit = self.audits().last()
        self.assertEqual((audit.metadata.get('requires_imei_before'), audit.metadata.get('requires_imei')),
                         (False, True))

    def test_the_service_refuses_it_too(self):
        self.laptop_unit('GRD-0501')
        with self.assertRaises(units.StockUnitError) as caught:
            units.set_serialized(self.laptop, serialized=True, requires_imei=True, actor=self.manager,
                                 may_change_requires_imei=True)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, units.UNITS_WITHOUT_IMEI))
        self.assertFalse(self.reread(self.laptop).requires_imei)

    def test_the_service_keeps_the_value_when_none_is_given(self):
        units.set_serialized(self.phone, serialized=True, requires_imei=None, actor=self.manager)
        self.assertTrue(self.reread(self.phone).requires_imei)

    def test_the_service_refuses_by_default(self):
        with self.assertRaises(units.StockUnitError) as caught:
            units.set_serialized(self.phone, serialized=True, requires_imei=False, actor=self.manager)
        self.assertEqual((caught.exception.status, caught.exception.code), (403, units.REQUIRES_IMEI_FORBIDDEN))
        self.assertTrue(self.reread(self.phone).requires_imei)


class UnserializeTest(GuardBase):
    """Review of #444 — un-serializing turns «lleva IMEI» off: the catalogue's decision, like turning it off."""

    def test_unserializing_a_product_with_imei_without_products_manage_is_403(self):
        before = self.audits().count()
        res = self.post(self.clerk, self.phone, {'is_serialized': False})
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json().get('code'), units.REQUIRES_IMEI_FORBIDDEN)
        phone = self.reread(self.phone)
        self.assertEqual((phone.is_serialized, phone.requires_imei), (True, True))
        self.assertEqual(self.audits().count(), before)

    def test_the_two_call_sequence_cannot_drop_the_imei(self):
        self.assertEqual(self.post(self.clerk, self.phone, {'is_serialized': False}).status_code, 403)
        res = self.post(self.clerk, self.phone, {'is_serialized': True, 'requires_imei': False})
        self.assertEqual(res.status_code, 403, res.content)
        phone = self.reread(self.phone)
        self.assertEqual((phone.is_serialized, phone.requires_imei), (True, True))

    def test_the_service_refuses_it_too(self):
        with self.assertRaises(units.StockUnitError) as caught:
            units.set_serialized(self.phone, serialized=False, actor=self.manager)
        self.assertEqual((caught.exception.status, caught.exception.code), (403, units.REQUIRES_IMEI_FORBIDDEN))

    def test_with_products_manage_it_is_unserialized_and_audited(self):
        res = self.post(self.manager, self.phone, {'is_serialized': False})
        self.assertEqual(res.status_code, 200, res.content)
        phone = self.reread(self.phone)
        self.assertEqual((phone.is_serialized, phone.requires_imei), (False, False))
        audit = self.audits().last()
        self.assertEqual((audit.metadata['is_serialized_before'], audit.metadata['requires_imei_before']),
                         (True, True))
        # Then a first switch that leaves it off keeps the catalogue's decision: «Mover inventario» is enough.
        res = self.post(self.clerk, self.phone, {'is_serialized': True, 'requires_imei': False})
        self.assertEqual(res.status_code, 200, res.content)

    def test_unserializing_a_product_without_imei_needs_no_catalogue_authority(self):
        res = self.post(self.clerk, self.laptop, {'is_serialized': False})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(self.reread(self.laptop).is_serialized)


class FirstSwitchTest(GuardBase):
    """Review of #444 — the first switch sets «lleva IMEI» as before, but never over devices without one."""

    def laptop_with_a_sold_device_and_no_stock(self):
        device = self.laptop_unit('GRD-OLD-1')
        StockUnit.objects.filter(pk=device.pk).update(status=StockUnit.Status.SOLD)
        BranchStock.objects.filter(product=self.laptop).update(quantity=0)
        Product.objects.filter(pk=self.laptop.pk).update(inventory=0)
        res = self.post(self.clerk, self.laptop, {'is_serialized': False})
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_first_switch_on_with_old_devices_without_imei_is_409(self):
        self.laptop_with_a_sold_device_and_no_stock()
        res = self.post(self.clerk, self.laptop, {'is_serialized': True, 'requires_imei': True})
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json().get('code'), units.UNITS_WITHOUT_IMEI)
        self.assertFalse(self.reread(self.laptop).is_serialized)

    def test_a_first_switch_on_without_old_devices_needs_no_catalogue_authority(self):
        empty = _prod(self.company, 'Watch Guard 2', 'watch-guard-2', inventory=0)
        res = self.post(self.clerk, empty, {'is_serialized': True, 'requires_imei': True})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(self.reread(empty).requires_imei)

    def test_the_receipt_s_first_switch_refuses_over_old_devices_without_imei(self):
        self.laptop_with_a_sold_device_and_no_stock()
        body = self.body(self.dev(self.laptop, unit('GRD-NEW-1', next(self._imeis))))
        self.assertEqual(self.confirm(self.costed, body).status_code, 409)
        laptop = self.reread(self.laptop)
        self.assertEqual((laptop.is_serialized, laptop.requires_imei), (False, False))
        self.assertFalse(StockUnit.objects.filter(serial_number='GRD-NEW-1').exists())

    def test_the_receipt_never_turns_the_imei_off(self):
        body = self.body(self.dev(self.phone, unit('GRD-NO-IMEI')))
        self.assertFalse(self.preview(self.costed, body, expect=200).json()['ok'])
        self.confirm(self.costed, body, expect=400)
        self.assertTrue(self.reread(self.phone).requires_imei)


class ReceiveRaceTest(GuardBase):
    """Review of #444 — «lleva IMEI» switched on while devices are received: read again under the product row."""

    def test_a_stale_product_cannot_bring_devices_without_imei_under_a_product_that_requires_one(self):
        stale = Product.objects.get(pk=self.laptop.pk)              # read before the switch
        self.laptop_unit('GRD-R-0', next(self._imeis))               # every device so far has an IMEI
        units.set_serialized(self.laptop, serialized=True, requires_imei=True, actor=self.manager,
                             may_change_requires_imei=True)          # committed meanwhile
        movements = self.snapshot()
        with self.assertRaises(units.StockUnitError) as caught:
            units.receive_units(branch=self.centro, product=stale, units=[{'serial_number': 'GRD-R-1'}],
                                actor=self.manager, reason='Compra')
        self.assertEqual(caught.exception.code, units.UNITS_WITHOUT_IMEI)
        self.assertEqual(self.snapshot(), movements)
        self.assertEqual(units.units_without_valid_imei(self.laptop), 0)

    def test_a_stale_product_with_imeis_is_received(self):
        stale = Product.objects.get(pk=self.laptop.pk)
        units.set_serialized(self.laptop, serialized=True, requires_imei=True, actor=self.manager,
                             may_change_requires_imei=True)
        units.receive_units(branch=self.centro, product=stale, actor=self.manager, reason='Compra',
                            units=[{'serial_number': 'GRD-R-2', 'imei': next(self._imeis)}])
        self.assertTrue(StockUnit.objects.filter(serial_number='GRD-R-2').exists())


class CompanyIsolationTest(GuardBase):
    def test_a_product_of_another_company_is_404(self):
        res = self.post(self.manager, self.foreign_phone, {'is_serialized': True, 'requires_imei': False})
        self.assertEqual(res.status_code, 404, res.content)
        self.assertTrue(self.reread(self.foreign_phone).requires_imei)

    def test_a_manager_of_another_company_gets_404_here(self):
        res = self.post(self.foreign_manager, self.phone, {'is_serialized': True, 'requires_imei': False})
        self.assertEqual(res.status_code, 404, res.content)
        self.assertTrue(self.reread(self.phone).requires_imei)

    def test_devices_of_another_company_never_block_this_one(self):
        units.receive_units(branch=self.foreign_branch, product=self.foreign_phone, actor=self.foreign_manager,
                            reason='Compra', units=[{'serial_number': 'GRD-F-1', 'imei': next(self._imeis)}])
        Product.objects.filter(pk=self.foreign_phone.pk).update(requires_imei=False)
        StockUnit.objects.filter(serial_number='GRD-F-1').update(imei=None)
        res = self.post(self.manager, self.laptop, {'is_serialized': True, 'requires_imei': True})
        self.assertEqual(res.status_code, 200, res.content)


class RegistrationDoorTest(RegistrationFixture):
    """Review of #444 — the guided registration (and the POS, which calls it) never turns «lleva IMEI» off."""

    def test_an_existing_variant_with_imei_keeps_it_when_the_registration_says_otherwise(self):
        self.post(self.clerk, self.new_device(), expect=201)
        product = Product.objects.get(company=self.company, product_model=self.model)
        self.assertTrue(product.requires_imei)
        body = self.new_device(requires_imei=False, units=[{'serial_number': 'SNREG-NOIMEI'}])
        body['product'] = {k: v for k, v in body['product'].items() if k != 'sku'}
        res = self.post(self.clerk, body)
        self.assertGreaterEqual(res.status_code, 400, res.content)
        product.refresh_from_db()
        self.assertTrue(product.requires_imei)
        self.assertFalse(StockUnit.objects.filter(serial_number='SNREG-NOIMEI').exists())
