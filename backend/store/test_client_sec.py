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


# =============================================================================
# 2 · the buyer's own profile
# =============================================================================

from django.test import SimpleTestCase  # noqa: E402

from store import identity_validation as identity  # noqa: E402
from store.models import AdminAuditLog, Customer  # noqa: E402
from store.tests import _order, _saas_company  # noqa: E402

WEB_PROFILE = '/api/account/profile/'

# Made up, and right by the rule: the check digit of each RUC is the real one.
RUC_COMPANY = '20999999990'
RUC_PERSON = '10999999994'
DNI = '70000001'


def v1_profile(slug):
    return f'/api/v1/customer/{slug}/profile/'


class IdentityRulesTest(SimpleTestCase):
    """One judge for a phone and a document, with the strictest rules there were."""

    def test_a_peruvian_mobile_as_the_web_checkout_accepts_it(self):
        for typed, kept in (
            ('987654321', '987654321'), ('+51987654321', '+51987654321'),
            ('+51 987 654 321', '+51987654321'), (' 987 654 321 ', '987654321'),
            ('', ''),
        ):
            with self.subTest(typed):
                self.assertEqual(identity.clean_phone(typed), kept)

    def test_what_is_not_a_peruvian_mobile_is_refused(self):
        for typed in ('12345', '887654321', '9876543210', '+1 415 555 0100', 'novecientos', '98765432a', None, 987654321):
            with self.subTest(typed), self.assertRaises(identity.IdentityError):
                identity.clean_phone(typed)

    def test_a_document_is_judged_by_its_type(self):
        for kind, typed, kept in (
            ('dni', DNI, DNI), ('dni', f' {DNI} ', DNI),
            ('ruc', RUC_COMPANY, RUC_COMPANY), ('ruc', RUC_PERSON, RUC_PERSON),
            ('ce', 'ab12345', 'AB12345'), ('ce', '001234567890', '001234567890'),
            ('', '', ''),
        ):
            with self.subTest(kind=kind, typed=typed):
                self.assertEqual(identity.clean_document(kind, typed), (kind, kept))

    def test_a_document_of_the_wrong_shape_is_refused(self):
        for kind, typed in (
            ('dni', '7000000'), ('dni', '700000011'), ('dni', '7000000A'),
            ('ruc', '2099999999'), ('ruc', '209999999900'),
            ('ruc', '20999999991'),                       # eleven digits, wrong check digit
            ('ruc', '30999999997'),                       # right check digit, a prefix nobody is given
            ('ce', 'AB123'), ('ce', 'AB12345678901'), ('ce', 'AB-12345'),
            ('pasaporte', 'AB123456'),                    # not a type this platform knows
            ('dni', ''), ('', DNI),                       # one without the other
            ('dni', None), ('dni', 70000001),
        ):
            with self.subTest(kind=kind, typed=typed), self.assertRaises(identity.IdentityError):
                identity.clean_document(kind, typed)

    def test_the_check_digit_of_a_ruc(self):
        self.assertTrue(identity.ruc_check_digit_matches(RUC_COMPANY))
        self.assertTrue(identity.ruc_check_digit_matches('20000000001'))
        for wrong in ('20999999991', '20000000002', '2099999999', 'abcdefghijk'):
            with self.subTest(wrong):
                self.assertFalse(identity.ruc_check_digit_matches(wrong))


@override_settings(ALLOWED_HOSTS=['*'])
class _ProfileBase(TestCase):
    def setUp(self):
        cache.clear()
        self.a = _saas_company('Tienda A', 'perfil-a', tax_id='20999999990')
        self.b = _saas_company('Tienda B', 'perfil-b', tax_id='20111111112')
        self.user = User.objects.create_user('ana', 'ana@correo.test', PASSWORD, first_name='Ana', last_name='Torres')
        self.web = web_session(self.user)

    def host(self, company):
        return {'HTTP_HOST': f'{company.slug}.example.com'}

    def get(self, company=None, client=None):
        return (client or self.web).get(WEB_PROFILE, **self.host(company or self.a))

    def patch(self, body, company=None, client=None):
        cache.clear()                                   # the write limit is its own test
        return (client or self.web).patch(WEB_PROFILE, body, format='json', **self.host(company or self.a))

    def record(self, company=None, user=None):
        return Customer.objects.filter(company=company or self.a, user=user or self.user).first()


class OwnProfileTest(_ProfileBase):
    def test_it_is_for_whoever_is_signed_in(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get(WEB_PROFILE, **self.host(self.a)).status_code, 401)
        self.assertEqual(anonymous.patch(WEB_PROFILE, {'phone': '987654321'}, format='json', **self.host(self.a)).status_code, 401)
        self.assertEqual(Customer.objects.count(), 0)

    def test_reading_it_creates_nothing(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(AdminAuditLog.objects.filter(action='customer_profile_updated').count(), 0)
        # The account's own name is offered to start from; nothing else is known.
        self.assertEqual(body, {
            'email': 'ana@correo.test', 'email_verified_at': None, 'email_verification_available': False,
            'first_name': 'Ana', 'last_name': 'Torres', 'phone': '',
            'document_type': '', 'document_number': '',
        })
        self.assertIn('no-store', response['Cache-Control'])

    def test_the_first_save_creates_the_record_of_this_account_in_this_shop(self):
        response = self.patch({'phone': '+51 987 654 321', 'document_type': 'dni', 'document_number': DNI})
        self.assertEqual(response.status_code, 200, response.content)

        record = self.record()
        self.assertEqual((record.company, record.user, record.customer_type), (self.a, self.user, 'person'))
        self.assertEqual((record.first_name, record.last_name), ('Ana', 'Torres'))
        self.assertEqual((record.phone, record.document_type, record.document_number), ('+51987654321', 'dni', DNI))
        self.assertEqual(record.email, 'ana@correo.test')
        self.assertTrue(record.is_active)
        self.assertEqual(response.json()['phone'], '+51987654321')
        self.assertEqual(self.get().json()['document_number'], DNI)

    def test_saving_again_changes_the_same_record(self):
        self.patch({'phone': '987654321'})
        self.patch({'first_name': 'Ana María', 'phone': '912345678'})
        self.assertEqual(Customer.objects.filter(user=self.user).count(), 1)
        record = self.record()
        self.assertEqual((record.first_name, record.last_name, record.phone), ('Ana María', 'Torres', '912345678'))

    def test_only_what_was_sent_changes(self):
        self.patch({'phone': '987654321', 'document_type': 'dni', 'document_number': DNI})
        self.patch({'last_name': 'Torres Rojas'})
        record = self.record()
        self.assertEqual((record.phone, record.document_number, record.last_name), ('987654321', DNI, 'Torres Rojas'))

    def test_a_document_can_be_taken_off(self):
        self.patch({'document_type': 'dni', 'document_number': DNI})
        self.assertEqual(self.patch({'document_type': '', 'document_number': ''}).status_code, 200)
        record = self.record()
        self.assertEqual((record.document_type, record.document_number), ('', ''))

    def test_the_address_and_the_record_itself_are_not_the_buyers_to_write(self):
        other = User.objects.create_user('otro', 'otro@correo.test', PASSWORD)
        response = self.patch({
            'phone': '987654321', 'email': 'robada@correo.test', 'company': self.b.pk, 'company_id': self.b.pk,
            'user': other.pk, 'user_id': other.pk, 'is_active': False, 'notes': 'nota interna',
            'customer_type': 'business', 'business_name': 'Empresa inventada', 'email_verified_at': '2026-01-01T00:00:00Z',
        })
        self.assertEqual(response.status_code, 200, response.content)
        record = Customer.objects.get()
        self.assertEqual((record.company, record.user, record.customer_type), (self.a, self.user, 'person'))
        self.assertEqual((record.email, record.notes, record.business_name, record.is_active), ('ana@correo.test', '', '', True))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'ana@correo.test')
        self.assertIsNone(verified_at(self.user))
        self.assertEqual(response.json()['email'], 'ana@correo.test')

    def test_a_record_needs_a_name(self):
        nameless = User.objects.create_user('sin_nombre', 'sin@correo.test', PASSWORD)
        client = web_session(nameless)
        response = self.patch({'phone': '987654321'}, client=client)
        self.assertEqual(response.status_code, 400)
        self.assertIn('first_name', response.json())
        self.assertIsNone(self.record(user=nameless))

        self.assertEqual(self.patch({'first_name': '  ', 'last_name': ''}).status_code, 400)

    def test_what_is_refused_says_which_field_and_saves_nothing(self):
        self.patch({'phone': '987654321'})
        for body, field in (
            ({'phone': '12345'}, 'phone'),
            ({'document_type': 'dni', 'document_number': '7000000'}, 'document_number'),
            ({'document_type': 'ruc', 'document_number': '20999999991'}, 'document_number'),
            ({'document_type': 'ce', 'document_number': 'AB123'}, 'document_number'),
            ({'document_type': 'pasaporte', 'document_number': 'AB123456'}, 'document_type'),
            ({'document_number': DNI}, 'document_type'),
            ({'document_type': 'dni'}, 'document_number'),
            ({'first_name': 'x' * 121}, 'first_name'),
        ):
            with self.subTest(body):
                response = self.patch({**body, 'last_name': 'No guardado'})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
                record = self.record()
                self.assertEqual((record.phone, record.last_name, record.document_number), ('987654321', 'Torres', ''))

    def test_a_ruc_and_a_foreigners_card_are_accepted(self):
        self.assertEqual(self.patch({'document_type': 'ruc', 'document_number': RUC_PERSON}).status_code, 200)
        self.assertEqual(self.patch({'document_type': 'ce', 'document_number': 'ab12345'}).status_code, 200)
        self.assertEqual(self.record().document_number, 'AB12345')

    def test_it_says_whether_the_address_is_verified(self):
        accounts.mark_email_verified(self.user)
        body = self.get().json()
        self.assertEqual(parse_datetime(body['email_verified_at']), verified_at(self.user))
        # The existing «send it again» only serves an account that is not active
        # yet, and whoever is signed in is: there is nothing to offer here.
        self.assertFalse(body['email_verification_available'])

    def test_an_archived_record_is_still_the_buyers_own_and_stays_archived(self):
        self.patch({'phone': '987654321'})
        Customer.objects.filter(user=self.user).update(is_active=False, notes='cerrado por la tienda')
        body = self.get().json()
        self.assertEqual(body['phone'], '987654321')
        self.assertNotIn('is_active', body)
        self.assertNotIn('cerrado', json.dumps(body))
        self.assertEqual(self.patch({'phone': '912345678'}).status_code, 200)
        record = self.record()
        self.assertEqual((record.phone, record.is_active, record.notes), ('912345678', False, 'cerrado por la tienda'))

    def test_a_shop_that_cannot_be_named_has_no_profile(self):
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG='', DEBUG=False):
            response = self.web.get(WEB_PROFILE, HTTP_HOST='nadie.example.com')
            self.assertEqual(response.status_code, 404)
            cache.clear()
            response = self.web.patch(WEB_PROFILE, {'phone': '987654321'}, format='json', HTTP_HOST='nadie.example.com')
            self.assertEqual(response.status_code, 404)
        self.assertEqual(Customer.objects.count(), 0)


