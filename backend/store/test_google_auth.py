"""
GOOGLE-AUTH — «Continuar con Google».

El navegador recibe de Google un ID token y lo entrega al backend. Aquí se
decide todo: se verifica la firma con las claves de Google, que el token es
PARA ESTA aplicación, que no venció, que nació de un intento iniciado en este
sitio y que el correo está verificado. Sólo entonces hay sesión, y la sesión es
la de siempre: cookies HttpOnly. Nada de Google se guarda salvo quién es.

    LA IDENTIDAD ES `sub`, NO EL CORREO.

Un correo se puede reasignar; el identificador de la cuenta de Google, no. Y un
correo que ya tiene cuenta aquí NO se enlaza solo: quien llega con Google tiene
que demostrar además que conoce la contraseña de esa cuenta.

Sin red: los tokens se firman con una clave de prueba y se parchea de dónde
sale la clave pública. Todo lo demás —audiencia, emisor, caducidad, nonce— lo
valida el código real.
"""
import time
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store import google_identity
from store.models import ExternalIdentity

User = get_user_model()

CLIENT_ID = 'prueba-123.apps.googleusercontent.com'
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

CONFIG = '/api/auth/google/config/'
LOGIN = '/api/auth/google/'
LINK = '/api/auth/google/link/'


@override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID)
class GoogleBase(TestCase):
    def setUp(self):
        cache.clear()
        patcher = mock.patch.object(google_identity, '_signing_key', return_value=KEY.public_key())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()

    def nonce(self):
        return self.client.get(CONFIG).json()['nonce']

    def token(self, key=KEY, algorithm='RS256', **claims):
        now = int(time.time())
        payload = {
            'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'sub': '109876543210',
            'email': 'ana@example.com', 'email_verified': True,
            'given_name': 'Ana', 'family_name': 'Pérez', 'iat': now, 'exp': now + 600,
        }
        if 'nonce' not in claims:
            payload['nonce'] = self.nonce()
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        return jwt.encode(payload, key, algorithm=algorithm, headers={'kid': 'prueba'})

    def sign_in(self, **claims):
        return self.client.post(LOGIN, {'credential': self.token(**claims)}, format='json')


class ConfigTest(GoogleBase):
    def test_the_button_is_offered_only_where_google_is_configured(self):
        body = self.client.get(CONFIG).json()
        self.assertTrue(body['enabled'])
        self.assertEqual(body['client_id'], CLIENT_ID)
        self.assertNotEqual(body['nonce'], self.client.get(CONFIG).json()['nonce'])

        with override_settings(GOOGLE_OAUTH_CLIENT_ID=''):
            self.assertEqual(self.client.get(CONFIG).json(), {'enabled': False})
            self.assertEqual(self.client.post(LOGIN, {'credential': 'x'}, format='json').status_code, 404)


class FirstSignInTest(GoogleBase):
    def test_a_new_person_gets_an_account_and_the_usual_cookie_session(self):
        res = self.sign_in()

        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['created'])
        user = User.objects.get(email='ana@example.com')
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.is_active)
        self.assertEqual((user.first_name, user.last_name), ('Ana', 'Pérez'))
        identity = ExternalIdentity.objects.get(user=user)
        self.assertEqual((identity.provider, identity.subject), ('google', '109876543210'))
        self.assertEqual(identity.email_at_link, 'ana@example.com')

        access = res.cookies['blackdog_access']
        self.assertTrue(access['httponly'])
        self.assertIn('blackdog_refresh', res.cookies)
        # La sesión va en cookies: el cuerpo no lleva ningún token, ni el de Google.
        text = res.content.decode()
        self.assertNotIn('eyJ', text)
        self.assertEqual(res.json()['user']['email'], 'ana@example.com')

    def test_nothing_from_google_is_kept_but_who_the_person_is(self):
        fields = {f.name for f in ExternalIdentity._meta.get_fields()}
        self.assertEqual(fields, {
            'id', 'user', 'provider', 'subject', 'email_at_link', 'created_at', 'last_login_at'})

    def test_coming_back_is_the_same_account_even_if_the_email_changed_at_google(self):
        self.sign_in()
        again = self.sign_in(email='ana.nueva@example.com')

        self.assertEqual(again.status_code, 200, again.content)
        self.assertFalse(again.json()['created'])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(ExternalIdentity.objects.count(), 1)
        self.assertIsNotNone(ExternalIdentity.objects.get().last_login_at)

    def test_two_people_with_generated_usernames_do_not_collide(self):
        self.sign_in()
        other = self.sign_in(sub='222', email='ana@otro.example')
        self.assertEqual(other.status_code, 200, other.content)
        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(len(set(User.objects.values_list('username', flat=True))), 2)

    def test_an_account_that_was_deactivated_does_not_come_back_through_google(self):
        self.sign_in()
        User.objects.update(is_active=False)

        res = self.sign_in()

        self.assertEqual(res.status_code, 403)
        self.assertNotIn('blackdog_access', res.cookies)


