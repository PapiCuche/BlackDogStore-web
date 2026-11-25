"""
SCAN-TO-STOCK-01 — «Registrar equipo por escaneo»: the check of each read and the explicit confirmation.

What these tests fix:

  * a read is classified and looked up INSIDE the caller's company only: an IMEI already registered says which device
    (sold, written off or on the shelf: all of them keep it; a voided one frees it); an IMEI of another company says
    nothing; a commercial code (UPC/EAN) names its product and never a unit; an EID or a MEID is never an IMEI;
  * confirming a NEW device is the till's «Llegó el equipo» (variant from the palette, pending paid order first,
    Kardex, label, idempotency) with the Kardex saying where it came from; a USED device enters its used variant
    pending inspection and unpublished, its price and warranty only with `products.manage`, its cost with `costs.view`;
  * a device registered by scan sells at the till by scanning its IMEI;
  * permissions: `inventory.adjust`, the caller's branches, another company = 404;
  * concurrency: two clerks, the same IMEI at once → one device; the same confirmation twice → one device.

Names are invented (AGENTS §2); IMEIs are synthetic with a valid Luhn digit.
"""
import os
from decimal import Decimal as D
from threading import Barrier, Thread

from django.db import connection
from django.test import TestCase, TransactionTestCase

from store import device_catalog
from store import inventory_services as inventory
from store import stock_unit_services as units
from store.models import (
    AdminAuditLog, Branch, OrderItem, OrderUnit, Product, ProductBarcode, StockMovement, StockUnit,
)
from store.test_pos_order_arrival import ArrivalShop
from store.test_pos_quick_device import MANAGER_CAPS, RECEIVER_CAPS, QuickShop, _api
from store.test_scan_classifier import gtin
from store.test_stock_units import imei
from store.tests import _c1_sale, _p2d_member, _p3_company, _prod

SCAN_IMEIS = [imei(f'35000000{n:06d}') for n in range(80)]
#: The stockroom: registers devices, does not use the till.
CLERK_CAPS = ['company.view', 'products.view', 'inventory.view', 'inventory.adjust']
VIEWER_CAPS = ['company.view', 'inventory.view']
CHECK = '/api/admin/inventory/scan/check/'


class ScanShop(QuickShop):
    """`QuickShop` plus a USED variant of the same model and a stockroom clerk without the till."""

    def build(self, slug='scan-tienda'):
        super().build(slug)
        self._scan = iter(SCAN_IMEIS)
        self.clerk, _ = _p2d_member(self.company, f'{slug}_bodega', CLERK_CAPS)
        used = _prod(self.company, 'Teléfono Q 128 GB Negro (seminuevo)', f'{slug}-q-used', price='2000.00',
                     inventory=0)
        Product.objects.filter(pk=used.pk).update(is_serialized=True, requires_imei=True)
        self.used = device_catalog.set_variant(
            Product.objects.get(pk=used.pk), model_id=self.model.pk, condition_line='used',
            option_ids=[self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk])
        self.sq = f'?company={self.company.pk}&branch={self.branch.pk}'

    def fresh(self):
        return next(self._scan)

    def check(self, user, code, **extra):
        return _api(user).post(f'{CHECK}?company={self.company.pk}', {'code': code, **extra}, format='json')

    def scan_receive(self, user, body, key='s-1', branch=None):
        q = f'?company={self.company.pk}&branch={(branch or self.branch).pk}'
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return _api(user).post(f'/api/admin/inventory/scan/receive/{q}', body, format='json', **headers)

    def new_body(self, **extra):
        body = {'condition': 'new', 'model': self.model.pk, 'method': 'scanner',
                'option_ids': [self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk], 'imei': self.fresh()}
        body.update(extra)
        return body

    def used_body(self, **extra):
        body = {'condition': 'used', 'product_id': self.used.pk, 'method': 'scanner', 'imei': self.fresh(),
                'battery_health': 87, 'functional_state': 'full'}
        body.update(extra)
        return body


# ---------------------------------------------------------------------------
# Each read
# ---------------------------------------------------------------------------

