"""
SOCIAL-AUTH-01 — «Continuar con Facebook» y «Continuar con Apple».

Google ya existe (`test_google_auth`) y no cambia. Facebook y Apple son flujos
de redirección: el navegador va al proveedor y vuelve con un código que SÓLO el
servidor canjea. Aquí se fija:

  * el intento (`state`, `nonce`, verificador PKCE) vive en el servidor, se usa
    una vez, caduca y pertenece al navegador que lo empezó;
  * Facebook se canjea con PKCE y el secreto de la app, y su token se comprueba
    con `debug_token` antes de creer `/me`;
  * Apple firma su secreto de cliente con ES256 y su `id_token` se verifica con
    sus claves, su emisor, la audiencia, la caducidad y el `nonce`;
  * una identidad nueva NUNCA se cuelga sola de una cuenta que ya existe;
  * Facebook y Apple sólo abren cuentas de COMPRADOR y no dan ningún privilegio.

Sin red: los proveedores se simulan; las claves son de prueba.
"""
import base64
import hashlib
import json
import time
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store import apple_identity, facebook_identity, social_identity
from store.integrations import registry, service
from store.models import Company, ExternalIdentity, IntegrationConfig, Membership, UserProfile

User = get_user_model()

FRONTEND = 'https://tienda.example'
APP_ID = '1234567890123'
APP_SECRET = 'fb-app-secret-de-prueba'
SERVICES_ID = 'pe.blackdog.web'
TEAM_ID = 'TEAM123456'
KEY_ID = 'KEY1234567'
APPLE_KEY = ec.generate_private_key(ec.SECP256R1())
APPLE_KEY_PEM = APPLE_KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
).decode()
ID_TOKEN_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

CONFIG = '/api/auth/social/config/'
FB_START = '/api/auth/facebook/start/'
FB_CALLBACK = '/api/auth/facebook/callback/'
APPLE_START = '/api/auth/apple/start/'
APPLE_CALLBACK = '/api/auth/apple/callback/'
IDENTITIES = '/api/auth/social/identities/'


def link_url(provider):
    return f'/api/auth/social/{provider}/link/'


def configure(provider_id, public, secrets):
    row = IntegrationConfig(
        provider=provider_id, slot=IntegrationConfig.SLOT_ACTIVE, enabled=True, public=public,
        validated=True, last_test_status='ok',
    )
    service._seal(row, secrets)
    row.save()
    return row


def configure_facebook():
    return configure('facebook', {'app_id': APP_ID, 'graph_version': 'v23.0'}, {'app_secret': APP_SECRET})


def configure_apple():
    return configure(
        'apple', {'services_id': SERVICES_ID, 'team_id': TEAM_ID, 'key_id': KEY_ID},
        {'private_key': APPLE_KEY_PEM},
    )


def query(response):
    return {key: values[0] for key, values in parse_qs(urlparse(response['Location']).query).items()}


@override_settings(APP_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(), FRONTEND_URL=FRONTEND)
class SocialBase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.fb_user = {'id': '1001', 'name': 'Ana Pérez', 'first_name': 'Ana', 'last_name': 'Pérez',
                        'email': 'ana@example.com'}
        self.fb_debug = {'data': {'app_id': APP_ID, 'is_valid': True, 'user_id': '1001'}}
        self.fb_exchange = {'access_token': 'fb-user-access-token', 'token_type': 'bearer'}
        self.fb_calls = []
        get = mock.patch.object(facebook_identity, '_get', side_effect=self._facebook)
        get.start()
        self.apple_claims = {}
        self.apple_calls = []
        post = mock.patch.object(apple_identity, '_post', side_effect=self._apple)
        post.start()
        key = mock.patch.object(apple_identity, '_signing_key', return_value=ID_TOKEN_KEY.public_key())
        key.start()
        self.addCleanup(mock.patch.stopall)

    # -- simulated providers ----------------------------------------------------

    def _facebook(self, url, params):
        self.fb_calls.append((url, dict(params)))
        if url.endswith('/oauth/access_token'):
            return self.fb_exchange
        if url.endswith('/debug_token'):
            return self.fb_debug
        if url.endswith('/me'):
            return self.fb_user
        raise AssertionError(url)

    def _apple(self, url, data):
        self.apple_calls.append((url, dict(data)))
        return {'access_token': 'apple-at', 'id_token': self.apple_token(**self.apple_claims)}

    def apple_token(self, key=ID_TOKEN_KEY, algorithm='RS256', **claims):
        now = int(time.time())
        payload = {
            'iss': 'https://appleid.apple.com', 'aud': SERVICES_ID, 'sub': '001234.abcdef.0987',
            'email': 'x7k2@privaterelay.appleid.com', 'email_verified': 'true',
            'is_private_email': 'true', 'iat': now, 'exp': now + 600, 'nonce': self.nonce,
        }
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        return jwt.encode(payload, key, algorithm=algorithm, headers={'kid': 'apple-prueba'})

    # -- the browser ---------------------------------------------------------------

    def facebook_start(self, client=None, **params):
        res = (client or self.client).get(FB_START, params)
        self.assertEqual(res.status_code, 302, res.content)
        return query(res)

    def facebook_callback(self, state, client=None, code='fb-code', **extra):
        return (client or self.client).get(FB_CALLBACK, {'code': code, 'state': state, **extra})

    def facebook_sign_in(self, **params):
        return self.facebook_callback(self.facebook_start(**params)['state'])

    def apple_start(self, client=None, **params):
        res = (client or self.client).get(APPLE_START, params)
        self.assertEqual(res.status_code, 302, res.content)
        sent = query(res)
        self.nonce = sent['nonce']
        return sent

    def apple_post(self, state, client=None, **form):
        body = {'state': state, 'code': 'apple-code', **form}
        return (client or self.client).post(
            APPLE_CALLBACK, urlencode(body), content_type='application/x-www-form-urlencoded',
        )

    def follow(self, posted, client=None):
        """What the browser does with Apple's 303: GET it, on this site."""
        self.assertEqual(posted.status_code, 303, posted.content)
        location = urlparse(posted['Location'])
        return (client or self.client).get(f'{location.path}?{location.query}')

    def apple_sign_in(self, **form):
        state = self.apple_start()['state']
        return self.follow(self.apple_post(state, **form))

    def assertLanded(self, res, path, **params):
        self.assertEqual(res.status_code, 302, res.content)
        location = urlparse(res['Location'])
        self.assertEqual(f'{location.scheme}://{location.netloc}', FRONTEND)
        self.assertEqual(location.path, path)
        got = {key: values[0] for key, values in parse_qs(location.query).items()}
        for key, value in params.items():
            self.assertEqual(got.get(key), value, got)
        return got

    def assertSignedIn(self, res):
        self.assertTrue(res.cookies['blackdog_access']['httponly'])
        self.assertIn('blackdog_refresh', res.cookies)

    def assertNotSignedIn(self, res):
        self.assertNotIn('blackdog_access', {k for k, v in res.cookies.items() if v.value})


