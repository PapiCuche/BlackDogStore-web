"""
LABEL-01 — one product-code policy, and a generated code only where none exists.

Owner decisions of 2026-10-23 (docs/adr-inventory-labels.md §1.1, D1–D3):

  * a code the user writes, or the file brings, is KEPT exactly: leading zeros,
    capitals and length. A manufacturer's EAN stays an EAN, never reclassified
    by its length;
  * a product that already has codes keeps all of them; nothing is overwritten,
    and nothing is regenerated on edit or reprint;
  * the code of a deactivated product is never reused;
  * a code that belongs to ANOTHER product of the company is refused with a
    clear 409 — a different code is never generated to get around it;
  * a code equal to a device's IMEI or serial is refused: the till looks up
    products first, so it would steal the device's scan;
  * a code is generated only when none was given and none is associated, only in
    the backend, from `InternalSequence` per company, concurrency-safe.
"""
import io
import threading
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import product_codes
from store import stock_unit_services as units_service
from store.models import (
    AdminAuditLog, Category, CompanyRole, InternalSequence, Membership, Product, ProductBarcode, StockUnit,
)
from store.test_stock_units import imei
from store.tests import _assign, _p2d_member, _p3_company

PRODUCTS = '/api/admin/products/'
REGISTER = '/api/admin/products/register/'
POS_LOOKUP = '/api/admin/pos/products/lookup/'
POS_UNIT_LOOKUP = '/api/admin/pos/units/lookup/'

IMEI_A = imei('35693803564901')
IMEI_B = imei('35693803564902')


def barcodes_url(pk):
    return f'/api/admin/products/{pk}/barcodes/'


def generate_url(pk):
    return f'/api/admin/products/{pk}/barcodes/generate/'


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def series(company):
    return InternalSequence.objects.filter(
        company=company, branch__isnull=True, document_type=InternalSequence.DOCUMENT_PRODUCT_CODE,
    ).first()


def next_value(company):
    row = series(company)
    return row.next_value if row else 1


def codes_of(product):
    return list(ProductBarcode.objects.filter(product=product).order_by('pk').values_list('code', flat=True))


