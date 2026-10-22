"""
INV-SWITCH-01 — the inventory AI switch. AI is used only when ALL of these
hold: the platform allows it (`INVENTORY_AI_ENABLED`), the company turned it
on, the person asked for it in this import, they may run the import, and a
provider is configured, up and within quota. Any one missing: no AI, and the
manual and local paths keep working. In this phase no provider exists, so no
call to anyone is ever made.
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store import inventory_ai
from store.models import AdminAuditLog, InventoryAISettings
from store.test_supplier_weekly_import import WeeklyFile
from store.tests import _p2d_branch, _p2d_member, _saas_company

URL = '/api/admin/inventory/ai-settings/'


class _FakeProvider:
    """A provider for the tests only: configured, with the health and quota the test sets."""

    def __init__(self, *, available=True, quota=True):
        self._available, self._quota = available, quota

    def is_available(self):
        return self._available

    def has_quota(self, company, user):
        return self._quota


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _saas_company('Empresa A', 'empresa-a-ai', tax_id='20000000071')
        self.branch = _p2d_branch(self.company, 'Centro')
        self.admin, _ = _p2d_member(self.company, 'ai_admin', ['company.view', 'settings.view', 'settings.manage'])
        self.viewer, _ = _p2d_member(self.company, 'ai_viewer', ['company.view', 'settings.view'])
        self.other = _saas_company('Empresa B', 'empresa-b-ai', tax_id='20000000072')
        self.outsider, _ = _p2d_member(self.other, 'ai_outsider', ['company.view', 'settings.view', 'settings.manage'])

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def turn_on(self, company=None):
        InventoryAISettings.objects.update_or_create(company=company or self.company, defaults={'enabled': True})

    def decide(self, *, requested=True, permitted=True):
        return inventory_ai.decide(self.company, self.admin, requested=requested, permitted=permitted)


class DecisionTest(_Base):
    """Each condition alone says no, with its own reason; all of them together say yes."""

    @override_settings(INVENTORY_AI_ENABLED=False)
    def test_the_platform_switch_is_off_by_default_and_says_no(self):
        self.turn_on()
        with mock.patch.object(inventory_ai, 'get_provider', return_value=_FakeProvider()):
            self.assertEqual(self.decide(), inventory_ai.Decision(False, 'global_off'))

    def test_the_setting_defaults_to_off(self):
        from django.conf import settings

        self.assertIs(settings.INVENTORY_AI_ENABLED, False)

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_a_company_that_did_not_turn_it_on_gets_no_ai(self):
        with mock.patch.object(inventory_ai, 'get_provider', return_value=_FakeProvider()):
            self.assertEqual(self.decide(), inventory_ai.Decision(False, 'company_off'))

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_an_import_that_did_not_ask_for_it_gets_no_ai(self):
        self.turn_on()
        with mock.patch.object(inventory_ai, 'get_provider', return_value=_FakeProvider()):
            self.assertEqual(self.decide(requested=False), inventory_ai.Decision(False, 'not_requested'))

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_someone_who_may_not_run_the_import_gets_no_ai(self):
        self.turn_on()
        with mock.patch.object(inventory_ai, 'get_provider', return_value=_FakeProvider()):
            self.assertEqual(self.decide(permitted=False), inventory_ai.Decision(False, 'forbidden'))

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_without_a_provider_it_is_not_configured(self):
        self.turn_on()
        self.assertIsNone(inventory_ai.get_provider())
        self.assertEqual(self.decide(), inventory_ai.Decision(False, 'not_configured'))

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_a_provider_that_is_down_is_unavailable(self):
        self.turn_on()
        with mock.patch.object(inventory_ai, 'get_provider', return_value=_FakeProvider(available=False)):
            self.assertEqual(self.decide(), inventory_ai.Decision(False, 'unavailable'))

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_a_spent_quota_says_no(self):
        self.turn_on()
        with mock.patch.object(inventory_ai, 'get_provider', return_value=_FakeProvider(quota=False)):
            self.assertEqual(self.decide(), inventory_ai.Decision(False, 'quota_exhausted'))

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_all_of_them_together_say_yes(self):
        self.turn_on()
        with mock.patch.object(inventory_ai, 'get_provider', return_value=_FakeProvider()):
            self.assertEqual(self.decide(), inventory_ai.Decision(True, None))

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_another_company_turning_it_on_changes_nothing_here(self):
        self.turn_on(self.other)
        with mock.patch.object(inventory_ai, 'get_provider', return_value=_FakeProvider()):
            self.assertEqual(self.decide(), inventory_ai.Decision(False, 'company_off'))

    def test_no_provider_is_built_in_this_phase(self):
        with override_settings(INVENTORY_AI_ENABLED=True, INVENTORY_AI_PROVIDER='anything'):
            self.assertIsNone(inventory_ai.get_provider())


class SettingsEndpointTest(_Base):
    def test_reading_says_off_and_who_may_change_it(self):
        body = self.client_for(self.viewer).get(URL, {'company': self.company.pk}).json()
        self.assertEqual((body['enabled'], body['global_enabled'], body['state'], body['can_manage']),
                         (False, False, 'disabled', False))
        self.assertNotIn('key', ' '.join(body))

    def test_reading_writes_nothing(self):
        self.client_for(self.viewer).get(URL, {'company': self.company.pk})
        self.assertFalse(InventoryAISettings.objects.exists())

    def test_an_administrator_turns_it_on_and_it_is_audited(self):
        response = self.client_for(self.admin).patch(f'{URL}?company={self.company.pk}', {'enabled': True},
                                                     format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()['enabled'], response.json()['state']), (True, 'not_configured'))
        log = AdminAuditLog.objects.get(action='inventory_ai_setting_changed')
        self.assertEqual((log.metadata, log.company_id), ({'enabled': True, 'previous': False}, self.company.pk))

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_on_with_a_working_provider_it_is_enabled(self):
        self.turn_on()
        with mock.patch.object(inventory_ai, 'get_provider', return_value=_FakeProvider()):
            body = self.client_for(self.viewer).get(URL, {'company': self.company.pk}).json()
        self.assertEqual((body['state'], body['global_enabled']), ('enabled', True))

    def test_only_a_literal_boolean_changes_it(self):
        for value in ('true', 1, None, 'sí'):
            with self.subTest(value=value):
                response = self.client_for(self.admin).patch(
                    f'{URL}?company={self.company.pk}', {'enabled': value}, format='json')
                self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(InventoryAISettings.objects.exists())

    def test_without_settings_manage_it_is_403(self):
        response = self.client_for(self.viewer).patch(f'{URL}?company={self.company.pk}', {'enabled': True},
                                                      format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(InventoryAISettings.objects.exists())

    def test_another_company_cannot_read_or_change_it(self):
        client = self.client_for(self.outsider)
        self.assertEqual(client.get(URL, {'company': self.company.pk}).status_code, 403)
        self.assertEqual(client.patch(f'{URL}?company={self.company.pk}', {'enabled': True},
                                      format='json').status_code, 403)
        self.assertFalse(InventoryAISettings.objects.filter(company=self.company).exists())

    def test_a_branch_limited_administrator_cannot_change_a_company_wide_switch(self):
        limited, _ = _p2d_member(self.company, 'ai_limited', ['company.view', 'settings.view', 'settings.manage'],
                                 branches=[self.branch])
        response = self.client_for(limited).patch(f'{URL}?company={self.company.pk}', {'enabled': True},
                                                  format='json')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_401(self):
        self.assertEqual(APIClient().get(URL, {'company': self.company.pk}).status_code, 401)


class ImportPreviewTest(WeeklyFile, TestCase):
    """The weekly supplier file says what it decided about AI, and never calls anyone in this phase."""

    def setUp(self):
        cache.clear()
        self.build_week('ai-switch-tienda')

    def as_(self, name, perms, branches=False):
        user, _ = _p2d_member(self.company, name, perms,
                              branches=[self.company.branches.order_by('pk').first()] if branches else None)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def preview_with(self, **data):
        return self.client_.post('/api/admin/supplier-offers/import/preview/',
                                 {'file': self.file([self.same()]), **data}, format='multipart')

    def test_without_asking_the_preview_says_ai_was_not_requested(self):
        body = self.preview_with().json()
        self.assertEqual(body['ai'], {'requested': False, 'allowed': False, 'reason': 'not_requested', 'used': False})

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_asking_with_nothing_configured_says_so_and_goes_on_without_it(self):
        InventoryAISettings.objects.create(company=self.company, enabled=True)
        response = self.preview_with(use_ai='true')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['ai'],
                         {'requested': True, 'allowed': False, 'reason': 'not_configured', 'used': False})
        self.assertEqual(len(response.json()['rows']), 1)

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_a_branch_limited_buyer_may_preview_but_not_with_ai(self):
        # Review P2: the switch is the whole company's, so is using it.
        InventoryAISettings.objects.create(company=self.company, enabled=True)
        limited = self.as_('ai-limited', ['company.view', 'products.view', 'products.manage'], branches=True)
        response = limited.post('/api/admin/supplier-offers/import/preview/',
                                {'file': self.file([self.same()]), 'use_ai': 'true'}, format='multipart')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['ai']['reason'], 'forbidden')

    @override_settings(INVENTORY_AI_ENABLED=True)
    def test_only_a_yes_asks_for_it(self):
        InventoryAISettings.objects.create(company=self.company, enabled=True)
        for value in ('false', 'no', 'yes', '0', ''):
            with self.subTest(value=value):
                self.assertEqual(self.preview_with(use_ai=value).json()['ai']['requested'], False)