# ---------------------------------------------------------------------------------
# What the browser is told
# ---------------------------------------------------------------------------------

class ConfigTest(SocialBase):
    def test_each_provider_is_offered_only_where_it_is_configured(self):
        body = self.client.get(CONFIG).json()
        self.assertEqual(body['providers'], {
            'google': {'enabled': False}, 'facebook': {'enabled': False}, 'apple': {'enabled': False}})

        configure_facebook()
        configure_apple()
        body = self.client.get(CONFIG).json()
        self.assertTrue(body['providers']['facebook']['enabled'])
        self.assertTrue(body['providers']['apple']['enabled'])
        # Nothing of the configuration travels: the browser only needs «on».
        text = json.dumps(body)
        for value in (APP_ID, APP_SECRET, SERVICES_ID, TEAM_ID, KEY_ID, 'PRIVATE KEY'):
            self.assertNotIn(value, text)

    def test_google_is_reported_as_google_reports_itself(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID='1234567-abcdefgh.apps.googleusercontent.com'):
            self.assertTrue(self.client.get(CONFIG).json()['providers']['google']['enabled'])

    def test_a_provider_that_is_not_configured_has_no_endpoints(self):
        for path in (FB_START, FB_CALLBACK, APPLE_START, APPLE_CALLBACK):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.apple_post('x').status_code, 404)

    def test_a_provider_switched_off_in_the_console_stops_at_once(self):
        row = configure_facebook()
        state = self.facebook_start()['state']
        row.enabled = False
        row.save()
        self.assertEqual(self.facebook_callback(state).status_code, 404)
        self.assertFalse(User.objects.exists())

    def test_both_are_platform_integrations_of_the_identity_section(self):
        for provider_id in ('facebook', 'apple'):
            provider = registry.get(provider_id)
            self.assertEqual((provider.category, provider.scope), ('identity', registry.SCOPE_PLATFORM))


# ---------------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------------