class CheckTest(ScanShop, TestCase):
    def setUp(self):
        self.build('scan-check')

    def reading(self, res):
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_a_valid_free_imei_is_an_imei_with_nothing_registered(self):
        body = self.reading(self.check(self.clerk, self.fresh(), expected='imei'))
        self.assertEqual((body['reading']['kind'], body['reading']['certain']), ('imei', True))
        self.assertIsNone(body['unit'])
        self.assertIsNone(body['product'])

    def test_fifteen_luhn_digits_while_the_serial_is_expected_are_asked(self):
        body = self.reading(self.check(self.clerk, self.fresh(), expected='serial'))
        self.assertEqual((body['reading']['kind'], body['reading']['certain']), ('imei', False))
        self.assertEqual(body['reading']['alternatives'], ['imei', 'serial'])
        res = self.check(self.clerk, self.fresh(), expected='model')
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_meid_shaped_code_is_still_looked_up_as_a_serial(self):
        unit = units.receive_units(branch=self.branch, product=self.existing, actor=self.manager, reason='Compra',
                                   units=[{'imei': self.fresh(), 'serial_number': 'A0000012345678'}])[0]
        body = self.reading(self.check(self.clerk, 'A0000012345678', product_id=self.existing.pk))
        self.assertEqual(body['reading']['kind'], 'meid')
        self.assertEqual(body['unit']['id'], unit.pk)

    def test_an_imei_that_fails_luhn_is_not_an_imei(self):
        value = self.fresh()
        body = self.reading(self.check(self.clerk, value[:-1] + str((int(value[-1]) + 1) % 10)))
        self.assertEqual(body['reading']['problem'], 'imei_check_digit')
        self.assertFalse(body['reading']['certain'])

    def test_an_imei_already_on_the_shelf_names_its_device(self):
        unit = self.here[0]
        body = self.reading(self.check(self.clerk, unit.imei))
        self.assertEqual(body['unit']['standing'], 'registered')
        self.assertEqual((body['unit']['id'], body['unit']['label_code']), (unit.pk, unit.label_code))

    def test_a_second_imei_counts_as_well(self):
        second = self.fresh()
        unit = units.receive_units(branch=self.branch, product=self.existing, actor=self.manager, reason='Compra',
                                   units=[{'imei': self.fresh(), 'imei2': second}])[0]
        self.assertEqual(self.reading(self.check(self.clerk, second))['unit']['id'], unit.pk)

    def test_a_sold_device_keeps_its_imei_and_says_so(self):
        unit = self.here[0]
        StockUnit.objects.filter(pk=unit.pk).update(status=StockUnit.Status.SOLD)
        body = self.reading(self.check(self.clerk, unit.imei))
        self.assertEqual(body['unit']['standing'], 'sold')
        self.assertIn('devolución', body['unit']['detail'])

    def test_a_written_off_device_keeps_its_imei(self):
        unit = units.write_off(self.here[0], reason='Pantalla rota', actor=self.manager)
        body = self.reading(self.check(self.clerk, unit.imei))
        self.assertEqual(body['unit']['standing'], 'written_off')

    def test_a_voided_device_frees_its_imei(self):
        unit = self.here[0]
        StockUnit.objects.filter(pk=unit.pk).update(status=StockUnit.Status.VOIDED, voided_at='2026-11-01T00:00Z')
        self.assertIsNone(self.reading(self.check(self.clerk, unit.imei))['unit'])

    def test_a_commercial_code_names_its_product_never_a_unit(self):
        code = gtin('19000000001')
        ProductBarcode.objects.create(company=self.company, product=self.existing, code=code)
        body = self.reading(self.check(self.clerk, code))
        self.assertEqual(body['reading']['kind'], 'gtin')
        self.assertEqual(body['product']['id'], self.existing.pk)
        self.assertEqual(body['product']['option_ids'],
                         sorted([self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk]))
        self.assertIsNone(body['unit'])

    def test_a_product_code_shaped_like_a_serial_is_a_conflict(self):
        ProductBarcode.objects.create(company=self.company, product=self.existing, code='QPROD12345')
        body = self.reading(self.check(self.clerk, 'QPROD12345'))
        self.assertEqual(body['conflict']['code'], 'product_code')
        self.assertEqual(body['product']['id'], self.existing.pk)

    def test_an_eid_and_a_meid_are_never_looked_up_as_an_imei(self):
        for code, kind in (('8904' + '0' * 28, 'eid'), (self.here[0].imei[:14], 'meid')):
            with self.subTest(kind=kind):
                body = self.reading(self.check(self.clerk, code))
                self.assertEqual(body['reading']['kind'], kind)
                self.assertIsNone(body['unit'])

    def test_a_serial_in_the_same_variant_is_a_duplicate_and_in_another_a_warning(self):
        serial = self.here[0].serial_number
        same = self.reading(self.check(self.clerk, serial, product_id=self.existing.pk))
        self.assertEqual(same['unit']['id'], self.here[0].pk)
        other = self.reading(self.check(self.clerk, serial, product_id=self.used.pk))
        self.assertIsNone(other['unit'])
        self.assertEqual(other['serial_elsewhere'], self.existing.name)

    def test_a_serial_read_before_the_variant_warns_where_it_already_is(self):
        body = self.reading(self.check(self.clerk, self.here[0].serial_number))
        self.assertIsNone(body['unit'])
        self.assertEqual(body['serial_elsewhere'], self.existing.name)

    def test_the_internal_label_names_its_device(self):
        unit = self.here[0]
        body = self.reading(self.check(self.clerk, unit.label_code.lower()))
        self.assertEqual((body['reading']['kind'], body['unit']['id']), ('label_code', unit.pk))

    def test_the_clerk_says_what_a_doubtful_code_is_and_the_rules_still_apply(self):
        body = self.reading(self.check(self.clerk, '1234567890', choice='serial'))
        self.assertEqual((body['reading']['kind'], body['reading']['certain']), ('serial', True))
        value = self.fresh()
        res = self.check(self.clerk, value[:-1] + str((int(value[-1]) + 1) % 10), choice='imei')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['code'], 'invalid_choice')

    def test_another_companys_device_and_codes_are_never_told(self):
        other = _p3_company('scan-otra', 'Otra tienda')
        product = _prod(other, 'Teléfono ajeno', 'scan-otra-p', price='10.00', inventory=0)
        Product.objects.filter(pk=product.pk).update(is_serialized=True, requires_imei=True)
        product.refresh_from_db()
        foreign = units.receive_units(branch=other.default_inventory_branch, product=product,
                                      units=[{'imei': self.fresh()}], reason='Compra')[0]
        code = gtin('19000000002')
        ProductBarcode.objects.create(company=other, product=product, code=code)
        body = self.reading(self.check(self.clerk, foreign.imei))
        self.assertIsNone(body['unit'])
        self.assertIsNone(self.reading(self.check(self.clerk, code))['product'])
        self.assertNotIn('ajeno', str(body))
        # And registering it here is allowed: an IMEI is unique per company.
        res = self.scan_receive(self.clerk, self.new_body(imei=foreign.imei))
        self.assertEqual(res.status_code, 201, res.content)

    def test_a_device_outside_the_callers_branches_exists_without_its_details(self):
        limited, _ = _p2d_member(self.company, 'scan-check-norte', CLERK_CAPS, branches=[self.north])
        body = self.reading(self.check(limited, self.here[0].imei))
        self.assertEqual(body['unit']['standing'], 'elsewhere')
        self.assertNotIn('id', body['unit'])
        self.assertNotIn('product_name', body['unit'])

    def test_out_of_branch_a_sold_or_written_off_device_tells_no_status(self):
        limited, _ = _p2d_member(self.company, 'scan-check-norte2', CLERK_CAPS, branches=[self.north])
        sold, gone = self.here[0], self.here[1]
        StockUnit.objects.filter(pk=sold.pk).update(status=StockUnit.Status.SOLD)
        units.write_off(gone, reason='Robado', actor=self.manager)
        for unit in (sold, gone):
            with self.subTest(status=unit.pk):
                brief = self.reading(self.check(limited, unit.imei))['unit']
                self.assertEqual(set(brief), {'standing', 'detail'})
                self.assertEqual(brief['standing'], 'elsewhere')
                for word in ('vend', 'baja', self.branch.name, self.existing.name):
                    self.assertNotIn(word, brief['detail'])

    def test_out_of_branch_a_serial_elsewhere_or_a_label_does_not_name_the_product(self):
        limited, _ = _p2d_member(self.company, 'scan-check-norte3', CLERK_CAPS, branches=[self.north])
        body = self.reading(self.check(limited, self.here[0].serial_number, product_id=self.used.pk))
        self.assertEqual(body['serial_elsewhere'], '')
        self.assertTrue(body['serial_elsewhere_hidden'])
        self.assertNotIn(self.existing.name, str(body))

    def test_without_inventory_adjust_nothing_is_read(self):
        viewer, _ = _p2d_member(self.company, 'scan-check-ver', VIEWER_CAPS)
        self.assertEqual(self.check(viewer, self.fresh()).status_code, 403)

    def test_a_bad_body_is_400(self):
        res = _api(self.clerk).post(f'{CHECK}?company={self.company.pk}', {'code': 12}, format='json')
        self.assertEqual(res.status_code, 400)