class ProfileOfOneShopTest(_ProfileBase):
    """The shop is the one being looked at. Its file on a person is its own."""

    def test_each_shop_keeps_its_own_record(self):
        self.patch({'phone': '987654321', 'document_type': 'dni', 'document_number': DNI}, company=self.a)
        self.assertEqual(self.get(company=self.b).json()['phone'], '')
        self.assertEqual(self.get(company=self.b).json()['document_number'], '')

        self.patch({'phone': '912345678'}, company=self.b)
        self.assertEqual(self.record(self.a).phone, '987654321')
        self.assertEqual(self.record(self.b).phone, '912345678')
        self.assertEqual(Customer.objects.filter(user=self.user).count(), 2)

    def test_a_document_held_in_another_shop_is_free_here(self):
        Customer.objects.create(company=self.b, first_name='Otra', document_type='dni', document_number=DNI)
        self.assertEqual(self.patch({'document_type': 'dni', 'document_number': DNI}, company=self.a).status_code, 200)

    def test_another_buyer_never_reads_or_writes_this_one(self):
        self.patch({'phone': '987654321', 'document_type': 'dni', 'document_number': DNI})
        other = User.objects.create_user('otro', 'otro@correo.test', PASSWORD, first_name='Otro')
        client = web_session(other)
        body = self.get(client=client).json()
        self.assertEqual((body['email'], body['phone'], body['document_number']), ('otro@correo.test', '', ''))
        self.patch({'phone': '912345678'}, client=client)
        self.assertEqual(self.record().phone, '987654321')
        self.assertEqual(self.record(user=other).phone, '912345678')


class ProfileDocumentConflictTest(_ProfileBase):
    """A document that is somebody else's here: one sentence, whoever it is."""

    def refused(self, number, kind='dni'):
        return self.patch({'document_type': kind, 'document_number': number})

    def test_one_answer_for_every_kind_of_owner(self):
        owner = User.objects.create_user('duena', 'duena@correo.test', PASSWORD)
        with_account = Customer.objects.create(
            company=self.a, user=owner, first_name='Dueña', last_name='Con Cuenta',
            document_type='dni', document_number='70000002',
        )
        Customer.objects.create(company=self.a, first_name='Cliente', last_name='De Mostrador', document_type='dni', document_number='70000003')
        Customer.objects.create(
            company=self.a, first_name='Cliente', last_name='Archivado', document_type='dni',
            document_number='70000004', is_active=False,
        )

        answers = [self.refused(number) for number in ('70000002', '70000003', '70000004')]
        self.assertEqual({response.status_code for response in answers}, {400})
        self.assertEqual(len({response.content for response in answers}), 1)
        body = answers[0].json()
        self.assertEqual(body, {'document_number': [identity.DOCUMENT_REFUSED]})
        text = answers[0].content.decode()
        for leaked in ('Dueña', 'Con Cuenta', 'duena', 'cuenta', 'registrad', 'existe', str(with_account.pk)):
            self.assertNotIn(leaked, text)

        self.assertIsNone(self.record())                               # nothing was saved, not even the record
        self.assertEqual(AdminAuditLog.objects.filter(action='customer_profile_updated').count(), 0)

    def test_the_rest_of_a_refused_save_is_not_kept(self):
        Customer.objects.create(company=self.a, first_name='Otro', document_type='dni', document_number='70000002')
        self.patch({'phone': '987654321'})
        response = self.patch({'phone': '912345678', 'document_type': 'dni', 'document_number': '70000002'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.record().phone, '987654321')

    def test_my_own_document_is_not_a_conflict(self):
        self.patch({'document_type': 'dni', 'document_number': DNI})
        self.assertEqual(self.patch({'document_type': 'dni', 'document_number': DNI, 'phone': '987654321'}).status_code, 200)

    def test_the_same_number_under_another_type_is_another_document(self):
        Customer.objects.create(company=self.a, first_name='Otro', document_type='ce', document_number='70000002')
        self.assertEqual(self.refused('70000002').status_code, 200)

    def test_a_document_taken_while_this_save_was_being_made_gets_the_same_answer(self):
        from django.db import IntegrityError

        with mock.patch('store.customer_profile.Customer.save', side_effect=IntegrityError('unique_customer_document_per_company')):
            response = self.refused(DNI)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'document_number': [identity.DOCUMENT_REFUSED]})


class ProfileAuditTest(_ProfileBase):
    def entries(self):
        return list(AdminAuditLog.objects.filter(action='customer_profile_updated').order_by('pk'))

    def test_a_save_is_written_down_with_the_names_of_what_changed_and_no_values(self):
        self.patch({'phone': '987654321', 'document_type': 'dni', 'document_number': DNI})
        self.patch({'phone': '912345678', 'last_name': 'Torres Rojas'})

        first, second = self.entries()
        record = self.record()
        self.assertEqual((first.actor, first.company, first.target_type, first.target_id), (self.user, self.a, 'customer', str(record.pk)))
        self.assertEqual(first.metadata, {
            'created': True,
            'changed_fields': ['document_number', 'document_type', 'first_name', 'last_name', 'phone'],
        })
        self.assertEqual(second.metadata, {'created': False, 'changed_fields': ['last_name', 'phone']})
        written = json.dumps([first.metadata, second.metadata])
        for value in ('987654321', '912345678', DNI, 'Torres', 'Ana', 'ana@correo.test'):
            self.assertNotIn(value, written)

    def test_a_save_that_changes_nothing_writes_nothing(self):
        self.patch({'phone': '987654321'})
        before = self.record().updated_at
        self.patch({'phone': '987654321', 'first_name': 'Ana'})
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.record().updated_at, before)

    def test_nothing_personal_reaches_a_log(self):
        with self.assertLogs(level='DEBUG') as captured:
            import logging
            logging.getLogger('store').debug('centinela')
            self.patch({'phone': '987654321', 'document_type': 'dni', 'document_number': DNI})
            Customer.objects.create(company=self.a, first_name='Otro', document_type='dni', document_number='70000002')
            self.patch({'document_type': 'dni', 'document_number': '70000002'})
        text = '\n'.join(captured.output)
        for value in ('987654321', DNI, '70000002', 'ana@correo.test'):
            self.assertNotIn(value, text)


