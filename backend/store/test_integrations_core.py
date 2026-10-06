"""
INTEGRATIONS-CONSOLE · the secret store, the provider registry and the console API.

    The panel configures.  The secret store protects.  The registry decides.
    The adapters talk to third parties.  The domain names no vendor.

What this module pins, with a provider invented for the purpose so that nothing
here depends on a real vendor:

  · a secret is stored sealed, and the sealed text is useless on another row;
  · the API never returns a secret — not on read, not on write, not in an error;
  · only a platform master reaches any of it, and the others learn nothing;
  · a draft does not replace what is running until it has been tested and
    activated, and two masters cannot overwrite each other in silence;
  · the audit trail says who did what and never with what value.
"""
import json
import logging
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store import integrations
from store.integrations import registry, secret_store, service
from store.integrations.registry import Field, Provider, TestOutcome
from store.models import AdminAuditLog, IntegrationConfig
from store.tests import _p3_company

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
SECRET = 'valor-secreto-NoEsReal-7f3a9c'
SECOND_SECRET = 'otro-secreto-NoEsReal-22b1d0'


class _Probe(Provider):
    """A provider that exists only in these tests."""

    id = 'probe'
    label = 'Sonda'
    category = 'testing'
    scope = registry.SCOPE_PLATFORM
    fields = (
        Field('endpoint', 'Dirección', required=True),
        Field('mode', 'Modo', kind='choice', choices=(('test', 'Pruebas'), ('live', 'Producción')), default='test'),
        Field('token', 'Token', secret=True, required=True),
        Field('extra', 'Clave extra', secret=True),
    )
    outcome = TestOutcome(True, 'ok', 'Correcto.')
    seen = []

    def clean(self, public, secrets):
        if public.get('endpoint') and not public['endpoint'].startswith('https://'):
            raise registry.ConfigError({'endpoint': 'La dirección tiene que ser https.'})

    def test(self, config, **options):
        type(self).seen.append(dict(config.secrets))
        return type(self).outcome


class _CompanyProbe(_Probe):
    id = 'probe_company'
    label = 'Sonda por empresa'
    scope = registry.SCOPE_COMPANY


@override_settings(APP_CONFIG_ENCRYPTION_KEY=KEY, APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS='')
class _Base(TestCase):
    def setUp(self):
        self.addCleanup(registry.unregister, _Probe.id)
        self.addCleanup(registry.unregister, _CompanyProbe.id)
        registry.register(_Probe())
        registry.register(_CompanyProbe())
        _Probe.outcome = TestOutcome(True, 'ok', 'Correcto.')
        _Probe.seen = []
        User = get_user_model()
        self.master = User.objects.create_superuser('master', 'master@example.com', 'x')
        self.other_master = User.objects.create_superuser('master2', 'master2@example.com', 'x')
        self.company = _p3_company('integ-a', 'Empresa A')
        self.other_company = _p3_company('integ-b', 'Empresa B')
        self.client = APIClient()
        self.client.force_authenticate(self.master)

    def url(self, provider='probe', action='', company=None):
        base = f'/api/admin/integrations/{provider}/' + (f'{action}/' if action else '')
        return base + (f'?company={company.pk}' if company else '')

    def save(self, public=None, secrets=None, version=None, provider='probe', company=None, client=None):
        body = {'public': public if public is not None else {'endpoint': 'https://api.example.invalid'},
                'secrets': secrets if secrets is not None else {'token': SECRET}}
        if version is not None:
            body['version'] = version
        return (client or self.client).put(self.url(provider, 'draft', company), body, format='json')

    def detail(self, provider='probe', company=None, client=None):
        return (client or self.client).get(self.url(provider, company=company))