# ---------------------------------------------------------------------------
# A new device
# ---------------------------------------------------------------------------

class ReceiveNewTest(ScanShop, TestCase):
    def setUp(self):
        self.build('scan-nuevo')

    def test_registers_into_the_existing_variant_with_kardex_and_label(self):
        before = inventory.branch_quantity(self.branch, self.existing)
        body = self.new_body(imei2=self.fresh(), serial_number='QZ7K2M9XW1', supplier=self.supplier.pk)
        res = self.scan_receive(self.clerk, body)
        self.assertEqual(res.status_code, 201, res.content)
        answer = res.json()
        unit = StockUnit.objects.get(pk=answer['unit']['id'])
        self.assertEqual((unit.company, unit.branch, unit.product, unit.condition, unit.status),
                         (self.company, self.branch, self.existing, 'new', 'available'))
        self.assertEqual((unit.imei, unit.imei2, unit.serial_number), (body['imei'], body['imei2'], 'QZ7K2M9XW1'))
        self.assertEqual((unit.supplier, unit.created_by), (self.supplier, self.clerk))
        self.assertTrue(unit.label_code)
        self.assertEqual(inventory.branch_quantity(self.branch, self.existing), before + 1)
        movement = StockMovement.objects.filter(product=self.existing).latest('pk')
        self.assertEqual((movement.movement_type, movement.branch, movement.actor, movement.reason),
                         (StockMovement.PURCHASE_ENTRY, self.branch, self.clerk, 'Inventario: registro por escaneo'))
        self.assertEqual(movement.company, self.company)
        self.assertIsNotNone(movement.created_at)
        self.assertIn(unit.pk, movement.metadata.get('unit_ids', []))
        self.assertEqual(answer['method'], 'scanner')
        self.assertIsNotNone(answer['label_batch'])
        self.assertNotIn('cost', answer['unit'])
        self.assert_invariant()

    def test_imei1_equal_to_imei2_is_refused(self):
        value = self.fresh()
        res = self.scan_receive(self.clerk, self.new_body(imei=value, imei2=value))
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'imei2')

    def test_a_duplicate_imei_is_409_and_nothing_is_written(self):
        before = StockUnit.objects.filter(company=self.company).count()
        res = self.scan_receive(self.clerk, self.new_body(imei=self.here[0].imei))
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'duplicate_imei')
        self.assertEqual(StockUnit.objects.filter(company=self.company).count(), before)

    def test_sold_and_written_off_imeis_cannot_enter_again(self):
        sold, gone = self.here[0], self.here[1]
        StockUnit.objects.filter(pk=sold.pk).update(status=StockUnit.Status.SOLD)
        units.write_off(gone, reason='Robado', actor=self.manager)
        for n, value in enumerate((sold.imei, gone.imei)):
            res = self.scan_receive(self.clerk, self.new_body(imei=value), key=f'dup-{n}')
            self.assertEqual(res.status_code, 409, res.content)

    def test_a_duplicate_serial_in_the_variant_is_409(self):
        res = self.scan_receive(self.clerk, self.new_body(serial_number=self.here[0].serial_number))
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'duplicate_serial')

    def test_a_commercial_code_cannot_be_a_units_serial(self):
        # A GTIN-shaped one is refused by its shape (`serial_not_a_serial`); any other product code by LABEL-02's rule.
        code = 'QPROD7X12345'
        ProductBarcode.objects.create(company=self.company, product=self.existing, code=code)
        res = self.scan_receive(self.clerk, self.new_body(serial_number=code))
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'product_code_conflict')

    def test_an_eid_is_never_accepted_as_an_imei(self):
        res = self.scan_receive(self.clerk, self.new_body(imei='8904' + '0' * 28))
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()['field'], 'imei')

    def test_a_new_combination_needs_products_manage_and_is_never_published(self):
        body = self.new_body(option_ids=[self.gb256.pk, self.blue.pk, self.esim.pk, self.ja.pk], price='3900.00')
        res = self.scan_receive(self.clerk, body)
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'create_forbidden')
        res = self.scan_receive(self.manager, body, key='s-2')
        self.assertEqual(res.status_code, 201, res.content)
        product = Product.objects.get(pk=res.json()['unit']['product_id'])
        self.assertFalse(product.is_published_online)
        self.assertTrue(res.json()['created_variant'])

    def test_an_incompatible_combination_is_refused(self):
        body = self.new_body(option_ids=[self.gb256.pk, self.blue.pk, self.chip.pk, self.lla.pk], price='3900.00')
        res = self.scan_receive(self.manager, body)
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_serial_that_is_an_imei_eid_or_commercial_code_is_refused_on_its_field(self):
        value = self.fresh()
        cases = [(value, {'imei': value}), (self.fresh(), {}), ('8904' + '0' * 28, {}), (gtin('19000000004'), {}),
                 (self.fresh()[:14] + '01', {})]
        for n, (serial, extra) in enumerate(cases):
            with self.subTest(serial=serial):
                res = self.scan_receive(self.clerk, self.new_body(serial_number=serial, **extra), key=f'sn-{n}')
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual((res.json()['field'], res.json()['code']), ('serial_number', 'serial_not_a_serial'))
        # A Luhn-valid number the clerk explicitly called the serial (not this device's IMEI) is accepted.
        res = self.scan_receive(self.clerk, self.new_body(serial_number=self.fresh(), serial_confirmed=True), key='ok')
        self.assertEqual(res.status_code, 201, res.content)
        # Never this device's own IMEI, confirmed or not.
        value = self.fresh()
        res = self.scan_receive(self.clerk, self.new_body(imei=value, serial_number=value, serial_confirmed=True),
                                key='own')
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_duplicate_in_a_branch_the_clerk_does_not_reach_names_nothing(self):
        limited, _ = _p2d_member(self.company, 'scan-nuevo-norte', CLERK_CAPS, branches=[self.north])
        res = self.scan_receive(limited, self.new_body(imei=self.here[0].imei), branch=self.north)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'duplicate_imei'))
        text = res.content.decode()
        for word in (self.branch.name, self.existing.name, self.here[0].label_code):
            self.assertNotIn(word, text)
        self.assertNotIn('unit', res.json())

    def test_the_till_names_nothing_of_a_branch_the_seller_does_not_reach_either(self):
        limited, _ = _p2d_member(self.company, 'scan-caja-norte', MANAGER_CAPS, branches=[self.north])
        q = f'?company={self.company.pk}&branch={self.north.pk}'
        body = {'model': self.model.pk, 'option_ids': [self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk],
                'imei': self.here[0].imei}
        res = _api(limited).post(f'/api/admin/pos/devices/receive/{q}', body, format='json',
                                 HTTP_IDEMPOTENCY_KEY='till-leak')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'duplicate_imei'), res.content)
        for word in (self.branch.name, self.here[0].label_code):
            self.assertNotIn(word, res.content.decode())
        self.assertNotIn('unit', res.json())

    def test_a_cost_needs_costs_view(self):
        res = self.scan_receive(self.clerk, self.new_body(cost='2500.00'))
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(res.json()['code'], 'costs_forbidden')

    def test_the_same_confirmation_twice_registers_one_device(self):
        body = self.new_body()
        first = self.scan_receive(self.clerk, body, key='same')
        second = self.scan_receive(self.clerk, body, key='same')
        self.assertEqual((first.status_code, second.status_code), (201, 200), second.content)
        self.assertTrue(second.json()['replayed'])
        self.assertEqual(first.json()['unit']['id'], second.json()['unit']['id'])
        self.assertEqual(StockUnit.objects.filter(company=self.company, imei=body['imei']).count(), 1)

    def test_without_a_key_nothing_is_written(self):
        before = StockUnit.objects.count()
        self.assertEqual(self.scan_receive(self.clerk, self.new_body(), key=None).status_code, 400)
        self.assertEqual(StockUnit.objects.count(), before)