class ProfileWriteLimitTest(_ProfileBase):
    def test_saving_is_limited_per_account_and_reading_is_not(self):
        from store.throttles import AccountProfileWriteThrottle

        limit = AccountProfileWriteThrottle().num_requests
        cache.clear()
        statuses = [
            self.web.patch(WEB_PROFILE, {'phone': '987654321'}, format='json', **self.host(self.a)).status_code
            for _ in range(limit)
        ]
        self.assertEqual(set(statuses), {200}, statuses)
        blocked = self.web.patch(WEB_PROFILE, {'phone': '912345678'}, format='json', **self.host(self.a))
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(self.record().phone, '987654321')
        self.assertEqual(self.get().status_code, 200)

        # Somebody else's budget is their own.
        other = User.objects.create_user('otro', 'otro@correo.test', PASSWORD, first_name='Otro')
        client = APIClient()
        client.force_authenticate(other)
        self.assertEqual(client.patch(WEB_PROFILE, {'phone': '987654321'}, format='json', **self.host(self.a)).status_code, 200)

    def test_the_app_spends_the_same_budget(self):
        from store.throttles import AccountProfileWriteThrottle
        from store.v1_customer_views import V1CustomerProfileView
        from store.account_views import AccountProfileView

        for view in (AccountProfileView, V1CustomerProfileView):
            instance = view()
            instance.request = mock.Mock(method='PATCH')
            self.assertEqual([type(t) for t in instance.get_throttles()], [AccountProfileWriteThrottle])


