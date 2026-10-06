"""
INTEGRATIONS-CONSOLE · WhatsApp Business, per company.

    a master opens Integraciones › WhatsApp › <empresa>, types that company's
    number and its three credentials, tests them and activates them
    → that company's next notice leaves with THOSE, and its webhook believes
      only what is signed with THAT app secret

The environment-variable references of `configure_whatsapp` keep working for a
company the console has not taken over.

No call to Meta: `urlopen` is simulated, or the provider is `FakeProvider`.
"""
import hashlib
import hmac
import io
import json
import os
import urllib.error
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from store import messaging
from store import whatsapp_services as wa
from store.messaging.fake import FakeProvider
from store.models import CompanyMessagingSettings, IntegrationConfig
from store.test_whatsapp import ENV, TEMPLATES, WhatsAppBase
from store.tests import _p3_company

PUBLIC = {'phone_number_id': '2077700000', 'business_account_id': '3099900000'}
SECRETS = {
    'access_token': 'token-del-panel-NoEsReal', 'app_secret': 'app-secret-del-panel-NoEsReal',
    'verify_token': 'verify-del-panel-NoEsReal',
}
URLOPEN = 'urllib.request.urlopen'


def graph_reply(payload):
    reply = mock.MagicMock()
    reply.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return reply


def graph_error(status, payload):
    return urllib.error.HTTPError('https://graph.facebook.com', status, 'x', {}, io.BytesIO(json.dumps(payload).encode()))


@override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode())
class _Base(WhatsAppBase):
    def setUp(self):
        super().setUp()
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.console = APIClient()
        self.console.force_authenticate(self.master)

    def url(self, action='', company=None):
        company = company or self.company
        return '/api/admin/integrations/whatsapp_cloud/' + (f'{action}/' if action else '') + f'?company={company.pk}'

    def detail(self, company=None):
        return self.console.get(self.url(company=company)).json()

    def save(self, public=PUBLIC, secrets=SECRETS, company=None):
        current = self.detail(company)['draft']
        body = {'public': public, 'secrets': secrets}
        if current:
            body['version'] = current['version']
        return self.console.put(self.url('draft', company), body, format='json')

    def test_connection(self, company=None):
        return self.console.post(self.url('test', company), {}, format='json').json()

    def go_live(self, public=PUBLIC, secrets=SECRETS, company=None):
        self.assertEqual(self.save(public, secrets, company).status_code, 200)
        self.assertEqual(self.test_connection(company)['status'], 'ok')       # FakeProvider: no network
        version = self.detail(company)['draft']['version']
        response = self.console.post(self.url('activate', company), {'version': version}, format='json')
        self.assertEqual(response.status_code, 200, response.content)

    def forget_the_environment(self):
        """A company that never ran `configure_whatsapp`: no number, no references."""
        CompanyMessagingSettings.objects.filter(pk=self.config.pk).update(
            whatsapp_phone_number_id='', whatsapp_access_token_env='', whatsapp_app_secret_env='',
            whatsapp_verify_token_env='')
        self.config.refresh_from_db()