class RefusedTokenTest(GoogleBase):
    """Cada forma de token que no debe abrir nada. Todas responden igual."""

    def refused(self, res):
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json(), {'detail': 'No se pudo verificar la cuenta de Google.'})
        self.assertNotIn('blackdog_access', res.cookies)
        self.assertEqual(User.objects.count(), 0)

    def test_a_token_for_another_application(self):
        self.refused(self.sign_in(aud='otra-app.apps.googleusercontent.com'))

    def test_a_token_from_another_issuer(self):
        self.refused(self.sign_in(iss='https://evil.example'))

    def test_an_expired_token(self):
        self.refused(self.sign_in(exp=int(time.time()) - 3600, iat=int(time.time()) - 7200))

    def test_a_token_signed_by_somebody_else(self):
        self.refused(self.sign_in(key=OTHER_KEY))

    def test_a_token_that_says_it_needs_no_signature(self):
        unsigned = jwt.encode(
            {'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'sub': '1',
             'email': 'x@example.com', 'email_verified': True, 'nonce': self.nonce(),
             'exp': int(time.time()) + 600},
            key=None, algorithm='none')
        self.refused(self.client.post(LOGIN, {'credential': unsigned}, format='json'))

    def test_a_token_signed_with_the_public_key_as_a_shared_secret(self):
        """La confusión RS256/HS256: sólo se acepta RS256."""
        forged = jwt.encode(
            {'iss': 'https://accounts.google.com', 'aud': CLIENT_ID, 'sub': '1',
             'email': 'x@example.com', 'email_verified': True, 'nonce': self.nonce(),
             'exp': int(time.time()) + 600},
            key='secreto-cualquiera', algorithm='HS256')
        self.refused(self.client.post(LOGIN, {'credential': forged}, format='json'))

    def test_a_token_that_did_not_start_here(self):
        self.refused(self.sign_in(nonce=None))
        self.refused(self.sign_in(nonce='inventado'))

    def test_an_unverified_email(self):
        self.refused(self.sign_in(email_verified=False))

    def test_a_token_without_a_subject_or_an_email(self):
        self.refused(self.sign_in(sub=None))
        self.refused(self.sign_in(email=None))

    def test_garbage(self):
        for credential in ('', 'no-es-un-token', None, 12345):
            with self.subTest(credential):
                self.refused(self.client.post(LOGIN, {'credential': credential}, format='json'))

    def test_the_same_token_does_not_open_a_second_session(self):
        credential = self.token()
        self.assertEqual(self.client.post(LOGIN, {'credential': credential}, format='json').status_code, 200)

        replay = APIClient().post(LOGIN, {'credential': credential}, format='json')

        self.assertEqual(replay.status_code, 400)
        self.assertNotIn('blackdog_access', replay.cookies)


class ExistingAccountTest(GoogleBase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(username='ana', email='Ana@Example.com', password='Pass123!segura')

    def test_an_email_that_already_has_an_account_is_never_linked_by_itself(self):
        res = self.sign_in()

        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(res.json()['code'], 'link_required')
        self.assertNotIn('blackdog_access', res.cookies)
        self.assertEqual(ExternalIdentity.objects.count(), 0)
        self.assertEqual(User.objects.count(), 1)

    def test_proving_the_password_links_google_to_that_account_and_signs_in(self):
        credential = self.token()
        self.assertEqual(self.client.post(LOGIN, {'credential': credential}, format='json').status_code, 409)

        res = self.client.post(LINK, {'credential': credential, 'password': 'Pass123!segura'}, format='json')

        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn('blackdog_access', res.cookies)
        identity = ExternalIdentity.objects.get()
        self.assertEqual(identity.user, self.owner)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('Pass123!segura'))      # la contraseña sigue siendo suya

        # Desde entonces, Google abre esa cuenta sin más.
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_a_wrong_password_links_nothing(self):
        res = self.client.post(LINK, {'credential': self.token(), 'password': 'otra'}, format='json')

        self.assertEqual(res.status_code, 401)
        self.assertEqual(ExternalIdentity.objects.count(), 0)
        self.assertNotIn('blackdog_access', res.cookies)

    def test_linking_answers_the_same_whether_or_not_the_account_exists(self):
        missing = self.client.post(
            LINK, {'credential': self.token(email='nadie@example.com', sub='999'), 'password': 'x'}, format='json')
        wrong = self.client.post(LINK, {'credential': self.token(), 'password': 'x'}, format='json')

        self.assertEqual(missing.status_code, wrong.status_code)
        self.assertEqual(missing.json(), wrong.json())

    def test_an_email_shared_by_two_accounts_cannot_be_linked_by_guessing_which(self):
        User.objects.create_user(username='ana2', email='ana@example.com', password='Pass123!segura')

        self.assertEqual(self.sign_in().status_code, 409)
        res = self.client.post(LINK, {'credential': self.token(), 'password': 'Pass123!segura'}, format='json')

        self.assertEqual(res.status_code, 401)
        self.assertEqual(ExternalIdentity.objects.count(), 0)

    def test_a_google_account_already_linked_to_someone_is_not_linked_again(self):
        self.client.post(LINK, {'credential': self.token(), 'password': 'Pass123!segura'}, format='json')
        other = User.objects.create_user(username='beto', email='beto@example.com', password='Pass123!segura')

        res = self.client.post(
            LINK, {'credential': self.token(email='beto@example.com'), 'password': 'Pass123!segura'}, format='json')

        # El `sub` ya es de Ana: entra Ana, y a Beto no se le toca.
        self.assertFalse(ExternalIdentity.objects.filter(user=other).exists())
        self.assertEqual(ExternalIdentity.objects.count(), 1)
        self.assertIn(res.status_code, (200, 401))