class FacebookFlowTest(SocialBase):
    def setUp(self):
        super().setUp()
        configure_facebook()

    def test_the_authorization_request_carries_state_and_pkce_and_a_fixed_callback(self):
        res = self.client.get(FB_START)
        location = urlparse(res['Location'])
        self.assertEqual(location.netloc, 'www.facebook.com')
        self.assertEqual(location.path, '/v23.0/dialog/oauth')
        sent = query(res)
        self.assertEqual(sent['client_id'], APP_ID)
        self.assertEqual(sent['redirect_uri'], f'{FRONTEND}/api/auth/facebook/callback/')
        self.assertEqual(sent['response_type'], 'code')
        self.assertEqual(sent['code_challenge_method'], 'S256')
        self.assertGreaterEqual(len(sent['state']), 32)
        self.assertIn('email', sent['scope'])
        cookie = res.cookies[social_identity.ATTEMPT_COOKIE]
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertNotEqual(cookie.value, sent['state'])

    @override_settings(ALLOWED_HOSTS=['*'])
    def test_the_callback_is_built_from_settings_and_never_from_the_request(self):
        res = self.client.get(FB_START, HTTP_HOST='evil.example', HTTP_X_FORWARDED_HOST='evil.example')
        self.assertEqual(query(res)['redirect_uri'], f'{FRONTEND}/api/auth/facebook/callback/')

    def test_a_new_person_gets_a_buyer_account_and_the_usual_cookie_session(self):
        res = self.facebook_sign_in(next='/orders')

        self.assertLanded(res, '/auth', social='facebook', next='/orders')
        self.assertSignedIn(res)
        identity = ExternalIdentity.objects.get(provider='facebook', subject='1001')
        user = identity.user
        self.assertEqual((user.first_name, user.last_name), ('Ana', 'Pérez'))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(identity.email_at_link, 'ana@example.com')
        # Facebook says nothing about whether it verified the address: it is not
        # the account's address, and it proves no mailbox.
        self.assertEqual(user.email, '')
        self.assertIsNone(UserProfile.objects.filter(user=user).values_list(
            'email_verified_at', flat=True).first())

    def test_the_code_is_exchanged_with_the_pkce_verifier_and_the_app_secret(self):
        sent = self.facebook_start()
        self.facebook_callback(sent['state'], code='the-code')
        url, params = self.fb_calls[0]
        self.assertTrue(url.endswith('/v23.0/oauth/access_token'))
        self.assertEqual(params['code'], 'the-code')
        self.assertEqual(params['client_secret'], APP_SECRET)
        self.assertEqual(params['redirect_uri'], f'{FRONTEND}/api/auth/facebook/callback/')
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(params['code_verifier'].encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(challenge, sent['code_challenge'])

    def test_the_user_token_is_checked_with_debug_token_and_me_uses_appsecret_proof(self):
        self.facebook_sign_in()
        urls = [url.rsplit('/', 1)[-1] for url, _params in self.fb_calls]
        self.assertEqual(urls, ['access_token', 'debug_token', 'me'])
        debug = self.fb_calls[1][1]
        self.assertEqual(debug['input_token'], 'fb-user-access-token')
        self.assertEqual(debug['access_token'], f'{APP_ID}|{APP_SECRET}')
        me = self.fb_calls[2][1]
        self.assertIn('appsecret_proof', me)
        self.assertIn('email', me['fields'])

    def test_nothing_from_facebook_is_stored_but_who_the_person_is(self):
        self.facebook_sign_in()
        for row in IntegrationConfig.objects.all():
            self.assertNotIn('fb-user-access-token', json.dumps(row.public))
        identity = ExternalIdentity.objects.get()
        self.assertNotIn('fb-user-access-token', repr(identity.__dict__))

    def test_coming_back_is_the_same_account(self):
        self.facebook_sign_in()
        self.client = APIClient()
        res = self.facebook_sign_in()
        self.assertLanded(res, '/auth', social='facebook')
        self.assertEqual(User.objects.count(), 1)

    def test_a_person_without_an_email_still_gets_an_account(self):
        del self.fb_user['email']
        res = self.facebook_sign_in()
        self.assertLanded(res, '/auth', social='facebook')
        identity = ExternalIdentity.objects.get(provider='facebook')
        self.assertEqual((identity.user.email, identity.email_at_link), ('', ''))
        self.assertTrue(identity.user.username)

    def test_a_token_for_another_app_is_refused(self):
        self.fb_debug['data']['app_id'] = '999'
        res = self.facebook_sign_in()
        self.assertLanded(res, '/auth', social_error='refused', provider='facebook')
        self.assertNotSignedIn(res)
        self.assertFalse(User.objects.exists())

    def test_a_token_facebook_says_is_not_valid_is_refused(self):
        self.fb_debug['data']['is_valid'] = False
        self.assertLanded(self.facebook_sign_in(), '/auth', social_error='refused')
        self.assertFalse(User.objects.exists())

    def test_me_must_be_the_person_the_token_belongs_to(self):
        self.fb_user['id'] = '2002'
        self.assertLanded(self.facebook_sign_in(), '/auth', social_error='refused')
        self.assertFalse(User.objects.exists())

    def test_a_refused_exchange_is_a_refusal(self):
        # What Facebook answers to a wrong PKCE verifier, a used code or a bad secret.
        self.fb_exchange = {'error': {'message': 'Invalid code verifier', 'type': 'OAuthException'}}
        self.assertLanded(self.facebook_sign_in(), '/auth', social_error='refused')
        self.assertEqual(len(self.fb_calls), 1)
        self.assertFalse(User.objects.exists())

    def test_the_person_said_no_at_facebook(self):
        state = self.facebook_start()['state']
        res = self.client.get(FB_CALLBACK, {'state': state, 'error': 'access_denied'})
        self.assertLanded(res, '/auth', social_error='cancelled', provider='facebook')
        self.assertEqual(self.fb_calls, [])

    def test_a_next_that_leaves_the_site_is_dropped(self):
        for evil in ('https://evil.example/x', '//evil.example', '/\\evil.example', 'javascript:alert(1)'):
            self.client = APIClient()
            got = self.assertLanded(self.facebook_sign_in(next=evil), '/auth', social='facebook')
            self.assertNotIn('next', got, evil)


class StateTest(SocialBase):
    """The attempt: server-side, single use, short-lived, and this browser's."""

    def setUp(self):
        super().setUp()
        configure_facebook()

    def test_a_missing_state_is_refused(self):
        self.facebook_start()
        res = self.client.get(FB_CALLBACK, {'code': 'fb-code'})
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertEqual(self.fb_calls, [])

    def test_a_state_nobody_issued_is_refused(self):
        self.facebook_start()
        self.assertLanded(self.facebook_callback('invented-state'), '/auth', social_error='refused')
        self.assertEqual(self.fb_calls, [])

    def test_a_replayed_callback_is_refused_and_the_code_is_exchanged_once(self):
        state = self.facebook_start()['state']
        self.assertLanded(self.facebook_callback(state), '/auth', social='facebook')
        again = self.facebook_callback(state)
        self.assertLanded(again, '/auth', social_error='refused')
        self.assertEqual(len([c for c in self.fb_calls if c[0].endswith('access_token')]), 1)

    def test_a_replay_with_the_cookie_copied_back_is_still_refused(self):
        # Single use is the server's, not the cookie's: clearing the cookie on
        # the way back is not what stops a second exchange.
        state = self.facebook_start()['state']
        binding = self.client.cookies[social_identity.ATTEMPT_COOKIE].value
        self.assertLanded(self.facebook_callback(state), '/auth', social='facebook')
        self.client.cookies[social_identity.ATTEMPT_COOKIE] = binding
        self.assertLanded(self.facebook_callback(state), '/auth', social_error='refused')
        self.assertEqual(len([c for c in self.fb_calls if c[0].endswith('access_token')]), 1)

    def test_an_apple_attempt_does_not_open_facebook(self):
        configure_apple()
        state = self.apple_start()['state']
        self.assertLanded(self.facebook_callback(state), '/auth', social_error='refused')
        self.assertEqual(self.fb_calls, [])
        self.assertFalse(User.objects.exists())

    def test_an_expired_attempt_is_refused(self):
        state = self.facebook_start()['state']
        later = time.time() + social_identity.ATTEMPT_TTL + 1
        with mock.patch.object(social_identity, '_now', return_value=later):
            res = self.facebook_callback(state)
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertEqual(self.fb_calls, [])

    def test_an_attempt_started_in_another_browser_is_refused(self):
        # Login CSRF: the attacker starts, and sends the victim the callback.
        attacker = APIClient()
        state = self.facebook_start(client=attacker)['state']
        res = self.facebook_callback(state)
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertNotSignedIn(res)
        self.assertEqual(self.fb_calls, [])

    def test_the_binding_cookie_must_match_not_merely_exist(self):
        state = self.facebook_start()['state']
        self.client.cookies[social_identity.ATTEMPT_COOKIE] = 'otro-valor'
        self.assertLanded(self.facebook_callback(state), '/auth', social_error='refused')

    def test_a_state_of_one_provider_does_not_open_another(self):
        configure_apple()
        state = self.facebook_start()['state']
        self.assertLanded(self.apple_post(state), '/auth', social_error='refused')
        res = self.client.get(APPLE_CALLBACK, {'state': state})
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertEqual(self.apple_calls, [])


# ---------------------------------------------------------------------------------
# Apple
# ---------------------------------------------------------------------------------

class AppleFlowTest(SocialBase):
    def setUp(self):
        super().setUp()
        configure_apple()

    def test_the_authorization_request(self):
        res = self.client.get(APPLE_START)
        location = urlparse(res['Location'])
        self.assertEqual((location.netloc, location.path), ('appleid.apple.com', '/auth/authorize'))
        sent = query(res)
        self.assertEqual(sent['client_id'], SERVICES_ID)
        self.assertEqual(sent['redirect_uri'], f'{FRONTEND}/api/auth/apple/callback/')
        self.assertEqual(sent['response_type'], 'code')
        self.assertEqual(sent['response_mode'], 'form_post')
        self.assertEqual(sent['scope'], 'name email')
        self.assertGreaterEqual(len(sent['state']), 32)
        self.assertGreaterEqual(len(sent['nonce']), 32)
        self.assertNotEqual(sent['state'], sent['nonce'])

    def test_the_form_post_only_hands_over_to_this_site_and_signs_nobody_in(self):
        state = self.apple_start()['state']
        posted = self.apple_post(state)
        self.assertEqual(posted.status_code, 303)
        location = urlparse(posted['Location'])
        self.assertEqual(f'{location.scheme}://{location.netloc}{location.path}', f'{FRONTEND}/api/auth/apple/callback/')
        sent = parse_qs(location.query)
        self.assertEqual(sent['state'], [state])
        # A one-time hand-off secret: only the browser Apple posted from learns it.
        self.assertGreaterEqual(len(sent['h'][0]), 32)
        self.assertNotSignedIn(posted)
        self.assertEqual(self.apple_calls, [])

    def test_a_relay_address_is_the_contact_address_and_is_marked(self):
        res = self.apple_sign_in(user=json.dumps({'name': {'firstName': 'Ana', 'lastName': 'Pérez'}}))

        self.assertLanded(res, '/auth', social='apple')
        self.assertSignedIn(res)
        identity = ExternalIdentity.objects.get(provider='apple', subject='001234.abcdef.0987')
        user = identity.user
        self.assertEqual(user.email, 'x7k2@privaterelay.appleid.com')
        self.assertEqual((user.first_name, user.last_name), ('Ana', 'Pérez'))
        self.assertIsNotNone(UserProfile.objects.get(user=user).email_verified_at)
        self.assertTrue(social_identity.is_private_relay(user.email))
        self.assertFalse(social_identity.is_private_relay('ana@example.com'))

    def test_the_token_endpoint_gets_a_signed_es256_client_secret(self):
        self.apple_sign_in()
        url, data = self.apple_calls[0]
        self.assertEqual(url, 'https://appleid.apple.com/auth/token')
        self.assertEqual(data['grant_type'], 'authorization_code')
        self.assertEqual(data['client_id'], SERVICES_ID)
        self.assertEqual(data['code'], 'apple-code')
        self.assertEqual(data['redirect_uri'], f'{FRONTEND}/api/auth/apple/callback/')
        secret = data['client_secret']
        self.assertEqual(jwt.get_unverified_header(secret), {'alg': 'ES256', 'kid': KEY_ID, 'typ': 'JWT'})
        claims = jwt.decode(secret, APPLE_KEY.public_key(), algorithms=['ES256'],
                            audience='https://appleid.apple.com')
        self.assertEqual((claims['iss'], claims['sub']), (TEAM_ID, SERVICES_ID))
        self.assertLessEqual(claims['exp'] - claims['iat'], 600)

    def test_coming_back_without_an_email_is_the_same_account(self):
        self.apple_sign_in()
        self.client = APIClient()
        self.apple_claims = {'email': None, 'email_verified': None, 'is_private_email': None}
        self.assertLanded(self.apple_sign_in(), '/auth', social='apple')
        self.assertEqual(User.objects.count(), 1)

    def test_a_first_sign_in_without_an_email_still_gets_an_account(self):
        self.apple_claims = {'email': None, 'email_verified': None, 'is_private_email': None}
        self.assertLanded(self.apple_sign_in(), '/auth', social='apple')
        self.assertEqual(ExternalIdentity.objects.get(provider='apple').user.email, '')

    def test_an_address_apple_did_not_verify_is_not_used(self):
        self.apple_claims = {'email': 'ana@example.com', 'email_verified': 'false', 'is_private_email': None}
        self.assertLanded(self.apple_sign_in(), '/auth', social='apple')
        self.assertEqual(ExternalIdentity.objects.get(provider='apple').user.email, '')

    def test_the_posted_id_token_and_user_email_are_not_believed(self):
        self.nonce = 'cualquiera'
        forged = self.apple_token(key=OTHER_KEY, sub='otra-persona', email='victima@example.com')
        res = self.apple_sign_in(id_token=forged, user=json.dumps({'email': 'victima@example.com'}))
        self.assertLanded(res, '/auth', social='apple')
        user = ExternalIdentity.objects.get(provider='apple').user
        self.assertEqual(ExternalIdentity.objects.get(provider='apple').subject, '001234.abcdef.0987')
        self.assertNotEqual(user.email, 'victima@example.com')

    def test_the_person_said_no_at_apple(self):
        state = self.apple_start()['state']
        res = self.follow(self.apple_post(state, code='', error='user_cancelled_authorize'))
        self.assertLanded(res, '/auth', social_error='cancelled', provider='apple')
        self.assertEqual(self.apple_calls, [])

    def test_a_form_post_for_an_unknown_state_lands_on_the_refusal(self):
        posted = self.apple_post('nadie-lo-emitio')
        self.assertLanded(posted, '/auth', social_error='refused')

    def test_only_the_first_form_post_of_an_attempt_counts(self):
        state = self.apple_start()['state']
        first = self.apple_post(state, code='primero')
        # A second post for the same attempt gets no way back in.
        self.assertLanded(self.apple_post(state, code='segundo'), '/auth', social_error='refused')
        self.follow(first)
        self.assertEqual(self.apple_calls[0][1]['code'], 'primero')

    def test_a_replayed_return_is_refused_and_the_code_is_exchanged_once(self):
        state = self.apple_start()['state']
        posted = self.apple_post(state)
        self.assertLanded(self.follow(posted), '/auth', social='apple')
        self.assertLanded(self.follow(posted), '/auth', social_error='refused')
        self.assertEqual(len(self.apple_calls), 1)

    def test_a_return_without_the_form_post_is_refused(self):
        state = self.apple_start()['state']
        self.assertLanded(self.client.get(APPLE_CALLBACK, {'state': state}), '/auth', social_error='refused')
        self.assertEqual(self.apple_calls, [])

    def test_a_return_in_another_browser_is_refused(self):
        attacker = APIClient()
        state = self.apple_start(client=attacker)['state']
        self.apple_post(state, client=attacker)
        res = self.client.get(APPLE_CALLBACK, {'state': state})
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertEqual(self.apple_calls, [])


class AppleTokenTest(SocialBase):
    """The id_token from Apple's token endpoint, checked claim by claim."""

    def setUp(self):
        super().setUp()
        configure_apple()

    def refused(self, **claims):
        self.apple_claims = claims
        res = self.apple_sign_in()
        self.assertLanded(res, '/auth', social_error='refused', provider='apple')
        self.assertNotSignedIn(res)
        self.assertFalse(User.objects.exists())

    def test_another_nonce(self):
        self.refused(nonce='otro-nonce')

    def test_no_nonce(self):
        self.refused(nonce=None)

    def test_another_issuer(self):
        self.refused(iss='https://accounts.google.com')

    def test_another_audience(self):
        self.refused(aud='com.otra.app')

    def test_expired(self):
        self.refused(exp=int(time.time()) - 120, iat=int(time.time()) - 900)

    def test_no_subject(self):
        self.refused(sub=None)

    def test_signed_by_somebody_else(self):
        self.apple_claims = {}
        original = self._apple

        def forged(url, data):
            self.apple_calls.append((url, dict(data)))
            return {'id_token': self.apple_token(key=OTHER_KEY)}
        with mock.patch.object(apple_identity, '_post', side_effect=forged):
            res = self.apple_sign_in()
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertIsNotNone(original)

    def test_a_token_that_says_it_needs_no_signature(self):
        def unsigned(url, data):
            token = jwt.encode({'iss': 'https://appleid.apple.com', 'aud': SERVICES_ID, 'sub': 'x',
                                'exp': int(time.time()) + 600, 'nonce': self.nonce}, None, algorithm='none')
            return {'id_token': token}
        with mock.patch.object(apple_identity, '_post', side_effect=unsigned):
            self.assertLanded(self.apple_sign_in(), '/auth', social_error='refused')
        self.assertFalse(User.objects.exists())
        # The algorithm is refused before any key is fetched.
        apple_identity._signing_key.assert_not_called()

    def test_a_token_signed_with_a_shared_secret(self):
        def hmac_signed(url, data):
            token = jwt.encode({'iss': 'https://appleid.apple.com', 'aud': SERVICES_ID, 'sub': 'x',
                                'exp': int(time.time()) + 600, 'nonce': self.nonce}, 'secreto' * 8,
                               algorithm='HS256')
            return {'id_token': token}
        with mock.patch.object(apple_identity, '_post', side_effect=hmac_signed):
            self.assertLanded(self.apple_sign_in(), '/auth', social_error='refused')
        self.assertFalse(User.objects.exists())
        # The algorithm is refused before any key is fetched.
        apple_identity._signing_key.assert_not_called()

    def test_apple_refusing_the_code(self):
        with mock.patch.object(apple_identity, '_post', return_value={'error': 'invalid_grant'}):
            self.assertLanded(self.apple_sign_in(), '/auth', social_error='refused')

    def test_apple_unreachable(self):
        with mock.patch.object(apple_identity, '_post', side_effect=social_identity.SocialAuthError()):
            self.assertLanded(self.apple_sign_in(), '/auth', social_error='refused')


# ---------------------------------------------------------------------------------
# Existing accounts and privileges
# ---------------------------------------------------------------------------------

class ExistingAccountTest(SocialBase):
    def setUp(self):
        super().setUp()
        configure_facebook()
        configure_apple()
        self.ana = User.objects.create_user('ana', 'Ana@Example.com', 'clave-segura-123')

    def test_a_facebook_email_that_already_has_an_account_is_never_linked_by_itself(self):
        res = self.facebook_sign_in()
        self.assertLanded(res, '/auth', social_error='link_required', provider='facebook')
        self.assertNotSignedIn(res)
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertEqual(User.objects.count(), 1)

    def test_an_apple_email_that_already_has_an_account_is_never_linked_by_itself(self):
        self.apple_claims = {'email': 'ana@example.com', 'is_private_email': None}
        res = self.apple_sign_in()
        self.assertLanded(res, '/auth', social_error='link_required', provider='apple')
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_the_account_password_still_works_after_a_refused_social_attempt(self):
        self.facebook_sign_in()
        res = APIClient().post('/api/auth/login/', {'username': 'ana', 'password': 'clave-segura-123'},
                               format='json')
        self.assertEqual(res.status_code, 200)


class PrivilegeTest(SocialBase):
    """Facebook and Apple open buyer accounts. Nothing more, ever."""

    def setUp(self):
        super().setUp()
        configure_facebook()

    def linked(self, user):
        ExternalIdentity.objects.create(user=user, provider='facebook', subject='1001')
        return user

    def assertKeptOut(self, user):
        self.linked(user)
        res = self.facebook_sign_in()
        self.assertLanded(res, '/auth', social_error='not_allowed', provider='facebook')
        self.assertNotSignedIn(res)

    def test_a_staff_account_cannot_be_entered_through_facebook(self):
        self.assertKeptOut(User.objects.create_user('staff', password='x' * 12, is_staff=True))

    def test_a_superuser_cannot_be_entered_through_facebook(self):
        self.assertKeptOut(User.objects.create_superuser('master', 'm@example.com', 'x' * 12))

    def test_a_member_of_a_company_cannot_be_entered_through_facebook(self):
        user = User.objects.create_user('vendedora', password='x' * 12)
        company = Company.objects.create(name='Tienda', slug='tienda-social')
        Membership.objects.create(user=user, company=company, role='sales')
        self.assertKeptOut(user)

    def test_an_inactive_membership_still_counts(self):
        user = User.objects.create_user('exvendedora', password='x' * 12)
        company = Company.objects.create(name='Tienda', slug='tienda-social-2')
        Membership.objects.create(user=user, company=company, role='sales', is_active=False)
        self.assertKeptOut(user)

    def test_an_internal_role_without_a_membership_cannot_be_entered_either(self):
        user = User.objects.create_user('inventario', password='x' * 12)
        UserProfile.objects.update_or_create(user=user, defaults={'role': UserProfile.ROLE_INVENTORY})
        self.assertKeptOut(user)

    def test_an_account_switched_off_does_not_come_back(self):
        user = self.linked(User.objects.create_user('ana', password='x' * 12))
        user.is_active = False
        user.save()
        res = self.facebook_sign_in()
        self.assertLanded(res, '/auth', social_error='inactive')
        self.assertNotSignedIn(res)

    def test_the_new_account_is_a_buyer_and_nothing_else(self):
        companies = Company.objects.count()
        self.facebook_sign_in()
        user = ExternalIdentity.objects.get().user
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertFalse(Membership.objects.filter(user=user).exists())
        self.assertEqual(Company.objects.count(), companies)
        role = UserProfile.objects.filter(user=user).values_list('role', flat=True).first()
        self.assertIn(role, (None, UserProfile.ROLE_CUSTOMER))
        me = self.client.get('/api/auth/me/').json()
        self.assertFalse(me['is_staff'])
        self.assertEqual(me['role'], UserProfile.ROLE_CUSTOMER)
        self.assertEqual(self.client.get('/api/admin/integrations/').status_code, 403)

    def test_is_privileged(self):
        buyer = User.objects.create_user('compradora', password='x' * 12)
        self.assertFalse(social_identity.is_privileged(buyer))
        self.assertTrue(social_identity.is_privileged(User(is_staff=True)))
        self.assertTrue(social_identity.is_privileged(User(is_superuser=True)))


# ---------------------------------------------------------------------------------
# «Mi cuenta»: linking and unlinking
# ---------------------------------------------------------------------------------

class LinkTest(SocialBase):
    def setUp(self):
        super().setUp()
        configure_facebook()
        configure_apple()
        self.ana = User.objects.create_user('ana', 'ana@example.com', 'clave-segura-123')

    def sign_in_with_password(self, client=None, username='ana', password='clave-segura-123'):
        client = client or self.client
        res = client.post('/api/auth/login/', {'username': username, 'password': password}, format='json')
        self.assertEqual(res.status_code, 200)
        return client

    def start_link(self, provider='facebook', password='clave-segura-123'):
        return self.client.post(link_url(provider), {'password': password}, format='json')

    def test_linking_needs_a_session(self):
        self.assertEqual(self.start_link().status_code, 401)

    def test_linking_needs_the_current_password(self):
        self.sign_in_with_password()
        res = self.start_link(password='otra')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'password')

    def test_linking_is_csrf_protected_like_every_cookie_session_write(self):
        client = APIClient(enforce_csrf_checks=True)
        self.sign_in_with_password(client)
        self.assertEqual(client.post(link_url('facebook'), {'password': 'clave-segura-123'},
                                     format='json').status_code, 403)

    def test_linking_facebook_from_my_account(self):
        self.sign_in_with_password()
        res = self.start_link()
        self.assertEqual(res.status_code, 200, res.content)
        authorize = urlparse(res.json()['authorize_url'])
        self.assertEqual(authorize.netloc, 'www.facebook.com')
        state = parse_qs(authorize.query)['state'][0]
        back = self.facebook_callback(state)
        self.assertLanded(back, '/account', linked='facebook')
        identity = ExternalIdentity.objects.get(provider='facebook')
        self.assertEqual(identity.user, self.ana)
        # The address of the account is not touched by Facebook's.
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.email, 'ana@example.com')

    def test_linking_apple_from_my_account(self):
        self.sign_in_with_password()
        authorize = urlparse(self.start_link('apple').json()['authorize_url'])
        sent = {key: values[0] for key, values in parse_qs(authorize.query).items()}
        self.nonce = sent['nonce']
        back = self.follow(self.apple_post(sent['state']))
        self.assertLanded(back, '/account', linked='apple')
        self.assertEqual(ExternalIdentity.objects.get(provider='apple').user, self.ana)

    def test_after_linking_facebook_opens_that_account(self):
        self.sign_in_with_password()
        state = parse_qs(urlparse(self.start_link().json()['authorize_url']).query)['state'][0]
        self.facebook_callback(state)
        self.client = APIClient()
        res = self.facebook_sign_in()
        self.assertLanded(res, '/auth', social='facebook')
        self.assertEqual(self.client.get('/api/auth/me/').json()['username'], 'ana')

    def test_an_identity_already_linked_to_somebody_else_is_not_moved(self):
        other = User.objects.create_user('otro', password='x' * 12)
        ExternalIdentity.objects.create(user=other, provider='facebook', subject='1001')
        self.sign_in_with_password()
        state = parse_qs(urlparse(self.start_link().json()['authorize_url']).query)['state'][0]
        self.assertLanded(self.facebook_callback(state), '/account', link_error='already_linked')
        self.assertEqual(ExternalIdentity.objects.get(provider='facebook').user, other)

    def test_an_account_already_linked_to_that_provider_is_told_so(self):
        ExternalIdentity.objects.create(user=self.ana, provider='facebook', subject='otro-id')
        self.sign_in_with_password()
        res = self.start_link()
        self.assertEqual(res.status_code, 409)

    def test_an_account_without_a_password_is_sent_to_set_one_first(self):
        self.ana.set_unusable_password()
        self.ana.save()
        self.client.force_authenticate(self.ana)
        res = self.start_link()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['code'], 'password_required')

    def test_a_privileged_account_cannot_link_facebook(self):
        self.ana.is_staff = True
        self.ana.save()
        self.sign_in_with_password()
        self.assertEqual(self.start_link().status_code, 403)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_an_account_that_became_staff_meanwhile_is_not_linked(self):
        self.sign_in_with_password()
        state = parse_qs(urlparse(self.start_link().json()['authorize_url']).query)['state'][0]
        User.objects.filter(pk=self.ana.pk).update(is_staff=True)
        self.assertLanded(self.facebook_callback(state), '/account', link_error='not_allowed')
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_google_is_not_linked_from_here(self):
        self.sign_in_with_password()
        self.assertEqual(self.start_link('google').status_code, 404)

    def test_a_link_attempt_cannot_be_finished_as_a_sign_in_by_another_browser(self):
        self.sign_in_with_password()
        state = parse_qs(urlparse(self.start_link().json()['authorize_url']).query)['state'][0]
        stranger = APIClient()
        res = self.facebook_callback(state, client=stranger)
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_a_link_attempt_needs_the_same_account_still_signed_in(self):
        self.sign_in_with_password()
        state = parse_qs(urlparse(self.start_link().json()['authorize_url']).query)['state'][0]
        self.client.post('/api/auth/logout/')
        res = self.facebook_callback(state)
        self.assertLanded(res, '/account', link_error='refused')
        self.assertFalse(ExternalIdentity.objects.exists())