class ProfileFromTheAppTest(_ProfileBase):
    """The same profile through the customer surface of v1."""

    def setUp(self):
        super().setUp()
        # A client of shop A by the only proof there is before a profile: a purchase.
        _order(self.a, user=self.user, total='120.00', paid=True)
        self.app = v1_session(self.user)

    def app_patch(self, body, slug='perfil-a', client=None):
        cache.clear()
        return (client or self.app).patch(v1_profile(slug), body, format='json')

    def test_it_reads_and_writes_the_same_record_as_the_web(self):
        self.assertEqual(self.app.get(v1_profile('perfil-a')).json(), self.get().json())

        response = self.app_patch({'phone': '+51 987 654 321', 'document_type': 'dni', 'document_number': DNI})
        self.assertEqual(response.status_code, 200, response.content)
        record = self.record()
        self.assertEqual((record.phone, record.document_number, record.user), ('+51987654321', DNI, self.user))
        self.assertEqual(response.json(), self.get().json())

        entry = AdminAuditLog.objects.get(action='customer_profile_updated')
        self.assertEqual((entry.actor, entry.company), (self.user, self.a))
        self.assertTrue(entry.metadata['created'])

    def test_it_refuses_what_the_web_refuses_in_the_same_words(self):
        Customer.objects.create(company=self.a, first_name='Otro', document_type='dni', document_number='70000002')
        for body in (
            {'phone': '12345'},
            {'document_type': 'ruc', 'document_number': '20999999991'},
            {'document_type': 'dni', 'document_number': '70000002'},
            {'document_number': DNI},
        ):
            with self.subTest(body):
                app, web = self.app_patch(body), self.patch(body)
                self.assertEqual(app.status_code, 400)
                self.assertEqual(app.json(), web.json())
        self.assertIsNone(self.record())

    def test_whoever_is_not_a_client_of_the_shop_named_finds_nothing(self):
        self.assertEqual(self.app.get(v1_profile('perfil-b')).status_code, 404)
        self.assertEqual(self.app_patch({'phone': '987654321'}, slug='perfil-b').status_code, 404)
        self.assertEqual(self.app_patch({'phone': '987654321'}, slug='no-existe').status_code, 404)
        self.assertEqual(Customer.objects.count(), 0)

    def test_the_shop_is_the_one_in_the_path_whatever_else_is_sent(self):
        response = self.app_patch({'phone': '987654321', 'company': self.b.pk, 'company_slug': 'perfil-b'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Customer.objects.get().company, self.a)

    def test_only_a_bearer_session_opens_it(self):
        self.assertEqual(APIClient().get(v1_profile('perfil-a')).status_code, 401)
        self.assertEqual(self.web.get(v1_profile('perfil-a')).status_code, 401)

    def test_working_for_a_shop_is_not_being_its_client(self):
        from store.models import Membership

        staff = User.objects.create_user('empleada', 'empleada@correo.test', PASSWORD, first_name='Eva')
        Membership.objects.create(user=staff, company=self.a, role='sales')
        client = v1_session(staff)
        self.assertEqual(client.get(v1_profile('perfil-a')).status_code, 404)
        self.assertEqual(self.app_patch({'phone': '987654321'}, client=client).status_code, 404)


class ProfileFirstSaveTwiceTest(_ProfileBase):
    def test_a_double_tap_on_the_first_save_lands_on_the_one_record(self):
        from django.db import IntegrityError

        real_save, calls = Customer.save, []

        def racing(record, *args, **kwargs):
            calls.append(record)
            if len(calls) == 1:
                # The other tap got there first: its record exists now.
                Customer.objects.create(company=self.a, user=self.user, first_name='Ana', last_name='Torres')
                raise IntegrityError('unique_customer_per_user_per_company')
            return real_save(record, *args, **kwargs)

        with mock.patch('store.customer_profile.Customer.save', autospec=True, side_effect=racing):
            response = self.patch({'phone': '987654321'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Customer.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.record().phone, '987654321')


# =============================================================================
# 3 · every online order is assessed
# =============================================================================

from decimal import Decimal  # noqa: E402
from unittest.mock import patch  # noqa: E402

from django.test import RequestFactory  # noqa: E402

from store import order_risk as risk  # noqa: E402
from store.models import (  # noqa: E402
    CartItem, CompanySettings, Order, OrderRiskAssessment,
)
from store.tests import (  # noqa: E402
    FAKE_SESSION_TOKEN, IZIPAY_TEST_SETTINGS, SESSION_TOKEN_PATH,
    _c1_sale, _m5_stock, _p2d_branch, _p2d_member, _prod, _saas_branch,
)

WEB_CHECKOUT = '/api/payments/create-checkout-session/'
Level = OrderRiskAssessment.Level
Review = OrderRiskAssessment.ReviewStatus


def from_address(ip='203.0.113.10', agent='Navegador de prueba/1.0'):
    """The request an order arrived in, as far as an assessment cares."""
    return RequestFactory().post('/', REMOTE_ADDR=ip, HTTP_USER_AGENT=agent)


class _RiskBase(TestCase):
    def setUp(self):
        cache.clear()
        self.a = _saas_company('Tienda A', 'riesgo-a', tax_id='20999999990')
        self.b = _saas_company('Tienda B', 'riesgo-b', tax_id='20111111112')
        self.n = 0

    def buyer(self, username='compradora', *, verified=True):
        user = User.objects.create_user(username, f'{username}@correo.test', PASSWORD, first_name='Ana')
        if verified:
            accounts.mark_email_verified(user)
        return user

    def order(self, company=None, user=None, *, total='100.00', paid=False, **extra):
        """An online order as checkout leaves it: pending, with what the buyer typed."""
        self.n += 1
        extra.setdefault('customer_phone', f'9{self.n:08d}')
        extra.setdefault('customer_email', f'pedido{self.n}@correo.test')
        extra.setdefault('document_type', 'dni')
        extra.setdefault('document_number', f'{40000000 + self.n}')
        if user is None:
            extra.setdefault('cart_session_key', f'carrito-{self.n}')
        return _order(company or self.a, user=user, total=total, paid=paid, **extra)

    def assess(self, order, ip='203.0.113.10'):
        return risk.assess(order, from_address(ip) if ip else None)

    def returning(self, company=None, username='compradora'):
        """A verified buyer who has already paid for something here: nothing to notice."""
        user = self.buyer(username)
        self.order(company, user, paid=True)
        return user

    def amount(self, value, company=None):
        CompanySettings.objects.update_or_create(
            company=company or self.a, defaults={'risk_review_amount': Decimal(value)},
        )


class AssessmentRecordTest(_RiskBase):
    def test_an_ordinary_order_is_low_and_asks_for_nobody(self):
        order = self.order(user=self.returning())
        row = self.assess(order)

        self.assertEqual(row, OrderRiskAssessment.objects.get(order=order))
        self.assertEqual((row.company, row.signals, row.score), (self.a, [], 0))
        self.assertEqual((row.level, row.review_status), (Level.LOW, Review.NOT_REQUIRED))
        self.assertEqual((row.reviewed_by, row.reviewed_at, row.review_note), (None, None, ''))

    def test_the_address_is_kept_as_a_keyed_hash_and_nothing_else_of_the_request_is(self):
        row = self.assess(self.order(user=self.returning()), ip='203.0.113.77')

        self.assertRegex(row.ip_hash, r'^[0-9a-f]{64}$')
        import hashlib
        self.assertNotEqual(row.ip_hash, hashlib.sha256(b'203.0.113.77').hexdigest())   # keyed, not a bare hash
        kept = json.dumps({f.name: str(getattr(row, f.name)) for f in OrderRiskAssessment._meta.fields})
        self.assertNotIn('203.0.113.77', kept)
        self.assertNotIn('Navegador de prueba', kept)
        # No column could hold the address or the browser.
        self.assertEqual({f.name for f in OrderRiskAssessment._meta.fields}, {
            'id', 'company', 'order', 'level', 'score', 'signals', 'ip_hash', 'review_status',
            'reviewed_by', 'reviewed_at', 'review_note', 'created_at', 'updated_at',
        })

    def test_the_same_address_is_another_hash_in_another_shop(self):
        here = self.assess(self.order(self.a, self.returning(self.a, 'una')), ip='203.0.113.5')
        there = self.assess(self.order(self.b, self.returning(self.b, 'otra')), ip='203.0.113.5')
        again = self.assess(self.order(self.a, User.objects.get(username='una')), ip='203.0.113.5')
        self.assertEqual(here.ip_hash, again.ip_hash)
        self.assertNotEqual(here.ip_hash, there.ip_hash)

    def test_the_key_is_this_installations_and_its_own(self):
        order = self.order(user=self.returning())
        first = risk.origin_hash(self.a, from_address('203.0.113.5'))
        with override_settings(SECRET_KEY='otra-clave-de-prueba-que-no-es-la-misma'):
            self.assertNotEqual(risk.origin_hash(self.a, from_address('203.0.113.5')), first)
        self.assertEqual(risk.origin_hash(self.a, None), '')
        self.assertEqual(self.assess(order, ip=None).ip_hash, '')

    def test_what_is_kept_is_the_code_of_each_rule_never_what_tripped_it(self):
        self.amount('50.00')
        Customer.objects.create(
            company=self.a, first_name='Archivada', document_type='dni', document_number='40009999', is_active=False,
        )
        order = self.order(total='5000.00', document_number='40009999', customer_phone='987000111', customer_email='rara@correo.test')
        row = self.assess(order)

        self.assertTrue(set(row.signals) <= set(risk.SIGNALS))
        self.assertEqual(row.signals, sorted(row.signals, key=risk.SIGNALS.index))
        kept = json.dumps(row.signals)
        for value in ('40009999', '987000111', 'rara@correo.test', '5000', 'Archivada'):
            self.assertNotIn(value, kept)

    def test_it_belongs_to_the_company_of_its_order(self):
        order = self.order(self.a)
        with self.assertRaises(ValueError):
            OrderRiskAssessment.objects.create(company=self.b, order=order)

    def test_an_order_is_assessed_once(self):
        order = self.order(user=self.returning())
        first = self.assess(order)
        self.assertIsNone(self.assess(order))
        self.assertEqual(OrderRiskAssessment.objects.get(order=order).pk, first.pk)


class EachSignalTest(_RiskBase):
    def signals(self, order, ip='203.0.113.10'):
        return set(self.assess(order, ip).signals)

    # -- who is buying ---------------------------------------------------------

    def test_guest_checkout(self):
        self.assertIn(risk.SIGNAL_GUEST_CHECKOUT, self.signals(self.order()))
        self.assertNotIn(risk.SIGNAL_GUEST_CHECKOUT, self.signals(self.order(user=self.buyer())))

    def test_email_unverified_is_about_an_account(self):
        unknown = self.buyer('sin_verificar', verified=False)
        self.assertIn(risk.SIGNAL_EMAIL_UNVERIFIED, self.signals(self.order(user=unknown)))
        self.assertNotIn(risk.SIGNAL_EMAIL_UNVERIFIED, self.signals(self.order(user=self.buyer('verificada'))))
        # A guest has no account to verify: that is its own signal, not this one.
        self.assertNotIn(risk.SIGNAL_EMAIL_UNVERIFIED, self.signals(self.order()))

    def test_first_order_of_an_account(self):
        user = self.buyer()
        self.assertIn(risk.SIGNAL_FIRST_ORDER, self.signals(self.order(user=user)))       # that one was never paid
        self.assertIn(risk.SIGNAL_FIRST_ORDER, self.signals(self.order(user=user)))
        self.order(self.b, user, paid=True)                                               # paid, in another shop
        self.assertIn(risk.SIGNAL_FIRST_ORDER, self.signals(self.order(user=user)))
        self.order(self.a, user, paid=True)
        self.assertNotIn(risk.SIGNAL_FIRST_ORDER, self.signals(self.order(user=user)))

    def test_first_order_of_a_guest_is_about_the_client_the_shop_matched(self):
        client = Customer.objects.create(company=self.a, first_name='Cliente', document_type='dni', document_number='40007777')
        self.assertIn(risk.SIGNAL_FIRST_ORDER, self.signals(self.order(customer=client)))
        self.order(customer=client, paid=True)
        self.assertNotIn(risk.SIGNAL_FIRST_ORDER, self.signals(self.order(customer=client)))
        self.assertIn(risk.SIGNAL_FIRST_ORDER, self.signals(self.order()))                # nobody the shop knows

    # -- how much ---------------------------------------------------------------

    def test_high_amount_is_off_until_the_company_names_an_amount(self):
        self.assertIsNone(CompanySettings.objects.filter(company=self.a).values_list('risk_review_amount', flat=True).first())
        self.assertNotIn(risk.SIGNAL_HIGH_AMOUNT, self.signals(self.order(total='999999.00')))

    def test_high_amount_from_the_companys_own_amount_upwards(self):
        self.amount('1000.00')
        self.assertNotIn(risk.SIGNAL_HIGH_AMOUNT, self.signals(self.order(total='999.99')))
        self.assertIn(risk.SIGNAL_HIGH_AMOUNT, self.signals(self.order(total='1000.00')))
        self.assertIn(risk.SIGNAL_HIGH_AMOUNT, self.signals(self.order(total='1500.00')))

    def test_high_amount_reads_the_amount_of_the_orders_company(self):
        self.amount('10.00', self.b)
        self.assertNotIn(risk.SIGNAL_HIGH_AMOUNT, self.signals(self.order(self.a, total='5000.00')))
        self.assertIn(risk.SIGNAL_HIGH_AMOUNT, self.signals(self.order(self.b, total='5000.00')))

    # -- how often --------------------------------------------------------------

    def test_velocity_ip_from_the_third_order_of_one_address_in_an_hour(self):
        self.assertEqual(risk.VELOCITY_IP_ORDERS, 3)
        self.assertEqual(risk.VELOCITY_IP_WINDOW, timedelta(hours=1))
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.7'))
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.7'))
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.8'))      # another address
        self.assertIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.7'))

    def test_velocity_ip_forgets_after_the_hour(self):
        for _ in range(2):
            self.assess(self.order(), '198.51.100.7')
        OrderRiskAssessment.objects.update(created_at=timezone.now() - timedelta(minutes=61))
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.7'))

    def test_velocity_ip_never_counts_another_companys_orders(self):
        for _ in range(4):
            self.assess(self.order(self.b), '198.51.100.7')
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(self.a), '198.51.100.7'))

    def test_velocity_ip_needs_an_address(self):
        for _ in range(4):
            self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), ip=None))

    def shared(self, n, company=None, **same):
        """`n` orders by `n` different guests that share one contact detail."""
        return [self.order(company, **same) for _ in range(n)]

    def test_velocity_contact_from_the_third_buyer_with_one_phone_in_a_day(self):
        self.assertEqual(risk.VELOCITY_CONTACT_BUYERS, 3)
        self.assertEqual(risk.VELOCITY_CONTACT_WINDOW, timedelta(hours=24))
        self.order(customer_phone='987 111 222')
        second = self.order(user=self.buyer('otra'), customer_phone='+51987111222')
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(second))
        third = self.order(customer_phone='987111222')                       # written three ways, one number
        self.assertIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(third))

    def test_velocity_contact_by_address_and_by_document(self):
        self.shared(2, customer_email='Compartido@Correo.test')
        self.assertIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(self.order(customer_email='compartido@correo.test')))

        self.shared(2, document_type='dni', document_number='40005555')
        self.assertIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(self.order(document_type='dni', document_number='40005555')))
        # The same number under another type is another document.
        self.shared(2, document_type='ce', document_number='40006666')
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(self.order(document_type='dni', document_number='40006666')))

    def test_velocity_contact_is_about_different_buyers_not_one_buying_again(self):
        user = self.buyer()
        for _ in range(4):
            last = self.order(user=user, customer_phone='987111222')
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(last))

        # One browser trying its payment again is one guest.
        for _ in range(4):
            last = self.order(customer_phone='987333444', cart_session_key='un-mismo-carrito')
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(last))

    def test_velocity_contact_forgets_after_a_day(self):
        self.shared(2, customer_phone='987111222')
        Order.objects.update(created_at=timezone.now() - timedelta(hours=25))
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(self.order(customer_phone='987111222')))

    def test_velocity_contact_never_counts_another_companys_orders(self):
        self.shared(5, self.b, customer_phone='987111222', customer_email='compartido@correo.test',
                    document_type='dni', document_number='40005555')
        order = self.order(self.a, customer_phone='987111222', customer_email='compartido@correo.test',
                           document_type='dni', document_number='40005555')
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(order))

    def test_velocity_contact_is_about_online_orders(self):
        self.shared(4, customer_phone='987111222', sales_channel='pos')
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(self.order(customer_phone='987111222')))

    def test_an_empty_detail_is_shared_with_nobody(self):
        self.shared(4, customer_phone='', customer_email='', document_type='', document_number='')
        order = self.order(customer_phone='', customer_email='', document_type='', document_number='')
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(order))

    # -- who the shop knows -------------------------------------------------------

    def test_document_mismatch_between_the_buyers_record_and_the_order(self):
        user = self.buyer()
        Customer.objects.create(company=self.a, user=user, first_name='Ana', document_type='dni', document_number='40001111')
        self.assertIn(risk.SIGNAL_DOCUMENT_MISMATCH, self.signals(self.order(user=user, document_number='40002222')))
        self.assertIn(risk.SIGNAL_DOCUMENT_MISMATCH,
                      self.signals(self.order(user=user, document_type='ce', document_number='40001111')))
        self.assertNotIn(risk.SIGNAL_DOCUMENT_MISMATCH, self.signals(self.order(user=user, document_number='40001111')))

    def test_document_mismatch_needs_a_record_with_a_document_in_this_company(self):
        user = self.buyer()
        self.assertNotIn(risk.SIGNAL_DOCUMENT_MISMATCH, self.signals(self.order(user=user)))       # no record
        Customer.objects.create(company=self.a, user=user, first_name='Ana')                       # a record, no document
        self.assertNotIn(risk.SIGNAL_DOCUMENT_MISMATCH, self.signals(self.order(user=user)))
        Customer.objects.create(company=self.b, user=user, first_name='Ana', document_type='dni', document_number='40001111')
        self.assertNotIn(risk.SIGNAL_DOCUMENT_MISMATCH, self.signals(self.order(self.a, user=user, document_number='40002222')))
        self.assertNotIn(risk.SIGNAL_DOCUMENT_MISMATCH, self.signals(self.order(document_number='40002222')))   # a guest

    def test_archived_customer_by_account_asks_for_a_person_whatever_the_score(self):
        user = self.returning()
        Customer.objects.create(company=self.a, user=user, first_name='Ana', is_active=False)
        row = self.assess(self.order(user=user))
        self.assertEqual(row.signals, [risk.SIGNAL_ARCHIVED_CUSTOMER])
        self.assertEqual(row.level, risk.level_for(risk.WEIGHTS[risk.SIGNAL_ARCHIVED_CUSTOMER]))
        self.assertNotEqual(row.level, Level.HIGH)
        self.assertEqual(row.review_status, Review.PENDING)

    def test_archived_customer_by_document(self):
        Customer.objects.create(
            company=self.a, first_name='Archivada', document_type='dni', document_number='40003333', is_active=False,
        )
        self.assertIn(risk.SIGNAL_ARCHIVED_CUSTOMER, self.signals(self.order(document_number='40003333')))
        self.assertNotIn(risk.SIGNAL_ARCHIVED_CUSTOMER,
                         self.signals(self.order(document_type='ce', document_number='40003333')))

    def test_archived_customer_is_about_this_company_and_about_archived_records(self):
        user = self.returning()
        Customer.objects.create(company=self.b, user=user, first_name='Ana', is_active=False)
        Customer.objects.create(company=self.b, first_name='Otra', document_type='dni', document_number='40003333', is_active=False)
        Customer.objects.create(company=self.a, first_name='Activa', document_type='dni', document_number='40004444')
        self.assertNotIn(risk.SIGNAL_ARCHIVED_CUSTOMER, self.signals(self.order(self.a, user=user, document_number='40003333')))
        self.assertNotIn(risk.SIGNAL_ARCHIVED_CUSTOMER, self.signals(self.order(self.a, document_number='40004444')))


