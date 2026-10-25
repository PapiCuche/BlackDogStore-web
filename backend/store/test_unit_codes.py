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


# ---------------------------------------------------------------------------
# label_code — the stable internal identifier of every device (D4)
# ---------------------------------------------------------------------------

POS_UNIT_LOOKUP = '/api/admin/pos/units/lookup/'
POS_LOOKUP = '/api/admin/pos/products/lookup/'


def luhn_ok(digits):
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2:
            value *= 2
            value -= 9 if value > 9 else 0
        total += value
    return total % 10 == 0


class LabelCodeTest(Fixture):
    def test_every_received_device_gets_its_own_internal_code(self):
        phone = self.product('Teléfono', serialized=True)
        units = self.receive(phone, {'serial_number': 'SNLC0001', 'imei': IMEI_A},
                             {'serial_number': 'SNLC0002', 'imei': IMEI_B})
        stored = dict(StockUnit.objects.filter(pk__in=[u.pk for u in units]).values_list('pk', 'label_code'))
        self.assertEqual(len(set(stored.values())), 2)
        for unit in units:
            code = stored[unit.pk]
            self.assertRegex(code, r'^U\d{10}$')
            self.assertTrue(luhn_ok(code[1:]), code)
            self.assertEqual(unit.label_code, code)                 # the returned objects carry it
            self.assertNotIn(code[1:10], (IMEI_A, IMEI_B))
            self.assertNotIn(IMEI_A[:9], code)

    def test_a_device_without_imei_gets_one_too(self):
        part = self.product('Repuesto seriado', serialized=True, requires_imei=False)
        unit = self.receive(part, {'serial_number': 'SNLC0003'})[0]
        self.assertRegex(StockUnit.objects.get(pk=unit.pk).label_code, r'^U\d{10}$')

    def test_the_prefix_is_the_companys(self):
        from store import unit_codes

        unit_codes.update_settings(self.company, {'unit_code_prefix': 'EQ'}, actor=self.clerk)
        phone = self.product('Teléfono', serialized=True)
        mine = self.receive(phone, {'serial_number': 'SNLC0004', 'imei': IMEI_A})[0]
        theirs_phone = self.product('Teléfono ajeno', company=self.other, serialized=True)
        theirs = self.receive(theirs_phone, {'serial_number': 'SNLC0004', 'imei': IMEI_A},
                              branch=self.other.default_inventory_branch)[0]
        self.assertRegex(StockUnit.objects.get(pk=mine.pk).label_code, r'^EQ\d{10}$')
        self.assertRegex(StockUnit.objects.get(pk=theirs.pk).label_code, r'^U\d{10}$')

    def test_a_drawn_code_that_is_taken_is_drawn_again(self):
        from unittest import mock

        from store import unit_codes

        phone = self.product('Teléfono', serialized=True)
        first = self.receive(phone, {'serial_number': 'SNLC0005', 'imei': IMEI_A})[0]
        taken = StockUnit.objects.get(pk=first.pk).label_code[1:10]
        self.coded('U' + '123456789' + str(unit_codes.luhn_check_digit('123456789')))
        draws = iter([taken, '123456789', '555555555'])
        with mock.patch.object(unit_codes, '_random_body', lambda: next(draws)):
            second = self.receive(phone, {'serial_number': 'SNLC0006', 'imei': IMEI_B})[0]
        self.assertEqual(StockUnit.objects.get(pk=second.pk).label_code,
                         'U555555555' + str(unit_codes.luhn_check_digit('555555555')))

    def test_when_every_draw_is_taken_the_reception_is_refused_clearly(self):
        from unittest import mock

        from store import unit_codes

        phone = self.product('Teléfono', serialized=True)
        first = self.receive(phone, {'serial_number': 'SNLC0007', 'imei': IMEI_A})[0]
        taken = StockUnit.objects.get(pk=first.pk).label_code[1:10]
        with mock.patch.object(unit_codes, '_random_body', lambda: taken), \
                self.assertRaises(units_service.StockUnitError):
            self.receive(phone, {'serial_number': 'SNLC0008', 'imei': IMEI_B})
        self.assertEqual(StockUnit.objects.filter(product=phone).count(), 1)

    def test_assigning_never_overwrites_a_code(self):
        from store import unit_codes

        phone = self.product('Teléfono', serialized=True)
        unit = self.receive(phone, {'serial_number': 'SNLC0009', 'imei': IMEI_A})[0]
        code = StockUnit.objects.get(pk=unit.pk).label_code
        with transaction.atomic():
            self.assertEqual(unit_codes.assign_label_code(StockUnit.objects.get(pk=unit.pk)), code)
        self.assertEqual(StockUnit.objects.get(pk=unit.pk).label_code, code)

    def test_a_stale_copy_without_the_code_never_overwrites_the_stored_one(self):
        from store import unit_codes

        phone = self.product('Teléfono', serialized=True)
        unit = self.receive(phone, {'serial_number': 'SNLC0016', 'imei': IMEI_A})[0]
        stored = StockUnit.objects.get(pk=unit.pk).label_code
        stale = StockUnit.objects.get(pk=unit.pk)
        stale.label_code = None                                  # read before it was coded
        with transaction.atomic():
            self.assertEqual(unit_codes.assign_label_code(stale), stored)
        self.assertEqual(StockUnit.objects.get(pk=unit.pk).label_code, stored)

    def test_a_label_code_cannot_become_a_product_code(self):
        phone = self.product('Teléfono', serialized=True)
        unit = self.receive(phone, {'serial_number': 'SNLC0010', 'imei': IMEI_A})[0]
        code = StockUnit.objects.get(pk=unit.pk).label_code
        product = self.product('Funda')
        for supplied in (code, code.lower()):
            with self.subTest(code=supplied), self.assertRaises(product_codes.ProductCodeError) as refused, \
                    transaction.atomic():
                product_codes.ensure_code(product, supplied=[supplied])
            self.assertEqual(refused.exception.code, 'unit_identifier_conflict')

    def test_the_till_finds_the_device_by_its_internal_code(self):
        phone = self.product('Teléfono', serialized=True)
        unit = self.receive(phone, {'serial_number': 'SNLC0011', 'imei': IMEI_A})[0]
        code = StockUnit.objects.get(pk=unit.pk).label_code
        till = client_for(self.clerk)
        for scanned in (code, code.lower()):
            with self.subTest(scanned=scanned):
                res = till.get(POS_UNIT_LOOKUP, {'code': scanned, 'branch': self.branch.pk})
                self.assertEqual(res.status_code, 200, res.content)
                self.assertEqual(res.json()['unit']['unit_id'], unit.pk)
                self.assertEqual(res.json()['unit']['label_code'], code)
        self.assertEqual(till.get(POS_LOOKUP, {'code': code, 'branch': self.branch.pk}).status_code, 404)
        # The IMEI and the serial keep working exactly as before.
        self.assertEqual(till.get(POS_UNIT_LOOKUP, {'code': IMEI_A, 'branch': self.branch.pk}).status_code, 200)
        self.assertEqual(till.get(POS_UNIT_LOOKUP, {'code': 'SNLC0011', 'branch': self.branch.pk}).status_code, 200)

    def test_typed_by_hand_a_phone_is_still_identified_by_its_imei(self):
        phone = self.product('Teléfono', serialized=True)
        unit = self.receive(phone, {'serial_number': 'SNLC0012', 'imei': IMEI_A})[0]
        code = StockUnit.objects.get(pk=unit.pk).label_code
        res = client_for(self.clerk).get(POS_UNIT_LOOKUP, {'code': code, 'branch': self.branch.pk,
                                                           'method': 'manual'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'manual_needs_imei')

    def test_a_code_with_a_wrong_check_digit_is_not_taken_for_an_internal_code(self):
        part = self.product('Repuesto', serialized=True, requires_imei=False)
        unit = self.receive(part, {'serial_number': 'SNLC0013'})[0]
        code = StockUnit.objects.get(pk=unit.pk).label_code
        wrong = code[:-1] + str((int(code[-1]) + 1) % 10)
        res = client_for(self.clerk).get(POS_UNIT_LOOKUP, {'code': wrong, 'branch': self.branch.pk})
        self.assertEqual(res.status_code, 404)

    def test_another_company_does_not_find_it(self):
        phone = self.product('Teléfono', serialized=True)
        unit = self.receive(phone, {'serial_number': 'SNLC0014', 'imei': IMEI_A})[0]
        code = StockUnit.objects.get(pk=unit.pk).label_code
        outsider, _ = _p2d_member(self.other, 'ucodes_outsider', ['sales.pos.use', 'inventory.view'])
        res = client_for(outsider).get(POS_UNIT_LOOKUP, {'code': code,
                                                         'branch': self.other.default_inventory_branch.pk})
        self.assertEqual(res.status_code, 404)

    def test_the_inventory_list_shows_it(self):
        phone = self.product('Teléfono', serialized=True)
        unit = self.receive(phone, {'serial_number': 'SNLC0015', 'imei': IMEI_A})[0]
        res = client_for(self.clerk).get(UNITS, {'branch': self.branch.pk})
        self.assertEqual(res.status_code, 200, res.content)
        row = next(r for r in res.json()['results'] if r['id'] == unit.pk)
        self.assertEqual(row['label_code'], StockUnit.objects.get(pk=unit.pk).label_code)


class UnitCodeCommandsTest(Fixture):
    def bare_unit(self, serial, imei_value=None):
        """A device received before LABEL-02: no internal code."""
        phone = self.product(f'Antiguo {serial}', serialized=True, requires_imei=bool(imei_value))
        row = {'serial_number': serial}
        if imei_value:
            row['imei'] = imei_value
        unit = self.receive(phone, row)[0]
        StockUnit.objects.filter(pk=unit.pk).update(label_code=None)
        return unit

    def run_command(self, name, *args):
        import io

        from django.core.management import call_command

        out = io.StringIO()
        call_command(name, *args, stdout=out)
        return out.getvalue()

    def test_assign_unit_codes_is_a_dry_run_by_default_then_fills_only_the_missing(self):
        bare = self.bare_unit('SNOLD001', IMEI_A)
        phone = self.product('Nuevo', serialized=True)
        coded = self.receive(phone, {'serial_number': 'SNNEW001', 'imei': IMEI_B})[0]
        kept = StockUnit.objects.get(pk=coded.pk).label_code

        output = self.run_command('assign_unit_codes', '--company', self.company.slug)
        self.assertIn('--apply', output)
        self.assertIsNone(StockUnit.objects.get(pk=bare.pk).label_code)

        self.run_command('assign_unit_codes', '--company', self.company.slug, '--apply')
        self.assertRegex(StockUnit.objects.get(pk=bare.pk).label_code, r'^U\d{10}$')
        self.assertEqual(StockUnit.objects.get(pk=coded.pk).label_code, kept)

        first = StockUnit.objects.get(pk=bare.pk).label_code
        self.run_command('assign_unit_codes', '--company', self.company.slug, '--apply')
        self.assertEqual(StockUnit.objects.get(pk=bare.pk).label_code, first)

    def test_assign_unit_codes_does_not_touch_another_company(self):
        foreign_phone = self.product('Ajeno', company=self.other, serialized=True)
        foreign = self.receive(foreign_phone, {'serial_number': 'SNFOR001', 'imei': IMEI_A},
                               branch=self.other.default_inventory_branch)[0]
        StockUnit.objects.filter(pk=foreign.pk).update(label_code=None)
        self.run_command('assign_unit_codes', '--company', self.company.slug, '--apply')
        self.assertIsNone(StockUnit.objects.get(pk=foreign.pk).label_code)

    def test_audit_code_collisions_lists_what_the_data_already_holds_without_the_identifier(self):
        unit = self.bare_unit('SNCOL001', IMEI_A)
        clash = self.product('Choque')
        ProductBarcode.objects.create(company=self.company, product=clash, code=IMEI_A)   # pre-LABEL data
        output = self.run_command('audit_code_collisions', '--company', self.company.slug)
        self.assertIn('1 ', output)
        self.assertIn(f'unit_id={unit.pk}', output)
        self.assertIn(f'product_id={clash.pk}', output)
        self.assertNotIn(IMEI_A, output)
        self.assertEqual(ProductBarcode.objects.filter(product=clash).count(), 1)      # read-only


# ---------------------------------------------------------------------------
# Capabilities, settings and the two keys of an IMEI on paper (D6)
# ---------------------------------------------------------------------------

SETTINGS = '/api/admin/label-settings/'
PRINT, SENSITIVE = 'inventory.labels.print', 'inventory.labels.sensitive'


def _label_capabilities_migration():
    """The LABEL-02 capability migration, found by name (its number moves with the merge order)."""
    import importlib
    import pkgutil

    import store.migrations

    names = [m.name for m in pkgutil.iter_modules(store.migrations.__path__)
             if m.name.endswith('_label_capabilities')]
    assert len(names) == 1, names
    return importlib.import_module(f'store.migrations.{names[0]}')


class CapabilitiesTest(Fixture):
    def caps(self, slug, company=None):
        from store.models import CompanyRole

        return set(CompanyRole.objects.get(company=company or self.company, slug=slug).capabilities)

    def test_both_are_in_the_catalogue_and_assignable(self):
        from store.capabilities import ASSIGNABLE_CAPABILITY_CODES, CAPABILITIES

        for code in (PRINT, SENSITIVE):
            self.assertIn(code, ASSIGNABLE_CAPABILITY_CODES)
            self.assertEqual(CAPABILITIES[code].module, 'inventory')

    def test_a_new_company_gives_printing_to_inventory_and_both_to_administration(self):
        self.assertIn(PRINT, self.caps('inventario'))
        self.assertNotIn(SENSITIVE, self.caps('inventario'))
        self.assertTrue({PRINT, SENSITIVE} <= self.caps('administrador'))
        self.assertFalse({PRINT, SENSITIVE} & self.caps('ventas'))

    def test_the_legacy_roles_agree(self):
        from store.models import UserProfile
        from store.tenancy import LEGACY_ROLE_CAPABILITIES

        self.assertIn(PRINT, LEGACY_ROLE_CAPABILITIES[UserProfile.ROLE_INVENTORY])
        self.assertNotIn(SENSITIVE, LEGACY_ROLE_CAPABILITIES[UserProfile.ROLE_INVENTORY])
        self.assertIn(SENSITIVE, LEGACY_ROLE_CAPABILITIES[UserProfile.ROLE_ADMIN])

    def test_the_migration_keeps_what_everybody_could_do_and_gives_sensitive_only_to_an_untouched_admin(self):
        from django.apps import apps

        from store.models import CompanyRole

        module = _label_capabilities_migration()
        roles = CompanyRole.objects.filter(company=self.company)
        inventory = sorted(self.caps('inventario') - {PRINT})
        roles.filter(slug='inventario').update(capabilities=inventory)
        roles.filter(slug='administrador').update(capabilities=sorted(module.ADMIN_PREVIOUS))
        edited_admin = sorted(module.ADMIN_PREVIOUS - {'reports.view'})
        CompanyRole.objects.filter(company=self.other, slug='administrador').update(capabilities=edited_admin)
        sales = sorted(self.caps('ventas'))

        module.grant(apps, None)
        module.grant(apps, None)                                       # idempotent

        self.assertEqual(self.caps('inventario'), set(inventory) | {PRINT})
        self.assertEqual(self.caps('administrador'), set(module.ADMIN_PREVIOUS) | {PRINT, SENSITIVE})
        self.assertEqual(self.caps('administrador', self.other), set(edited_admin) | {PRINT})
        self.assertEqual(sorted(self.caps('ventas')), sales)

    def test_the_frozen_set_is_frozen_and_ends_at_the_catalogue(self):
        import inspect

        from store.capabilities import ASSIGNABLE_CAPABILITY_CODES

        module = _label_capabilities_migration()
        source = inspect.getsource(module)
        self.assertNotIn('from store.capabilities import', source)
        self.assertEqual(module.ADMIN_PREVIOUS | set(module.NEW_CAPABILITIES), frozenset(ASSIGNABLE_CAPABILITY_CODES))


class LabelSettingsTest(Fixture):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        from store.models import CompanyRole, Membership
        from store.tests import _assign

        self.admin = get_user_model().objects.create_user('ucodes_admin', password='Pass123!')
        membership = Membership.objects.create(user=self.admin, company=self.company, role='customer')
        _assign(membership, CompanyRole.objects.get(company=self.company, slug='administrador'))

    def test_reading_gives_the_defaults_and_writes_nothing(self):
        from store.models import CompanyLabelSettings

        res = client_for(self.admin).get(SETTINGS)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['unit_code_prefix'], res.json()['device_symbol']), ('U', 'internal'))
        self.assertTrue(res.json()['can_manage'])
        self.assertFalse(CompanyLabelSettings.objects.exists())

    def test_the_administrator_changes_them_and_it_is_audited_with_the_company(self):
        from store.models import AdminAuditLog

        res = client_for(self.admin).patch(SETTINGS, {'device_symbol': 'imei1', 'unit_code_prefix': 'EQ'},
                                          format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['unit_code_prefix'], res.json()['device_symbol']), ('EQ', 'imei1'))
        line = AdminAuditLog.objects.get(action='label_settings_updated')
        self.assertEqual(line.company, self.company)
        self.assertEqual(line.metadata['changed_fields'], ['device_symbol', 'unit_code_prefix'])

    def test_without_company_manage_it_is_403(self):
        res = client_for(self.clerk).patch(SETTINGS, {'device_symbol': 'imei1'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(client_for(self.clerk).get(SETTINGS).status_code, 403)       # no company.view either
        reader, _ = _p2d_member(self.company, 'ucodes_reader', ['company.view'])
        self.assertEqual(client_for(reader).get(SETTINGS).json()['can_manage'], False)
        self.assertEqual(client_for(reader).patch(SETTINGS, {'device_symbol': 'imei1'}, format='json').status_code,
                         403)
        from store import unit_codes

        self.assertEqual(unit_codes.settings_for(self.company).device_symbol, 'internal')

    def test_another_companys_settings_are_404(self):
        res = client_for(self.admin).patch(f'{SETTINGS}?company={self.other.pk}', {'device_symbol': 'imei1'},
                                          format='json')
        self.assertEqual(res.status_code, 404)

    def test_a_prefix_that_is_not_letters_or_is_the_product_prefix_and_an_unknown_symbol_are_400(self):
        for body, field in (({'unit_code_prefix': 'u1'}, 'unit_code_prefix'),
                            ({'unit_code_prefix': 'ABCDE'}, 'unit_code_prefix'),
                            ({'unit_code_prefix': 'P'}, 'unit_code_prefix'),
                            ({'device_symbol': 'imei2'}, 'device_symbol')):
            with self.subTest(body=body):
                res = client_for(self.admin).patch(SETTINGS, body, format='json')
                self.assertEqual(res.status_code, 400, res.content)
                self.assertIn(field, res.json())


class SensitiveAccessTest(Fixture):
    def test_an_imei_reaches_paper_only_with_the_profile_and_the_capability(self):
        from store import unit_codes

        sensitive, _ = _p2d_member(self.company, 'ucodes_sensitive', ['inventory.view', PRINT, SENSITIVE])
        plain, _ = _p2d_member(self.company, 'ucodes_plain', ['inventory.view', PRINT])

        self.assertFalse(unit_codes.device_label_access(sensitive, self.company).may_print_identifiers)
        unit_codes.update_settings(self.company, {'device_symbol': 'imei1'}, actor=self.clerk)
        self.assertTrue(unit_codes.device_label_access(sensitive, self.company).may_print_identifiers)
        self.assertFalse(unit_codes.device_label_access(plain, self.company).may_print_identifiers)
        other_user, _ = _p2d_member(self.other, 'ucodes_other_sensitive', ['inventory.view', PRINT, SENSITIVE])
        self.assertFalse(unit_codes.device_label_access(other_user, self.other).may_print_identifiers)

    def test_a_sensitive_print_is_audited_with_the_unit_ids_and_never_the_imei(self):
        from store import unit_codes

        phone = self.product('Teléfono', serialized=True)
        units = self.receive(phone, {'serial_number': 'SNSEN001', 'imei': IMEI_A},
                             {'serial_number': 'SNSEN002', 'imei': IMEI_B})
        line = unit_codes.audit_sensitive_print(actor=self.clerk, company=self.company,
                                                unit_ids=[u.pk for u in units], purpose='device_commercial')
        self.assertEqual(line.action, 'label_sensitive_printed')
        self.assertEqual(line.company, self.company)
        self.assertEqual(line.metadata['unit_ids'], sorted(u.pk for u in units))
        text = str(line.metadata)
        for secret in (IMEI_A, IMEI_B, 'SNSEN001'):
            self.assertNotIn(secret, text)


# ---------------------------------------------------------------------------
# #178 review — prefixes never coincide; a serial is never another device's code
# ---------------------------------------------------------------------------

class PrefixesApartTest(Fixture):
    def product_prefix(self, prefix, company=None):
        row = product_codes.product_code_sequence(company or self.company)
        row.prefix = prefix
        row.save()
        return row

    def legacy_product_prefix_U(self):
        """A product series set to `U` before the rule that keeps it apart from the unit prefix."""
        from store.models import InternalSequence

        row = product_codes.product_code_sequence(self.company)
        InternalSequence.objects.filter(pk=row.pk).update(prefix='U')

    def test_a_company_whose_product_prefix_is_U_can_still_save_its_settings(self):
        from store import unit_codes

        self.legacy_product_prefix_U()
        row = unit_codes.update_settings(self.company, {'device_symbol': 'imei1'}, actor=self.clerk)
        self.assertEqual(row.device_symbol, 'imei1')
        self.assertNotEqual(row.unit_code_prefix, 'U')
        self.assertEqual(row.unit_code_prefix, unit_codes.DEFAULT_FALLBACK_PREFIX)

    def test_an_empty_change_writes_no_row(self):
        from store import unit_codes
        from store.models import AdminAuditLog, CompanyLabelSettings

        unit_codes.update_settings(self.company, {}, actor=self.clerk)
        unit_codes.update_settings(self.company, {'device_symbol': 'internal'}, actor=self.clerk)
        self.assertFalse(CompanyLabelSettings.objects.filter(company=self.company).exists())
        self.assertFalse(AdminAuditLog.objects.filter(action='label_settings_updated').exists())

    def test_without_settings_a_reception_never_issues_the_product_prefix(self):
        from store import unit_codes

        self.legacy_product_prefix_U()
        phone = self.product('Teléfono', serialized=True)
        unit = self.receive(phone, {'serial_number': 'SNPF0001', 'imei': IMEI_A})[0]
        code = StockUnit.objects.get(pk=unit.pk).label_code
        self.assertTrue(code.startswith(unit_codes.DEFAULT_FALLBACK_PREFIX), code)
        self.assertRegex(code, r'^[A-Z]\d{10}$')

    def test_a_stored_unit_prefix_equal_to_the_product_prefix_refuses_the_reception(self):
        from store.models import CompanyLabelSettings

        CompanyLabelSettings.objects.create(company=self.company, unit_code_prefix='EQ')
        CompanyLabelSettings.objects.filter(company=self.company).update(unit_code_prefix='P')   # bypassing clean()
        phone = self.product('Teléfono', serialized=True)
        with self.assertRaises(units_service.StockUnitError):
            self.receive(phone, {'serial_number': 'SNPF0002', 'imei': IMEI_A})
        self.assertEqual(StockUnit.objects.filter(product=phone).count(), 0)

    def test_the_product_prefix_cannot_become_the_unit_prefix(self):
        from django.core.exceptions import ValidationError

        from store import unit_codes

        with self.assertRaises(ValidationError):                     # no settings row: units use `U`
            self.product_prefix('U')
        unit_codes.update_settings(self.company, {'unit_code_prefix': 'EQ'}, actor=self.clerk)
        with self.assertRaises(ValidationError):
            self.product_prefix('EQ')
        self.assertEqual(self.product_prefix('U').prefix, 'U')      # free again once units use EQ
        self.assertEqual(self.product_prefix('EQ', company=self.other).prefix, 'EQ')   # another company


class SerialAgainstLabelCodeTest(Fixture):
    def coded_device(self):
        phone = self.product('Teléfono', serialized=True)
        unit = self.receive(phone, {'serial_number': 'SNLB0001', 'imei': IMEI_A})[0]
        return StockUnit.objects.get(pk=unit.pk).label_code

    def test_a_serial_equal_to_another_devices_internal_code_is_refused(self):
        code = self.coded_device()
        part = self.product('Repuesto', serialized=True, requires_imei=False)
        for serial in (code, code.lower()):
            with self.subTest(serial=serial), self.assertRaises(units_service.StockUnitError) as refused:
                self.receive(part, {'serial_number': serial})
            error = refused.exception
            self.assertEqual((error.status, error.code, error.field), (409, 'label_code_conflict', 'serial_number'))
        with self.assertRaises(units_service.StockUnitError):
            units_service.clean_unit(part, {'serial_number': code})
        self.assertEqual(StockUnit.objects.filter(product=part).count(), 0)

    def test_audit_code_collisions_also_lists_a_serial_that_is_another_devices_code(self):
        import io

        from django.core.management import call_command

        code = self.coded_device()
        part = self.product('Repuesto', serialized=True, requires_imei=False)
        other = self.receive(part, {'serial_number': 'SNLB0002'})[0]
        StockUnit.objects.filter(pk=other.pk).update(serial_number=code)          # pre-LABEL data
        out = io.StringIO()
        call_command('audit_code_collisions', '--company', self.company.slug, stdout=out)
        self.assertIn(f'unit_id={other.pk} campo=serial_number', out.getvalue())
        self.assertNotIn(code, out.getvalue())

    def test_the_till_says_it_also_reads_the_internal_code(self):
        res = client_for(self.clerk).get(POS_UNIT_LOOKUP, {'code': 'NOEXISTE1', 'branch': self.branch.pk})
        self.assertEqual(res.status_code, 404)
        self.assertIn('código interno', res.json()['detail'])


class SensitiveAuditGuardsTest(Fixture):
    def test_only_this_companys_devices_and_a_known_purpose(self):
        from store import unit_codes
        from store.models import AdminAuditLog

        phone = self.product('Teléfono', serialized=True)
        mine = self.receive(phone, {'serial_number': 'SNAU0001', 'imei': IMEI_A})[0]
        foreign_phone = self.product('Ajeno', company=self.other, serialized=True)
        foreign = self.receive(foreign_phone, {'serial_number': 'SNAU0002', 'imei': IMEI_B},
                               branch=self.other.default_inventory_branch)[0]
        for unit_ids, purpose in (([mine.pk, foreign.pk], 'device_commercial'), ([mine.pk], 'lo que sea'),
                                  ([], 'device_technical')):
            with self.subTest(unit_ids=unit_ids, purpose=purpose), self.assertRaises(ValueError):
                unit_codes.audit_sensitive_print(actor=self.clerk, company=self.company, unit_ids=unit_ids,
                                                 purpose=purpose)
        self.assertFalse(AdminAuditLog.objects.filter(action='label_sensitive_printed').exists())
        line = unit_codes.audit_sensitive_print(actor=self.clerk, company=self.company, unit_ids=[mine.pk],
                                                purpose='device_technical')
        self.assertEqual(line.metadata['purpose'], 'device_technical')