class IdentitiesTest(SocialBase):
    def setUp(self):
        super().setUp()
        configure_facebook()
        self.ana = User.objects.create_user('ana', 'ana@example.com', 'clave-segura-123')
        self.client.force_authenticate(self.ana)

    def test_the_list_needs_a_session(self):
        self.assertEqual(APIClient().get(IDENTITIES).status_code, 401)

    def test_the_list_says_what_is_linked_and_what_can_be(self):
        ExternalIdentity.objects.create(user=self.ana, provider='facebook', subject='1001',
                                        email_at_link='ana@example.com')
        body = self.client.get(IDENTITIES).json()
        self.assertTrue(body['has_password'])
        rows = {row['provider']: row for row in body['providers']}
        self.assertEqual(set(rows), {'google', 'facebook', 'apple'})
        self.assertTrue(rows['facebook']['linked'])
        self.assertTrue(rows['facebook']['enabled'])
        self.assertFalse(rows['apple']['linked'])
        self.assertFalse(rows['apple']['enabled'])
        self.assertNotIn('1001', json.dumps(body))

    def test_another_persons_identities_are_not_listed(self):
        other = User.objects.create_user('otro', password='x' * 12)
        ExternalIdentity.objects.create(user=other, provider='facebook', subject='1001')
        rows = {row['provider']: row for row in self.client.get(IDENTITIES).json()['providers']}
        self.assertFalse(rows['facebook']['linked'])

    def test_unlinking_when_a_password_remains(self):
        ExternalIdentity.objects.create(user=self.ana, provider='facebook', subject='1001')
        res = self.client.delete(IDENTITIES + 'facebook/')
        self.assertEqual(res.status_code, 204)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_unlinking_the_only_way_in_is_refused(self):
        self.ana.set_unusable_password()
        self.ana.save()
        ExternalIdentity.objects.create(user=self.ana, provider='facebook', subject='1001')
        res = self.client.delete(IDENTITIES + 'facebook/')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()['code'], 'last_way_in')
        self.assertTrue(ExternalIdentity.objects.exists())

    def test_unlinking_one_of_two_providers_without_a_password(self):
        self.ana.set_unusable_password()
        self.ana.save()
        ExternalIdentity.objects.create(user=self.ana, provider='facebook', subject='1001')
        ExternalIdentity.objects.create(user=self.ana, provider='google', subject='g-1')
        self.assertEqual(self.client.delete(IDENTITIES + 'facebook/').status_code, 204)
        self.assertEqual(self.client.delete(IDENTITIES + 'google/').status_code, 409)

    def test_unlinking_what_is_not_linked(self):
        self.assertEqual(self.client.delete(IDENTITIES + 'facebook/').status_code, 404)
        self.assertEqual(self.client.delete(IDENTITIES + 'twitter/').status_code, 404)

    def test_only_ones_own_identity_is_unlinked(self):
        other = User.objects.create_user('otro', password='x' * 12)
        ExternalIdentity.objects.create(user=other, provider='facebook', subject='1001')
        self.assertEqual(self.client.delete(IDENTITIES + 'facebook/').status_code, 404)
        self.assertTrue(ExternalIdentity.objects.filter(user=other).exists())

    def test_a_relay_address_is_flagged_for_the_screen(self):
        self.ana.email = 'abc@privaterelay.appleid.com'
        self.ana.save()
        self.assertTrue(self.client.get(IDENTITIES).json()['email_is_private_relay'])