class ScannedDeviceSellsAtTheTillTest(ScanShop, TestCase):
    def setUp(self):
        self.build('scan-caja')

    def test_a_device_registered_by_scan_is_found_and_sold_by_scanning_its_imei(self):
        body = self.new_body()
        res = self.scan_receive(self.clerk, body)
        self.assertEqual(res.status_code, 201, res.content)
        lookup = _api(self.seller).get(
            f'/api/admin/pos/units/lookup/?company={self.company.pk}&branch={self.branch.pk}'
            f'&code={body["imei"]}&method=scanner')
        self.assertEqual(lookup.status_code, 200, lookup.content)
        unit_id = res.json()['unit']['id']
        self.assertEqual(lookup.json()['unit']['unit_id'], unit_id)
        order, created = _c1_sale(actor=self.seller, company=self.company, branch=self.branch, items=[
            {'product': self.existing.pk, 'quantity': 1, 'units': [{'id': unit_id, 'method': 'scanner'}]}])
        self.assertTrue(created)
        self.assertEqual(StockUnit.objects.get(pk=unit_id).status, 'sold')
        self.assertEqual(OrderUnit.objects.get(order=order).method, 'scanner')
        self.assert_invariant()


class PendingOrderTest(ScanShop, ArrivalShop, TestCase):
    """A paid order waits for this variant here: it is presented before the device may enter as free stock."""

    def setUp(self):
        self.build('scan-pedido')
        self.manager_scan, _ = _p2d_member(self.company, 'scan-pedido-jefe', [
            *CLERK_CAPS, 'sales.orders.view', 'sales.orders.manage'])

    def test_free_stock_is_409_with_the_lines_then_the_device_goes_to_its_order(self):
        line = self.pending()
        body = self.new_body()
        res = self.scan_receive(self.manager_scan, body)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'pending_order_line')
        self.assertEqual(res.json()['lines'][0]['order_item'], line.pk)
        self.assertFalse(StockUnit.objects.filter(imei=body['imei']).exists())
        res = self.scan_receive(self.manager_scan, {**body, 'order_item': line.pk}, key='s-order')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['mode'], 'order')
        line.refresh_from_db()
        self.assertEqual(line.supply_status, OrderItem.SUPPLY_RECEIVED)

    def test_free_stock_over_a_waiting_order_needs_a_reason_and_is_audited(self):
        self.pending()
        res = self.scan_receive(self.manager_scan, self.new_body(confirm_stock=True, reason='Cliente desistió'))
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(AdminAuditLog.objects.filter(
            company=self.company, action='pos_device_received_over_pending_order').exists())


