"""
ANALYTICS-MARKETING · the three measurement providers in the console.

    Google Analytics 4 · Meta (Pixel and Conversions API) · TikTok (Pixel and Events API)

They extend the provider registry of INTEGRATIONS-CONSOLE-01: nothing here has
a model, a view or a permission of its own. What they add:

  * an ID that is PUBLIC (the browser needs it to load the provider's script)
    and credentials that are not (they never leave the server);
  * a public, sanitised endpoint the storefront reads at run time, so that
    changing an ID in the console changes the next page view with no rebuild;
  * a test that never sends a real event: either it uses the provider's own
    test channel, or it says that nothing was verified.

No request leaves this process: `urlopen` is simulated.
"""
import io
import json
import urllib.error
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store.integrations import registry, service
from store.models import AdminAuditLog, CompanyRole, IntegrationConfig
from store.tests import _p2d_member, _p3_company

PUBLIC_CONFIG = '/api/measurement/config/'
URLOPEN = 'urllib.request.urlopen'

GA = {'measurement_id': 'G-NOESREAL01'}
GA_SECRET = {'api_secret': 'ga-api-secret-NoEsReal-0001'}
META = {'mode': 'pixel_and_capi', 'pixel_id': '123456789012345', 'test_event_code': 'TEST12345'}
META_SECRET = {'access_token': 'EAANoEsRealNoEsRealNoEsRealNoEsReal0001'}
TIKTOK = {'mode': 'pixel_and_events_api', 'pixel_code': 'C0NOESREAL0NOESREAL0', 'test_event_code': 'TEST67890'}
TIKTOK_SECRET = {'access_token': 'tiktok-access-token-NoEsReal-0001'}
SECRETS = (*GA_SECRET.values(), *META_SECRET.values(), *TIKTOK_SECRET.values())
IDS = ('google_analytics', 'meta', 'tiktok')


def reply(payload):
    answer = mock.MagicMock()
    answer.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return answer


def http_error(status, payload):
    return urllib.error.HTTPError('https://x.invalid', status, 'x', {}, io.BytesIO(json.dumps(payload).encode()))


@override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode())
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.console = APIClient()
        self.console.force_authenticate(self.master)

    def url(self, provider, action=''):
        return f'/api/admin/integrations/{provider}/' + (f'{action}/' if action else '')

    def detail(self, provider):
        return self.console.get(self.url(provider)).json()

    def save(self, provider, public, secrets=None):
        current = self.detail(provider)['draft']
        body = {'public': public, 'secrets': secrets or {}}
        if current:
            body['version'] = current['version']
        return self.console.put(self.url(provider, 'draft'), body, format='json')

    def run_test(self, provider, **kwargs):
        with mock.patch(URLOPEN, **kwargs) as urlopen:
            return self.console.post(self.url(provider, 'test'), {}, format='json').json(), urlopen

    def go_live(self, provider, public, secrets=None, **kwargs):
        response = self.save(provider, public, secrets)
        self.assertEqual(response.status_code, 200, response.content)
        result, _ = self.run_test(provider, **kwargs)
        self.assertTrue(result['ok'], result)
        version = self.detail(provider)['draft']['version']
        response = self.console.post(self.url(provider, 'activate'), {'version': version}, format='json')
        self.assertEqual(response.status_code, 200, response.content)

    def public_config(self):
        return APIClient().get(PUBLIC_CONFIG)


