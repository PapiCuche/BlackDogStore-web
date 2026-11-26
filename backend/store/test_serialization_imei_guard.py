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
"""
from store import stock_unit_services as units
from store.models import AdminAuditLog, Product, StockUnit
from store.test_inventory_receipts import ReceiptBase
from store.test_registration import _client
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
            units.set_serialized(self.laptop, serialized=True, requires_imei=True, actor=self.manager)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, units.UNITS_WITHOUT_IMEI))
        self.assertFalse(self.reread(self.laptop).requires_imei)

    def test_the_service_keeps_the_value_when_none_is_given(self):
        units.set_serialized(self.phone, serialized=True, requires_imei=None, actor=self.manager)
        self.assertTrue(self.reread(self.phone).requires_imei)


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