class ScoreAndLevelTest(_RiskBase):
    def test_every_signal_has_a_weight_and_words_for_the_panel(self):
        self.assertEqual(set(risk.SIGNALS), {
            'guest_checkout', 'email_unverified', 'first_order', 'high_amount',
            'velocity_ip', 'velocity_contact', 'document_mismatch', 'archived_customer',
        })
        self.assertEqual(set(risk.WEIGHTS), set(risk.SIGNALS))
        self.assertEqual(set(risk.LABELS), set(risk.SIGNALS))
        for code in risk.SIGNALS:
            self.assertGreater(risk.WEIGHTS[code], 0, code)
            self.assertTrue(risk.LABELS[code].strip(), code)

    def test_the_level_comes_from_the_score_at_two_thresholds(self):
        self.assertLess(risk.MEDIUM_SCORE, risk.HIGH_SCORE)
        for score, level in (
            (0, Level.LOW), (risk.MEDIUM_SCORE - 1, Level.LOW),
            (risk.MEDIUM_SCORE, Level.MEDIUM), (risk.HIGH_SCORE - 1, Level.MEDIUM),
            (risk.HIGH_SCORE, Level.HIGH), (risk.HIGH_SCORE + 40, Level.HIGH),
        ):
            with self.subTest(score):
                self.assertEqual(risk.level_for(score), level)

    def test_the_score_is_the_sum_of_what_was_noticed(self):
        self.amount('1000.00')
        row = self.assess(self.order(total='2000.00'))
        self.assertEqual(set(row.signals), {risk.SIGNAL_GUEST_CHECKOUT, risk.SIGNAL_FIRST_ORDER, risk.SIGNAL_HIGH_AMOUNT})
        self.assertEqual(row.score, sum(risk.WEIGHTS[code] for code in row.signals))
        self.assertEqual(row.level, risk.level_for(row.score))

    def test_a_first_purchase_without_an_account_is_ordinary(self):
        # Proportional: what most honest buyers look like must not ask for anybody.
        row = self.assess(self.order())
        self.assertEqual(set(row.signals), {risk.SIGNAL_GUEST_CHECKOUT, risk.SIGNAL_FIRST_ORDER})
        self.assertEqual((row.level, row.review_status), (Level.LOW, Review.NOT_REQUIRED))

        row = self.assess(self.order(user=self.buyer(verified=False)))
        self.assertEqual(set(row.signals), {risk.SIGNAL_EMAIL_UNVERIFIED, risk.SIGNAL_FIRST_ORDER})
        self.assertEqual((row.level, row.review_status), (Level.LOW, Review.NOT_REQUIRED))

    def test_a_high_level_asks_for_a_person_and_a_medium_one_does_not(self):
        self.amount('1000.00')
        medium = self.assess(self.order(total='2000.00'))
        self.assertEqual((medium.level, medium.review_status), (Level.MEDIUM, Review.NOT_REQUIRED))

        for _ in range(2):
            self.assess(self.order(), '198.51.100.7')
        high = self.assess(self.order(total='2000.00'), '198.51.100.7')
        self.assertGreaterEqual(high.score, risk.HIGH_SCORE)
        self.assertEqual((high.level, high.review_status), (Level.HIGH, Review.PENDING))


