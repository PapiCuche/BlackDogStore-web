"""
B1-0b-1 — the inventory cost doors never read an ambiguous amount in silence.

The doors a PERSON types a cost into, outside the supplier offers of B1-0:
  * the cost of each device (`stock_unit_services._money`): «Registrar equipos», the guided registration, the device
    rows of an on-order reception, the device upload and the cost correction of a device;
  * the cost of the initial stock of the guided registration (it reached `cost_services.clean_cost`, which reads
    «5.500» as 5.5000);
  * the reference cost of a product (`admin_views._clean_reference_cost`, which replaced every comma with a dot).

The policy is B1-0's (`money_input.amount_text`): «5,500»/«5.500» are ambiguous and refused, never guessed; four
or more digits after one separator are not decimals; two amounts in one text are refused; a currency, a letter, a
sign or an exponent are not an amount. What is TYPED (a Decimal or an int the system computed, such as an internal
cost of four decimals) is read as before; a spreadsheet NUMBER cell is a number, not a text. What is stored is
never re-read.
"""
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase

from store import money_input
from store import stock_unit_services as units
from store.models import AdminAuditLog, BranchStock, Product, StockMovement, StockUnit
from store import test_cost_model, test_registration, test_supply_reception, test_unit_import
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C
from store.test_supply_reception import as_client, imei

OWN_IMEIS = [imei(f'35693803599{n:03d}') for n in range(5)]


class AmountTextPolicyTest(SimpleTestCase):
    """The shared reading of an amount typed by a person."""

    def read(self, text):
        return money_input.amount_text(text, 'el costo')

    def test_the_owners_cases(self):
        for text, expected in (('5500', '5500'), ('5500,00', '5500.00'), ('5500.00', '5500.00'),
                               ('1.234.567', '1234567'), ('5.500,00', '5500.00'), ('5,50', '5.50')):
            with self.subTest(text):
                self.assertEqual(self.read(text), expected)
        for text in ('5.500', '5,500'):
            with self.subTest(text), self.assertRaises(money_input.AmountTextError) as caught:
                self.read(text)
            self.assertEqual(caught.exception.code, 'ambiguous_amount')
        for text in ('5.500,1234', '1800,2000', '5.5000'):
            with self.subTest(text), self.assertRaises(money_input.AmountTextError) as caught:
                self.read(text)
            self.assertEqual(caught.exception.code, 'invalid')
        for text in ('1800/2000', '5400 - 5500', '4390 4500'):
            with self.subTest(text), self.assertRaises(money_input.AmountTextError) as caught:
                self.read(text)
            self.assertEqual(caught.exception.code, 'several_amounts')
        for text in ('S/ 5500', '$1500', '1e3', '-5', 'NaN', '1 234,56'):
            with self.subTest(text), self.assertRaises(money_input.AmountTextError):
                self.read(text)

    def test_the_supplier_offers_keep_their_words(self):
        # B1-0 now reads through the shared policy: its messages and codes do not change.
        from store.supply_services import SupplyError, clean_money

        with self.assertRaises(SupplyError) as caught:
            clean_money('5,500', 'commercial_price', 'el precio comercial')
        self.assertEqual((caught.exception.code, caught.exception.field), ('ambiguous_amount', 'commercial_price'))
        self.assertIn('Escribe el precio comercial sin separador de miles (5500)', caught.exception.detail)