# ---------------------------------------------------------------------------
# A used device
# ---------------------------------------------------------------------------

class ReceiveUsedTest(ScanShop, TestCase):
    def setUp(self):
        self.build('scan-usado')

    def test_registers_pending_inspection_unpublished_with_its_details(self):
        body = self.used_body(defects='', accessories='Caja', serial_number='UQ7K2M9XW1')
        res = self.scan_receive(self.clerk, body)
        self.assertEqual(res.status_code, 201, res.content)
        unit = StockUnit.objects.get(pk=res.json()['unit']['id'])
        self.assertEqual((unit.product, unit.condition, unit.battery_health, unit.functional_state),
                         (self.used, 'used', 87, 'full'))
        self.assertEqual((unit.inspection_status, unit.published_online), ('pending', False))
        self.assertTrue(unit.label_code)
        self.assertIn('inspección', res.json()['warnings'][0])
        movement = StockMovement.objects.filter(product=self.used).latest('pk')
        self.assertEqual((movement.reason, movement.actor, movement.company),
                         ('Inventario: registro por escaneo', self.clerk, self.company))
        self.assert_invariant()

    def test_an_uninspected_used_device_is_not_offered_at_the_till(self):
        body = self.used_body()
        self.assertEqual(self.scan_receive(self.clerk, body).status_code, 201)
        lookup = _api(self.seller).get(
            f'/api/admin/pos/units/lookup/?company={self.company.pk}&branch={self.branch.pk}'
            f'&code={body["imei"]}&method=scanner')
        self.assertEqual(lookup.status_code, 409, lookup.content)

    def test_price_and_warranty_need_products_manage(self):
        for n, extra in enumerate(({'sale_price': '2100.00'}, {'warranty_days': 30})):
            with self.subTest(extra=extra):
                res = self.scan_receive(self.clerk, self.used_body(**extra), key=f'p-{n}')
                self.assertEqual(res.status_code, 403, res.content)
                self.assertEqual(res.json()['code'], 'prices_forbidden')
        res = self.scan_receive(self.manager, self.used_body(sale_price='2100.00', warranty_days=30), key='ok')
        self.assertEqual(res.status_code, 201, res.content)
        unit = StockUnit.objects.get(pk=res.json()['unit']['id'])
        self.assertEqual((unit.sale_price, unit.warranty_days), (D('2100.00'), 30))

    def test_a_cost_needs_costs_view(self):
        res = self.scan_receive(self.clerk, self.used_body(cost='900.00'))
        self.assertEqual(res.status_code, 403, res.content)

    def test_a_new_variant_or_another_companys_is_refused(self):
        res = self.scan_receive(self.clerk, self.used_body(product_id=self.existing.pk))
        self.assertEqual((res.status_code, res.json()['code']), (400, 'not_used_line'))
        other = _p3_company('scan-usado-otra', 'Otra')
        foreign = _prod(other, 'Ajeno', 'scan-usado-ajeno', price='1.00', inventory=0)
        res = self.scan_receive(self.clerk, self.used_body(product_id=foreign.pk), key='f')
        self.assertEqual(res.status_code, 404, res.content)

    def test_a_duplicate_imei_names_the_device(self):
        res = self.scan_receive(self.clerk, self.used_body(imei=self.here[0].imei))
        self.assertEqual((res.status_code, res.json()['code']), (409, 'duplicate_imei'))

    def test_the_same_confirmation_twice_registers_one_device(self):
        body = self.used_body()
        first = self.scan_receive(self.clerk, body, key='u-same')
        second = self.scan_receive(self.clerk, body, key='u-same')
        self.assertEqual((first.status_code, second.status_code), (201, 200), second.content)
        self.assertEqual(StockUnit.objects.filter(company=self.company, imei=body['imei']).count(), 1)
        other = self.scan_receive(self.clerk, self.used_body(), key='u-same')
        self.assertEqual((other.status_code, other.json()['code']), (409, 'idempotency_conflict'))

    def test_the_configurations_list_the_used_variants(self):
        res = _api(self.clerk).get(f'/api/admin/inventory/scan/models/{self.model.pk}/configurations/{self.sq}')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([v['id'] for v in res.json()['used_variants']], [self.used.pk])
        self.assertEqual(res.json()['branch']['id'], self.branch.pk)
        search = _api(self.clerk).get(f'/api/admin/inventory/scan/models/{self.sq}&q=Tel')
        self.assertEqual([m['id'] for m in search.json()['results']], [self.model.pk])


