"""
INTEGRATIONS-CONSOLE · Google sign-in.

One public value: the OAuth client ID. A master sets it in the console and the
sign-in button, and the audience every Google token is checked against, are
that one from the next request on. There is no client secret in this flow and
the console does not ask for one.

Everything `test_google_auth` fixes — the backend verifies the token, the nonce
is single-use, the session is the usual HttpOnly cookie, linking is safe — is
untouched: this only changes where the client ID is read from.
"""
from unittest import mock

import jwt
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from store import google_identity
from store.integrations import registry
from store.test_google_auth import CLIENT_ID, CONFIG, LOGIN, GoogleBase

PANEL_ID = '424242424242-panelpanelpanel.apps.googleusercontent.com'
OTHER_ID = '515151515151-otrootrootro.apps.googleusercontent.com'
URL = '/api/admin/integrations/google/'


@override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(), GOOGLE_OAUTH_CLIENT_ID='')
class _Base(GoogleBase):
    def setUp(self):
        super().setUp()
        self.master = get_user_model().objects.create_superuser('master', 'master@example.com', 'x')
        self.console = APIClient()
        self.console.force_authenticate(self.master)
        self.reachable_patch = mock.patch.object(google_identity, 'signing_keys_reachable', return_value=True)
        self.reachable = self.reachable_patch.start()
        self.addCleanup(mock.patch.stopall)

    def detail(self):
        return self.console.get(URL).json()

    def save(self, client_id=PANEL_ID, **extra):
        current = self.detail()['draft']
        body = {'public': {'client_id': client_id}, 'secrets': {}, **extra}
        if current:
            body['version'] = current['version']
        return self.console.put(URL + 'draft/', body, format='json')

    def go_live(self, client_id=PANEL_ID):
        self.assertEqual(self.save(client_id).status_code, 200)
        self.assertEqual(self.console.post(URL + 'test/', {}, format='json').json()['status'], 'ok')
        version = self.detail()['draft']['version']
        self.assertEqual(self.console.post(URL + 'activate/', {'version': version}, format='json').status_code, 200)


class RuntimeTest(_Base):
    def test_with_nothing_configured_there_is_no_button_and_no_endpoint(self):
        self.assertEqual(self.client.get(CONFIG).json(), {'enabled': False})
        self.assertEqual(self.client.post(LOGIN, {'credential': 'x'}, format='json').status_code, 404)

    def test_the_button_gets_the_client_id_of_the_console(self):
        self.go_live()
        body = self.client.get(CONFIG).json()
        self.assertTrue(body['enabled'])
        self.assertEqual(body['client_id'], PANEL_ID)

    def test_a_token_is_accepted_only_for_the_audience_of_the_console(self):
        self.go_live()
        self.assertEqual(self.sign_in(aud=CLIENT_ID).status_code, 400)       # minted for another application
        accepted = self.sign_in(aud=PANEL_ID)
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertTrue(accepted.cookies['blackdog_access']['httponly'])

    def test_the_console_wins_over_the_environment(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID):
            self.go_live()
            self.assertEqual(self.client.get(CONFIG).json()['client_id'], PANEL_ID)
            self.assertEqual(self.sign_in(aud=CLIENT_ID).status_code, 400)

    def test_changing_the_client_id_changes_the_next_sign_in_without_a_restart(self):
        self.go_live()
        self.go_live(OTHER_ID)
        self.assertEqual(self.client.get(CONFIG).json()['client_id'], OTHER_ID)
        self.assertEqual(self.sign_in(aud=PANEL_ID).status_code, 400)
        self.assertEqual(self.sign_in(aud=OTHER_ID, sub='222', email='otra@example.com').status_code, 200)

    def test_switching_it_off_removes_the_button_and_does_not_fall_back(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID):
            self.go_live()
            self.console.post(URL + 'disable/', {}, format='json')
            self.assertEqual(self.client.get(CONFIG).json(), {'enabled': False})
            self.assertEqual(self.client.post(LOGIN, {'credential': 'x'}, format='json').status_code, 404)


class ValidationTest(_Base):
    def test_only_a_google_client_id_is_accepted(self):
        for wrong in ('prueba', 'https://accounts.google.com', 'GOCSPX-esto-es-un-secreto',
                      '123-abc.apps.googleusercontent.com.evil.example'):
            with self.subTest(wrong):
                response = self.save(wrong)
                self.assertEqual(response.status_code, 400)
                self.assertIn('client_id', response.json()['errors'])

    def test_an_empty_draft_does_not_pass_the_test(self):
        self.assertEqual(self.save(' ').status_code, 200)
        self.assertEqual(self.console.post(URL + 'test/', {}, format='json').json()['status'], 'incomplete')

    def test_the_console_does_not_ask_for_a_client_secret_and_stores_none(self):
        provider = registry.get('google')
        self.assertEqual([f.name for f in provider.fields], ['client_id'])
        self.assertFalse(any(f.secret for f in provider.fields))
        response = self.console.put(URL + 'draft/', {
            'public': {'client_id': PANEL_ID}, 'secrets': {'client_secret': 'GOCSPX-no-se-guarda'}}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_the_test_says_whether_the_server_can_reach_google_s_keys(self):
        self.save()
        self.assertEqual(self.console.post(URL + 'test/', {}, format='json').json()['status'], 'ok')
        self.reachable.return_value = False
        result = self.console.post(URL + 'test/', {}, format='json').json()
        self.assertEqual(result['status'], 'unavailable')
        version = self.detail()['draft']['version']
        self.assertEqual(self.console.post(URL + 'activate/', {'version': version}, format='json').status_code, 409)

    def test_reaching_the_keys_is_one_request_that_can_fail(self):
        self.reachable_patch.stop()
        self.addCleanup(setattr, google_identity, '_jwks_client', None)
        google_identity._jwks_client = None
        with mock.patch.object(jwt.PyJWKClient, 'get_signing_keys', side_effect=jwt.PyJWKClientError('sin red')):
            self.assertFalse(google_identity.signing_keys_reachable())
        with mock.patch.object(jwt.PyJWKClient, 'get_signing_keys', return_value=[object()]):
            self.assertTrue(google_identity.signing_keys_reachable())


class LegacyEnvironmentTest(_Base):
    def test_an_installation_configured_by_environment_keeps_its_button(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID):
            self.assertEqual(self.client.get(CONFIG).json()['client_id'], CLIENT_ID)
            self.assertEqual(self.sign_in().status_code, 200)
            detail = self.detail()
        self.assertEqual((detail['state'], detail['source']), ('ACTIVE', 'env'))
        self.assertEqual(detail['env']['public'], {'client_id': CLIENT_ID})
        self.assertEqual(detail['env']['secrets'], [])
