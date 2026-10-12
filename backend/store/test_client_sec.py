"""
CLIENT-SEC-01, phase 1 · who a buyer is, and which orders deserve a second look.

Four things, and all of them ADVICE unless a company says otherwise:

  * A VERIFIED E-MAIL IS A FACT OF ITS OWN (`UserProfile.email_verified_at`),
    written by the roads that prove somebody reads the mailbox and by nothing
    else. «Active» only says the account may sign in.
  * THE BUYER'S OWN PROFILE: name, phone and document, judged by one validator.
    A document that is somebody else's here is refused without saying whose.
  * EVERY ONLINE ORDER IS ASSESSED as it is created: which rules noticed
    something (their CODES, never the values), a level, and whether a person
    should look. A keyed hash of the address it came from; never the address.
  * A PERSON DECIDES, once. The decision is advice: it cancels nothing, refunds
    nothing and changes no payment. A company may hold «shipped» and
    «delivered» until somebody has looked; by default it does not.

With every option as it is created, checkout, payment and dispatch behave as
they did the release before.
"""
import json
import re
from datetime import timedelta
from importlib import import_module
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail as outbox_module
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from store import accounts
from store.models import AccountToken, ExternalIdentity, UserProfile
from store.test_google_auth import LINK as GOOGLE_LINK, GoogleBase
from store.test_staff_onboarding import _Base as InvitationBase

User = get_user_model()

PASSWORD = 'Clave-larga-de-prueba-9'
REGISTER = '/api/auth/register/'
WEB_LOGIN = '/api/auth/login/'
WEB_ME = '/api/auth/me/'
V1_LOGIN = '/api/v1/auth/login/'
V1_ME = '/api/v1/auth/me/'


def verified_at(user):
    """What the database says today, not what an instance remembers."""
    return UserProfile.objects.filter(user=user).values_list('email_verified_at', flat=True).first()


def web_session(user, password=PASSWORD):
    """A browser signed in as `user`: cookie session, CSRF as the frontend sends it."""
    cache.clear()
    client = APIClient()
    response = client.post(WEB_LOGIN, {'username': user.username, 'password': password}, format='json')
    assert response.status_code == 200, response.content
    return client


def v1_session(user, password=PASSWORD):
    """The app signed in as `user`."""
    cache.clear()
    client = APIClient()
    payload = client.post(V1_LOGIN, {'email': user.email, 'password': password}, format='json').json()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {payload['access']}")
    return client


# =============================================================================
# 1 · a verified e-mail is a fact of its own
# =============================================================================