class SessionTest(SocialBase):
    def setUp(self):
        super().setUp()
        configure_facebook()

    def test_logout_ends_a_facebook_session_like_any_other(self):
        res = self.facebook_sign_in()
        self.assertSignedIn(res)
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)
        self.client.post('/api/auth/logout/')
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)

    def test_the_attempt_cookie_is_removed_when_the_attempt_ends(self):
        res = self.facebook_sign_in()
        self.assertEqual(res.cookies[social_identity.ATTEMPT_COOKIE].value, '')

    def test_the_response_is_never_cached(self):
        res = self.client.get(FB_START)
        self.assertIn('no-store', res['Cache-Control'])


# ---------------------------------------------------------------------------------
# The console (Configuración › Integraciones), master only
# ---------------------------------------------------------------------------------

class ConsoleTest(SocialBase):
    def setUp(self):
        super().setUp()
        self.master = User.objects.create_superuser('master', 'master@example.com', 'x' * 12)
        self.console = APIClient()
        self.console.force_authenticate(self.master)

    def save(self, provider_id, public, secrets):
        return self.console.put(f'/api/admin/integrations/{provider_id}/draft/',
                                {'public': public, 'secrets': secrets}, format='json')

    def test_facebook_needs_a_numeric_app_id_and_a_version_of_the_right_shape(self):
        res = self.save('facebook', {'app_id': 'abc', 'graph_version': '23'}, {'app_secret': APP_SECRET})
        self.assertEqual(res.status_code, 400, res.content)
        body = json.dumps(res.json())
        self.assertIn('app_id', body)
        self.assertIn('graph_version', body)
        self.assertNotIn(APP_SECRET, body)

    def test_apple_refuses_a_key_that_cannot_sign_es256(self):
        rsa_pem = OTHER_KEY.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ).decode()
        res = self.save('apple', {'services_id': SERVICES_ID, 'team_id': 'corto', 'key_id': KEY_ID},
                        {'private_key': rsa_pem})
        self.assertEqual(res.status_code, 400, res.content)
        body = json.dumps(res.json())
        self.assertIn('team_id', body)
        self.assertIn('private_key', body)
        self.assertNotIn('PRIVATE KEY', body)

    def test_the_secrets_are_sealed_and_never_shown_again(self):
        self.assertEqual(self.save('facebook', {'app_id': APP_ID}, {'app_secret': APP_SECRET}).status_code, 200)
        self.assertEqual(self.save('apple', {'services_id': SERVICES_ID, 'team_id': TEAM_ID, 'key_id': KEY_ID},
                                   {'private_key': APPLE_KEY_PEM}).status_code, 200)
        for provider_id in ('facebook', 'apple'):
            shown = json.dumps(self.console.get(f'/api/admin/integrations/{provider_id}/').json())
            self.assertNotIn(APP_SECRET, shown)
            self.assertNotIn('PRIVATE KEY', shown)
        for row in IntegrationConfig.objects.all():
            self.assertNotIn(APP_SECRET, row.sealed_secrets)
            self.assertNotIn('PRIVATE KEY', row.sealed_secrets)

    def test_a_buyer_cannot_touch_the_console(self):
        buyer = User.objects.create_user('compradora', password='x' * 12)
        client = APIClient()
        client.force_authenticate(buyer)
        self.assertEqual(client.get('/api/admin/integrations/facebook/').status_code, 403)

    def test_the_facebook_test_asks_facebook_for_an_app_token(self):
        self.save('facebook', {'app_id': APP_ID}, {'app_secret': APP_SECRET})
        with mock.patch.object(facebook_identity, '_get', return_value={'access_token': 'app-token'}) as get:
            res = self.console.post('/api/admin/integrations/facebook/test/', {}, format='json')
        self.assertEqual(res.json()['status'], 'ok', res.content)
        self.assertEqual(get.call_args[0][1]['grant_type'], 'client_credentials')
        with mock.patch.object(facebook_identity, '_get', return_value={'error': {'message': 'x'}}):
            res = self.console.post('/api/admin/integrations/facebook/test/', {}, format='json')
        self.assertEqual(res.json()['status'], 'rejected')

    def test_the_apple_test_signs_a_client_secret(self):
        self.save('apple', {'services_id': SERVICES_ID, 'team_id': TEAM_ID, 'key_id': KEY_ID},
                  {'private_key': APPLE_KEY_PEM})
        with mock.patch.object(apple_identity, 'signing_keys_reachable', return_value=True):
            res = self.console.post('/api/admin/integrations/apple/test/', {}, format='json')
        self.assertEqual(res.json()['status'], 'unverified', res.content)