class DeviceCostTest(test_cost_model._Shop):
    """`stock_unit_services._money`: the cost of each device."""

    def setUp(self):
        super().setUp()
        self.phone = _prod_serialized(self.company, 'iPhone 16 B10b1', 'iphone-16-b10b1')

    def receive(self, cost, serial='SER-B1-01', imei=IMEI_A):
        return units.receive_units(branch=self.centro, product=self.phone, reason='Compra',
                                   units=[{'serial_number': serial, 'imei': imei, 'cost': cost}])

    def test_an_ambiguous_or_double_cost_is_refused_and_nothing_enters(self):
        for cost, code in (('5.500', 'ambiguous_amount'), ('5,500', 'ambiguous_amount'),
                           ('1800,2000', 'invalid'), ('5.500,1234', 'invalid'), ('1800/2000', 'several_amounts'),
                           ('S/ 1500', 'invalid')):
            with self.subTest(cost), self.assertRaises(units.StockUnitError) as caught:
                self.receive(cost)
            self.assertEqual((caught.exception.code, caught.exception.field), (code, 'cost'))
        self.assertFalse(StockUnit.objects.filter(product=self.phone).exists())
        self.assertFalse(StockMovement.objects.filter(product=self.phone).exists())

    def test_unambiguous_texts_keep_their_meaning(self):
        for n, (cost, expected) in enumerate((('5500', '5500.00'), ('5500,00', '5500.00'), ('5500.00', '5500.00'),
                                              ('5.500,00', '5500.00'), ('1.234.567', '1234567.00'))):
            with self.subTest(cost):
                unit, = self.receive(cost, serial=f'SER-B1-1{n}', imei=None)
                self.assertEqual(unit.cost, D(expected))

    def test_typed_values_are_read_as_before_and_floats_never_round_in_silence(self):
        unit, = self.receive(D('1234.5678'))                       # an internal cost of four decimals
        self.assertEqual(unit.cost, D('1234.57'))
        second, = self.receive(3800, serial='SER-B1-02', imei=IMEI_B)
        self.assertEqual(second.cost, D('3800.00'))
        with self.assertRaises(units.StockUnitError):
            self.receive(12.345, serial='SER-B1-03', imei=IMEI_C)
        third, = self.receive(12.5, serial='SER-B1-03', imei=IMEI_C)
        self.assertEqual(third.cost, D('12.50'))

    def test_the_cost_correction_refuses_an_ambiguous_text_and_keeps_the_stored_cost(self):
        unit, = self.receive('3800.00')
        with self.assertRaises(units.StockUnitError) as caught:
            units.update_unit(unit, cost='3.800', cost_reason='Factura')
        self.assertEqual(caught.exception.code, 'ambiguous_amount')
        unit.refresh_from_db()
        self.assertEqual(unit.cost, D('3800.00'))
        self.assertFalse(AdminAuditLog.objects.filter(action__icontains='cost', target_id=str(unit.pk)).exists())

    def test_a_stored_cost_is_never_re_read(self):
        # A device saved before B1-0b-1 with a misread cost keeps it: only new input is parsed.
        unit, = self.receive('5.50')
        StockUnit.objects.filter(pk=unit.pk).update(cost=D('5.50'))
        units.update_unit(unit, notes='Caja abierta')
        unit.refresh_from_db()
        self.assertEqual(unit.cost, D('5.50'))


def _prod_serialized(company, name, slug):
    from store.tests import _prod

    product = _prod(company, name, slug, price='4500.00', inventory=0)
    Product.objects.filter(pk=product.pk).update(is_serialized=True, requires_imei=False)
    product.refresh_from_db()
    return product


class OnOrderReceptionTest(test_supply_reception.ReceptionShop, TestCase):
    """The device rows of an on-order reception, and the line's internal cost of four decimals."""

    def setUp(self):
        self.build('b10b1-bajo-pedido')

    def test_a_device_cost_typed_ambiguously_refuses_the_whole_reception(self):
        order = self.paid_order(self.phone, 1)
        res = as_client(self.staff).post(self.url(order), {'units': [
            {'serial_number': 'EQPB1001', 'imei': OWN_IMEIS[0], 'cost': '2.450'}]}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('ambiguo', str(res.json()))
        self.assertFalse(StockUnit.objects.filter(product=self.phone).exists())
        self.assertEqual(order.items.get().supply_status, 'pending')

    def test_devices_without_their_own_cost_take_the_lines_four_decimal_cost(self):
        from store.models import SupplierOffer

        SupplierOffer.objects.filter(pk=self.phone_offer.pk).update(agreed_cost=D('2499.99'))
        order = self.paid_order(self.phone, 1)
        item = order.items.get()
        type(item).objects.filter(pk=item.pk).update(unit_cost=D('2499.9876'))
        res = as_client(self.staff).post(self.url(order), {'units': [
            {'serial_number': 'EQPB1002', 'imei': OWN_IMEIS[1]}]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(StockUnit.objects.get(product=self.phone).cost, D('2499.99'))

    def test_without_costs_view_a_device_cost_is_still_refused(self):
        order = self.paid_order(self.phone, 1)
        res = as_client(self.no_costs).post(self.url(order), {'units': [
            {'serial_number': 'EQPB1003', 'imei': OWN_IMEIS[2], 'cost': '2450'}]}, format='json')
        self.assertEqual((res.status_code, res.json().get('code')), (403, 'costs_forbidden'), res.content)
        self.assertFalse(StockUnit.objects.filter(product=self.phone).exists())


class GuidedRegistrationTest(test_registration.RegistrationFixture):
    """The guided registration: initial stock cost, device cost and reference cost."""

    def test_an_ambiguous_initial_stock_cost_is_refused_naming_the_row(self):
        before = self.counts()
        body = self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 5, 'unit_cost': '5.500'}])
        res = self.post(self.admin, body, expect=400)
        self.assertEqual(res.json().get('field'), 'stock.unit_cost')
        self.assertIn('ambiguo', res.json()['detail'])
        self.assertEqual(self.counts(), before)

    def test_an_unambiguous_initial_stock_cost_keeps_its_meaning(self):
        body = self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 5, 'unit_cost': '5500,00'}])
        data = self.post(self.admin, body, expect=201).json()
        product = Product.objects.get(pk=data['product']['id'])
        self.assertEqual(BranchStock.objects.get(product=product, branch=self.centro).average_cost, D('5500.0000'))

    def test_a_typed_initial_stock_cost_is_read_as_before(self):
        body = self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 2, 'unit_cost': 17.31}])
        data = self.post(self.admin, body, expect=201).json()
        movement = StockMovement.objects.get(product_id=data['product']['id'], branch=self.centro)
        self.assertEqual(movement.unit_cost, D('17.31'))

    def test_an_ambiguous_device_cost_is_refused(self):
        before = self.counts()
        body = self.used_device()
        body['unit']['cost'] = '1,800'
        res = self.post(self.admin, body, expect=400)
        self.assertIn('ambiguo', str(res.json()))
        self.assertEqual(self.counts(), before)

    def test_an_ambiguous_reference_cost_is_refused(self):
        before = self.counts()
        body = self.accessory()
        body['product']['reference_cost'] = '21,500'
        res = self.post(self.admin, body, expect=400)
        self.assertEqual(res.json().get('field'), 'product.reference_cost')
        self.assertEqual(self.counts(), before)

    def test_without_costs_view_a_stated_cost_is_still_forbidden(self):
        body = self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 1, 'unit_cost': '5.500'}])
        res = self.post(self.clerk, body)
        self.assertEqual(res.status_code, 403, res.content)