class UsedPendingOrderTest(ScanShop, ArrivalShop, TestCase):
    """Review B10 — a paid order waiting for the USED variant here is presented before free stock, as for a new one."""

    def setUp(self):
        self.build('scan-usado-pedido')

    def test_free_stock_of_a_used_device_over_a_waiting_order_needs_a_reason(self):
        line = self.pending(product=self.used)
        body = self.used_body()
        res = self.scan_receive(self.clerk, body)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'pending_order_line'), res.content)
        self.assertEqual(res.json()['lines'][0]['order_item'], line.pk)
        self.assertFalse(StockUnit.objects.filter(imei=body['imei']).exists())
        res = self.scan_receive(self.clerk, {**body, 'confirm_stock': True, 'reason': 'Cliente desistió'}, key='s-2')
        self.assertEqual(res.status_code, 201, res.content)
        log = AdminAuditLog.objects.get(company=self.company, action='pos_device_received_over_pending_order')
        self.assertEqual(log.metadata['order_item_ids'], [line.pk])

    def test_a_used_device_is_not_delivered_to_an_order_from_here(self):
        line = self.pending(product=self.used)
        res = self.scan_receive(self.clerk, {**self.used_body(), 'order_item': line.pk})
        self.assertEqual((res.status_code, res.json()['code']), (400, 'used_order_line'), res.content)


class PermissionsTest(ScanShop, TestCase):
    def setUp(self):
        self.build('scan-permisos')

    def test_without_inventory_adjust_nothing_is_registered(self):
        viewer, _ = _p2d_member(self.company, 'scan-permisos-ver', VIEWER_CAPS)
        self.assertEqual(self.scan_receive(viewer, self.new_body()).status_code, 403)
        res = _api(viewer).get(f'/api/admin/inventory/scan/models/{self.model.pk}/configurations/{self.sq}')
        self.assertEqual(res.status_code, 403)

    def test_a_branch_outside_the_callers_grants_is_404(self):
        limited, _ = _p2d_member(self.company, 'scan-permisos-norte', CLERK_CAPS, branches=[self.north])
        self.assertEqual(self.scan_receive(limited, self.new_body()).status_code, 404)

    def test_another_companys_model_is_404(self):
        other = _p3_company('scan-permisos-otra', 'Otra')
        model = device_catalog.create_model(company=other, name='Modelo ajeno')
        res = _api(self.clerk).get(f'/api/admin/inventory/scan/models/{model.pk}/configurations/{self.sq}')
        self.assertEqual(res.status_code, 404)
        res = self.scan_receive(self.clerk, self.new_body(model=model.pk))
        self.assertEqual(res.status_code, 404, res.content)

    def test_the_till_receiver_works_here_too(self):
        receiver, _ = _p2d_member(self.company, 'scan-permisos-caja', RECEIVER_CAPS)
        self.assertEqual(self.scan_receive(receiver, self.new_body()).status_code, 201)