class Fixture(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('codes-tienda', 'Tienda Códigos')
        self.other = _p3_company('codes-otra', 'Otra Códigos')
        self.branch = self.company.default_inventory_branch
        self.cases = Category.objects.create(company=self.company, name='Fundas', slug='fundas-codes')
        self.manager, _ = _p2d_member(self.company, 'codes_manager', [
            'products.view', 'products.manage', 'inventory.view', 'inventory.adjust', 'sales.pos.use'])
        self.viewer, _ = _p2d_member(self.company, 'codes_viewer', ['products.view', 'sales.pos.use'])
        self.outsider, _ = _p2d_member(self.other, 'codes_outsider', [
            'products.view', 'products.manage', 'sales.pos.use'])

    def product(self, name, company=None, **extra):
        company = company or self.company
        slug = name.lower().replace(' ', '-')[:50]
        return Product.objects.create(company=company, name=name, slug=slug, price='10.00', inventory=0, **extra)

    def ensure(self, product, *supplied, **kwargs):
        with transaction.atomic():
            return product_codes.ensure_code(product, supplied=supplied, **kwargs)

    def device_with_imei(self, value, *, serial=None):
        phone = self.product(f'Equipo {value[-4:]}')
        phone = units_service.set_serialized(phone, serialized=True, requires_imei=True)
        row = {'imei': value, 'serial_number': serial or f'SN{value[-6:]}'}
        units_service.receive_units(branch=self.branch, product=phone, units=[row], reason='prueba')
        return phone


# ---------------------------------------------------------------------------
# The policy itself
# ---------------------------------------------------------------------------

class PolicyTest(Fixture):
    def test_a_product_without_a_code_receives_one_generated_internal_and_primary(self):
        product = self.product('Funda azul')
        result = self.ensure(product)
        self.assertIsNotNone(result.generated)
        row = ProductBarcode.objects.get(product=product)
        self.assertEqual(row.code, 'P000001')
        self.assertEqual(row.symbology, ProductBarcode.INTERNAL)
        self.assertTrue(row.is_primary)
        self.assertTrue(row.is_active)
        self.assertEqual(next_value(self.company), 2)

    def test_the_generated_code_is_short_upper_case_and_never_all_digits(self):
        product = self.product('Funda roja')
        code = self.ensure(product).generated.code
        self.assertRegex(code, r'^[A-Z0-9]+$')
        self.assertRegex(code, r'[A-Z]')
        self.assertLessEqual(len(code), product_codes.PRODUCT_CODE_MAX_LENGTH)

    def test_a_supplied_code_is_kept_exactly_and_nothing_is_generated(self):
        product = self.product('Cable')
        result = self.ensure(product, 'abC-01')
        self.assertIsNone(result.generated)
        self.assertEqual(codes_of(product), ['abC-01'])
        self.assertIsNone(series(self.company))                       # not even the series row

    def test_leading_zeros_capitals_and_length_are_kept(self):
        product = self.product('Cargador')
        self.ensure(product, '  0075012345678 ', 'c000701', 'C000702')
        self.assertEqual(codes_of(product), ['0075012345678', 'c000701', 'C000702'])

    def test_a_manufacturer_ean_is_kept_and_its_symbology_is_named_only_by_its_check_digit(self):
        ean = self.product('Audífonos')
        self.ensure(ean, '7501031311309')                             # a valid EAN-13
        self.assertEqual(ProductBarcode.objects.get(product=ean).symbology, ProductBarcode.EAN13)

        internal = self.product('Protector')
        self.ensure(internal, '100000000001')                         # 12 digits, NOT a UPC-A
        row = ProductBarcode.objects.get(product=internal)
        self.assertEqual(row.code, '100000000001')
        self.assertEqual(row.symbology, ProductBarcode.UNKNOWN)

    def test_a_code_of_another_product_is_refused_and_no_alternative_is_generated(self):
        first = self.product('Funda A')
        self.ensure(first, 'FUNDA-01')
        second = self.product('Funda B')
        with self.assertRaises(product_codes.ProductCodeError) as refused:
            self.ensure(second, 'FUNDA-01')
        self.assertEqual(refused.exception.code, 'code_conflict')
        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(refused.exception.existing_product, first)
        self.assertIn('Funda A', refused.exception.detail)
        self.assertEqual(codes_of(second), [])
        self.assertIsNone(series(self.company))

    def test_the_code_of_a_deactivated_product_is_refused_too(self):
        old = self.product('Funda vieja')
        self.ensure(old, 'VIEJA-01')
        old.is_active = False
        old.save(update_fields=['is_active'])
        with self.assertRaises(product_codes.ProductCodeError):
            self.ensure(self.product('Funda nueva'), 'VIEJA-01')
        self.assertEqual(codes_of(old), ['VIEJA-01'])

    def test_a_deactivated_products_generated_code_is_never_issued_again(self):
        old = self.product('Funda gris')
        spent = self.ensure(old).generated.code
        old.is_active = False
        old.save(update_fields=['is_active'])
        # Even a series moved back by hand skips what any product of the company holds.
        InternalSequence.objects.filter(pk=series(self.company).pk).update(next_value=1)
        fresh = self.ensure(self.product('Funda verde')).generated.code
        self.assertNotEqual(fresh, spent)
        self.assertEqual(codes_of(old), [spent])

    def test_an_occupied_number_is_spent_and_skipped_whatever_its_capitals(self):
        imported = self.product('Importado')
        self.ensure(imported, 'P000001', 'p000002')
        product = self.product('Nuevo')
        self.assertEqual(self.ensure(product).generated.code, 'P000003')
        self.assertEqual(next_value(self.company), 4)

    def test_a_code_that_differs_only_in_capitals_is_accepted_and_flagged(self):
        self.ensure(self.product('Mayúsculas'), 'ABC123')
        product = self.product('Minúsculas')
        result = self.ensure(product, 'abc123')
        self.assertEqual(codes_of(product), ['abc123'])
        self.assertTrue(any('mayúsculas' in warning for warning in result.warnings), result.warnings)

    def test_a_device_imei_or_serial_is_refused_as_a_product_code(self):
        self.device_with_imei(IMEI_A, serial='SNCODE01')
        for position, code in enumerate((IMEI_A, 'SNCODE01', 'sncode01')):
            with self.subTest(code=code), self.assertRaises(product_codes.ProductCodeError) as refused:
                self.ensure(self.product(f'Accesorio {position}'), code)
            self.assertEqual(refused.exception.code, 'unit_identifier_conflict')
            self.assertEqual(refused.exception.status, 409)

    def test_an_invalid_code_is_refused_and_a_number_is_not_text(self):
        product = self.product('Inválido')
        for raw in ('ab', 'con espacio', 'x' * 65, 750123456789):
            with self.subTest(raw=raw), self.assertRaises(product_codes.ProductCodeError) as refused:
                self.ensure(product, raw)
            self.assertEqual(refused.exception.status, 400)
        self.assertEqual(codes_of(product), [])

    def test_several_codes_are_all_kept_and_new_ones_are_added_without_touching_them(self):
        product = self.product('Varios')
        self.ensure(product, '7501031311309', 'INT-01')
        primary = ProductBarcode.objects.get(product=product, is_primary=True)
        self.ensure(product, 'INT-01', 'INT-02')
        self.assertEqual(codes_of(product), ['7501031311309', 'INT-01', 'INT-02'])
        self.assertEqual(ProductBarcode.objects.get(product=product, is_primary=True), primary)

    def test_calling_it_again_never_regenerates_or_changes_a_code(self):
        """What a reprint, an edit or a retry would do: nothing."""
        product = self.product('Reimpresión')
        code = self.ensure(product).generated.code
        before = list(ProductBarcode.objects.filter(product=product).values())
        for _ in range(3):
            self.assertIsNone(self.ensure(product).generated)
        self.assertEqual(list(ProductBarcode.objects.filter(product=product).values()), before)
        self.assertEqual(codes_of(product), [code])
        self.assertEqual(next_value(self.company), 2)

    def test_a_product_with_only_inactive_codes_does_not_receive_a_new_one(self):
        product = self.product('Inactivo')
        self.ensure(product, 'INACT-01')
        ProductBarcode.objects.filter(product=product).update(is_active=False)
        self.assertIsNone(self.ensure(product).generated)
        self.assertEqual(codes_of(product), ['INACT-01'])

    def test_generate_false_adds_supplied_codes_but_never_generates(self):
        product = self.product('Sin generar')
        self.assertIsNone(self.ensure(product, generate=False).generated)
        self.assertEqual(codes_of(product), [])

    def test_two_companies_number_their_codes_apart(self):
        mine = self.ensure(self.product('Mía')).generated.code
        theirs = self.ensure(self.product('Suya', company=self.other)).generated.code
        self.assertEqual((mine, theirs), ('P000001', 'P000001'))
        self.assertNotEqual(series(self.company).pk, series(self.other).pk)

    def test_the_prefix_and_padding_are_the_companys(self):
        product_codes.product_code_sequence(self.company)
        row = series(self.company)
        row.prefix, row.padding = 'C', 6
        row.save()
        self.assertEqual(self.ensure(self.product('Con C')).generated.code, 'C000001')
        self.assertEqual(self.ensure(self.product('Otra P', company=self.other)).generated.code, 'P000001')

    def test_a_prefix_that_could_not_be_scanned_or_passes_for_a_gtin_is_refused(self):
        row = product_codes.product_code_sequence(self.company)
        for prefix, padding in (('p', 6), ('C-', 6), ('12', 6), ('', 6), ('ABCDE', 4), ('C', 10), ('C', 2)):
            row.prefix, row.padding = prefix, padding
            with self.subTest(prefix=prefix, padding=padding), self.assertRaises(ValidationError):
                row.save()

    def test_a_disabled_series_refuses_clearly_instead_of_leaving_the_product_without_a_code(self):
        row = product_codes.product_code_sequence(self.company)
        row.is_active = False
        row.save()
        with self.assertRaises(product_codes.ProductCodeError) as refused:
            self.ensure(self.product('Serie apagada'))
        self.assertEqual(refused.exception.status, 400)

    def test_a_series_full_of_occupied_numbers_stops_with_a_clear_error(self):
        self.ensure(self.product('Ocupa'), 'P000001', 'P000002', 'P000003')
        with mock.patch.object(product_codes, 'MAX_SKIPS', 3), \
                self.assertRaises(product_codes.ProductCodeError) as refused:
            self.ensure(self.product('Sin hueco'))
        self.assertEqual(refused.exception.code, 'sequence_exhausted')

    def test_a_counter_that_outgrows_the_maximum_length_stops(self):
        row = product_codes.product_code_sequence(self.company)
        row.prefix, row.padding = 'P', 9
        row.save()
        InternalSequence.objects.filter(pk=row.pk).update(next_value=10 ** 9)
        with self.assertRaises(product_codes.ProductCodeError) as refused:
            self.ensure(self.product('Muy largo'))
        self.assertEqual(refused.exception.code, 'sequence_exhausted')

    def test_it_must_run_inside_the_transaction_that_writes_the_product(self):
        product = self.product('Fuera')
        outside = mock.Mock(in_atomic_block=False)
        with mock.patch.object(product_codes.transaction, 'get_connection', return_value=outside), \
                self.assertRaises(product_codes.ProductCodeError) as refused:
            product_codes.ensure_code(product)
        self.assertEqual(refused.exception.code, 'not_in_transaction')
        self.assertEqual(codes_of(product), [])

    def test_each_code_written_leaves_an_audit_line_with_the_company(self):
        product = self.product('Auditada')
        self.ensure(product, actor=self.manager)
        line = AdminAuditLog.objects.get(action='barcode_created', target_type='product_barcode')
        self.assertEqual(line.company, self.company)
        self.assertEqual(line.metadata['product_id'], product.pk)
        self.assertTrue(line.metadata['generated'])


# ---------------------------------------------------------------------------
# Every creation path
# ---------------------------------------------------------------------------

class AdminCreateTest(Fixture):
    def create(self, user, **body):
        payload = {'name': 'Funda MagSafe', 'price': '89.90', 'category': self.cases.pk}
        payload.update(body)
        return client_for(user).post(PRODUCTS, payload, format='json')

    def test_without_a_code_the_new_product_receives_one(self):
        res = self.create(self.manager)
        self.assertEqual(res.status_code, 201, res.content)
        product = Product.objects.get(pk=res.json()['id'])
        self.assertEqual(codes_of(product), ['P000001'])
        self.assertEqual([row['code'] for row in res.json()['codes']], ['P000001'])

    def test_a_written_code_is_kept_and_nothing_is_generated(self):
        res = self.create(self.manager, code=' 0075012345678 ')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(codes_of(Product.objects.get(pk=res.json()['id'])), ['0075012345678'])
        self.assertIsNone(series(self.company))

    def test_a_code_of_another_product_is_a_409_naming_the_field_and_nothing_is_created(self):
        first = self.create(self.manager, code='FUNDA-01').json()
        products = Product.objects.count()
        res = self.create(self.manager, name='Otra funda', code='FUNDA-01')
        self.assertEqual(res.status_code, 409, res.content)
        body = res.json()
        self.assertEqual((body['field'], body['code']), ('code', 'code_conflict'))
        self.assertEqual(body['existing_product'], first['id'])
        self.assertEqual(Product.objects.count(), products)
        self.assertIsNone(series(self.company))

    def test_a_device_imei_is_a_409(self):
        self.device_with_imei(IMEI_A)
        res = self.create(self.manager, code=IMEI_A)
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'unit_identifier_conflict')

    def test_without_products_manage_it_is_403_and_no_number_is_spent(self):
        res = self.create(self.viewer)
        self.assertEqual(res.status_code, 403)
        self.assertIsNone(series(self.company))

    def test_editing_never_generates_nor_changes_a_code(self):
        bare = self.product('Sin código previo')                     # predates LABEL-01
        coded = Product.objects.get(pk=self.create(self.manager).json()['id'])
        for product in (bare, coded):
            before = codes_of(product)
            res = client_for(self.manager).patch(f'{PRODUCTS}{product.pk}/',
                                                  {'price': '99.00', 'is_active': False}, format='json')
            self.assertEqual(res.status_code, 200, res.content)
            self.assertEqual(codes_of(product), before)
        self.assertEqual(next_value(self.company), 2)