class SecretStoreTest(_Base):
    def test_what_is_stored_is_not_what_was_typed(self):
        self.assertEqual(self.save().status_code, 200)
        row = IntegrationConfig.objects.get(provider='probe', slot='draft')
        self.assertNotIn(SECRET, row.sealed_secrets)
        self.assertNotIn(SECRET, json.dumps(row.public))
        self.assertNotIn(SECRET, json.dumps(row.secret_meta))
        self.assertEqual(service.open_secrets(row), {'token': SECRET})

    def test_a_sealed_value_only_opens_on_the_row_it_was_sealed_for(self):
        """Copying one row's sealed text into another row must not hand over its secrets."""
        self.save()
        self.save(provider='probe_company', company=self.company)
        mine = IntegrationConfig.objects.get(provider='probe', slot='draft')
        theirs = IntegrationConfig.objects.get(provider='probe_company', slot='draft')
        theirs.sealed_secrets = mine.sealed_secrets
        with self.assertRaises(secret_store.SecretStoreError):
            service.open_secrets(theirs)

    def test_a_tampered_value_does_not_open(self):
        self.save()
        row = IntegrationConfig.objects.get(provider='probe', slot='draft')
        row.sealed_secrets = row.sealed_secrets[:-6] + 'AAAAAA'
        with self.assertRaises(secret_store.SecretStoreError):
            service.open_secrets(row)

    def test_another_root_key_cannot_open_it(self):
        self.save()
        row = IntegrationConfig.objects.get(provider='probe', slot='draft')
        with override_settings(APP_CONFIG_ENCRYPTION_KEY=OTHER_KEY):
            with self.assertRaises(secret_store.SecretStoreError):
                service.open_secrets(row)

    def test_the_root_key_can_be_rotated_without_losing_what_was_stored(self):
        self.save()
        row = IntegrationConfig.objects.get(provider='probe', slot='draft')
        with override_settings(APP_CONFIG_ENCRYPTION_KEY=OTHER_KEY, APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS=KEY):
            self.assertEqual(service.open_secrets(row), {'token': SECRET})
            self.assertEqual(service.reseal_all(), 1)
        row.refresh_from_db()
        with override_settings(APP_CONFIG_ENCRYPTION_KEY=OTHER_KEY, APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS=''):
            self.assertEqual(service.open_secrets(row), {'token': SECRET})

    @override_settings(APP_CONFIG_ENCRYPTION_KEY='', DEBUG=False)
    def test_without_a_root_key_production_stores_nothing_and_says_so(self):
        response = self.save()
        self.assertEqual(response.status_code, 503)
        self.assertIn('APP_CONFIG_ENCRYPTION_KEY', response.json()['detail'])
        self.assertFalse(IntegrationConfig.objects.exists())

    @override_settings(APP_CONFIG_ENCRYPTION_KEY='no-es-una-clave')
    def test_a_malformed_root_key_is_an_unavailable_store_not_a_crash(self):
        self.assertEqual(self.save().status_code, 503)

    def test_the_key_is_never_in_the_database(self):
        self.save()
        dump = json.dumps(list(IntegrationConfig.objects.values()), default=str)
        self.assertNotIn(KEY, dump)