class ReferenceCostTest(test_registration.RegistrationFixture):
    """`PATCH /admin/products/<id>/ reference_cost`: the same reading, `costs.view`, and the company's own products."""

    def setUp(self):
        super().setUp()
        from store.tests import _prod

        self.product = _prod(self.company, 'Cargador B10b1', 'cargador-b10b1', price='99.00', inventory=0)
        self.foreign = _prod(self.other, 'Cargador ajeno', 'cargador-ajeno-b10b1', price='99.00', inventory=0)

    def patch(self, user, value, product=None):
        from store.test_registration import _client

        return _client(user).patch(f'/api/admin/products/{(product or self.product).pk}/',
                                   {'reference_cost': value}, format='json')

    def test_ambiguous_and_double_texts_are_refused_and_nothing_changes(self):
        for value in ('5.500', '5,500', '1800,2000', '1800/2000', '5.500,1234'):
            with self.subTest(value):
                res = self.patch(self.admin, value)
                self.assertEqual(res.status_code, 400, res.content)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.reference_cost)

    def test_unambiguous_texts_and_numbers_keep_their_meaning(self):
        for value, expected in (('5500,00', '5500.00'), ('5500', '5500.00'), ('5.500,00', '5500.00'),
                                (5500.5, '5500.50'), ('', None)):
            with self.subTest(value):
                res = self.patch(self.admin, value)
                self.assertEqual(res.status_code, 200, res.content)
                self.product.refresh_from_db()
                self.assertEqual(self.product.reference_cost, None if expected is None else D(expected))

    def test_costs_view_and_the_company_still_decide(self):
        self.assertEqual(self.patch(self.catalog_only, '5500').status_code, 403)
        self.assertEqual(self.patch(self.outsider, '5500', product=self.product).status_code, 404)
        self.assertEqual(self.patch(self.admin, '5500', product=self.foreign).status_code, 404)


class DeviceUploadTest(test_unit_import.ImportBase):
    """The device upload: a NUMBER cell is a number; a TEXT cell follows the policy of a typed text."""

    def test_number_and_text_cells(self):
        client = self.cost_client()
        payload = self.preview([
            self.phone_row('F2LXB10B101', IMEI_A, **{'Costo': 12.345}),      # row 2: a real third decimal
            self.phone_row('F2LXB10B102', IMEI_B, **{'Costo': 1179.9999999999998}),   # row 3: float noise of 1180
            self.phone_row('F2LXB10B103', IMEI_C, **{'Costo': '5.500'}),     # row 4: text, ambiguous
        ], client=client).json()
        errors = self.errors(payload)
        self.assertIn('dos decimales', errors.get(2, ''))
        self.assertNotIn('ambiguo', errors.get(2, ''))
        self.assertNotIn(3, errors)
        self.assertIn('ambiguo', errors.get(4, ''))