class RegistrationTest(Fixture):
    def accessory(self, **product):
        body = {'name': 'Funda MagSafe', 'category': self.cases.pk, 'price': '89.90', 'is_active': True,
                'show_online': False}
        body.update(product)
        return {'kind': 'accessory', 'product': body, 'stock': []}

    def register(self, body, key='reg-1'):
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return client_for(self.manager).post(REGISTER, body, format='json', **headers)

    def test_without_sku_the_article_receives_a_generated_code(self):
        res = self.register(self.accessory())
        self.assertEqual(res.status_code, 201, res.content)
        product = Product.objects.get(pk=res.json()['product']['id'])
        self.assertEqual(codes_of(product), ['P000001'])
        self.assertEqual(res.json()['product']['codes'], ['P000001'])

    def test_a_written_sku_is_kept_with_its_capitals_and_nothing_is_generated(self):
        res = self.register(self.accessory(sku='0abC-77'))
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(codes_of(Product.objects.get(pk=res.json()['product']['id'])), ['0abC-77'])
        self.assertIsNone(series(self.company))

    def test_a_sku_of_another_article_is_a_409_on_the_sku_field(self):
        self.register(self.accessory(sku='FUNDA-01'), key='reg-a')
        res = self.register(self.accessory(name='Otra', sku='FUNDA-01'), key='reg-b')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual((res.json()['field'], res.json()['code']), ('sku', 'code_conflict'))
        self.assertIsNone(series(self.company))

    def test_a_dry_run_spends_no_number(self):
        body = {**self.accessory(), 'dry_run': True}
        self.assertEqual(self.register(body, key=None).status_code, 200)
        self.assertEqual(next_value(self.company), 1)
        self.assertEqual(ProductBarcode.objects.filter(company=self.company).count(), 0)

    def test_repeating_the_same_registration_spends_no_second_number(self):
        first = self.register(self.accessory())
        again = self.register(self.accessory())
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertTrue(again.json()['replayed'])
        self.assertEqual(ProductBarcode.objects.filter(company=self.company).count(), 1)
        self.assertEqual(next_value(self.company), 2)