@override_settings(ALLOWED_HOSTS=['*'], **IZIPAY_TEST_SETTINGS)
class AssessedAtCheckoutTest(TestCase):
    """Where an online order is made — and only there."""

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Tienda', 'riesgo-caja', tax_id='20999999990')
        self.branch = self.company.branches.first() or _saas_branch(self.company)
        self.company.default_inventory_branch = self.branch
        self.company.save(update_fields=['default_inventory_branch'])
        self.product = _prod(self.company, 'Funda', 'funda-riesgo', price='80.00')
        _m5_stock(self.product, self.branch, 50)
        self.host = {'HTTP_HOST': 'riesgo-caja.example.com'}
        patcher = patch(SESSION_TOKEN_PATH, return_value=FAKE_SESSION_TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)

    def web_body(self, session='carrito-web-1', **overrides):
        CartItem.objects.get_or_create(session_key=session, product=self.product, defaults={'quantity': 1})
        body = {
            'session_key': session, 'customer_name': 'Ana Torres', 'customer_email': 'ana@correo.test',
            'customer_phone': '987654321', 'document_type': 'dni', 'document_number': DNI,
            'delivery_method': 'pickup_store', 'receipt_type': 'boleta',
            'accepted_terms': True, 'accepted_warranty_policy': True,
        }
        body.update(overrides)
        return body

    def web_checkout(self, client=None, **overrides):
        cache.clear()
        return (client or APIClient()).post(
            WEB_CHECKOUT, self.web_body(**overrides), format='json', REMOTE_ADDR='203.0.113.40', **self.host,
        )

    def app_checkout(self, client, **overrides):
        cache.clear()
        body = {
            'items': [{'product_slug': 'funda-riesgo', 'quantity': 1}], 'customer_name': 'Ana Torres',
            'customer_phone': '987654321', 'document_type': 'dni', 'document_number': DNI,
            'delivery_method': 'pickup_store', 'receipt_type': 'boleta', 'accepted_terms': True,
            'accepted_warranty_policy': True, 'idempotency_key': 'pedido-app-0001',
        }
        body.update(overrides)
        return client.post('/api/v1/customer/riesgo-caja/checkout/', body, format='json', REMOTE_ADDR='203.0.113.41')

    def test_a_guest_on_the_web(self):
        response = self.web_checkout()
        self.assertEqual(response.status_code, 200, response.content)
        row = OrderRiskAssessment.objects.get(order_id=response.json()['order_id'])
        self.assertEqual(row.company, self.company)
        self.assertEqual(set(row.signals), {risk.SIGNAL_GUEST_CHECKOUT, risk.SIGNAL_FIRST_ORDER})
        self.assertEqual(row.ip_hash, risk.origin_hash(self.company, from_address('203.0.113.40')))
        # The buyer is told nothing about it.
        self.assertNotIn('risk', response.content.decode().lower())
        self.assertNotIn('review', response.content.decode().lower())

    def test_a_signed_in_buyer_on_the_web(self):
        user = User.objects.create_user('ana', 'ana@correo.test', PASSWORD, first_name='Ana')
        response = self.web_checkout(web_session(user))
        self.assertEqual(response.status_code, 200, response.content)
        row = OrderRiskAssessment.objects.get(order_id=response.json()['order_id'])
        self.assertEqual(set(row.signals), {risk.SIGNAL_EMAIL_UNVERIFIED, risk.SIGNAL_FIRST_ORDER})

    def test_the_app(self):
        user = User.objects.create_user('ana', 'ana@correo.test', PASSWORD, first_name='Ana')
        accounts.mark_email_verified(user)
        Customer.objects.create(company=self.company, user=user, first_name='Ana', document_type='dni', document_number='70000009')
        client = v1_session(user)
        response = self.app_checkout(client)
        self.assertEqual(response.status_code, 201, response.content)
        row = OrderRiskAssessment.objects.get(order_id=response.json()['order_id'])
        self.assertEqual(set(row.signals), {risk.SIGNAL_FIRST_ORDER, risk.SIGNAL_DOCUMENT_MISMATCH})
        self.assertEqual(row.ip_hash, risk.origin_hash(self.company, from_address('203.0.113.41')))
        self.assertNotIn('risk', response.content.decode().lower())

        # The same request again is the same order, and it is not assessed twice.
        again = self.app_checkout(client)
        self.assertEqual(again.json()['order_id'], response.json()['order_id'])
        self.assertEqual(OrderRiskAssessment.objects.count(), 1)

    def test_an_assessment_that_breaks_never_costs_the_sale(self):
        def broken(order, request):
            # A refusal of the DATABASE, the kind that poisons a transaction.
            Customer.objects.create(company=order.company)

        with patch('store.order_risk._assess', side_effect=broken), self.assertLogs('store.order_risk', level='ERROR') as logged:
            response = self.web_checkout()
        self.assertEqual(response.status_code, 200, response.content)
        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertFalse(OrderRiskAssessment.objects.exists())                # no row: «not assessed», not «low»
        text = '\n'.join(logged.output)
        for value in ('ana@correo.test', '987654321', DNI, 'Ana Torres', '203.0.113.40'):
            self.assertNotIn(value, text)

    def test_a_python_error_in_it_does_not_cost_the_sale_either(self):
        with patch('store.order_risk._signals', side_effect=RuntimeError('ana@correo.test 987654321')), \
                self.assertLogs('store.order_risk', level='ERROR') as logged:
            response = self.web_checkout()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(OrderRiskAssessment.objects.exists())
        self.assertNotIn('ana@correo.test', '\n'.join(logged.output))         # the error's own words are not logged

    def test_a_sale_at_the_till_is_not_assessed(self):
        cashier, _ = _p2d_member(self.company, 'caja_riesgo', ['company.view', 'sales.pos.use'])
        order, created = _c1_sale(
            actor=cashier, company=self.company, branch=self.branch,
            items=[{'product': self.product.pk, 'quantity': 1}],
        )
        self.assertTrue(created)
        self.assertEqual(order.sales_channel, 'pos')
        self.assertFalse(OrderRiskAssessment.objects.exists())

    def test_an_order_from_before_has_no_row(self):
        order = _order(self.company, total='900.00', paid=True)
        self.assertFalse(OrderRiskAssessment.objects.filter(order=order).exists())
        self.assertIsNone(risk.for_order(order))


# =============================================================================
# 4 · a person decides, once — and staff read what was noticed
# =============================================================================

from store.models import Membership  # noqa: E402
from store.tests import _pilot_company  # noqa: E402

VIEW, MANAGE = 'sales.orders.view', 'sales.orders.manage'
REVIEW_SIGNALS = [risk.SIGNAL_GUEST_CHECKOUT, risk.SIGNAL_HIGH_AMOUNT, risk.SIGNAL_VELOCITY_IP]


def panel_review(pk):
    return f'/api/admin/orders/{pk}/risk-review/'


def app_review(slug, pk):
    return f'/api/v1/internal/{slug}/orders/{pk}/risk-review/'


def acting(user):
    """A client acting as `user`, on whichever surface the URL belongs to."""
    cache.clear()
    client = APIClient()
    client.force_authenticate(user)
    return client


class _ReviewBase(TestCase):
    def setUp(self):
        cache.clear()
        self.a = _saas_company('Tienda A', 'revision-a', tax_id='20999999990')
        self.b = _saas_company('Tienda B', 'revision-b', tax_id='20111111112')
        self.north = _p2d_branch(self.a, 'Norte')
        self.south = _p2d_branch(self.a, 'Sur')
        self.viewer, _ = _p2d_member(self.a, 'solo_ver', ['company.view', VIEW])
        self.manager, _ = _p2d_member(self.a, 'gestora', ['company.view', VIEW, MANAGE])
        self.manager.first_name, self.manager.last_name = 'Gema', 'Gestora'
        self.manager.save(update_fields=['first_name', 'last_name'])
        self.northerner, _ = _p2d_member(self.a, 'solo_norte', ['company.view', VIEW, MANAGE], branches=[self.north])
        self.outsider, _ = _p2d_member(self.a, 'sin_ventas', ['company.view'])
        self.foreigner, _ = _p2d_member(self.b, 'gestora_b', ['company.view', VIEW, MANAGE])
        self.buyer = User.objects.create_user('compradora', 'compradora@correo.test', PASSWORD, first_name='Ana')

        self.order = self.pending(self.a, self.north, user=self.buyer)
        self.southern = self.pending(self.a, self.south)
        self.foreign = self.pending(self.b, None)
        self.row = self.order.risk_assessment

    def pending(self, company, branch, *, status=Review.PENDING, level=Level.HIGH, user=None, **extra):
        order = _order(
            company, user=user, total='900.00', paid=True, fulfillment_branch=branch,
            customer_name='Ana Torres', customer_email='ana.torres@correo.test', customer_phone='987654321',
            document_type='dni', document_number=DNI, **extra,
        )
        OrderRiskAssessment.objects.create(
            company=company, order=order, level=level, score=70, signals=list(REVIEW_SIGNALS),
            ip_hash='a' * 64, review_status=status,
        )
        return order

    def decide(self, user, decision='approve', *, order=None, note=None, surface='panel', slug='revision-a'):
        order = order or self.order
        body = {'decision': decision}
        if note is not None:
            body['note'] = note
        url = panel_review(order.pk) if surface == 'panel' else app_review(slug, order.pk)
        return acting(user).post(url, body, format='json')

    def entries(self):
        return list(AdminAuditLog.objects.filter(action='order_risk_reviewed').order_by('pk'))


