"""
LABEL-02 — the identity of a device and the rules of the IMEI (docs/adr-inventory-labels.md D4, D6).

  * A device's IMEI, second IMEI or serial can never be a product code of the
    company: the till looks up products first, so that product would steal the
    device's scan. LABEL-01 refuses it from the product side; this refuses it
    from the device side, at the one door devices come in through
    (`receive_units`) and in the bulk upload's preview (`clean_unit`).
"""
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient

from store import product_codes
from store import stock_unit_services as units_service
from store.models import Product, ProductBarcode, StockMovement, StockUnit
from store.test_stock_units import imei
from store.tests import _p2d_member, _p3_company

UNITS = '/api/admin/inventory/units/'
REGISTER = '/api/admin/products/register/'

IMEI_A = imei('35693803565001')
IMEI_B = imei('35693803565002')
IMEI_C = imei('35693803565003')


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class Fixture(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('ucodes-tienda', 'Tienda Unidades')
        self.other = _p3_company('ucodes-otra', 'Otra Unidades')
        self.branch = self.company.default_inventory_branch
        self.clerk, _ = _p2d_member(self.company, 'ucodes_clerk', [
            'products.view', 'products.manage', 'inventory.view', 'inventory.adjust', 'sales.pos.use'])
        self._n = 0

    def product(self, name, company=None, *, serialized=False, requires_imei=True, **extra):
        self._n += 1
        product = Product.objects.create(company=company or self.company, name=name,
                                         slug=f'ucodes-{self._n}', price='10.00', inventory=0, **extra)
        if serialized:
            product = units_service.set_serialized(product, serialized=True, requires_imei=requires_imei)
        return product

    def coded(self, code, company=None, **extra):
        product = self.product(f'Accesorio {code}', company=company, **extra)
        with transaction.atomic():
            product_codes.ensure_code(product, supplied=[code])
        return product

    def receive(self, product, *rows, branch=None):
        return units_service.receive_units(branch=branch or self.branch, product=product, units=list(rows),
                                           reason='prueba')


# ---------------------------------------------------------------------------
# The reverse check: a device identifier is never a product code
# ---------------------------------------------------------------------------

class ReverseCheckTest(Fixture):
    def test_an_imei_that_is_already_a_product_code_is_refused_and_nothing_enters(self):
        self.coded(IMEI_A)
        phone = self.product('Teléfono', serialized=True)
        with self.assertRaises(units_service.StockUnitError) as refused:
            self.receive(phone, {'serial_number': 'SNOK0001', 'imei': IMEI_B},
                         {'serial_number': 'SNOK0002', 'imei': IMEI_A})
        error = refused.exception
        self.assertEqual((error.status, error.code, error.field, error.line), (409, 'product_code_conflict', 'imei', 2))
        self.assertIn('Accesorio', str(error))
        self.assertEqual(StockUnit.objects.filter(company=self.company).count(), 0)
        self.assertEqual(StockMovement.objects.filter(product=phone).count(), 0)

    def test_the_second_imei_and_the_serial_whatever_its_capitals_are_refused_too(self):
        self.coded(IMEI_B)
        self.coded('sncode55')
        phone = self.product('Teléfono', serialized=True)
        cases = (({'serial_number': 'SNOK0003', 'imei': IMEI_C, 'imei2': IMEI_B}, 'imei2'),
                 ({'serial_number': 'SNCODE55', 'imei': IMEI_C}, 'serial_number'))
        for row, field in cases:
            with self.subTest(field=field), self.assertRaises(units_service.StockUnitError) as refused:
                self.receive(phone, row)
            self.assertEqual((refused.exception.code, refused.exception.field), ('product_code_conflict', field))

    def test_an_inactive_code_or_one_of_a_deactivated_product_still_blocks(self):
        old = self.coded(IMEI_A, is_active=False)
        ProductBarcode.objects.filter(product=old).update(is_active=False)
        phone = self.product('Teléfono', serialized=True)
        with self.assertRaises(units_service.StockUnitError):
            self.receive(phone, {'serial_number': 'SNOK0004', 'imei': IMEI_A})

    def test_another_companys_code_does_not_block(self):
        self.coded(IMEI_A, company=self.other)
        phone = self.product('Teléfono', serialized=True)
        self.assertEqual(len(self.receive(phone, {'serial_number': 'SNOK0005', 'imei': IMEI_A})), 1)

    def test_the_bulk_upload_preview_sees_it_before_anything_is_applied(self):
        self.coded(IMEI_A)
        phone = self.product('Teléfono', serialized=True)
        with self.assertRaises(units_service.StockUnitError) as refused:
            units_service.clean_unit(phone, {'serial_number': 'SNOK0006', 'imei': IMEI_A})
        self.assertEqual(refused.exception.code, 'product_code_conflict')

    def test_the_reception_screen_answers_409_naming_the_line_and_the_field(self):
        self.coded(IMEI_A)
        phone = self.product('Teléfono', serialized=True)
        res = client_for(self.clerk).post(UNITS, {
            'branch': self.branch.pk, 'product_id': phone.pk, 'reason': 'Compra',
            'units': [{'serial_number': 'SNOK0007', 'imei': IMEI_A}]}, format='json')
        self.assertEqual(res.status_code, 409, res.content)
        body = res.json()
        self.assertEqual((body['code'], body['field'], body['line']), ('product_code_conflict', 'imei', 1))

    def test_the_guided_registration_answers_409_too(self):
        self.coded(IMEI_A)
        res = client_for(self.clerk).post(REGISTER, {
            'kind': 'new_device', 'branch': self.branch.pk, 'requires_imei': True,
            'model': {'name': 'Modelo Unidades'},
            'product': {'name': 'Equipo Unidades', 'price': '999.00', 'sku': 'MOD-UNI-01', 'is_active': True,
                        'show_online': False},
            'units': [{'serial_number': 'SNOK0008', 'imei': IMEI_A}],
        }, format='json', HTTP_IDEMPOTENCY_KEY='ucodes-reg-1')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual((res.json()['code'], res.json()['field']), ('product_code_conflict', 'imei'))
        self.assertFalse(Product.objects.filter(company=self.company, name='Equipo Unidades').exists())

    def test_a_device_without_conflict_still_enters_as_before(self):
        self.coded('FUNDA-OK-1')
        phone = self.product('Teléfono', serialized=True)
        self.assertEqual(len(self.receive(phone, {'serial_number': 'SNOK0009', 'imei': IMEI_A, 'imei2': IMEI_B})), 1)