class RegistryTest(_Base):
    def test_the_three_are_registered_as_platform_providers_of_their_category(self):
        expected = {'google_analytics': 'analytics', 'meta': 'marketing', 'tiktok': 'marketing'}
        for provider_id, category in expected.items():
            provider = registry.get(provider_id)
            self.assertIsNotNone(provider, provider_id)
            self.assertEqual((provider.category, provider.scope), (category, registry.SCOPE_PLATFORM))

    def test_they_reuse_the_console_and_bring_no_model_of_their_own(self):
        from django.apps import apps

        names = {model.__name__ for model in apps.get_app_config('store').get_models()}
        for forbidden in ('GoogleAnalyticsConfig', 'MetaConfig', 'TikTokConfig'):
            self.assertNotIn(forbidden, names)
        self.go_live('google_analytics', GA)
        self.assertEqual(IntegrationConfig.objects.get(slot='active').provider, 'google_analytics')

    def test_which_fields_are_secret_is_declared(self):
        secret = {p: [f.name for f in registry.get(p).secret_fields()] for p in IDS}
        self.assertEqual(secret, {'google_analytics': ['api_secret'], 'meta': ['access_token'], 'tiktok': ['access_token']})
        public = {p: [f.name for f in registry.get(p).public_fields()] for p in IDS}
        self.assertEqual(public['google_analytics'], ['measurement_id'])
        self.assertEqual(public['meta'], ['mode', 'pixel_id', 'test_event_code'])
        self.assertEqual(public['tiktok'], ['mode', 'pixel_code', 'test_event_code'])

    def test_only_the_modes_that_exist_are_offered(self):
        meta = dict(registry.get('meta').field('mode').choices)
        tiktok = dict(registry.get('tiktok').field('mode').choices)
        self.assertEqual(sorted(meta), ['pixel_and_capi', 'pixel_only'])
        self.assertEqual(sorted(tiktok), ['pixel_and_events_api', 'pixel_only'])

    def test_no_provider_takes_an_address_to_send_to(self):
        """Where the scripts come from and where events go is the code's, never a field."""
        for provider_id in IDS:
            for field in registry.get(provider_id).fields:
                self.assertNotIn(field.kind, ('url', 'host'), f'{provider_id}.{field.name}')
                self.assertNotRegex(field.name, r'url|endpoint|host|script|domain')


class MasterOnlyTest(_Base):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        company = _p3_company('medicion-a', 'Empresa A')
        everything = CompanyRole.objects.get(company=company, slug='administrador').capabilities
        self.others = {
            'administrador de empresa': _p2d_member(company, 'admin_a', everything)[0],
            'personal': User.objects.create_user('personal', 'p@example.com', 'x', is_staff=True),
            'cliente': User.objects.create_user('cliente', 'c@example.com', 'x'),
        }

    def calls(self, client, provider):
        yield client.get(self.url(provider))
        yield client.put(self.url(provider, 'draft'), {'public': GA, 'secrets': {}}, format='json')
        for action in ('test', 'activate', 'disable', 'enable', 'revoke'):
            yield client.post(self.url(provider, action), {'confirm': 'REVOCAR', 'version': 1}, format='json')

    def test_a_master_reaches_them(self):
        for provider in IDS:
            self.assertEqual(self.console.get(self.url(provider)).status_code, 200)

    def test_nobody_else_does_whatever_their_role(self):
        for provider in IDS:
            for who, user in self.others.items():
                client = APIClient()
                client.force_authenticate(user)
                for response in self.calls(client, provider):
                    self.assertEqual(response.status_code, 403, f'{who} · {provider}')
            for response in self.calls(APIClient(), provider):
                self.assertEqual(response.status_code, 401, provider)
        self.assertFalse(IntegrationConfig.objects.exists())