# ---------------------------------------------------------------------------
# Two clerks, one IMEI
# ---------------------------------------------------------------------------

class ImeiSerialCrossTest(ScanShop, TestCase):
    """
    Review R1 — an IMEI and a serial never name two devices. A serial equal to an IMEI (1 or 2) of a live device of
    the company is refused, and an IMEI equal to a live device's serial too, on every door (`receive_units`, the till,
    the scan door). Sold and written-off devices still occupy; a voided one frees.
    """

    def setUp(self):
        self.build('scan-cruce')
        self.plain = _prod(self.company, 'Laptop sin IMEI', 'scan-cruce-laptop', price='10.00', inventory=0)
        Product.objects.filter(pk=self.plain.pk).update(is_serialized=True, requires_imei=False)
        self.plain.refresh_from_db()

    def test_receive_units_refuses_a_serial_that_is_another_devices_imei_or_imei2(self):
        second = self.fresh()
        holder = units.receive_units(branch=self.branch, product=self.existing, actor=self.manager, reason='Compra',
                                     units=[{'imei': self.fresh(), 'imei2': second}])[0]
        for value in (holder.imei, second):
            with self.subTest(value=value), self.assertRaises(units.StockUnitError) as caught:
                units.receive_units(branch=self.branch, product=self.plain, actor=self.manager, reason='Compra',
                                    units=[{'serial_number': value}])
            self.assertEqual(caught.exception.field, 'serial_number')

    def test_sold_and_written_off_still_occupy_voided_frees(self):
        sold, gone, void = self.here[0], self.here[1], units.receive_units(
            branch=self.branch, product=self.existing, actor=self.manager, reason='Compra',
            units=[{'imei': self.fresh()}])[0]
        StockUnit.objects.filter(pk=sold.pk).update(status=StockUnit.Status.SOLD)
        units.write_off(gone, reason='Robado', actor=self.manager)
        for unit in (sold, gone):
            with self.subTest(status=unit.pk), self.assertRaises(units.StockUnitError):
                units.receive_units(branch=self.branch, product=self.plain, actor=self.manager, reason='Compra',
                                    units=[{'serial_number': unit.imei}])
        StockUnit.objects.filter(pk=void.pk).update(status=StockUnit.Status.VOIDED, voided_at='2026-11-01T00:00Z')
        units.receive_units(branch=self.branch, product=self.plain, actor=self.manager, reason='Compra',
                            units=[{'serial_number': void.imei}])

    def test_receive_units_refuses_an_imei_that_is_another_devices_serial(self):
        value = self.fresh()
        units.receive_units(branch=self.branch, product=self.plain, actor=self.manager, reason='Compra',
                            units=[{'serial_number': value}])
        with self.assertRaises(units.StockUnitError) as caught:
            units.receive_units(branch=self.branch, product=self.existing, actor=self.manager, reason='Compra',
                                units=[{'imei': value}])
        self.assertEqual(caught.exception.field, 'imei')

    def test_scan_door_new_device_without_imei_cannot_take_an_imei_as_its_serial(self):
        body = {'condition': 'new', 'model': self.model.pk, 'method': 'manual', 'price': '3900.00',
                'option_ids': [self.gb256.pk, self.blue.pk, self.esim.pk, self.ja.pk],
                'serial_number': self.here[0].imei, 'serial_confirmed': True}
        res = self.scan_receive(self.manager, body)
        self.assertEqual((res.status_code, res.json()['code'], res.json()['field']),
                         (409, 'duplicate_serial', 'serial_number'), res.content)
        self.assertFalse(StockUnit.objects.filter(serial_number=self.here[0].imei).exists())

    def test_scan_door_used_device_without_imei_cannot_take_an_imei_as_its_serial(self):
        Product.objects.filter(pk=self.used.pk).update(requires_imei=False)
        body = {'condition': 'used', 'product_id': self.used.pk, 'method': 'manual',
                'serial_number': self.here[0].imei, 'serial_confirmed': True}
        res = self.scan_receive(self.clerk, body)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'duplicate_serial'), res.content)

    def test_the_till_refuses_a_serial_that_is_another_devices_imei(self):
        body = self.body([self.gb128, self.black, self.esim, self.lla], price=None)
        body['serial_number'] = self.here[0].imei
        res = self.post(self.manager, body, key='till-cross')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'duplicate_serial'), res.content)