# ---------------------------------------------------------------------------------
# Review P1/P3: finishing somebody else's attempt
# ---------------------------------------------------------------------------------

class AttemptTheftTest(SocialBase):
    """
    The attacker starts an attempt in THEIR browser and gets the victim to
    authorize at the provider with it. Whatever the victim's browser does next,
    the attacker's browser — which holds the binding cookie — must not be able
    to finish it.
    """

    def setUp(self):
        super().setUp()
        configure_facebook()
        configure_apple()
        self.attacker = APIClient()
        self.victim = APIClient()

    def assertNothingOpened(self, res):
        self.assertNotSignedIn(res)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_apple_victim_follows_without_the_cookie_then_the_attacker_cannot_finish(self):
        state = self.apple_start(client=self.attacker)['state']
        posted = self.apple_post(state, client=self.victim)             # Apple, from the victim
        self.assertLanded(self.follow(posted, client=self.victim), '/auth', social_error='refused')
        res = self.attacker.get(APPLE_CALLBACK, {'state': state})
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertNothingOpened(res)
        # Even knowing the whole address the victim was sent to.
        self.assertLanded(self.follow(posted, client=self.attacker), '/auth', social_error='refused')
        self.assertEqual(self.apple_calls, [])
        self.assertFalse(User.objects.exists())

    def test_apple_victim_never_follows_and_the_attacker_cannot_finish(self):
        state = self.apple_start(client=self.attacker)['state']
        self.apple_post(state, client=self.victim)
        res = self.attacker.get(APPLE_CALLBACK, {'state': state})
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertNothingOpened(res)
        self.assertEqual(self.apple_calls, [])

    def test_apple_link_attempt_cannot_be_finished_by_its_owner_with_a_victims_code(self):
        mallory = User.objects.create_user('mallory', 'm@example.com', 'clave-segura-123')
        self.attacker.post('/api/auth/login/', {'username': 'mallory', 'password': 'clave-segura-123'},
                           format='json')
        authorize = urlparse(self.attacker.post(link_url('apple'), {'password': 'clave-segura-123'},
                                                format='json').json()['authorize_url'])
        sent = {key: values[0] for key, values in parse_qs(authorize.query).items()}
        self.nonce = sent['nonce']
        posted = self.apple_post(sent['state'], client=self.victim)
        self.follow(posted, client=self.victim)
        res = self.attacker.get(APPLE_CALLBACK, {'state': sent['state']})
        self.assertEqual(res.status_code, 302)
        # Refused before the attempt is even read: it was spent by the victim's return.
        self.assertIn('error=refused', res['Location'])
        self.assertFalse(ExternalIdentity.objects.filter(user=mallory).exists())

    def test_apple_link_attempt_without_the_victim_following(self):
        mallory = User.objects.create_user('mallory', 'm@example.com', 'clave-segura-123')
        self.attacker.post('/api/auth/login/', {'username': 'mallory', 'password': 'clave-segura-123'},
                           format='json')
        authorize = urlparse(self.attacker.post(link_url('apple'), {'password': 'clave-segura-123'},
                                                format='json').json()['authorize_url'])
        state = parse_qs(authorize.query)['state'][0]
        self.apple_post(state, client=self.victim)
        res = self.attacker.get(APPLE_CALLBACK, {'state': state})
        self.assertIn('error=refused', res['Location'])
        self.assertFalse(ExternalIdentity.objects.filter(user=mallory).exists())

    def test_facebook_a_cookieless_return_burns_the_attempt(self):
        state = self.facebook_start(client=self.attacker)['state']
        # Facebook sends the victim back with the victim's code; no cookie there.
        self.assertLanded(self.facebook_callback(state, client=self.victim, code='codigo-de-la-victima'),
                          '/auth', social_error='refused')
        res = self.facebook_callback(state, client=self.attacker, code='codigo-de-la-victima')
        self.assertLanded(res, '/auth', social_error='refused')
        self.assertNothingOpened(res)
        self.assertEqual(self.fb_calls, [])

    def test_a_link_started_by_one_account_is_not_finished_for_another(self):
        User.objects.create_user('ana', 'ana@example.com', 'clave-segura-123')
        User.objects.create_user('beto', 'beto@example.com', 'clave-segura-456')
        client = self.attacker
        client.post('/api/auth/login/', {'username': 'ana', 'password': 'clave-segura-123'}, format='json')
        authorize = client.post(link_url('facebook'), {'password': 'clave-segura-123'},
                                format='json').json()['authorize_url']
        state = parse_qs(urlparse(authorize).query)['state'][0]
        client.post('/api/auth/login/', {'username': 'beto', 'password': 'clave-segura-456'}, format='json')
        res = self.facebook_callback(state, client=client)
        self.assertLanded(res, '/account', link_error='refused')
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_a_link_survives_an_access_cookie_that_expired_at_the_provider(self):
        ana = User.objects.create_user('ana', 'ana@example.com', 'clave-segura-123')
        client = self.attacker
        client.post('/api/auth/login/', {'username': 'ana', 'password': 'clave-segura-123'}, format='json')
        authorize = client.post(link_url('facebook'), {'password': 'clave-segura-123'},
                                format='json').json()['authorize_url']
        state = parse_qs(urlparse(authorize).query)['state'][0]
        del client.cookies['blackdog_access']
        self.assertLanded(self.facebook_callback(state, client=client), '/account', linked='facebook')
        self.assertEqual(ExternalIdentity.objects.get().user, ana)