class RuntimeTest(_Base):
    def test_a_company_configured_only_in_the_console_sends_with_its_number(self):
        self.forget_the_environment()
        self.go_live()

        self.new_order()

        [message] = FakeProvider.sent
        self.assertEqual(message['phone_number_id'], '2077700000')
        self.assertEqual(message['to'], '51987654321')

    def test_meta_receives_the_token_and_the_number_of_the_console(self):
        self.go_live()
        with override_settings(WHATSAPP_PROVIDER='cloud_api'), \
                mock.patch(URLOPEN, return_value=graph_reply({'messages': [{'id': 'wamid.PANEL'}]})) as urlopen:
            self.new_order()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'https://graph.facebook.com/v21.0/2077700000/messages')
        self.assertEqual(request.get_header('Authorization'), 'Bearer token-del-panel-NoEsReal')
        self.assertNotEqual(ENV['WHATSAPP_TOKEN_TALLER'], SECRETS['access_token'])

    def test_rotating_the_token_in_the_console_changes_the_next_send_without_a_restart(self):
        self.go_live()
        self.go_live(secrets={**SECRETS, 'access_token': 'token-NUEVO-NoEsReal'})
        with override_settings(WHATSAPP_PROVIDER='cloud_api'), \
                mock.patch(URLOPEN, return_value=graph_reply({'messages': [{'id': 'wamid.NUEVO'}]})) as urlopen:
            self.new_order()
        self.assertEqual(urlopen.call_args.args[0].get_header('Authorization'), 'Bearer token-NUEVO-NoEsReal')

    def test_the_webhook_believes_only_the_app_secret_of_the_console(self):
        self.go_live()
        body = json.dumps({'object': 'whatsapp_business_account', 'entry': []}).encode()

        def post(secret):
            signature = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            return APIClient().post('/api/v1/webhooks/whatsapp/m8-taller/', body, content_type='application/json',
                                    HTTP_X_HUB_SIGNATURE_256=signature)

        self.assertEqual(post(SECRETS['app_secret']).status_code, 200)
        self.assertEqual(post(ENV['WHATSAPP_SECRET_TALLER']).status_code, 403)

    def test_the_subscription_handshake_needs_the_verify_token_of_the_console(self):
        self.go_live()

        def handshake(token):
            return APIClient().get('/api/v1/webhooks/whatsapp/m8-taller/', {
                'hub.mode': 'subscribe', 'hub.verify_token': token, 'hub.challenge': '8675309'})

        self.assertEqual(handshake(SECRETS['verify_token']).content, b'8675309')
        self.assertEqual(handshake(ENV['WHATSAPP_VERIFY_TALLER']).status_code, 403)

    def test_a_company_with_no_settings_row_gets_one_when_the_console_activates(self):
        """Still off: turning the notices on stays the company's own decision."""
        CompanyMessagingSettings.objects.filter(pk=self.config.pk).delete()
        self.go_live()
        config = wa.config_for(self.company)
        self.assertIsNotNone(config)
        self.assertFalse(config.whatsapp_enabled)
        self.assertEqual(wa.readiness(config), (True, []))

    def test_switching_it_off_in_the_console_sends_nothing_and_does_not_fall_back(self):
        self.go_live()
        self.console.post(self.url('disable'), {}, format='json')

        order = self.new_order()

        self.assertEqual(FakeProvider.sent, [])
        self.assertNotEqual(self.delivery(order).status, 'sent')
        self.assertEqual(messaging.credential(wa.config_for(self.company), 'access_token'), '')

    def test_credentials_this_server_cannot_read_send_nothing_and_do_not_fall_back(self):
        self.go_live()
        with override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()), \
                self.assertLogs('store.messaging', level='ERROR'):
            self.new_order()
            self.assertEqual(messaging.credential(wa.config_for(self.company), 'access_token'), '')
        self.assertEqual(FakeProvider.sent, [])

    def test_the_company_s_own_screen_says_ready_and_shows_no_credential(self):
        self.forget_the_environment()
        self.go_live()
        client = self.with_capabilities('settings.view', 'settings.manage', slug='ajustes-wa')
        response = client.get('/api/v1/internal/m8-taller/messaging/whatsapp/')
        body = response.json()
        self.assertTrue(body['ready'], body)
        self.assertTrue(body['phone_number_configured'])
        self.assertEqual(body['credentials'], {'access_token': True, 'app_secret': True, 'verify_token': True})
        for secret in SECRETS.values():
            self.assertNotIn(secret, response.content.decode())

    def test_opt_in_still_decides(self):
        from store.models import Customer

        self.go_live()
        Customer.objects.filter(pk=self.customer.pk).update(whatsapp_opt_in_at=None)
        self.new_order()
        self.assertEqual(FakeProvider.sent, [])


class IsolationTest(_Base):
    def setUp(self):
        super().setUp()
        self.other = _p3_company('integ-wa-b', 'Otra empresa')

    def test_the_console_asks_which_company(self):
        response = self.console.get('/api/admin/integrations/whatsapp_cloud/')
        self.assertEqual(response.status_code, 400)

    def test_one_company_s_credentials_are_not_another_s(self):
        self.go_live()
        other_config = CompanyMessagingSettings.objects.create(company=self.other, whatsapp_enabled=True)

        self.assertEqual(self.detail(self.other)['state'], 'NOT_CONFIGURED')
        self.assertEqual(messaging.credential(other_config, 'access_token'), '')
        self.assertEqual(wa.readiness(other_config)[0], False)
        with self.assertRaises(messaging.NotConfigured):
            messaging.provider_for(other_config)

    def test_each_company_keeps_its_own(self):
        self.go_live()
        self.go_live({'phone_number_id': '4011100000'}, {**SECRETS, 'access_token': 'token-de-la-otra'}, self.other)
        mine, theirs = wa.config_for(self.company), wa.config_for(self.other)
        self.assertEqual(messaging.credential(mine, 'access_token'), SECRETS['access_token'])
        self.assertEqual(messaging.credential(theirs, 'access_token'), 'token-de-la-otra')
        self.assertEqual(messaging.phone_number_id(theirs), '4011100000')
        self.assertEqual(IntegrationConfig.objects.filter(provider='whatsapp_cloud', slot='active').count(), 2)

    def test_a_company_administrator_cannot_reach_the_console(self):
        self.with_capabilities('settings.view', 'settings.manage', slug='ajustes-wa')
        client = APIClient()
        client.force_authenticate(self.membership.user)
        self.assertEqual(client.get(self.url()).status_code, 403)
        self.assertEqual(client.put(self.url('draft'), {'public': PUBLIC, 'secrets': SECRETS}, format='json').status_code, 403)
        self.assertFalse(IntegrationConfig.objects.exists())