# ---------------------------------------------------------------------------
# The codes API: manual codes, «generate», tenancy and permissions
# ---------------------------------------------------------------------------

class CodesApiTest(Fixture):
    def test_a_manual_code_equal_to_a_device_imei_is_refused(self):
        self.device_with_imei(IMEI_A)
        product = self.product('Funda')
        res = client_for(self.manager).post(barcodes_url(product.pk), {'code': IMEI_A}, format='json')
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual((res.json()['field'], res.json()['code']), ('code', 'unit_identifier_conflict'))
        self.assertEqual(codes_of(product), [])

    def test_a_manual_code_of_another_product_names_it(self):
        holder = self.product('Dueño')
        self.ensure(holder, 'DUENO-1')
        res = client_for(self.manager).post(barcodes_url(self.product('Otro').pk), {'code': 'DUENO-1'},
                                            format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['existing_product'], holder.pk)
        self.assertEqual(res.json()['code'], 'code_conflict')

    def test_generate_gives_a_code_only_when_missing_and_repeating_returns_the_same(self):
        product = self.product('Generar')
        first = client_for(self.manager).post(generate_url(product.pk), format='json')
        self.assertEqual(first.status_code, 201, first.content)
        self.assertTrue(first.json()['generated'])
        again = client_for(self.manager).post(generate_url(product.pk), format='json')
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()['generated'])
        self.assertEqual(codes_of(product), ['P000001'])
        self.assertEqual([row['code'] for row in again.json()['results']], ['P000001'])

    def test_generate_leaves_a_product_with_codes_alone(self):
        product = self.product('Con EAN')
        self.ensure(product, '7501031311309')
        res = client_for(self.manager).post(generate_url(product.pk), format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(codes_of(product), ['7501031311309'])
        self.assertIsNone(series(self.company))

    def test_generate_refuses_a_product_whose_codes_were_all_deactivated(self):
        product = self.product('Desactivados')
        self.ensure(product, 'DESAC-1')
        ProductBarcode.objects.filter(product=product).update(is_active=False)
        res = client_for(self.manager).post(generate_url(product.pk), format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'only_inactive_codes')
        self.assertEqual(codes_of(product), ['DESAC-1'])

    def test_another_companys_product_is_404_and_nothing_is_spent(self):
        foreign = self.product('Ajeno', company=self.other)
        for method, url in (('post', generate_url(foreign.pk)), ('get', barcodes_url(foreign.pk)),
                            ('post', barcodes_url(foreign.pk))):
            with self.subTest(method=method, url=url):
                res = getattr(client_for(self.manager), method)(url, {'code': 'AJENO-1'}, format='json')
                self.assertEqual(res.status_code, 404)
        self.assertEqual(codes_of(foreign), [])
        self.assertIsNone(series(self.other))

    def test_generating_needs_products_manage(self):
        product = self.product('Sólo ver')
        res = client_for(self.viewer).post(generate_url(product.pk), format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(client_for(self.viewer).get(barcodes_url(product.pk)).status_code, 200)
        self.assertEqual(codes_of(product), [])

    def test_generating_leaves_an_audit_line(self):
        product = self.product('Auditar')
        client_for(self.manager).post(generate_url(product.pk), format='json')
        self.assertTrue(AdminAuditLog.objects.filter(
            company=self.company, action='barcode_created', metadata__generated=True).exists())


class PosCompatibilityTest(Fixture):
    def test_the_till_finds_the_generated_code_and_the_imei_still_finds_its_device(self):
        accessory = Product.objects.get(pk=client_for(self.manager).post(
            PRODUCTS, {'name': 'Funda POS', 'price': '20.00'}, format='json').json()['id'])
        code = codes_of(accessory)[0]
        phone = self.device_with_imei(IMEI_B)

        till = client_for(self.manager)
        found = till.get(POS_LOOKUP, {'code': code, 'branch': self.branch.pk})
        self.assertEqual(found.status_code, 200, found.content)
        self.assertEqual(found.json()['id'], accessory.pk)

        # The product lookup does not take the IMEI…
        self.assertEqual(till.get(POS_LOOKUP, {'code': IMEI_B, 'branch': self.branch.pk}).status_code, 404)
        # …so the unit lookup still resolves it.
        unit = till.get(POS_UNIT_LOOKUP, {'code': IMEI_B, 'branch': self.branch.pk})
        self.assertEqual(unit.status_code, 200, unit.content)
        self.assertEqual(StockUnit.objects.get(imei=IMEI_B).product, phone)

    def test_another_company_cannot_scan_this_companys_code(self):
        accessory = self.product('Funda privada')
        self.ensure(accessory)
        res = client_for(self.outsider).get(POS_LOOKUP, {'code': codes_of(accessory)[0],
                                                         'branch': self.other.default_inventory_branch.pk})
        self.assertEqual(res.status_code, 404)


# ---------------------------------------------------------------------------
# assign_internal_codes
# ---------------------------------------------------------------------------

class AssignCommandTest(Fixture):
    def run_command(self, *args):
        out = io.StringIO()
        call_command('assign_internal_codes', '--company', self.company.slug, *args, stdout=out)
        return out.getvalue()

    def test_dry_run_by_default_writes_nothing(self):
        bare = self.product('Sin código')
        output = self.run_command()
        self.assertIn('1', output)
        self.assertIn('--apply', output)
        self.assertEqual(codes_of(bare), [])
        self.assertIsNone(series(self.company))

    def test_apply_fills_only_the_missing_and_a_second_run_does_nothing(self):
        bare = self.product('Sin código')
        coded = self.product('Con código')
        self.ensure(coded, '0075012345678')
        inactive_codes = self.product('Con inactivos')
        self.ensure(inactive_codes, 'INACT-9')
        ProductBarcode.objects.filter(product=inactive_codes).update(is_active=False)
        retired = self.product('Retirado', is_active=False)
        foreign = self.product('Ajeno', company=self.other)

        self.run_command('--apply')
        self.assertEqual(codes_of(bare), ['P000001'])
        self.assertEqual(codes_of(coded), ['0075012345678'])
        self.assertEqual(codes_of(inactive_codes), ['INACT-9'])
        self.assertEqual(codes_of(retired), [])
        self.assertEqual(codes_of(foreign), [])

        self.run_command('--apply')
        self.assertEqual(codes_of(bare), ['P000001'])
        self.assertEqual(next_value(self.company), 2)

    def test_an_unknown_company_is_an_error(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('assign_internal_codes', '--company', 'no-existe', stdout=io.StringIO())


# ---------------------------------------------------------------------------
# Concurrency — real threads, real commits (PostgreSQL)
# ---------------------------------------------------------------------------

class TwoCreationsAtOnceTest(TransactionTestCase):
    """Two people create a product without a code at the same instant."""

    def together(self, jobs):
        barrier = threading.Barrier(len(jobs))
        results = [None] * len(jobs)

        def run(position, job):
            try:
                barrier.wait(timeout=10)
                results[position] = job()
            except Exception as exc:  # noqa: BLE001
                results[position] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(position, job)) for position, job in enumerate(jobs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return results

    def test_they_receive_distinct_consecutive_codes(self):
        cache.clear()
        company = _p3_company('codes-race', 'Tienda Carrera')
        user = get_user_model().objects.create_user('codes_race_admin', password='Pass123!')
        membership = Membership.objects.create(user=user, company=company, role='customer')
        _assign(membership, CompanyRole.objects.get(company=company, slug='administrador'))

        def create(position):
            res = client_for(user).post(PRODUCTS, {'name': f'Carrera {position}', 'price': '5.00'}, format='json')
            return res.status_code, res.json()

        # Widen the window between reading the counter and writing it, so the
        # four requests really overlap inside the allocation.
        real_format = InternalSequence.format

        def slow_format(sequence, value):
            time.sleep(0.15)
            return real_format(sequence, value)

        with mock.patch.object(InternalSequence, 'format', slow_format):
            results = self.together([lambda position=position: create(position) for position in range(4)])
        for result in results:
            self.assertNotIsInstance(result, Exception, repr(result))
        self.assertEqual([status for status, _ in results], [201] * 4, results)
        codes = sorted(ProductBarcode.objects.filter(company=company).values_list('code', flat=True))
        self.assertEqual(codes, ['P000001', 'P000002', 'P000003', 'P000004'])
        self.assertEqual(series(company).next_value, 5)