class ImeiSerialCrossInOneBatchTest(ScanShop, TestCase):
    """Review H1 — inside ONE batch too: a row's serial is never another row's IMEI, nor its IMEI another's serial."""

    def setUp(self):
        self.build('scan-lote')
        self.plain = _prod(self.company, 'Laptop del lote', 'scan-lote-laptop', price='10.00', inventory=0)
        Product.objects.filter(pk=self.plain.pk).update(is_serialized=True, requires_imei=False)
        self.plain.refresh_from_db()

    def test_a_serial_then_the_same_value_as_an_imei_is_refused_on_the_second_row(self):
        value = self.fresh()
        with self.assertRaises(units.StockUnitError) as caught:
            units.receive_units(branch=self.branch, product=self.plain, actor=self.manager, reason='Compra',
                                units=[{'serial_number': value}, {'serial_number': 'LOTE0002X', 'imei': value}])
        self.assertEqual((caught.exception.line, caught.exception.field), (2, 'imei'))
        self.assertFalse(StockUnit.objects.filter(product=self.plain).exists())

    def test_an_imei_then_the_same_value_as_a_serial_is_refused_on_the_second_row(self):
        value = self.fresh()
        with self.assertRaises(units.StockUnitError) as caught:
            units.receive_units(branch=self.branch, product=self.plain, actor=self.manager, reason='Compra',
                                units=[{'serial_number': 'LOTE0001X', 'imei': value}, {'serial_number': value}])
        self.assertEqual((caught.exception.line, caught.exception.field), (2, 'serial_number'))

    def test_a_fifty_row_batch_checks_the_identifiers_in_a_constant_number_of_queries(self):
        # Review H2: the occupancy of the batch is read once, not three queries per row.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def lookups(count):
            rows = [{'imei': self.fresh() if n < 30 else imei(f'36{count:02d}{n:010d}')} for n in range(count)]
            with CaptureQueriesContext(connection) as ctx:
                units.receive_units(branch=self.branch, product=self.existing, actor=self.manager, reason='Compra',
                                    units=rows)
            # The occupancy lookups: a SELECT of devices filtered BY an IMEI (the internal label's own uniqueness check
            # per new device filters by label code and serial, and is not one of them).
            return sum(1 for q in ctx.captured_queries
                       if q['sql'].lstrip().upper().startswith('SELECT') and 'WHERE' in q['sql']
                       and '"store_stockunit"."imei"' in q['sql'].split('WHERE', 1)[1])

        self.assertLessEqual(lookups(50), lookups(5))


class MaskingScopeTest(ScanShop, TestCase):
    """
    Review R3 — the 409 and the check are masked only for actors restricted to some branches. A company-wide actor
    keeps the device's details, also for a device in a deactivated branch.
    """

    def setUp(self):
        self.build('scan-mascara')
        self.north_unit = units.receive_units(branch=self.north, product=self.existing, actor=self.manager,
                                              reason='Compra', units=[{'imei': self.fresh()}])[0]
        Branch.objects.filter(pk=self.north.pk).update(is_active=False)
        self.limited, _ = _p2d_member(self.company, 'scan-mascara-sel', MANAGER_CAPS, branches=[self.branch])

    def test_a_company_wide_actor_is_told_which_device_even_in_a_deactivated_branch(self):
        res = self.scan_receive(self.manager, self.new_body(imei=self.north_unit.imei))
        self.assertEqual((res.status_code, res.json()['code']), (409, 'duplicate_imei'), res.content)
        self.assertEqual(res.json()['unit']['id'], self.north_unit.pk)
        self.assertIn(self.north.name, res.json()['detail'])
        brief = self.check(self.manager, self.north_unit.imei).json()['unit']
        self.assertEqual((brief['standing'], brief['id']), ('registered', self.north_unit.pk))

    def test_a_restricted_actor_is_told_only_that_it_exists(self):
        res = self.scan_receive(self.limited, self.new_body(imei=self.north_unit.imei))
        self.assertEqual((res.status_code, res.json()['code']), (409, 'duplicate_imei'), res.content)
        self.assertNotIn('unit', res.json())
        self.assertNotIn(self.north.name, res.content.decode())
        self.assertEqual(self.check(self.limited, self.north_unit.imei).json()['unit']['standing'], 'elsewhere')

    def test_a_label_code_out_of_reach_is_not_called_another_devices_internal_code(self):
        # Review R2.
        body = self.check(self.limited, self.north_unit.label_code, choice='serial').json()
        self.assertEqual(body['conflict']['code'], 'label_code')
        self.assertNotIn('código interno', body['conflict']['detail'])
        self.assertIn('ya existe en la empresa', body['conflict']['detail'])
        reach = self.check(self.manager, self.north_unit.label_code, choice='serial').json()
        self.assertIn('código interno', reach['conflict']['detail'])


class SameImeiRaceTest(ScanShop, TransactionTestCase):
    def setUp(self):
        self.build('scan-carrera')

    def race(self, make_body):
        if connection.vendor != 'postgresql':
            # Review B12: CI runs PostgreSQL (backend-postgres-validation.yml); there a skip would hide the race.
            if os.environ.get('CI'):
                self.fail('CI must run this race on PostgreSQL.')
            self.skipTest('needs PostgreSQL row and advisory locks')
        value = self.fresh()
        barrier = Barrier(2)
        results = []

        def run(key):
            try:
                barrier.wait()
                res = self.scan_receive(self.manager, make_body(value), key=key)
                results.append((res.status_code, res.json().get('code')))
            finally:
                connection.close()

        threads = [Thread(target=run, args=(f'race-{n}',)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(code for code, _ in results), [201, 409], results)
        self.assertEqual(StockUnit.objects.filter(company=self.company, imei=value).count(), 1)
        self.assert_invariant()

    def test_two_clerks_scan_the_same_new_imei_one_wins(self):
        self.race(lambda value: self.new_body(imei=value))

    def test_two_clerks_scan_the_same_used_imei_one_wins(self):
        self.race(lambda value: self.used_body(imei=value))