@override_settings(WHATSAPP_PROVIDER='cloud_api')
class ConnectionTestTest(_Base):
    def run_test(self, **kwargs):
        self.save()
        with mock.patch(URLOPEN, **kwargs) as urlopen:
            return self.test_connection(), urlopen

    def test_it_asks_meta_about_the_number_and_sends_nobody_anything(self):
        result, urlopen = self.run_test(return_value=graph_reply(
            {'id': '2077700000', 'display_phone_number': '+51 987 000 111', 'verified_name': 'Taller'}))

        self.assertEqual(result['status'], 'ok')
        [call] = urlopen.call_args_list
        request = call.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertTrue(request.full_url.startswith('https://graph.facebook.com/v21.0/2077700000?fields='))
        self.assertNotIn('/messages', request.full_url)
        self.assertEqual(request.get_header('Authorization'), 'Bearer token-del-panel-NoEsReal')
        self.assertNotIn(SECRETS['access_token'], request.full_url)
        self.assertLessEqual(call.kwargs['timeout'], 15)

    def test_a_refused_token_is_an_authentication_failure(self):
        echo = {'error': {'code': 190, 'message': 'Invalid OAuth access token: token-del-panel-NoEsReal'}}
        result, _ = self.run_test(side_effect=graph_error(401, echo))
        self.assertEqual(result['status'], 'auth_failed')
        self.assertNotIn('token-del-panel', json.dumps(result))

    def test_a_number_the_token_cannot_see_is_invalid(self):
        result, _ = self.run_test(side_effect=graph_error(400, {'error': {'code': 100, 'message': 'Unsupported get request'}}))
        self.assertEqual(result['status'], 'invalid')
        result, _ = self.run_test(return_value=graph_reply({'id': '999'}))
        self.assertEqual(result['status'], 'invalid')

    def test_no_answer_from_meta_is_unavailable(self):
        for silence in (urllib.error.URLError('sin red'), TimeoutError('timed out'), graph_error(503, {})):
            with self.subTest(type(silence).__name__):
                result, _ = self.run_test(side_effect=silence)
                self.assertEqual(result['status'], 'unavailable')

    def test_a_failed_test_cannot_be_activated(self):
        self.run_test(side_effect=graph_error(401, {}))
        version = self.detail()['draft']['version']
        self.assertEqual(self.console.post(self.url('activate'), {'version': version}, format='json').status_code, 409)


class ValidationTest(_Base):
    def test_a_number_id_is_digits(self):
        response = self.save({'phone_number_id': '+51 987 654 321'})
        self.assertIn('phone_number_id', response.json()['errors'])

    def test_the_three_credentials_are_required(self):
        self.save(secrets={'access_token': 'sólo-el-token'})
        self.assertEqual(self.test_connection()['status'], 'incomplete')

    def test_with_the_simulated_provider_the_test_calls_nobody(self):
        self.save()
        with mock.patch(URLOPEN) as urlopen:
            result = self.test_connection()
        self.assertEqual(result['status'], 'ok')
        self.assertIn('simulado', result['message'])
        urlopen.assert_not_called()

    def test_no_credential_comes_back(self):
        self.go_live()
        text = self.console.get(self.url()).content.decode()
        for secret in SECRETS.values():
            self.assertNotIn(secret, text)
        self.assertEqual(self.detail()['active']['public']['phone_number_id'], '2077700000')


class LegacyEnvironmentTest(_Base):
    def test_a_company_configured_with_references_keeps_sending_and_shows_as_environment(self):
        self.new_order()
        self.assertEqual(FakeProvider.sent[0]['phone_number_id'], '1055500000')
        detail = self.detail()
        self.assertEqual((detail['state'], detail['source']), ('ACTIVE', 'env'))
        self.assertEqual(sorted(detail['env']['secrets']), ['access_token', 'app_secret', 'verify_token'])
        text = json.dumps(detail)
        for value in (*ENV.values(), *ENV.keys()):
            self.assertNotIn(value, text)

    def test_a_master_moves_the_references_into_the_store_without_seeing_them(self):
        response = self.console.post(self.url('import-env'), {}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        for value in ENV.values():
            self.assertNotIn(value, response.content.decode())
        self.assertEqual(self.test_connection()['status'], 'ok')
        version = self.detail()['draft']['version']
        self.console.post(self.url('activate'), {'version': version}, format='json')

        with mock.patch.dict(os.environ, {name: '' for name in ENV}):       # the variables are gone
            self.new_order()
        self.assertEqual(len(FakeProvider.sent), 1)
        self.assertEqual(self.detail()['source'], 'panel')

    def test_a_reference_outside_the_namespace_still_resolves_to_nothing(self):
        CompanyMessagingSettings.objects.filter(pk=self.config.pk).update(whatsapp_access_token_env='SECRET_KEY')
        self.config.refresh_from_db()
        with mock.patch.dict(os.environ, {'SECRET_KEY': 'la-clave-de-django'}):
            self.assertEqual(messaging.credential(self.config, 'access_token'), '')
            self.assertNotIn('access_token', self.detail().get('env', {}).get('secrets', []))
