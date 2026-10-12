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
