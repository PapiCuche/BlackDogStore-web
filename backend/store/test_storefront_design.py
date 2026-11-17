"""
P2 · F5a — el interruptor de diseño de la tienda, por empresa (`storefront_design`).

V3 es el diseño de todas las tiendas. V4 sólo se ve en la vista previa `/v4`
hasta que el propietario la elige desde el panel. Este interruptor es lo único
que cambia de una a otra, y volver a V3 es elegir V3 en el mismo panel.
"""
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store.models import AdminAuditLog, StorefrontPageSettings
from store.storefront_content_services import public_page_settings
from store.tests import _p2d_member, _p3_company

PAGE = '/api/admin/storefront/page/'
CONFIG = '/api/storefront/config/'


class StorefrontDesignBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('diseno-tienda', 'Tienda Diseño')
        self.other = _p3_company('diseno-otra', 'Otra Tienda')
        self.manager, _ = _p2d_member(self.company, 'diseno_gestor', ['company.view', 'company.manage'])
        self.viewer, _ = _p2d_member(self.company, 'diseno_lector', ['company.view'])
        self.outsider, _ = _p2d_member(self.other, 'diseno_ajeno', ['company.view', 'company.manage'])

    @staticmethod
    def _as(user):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def _patch(self, user, company=None, **data):
        return self._as(user).patch(f'{PAGE}?company={(company or self.company).pk}', data, format='json')

    def _get(self, user, company=None):
        return self._as(user).get(f'{PAGE}?company={(company or self.company).pk}')


class TheDefaultIsV3Test(StorefrontDesignBase):
    def test_the_model_field_defaults_to_v3(self):
        row = StorefrontPageSettings.objects.create(company=self.company)
        self.assertEqual(row.storefront_design, 'v3')

    def test_a_shop_without_a_page_row_reads_v3(self):
        self.assertFalse(StorefrontPageSettings.objects.filter(company=self.company).exists())
        self.assertEqual(public_page_settings(self.company)['storefront_design'], 'v3')
        self.assertEqual(self._get(self.viewer).data['page']['storefront_design'], 'v3')

    def test_saving_other_fields_keeps_v3(self):
        res = self._patch(self.manager, hero_title='Titular')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['page']['storefront_design'], 'v3')

    def test_a_blank_value_falls_back_to_v3(self):
        self._patch(self.manager, storefront_design='v4')
        res = self._patch(self.manager, storefront_design='')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['page']['storefront_design'], 'v3')


class OnlyV3AndV4Test(StorefrontDesignBase):
    def test_v4_is_accepted_and_v3_restores_it(self):
        res = self._patch(self.manager, storefront_design='v4')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['page']['storefront_design'], 'v4')
        self.assertEqual(StorefrontPageSettings.objects.get(company=self.company).storefront_design, 'v4')

        res = self._patch(self.manager, storefront_design='v3')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(StorefrontPageSettings.objects.get(company=self.company).storefront_design, 'v3')

    def test_anything_else_is_refused_and_nothing_changes(self):
        for value in ('v5', 'V4', 'v4 ', 'dark', '<script>', 4):
            with self.subTest(value=value):
                res = self._patch(self.manager, storefront_design=value)
                self.assertEqual(res.status_code, 400, res.data)
                self.assertIn('storefront_design', res.data['errors'])
        self.assertEqual(public_page_settings(self.company)['storefront_design'], 'v3')

    def test_the_choices_are_exactly_v3_and_v4(self):
        field = StorefrontPageSettings._meta.get_field('storefront_design')
        self.assertEqual([value for value, _label in field.choices], ['v3', 'v4'])


class PermissionsTest(StorefrontDesignBase):
    def test_a_viewer_reads_it(self):
        res = self._get(self.viewer)
        self.assertEqual(res.status_code, 200)
        self.assertIn('storefront_design', res.data['page'])

    def test_a_viewer_cannot_change_it(self):
        res = self._patch(self.viewer, storefront_design='v4')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(public_page_settings(self.company)['storefront_design'], 'v3')

    def test_anonymous_cannot_change_it(self):
        res = self._patch(None, storefront_design='v4')
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(public_page_settings(self.company)['storefront_design'], 'v3')

    def test_another_company_cannot_change_it(self):
        res = self._patch(self.outsider, storefront_design='v4')
        self.assertIn(res.status_code, (403, 404))
        self.assertEqual(public_page_settings(self.company)['storefront_design'], 'v3')

    def test_the_switch_belongs_to_one_company(self):
        self._patch(self.manager, storefront_design='v4')
        self.assertEqual(public_page_settings(self.company)['storefront_design'], 'v4')
        self.assertEqual(public_page_settings(self.other)['storefront_design'], 'v3')


class AuditTest(StorefrontDesignBase):
    def _entries(self):
        return AdminAuditLog.objects.filter(company=self.company, action='storefront_page_updated').order_by('pk')

    def test_switching_writes_an_entry_with_before_and_after(self):
        self._patch(self.manager, storefront_design='v4')
        entry = self._entries().last()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.manager)
        self.assertEqual(entry.metadata['storefront_design'], {'from': 'v3', 'to': 'v4'})

        self._patch(self.manager, storefront_design='v3')
        self.assertEqual(self._entries().last().metadata['storefront_design'], {'from': 'v4', 'to': 'v3'})

    def test_saving_without_switching_does_not_claim_a_switch(self):
        self._patch(self.manager, hero_title='Titular')
        entry = self._entries().last()
        self.assertIsNotNone(entry)
        self.assertNotIn('storefront_design', entry.metadata)

    def test_a_refused_value_writes_no_entry(self):
        self._patch(self.manager, storefront_design='v9')
        self.assertFalse(self._entries().exists())