class ValidationTest(_Base):
    def errors(self, provider, public, secrets=None):
        response = self.save(provider, public, secrets)
        self.assertEqual(response.status_code, 400, response.content)
        return response.json()['errors']

    def test_an_id_has_the_shape_its_provider_gives_it(self):
        for wrong in ('UA-12345-1', 'G-', 'g-noesreal01', 'G-ABC DEF', "G-X');alert(1)//", 'GTM-ABC123'):
            with self.subTest(ga=wrong):
                self.assertIn('measurement_id', self.errors('google_analytics', {'measurement_id': wrong}))
        for wrong in ('abc', '12345', "1234567890');fbq('x", '123456789012345678901234567890'):
            with self.subTest(meta=wrong):
                self.assertIn('pixel_id', self.errors('meta', {**META, 'mode': 'pixel_only', 'pixel_id': wrong}))
        for wrong in ('abc', 'c0noesreal0noesreal0', "C0NOESREAL');ttq.x('", 'C0NOESREAL0NOESREAL0C0NOESREAL0'):
            with self.subTest(tiktok=wrong):
                self.assertIn('pixel_code', self.errors('tiktok', {**TIKTOK, 'mode': 'pixel_only', 'pixel_code': wrong}))

    def test_an_id_in_another_alphabets_digits_or_with_a_line_break_is_not_an_id(self):
        """REVIEW: it passed, and then made the request to the provider crash outside its error handling."""
        self.assertIn('pixel_id', self.errors('meta', {'mode': 'pixel_only', 'pixel_id': '١٢٣٤٥٦٧٨٩٠١٢٣٤٥'}))
        self.assertIn('measurement_id', self.errors('google_analytics', {'measurement_id': 'G-NOESREAL٠١'}))
        from store.integrations.providers import measurement
        for pattern, value in ((measurement._META_PIXEL, '123456789012345\n'), (measurement._GA_ID, 'G-NOESREAL01\n'),
                               (measurement._TIKTOK_PIXEL, 'C0NOESREAL0NOESREAL0\n'), (measurement._TEST_CODE, 'TEST12345\n')):
            self.assertIsNone(pattern.fullmatch(value))
            self.assertFalse(measurement.matches(pattern, value))

    def test_a_request_that_cannot_even_be_built_is_a_refusal_not_a_crash(self):
        from store.measurement import adapters
        answer = adapters.tiktok_send('C0NOESREAL0NOESREAL0', 'token-con-ñ-y-٣', [{'event': 'Purchase'}])
        self.assertEqual((answer.kind, answer.retryable), ('invalid', False))
        answer = adapters.meta_send('١٢٣', 'token', [{'event_name': 'Purchase'}])
        self.assertEqual((answer.kind, answer.retryable), ('invalid', False))

    def test_a_refused_id_is_not_repeated_back(self):
        response = self.save('google_analytics', {'measurement_id': "G-X');alert(1)//"})
        self.assertNotIn('alert', response.content.decode())

    def test_the_server_side_mode_needs_its_token_and_the_pixel_mode_does_not(self):
        self.assertIn('access_token', self.errors('meta', META))
        self.assertIn('access_token', self.errors('tiktok', TIKTOK))
        self.assertEqual(self.save('meta', {**META, 'mode': 'pixel_only'}).status_code, 200)
        self.assertEqual(self.save('tiktok', {**TIKTOK, 'mode': 'pixel_only'}).status_code, 200)

    def test_a_test_event_code_is_a_short_code_and_nothing_else(self):
        self.assertIn('test_event_code', self.errors('meta', {**META, 'test_event_code': 'TEST 1; DROP'}, META_SECRET))
        self.assertIn('test_event_code', self.errors('tiktok', {**TIKTOK, 'test_event_code': '<script>'}, TIKTOK_SECRET))


class SecretsTest(_Base):
    def configure_all(self):
        self.go_live('google_analytics', GA, GA_SECRET, return_value=reply({'validationMessages': []}))
        self.go_live('meta', META, META_SECRET, return_value=reply({'events_received': 1}))
        self.go_live('tiktok', TIKTOK, TIKTOK_SECRET, return_value=reply({'code': 0, 'message': 'OK'}))

    def test_they_are_sealed_in_the_database(self):
        self.configure_all()
        for row in IntegrationConfig.objects.all():
            legible = json.dumps([row.public, row.secret_meta]) + row.sealed_secrets
            for secret in SECRETS:
                self.assertNotIn(secret, legible)
            self.assertTrue(row.sealed_secrets)
            self.assertEqual(list(service.open_secrets(row)), [registry.get(row.provider).secret_fields()[0].name])

    def test_no_api_answer_carries_one(self):
        self.configure_all()
        text = self.console.get('/api/admin/integrations/').content.decode() + self.public_config().content.decode()
        for provider in IDS:
            text += self.console.get(self.url(provider)).content.decode()
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        for provider, name in (('google_analytics', 'api_secret'), ('meta', 'access_token'), ('tiktok', 'access_token')):
            self.assertEqual(self.detail(provider)['active']['secrets'][name], {
                'configured': True, 'updated_at': mock.ANY, 'updated_by': 'master'})

    def test_neither_the_audit_log_nor_the_application_log_holds_one(self):
        with self.assertLogs(level='DEBUG') as captured:
            import logging
            logging.getLogger('store.integrations').info('configurando')
            self.configure_all()
            # …including the paths that log: a provider that refuses, and one that crashes quoting the token.
            self.save('meta', META, META_SECRET)
            self.run_test('meta', side_effect=http_error(400, {'error': {'message': f'Invalid token {META_SECRET["access_token"]}'}}))
            self.run_test('tiktok', side_effect=RuntimeError(f'boom {TIKTOK_SECRET["access_token"]}'))
        text = '\n'.join(captured.output) + json.dumps(list(AdminAuditLog.objects.values_list('metadata', flat=True)))
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertGreaterEqual(AdminAuditLog.objects.filter(target_type='integration').count(), 9)

    def test_a_refusal_from_the_provider_never_reaches_the_console_verbatim(self):
        self.save('meta', META, META_SECRET)
        echo = {'error': {'message': f'Malformed access token {META_SECRET["access_token"]}', 'code': 190}}
        result, _ = self.run_test('meta', side_effect=http_error(400, echo))
        self.assertFalse(result['ok'])
        self.assertNotIn(META_SECRET['access_token'], json.dumps(result))