class WriteOnlyApiTest(_Base):
    def assertNoSecret(self, response):
        text = response.content.decode()
        self.assertNotIn(SECRET, text)
        self.assertNotIn(SECOND_SECRET, text)

    def test_no_response_ever_carries_a_secret(self):
        saved = self.save(secrets={'token': SECRET, 'extra': SECOND_SECRET})
        self.assertNoSecret(saved)
        self.assertNoSecret(self.detail())
        self.assertNoSecret(self.client.get('/api/admin/integrations/'))
        self.assertNoSecret(self.client.post(self.url(action='test'), {}, format='json'))
        self.assertNoSecret(self.client.post(self.url(action='activate'), {'version': 2}, format='json'))
        self.assertNoSecret(self.detail())

    def test_a_secret_is_described_not_shown(self):
        self.save()
        token = self.detail().json()['draft']['secrets']['token']
        self.assertEqual(token['configured'], True)
        self.assertEqual(token['last_four'], SECRET[-4:])
        self.assertEqual(token['updated_by'], 'master')
        self.assertIn('updated_at', token)
        self.assertEqual(self.detail().json()['draft']['secrets']['extra'], {'configured': False})

    def test_a_short_secret_shows_none_of_itself(self):
        self.save(secrets={'token': 'abcd12'})
        self.assertEqual(self.detail().json()['draft']['secrets']['token']['last_four'], '')

    def test_saving_without_a_secret_keeps_the_one_already_stored(self):
        self.save()
        version = self.detail().json()['draft']['version']
        again = self.save(public={'endpoint': 'https://otra.example.invalid'}, secrets={}, version=version)
        self.assertEqual(again.status_code, 200, again.content)
        row = IntegrationConfig.objects.get(provider='probe', slot='draft')
        self.assertEqual(service.open_secrets(row), {'token': SECRET})
        self.assertEqual(row.public['endpoint'], 'https://otra.example.invalid')

    def test_a_secret_is_replaced_by_typing_a_new_one_and_revoked_with_null(self):
        self.save(secrets={'token': SECRET, 'extra': SECOND_SECRET})
        version = self.detail().json()['draft']['version']
        self.save(secrets={'token': 'nuevo-valor-NoEsReal-9911', 'extra': None}, version=version)
        row = IntegrationConfig.objects.get(provider='probe', slot='draft')
        self.assertEqual(service.open_secrets(row), {'token': 'nuevo-valor-NoEsReal-9911'})
        self.assertEqual(self.detail().json()['draft']['secrets']['extra'], {'configured': False})

    def test_only_the_fields_the_provider_declares_are_accepted(self):
        response = self.save(public={'endpoint': 'https://api.example.invalid', 'inventado': 'x'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('inventado', response.json()['errors'])
        response = self.save(secrets={'token': SECRET, 'otro': 'x'})
        self.assertEqual(response.status_code, 400)
        # A secret sent where a public value goes is refused, not stored in the clear.
        response = self.save(public={'endpoint': 'https://api.example.invalid', 'token': SECRET})
        self.assertEqual(response.status_code, 400)
        self.assertNoSecret(response)

    def test_a_validation_error_names_the_field_and_repeats_no_value(self):
        response = self.save(public={'endpoint': 'http://inseguro.example.invalid'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('endpoint', response.json()['errors'])
        self.assertNoSecret(response)
        response = self.save(public={'endpoint': 'https://api.example.invalid', 'mode': 'otro'})
        self.assertIn('mode', response.json()['errors'])

    def test_an_unknown_provider_does_not_exist(self):
        self.assertEqual(self.client.get('/api/admin/integrations/no-existe/').status_code, 404)

    def test_the_log_holds_no_secret(self):
        with self.assertLogs(level=logging.DEBUG) as captured:
            logging.getLogger('store.integrations').debug('inicio')
            self.save(secrets={'token': SECRET, 'extra': SECOND_SECRET})
            _Probe.outcome = TestOutcome(False, 'auth_failed', 'El proveedor rechazó las credenciales.')
            self.client.post(self.url(action='test'), {}, format='json')
        self.assertNotIn(SECRET, '\n'.join(captured.output))
        self.assertNotIn(SECOND_SECRET, '\n'.join(captured.output))


class MasterOnlyTest(_Base):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        from store.models import CompanyRole
        from store.tests import _p2d_member

        # The most a company can give: every capability of its Administrador role.
        everything = CompanyRole.objects.get(company=self.company, slug='administrador').capabilities
        self.admin, _ = _p2d_member(self.company, 'admin_a', everything)
        self.technician, _ = _p2d_member(self.company, 'tecnico_a', ['service.orders.view'])
        self.staff_flag = User.objects.create_user('staff_flag', 'staff@example.com', 'x', is_staff=True)
        self.customer = User.objects.create_user('cliente', 'cliente@example.com', 'x')
        self.save()
        self.save(provider='probe_company', company=self.company)

    ROUTES = (
        ('get', '/api/admin/integrations/', None),
        ('get', '/api/admin/integrations/probe/', None),
        ('put', '/api/admin/integrations/probe/draft/', {'public': {}, 'secrets': {}}),
        ('post', '/api/admin/integrations/probe/test/', {}),
        ('post', '/api/admin/integrations/probe/activate/', {'version': 1}),
        ('post', '/api/admin/integrations/probe/disable/', {}),
        ('post', '/api/admin/integrations/probe/enable/', {}),
        ('post', '/api/admin/integrations/probe/revoke/', {'confirm': 'REVOCAR'}),
        ('post', '/api/admin/integrations/probe/import-env/', {}),
    )

    def _call(self, client, method, path, body):
        return getattr(client, method)(path, body, format='json') if body is not None else getattr(client, method)(path)

    def test_nobody_but_a_master_reaches_any_of_it(self):
        for user in (self.admin, self.technician, self.staff_flag, self.customer):
            client = APIClient()
            client.force_authenticate(user)
            for method, path, body in self.ROUTES:
                response = self._call(client, method, path, body)
                self.assertEqual(response.status_code, 403, f'{user.username} {method} {path}')
                self.assertNotIn(SECRET[-4:], response.content.decode())

    def test_without_a_session_there_is_nothing(self):
        for method, path, body in self.ROUTES:
            self.assertEqual(self._call(APIClient(), method, path, body).status_code, 401, path)

    def test_the_refusal_says_the_same_whether_or_not_something_is_configured(self):
        """No oracle: a refused caller cannot tell a configured integration from an unknown one."""
        client = APIClient()
        client.force_authenticate(self.admin)
        configured = client.get('/api/admin/integrations/probe/')
        unknown = client.get('/api/admin/integrations/no-existe/')
        self.assertEqual((configured.status_code, configured.json()), (unknown.status_code, unknown.json()))

    def test_a_company_administrator_cannot_reach_their_own_companys_integration(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        self.assertEqual(client.get(self.url('probe_company', company=self.company)).status_code, 403)
        self.assertEqual(self.save(provider='probe_company', company=self.company, client=client).status_code, 403)

    def test_nothing_changed_while_they_were_trying(self):
        before = list(IntegrationConfig.objects.values_list('pk', 'version', 'sealed_secrets'))
        self.test_nobody_but_a_master_reaches_any_of_it()
        self.assertEqual(list(IntegrationConfig.objects.values_list('pk', 'version', 'sealed_secrets')), before)


class ScopeTest(_Base):
    def test_a_company_integration_needs_its_company_and_keeps_to_it(self):
        self.assertEqual(self.save(provider='probe_company').status_code, 400)
        self.assertEqual(self.save(provider='probe_company', company=self.company).status_code, 200)
        other = self.detail('probe_company', company=self.other_company).json()
        self.assertIsNone(other['draft'])
        self.assertEqual(other['state'], 'NOT_CONFIGURED')
        mine = self.detail('probe_company', company=self.company).json()
        self.assertEqual(mine['draft']['secrets']['token']['last_four'], SECRET[-4:])
        self.assertEqual(mine['company'], {'id': self.company.pk, 'name': 'Empresa A', 'slug': 'integ-a'})

    def test_one_companys_configuration_is_never_resolved_for_another(self):
        self.save(provider='probe_company', company=self.company)
        self.client.post(self.url('probe_company', 'test', self.company), {}, format='json')
        self.client.post(self.url('probe_company', 'activate', self.company), {'version': 2}, format='json')
        self.assertEqual(integrations.resolve('probe_company', company=self.company).secrets, {'token': SECRET})
        self.assertIsNone(integrations.resolve('probe_company', company=self.other_company))

    def test_a_platform_integration_takes_no_company(self):
        self.assertEqual(self.save(company=self.company).status_code, 400)

    def test_an_unknown_company_is_not_found(self):
        response = self.client.get('/api/admin/integrations/probe_company/?company=999999')
        self.assertEqual(response.status_code, 404)


class LifecycleTest(_Base):
    def state(self):
        return self.detail().json()['state']

    def activate(self, **extra):
        version = self.detail().json()['draft']['version']
        return self.client.post(self.url(action='activate'), {'version': version, **extra}, format='json')

    def test_the_states_follow_what_was_done(self):
        self.assertEqual(self.state(), 'NOT_CONFIGURED')
        self.save()
        self.assertEqual(self.state(), 'CONFIGURED')
        self.assertEqual(self.client.post(self.url(action='test'), {}, format='json').json()['status'], 'ok')
        self.assertEqual(self.state(), 'VALIDATED')
        self.assertEqual(self.activate().status_code, 200)
        self.assertEqual(self.state(), 'ACTIVE')
        self.client.post(self.url(action='disable'), {}, format='json')
        self.assertEqual(self.state(), 'DISABLED')
        self.client.post(self.url(action='enable'), {}, format='json')
        self.assertEqual(self.state(), 'ACTIVE')
        self.client.post(self.url(action='revoke'), {'confirm': 'REVOCAR'}, format='json')
        self.assertEqual(self.state(), 'NOT_CONFIGURED')
        self.assertFalse(IntegrationConfig.objects.filter(provider='probe').exists())

    def test_a_draft_is_not_what_runs(self):
        self.save()
        self.assertIsNone(integrations.resolve('probe'))
        self.client.post(self.url(action='test'), {}, format='json')
        self.assertIsNone(integrations.resolve('probe'))
        self.activate()
        resolved = integrations.resolve('probe')
        self.assertEqual(resolved.secrets, {'token': SECRET})
        self.assertEqual(resolved.public['endpoint'], 'https://api.example.invalid')
        self.assertEqual(resolved.source, 'panel')

    def test_a_draft_that_was_not_tested_is_not_activated(self):
        self.save()
        response = self.activate()
        self.assertEqual(response.status_code, 409)
        self.assertIn('prueba', response.json()['detail'].lower())
        self.assertIsNone(integrations.resolve('probe'))

    def test_a_draft_that_failed_its_test_does_not_replace_what_is_running(self):
        self.save()
        self.client.post(self.url(action='test'), {}, format='json')
        self.activate()
        # A second master types a wrong token.
        self.save(secrets={'token': 'token-equivocado-NoEsReal'}, version=None)
        _Probe.outcome = TestOutcome(False, 'auth_failed', 'El proveedor rechazó las credenciales.')
        tested = self.client.post(self.url(action='test'), {}, format='json').json()
        self.assertEqual(tested['status'], 'auth_failed')
        self.assertEqual(self.activate().status_code, 409)
        self.assertEqual(integrations.resolve('probe').secrets, {'token': SECRET})
        self.assertEqual(self.state(), 'ACTIVE')
        self.assertEqual(self.detail().json()['draft']['last_test_status'], 'auth_failed')

    def test_changing_a_tested_draft_asks_for_the_test_again(self):
        self.save()
        self.client.post(self.url(action='test'), {}, format='json')
        version = self.detail().json()['draft']['version']
        self.save(public={'endpoint': 'https://otra.example.invalid'}, secrets={}, version=version)
        self.assertEqual(self.state(), 'CONFIGURED')
        self.assertEqual(self.activate().status_code, 409)

    def test_the_test_runs_on_what_was_stored_not_on_what_the_browser_sends(self):
        self.save()
        self.client.post(self.url(action='test'), {'secrets': {'token': 'inyectado'}}, format='json')
        self.assertEqual(_Probe.seen, [{'token': SECRET}])

    def test_activating_changes_what_the_running_system_resolves_at_once(self):
        self.save()
        self.client.post(self.url(action='test'), {}, format='json')
        self.activate()
        self.assertEqual(integrations.resolve('probe').secrets['token'], SECRET)
        self.save(secrets={'token': SECOND_SECRET})
        self.client.post(self.url(action='test'), {}, format='json')
        self.activate()
        self.assertEqual(integrations.resolve('probe').secrets['token'], SECOND_SECRET)

    def test_a_disabled_integration_resolves_to_nothing(self):
        self.save()
        self.client.post(self.url(action='test'), {}, format='json')
        self.activate()
        self.client.post(self.url(action='disable'), {}, format='json')
        self.assertIsNone(integrations.resolve('probe'))

    def test_revoking_asks_for_the_word(self):
        self.save()
        response = self.client.post(self.url(action='revoke'), {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(IntegrationConfig.objects.filter(provider='probe').exists())

    def test_a_required_secret_missing_is_incomplete_not_valid(self):
        self.save(secrets={})
        tested = self.client.post(self.url(action='test'), {}, format='json').json()
        self.assertEqual(tested['status'], 'incomplete')
        self.assertEqual(_Probe.seen, [])


class ConcurrencyTest(_Base):
    def test_two_masters_do_not_overwrite_each_other_in_silence(self):
        self.save()
        version = self.detail().json()['draft']['version']
        second = APIClient()
        second.force_authenticate(self.other_master)
        first_write = self.save(public={'endpoint': 'https://uno.example.invalid'}, secrets={}, version=version)
        self.assertEqual(first_write.status_code, 200)
        stale = self.save(public={'endpoint': 'https://dos.example.invalid'}, secrets={}, version=version, client=second)
        self.assertEqual(stale.status_code, 409)
        self.assertIn('version', stale.json())
        row = IntegrationConfig.objects.get(provider='probe', slot='draft')
        self.assertEqual(row.public['endpoint'], 'https://uno.example.invalid')

    def test_an_existing_draft_cannot_be_written_without_saying_which_one_was_read(self):
        self.save()
        self.assertEqual(self.save(public={'endpoint': 'https://x.example.invalid'}, secrets={}).status_code, 409)

    def test_activation_names_the_draft_it_read(self):
        self.save()
        self.client.post(self.url(action='test'), {}, format='json')
        response = self.client.post(self.url(action='activate'), {'version': 1}, format='json')
        self.assertEqual(response.status_code, 409)


class AuditTest(_Base):
    def test_every_act_is_recorded_with_its_author_and_without_a_value(self):
        self.save(secrets={'token': SECRET, 'extra': SECOND_SECRET})
        self.client.post(self.url(action='test'), {}, format='json')
        version = self.detail().json()['draft']['version']
        self.client.post(self.url(action='activate'), {'version': version}, format='json')
        self.client.post(self.url(action='disable'), {}, format='json')
        self.client.post(self.url(action='enable'), {}, format='json')
        self.client.post(self.url(action='revoke'), {'confirm': 'REVOCAR'}, format='json')

        rows = list(AdminAuditLog.objects.filter(target_type='integration').order_by('pk'))
        self.assertEqual(
            [row.action for row in rows],
            ['integration_draft_saved', 'integration_tested', 'integration_activated',
             'integration_disabled', 'integration_enabled', 'integration_revoked'],
        )
        for row in rows:
            self.assertEqual(row.actor, self.master)
            self.assertEqual(row.target_id, 'probe')
            dumped = json.dumps(row.metadata)
            self.assertNotIn(SECRET, dumped)
            self.assertNotIn(SECOND_SECRET, dumped)
            self.assertNotIn(SECRET[-4:], dumped)
        self.assertEqual(sorted(rows[0].metadata['secrets_changed']), ['extra', 'token'])
        self.assertEqual(rows[0].metadata['fields_changed'], ['endpoint', 'mode'])
        self.assertEqual(rows[1].metadata['result'], 'ok')

    def test_a_company_integration_is_recorded_under_its_company(self):
        self.save(provider='probe_company', company=self.company)
        row = AdminAuditLog.objects.get(target_type='integration')
        self.assertEqual(row.company, self.company)

    def test_a_refused_attempt_leaves_no_record_to_read(self):
        User = get_user_model()
        client = APIClient()
        client.force_authenticate(User.objects.create_user('nadie', 'n@example.com', 'x'))
        self.save(client=client)
        self.assertFalse(AdminAuditLog.objects.filter(target_type='integration').exists())


class RegistryTest(_Base):
    def test_a_provider_declares_what_its_test_may_be_given(self):
        """The console draws the inputs of a test from the provider, and the API accepts those and no others."""
        class _WithOption(_Probe):
            id = 'probe_option'
            test_fields = (Field('send_to', 'Enviar a', kind='email'),)
            given = []

            def test(self, config, **options):
                type(self).given.append(options)
                return TestOutcome(True, 'ok', 'Correcto.')

        registry.register(_WithOption())
        self.addCleanup(registry.unregister, 'probe_option')
        self.save(provider='probe_option')

        described = self.detail('probe_option').json()
        self.assertEqual([f['name'] for f in described['test_fields']], ['send_to'])
        self.assertEqual(self.detail().json()['test_fields'], [])

        url = self.url('probe_option', 'test')
        self.assertEqual(self.client.post(url, {'send_to': 'ana@example.pe', 'other': 'x'}, format='json').status_code, 200)
        self.assertEqual(_WithOption.given[-1], {'send_to': 'ana@example.pe'})
        refused = self.client.post(url, {'send_to': 'no es un correo'}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('send_to', refused.json()['errors'])
        # A provider that declares none is given none, whatever is posted.
        self.save()
        with mock.patch.object(_Probe, 'test', return_value=TestOutcome(True, 'ok', 'Correcto.')) as plain:
            self.client.post(self.url(action='test'), {'send_to': 'ana@example.pe'}, format='json')
        self.assertEqual(plain.call_args.kwargs, {})

    def test_the_list_says_what_each_integration_is_and_how_it_stands(self):
        self.save()
        listed = {item['id']: item for item in self.client.get('/api/admin/integrations/').json()['results']}
        probe = listed['probe']
        self.assertEqual((probe['label'], probe['category'], probe['scope']), ('Sonda', 'testing', 'platform'))
        self.assertEqual(probe['state'], 'CONFIGURED')
        self.assertIn('smtp', listed)

    def test_the_detail_describes_the_fields_so_a_screen_can_be_drawn_from_it(self):
        fields = {field['name']: field for field in self.detail().json()['fields']}
        self.assertEqual(fields['token']['secret'], True)
        self.assertEqual(fields['endpoint']['required'], True)
        self.assertEqual(fields['mode']['choices'], [{'value': 'test', 'label': 'Pruebas'}, {'value': 'live', 'label': 'Producción'}])
        self.assertNotIn('value', fields['token'])

    def test_two_providers_cannot_take_the_same_id(self):
        with self.assertRaises(ValueError):
            registry.register(_Probe())

    def test_every_registered_provider_declares_what_the_console_needs(self):
        for provider in registry.all_providers():
            self.assertTrue(provider.id and provider.label and provider.category, provider)
            self.assertIn(provider.scope, (registry.SCOPE_PLATFORM, registry.SCOPE_COMPANY))
            names = [field.name for field in provider.fields]
            self.assertEqual(len(names), len(set(names)), provider.id)
            self.assertTrue(callable(provider.test))