class ReviewDecisionTest(_ReviewBase):
    def test_approving_writes_who_when_and_why_and_touches_nothing_else(self):
        before = timezone.now()
        response = self.decide(self.manager, 'approve', note='Llamé a la clienta y confirmó la compra.')
        self.assertEqual(response.status_code, 200, response.content)

        self.row.refresh_from_db()
        self.assertEqual((self.row.review_status, self.row.reviewed_by), (Review.APPROVED, self.manager))
        self.assertGreaterEqual(self.row.reviewed_at, before)
        self.assertEqual(self.row.review_note, 'Llamé a la clienta y confirmó la compra.')
        # What was noticed stays as it was noticed.
        self.assertEqual((self.row.level, self.row.score, self.row.signals), (Level.HIGH, 70, REVIEW_SIGNALS))

        self.order.refresh_from_db()
        self.assertEqual(
            (self.order.status, self.order.paid, self.order.fulfillment_status),
            (Order.Status.PAID, True, Order.FulfillmentStatus.PENDING),
        )
        body = response.json()['risk']
        self.assertEqual((body['review_status'], body['reviewed_by'], body['can_review']), ('approved', 'Gema Gestora', False))

    def test_rejecting_is_advice_too(self):
        response = self.decide(self.manager, 'reject', surface='app')
        self.assertEqual(response.status_code, 200, response.content)
        self.row.refresh_from_db()
        self.assertEqual((self.row.review_status, self.row.reviewed_by, self.row.review_note), (Review.REJECTED, self.manager, ''))
        self.order.refresh_from_db()
        # Not cancelled, not refunded, still paid, still where it was.
        self.assertEqual(
            (self.order.status, self.order.paid, self.order.fulfillment_status),
            (Order.Status.PAID, True, Order.FulfillmentStatus.PENDING),
        )
        self.assertEqual(response.json()['risk']['review_status'], 'rejected')

    def test_a_decision_is_made_once(self):
        self.assertEqual(self.decide(self.manager, 'approve', note='Primera').status_code, 200)
        other, _ = _p2d_member(self.a, 'otra_gestora', ['company.view', VIEW, MANAGE])

        for surface in ('panel', 'app'):
            for decision in ('approve', 'reject'):
                with self.subTest(surface=surface, decision=decision):
                    again = self.decide(other, decision, note='Segunda', surface=surface)
                    self.assertEqual(again.status_code, 409, again.content)
                    self.assertEqual(again.json()['code'], 'review_not_pending')
        self.row.refresh_from_db()
        self.assertEqual((self.row.review_status, self.row.reviewed_by, self.row.review_note), (Review.APPROVED, self.manager, 'Primera'))
        self.assertEqual(len(self.entries()), 1)

    def test_only_what_is_waiting_can_be_decided(self):
        quiet = self.pending(self.a, self.north, status=Review.NOT_REQUIRED, level=Level.LOW)
        self.assertEqual(self.decide(self.manager, order=quiet).status_code, 409)
        unassessed = _order(self.a, total='10.00', fulfillment_branch=self.north)
        for surface in ('panel', 'app'):
            self.assertEqual(self.decide(self.manager, order=unassessed, surface=surface).status_code, 404)
        self.assertEqual(self.entries(), [])
        self.assertFalse(OrderRiskAssessment.objects.filter(order=unassessed).exists())

    def test_what_is_not_a_decision_is_refused(self):
        for surface in ('panel', 'app'):
            for body in ({}, {'decision': 'approved'}, {'decision': 'cancel'}, {'decision': 'approve', 'note': 'x' * 301}):
                with self.subTest(surface=surface, body=body):
                    url = panel_review(self.order.pk) if surface == 'panel' else app_review('revision-a', self.order.pk)
                    self.assertEqual(acting(self.manager).post(url, body, format='json').status_code, 400)
        self.row.refresh_from_db()
        self.assertEqual(self.row.review_status, Review.PENDING)

    def test_it_is_written_down_without_anything_personal(self):
        self.decide(self.manager, 'reject', note='La clienta Ana Torres, DNI 70000001, no contesta al 987654321.')
        [entry] = self.entries()
        self.assertEqual((entry.actor, entry.company, entry.target_type, entry.target_id), (self.manager, self.a, 'order', str(self.order.pk)))
        self.assertEqual(entry.metadata, {
            'order_id': self.order.pk, 'decision': 'rejected', 'level': 'high',
            'signals': REVIEW_SIGNALS, 'has_note': True,
        })
        written = json.dumps(entry.metadata)
        for value in ('Ana', 'Torres', DNI, '987654321', 'ana.torres@correo.test', 'contesta', 'a' * 64):
            self.assertNotIn(value, written)

    def test_nothing_personal_reaches_a_log(self):
        with self.assertLogs(level='DEBUG') as captured:
            import logging
            logging.getLogger('store').debug('centinela')
            self.decide(self.manager, 'approve', note='Ana Torres confirmó por teléfono 987654321')
            self.decide(self.manager, 'reject')
        text = '\n'.join(captured.output)
        for value in ('Ana Torres', '987654321', DNI, 'ana.torres@correo.test'):
            self.assertNotIn(value, text)


class ReviewPermissionTest(_ReviewBase):
    def assert_still_pending(self, order=None):
        row = OrderRiskAssessment.objects.get(order=order or self.order)
        self.assertEqual((row.review_status, row.reviewed_by), (Review.PENDING, None))

    def test_seeing_orders_is_not_deciding_on_them(self):
        for surface in ('panel', 'app'):
            for user in (self.viewer, self.outsider):
                with self.subTest(surface=surface, user=user.username):
                    self.assertEqual(self.decide(user, surface=surface).status_code, 403)
        self.assert_still_pending()

    def test_a_buyer_decides_nothing_not_even_on_their_own_order(self):
        self.assertEqual(self.decide(self.buyer, surface='panel').status_code, 403)
        self.assertEqual(self.decide(self.buyer, surface='app').status_code, 404)
        self.assertIn(APIClient().post(panel_review(self.order.pk), {'decision': 'approve'}, format='json').status_code, (401, 403))
        self.assertEqual(APIClient().post(app_review('revision-a', self.order.pk), {'decision': 'approve'}, format='json').status_code, 401)
        self.assert_still_pending()

    def test_another_companys_staff_never_decide_here(self):
        # Their own company is the one they act in: this order is not in it.
        self.assertEqual(self.decide(self.foreigner, surface='panel').status_code, 404)
        # Naming this company does not open it: the panel answers what it always
        # answers to a company the caller has no access to.
        self.assertEqual(
            acting(self.foreigner).post(panel_review(self.order.pk) + f'?company={self.a.pk}', {'decision': 'approve'}, format='json').status_code,
            403,
        )
        self.assertEqual(self.decide(self.foreigner, surface='app', slug='revision-a').status_code, 404)
        self.assertEqual(self.decide(self.foreigner, surface='app', slug='revision-b').status_code, 404)
        self.assert_still_pending()
        # And this company's staff never decide there.
        self.assertEqual(self.decide(self.manager, order=self.foreign, surface='panel').status_code, 404)
        self.assertEqual(self.decide(self.manager, order=self.foreign, surface='app').status_code, 404)
        self.assert_still_pending(self.foreign)
        self.assertEqual(self.entries(), [])

    def test_a_branch_scope_holds(self):
        for surface in ('panel', 'app'):
            self.assertEqual(self.decide(self.northerner, order=self.southern, surface=surface).status_code, 404)
        self.assert_still_pending(self.southern)
        self.assertEqual(self.decide(self.northerner, order=self.order).status_code, 200)

    def test_the_pilots_operators_from_before_companies(self):
        pilot = _pilot_company()
        order = self.pending(pilot, None)

        def legacy(username, role):
            user = User.objects.create_user(username, f'{username}@correo.test', PASSWORD)
            user.profile.role = role
            user.profile.save()
            return user

        warehouse, counter = legacy('almacen', UserProfile.ROLE_INVENTORY), legacy('ventas', UserProfile.ROLE_SALES)
        # Warehouse staff read the order and move the goods; whether a sale
        # looks right is not theirs to say.
        seen = acting(warehouse).get(f'/api/admin/orders/{order.pk}/')
        self.assertEqual(seen.status_code, 200)
        self.assertEqual((seen.json()['risk']['review_status'], seen.json()['risk']['can_review']), ('pending', False))
        self.assertEqual(self.decide(warehouse, order=order).status_code, 403)
        self.assert_still_pending(order)

        self.assertTrue(acting(counter).get(f'/api/admin/orders/{order.pk}/').json()['risk']['can_review'])
        self.assertEqual(self.decide(counter, order=order).status_code, 200)