@override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='diseno-tienda')
class PublicPayloadTest(StorefrontDesignBase):
    def _config(self):
        cache.clear()
        res = APIClient().get(CONFIG)
        self.assertEqual(res.status_code, 200)
        return res.json()

    def test_the_public_config_carries_the_value(self):
        self.assertEqual(self._config()['page']['storefront_design'], 'v3')
        self._patch(self.manager, storefront_design='v4')
        self.assertEqual(self._config()['page']['storefront_design'], 'v4')

    def test_only_the_value_travels(self):
        self._patch(self.manager, storefront_design='v4')
        payload = self._config()
        self.assertEqual(payload['page']['storefront_design'], 'v4')
        # Nada más del interruptor: ni quién lo cambió, ni cuándo, ni las opciones.
        mentions = [key for key in _keys(payload) if 'design' in key]
        self.assertEqual(mentions, ['storefront_design'])
        for private in ('updated_by', 'updated_at', 'created_at', 'storefront_design_choices'):
            self.assertNotIn(private, payload['page'])


def _keys(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


class MigrationDbDefaultTest(TestCase):
    """
    0172 runs while the previous release still serves. Its INSERTs into the page table do not name the new
    column, so the column needs a default IN THE DATABASE (`db_default`), not only in Django.
    """

    def test_the_column_has_a_database_default_of_v3(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = 'storefront_design'",
                [StorefrontPageSettings._meta.db_table])
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        default, nullable = row
        self.assertIn("'v3'", default or '')
        self.assertEqual(nullable, 'NO')

    def test_an_insert_that_does_not_name_the_column_reads_v3(self):
        company = _p3_company('diseno-anterior', 'Anterior')
        table = StorefrontPageSettings._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} (company_id, hero_eyebrow, hero_title, hero_subtitle, "
                "new_line_label, used_line_label, hero_primary_cta_label, hero_primary_cta_url, "
                "hero_secondary_cta_label, hero_secondary_cta_url, hero_variant, hero_image_url, "
                "services_image_url, location_image_url, services_hero_title, services_hero_subtitle, "
                "services_warranty_note, created_at, updated_at) "
                "VALUES (%s, '', '', '', '', '', '', '', '', '', 'dark', '', '', '', '', '', '', NOW(), NOW()) "
                "RETURNING id", [company.pk])
            pk = cursor.fetchone()[0]
        self.assertEqual(StorefrontPageSettings.objects.get(pk=pk).storefront_design, 'v3')

    def test_the_migration_is_0172_and_only_adds_the_field(self):
        from importlib import import_module

        from django.db import migrations

        module = import_module('store.migrations.0172_storefront_design')
        operations = module.Migration.operations
        self.assertEqual(module.Migration.dependencies, [('store', '0171_accessory_compatibility')])
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertIsInstance(operation, migrations.AddField)
        self.assertEqual((operation.model_name, operation.name), ('storefrontpagesettings', 'storefront_design'))
        self.assertEqual(operation.field.default, 'v3')
        self.assertEqual(getattr(operation.field.db_default, 'value', operation.field.db_default), 'v3')


class SelectedBranchMemberScopeTest(TestCase):
    """
    WRITE-SCOPE-01 — the shop's design and its home page reach every branch. A member restricted to some
    branches who holds `company.manage` may read them but not change them for the whole company. Modelled
    on `F2SelectedCompanySettingsScopeTest`.
    """

    CAPS = ['company.view', 'company.manage']

    def setUp(self):
        from store.tests import _p2d_branch, _p2d_company

        cache.clear()
        self.company = _p2d_company('f5-wsc')
        self.b1 = _p2d_branch(self.company, 'F5WC B1')
        self.b2 = _p2d_branch(self.company, 'F5WC B2')
        self.selected, _ = _p2d_member(self.company, 'f5wc_selected', self.CAPS, branches=[self.b1])
        self.wide, _ = _p2d_member(self.company, 'f5wc_wide', self.CAPS)
        cache.clear()

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _patch(self, user, **data):
        return self._as(user).patch(f'{PAGE}?company={self.company.pk}', data, format='json')

    def test_a_selected_member_cannot_switch_the_design(self):
        res = self._patch(self.selected, storefront_design='v4')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(public_page_settings(self.company)['storefront_design'], 'v3')
        self.assertFalse(AdminAuditLog.objects.filter(
            company=self.company, action='storefront_page_updated').exists())

    def test_a_selected_member_cannot_edit_the_home_page_either(self):
        res = self._patch(self.selected, hero_title='Sólo mis sucursales')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(public_page_settings(self.company)['hero_title'], '')

    def test_a_selected_member_may_still_read_it(self):
        res = self._as(self.selected).get(f'{PAGE}?company={self.company.pk}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['page']['storefront_design'], 'v3')

    def test_a_company_wide_member_may_switch_it(self):
        res = self._patch(self.wide, storefront_design='v4')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(public_page_settings(self.company)['storefront_design'], 'v4')