@override_settings(
    REQUIRE_EMAIL_VERIFICATION=True, FRONTEND_URL='https://tienda.test',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class EmailVerifiedByEachRoadTest(TestCase):
    """Each road that proves the mailbox writes the fact; nothing else does."""

    def setUp(self):
        cache.clear()
        outbox_module.outbox = []
        self.client = APIClient()

    def register(self, username='ana', email='ana@correo.test'):
        cache.clear()
        response = self.client.post(REGISTER, {
            'username': username, 'email': email, 'first_name': 'Ana', 'last_name': 'Torres',
            'password': PASSWORD, 'password_confirm': PASSWORD,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return User.objects.get(username=username)

    def link_token(self) -> str:
        return re.search(r'verify-email\?token=([A-Za-z0-9_-]+)', outbox_module.outbox[-1].body).group(1)

    def code(self) -> str:
        return re.search(r'Tu código: (\d{6})\b', outbox_module.outbox[-1].body).group(1)

    def test_registering_proves_nothing(self):
        user = self.register()
        self.assertFalse(user.is_active)
        self.assertIsNone(verified_at(user))

    def test_the_link_proves_it(self):
        user = self.register()
        before = timezone.now()
        response = self.client.post('/api/auth/verify-email/', {'token': self.link_token()}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertGreaterEqual(verified_at(user), before)

    def test_the_code_proves_it(self):
        user = self.register()
        cache.clear()
        response = self.client.post(
            '/api/auth/verify-email/code/', json.dumps({'email': user.email, 'code': self.code()}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(verified_at(user))

    def test_a_wrong_code_proves_nothing(self):
        user = self.register()
        wrong = '000000' if self.code() != '000000' else '111111'
        cache.clear()
        self.client.post(
            '/api/auth/verify-email/code/', json.dumps({'email': user.email, 'code': wrong}),
            content_type='application/json',
        )
        self.assertIsNone(verified_at(user))

    def reset(self, user):
        cache.clear()
        self.client.post('/api/auth/password-reset/request/', {'email': user.email}, format='json')
        token = re.search(r'[?&]token=([A-Za-z0-9_\-]+)', outbox_module.outbox[-1].body).group(1)
        cache.clear()
        return self.client.post(
            '/api/auth/password-reset/confirm/', {'token': token, 'new_password': 'Otra-clave-larga-7'}, format='json',
        )

    def test_a_recovery_link_that_finishes_an_account_proves_it(self):
        user = self.register()
        self.assertEqual(self.reset(user).status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(verified_at(user))

    def test_the_first_proof_is_the_one_kept(self):
        user = self.register()
        self.client.post('/api/auth/verify-email/', {'token': self.link_token()}, format='json')
        first = verified_at(user)
        accounts.mark_email_verified(user)
        self.assertEqual(verified_at(user), first)

    @override_settings(REQUIRE_EMAIL_VERIFICATION=False)
    def test_an_account_made_without_verification_is_active_and_not_verified(self):
        user = self.register()
        self.assertTrue(user.is_active)
        self.assertIsNone(verified_at(user))


class EmailVerifiedByAnInvitationTest(InvitationBase):
    def test_an_invitation_for_this_address_proves_it(self):
        response = self.register(self.invite())
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNotNone(verified_at(User.objects.get(username='ana.torres')))

    def test_an_invitation_for_another_address_proves_nothing(self):
        raw = self.invite()
        response = self.register(raw, email='otra@empresa.test', username='otra')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()['requires_verification'])
        self.assertIsNone(verified_at(User.objects.get(username='otra')))

    @override_settings(REQUIRE_EMAIL_VERIFICATION=False)
    def test_it_proves_it_as_well_where_verification_is_not_required(self):
        self.register(self.invite())
        self.assertIsNotNone(verified_at(User.objects.get(username='ana.torres')))

    @override_settings(REQUIRE_EMAIL_VERIFICATION=False)
    def test_without_one_an_unrequired_registration_stays_unknown(self):
        self.register()
        self.assertIsNone(verified_at(User.objects.get(username='ana.torres')))


class EmailVerifiedByGoogleTest(GoogleBase):
    def test_a_first_sign_in_with_google_proves_it(self):
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertIsNotNone(verified_at(User.objects.get(email='ana@example.com')))

    def test_linking_google_to_an_account_proves_it(self):
        user = User.objects.create_user('ana', 'ana@example.com', PASSWORD)
        self.assertIsNone(verified_at(user))
        response = self.client.post(GOOGLE_LINK, {'credential': self.token(), 'password': PASSWORD}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(verified_at(user))

    def test_a_wrong_password_links_nothing_and_proves_nothing(self):
        user = User.objects.create_user('ana', 'ana@example.com', PASSWORD)
        response = self.client.post(GOOGLE_LINK, {'credential': self.token(), 'password': 'no-es'}, format='json')
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(verified_at(user))

    def test_google_proves_the_address_it_names_and_no_other(self):
        # The account's address was changed after it was linked: what Google
        # vouches for today is not the address this account has.
        user = User.objects.create_user('ana', 'distinta@example.com', PASSWORD)
        ExternalIdentity.objects.create(
            user=user, provider='google', subject='109876543210', email_at_link='ana@example.com',
        )
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertIsNone(verified_at(user))

    def test_a_later_sign_in_proves_an_account_that_was_still_unknown(self):
        user = User.objects.create_user('ana', 'ana@example.com', PASSWORD)
        ExternalIdentity.objects.create(
            user=user, provider='google', subject='109876543210', email_at_link='ana@example.com',
        )
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertIsNotNone(verified_at(user))


class EmailVerifiedIsReportedTest(TestCase):
    """An ADDED field on «who am I»; what was already there means what it meant."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('ana', 'ana@correo.test', PASSWORD)

    def test_the_web_answer_carries_it(self):
        client = web_session(self.user)
        self.assertIsNone(client.get(WEB_ME).json()['email_verified_at'])

        accounts.mark_email_verified(self.user)
        body = client.get(WEB_ME).json()
        self.assertEqual(parse_datetime(body['email_verified_at']), verified_at(self.user))
        self.assertEqual(body['email'], 'ana@correo.test')            # the rest is what it was

    def test_the_app_answer_carries_it_and_keeps_the_old_field_as_it_was(self):
        client = v1_session(self.user)
        body = client.get(V1_ME).json()['user']
        self.assertIsNone(body['email_verified_at'])
        # «Active» is still what `is_email_verified` says, verified or not.
        self.assertTrue(body['is_email_verified'])

        accounts.mark_email_verified(self.user)
        self.assertIsNotNone(client.get(V1_ME).json()['user']['email_verified_at'])


class EmailVerifiedWhenTheColumnArrivedTest(TestCase):
    """Migration 0118: what was already proven is copied; the rest stays unknown."""

    def arrive(self):
        from django.apps import apps

        import_module('store.migrations.0118_client_sec').record_verified_addresses(apps, None)

    def account(self, username, **extra):
        return User.objects.create_user(username, f'{username}@correo.test', PASSWORD, **extra)

    def token(self, user, *, used_at, purpose=AccountToken.PURPOSE_EMAIL_VERIFICATION):
        _raw, row = AccountToken.make(user, purpose, ttl_hours=24)
        AccountToken.objects.filter(pk=row.pk).update(used_at=used_at)
        return row

    def test_a_used_verification_link_of_an_active_account_is_copied(self):
        when = timezone.now() - timedelta(days=40)
        later = timezone.now() - timedelta(days=3)
        user = self.account('verificada')
        self.token(user, used_at=later)
        self.token(user, used_at=when)

        self.arrive()
        self.assertEqual(verified_at(user), when)                     # the first time it was proven

    def test_google_gives_the_day_it_was_linked(self):
        user = self.account('con_google')
        identity = ExternalIdentity.objects.create(
            user=user, provider='google', subject='g-1', email_at_link='CON_GOOGLE@correo.test',
        )
        self.arrive()
        self.assertEqual(verified_at(user), identity.created_at)

    def test_everybody_else_stays_unknown(self):
        plain = self.account('sin_prueba')                            # active because nobody asked
        waiting = self.account('esperando', is_active=False)
        self.token(waiting, used_at=None)                             # sent, never used
        switched_off = self.account('apagada', is_active=False)
        self.token(switched_off, used_at=timezone.now())              # proven once, not active today
        recovered = self.account('recuperada')
        self.token(recovered, used_at=timezone.now(), purpose=AccountToken.PURPOSE_PASSWORD_RESET)
        moved = self.account('mudada')                                # Google vouched for another address
        ExternalIdentity.objects.create(user=moved, provider='google', subject='g-2', email_at_link='antes@correo.test')

        self.arrive()
        for user in (plain, waiting, switched_off, recovered, moved):
            with self.subTest(user.username):
                self.assertIsNone(verified_at(user))

    def test_it_can_run_twice_and_keeps_what_is_already_written(self):
        user = self.account('verificada')
        self.token(user, used_at=timezone.now() - timedelta(days=2))
        known = timezone.now() - timedelta(days=90)
        UserProfile.objects.filter(user=user).update(email_verified_at=known)

        self.arrive()
        self.arrive()
        self.assertEqual(verified_at(user), known)

    def test_the_step_reads_no_live_code(self):
        import inspect

        source = inspect.getsource(import_module('store.migrations.0118_client_sec'))
        self.assertNotIn('from store', source)
        self.assertNotIn('import store', source)