class LinkSessionTest(SocialBase):
    """Who is signed in when a link comes back: the access cookie, else the refresh one."""

    def setUp(self):
        super().setUp()
        configure_facebook()
        self.ana = User.objects.create_user('ana', 'ana@example.com', 'clave-segura-123')
        self.client.post('/api/auth/login/', {'username': 'ana', 'password': 'clave-segura-123'}, format='json')
        authorize = self.client.post(link_url('facebook'), {'password': 'clave-segura-123'},
                                     format='json').json()['authorize_url']
        self.state = parse_qs(urlparse(authorize).query)['state'][0]

    def test_a_refresh_revoked_elsewhere_does_not_carry_a_link(self):
        from store.token_revocation import revoke_all_tokens
        del self.client.cookies['blackdog_access']
        revoke_all_tokens(self.ana)              # «cerrar todas las sesiones», a password change
        self.assertLanded(self.facebook_callback(self.state), '/account', link_error='refused')
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_a_refresh_older_than_the_revocation_stamp_does_not_carry_a_link(self):
        # The stamp alone, as when the blacklist could not be written: the
        # refresh is still well signed and not blacklisted, and still refused.
        from django.utils import timezone
        del self.client.cookies['blackdog_access']
        UserProfile.objects.update_or_create(user=self.ana, defaults={'tokens_valid_after': timezone.now()})
        self.assertLanded(self.facebook_callback(self.state), '/account', link_error='refused')
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_a_revoked_access_cookie_is_not_overruled_by_the_refresh_one(self):
        from rest_framework_simplejwt.tokens import AccessToken
        from store.token_revocation import revoke_access_token
        revoke_access_token(self.ana, AccessToken(self.client.cookies['blackdog_access'].value))
        self.assertLanded(self.facebook_callback(self.state), '/account', link_error='refused')
        self.assertFalse(ExternalIdentity.objects.exists())


class ThrottleScopeTest(TestCase):
    def test_the_config_has_its_own_looser_scope_and_the_flow_stays_strict(self):
        from django.conf import settings as django_settings
        from store.social_views import SocialCallbackView, SocialConfigView, SocialStartView

        rates = django_settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        self.assertEqual(SocialConfigView.throttle_classes[0].scope, 'social_config')
        self.assertEqual(rates['social_config'], '120/min')
        for view in (SocialStartView, SocialCallbackView):
            self.assertEqual(view.throttle_classes[0].scope, 'social_sign_in')
        self.assertEqual(rates['social_sign_in'], '30/min')