class PublicRuntimeConfigTest(_Base):
    """What the storefront is told, without a session, at every page load."""

    def test_with_nothing_active_it_says_so_and_names_no_provider(self):
        response = self.public_config()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'providers': {}})

    def test_it_gives_the_public_ids_of_what_is_active_and_only_those(self):
        SecretsTest.configure_all(self)
        self.assertEqual(self.public_config().json(), {'providers': {
            'google_analytics': {'measurement_id': 'G-NOESREAL01', 'purchase': 'server'},
            'meta': {'pixel_id': '123456789012345', 'purchase': 'both'},
            'tiktok': {'pixel_code': 'C0NOESREAL0NOESREAL0', 'purchase': 'both'},
        }})

    def test_without_server_credentials_the_purchase_is_the_browsers_to_send(self):
        self.go_live('google_analytics', GA)
        self.go_live('meta', {'mode': 'pixel_only', 'pixel_id': '123456789012345'})
        self.go_live('tiktok', {'mode': 'pixel_only', 'pixel_code': 'C0NOESREAL0NOESREAL0'})
        providers = self.public_config().json()['providers']
        self.assertEqual({name: value['purchase'] for name, value in providers.items()},
                         {'google_analytics': 'browser', 'meta': 'browser', 'tiktok': 'browser'})

    def test_changing_an_id_in_the_console_changes_the_next_answer_without_a_restart(self):
        self.go_live('google_analytics', GA)
        self.go_live('google_analytics', {'measurement_id': 'G-OTRONOESREAL'})
        self.assertEqual(self.public_config().json()['providers']['google_analytics']['measurement_id'], 'G-OTRONOESREAL')

    def test_a_draft_a_disabled_provider_and_a_revoked_one_are_not_announced(self):
        self.save('google_analytics', GA)
        self.assertEqual(self.public_config().json(), {'providers': {}})
        self.go_live('meta', {'mode': 'pixel_only', 'pixel_id': '123456789012345'})
        self.console.post(self.url('meta', 'disable'), {}, format='json')
        self.assertEqual(self.public_config().json(), {'providers': {}})
        self.go_live('tiktok', {'mode': 'pixel_only', 'pixel_code': 'C0NOESREAL0NOESREAL0'})
        self.console.post(self.url('tiktok', 'revoke'), {'confirm': 'REVOCAR'}, format='json')
        self.assertEqual(self.public_config().json(), {'providers': {}})

    def test_it_is_read_only_and_carries_nothing_but_what_a_script_tag_shows_anyway(self):
        SecretsTest.configure_all(self)
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(APIClient(), method)(PUBLIC_CONFIG, {}, format='json').status_code, 405)
        body = self.public_config().content.decode()
        for forbidden in (*SECRETS, 'TEST12345', 'TEST67890', 'access_token', 'api_secret', 'test_event_code', 'mode'):
            self.assertNotIn(forbidden, body)

    def test_a_configuration_the_server_cannot_read_is_not_announced_and_breaks_nothing(self):
        SecretsTest.configure_all(self)
        with override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()), \
                self.assertLogs('store', level='ERROR'):
            response = self.public_config()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'providers': {}})