class RiskReadByStaffTest(_ReviewBase):
    def detail(self, user, order=None, surface='panel'):
        order = order or self.order
        url = f'/api/admin/orders/{order.pk}/' if surface == 'panel' else f'/api/v1/internal/revision-a/orders/{order.pk}/'
        return acting(user).get(url)

    def test_the_order_shows_what_was_noticed_in_plain_words(self):
        for surface in ('panel', 'app'):
            with self.subTest(surface):
                block = self.detail(self.viewer, surface=surface).json()['risk']
                self.assertEqual(block, {
                    'level': 'high', 'level_label': 'Alto', 'score': 70,
                    'signals': [{'code': code, 'label': risk.LABELS[code]} for code in REVIEW_SIGNALS],
                    'review_status': 'pending', 'review_status_label': 'Por revisar',
                    'reviewed_by': None, 'reviewed_at': None, 'review_note': '',
                    'assessed_at': block['assessed_at'], 'can_review': False,
                })
                self.assertEqual(parse_datetime(block['assessed_at']), self.row.created_at)
                self.assertNotIn('a' * 64, json.dumps(block))                  # the origin hash is nobody's to read

    def test_whoever_may_decide_is_told_so_by_the_server(self):
        for surface in ('panel', 'app'):
            self.assertTrue(self.detail(self.manager, surface=surface).json()['risk']['can_review'])
            self.assertFalse(self.detail(self.viewer, surface=surface).json()['risk']['can_review'])
        self.decide(self.manager, 'approve', note='Confirmado')
        for surface in ('panel', 'app'):
            block = self.detail(self.manager, surface=surface).json()['risk']
            self.assertEqual(
                (block['review_status'], block['reviewed_by'], block['review_note'], block['can_review']),
                ('approved', 'Gema Gestora', 'Confirmado', False),
            )
            self.assertIsNotNone(block['reviewed_at'])

    def test_an_order_that_was_not_assessed_says_so(self):
        plain = _order(self.a, total='10.00', fulfillment_branch=self.north)
        for surface in ('panel', 'app'):
            self.assertIsNone(self.detail(self.viewer, plain, surface).json()['risk'])

    def test_without_the_capability_to_see_orders_there_is_nothing_to_read(self):
        self.assertEqual(self.detail(self.outsider, surface='panel').status_code, 403)
        self.assertEqual(self.detail(self.outsider, surface='app').status_code, 403)
        self.assertEqual(self.detail(self.foreigner, surface='panel').status_code, 404)
        self.assertEqual(self.detail(self.foreigner, surface='app').status_code, 404)
        self.assertEqual(self.detail(self.northerner, self.southern, 'panel').status_code, 404)

    def rows(self, user, query='', surface='panel'):
        url = '/api/admin/orders/' if surface == 'panel' else '/api/v1/internal/revision-a/orders/'
        response = acting(user).get(url + query)
        self.assertEqual(response.status_code, 200, response.content)
        return {row['id']: row for row in response.json()['results']}

    def test_the_list_carries_level_and_review_state(self):
        plain = _order(self.a, total='10.00', fulfillment_branch=self.north)
        for surface in ('panel', 'app'):
            with self.subTest(surface):
                rows = self.rows(self.viewer, surface=surface)
                self.assertEqual((rows[self.order.pk]['risk_level'], rows[self.order.pk]['review_status']), ('high', 'pending'))
                self.assertEqual((rows[plain.pk]['risk_level'], rows[plain.pk]['review_status']), (None, None))

    def test_the_list_can_be_narrowed_to_what_is_waiting(self):
        plain = _order(self.a, total='10.00', fulfillment_branch=self.north)
        decided = self.pending(self.a, self.north, status=Review.APPROVED)
        quiet = self.pending(self.a, self.north, status=Review.NOT_REQUIRED, level=Level.MEDIUM)
        for surface in ('panel', 'app'):
            with self.subTest(surface):
                self.assertEqual(set(self.rows(self.viewer, '?review=pending', surface)), {self.order.pk, self.southern.pk})
                self.assertEqual(set(self.rows(self.viewer, '?review=approved', surface)), {decided.pk})
                # Not a state: ignored, like a date nobody can read.
                self.assertEqual(
                    set(self.rows(self.viewer, '?review=cualquiera', surface)),
                    {self.order.pk, self.southern.pk, plain.pk, decided.pk, quiet.pk},
                )

    def test_the_list_never_shows_another_companys_or_another_branchs(self):
        for surface in ('panel', 'app'):
            with self.subTest(surface):
                self.assertNotIn(self.foreign.pk, self.rows(self.viewer, '?review=pending', surface))
                self.assertEqual(set(self.rows(self.northerner, '?review=pending', surface)), {self.order.pk})
        theirs = acting(self.foreigner).get('/api/admin/orders/?review=pending').json()['results']
        self.assertEqual([row['id'] for row in theirs], [self.foreign.pk])

    def test_reading_writes_nothing(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for url in (
            f'/api/admin/orders/{self.order.pk}/', '/api/admin/orders/?review=pending',
            f'/api/v1/internal/revision-a/orders/{self.order.pk}/', '/api/v1/internal/revision-a/orders/?review=pending',
            panel_review(self.order.pk), app_review('revision-a', self.order.pk),
        ):
            client = acting(self.manager)
            with self.subTest(url), CaptureQueriesContext(connection) as queries:
                client.get(url)
                client.options(url)
            writes = [q['sql'] for q in queries.captured_queries if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
            self.assertEqual(writes, [])
        self.assertEqual(acting(self.manager).get(panel_review(self.order.pk)).status_code, 405)
        self.assertEqual(acting(self.manager).get(app_review('revision-a', self.order.pk)).status_code, 405)


@override_settings(ALLOWED_HOSTS=['*'])
class TheBuyerNeverSeesItTest(_ReviewBase):
    HIDDEN = ('risk', 'review', 'signal', 'score', 'ip_hash', 'a' * 64)

    def setUp(self):
        super().setUp()
        Customer.objects.create(company=self.a, user=self.buyer, first_name='Ana')
        self.decide(self.manager, 'reject', note='nota de la tienda')

    def assert_clean(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        text = response.content.decode().lower()
        for word in self.HIDDEN + ('nota de la tienda',):
            self.assertNotIn(word, text)

    def test_not_in_their_orders_on_the_web(self):
        client = web_session(self.buyer)
        listing = client.get('/api/orders/', HTTP_HOST='revision-a.example.com')
        self.assert_clean(listing)
        self.assertIn(self.order.pk, [row['id'] for row in listing.json()])
        self.assert_clean(client.get(f'/api/orders/{self.order.pk}/', HTTP_HOST='revision-a.example.com'))

    def test_not_in_their_orders_in_the_app(self):
        client = v1_session(self.buyer)
        listing = client.get('/api/v1/customer/revision-a/orders/')
        self.assert_clean(listing)
        self.assert_clean(client.get(f'/api/v1/customer/revision-a/orders/{self.order.pk}/'))

    def test_not_in_their_profile_or_in_who_they_are(self):
        self.assert_clean(v1_session(self.buyer).get(v1_profile('revision-a')))
        self.assert_clean(v1_session(self.buyer).get(V1_ME))
        client = web_session(self.buyer)
        self.assert_clean(client.get(WEB_PROFILE, HTTP_HOST='revision-a.example.com'))
        self.assert_clean(client.get(WEB_ME))

    def test_what_a_buyer_reads_of_an_order_is_a_closed_list(self):
        from store.serializers import OrderSerializer
        from store.v1_customer_serializers import V1CustomerOrderSerializer

        self.assertEqual(V1CustomerOrderSerializer.Meta.fields, [
            'id', 'status', 'status_label', 'fulfillment_status', 'fulfillment_status_label',
            'total', 'discount_amount', 'coupon_code', 'delivery_method', 'delivery_method_label',
            'created_at', 'paid_at', 'items',
        ])
        self.assertEqual(OrderSerializer.Meta.fields, [
            'id', 'user', 'customer_name', 'customer_email', 'total', 'discount_amount',
            'coupon_code', 'status', 'paid', 'paid_at', 'created_at', 'items',
        ])