class ConnectionTestTest(_Base):
    def test_ga_without_a_secret_checks_nothing_and_says_so(self):
        self.save('google_analytics', GA)
        result, urlopen = self.run_test('google_analytics')
        self.assertEqual((result['ok'], result['status']), (True, 'unverified'))
        urlopen.assert_not_called()

    def test_ga_with_a_secret_asks_googles_validation_server_which_records_nothing(self):
        self.save('google_analytics', GA, GA_SECRET)
        result, urlopen = self.run_test('google_analytics', return_value=reply({'validationMessages': []}))
        self.assertEqual(result['status'], 'unverified')         # Google says it checks neither the ID nor the secret
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.startswith('https://www.google-analytics.com/debug/mp/collect?'))
        self.assertNotIn('/mp/collect?', request.full_url.replace('/debug/mp/collect?', ''))
        self.assertLessEqual(urlopen.call_args.kwargs['timeout'], 10)

        result, _ = self.run_test('google_analytics', return_value=reply({'validationMessages': [
            {'fieldPath': 'events', 'description': 'x', 'validationCode': 'NAME_INVALID'}]}))
        self.assertEqual((result['ok'], result['status']), (False, 'invalid'))

    def test_the_pixel_only_modes_send_nothing(self):
        for provider, public in (('meta', {'mode': 'pixel_only', 'pixel_id': '123456789012345'}),
                                 ('tiktok', {'mode': 'pixel_only', 'pixel_code': 'C0NOESREAL0NOESREAL0'})):
            self.save(provider, public)
            result, urlopen = self.run_test(provider)
            self.assertEqual((result['ok'], result['status']), (True, 'unverified'), provider)
            urlopen.assert_not_called()

    def test_meta_is_verified_with_a_test_event_that_is_not_a_real_one(self):
        self.save('meta', META, META_SECRET)
        result, urlopen = self.run_test('meta', return_value=reply({'events_received': 1, 'fbtrace_id': 'x'}))
        self.assertEqual(result['status'], 'ok')
        request = urlopen.call_args.args[0]
        self.assertRegex(request.full_url, r'^https://graph\.facebook\.com/v\d+\.\d+/123456789012345/events$')
        self.assertNotIn(META_SECRET['access_token'], request.full_url)       # in the body, not in a URL a proxy logs
        body = json.loads(request.data)
        self.assertEqual(body['test_event_code'], 'TEST12345')
        self.assertEqual(body['access_token'], META_SECRET['access_token'])
        [event] = body['data']
        self.assertEqual((event['event_name'], event['action_source']), ('PageView', 'website'))
        self.assertNotIn('em', event['user_data'])

    def test_without_a_test_code_a_server_side_mode_is_not_tested_with_a_real_event(self):
        for provider, public, secrets in (('meta', {**META, 'test_event_code': ''}, META_SECRET),
                                          ('tiktok', {**TIKTOK, 'test_event_code': ''}, TIKTOK_SECRET)):
            self.save(provider, public, secrets)
            result, urlopen = self.run_test(provider)
            self.assertEqual((result['ok'], result['status']), (True, 'unverified'), provider)
            urlopen.assert_not_called()

    def test_tiktok_is_verified_with_a_test_event_through_the_events_api(self):
        self.save('tiktok', TIKTOK, TIKTOK_SECRET)
        result, urlopen = self.run_test('tiktok', return_value=reply({'code': 0, 'message': 'OK', 'request_id': 'x'}))
        self.assertEqual(result['status'], 'ok')
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'https://business-api.tiktok.com/open_api/v1.3/event/track/')
        self.assertEqual(request.get_header('Access-token'), TIKTOK_SECRET['access_token'])
        body = json.loads(request.data)
        self.assertEqual((body['event_source'], body['event_source_id'], body['test_event_code']),
                         ('web', 'C0NOESREAL0NOESREAL0', 'TEST67890'))
        self.assertNotIn(TIKTOK_SECRET['access_token'], request.data.decode())

    def test_what_the_providers_answer_becomes_a_kind_of_failure(self):
        self.save('meta', META, META_SECRET)
        for failure, expected in ((http_error(401, {}), 'auth_failed'), (http_error(400, {'error': {'code': 190}}), 'auth_failed'),
                                  (http_error(400, {'error': {'code': 100}}), 'invalid'), (http_error(503, {}), 'unavailable'),
                                  (urllib.error.URLError('sin red'), 'unavailable'), (TimeoutError('t'), 'unavailable')):
            result, _ = self.run_test('meta', side_effect=failure)
            self.assertEqual(result['status'], expected, repr(failure))
        self.save('tiktok', TIKTOK, TIKTOK_SECRET)
        for answer, expected in (({'code': 40105, 'message': 'x'}, 'auth_failed'), ({'code': 40002, 'message': 'x'}, 'invalid'),
                                 ({'code': 50000, 'message': 'x'}, 'unavailable')):
            result, _ = self.run_test('tiktok', return_value=reply(answer))
            self.assertEqual(result['status'], expected, answer)

    def test_a_failed_test_cannot_be_activated(self):
        self.save('meta', META, META_SECRET)
        self.run_test('meta', side_effect=http_error(401, {}))
        version = self.detail('meta')['draft']['version']
        self.assertEqual(self.console.post(self.url('meta', 'activate'), {'version': version}, format='json').status_code, 409)
        self.assertEqual(self.public_config().json(), {'providers': {}})
