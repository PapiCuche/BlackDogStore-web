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
        cache.clear()
        login = APIClient().post(V1_LOGIN, {'email': self.user.email, 'password': PASSWORD}, format='json').json()
        client = v1_session(self.user)
        body = client.get(V1_ME).json()
        self.assertIn('email_verified_at', body)
        self.assertIsNone(body['email_verified_at'])
        # «Active» is still what `is_email_verified` says, verified or not, and
        # the user object is the one login returns, key for key.
        self.assertTrue(body['user']['is_email_verified'])
        self.assertEqual(body['user'], login['user'])
        self.assertNotIn('email_verified_at', login)

        accounts.mark_email_verified(self.user)
        body = client.get(V1_ME).json()
        self.assertEqual(parse_datetime(body['email_verified_at']), verified_at(self.user))
        self.assertTrue(body['user']['is_email_verified'])


class EmailVerifiedWhenTheColumnArrivedTest(TestCase):
    """Migration 0118: what was already proven is copied; the rest stays unknown."""

    def arrive(self):
        from django.apps import apps

        import_module('store.migrations.0120_client_sec_verified_backfill').record_verified_addresses(apps, None)

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
        moved = self.account('mudada')                                # Google vouched for another address
        ExternalIdentity.objects.create(user=moved, provider='google', subject='g-2', email_at_link='antes@correo.test')
        unlinked = self.account('sin_direccion')                      # an identity that never recorded an address
        ExternalIdentity.objects.create(user=unlinked, provider='google', subject='g-3', email_at_link='')

        self.arrive()
        for user in (plain, waiting, switched_off, moved, unlinked):
            with self.subTest(user.username):
                self.assertIsNone(verified_at(user))

    def test_a_used_recovery_link_is_a_proof_as_well(self):
        # F7 · it was sent to the mailbox and used: the same proof, whichever link it was.
        when = timezone.now() - timedelta(days=9)
        user = self.account('recuperada')
        self.token(user, used_at=when, purpose=AccountToken.PURPOSE_PASSWORD_RESET)
        self.arrive()
        self.assertEqual(verified_at(user), when)

    def test_the_earlier_of_two_proofs_is_the_one_kept(self):
        user = self.account('dos_pruebas')
        identity = ExternalIdentity.objects.create(user=user, provider='google', subject='g-9', email_at_link='dos_pruebas@correo.test')
        self.token(user, used_at=identity.created_at + timedelta(days=2))
        early = self.account('enlace_antes')
        linked = ExternalIdentity.objects.create(user=early, provider='google', subject='g-8', email_at_link='enlace_antes@correo.test')
        first = linked.created_at - timedelta(days=30)
        self.token(early, used_at=first)
        self.arrive()
        self.assertEqual(verified_at(user), identity.created_at)
        self.assertEqual(verified_at(early), first)

    def test_it_is_one_statement_however_many_accounts_there_are(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for n in range(6):
            self.token(self.account(f'cuenta{n}'), used_at=timezone.now())
        with CaptureQueriesContext(connection) as queries:
            self.arrive()
        self.assertEqual(len(queries.captured_queries), 1)
        self.assertTrue(queries.captured_queries[0]['sql'].lstrip().upper().startswith('UPDATE'))

    def test_the_tables_and_their_filling_are_separate_migrations(self):
        # F11 · filling a table inside the migration that alters it keeps the lock for as long as the fill takes.
        from django.db import migrations as dj_migrations

        for schema in ('0118_client_sec', '0119_delivery_base'):
            operations = import_module(f'store.migrations.{schema}').Migration.operations
            self.assertFalse([op for op in operations if isinstance(op, dj_migrations.RunPython)], schema)
        for data in ('0120_client_sec_verified_backfill', '0121_delivery_records_backfill'):
            operations = import_module(f'store.migrations.{data}').Migration.operations
            self.assertTrue(all(isinstance(op, dj_migrations.RunPython) for op in operations), data)
        self.assertFalse(import_module('store.migrations.0121_delivery_records_backfill').Migration.atomic)

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

        source = inspect.getsource(import_module('store.migrations.0120_client_sec_verified_backfill'))
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
            ('ruc', '30999999995'),                       # right check digit, a prefix nobody is given
            ('ce', 'AB123'), ('ce', 'AB12345678901'), ('ce', 'AB-12345'),
            ('pasaporte', 'AB123456'),                    # not a type this platform knows
            ('dni', ''), ('', DNI),                       # one without the other
            ('dni', None), ('dni', 70000001),
        ):
            with self.subTest(kind=kind, typed=typed), self.assertRaises(identity.IdentityError):
                identity.clean_document(kind, typed)

    def test_a_refusal_names_the_field_and_says_what_is_missing(self):
        for kind, typed, field, words in (
            ('', DNI, 'document_type', 'Indica el tipo de documento.'),
            ('dni', '', 'document_number', 'Indica el número de documento.'),
            ('pasaporte', 'AB123456', 'document_type', 'Tipo de documento no válido.'),
            ('ruc', '30999999995', 'document_number', 'El RUC no es válido. Revisa el número.'),
        ):
            with self.subTest(kind=kind, typed=typed), self.assertRaises(identity.IdentityError) as refused:
                identity.clean_document(kind, typed)
            self.assertEqual((refused.exception.field, refused.exception.message), (field, words))

    def test_the_check_digit_of_a_ruc(self):
        self.assertTrue(identity.ruc_check_digit_matches('30999999995'))      # the digit alone says nothing of the prefix
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
        # Most of these tests write a document, and that takes a proved address (F2).
        accounts.mark_email_verified(self.user)
        self.web = web_session(self.user)

    def unverified(self):
        UserProfile.objects.filter(user=self.user).update(email_verified_at=None)

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
        self.unverified()
        response = self.get()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(AdminAuditLog.objects.filter(action='customer_profile_updated').count(), 0)
        # The account's own name is offered to start from; nothing else is known.
        self.assertEqual(body, {
            'email': 'ana@correo.test', 'email_verified_at': None, 'email_verification_available': True,
            'first_name': 'Ana', 'last_name': 'Torres', 'phone': '',
            'document_type': '', 'document_number': '', 'document_locked': False,
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

    def test_a_document_on_file_is_corrected_but_never_taken_off(self):
        self.patch({'document_type': 'dni', 'document_number': DNI})
        for body in ({'document_type': '', 'document_number': ''}, {'document_number': ''}, {'document_type': ''}):
            with self.subTest(body):
                response = self.patch(body)
                self.assertEqual(response.status_code, 400)
        self.assertIn('no se puede quitar', self.patch({'document_type': '', 'document_number': ''}).json()['document_number'][0])
        record = self.record()
        self.assertEqual((record.document_type, record.document_number), ('dni', DNI))
        # A typo is still corrected by its owner, while the shop has not relied on it.
        self.assertEqual(self.patch({'document_type': 'dni', 'document_number': '70000009'}).status_code, 200)
        self.assertEqual(self.record().document_number, '70000009')

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
        self.assertLess(verified_at(self.user), parse_datetime('2026-01-01T00:00:00Z') + timedelta(days=36500))
        self.assertNotEqual(verified_at(self.user), parse_datetime('2026-01-01T00:00:00Z'))
        self.assertEqual(response.json()['email'], 'ana@correo.test')

    def test_the_record_of_a_business_keeps_its_own_name_and_needs_no_other(self):
        # Made by a purchase with a RUC: the shop filed it as a business, by its
        # registered name. That name is not this form's to write.
        business = User.objects.create_user('empresa', 'compras@empresa.test', PASSWORD)
        Customer.objects.create(
            company=self.a, user=business, customer_type='business', business_name='Comercial de Prueba S.A.C.',
            document_type='ruc', document_number=RUC_COMPANY,
        )
        client = web_session(business)
        body = self.get(client=client).json()
        self.assertEqual((body['first_name'], body['last_name'], body['document_number']), ('', '', RUC_COMPANY))
        self.assertNotIn('Comercial', json.dumps(body))

        self.assertEqual(self.patch({'phone': '987654321'}, client=client).status_code, 200)
        record = self.record(user=business)
        self.assertEqual(
            (record.phone, record.customer_type, record.business_name),
            ('987654321', 'business', 'Comercial de Prueba S.A.C.'),
        )

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
        # Proved: there is nothing left to offer.
        self.assertFalse(body['email_verification_available'])

    def test_an_archived_record_is_still_the_buyers_own_and_stays_archived(self):
        self.patch({'phone': '987654321'})
        Customer.objects.filter(user=self.user).update(is_active=False, notes='cerrado por la tienda')
        body = self.get().json()
        self.assertEqual(body['phone'], '987654321')
        self.assertNotIn('is_active', body)
        self.assertNotIn('cerrado', json.dumps(body))

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
        # Per hour, not per minute: each save may ask whether a document is on file.
        self.assertEqual((limit, AccountProfileWriteThrottle().duration), (20, 3600))
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

    def paid_now(self, order):
        """The order was just paid: what payment confirmation does about its review."""
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        order.refresh_from_db()
        risk.review_when_paid(order)
        return OrderRiskAssessment.objects.get(order=order)

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
        with self.assertNoLogs('store.order_risk', level='ERROR'):            # a no-op, not a swallowed refusal
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

    def test_first_order_counts_a_paid_order_the_shop_filed_under_the_buyers_record(self):
        # A purchase made at the counter, or as a guest, that the shop linked to
        # the file this account owns: `Order.user` is empty, `customer.user` is them.
        user = self.buyer()
        record = Customer.objects.create(company=self.a, user=user, first_name='Ana')
        self.order(customer=record, paid=True)
        self.assertNotIn(risk.SIGNAL_FIRST_ORDER, self.signals(self.order(user=user)))
        # Somebody else's file is not theirs.
        other = self.buyer('otra')
        self.assertIn(risk.SIGNAL_FIRST_ORDER, self.signals(self.order(user=other)))

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

    def others_paid(self, n, ip, company=None, **extra):
        """`n` OTHER guests whose orders came from `ip`, were assessed, and were PAID."""
        made = []
        for _ in range(n):
            order = self.order(company, paid=True, **extra)
            self.assess(order, ip)
            made.append(order)
        return made

    def test_velocity_ip_from_the_third_buyer_of_one_address_in_an_hour(self):
        self.assertEqual(risk.VELOCITY_IP_BUYERS, 3)
        self.assertEqual(risk.VELOCITY_IP_WINDOW, timedelta(hours=1))
        self.others_paid(1, '198.51.100.7')
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.7'))      # two buyers
        self.others_paid(1, '198.51.100.7')
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.8'))      # another address
        self.assertIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.7'))         # the third

    def test_velocity_ip_is_about_different_buyers_not_one_retrying(self):
        # F3 · a guest whose card is declined tries again from the same browser: one buyer.
        for _ in range(6):
            last = self.order(cart_session_key='un-mismo-carrito')
            found = self.signals(last, '198.51.100.7')
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, found)
        user = self.buyer()
        for _ in range(6):
            found = self.signals(self.order(user=user), '198.51.100.9')
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, found)

    def test_velocity_ip_counts_another_buyer_once_however_often_they_bought(self):
        # A regular who paid for three orders from the shop's own Wi-Fi is ONE other buyer.
        regular = self.buyer('habitual')
        for _ in range(3):
            self.assess(self.order(user=regular, paid=True), '198.51.100.7')
        for _ in range(3):
            self.assess(self.order(paid=True, cart_session_key='otro-carrito'), '198.51.100.7')
        self.assertIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.7'))          # two others + this one
        for _ in range(4):
            self.assess(self.order(user=regular, paid=True), '198.51.100.9')
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.9'))       # one other + this one

    def test_velocity_ip_counts_other_buyers_only_once_they_paid(self):
        # F3 · orders nobody paid for are free to make: they cannot mark somebody else's order.
        for _ in range(5):
            self.assess(self.order(), '198.51.100.7')                      # five other guests, none paid
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.7'))

    def test_velocity_ip_forgets_after_the_hour(self):
        self.others_paid(2, '198.51.100.7')
        OrderRiskAssessment.objects.update(created_at=timezone.now() - timedelta(minutes=61))
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), '198.51.100.7'))

    def test_velocity_ip_never_counts_another_companys_orders(self):
        self.others_paid(4, '198.51.100.7', self.b)
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(self.a), '198.51.100.7'))

    def test_velocity_ip_would_not_count_them_even_under_the_same_hash(self):
        # The hash is bound to the company, so this cannot happen by itself; the
        # count is asked inside the company all the same.
        here = risk.origin_hash(self.a, from_address('198.51.100.7'))
        for _ in range(4):
            OrderRiskAssessment.objects.create(company=self.b, order=self.order(self.b, paid=True), ip_hash=here)
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(self.a), '198.51.100.7'))

    def test_velocity_ip_needs_an_address(self):
        for _ in range(4):
            self.assess(self.order(paid=True), ip=None)
        self.assertNotIn(risk.SIGNAL_VELOCITY_IP, self.signals(self.order(), ip=None))

    def test_one_ipv6_household_is_one_origin(self):
        # F10 · a home is given a whole /64 and its devices move inside it.
        first = risk.origin_hash(self.a, from_address('2001:db8:aaaa:bbbb::1'))
        self.assertEqual(first, risk.origin_hash(self.a, from_address('2001:db8:aaaa:bbbb:1234:5678:9abc:def0')))
        self.assertNotEqual(first, risk.origin_hash(self.a, from_address('2001:db8:aaaa:cccc::1')))
        # An IPv4 address written the IPv6 way is that IPv4 address.
        self.assertEqual(risk.origin_hash(self.a, from_address('::ffff:203.0.113.5')),
                         risk.origin_hash(self.a, from_address('203.0.113.5')))
        self.assertNotEqual(risk.origin_hash(self.a, from_address('203.0.113.5')),
                            risk.origin_hash(self.a, from_address('203.0.113.6')))
        self.assertRegex(risk.origin_hash(self.a, from_address('no-es-una-direccion')), r'^[0-9a-f]{64}$')

    def shared(self, n, company=None, *, paid=True, **same):
        """`n` orders by `n` different guests that share one contact detail — PAID unless said otherwise."""
        return [self.order(company, paid=paid, **same) for _ in range(n)]

    def test_velocity_contact_from_the_third_buyer_with_one_phone_in_a_day(self):
        self.assertEqual(risk.VELOCITY_CONTACT_BUYERS, 3)
        self.assertEqual(risk.VELOCITY_CONTACT_WINDOW, timedelta(hours=24))
        self.order(customer_phone='987 111 222', paid=True)
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(self.order(customer_phone='987111222')))
        self.order(user=self.buyer('otra'), customer_phone='+51987111222', paid=True)
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
            last = self.order(user=user, customer_phone='987111222', paid=True)
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(last))

        # One browser trying its payment again is one guest.
        for _ in range(4):
            last = self.order(customer_phone='987333444', cart_session_key='un-mismo-carrito', paid=True)
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(last))

    def test_velocity_contact_counts_other_buyers_only_once_they_paid(self):
        # F3 · two unpaid orders carrying somebody's phone must not mark that person's next order.
        self.shared(5, paid=False, customer_phone='987111222', customer_email='victima@correo.test',
                    document_type='dni', document_number='40005555')
        order = self.order(customer_phone='987111222', customer_email='victima@correo.test',
                           document_type='dni', document_number='40005555')
        self.assertNotIn(risk.SIGNAL_VELOCITY_CONTACT, self.signals(order))

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
        self.shared(4, customer_phone='', customer_email='', document_type='', document_number='')      # paid, and empty
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
        order = self.order(user=user)
        row = self.assess(order)
        self.assertEqual(row.signals, [risk.SIGNAL_ARCHIVED_CUSTOMER])
        self.assertEqual(row.level, risk.level_for(risk.WEIGHTS[risk.SIGNAL_ARCHIVED_CUSTOMER]))
        self.assertNotEqual(row.level, Level.HIGH)
        self.assertTrue(risk.needs_review(row))
        self.assertEqual(self.paid_now(order).review_status, Review.PENDING)

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

        self.assertFalse(risk.needs_review(medium))
        self.assertEqual(self.paid_now(medium.order).review_status, Review.NOT_REQUIRED)

        for _ in range(2):
            self.assess(self.order(paid=True), '198.51.100.7')
        order = self.order(total='2000.00')
        high = self.assess(order, '198.51.100.7')
        self.assertGreaterEqual(high.score, risk.HIGH_SCORE)
        self.assertEqual(high.level, Level.HIGH)
        self.assertTrue(risk.needs_review(high))
        self.assertEqual(self.paid_now(order).review_status, Review.PENDING)


@override_settings(ALLOWED_HOSTS=['*'], **IZIPAY_TEST_SETTINGS)
class _CheckoutBase(TestCase):
    """A shop that sells online: one company, one branch with stock, a web and an app checkout."""

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



class AssessedAtCheckoutTest(_CheckoutBase):
    """Where an online order is made — and only there."""

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
            # A refusal of the DATABASE, the kind that poisons a transaction:
            # a row its own rule forbids, written past the model's checks.
            Customer.objects.bulk_create([Customer(company=order.company)])

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
            'created_at', 'paid_at', 'items', 'delivery',
        ])
        self.assertEqual(OrderSerializer.Meta.fields, [
            'id', 'user', 'customer_name', 'customer_email', 'total', 'discount_amount',
            'coupon_code', 'status', 'paid', 'paid_at', 'created_at', 'items', 'delivery',
        ])


# =============================================================================
# 5 · whoever may decide is told there is something to decide
# =============================================================================

from store import notification_events as events  # noqa: E402
from store.models import Notification, NotificationDelivery, NotificationEvent  # noqa: E402


class ReviewNeededNotificationTest(_RiskBase):
    def setUp(self):
        super().setUp()
        self.north = _p2d_branch(self.a, 'Norte')
        self.south = _p2d_branch(self.a, 'Sur')
        self.manager, _ = _p2d_member(self.a, 'gestora', ['company.view', VIEW, MANAGE])
        self.northerner, _ = _p2d_member(self.a, 'solo_norte', ['company.view', VIEW, MANAGE], branches=[self.north])
        self.viewer, _ = _p2d_member(self.a, 'solo_ver', ['company.view', VIEW])
        self.foreigner, _ = _p2d_member(self.b, 'gestora_b', ['company.view', VIEW, MANAGE])
        self.master = User.objects.create_superuser('maestra', 'maestra@correo.test', PASSWORD)
        Customer.objects.create(
            company=self.a, first_name='Archivada', last_name='Reservada', document_type='dni',
            document_number='40008888', is_active=False,
        )

    def suspicious(self, branch, company=None, **extra):
        """An order that asks for a person: its document is an archived client's."""
        return self.order(
            company, fulfillment_branch=branch, total='4321.00', customer_name='Archivada Reservada',
            customer_email='reservada@correo.test', customer_phone='987000999', document_number='40008888', **extra,
        )

    def flag(self, order):
        """Assessed when it was made, and then paid: the moment a review can start to wait."""
        self.assess(order)
        return self.paid_now(order)

    def told(self, order):
        return set(
            Notification.objects.filter(event__event_type=events.COMMERCE_ORDER_REVIEW_NEEDED, target_id=order.pk)
            .values_list('user__username', flat=True)
        )

    def test_the_event_is_in_the_catalogue_and_is_not_a_mail(self):
        self.assertIn(events.COMMERCE_ORDER_REVIEW_NEEDED, events.ALL_EVENT_TYPES)
        self.assertNotIn(events.COMMERCE_ORDER_REVIEW_NEEDED, events.EMAIL_WORTHY_EVENTS)

    def test_a_pending_review_tells_whoever_may_decide_it_in_that_branch(self):
        order = self.suspicious(self.north)
        row = self.flag(order)
        self.assertEqual(row.review_status, Review.PENDING)

        self.assertEqual(self.told(order), {'gestora', 'solo_norte'})
        event = NotificationEvent.objects.get(event_type=events.COMMERCE_ORDER_REVIEW_NEEDED)
        self.assertEqual((event.company, event.target_type, event.target_id), (self.a, 'order', order.pk))
        self.assertEqual(event.payload, {'level': row.level})

        notice = Notification.objects.get(user=self.manager)
        self.assertEqual(
            (notice.company, notice.audience, notice.priority, notice.target_type, notice.target_id),
            (self.a, Notification.Audience.INTERNAL, Notification.Priority.WARNING, 'order', order.pk),
        )
        self.assertFalse(NotificationDelivery.objects.exists())               # in the panel's bell, nowhere else

    def test_it_says_which_order_and_how_it_looks_and_nothing_about_the_buyer(self):
        order = self.suspicious(self.north)
        row = self.flag(order)
        notice = Notification.objects.get(user=self.manager)
        text = f'{notice.title} {notice.body}'
        self.assertIn(f'#{order.pk}', text)
        self.assertIn(row.get_level_display().lower(), text.lower())
        event = NotificationEvent.objects.get()
        said = text + json.dumps(event.payload) + event.event_key
        for value in ('Archivada', 'Reservada', 'reservada@correo.test', '987000999', '40008888', '4321', 'archiv'):
            self.assertNotIn(value, said)

    def test_an_order_nobody_paid_for_asks_for_nobody_and_tells_nobody(self):
        # F3 · an unpaid order costs nothing to make: it must not fill the queue or ring the bell.
        orders = [self.suspicious(self.north) for _ in range(10)]
        rows = [self.assess(order) for order in orders]
        self.assertTrue(all(risk.needs_review(row) for row in rows))        # what was noticed is still written down
        self.assertEqual({row.review_status for row in rows}, {Review.NOT_REQUIRED})
        self.assertFalse(NotificationEvent.objects.exists())
        self.assertFalse(Notification.objects.exists())
        # Asked again without a payment, nothing changes.
        self.assertIsNone(risk.review_when_paid(orders[0]))
        self.assertEqual(OrderRiskAssessment.objects.get(order=orders[0]).review_status, Review.NOT_REQUIRED)
        self.assertFalse(Notification.objects.exists())

    def test_paying_it_is_what_puts_it_in_the_queue_once(self):
        order = self.suspicious(self.north)
        self.assess(order)
        row = self.paid_now(order)
        self.assertEqual(row.review_status, Review.PENDING)
        self.assertEqual(self.told(order), {'gestora', 'solo_norte'})
        # A notification replayed, a reconciliation after it: one review, one notice each.
        risk.review_when_paid(order)
        risk.review_when_paid(order)
        self.assertEqual(NotificationEvent.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.manager).count(), 1)

    def test_a_decision_already_made_is_not_reopened_by_a_late_confirmation(self):
        order = self.suspicious(self.north)
        self.assess(order)
        self.paid_now(order)
        risk.review(order=order, company=self.a, actor=self.manager, decision='reject')
        risk.review_when_paid(order)
        row = OrderRiskAssessment.objects.get(order=order)
        self.assertEqual((row.review_status, row.reviewed_by), (Review.REJECTED, self.manager))

    def test_an_order_that_was_never_assessed_is_left_alone(self):
        order = _order(self.a, total='50.00', paid=True)
        self.assertIsNone(risk.review_when_paid(order))
        self.assertFalse(OrderRiskAssessment.objects.filter(order=order).exists())

    def test_it_can_never_fail_what_called_it(self):
        order = self.suspicious(self.north)
        self.assess(order)
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID, paid=True)
        order.refresh_from_db()

        def broken(order):
            Customer.objects.bulk_create([Customer(company=order.company)])   # the database refuses, mid-transaction

        with patch('store.order_risk._review_when_paid', side_effect=broken), self.assertLogs('store.order_risk', level='ERROR') as logged:
            self.assertIsNone(risk.review_when_paid(order))
        self.assertTrue(Order.objects.filter(pk=order.pk, paid=True).exists())          # the transaction is still usable
        self.assertNotIn('reservada@correo.test', '\n'.join(logged.output))

    def test_a_branch_scope_holds(self):
        order = self.suspicious(self.south)
        self.flag(order)
        self.assertEqual(self.told(order), {'gestora'})

    def test_an_order_no_branch_answers_for_goes_to_whoever_sees_the_whole_company(self):
        order = self.suspicious(None)
        self.flag(order)
        self.assertEqual(self.told(order), {'gestora'})

    def test_another_companys_staff_are_never_told(self):
        order = self.suspicious(None, self.b)                                 # that document is archived in A, not here
        Customer.objects.create(company=self.b, first_name='Otra', document_type='dni', document_number='40008887', is_active=False)
        theirs = self.order(self.b, document_number='40008887')
        self.flag(order)
        self.flag(theirs)
        self.assertEqual(self.told(order), set())
        self.assertEqual(self.told(theirs), {'gestora_b'})
        self.assertFalse(Notification.objects.filter(company=self.a).exists())

    def test_an_order_that_asks_for_nobody_tells_nobody(self):
        row = self.flag(self.order(fulfillment_branch=self.north))
        self.assertEqual(row.review_status, Review.NOT_REQUIRED)
        self.assertFalse(NotificationEvent.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_a_notice_that_cannot_be_written_costs_neither_the_assessment_nor_the_sale(self):
        def broken(**kwargs):
            # The database refuses, mid-transaction (a row past the model's checks).
            Customer.objects.bulk_create([Customer(company=kwargs['company'])])

        order = self.suspicious(self.north)
        with patch('store.commerce_notifications.notif.emit', side_effect=broken):
            row = self.flag(order)
        self.assertIsNotNone(row)
        self.assertEqual(OrderRiskAssessment.objects.get(order=order).review_status, Review.PENDING)
        self.assertFalse(Notification.objects.exists())
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())


# =============================================================================
# 6 · a company may wait for the review before an order leaves — if it says so
# =============================================================================

from store import order_fulfillment_services as fulfillment  # noqa: E402

SETTINGS_URL = '/api/admin/company-settings/'


class HoldPendingReviewTest(_ReviewBase):
    def hold(self, company=None, on=True):
        CompanySettings.objects.update_or_create(
            company=company or self.a, defaults={'hold_orders_pending_review': on},
        )

    def move(self, status, *, order=None, user=None, surface='panel'):
        order = order or self.order
        url = (
            f'/api/admin/orders/{order.pk}/fulfillment-status/' if surface == 'panel'
            else f'/api/v1/internal/revision-a/orders/{order.pk}/fulfillment/'
        )
        return acting(user or self.manager).patch(url, {'fulfillment_status': status}, format='json')

    def state(self, order=None):
        return Order.objects.get(pk=(order or self.order).pk).fulfillment_status

    def test_it_is_off_unless_a_company_turns_it_on(self):
        self.assertFalse(CompanySettings._meta.get_field('hold_orders_pending_review').default)
        self.assertFalse(CompanySettings.objects.filter(hold_orders_pending_review=True).exists())
        # A company with no settings row at all holds nothing either.
        self.assertFalse(risk.review_holds_dispatch(self.order, self.a))

    def test_off_a_pending_review_holds_nothing(self):
        for surface, status in (('panel', 'shipped'), ('app', 'delivered')):
            with self.subTest(surface):
                response = self.move(status, surface=surface)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(self.state(), status)
        self.assertEqual(self.row.review_status, Review.PENDING)              # and nobody had to decide

    def test_on_an_order_waiting_for_review_is_not_sent_or_handed_over(self):
        self.hold()
        for surface in ('panel', 'app'):
            for status in ('shipped', 'delivered'):
                with self.subTest(surface=surface, status=status):
                    response = self.move(status, surface=surface)
                    self.assertEqual(response.status_code, 409, response.content)
                    body = response.json()
                    self.assertEqual(body['code'], 'review_pending')
                    self.assertIn('revisión', body['detail'])
                    self.assertEqual(self.state(), Order.FulfillmentStatus.PENDING)
        self.assertFalse(AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').exists())

    def test_on_every_other_state_is_free(self):
        self.hold()
        self.assertEqual(fulfillment.HELD_WHILE_REVIEW_PENDING, {'ready_for_pickup', 'shipped', 'delivered'})
        for status in ('confirmed', 'preparing', 'cancelled', 'pending'):
            with self.subTest(status):
                self.assertEqual(self.move(status).status_code, 200)
                self.assertEqual(self.state(), status)

    def test_either_answer_lets_it_go(self):
        self.hold()
        rejected = self.pending(self.a, self.north)
        self.assertEqual(self.move('shipped').status_code, 409)
        self.assertEqual(self.move('shipped', order=rejected).status_code, 409)

        self.decide(self.manager, 'approve')
        self.decide(self.manager, 'reject', order=rejected)
        self.assertEqual(self.move('shipped').status_code, 200)
        self.assertEqual(self.move('delivered', order=rejected, surface='app').status_code, 200)
        # Rejecting did not cancel it: it left because a person looked.
        self.assertEqual(self.state(rejected), 'delivered')

    def test_on_it_holds_only_what_is_waiting(self):
        self.hold()
        quiet = self.pending(self.a, self.north, status=Review.NOT_REQUIRED, level=Level.LOW)
        unassessed = _order(self.a, total='10.00', paid=True, fulfillment_branch=self.north)
        self.assertEqual(self.move('shipped', order=quiet).status_code, 200)
        self.assertEqual(self.move('delivered', order=unassessed).status_code, 200)

    def test_it_is_each_companys_own_option(self):
        self.hold(self.b)
        self.assertEqual(self.move('shipped').status_code, 200)               # A did not turn it on

        self.hold(self.a)
        self.hold(self.b, on=False)
        theirs = acting(self.foreigner).patch(
            f'/api/admin/orders/{self.foreign.pk}/fulfillment-status/', {'fulfillment_status': 'shipped'}, format='json',
        )
        self.assertEqual(theirs.status_code, 200)

    def test_it_is_decided_inside_the_transaction_with_the_order_locked(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.hold()
        with CaptureQueriesContext(connection) as queries, self.assertRaises(fulfillment.FulfillmentBlocked) as blocked:
            fulfillment.change_fulfillment_status(
                order=self.order, new_status='shipped', actor=self.manager, company=self.a,
            )
        self.assertEqual((blocked.exception.code, blocked.exception.pending), ('review_pending', {}))
        statements = [q['sql'] for q in queries.captured_queries]
        lock = next(i for i, sql in enumerate(statements) if 'FOR UPDATE' in sql and 'store_order' in sql)
        asked = next(i for i, sql in enumerate(statements) if 'store_orderriskassessment' in sql)
        self.assertLess(lock, asked)
        self.assertTrue(any('SAVEPOINT' in sql for sql in statements[:lock]))

    def test_the_other_refusal_keeps_its_own_code(self):
        blocked = fulfillment.FulfillmentBlocked('faltan equipos', pending={7: 2})
        self.assertEqual(blocked.code, 'units_pending')


class ReviewOptionsInCompanySettingsTest(TestCase):
    """The two options live in the company's settings, and take two keys to change."""

    def setUp(self):
        cache.clear()
        self.a = _saas_company('Tienda A', 'opciones-a', tax_id='20999999990')
        self.b = _saas_company('Tienda B', 'opciones-b', tax_id='20111111112')
        CompanySettings.objects.create(company=self.a, currency='PEN')
        CompanySettings.objects.create(company=self.b, currency='PEN')
        self.admin, _ = _p2d_member(self.a, 'admin_a', ['company.view', 'company.manage', 'settings.manage'])
        self.company_only, _ = _p2d_member(self.a, 'solo_empresa', ['company.view', 'company.manage'])
        self.settings_only, _ = _p2d_member(self.a, 'solo_config', ['company.view', 'settings.manage'])
        self.viewer, _ = _p2d_member(self.a, 'solo_ver', ['company.view'])
        self.admin_b, _ = _p2d_member(self.b, 'admin_b', ['company.view', 'company.manage', 'settings.manage'])

    def options(self, company=None):
        row = CompanySettings.objects.get(company=company or self.a)
        return row.risk_review_amount, row.hold_orders_pending_review

    def patch(self, user, body, query=''):
        return acting(user).patch(SETTINGS_URL + query, body, format='json')

    def test_they_are_read_with_the_rest_and_start_off(self):
        response = acting(self.viewer).get(SETTINGS_URL)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIsNone(body['settings']['risk_review_amount'])
        self.assertIs(body['settings']['hold_orders_pending_review'], False)
        self.assertIs(body['can_manage_order_review'], False)
        self.assertIs(acting(self.admin).get(SETTINGS_URL).json()['can_manage_order_review'], True)
        self.assertIs(acting(self.company_only).get(SETTINGS_URL).json()['can_manage_order_review'], False)
        self.assertIs(acting(self.settings_only).get(SETTINGS_URL).json()['can_manage_order_review'], False)

    def test_whoever_administers_the_company_and_its_configuration_sets_them(self):
        response = self.patch(self.admin, {'risk_review_amount': '1500.00', 'hold_orders_pending_review': True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.options(), (Decimal('1500.00'), True))
        self.assertEqual(response.json()['settings']['risk_review_amount'], '1500.00')

        entry = AdminAuditLog.objects.get(action='company_settings_updated')
        self.assertEqual((entry.actor, entry.company), (self.admin, self.a))
        self.assertEqual(entry.metadata['changed_fields'], ['hold_orders_pending_review', 'risk_review_amount'])
        self.assertNotIn('1500', json.dumps(entry.metadata))

        self.assertEqual(self.patch(self.admin, {'risk_review_amount': None}).status_code, 200)
        self.assertEqual(self.options(), (None, True))

    def test_administering_the_company_alone_is_not_enough(self):
        for body in ({'risk_review_amount': '10.00'}, {'hold_orders_pending_review': True},
                     {'phone': '+51 987 000 111', 'hold_orders_pending_review': True}):
            with self.subTest(body):
                response = self.patch(self.company_only, body)
                self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(self.options(), (None, False))
        self.assertEqual(CompanySettings.objects.get(company=self.a).phone, '')       # nothing of a refused save is kept
        self.assertFalse(AdminAuditLog.objects.filter(action='company_settings_updated').exists())

    def test_the_rest_of_the_screen_is_whose_it_was(self):
        # What `company.manage` could change before, it still changes — also when
        # the form sends the two options back exactly as they are.
        response = self.patch(self.company_only, {
            'phone': '+51 987 000 111', 'risk_review_amount': None, 'hold_orders_pending_review': False,
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(CompanySettings.objects.get(company=self.a).phone, '+51 987 000 111')

    def test_neither_is_configuration_alone_nor_reading(self):
        for user in (self.settings_only, self.viewer):
            self.assertEqual(self.patch(user, {'hold_orders_pending_review': True}).status_code, 403)
        self.assertEqual(self.options(), (None, False))

    def test_an_amount_is_a_positive_amount(self):
        for value in ('0', '-5', 'mucho', '0.001', '12345678901234.00'):
            with self.subTest(value):
                response = self.patch(self.admin, {'risk_review_amount': value})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('risk_review_amount', response.json())
        self.assertEqual(self.patch(self.admin, {'hold_orders_pending_review': 'quizás'}).status_code, 400)
        self.assertEqual(self.options(), (None, False))

    def test_another_companys_options_are_out_of_reach(self):
        response = self.patch(self.admin, {'hold_orders_pending_review': True}, f'?company={self.b.pk}')
        self.assertEqual(response.status_code, 404)
        self.patch(self.admin, {'hold_orders_pending_review': True, 'risk_review_amount': '99.00'})
        self.assertEqual(self.options(self.b), (None, False))

    @override_settings(ALLOWED_HOSTS=['*'])
    def test_the_public_never_learns_them(self):
        self.patch(self.admin, {'hold_orders_pending_review': True, 'risk_review_amount': '777.00'})
        for response in (
            APIClient().get('/api/storefront/config/', HTTP_HOST='opciones-a.example.com'),
            APIClient().get('/api/v1/storefront/opciones-a/config/'),
        ):
            self.assertEqual(response.status_code, 200)
            text = response.content.decode()
            for word in ('risk_review', 'hold_orders', '777'):
                self.assertNotIn(word, text)



# =============================================================================
# 7 · after the independent review
# =============================================================================

from django.core import mail as django_mail  # noqa: E402

from store import commerce_notifications, customer_services, email_services  # noqa: E402


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class GuestOrderAndSomebodyElsesRecordTest(_CheckoutBase):
    """
    F1 · a document typed by a guest never hands their order to an ACCOUNT.

    A record with an account behind it is somebody's login: whoever holds it
    reads the orders linked to it and receives their notices. A guest's
    document is checked for shape only, so it cannot be the key to that.
    """

    VICTIM_DNI = '70000055'

    def squatter(self):
        """An account whose record in this shop carries a stranger's document."""
        user = User.objects.create_user('ocupante', 'ocupante@correo.test', PASSWORD, first_name='Otra')
        record = Customer.objects.create(
            company=self.company, user=user, first_name='Otra', email='ocupante@correo.test',
            document_type='dni', document_number=self.VICTIM_DNI,
        )
        return user, record

    def guest_order(self, **overrides):
        overrides.setdefault('document_number', self.VICTIM_DNI)
        overrides.setdefault('customer_email', 'victima@correo.test')
        self.n = getattr(self, 'n', 0) + 1
        response = self.web_checkout(session=f'carrito-victima-{self.n}', **overrides)
        self.assertEqual(response.status_code, 200, response.content)
        return Order.objects.get(pk=response.json()['order_id'])

    def test_the_order_of_a_guest_is_not_linked_to_an_account_that_holds_their_document(self):
        user, record = self.squatter()
        order = self.guest_order()

        self.assertIsNone(order.user)
        self.assertIsNone(order.customer)                                   # unlinked, with its snapshot intact
        self.assertEqual((order.document_number, order.customer_email), (self.VICTIM_DNI, 'victima@correo.test'))
        self.assertEqual(Customer.objects.filter(company=self.company, document_number=self.VICTIM_DNI).count(), 1)   # no twin
        self.assertFalse(Order.objects.filter(customer=record).exists())

        web = web_session(user).get('/api/orders/', HTTP_HOST='riesgo-caja.example.com')
        self.assertEqual(web.json(), [])
        app = v1_session(user)
        self.assertNotIn(order.pk, [row['id'] for row in app.get('/api/v1/customer/riesgo-caja/orders/').json()])
        self.assertEqual(app.get(f'/api/v1/customer/riesgo-caja/orders/{order.pk}/').status_code, 404)

    def test_neither_its_notices_nor_its_mail_go_to_that_account(self):
        user, record = self.squatter()
        order = self.guest_order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        order.refresh_from_db()
        django_mail.outbox = []

        self.assertIsNone(commerce_notifications.emit_payment_confirmed(order))
        for status in ('ready_for_pickup', 'shipped', 'delivered'):
            self.assertIsNone(commerce_notifications.emit_fulfillment_changed(order, status))
        self.assertFalse(Notification.objects.filter(customer=record).exists())
        self.assertFalse(Notification.objects.filter(company=self.company, audience=Notification.Audience.CUSTOMER).exists())

        # The buyer still gets their own confirmation, at the address they typed.
        self.assertTrue(email_services.send_order_confirmation_email(order))
        recipients = [address for message in django_mail.outbox for address in message.to]
        self.assertEqual(recipients, ['victima@correo.test'])
        self.assertNotIn('ocupante@correo.test', recipients)

    def test_it_cannot_be_re_aimed_at_the_next_stranger(self):
        user, record = self.squatter()
        first = self.guest_order()
        Customer.objects.filter(pk=record.pk).update(document_number='70000066')
        second = self.guest_order(document_number='70000066', customer_email='otra@correo.test')
        self.assertEqual((first.customer, second.customer), (None, None))
        self.assertEqual(web_session(user).get('/api/orders/', HTTP_HOST='riesgo-caja.example.com').json(), [])

    def test_a_guest_whose_document_is_a_walk_in_record_is_still_linked_to_it(self):
        walk_in = Customer.objects.create(
            company=self.company, first_name='Clienta', last_name='De Mostrador', document_type='dni', document_number=self.VICTIM_DNI,
        )
        order = self.guest_order()
        self.assertEqual(order.customer, walk_in)
        self.assertIsNone(walk_in.user)
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 1)

    def test_a_guest_nobody_knows_still_gets_a_record_of_their_own(self):
        order = self.guest_order()
        self.assertIsNotNone(order.customer)
        self.assertEqual((order.customer.user, order.customer.document_number), (None, self.VICTIM_DNI))
        self.assertEqual(self.guest_order().customer, order.customer)       # and the next order finds it

    def test_a_signed_in_sale_is_still_matched_by_its_account_alone(self):
        user, record = self.squatter()
        response = self.web_checkout(web_session(user), session='carrito-propio', document_number='70000077')
        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertEqual((order.user, order.customer), (user, record))
        record.refresh_from_db()
        self.assertEqual(record.document_number, self.VICTIM_DNI)           # a sale does not rewrite the record

    def test_a_record_taken_while_the_guest_was_paying_is_not_adopted_either(self):
        # The fallback after a lost race re-reads by the same key: it must ask the same question.
        from django.db import IntegrityError

        order = _order(self.company, document_type='dni', document_number=self.VICTIM_DNI, customer_name='Víctima Real')
        made = []

        def lost_the_race(*args, **kwargs):
            # Nobody held the document at the first look; an account took it before this record could be written.
            made.append(self.squatter())
            raise IntegrityError('unique_customer_document_per_company')

        with mock.patch('store.customer_services._create_from_order', side_effect=lost_the_race):
            self.assertIsNone(customer_services.link_order_to_customer(order))
        self.assertEqual(len(made), 1)                                      # the fallback WAS reached
        order.refresh_from_db()
        self.assertIsNone(order.customer)
        self.assertFalse(Order.objects.filter(customer=made[0][1]).exists())

    def test_a_walk_in_record_that_won_the_race_is_still_adopted(self):
        from django.db import IntegrityError

        order = _order(self.company, document_type='dni', document_number=self.VICTIM_DNI, customer_name='Víctima Real')
        made = []

        def lost_the_race(*args, **kwargs):
            made.append(Customer.objects.create(company=self.company, first_name='Clienta', document_type='dni', document_number=self.VICTIM_DNI))
            raise IntegrityError('unique_customer_document_per_company')

        with mock.patch('store.customer_services._create_from_order', side_effect=lost_the_race):
            self.assertEqual(customer_services.link_order_to_customer(order), made[0])

    def test_the_panel_finds_and_files_that_document_as_before(self):
        user, record = self.squatter()
        self.assertEqual(customer_services.match_by_document(self.company, 'dni', self.VICTIM_DNI), record)
        self.assertEqual(customer_services.resolve_customer(self.company, document_type='dni', document_number=self.VICTIM_DNI), record)
        with self.assertRaises(customer_services.DuplicateCustomerError):
            customer_services.assert_document_available(self.company, 'dni', self.VICTIM_DNI)


class OnlyPlainDigitsAndNoControlCharactersTest(_ProfileBase):
    """F8, F9 · a digit is 0-9, and a name has no control characters in it."""

    ARABIC_DNI = '٧٠٠٠٠٠٠١'                 # «70000001» in Arabic-Indic digits
    FULLWIDTH_DNI = '７００００００１'

    def test_digits_of_another_script_are_not_a_document(self):
        for kind, typed in (('dni', self.ARABIC_DNI), ('dni', self.FULLWIDTH_DNI), ('dni', '7000000١'),
                            ('ruc', '٢٠٩٩٩٩٩٩٩٩٠'), ('ruc', '2099999999٠')):
            with self.subTest(typed), self.assertRaises(identity.IdentityError):
                identity.clean_document(kind, typed)
        self.assertFalse(identity.ruc_check_digit_matches('٢٠٩٩٩٩٩٩٩٩٠'))

    def test_nor_a_phone(self):
        for typed in ('٩٨٧٦٥٤٣٢١', '98765432١', '+51 ９８７ ６５４ ３２１'):
            with self.subTest(typed), self.assertRaises(identity.IdentityError):
                identity.clean_phone(typed)
        self.assertEqual(identity.phone_key('٩٨٧٦٥٤٣٢١'), '')
        self.assertEqual(identity.phone_key('987 654 321'), '987654321')

    def test_they_do_not_slip_past_the_rule_of_one_document_per_client(self):
        Customer.objects.create(company=self.a, first_name='Otra', document_type='dni', document_number=DNI)
        response = self.patch({'document_type': 'dni', 'document_number': self.ARABIC_DNI})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Customer.objects.filter(document_number=self.ARABIC_DNI).exists())

    def test_a_control_character_in_a_name_is_refused_not_an_error(self):
        for field, typed in (('first_name', 'Ana\x00'), ('last_name', 'To\x00rres'), ('first_name', 'Ana\x1b[31m'),
                             ('last_name', 'Torres\r\nX-Cabecera: 1'), ('first_name', 'Ana\x7f')):
            with self.subTest(typed=typed):
                response = self.patch({field: typed, 'phone': '987654321'})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
        self.assertIsNone(self.record())
        self.assertEqual(self.patch({'first_name': 'Ana María', 'last_name': "D'Ávila-Ñuñez"}).status_code, 200)



class WhatTheShopRecordedIsNotTheBuyersToRewriteTest(_ProfileBase):
    """
    F5 · the subject of a file corrects their contact details. They do not
    rewrite what the shop relies on: an archived file, or the document of a
    file the shop made or has already sold on.
    """

    LOCKED = 'El documento de tu ficha lo cambia la tienda'

    def staff(self):
        return User.objects.get_or_create(username='personal', defaults={'email': 'personal@tienda.test'})[0]

    def test_nothing_of_an_archived_file_changes_and_nothing_says_so(self):
        self.patch({'phone': '987654321', 'document_type': 'dni', 'document_number': DNI})
        Customer.objects.filter(user=self.user).update(is_active=False)
        kept = Customer.objects.filter(user=self.user).values().get()
        audits = AdminAuditLog.objects.filter(action='customer_profile_updated').count()

        for body in ({'phone': '912345678'}, {'first_name': 'Otra'}, {'document_type': 'dni', 'document_number': '70000009'},
                     {'document_type': '', 'document_number': ''}, {'phone': 'no-es-un-teléfono'}):
            with self.subTest(body):
                response = self.patch(body)
                self.assertEqual(response.status_code, 200, response.content)      # the answer of an unchanged save
                self.assertEqual(response.json(), self.get().json())
                self.assertEqual(response.json()['phone'], '987654321')
        self.assertEqual(Customer.objects.filter(user=self.user).values().get(), kept)
        self.assertEqual(AdminAuditLog.objects.filter(action='customer_profile_updated').count(), audits)
        text = json.dumps(self.get().json())
        for hint in ('archiv', 'is_active', 'inactiv'):
            self.assertNotIn(hint, text)

    def test_the_document_is_frozen_once_the_shop_has_sold_on_it(self):
        self.patch({'document_type': 'dni', 'document_number': DNI})
        self.assertFalse(self.get().json()['document_locked'])
        _order(self.a, user=self.user, total='50.00')                       # never paid: nothing relies on it yet
        self.assertFalse(self.get().json()['document_locked'])
        _order(self.b, user=self.user, total='50.00', paid=True)            # paid, in another shop
        self.assertFalse(self.get().json()['document_locked'])

        _order(self.a, user=self.user, total='50.00', paid=True)
        self.assertTrue(self.get().json()['document_locked'])
        for body in ({'document_type': 'dni', 'document_number': '70000009'}, {'document_type': 'ce', 'document_number': 'AB12345'}):
            with self.subTest(body):
                response = self.patch({**body, 'phone': '912345678'})
                self.assertEqual(response.status_code, 400)
                self.assertIn(self.LOCKED, response.json()['document_number'][0])
        record = self.record()
        self.assertEqual((record.document_number, record.phone), (DNI, ''))
        # The rest of the file is still theirs to correct, also when the form sends the document back as it is.
        self.assertEqual(self.patch({'phone': '912345678', 'document_type': 'dni', 'document_number': DNI}).status_code, 200)
        self.assertEqual(self.record().phone, '912345678')

    def test_a_paid_order_linked_to_the_file_counts_as_well(self):
        self.patch({'document_type': 'dni', 'document_number': DNI})
        _order(self.a, customer=self.record(), total='50.00', paid=True)    # a counter sale the shop put on this file
        self.assertTrue(self.get().json()['document_locked'])

    def test_the_document_of_a_file_the_shop_made_is_frozen(self):
        Customer.objects.create(
            company=self.a, user=self.user, first_name='Ana', document_type='dni', document_number=DNI, created_by=self.staff(),
        )
        self.assertTrue(self.get().json()['document_locked'])
        response = self.patch({'document_type': 'dni', 'document_number': '70000009'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.record().document_number, DNI)
        # A file the shop made WITHOUT a document: the buyer may not be the one to give it one either.
        other = User.objects.create_user('otro', 'otro@correo.test', PASSWORD, first_name='Otro')
        Customer.objects.create(company=self.a, user=other, first_name='Otro', created_by=self.staff())
        self.assertEqual(self.patch({'document_type': 'dni', 'document_number': '70000008'}, client=web_session(other)).status_code, 400)

    def test_a_file_the_buyer_made_themself_is_not_frozen_by_that(self):
        self.patch({'document_type': 'dni', 'document_number': DNI})
        self.assertEqual(self.record().created_by, self.user)
        self.assertFalse(self.get().json()['document_locked'])
        self.assertEqual(self.patch({'document_type': 'dni', 'document_number': '70000009'}).status_code, 200)


WEB_VERIFY_SEND = '/api/account/verify-email/send/'
WEB_VERIFY_CONFIRM = '/api/account/verify-email/confirm/'
CODE_REFUSED = {'detail': 'El código no es válido o ya venció. Pide uno nuevo.'}


@override_settings(
    ALLOWED_HOSTS=['*'], FRONTEND_URL='https://tienda.test',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class _OwnAddressBase(_ProfileBase):
    """An account that signs in and has not proved its mailbox, and the two steps of F7."""

    def setUp(self):
        super().setUp()
        self.unverified()
        django_mail.outbox = []

    def send(self, client=None, body=None, company=None):
        cache.clear()
        return (client or self.web).post(WEB_VERIFY_SEND, body or {}, format='json', **self.host(company or self.a))

    def confirm(self, code, client=None, company=None):
        cache.clear()
        return (client or self.web).post(WEB_VERIFY_CONFIRM, {'code': code}, format='json', **self.host(company or self.a))

    def code(self, message=None) -> str:
        return re.search(r'Tu código: (\d{6})\b', (message or django_mail.outbox[-1]).body).group(1)

    def wrong(self) -> str:
        return '000000' if self.code() != '000000' else '111111'


class VerifyMyOwnAddressTest(_OwnAddressBase):
    """
    F7 · an account that can already sign in proves its mailbox: a 6-digit code
    to ITS OWN address, with the rules the registration code has.
    """

    def test_it_is_for_whoever_is_signed_in(self):
        guest = APIClient()
        self.assertEqual(guest.post(WEB_VERIFY_SEND, {}, format='json', **self.host(self.a)).status_code, 401)
        self.assertEqual(guest.post(WEB_VERIFY_CONFIRM, {'code': '123456'}, format='json', **self.host(self.a)).status_code, 401)
        self.assertEqual(django_mail.outbox, [])

    def test_the_profile_says_when_there_is_something_to_verify(self):
        self.assertTrue(self.get().json()['email_verification_available'])
        accounts.mark_email_verified(self.user)
        self.assertFalse(self.get().json()['email_verification_available'])
        nameless = User.objects.create_user('sin_correo', '', PASSWORD, first_name='Sin')
        self.assertFalse(self.get(client=web_session(nameless)).json()['email_verification_available'])

    def test_the_code_goes_to_the_accounts_own_address_and_nowhere_else(self):
        response = self.send(body={'email': 'otra@correo.test', 'to': 'otra@correo.test', 'user': 999})
        self.assertEqual(response.status_code, 200, response.content)
        [message] = django_mail.outbox
        self.assertEqual(message.to, ['ana@correo.test'])
        self.assertRegex(self.code(), r'^\d{6}$')
        self.assertNotIn(self.code(), response.content.decode())            # the answer never carries the code
        self.assertIsNone(verified_at(self.user))                           # asking proves nothing

        entry = AdminAuditLog.objects.get(action='account_email_verification_sent')
        self.assertEqual((entry.actor, entry.company, entry.target_type, entry.target_id), (self.user, self.a, 'user', str(self.user.pk)))
        self.assertNotIn(self.code(), json.dumps(entry.metadata))
        self.assertNotIn('ana@correo.test', json.dumps(entry.metadata))

    def test_the_right_code_proves_the_mailbox(self):
        self.send()
        before = timezone.now()
        response = self.confirm(f' {self.code()[:3]} {self.code()[3:]} ')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertGreaterEqual(verified_at(self.user), before)
        self.assertEqual(parse_datetime(response.json()['email_verified_at']), verified_at(self.user))
        body = self.get().json()
        self.assertEqual((parse_datetime(body['email_verified_at']), body['email_verification_available']), (verified_at(self.user), False))
        entry = AdminAuditLog.objects.get(action='account_email_verified')
        self.assertEqual((entry.actor, entry.company), (self.user, self.a))
        self.assertNotIn(self.code(), json.dumps(entry.metadata))
        # Spent: it does not work twice.
        self.assertEqual(self.confirm(self.code()).json(), CODE_REFUSED)

    def test_one_answer_for_every_refusal_and_five_wrong_tries_kill_the_code(self):
        self.assertEqual(self.confirm('123456').status_code, 400)           # none was ever sent
        self.send()
        for typed in (self.wrong(), '12345', 'abcdef', '', None, 123456):
            cache.clear()
            response = self.web.post(WEB_VERIFY_CONFIRM, {'code': typed}, format='json', **self.host(self.a))
            self.assertEqual((response.status_code, response.json()), (400, CODE_REFUSED), typed)
        self.assertIsNone(verified_at(self.user))
        # Not-a-code tries cost nothing; wrong codes do.
        for _ in range(4):
            self.confirm(self.wrong())
        self.assertEqual(self.confirm(self.code()).json(), CODE_REFUSED)    # the fifth wrong try killed it
        self.assertIsNone(verified_at(self.user))

    def test_a_code_lasts_a_quarter_of_an_hour(self):
        self.send()
        AccountToken.objects.filter(user=self.user).update(code_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.confirm(self.code()).json(), CODE_REFUSED)
        self.assertIsNone(verified_at(self.user))

    def test_one_code_a_minute_and_five_a_day_whoever_asks(self):
        self.assertEqual(self.send().status_code, 200)
        again = self.send()
        self.assertEqual(again.status_code, 429)
        self.assertEqual(len(django_mail.outbox), 1)
        for n in range(4):
            AccountToken.objects.filter(user=self.user).update(created_at=timezone.now() - timedelta(minutes=5 * (n + 1)))
            self.assertEqual(self.send().status_code, 200)
        AccountToken.objects.filter(user=self.user).update(created_at=timezone.now() - timedelta(minutes=30))
        self.assertEqual(self.send().status_code, 429)                      # the day's five are spent
        self.assertEqual(len(django_mail.outbox), 5)

    def test_a_new_code_kills_the_one_before(self):
        self.send()
        old = self.code()
        AccountToken.objects.filter(user=self.user).update(created_at=timezone.now() - timedelta(minutes=2))
        self.send()
        new = self.code()
        # Only the newest row still carries a code at all.
        self.assertEqual(AccountToken.objects.filter(user=self.user).exclude(code_hash='').count(), 1)
        if old != new:
            self.assertEqual(self.confirm(old).json(), CODE_REFUSED)
        self.assertEqual(self.confirm(new).status_code, 200)

    def test_a_code_is_one_accounts_and_no_others(self):
        other = User.objects.create_user('luis', 'luis@correo.test', PASSWORD, first_name='Luis')
        self.send(client=web_session(other))
        his = self.code()
        self.assertEqual(django_mail.outbox[-1].to, ['luis@correo.test'])
        self.assertEqual(self.confirm(his).json(), CODE_REFUSED)            # typed into Ana's session
        self.assertIsNone(verified_at(self.user))
        self.assertIsNone(verified_at(other))

    def test_an_address_already_verified_is_sent_nothing(self):
        accounts.mark_email_verified(self.user)
        response = self.send()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['already_verified'])
        self.assertEqual(django_mail.outbox, [])
        self.assertFalse(AccountToken.objects.filter(user=self.user).exists())

    def test_a_mail_that_did_not_leave_costs_nothing(self):
        with mock.patch('store.account_views.send_own_email_code', return_value=False):
            response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(AccountToken.objects.filter(user=self.user).exists())
        self.assertEqual(self.send().status_code, 200)                      # no wait was started

    def test_that_mail_carries_the_code_and_no_link(self):
        # A link in it would be a door for whoever cannot sign in; the code needs the session.
        self.send()
        [message] = django_mail.outbox
        everything = message.body + ''.join(str(body) for body, _kind in getattr(message, 'alternatives', []))
        self.assertRegex(self.code(), r'^\d{6}$')
        for forbidden in ('token=', 'verify-email', 'http://', 'https://'):
            self.assertNotIn(forbidden, everything)

    def test_it_is_a_token_of_its_own_kind(self):
        self.send()
        self.assertEqual(
            list(AccountToken.objects.filter(user=self.user).values_list('purpose', flat=True)),
            [AccountToken.PURPOSE_OWN_EMAIL_PROOF],
        )
        self.assertIn(AccountToken.PURPOSE_OWN_EMAIL_PROOF, dict(AccountToken.PURPOSE_CHOICES))

    def test_the_code_is_for_the_address_it_was_sent_to(self):
        # The proof is of ONE mailbox: an account that changes its address afterwards has not read the new one.
        self.send()
        code = self.code()
        User.objects.filter(pk=self.user.pk).update(email='otra@correo.test')
        self.assertEqual(self.confirm(code).json(), CODE_REFUSED)
        self.assertIsNone(verified_at(self.user))
        User.objects.filter(pk=self.user.pk).update(email='ANA@correo.test')         # the same mailbox, written otherwise
        self.assertEqual(self.confirm(code).status_code, 200)

    def test_it_does_not_spend_the_registration_resends(self):
        from store import verification_codes

        self.send()
        self.user.is_active = False                                         # only to ask the registration budget
        self.assertEqual(
            AccountToken.objects.filter(user=self.user, purpose=AccountToken.PURPOSE_EMAIL_VERIFICATION).count(), 0,
        )
        self.assertIsNotNone(verification_codes.issue(self.user))

    def test_the_old_road_for_accounts_that_cannot_sign_in_yet_is_what_it_was(self):
        # A code sent to a signed-in account does not open the unauthenticated endpoint.
        self.send()
        cache.clear()
        response = APIClient().post('/api/auth/verify-email/code/', json.dumps({'email': 'ana@correo.test', 'code': self.code()}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(verified_at(self.user))

    def test_both_steps_are_limited_per_account(self):
        from store.account_views import AccountVerifyEmailConfirmView, AccountVerifyEmailSendView
        from store.throttles import AccountVerifyConfirmThrottle, AccountVerifySendThrottle

        self.assertEqual([c for c in AccountVerifyEmailSendView.throttle_classes], [AccountVerifySendThrottle])
        self.assertEqual([c for c in AccountVerifyEmailConfirmView.throttle_classes], [AccountVerifyConfirmThrottle])
        limit = AccountVerifyConfirmThrottle().num_requests
        cache.clear()
        statuses = [self.web.post(WEB_VERIFY_CONFIRM, {'code': '1'}, format='json', **self.host(self.a)).status_code for _ in range(limit + 1)]
        self.assertEqual((set(statuses[:-1]), statuses[-1]), ({400}, 429))

    def test_the_app_has_the_same_two_steps(self):
        _order(self.a, user=self.user, total='10.00', paid=True)
        app = v1_session(self.user)
        cache.clear()
        self.assertEqual(app.post('/api/v1/customer/perfil-a/verify-email/send/', {}, format='json').status_code, 200)
        self.assertEqual(django_mail.outbox[-1].to, ['ana@correo.test'])
        cache.clear()
        self.assertEqual(app.post('/api/v1/customer/perfil-a/verify-email/confirm/', {'code': self.wrong()}, format='json').json(), CODE_REFUSED)
        cache.clear()
        done = app.post('/api/v1/customer/perfil-a/verify-email/confirm/', {'code': self.code()}, format='json')
        self.assertEqual(done.status_code, 200, done.content)
        self.assertIsNotNone(verified_at(self.user))
        cache.clear()
        self.assertEqual(app.post('/api/v1/customer/perfil-b/verify-email/send/', {}, format='json').status_code, 404)
        self.assertEqual(self.web.post('/api/v1/customer/perfil-a/verify-email/send/', {}, format='json').status_code, 401)


@override_settings(FRONTEND_URL='https://tienda.test', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EveryConsumedResetProvesTheMailboxTest(TestCase):
    """F7 · a recovery link read in the mailbox proves it, whether or not the account was already active."""

    def test_an_active_account_that_resets_its_password_is_verified(self):
        cache.clear()
        user = User.objects.create_user('ana', 'ana@correo.test', PASSWORD)
        self.assertIsNone(verified_at(user))
        django_mail.outbox = []
        client = APIClient()
        client.post('/api/auth/password-reset/request/', {'email': 'ana@correo.test'}, format='json')
        token = re.search(r'[?&]token=([A-Za-z0-9_\-]+)', django_mail.outbox[-1].body).group(1)
        cache.clear()
        bad = client.post('/api/auth/password-reset/confirm/', {'token': 'x' * 40, 'new_password': 'Otra-clave-larga-7'}, format='json')
        self.assertEqual(bad.status_code, 400)
        self.assertIsNone(verified_at(user))                                 # a token that was not consumed proves nothing
        cache.clear()
        done = client.post('/api/auth/password-reset/confirm/', {'token': token, 'new_password': 'Otra-clave-larga-7'}, format='json')
        self.assertEqual(done.status_code, 200, done.content)
        self.assertIsNotNone(verified_at(user))



class ADocumentTakesAProvedAddressAndHasItsOwnBudgetTest(_ProfileBase):
    """
    F2 · saving a document answers whether that document can be kept, which is
    a question about the shop's other clients. Only an account with a proved
    mailbox may ask it, and only a few times a day.
    """

    NEEDS_PROOF = 'Verifica tu correo'

    def raw_patch(self, body, client=None):
        """Without clearing the limits between calls."""
        return (client or self.web).patch(WEB_PROFILE, body, format='json', **self.host(self.a))

    def test_an_account_nobody_proved_keeps_its_name_and_phone_editable_but_not_its_document(self):
        self.unverified()
        self.assertEqual(self.patch({'first_name': 'Ana María', 'phone': '987654321'}).status_code, 200)
        response = self.patch({'document_type': 'dni', 'document_number': DNI, 'phone': '912345678'})
        self.assertEqual(response.status_code, 400)
        self.assertIn(self.NEEDS_PROOF, response.json()['document_number'][0])
        record = self.record()
        self.assertEqual((record.document_number, record.phone, record.first_name), ('', '987654321', 'Ana María'))

    def test_the_refusal_is_the_same_whether_the_document_is_somebodys_or_not(self):
        self.unverified()
        Customer.objects.create(company=self.a, first_name='Otra', document_type='dni', document_number='70000002')
        taken, free = (self.patch({'document_type': 'dni', 'document_number': number}) for number in ('70000002', '70000003'))
        self.assertEqual((taken.status_code, taken.content), (free.status_code, free.content))

    def test_once_proved_the_document_is_saved(self):
        self.unverified()
        self.assertEqual(self.patch({'document_type': 'dni', 'document_number': DNI}).status_code, 400)
        accounts.mark_email_verified(self.user)
        self.assertEqual(self.patch({'document_type': 'dni', 'document_number': DNI}).status_code, 200)

    def test_the_app_asks_for_the_same(self):
        self.unverified()
        _order(self.a, user=self.user, total='10.00', paid=True)
        cache.clear()
        response = v1_session(self.user).patch(v1_profile('perfil-a'), {'document_type': 'dni', 'document_number': DNI}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn(self.NEEDS_PROOF, response.json()['document_number'][0])

    def test_a_few_document_changes_a_day_refusals_included(self):
        from store import customer_profile

        self.assertEqual(customer_profile.DOCUMENT_CHANGES_PER_DAY, 3)
        Customer.objects.create(company=self.a, first_name='Otra', document_type='dni', document_number='70000002')
        cache.clear()
        answers = [
            self.raw_patch({'document_type': 'dni', 'document_number': '70000002'}),       # somebody's: refused, and counted
            self.raw_patch({'document_type': 'dni', 'document_number': '70000003'}),       # saved
            self.raw_patch({'document_type': 'dni', 'document_number': '70000004'}),       # saved
        ]
        self.assertEqual([r.status_code for r in answers], [400, 200, 200])
        for number in ('70000005', '70000002'):
            spent = self.raw_patch({'document_type': 'dni', 'document_number': number})
            self.assertEqual(spent.status_code, 400)
            self.assertIn('mañana', spent.json()['document_number'][0])                   # free or taken: one answer
        self.assertEqual(self.record().document_number, '70000004')
        # The rest of the profile is not what was being limited.
        self.assertEqual(self.raw_patch({'phone': '987654321'}).status_code, 200)
        self.assertEqual(self.raw_patch({'phone': '912345678', 'document_type': 'dni', 'document_number': '70000004'}).status_code, 200)

    def test_what_is_not_a_document_at_all_spends_nothing(self):
        cache.clear()
        for _ in range(6):
            self.assertEqual(self.raw_patch({'document_type': 'dni', 'document_number': '123'}).status_code, 400)
        self.assertEqual(self.raw_patch({'document_type': 'dni', 'document_number': DNI}).status_code, 200)

    def test_the_budget_is_each_accounts_own_and_spans_shops_and_doors(self):
        cache.clear()
        for number in ('70000003', '70000004', '70000005'):
            self.assertEqual(self.raw_patch({'document_type': 'dni', 'document_number': number}).status_code, 200)
        # Another shop, and the app: the same account, the same budget.
        elsewhere = self.web.patch(WEB_PROFILE, {'document_type': 'dni', 'document_number': '70000006'}, format='json', **self.host(self.b))
        self.assertEqual(elsewhere.status_code, 400)
        other = User.objects.create_user('otro', 'otro@correo.test', PASSWORD, first_name='Otro')
        accounts.mark_email_verified(other)
        client = APIClient()
        client.force_authenticate(other)
        self.assertEqual(self.raw_patch({'document_type': 'dni', 'document_number': '70000007'}, client=client).status_code, 200)


from store import tracking_services as tracking  # noqa: E402
from store.models import Device  # noqa: E402
from store.test_tracking import TrackingBase  # noqa: E402
from store.tests import _m7_user, _v1_customer  # noqa: E402


class ClaimAfterOpeningMyAccountTest(TrackingBase):
    """
    F4 · somebody who left a device at the counter opens «Mi cuenta» before
    claiming the repair. The record that page made for them is EMPTY: it must
    not stand between them and the file the shop already keeps on them.
    """

    CLAIM = '/api/account/repairs/claim/'
    HER_DNI = '45678912'

    def setUp(self):
        super().setUp()
        cache.clear()
        self.walk_in = _v1_customer(self.company, None, first_name='Elena', last_name='SinCuenta',
                                    document_type='dni', document_number=self.HER_DNI)
        device = Device.objects.create(company=self.company, customer=self.walk_in, brand='Genérica', model='X200')
        self.repair = self.make_order(customer=self.walk_in, device=device)
        self.elena = _m7_user('elena_registrada')
        self.elena.first_name = 'Elena'
        self.elena.save(update_fields=['first_name'])

    def claim(self, user=None, document=None):
        cache.clear()
        client = APIClient()
        client.force_authenticate(user or self.elena)
        return client.post(self.CLAIM, {'token': tracking.token_for(self.repair), 'document_number': document or self.HER_DNI}, format='json')

    def own_record(self, **data):
        from store import customer_profile

        customer_profile.update(company=self.company, user=self.elena, data={'first_name': 'Elena', 'phone': '987654321', **data})
        return Customer.objects.get(company=self.company, user=self.elena)

    def test_an_empty_record_does_not_block_the_claim(self):
        empty = self.own_record()
        response = self.claim()
        self.assertEqual(response.status_code, 200, response.content)

        self.walk_in.refresh_from_db()
        self.assertEqual(self.walk_in.user, self.elena)                      # she is the client the shop knew
        self.assertFalse(Customer.objects.filter(pk=empty.pk).exists())      # and the empty one is gone
        self.assertEqual(Customer.objects.filter(company=self.company, user=self.elena).count(), 1)
        entry = AdminAuditLog.objects.get(action='customer_account_linked')
        self.assertEqual((entry.actor, entry.company, entry.target_id), (self.elena, self.company, str(self.walk_in.pk)))
        self.assertEqual(entry.metadata['replaced_empty_customer_id'], empty.pk)
        self.assertNotIn(self.HER_DNI, json.dumps(entry.metadata))
        self.assertNotIn('987654321', json.dumps(entry.metadata))

    def test_the_claim_still_has_to_be_proved(self):
        empty = self.own_record()
        self.assertEqual(self.claim(document='11111111').status_code, 403)
        self.assertTrue(Customer.objects.filter(pk=empty.pk).exists())       # nothing moved on a claim that failed
        self.walk_in.refresh_from_db()
        self.assertIsNone(self.walk_in.user)

    def test_a_record_that_holds_anything_keeps_the_old_answer(self):
        from store.models import CustomerAddress

        cases = {
            'a purchase': lambda record: _order(self.company, user=self.elena, customer=record, total='10.00'),
            'an address': lambda record: CustomerAddress.objects.create(
                company=self.company, customer=record, recipient_name='Elena', address_line='x', district='y', city='z'),
            'a device': lambda record: Device.objects.create(company=self.company, customer=record, brand='Otra', model='Y1'),
            'a note of the shop': lambda record: Customer.objects.filter(pk=record.pk).update(notes='la tienda escribió aquí'),
            'a document of its own': lambda record: Customer.objects.filter(pk=record.pk).update(document_type='dni', document_number='40001234'),
        }
        for label, fill in cases.items():
            with self.subTest(label):
                Customer.objects.filter(company=self.company, user=self.elena).delete()
                Customer.objects.filter(pk=self.walk_in.pk).update(user=None)
                record = self.own_record()
                fill(record)
                response = self.claim()
                self.assertEqual(response.status_code, 409, response.content)
                self.assertTrue(Customer.objects.filter(pk=record.pk).exists())
                self.walk_in.refresh_from_db()
                self.assertIsNone(self.walk_in.user)
                # Clean up what this case hung from the record, so the next one starts empty.
                Order.objects.filter(customer=record).delete()
                Device.objects.filter(customer=record).delete()

    def test_without_a_record_of_her_own_it_is_what_it_was(self):
        self.assertEqual(self.claim().status_code, 200)
        self.walk_in.refresh_from_db()
        self.assertEqual(self.walk_in.user, self.elena)
        self.assertNotIn('replaced_empty_customer_id', AdminAuditLog.objects.get(action='customer_account_linked').metadata)


from store.test_payment_hardening import Base as PaymentBase  # noqa: E402


class AReviewStartsWhenTheOrderIsPaidTest(PaymentBase):
    """F3 · the real roads by which an order becomes paid: the signed notification, and asking the gateway."""

    def setUp(self):
        super().setUp()
        row = OrderRiskAssessment.objects.get(order=self.order)             # made by the checkout of the fixture
        self.assertEqual(row.review_status, Review.NOT_REQUIRED)
        # What was noticed about it asks for a person.
        OrderRiskAssessment.objects.filter(pk=row.pk).update(
            level=Level.HIGH, score=70, signals=[risk.SIGNAL_GUEST_CHECKOUT, risk.SIGNAL_HIGH_AMOUNT, risk.SIGNAL_VELOCITY_IP],
        )
        self.manager, _ = _p2d_member(self.order.company, 'gestora_pago', ['company.view', VIEW, MANAGE])

    def state(self):
        return OrderRiskAssessment.objects.get(order=self.order).review_status

    def notices(self):
        return Notification.objects.filter(event__event_type=events.COMMERCE_ORDER_REVIEW_NEEDED, target_id=self.order.pk)

    def test_unpaid_it_waits_for_nobody(self):
        self.assertEqual(self.state(), Review.NOT_REQUIRED)
        self.assertFalse(self.notices().exists())
        self.assertEqual(self.notify(status='UNPAID').status_code, 200)     # a declined card
        self.assertEqual(self.state(), Review.NOT_REQUIRED)
        self.assertFalse(self.notices().exists())

    def test_the_signed_notification_that_pays_it_starts_the_review(self):
        self.assertEqual(self.notify().status_code, 200)
        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.state(), Review.PENDING)
        self.assertEqual([n.user for n in self.notices()], [self.manager])
        # Replayed, it is the same payment: one review, one notice.
        self.assertEqual(self.notify().status_code, 200)
        self.assertEqual(self.notices().count(), 1)

    def test_being_paid_by_asking_the_gateway_starts_it_too(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())
        self.ask()
        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.state(), Review.PENDING)
        self.assertEqual(self.notices().count(), 1)

    def test_a_review_that_cannot_start_never_costs_the_payment(self):
        def broken(order):
            Customer.objects.bulk_create([Customer(company=order.company)])   # the database refuses, mid-transaction

        with mock.patch('store.order_risk._review_when_paid', side_effect=broken):
            self.assertEqual(self.notify().status_code, 200)
        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.AUTHORIZED)
        self.assertEqual(self.state(), Review.NOT_REQUIRED)


from store.models import PaymentTransaction  # noqa: E402


class OnlyPaidOrdersWaitForReviewTest(_ReviewBase):
    """F3, F6 · the queue, the hold and the card are about orders somebody paid for."""

    def unpaid_pending(self):
        """A row an earlier version could have left: waiting, on an order nobody paid."""
        order = self.pending(self.a, self.north)
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PENDING_PAYMENT, paid=False, paid_at=None)
        return order

    def test_the_queue_lists_paid_orders_only(self):
        unpaid = self.unpaid_pending()
        for url in ('/api/admin/orders/?review=pending', '/api/v1/internal/revision-a/orders/?review=pending'):
            ids = [row['id'] for row in acting(self.viewer).get(url).json()['results']]
            self.assertNotIn(unpaid.pk, ids)
            self.assertIn(self.order.pk, ids)

    def test_the_hold_is_about_paid_orders_only(self):
        CompanySettings.objects.update_or_create(company=self.a, defaults={'hold_orders_pending_review': True})
        unpaid = self.unpaid_pending()
        self.assertFalse(risk.review_holds_dispatch(unpaid, self.a))
        self.assertTrue(risk.review_holds_dispatch(self.order, self.a))

    def test_with_the_hold_on_the_buyer_is_not_told_to_come_and_collect_it_either(self):
        # F6 · «listo para retiro» sends the buyer a notice to come: it waits for the review as well.
        CompanySettings.objects.update_or_create(company=self.a, defaults={'hold_orders_pending_review': True})
        for surface, url in (('panel', f'/api/admin/orders/{self.order.pk}/fulfillment-status/'),
                             ('app', f'/api/v1/internal/revision-a/orders/{self.order.pk}/fulfillment/')):
            with self.subTest(surface):
                response = acting(self.manager).patch(url, {'fulfillment_status': 'ready_for_pickup'}, format='json')
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(response.json()['code'], 'review_pending')
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, Order.FulfillmentStatus.PENDING)
        self.assertFalse(Notification.objects.filter(event__event_type=events.COMMERCE_FULFILLMENT_READY).exists())
        # The states that send the buyer nothing stay free, and a decision lets it go.
        self.assertEqual(acting(self.manager).patch(f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
                                                   {'fulfillment_status': 'preparing'}, format='json').status_code, 200)
        self.decide(self.manager, 'approve')
        self.assertEqual(acting(self.manager).patch(f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
                                                   {'fulfillment_status': 'ready_for_pickup'}, format='json').status_code, 200)

    def test_off_ready_for_pickup_is_as_free_as_it_always_was(self):
        response = acting(self.manager).patch(f'/api/admin/orders/{self.order.pk}/fulfillment-status/',
                                              {'fulfillment_status': 'ready_for_pickup'}, format='json')
        self.assertEqual(response.status_code, 200)

    def test_a_reviewer_without_a_name_is_shown_as_staff_not_by_their_login(self):
        # F12 · a login name is not a name to show.
        nameless, _ = _p2d_member(self.a, 'usuario.interno.77', ['company.view', VIEW, MANAGE])
        response = self.decide(nameless, 'approve')
        self.assertEqual(response.json()['risk']['reviewed_by'], 'Personal de la tienda')
        self.assertNotIn('usuario.interno.77', acting(self.viewer).get(f'/api/admin/orders/{self.order.pk}/').content.decode())

    def test_managing_orders_without_seeing_them_does_not_read_what_was_noticed(self):
        # Routes that ask only for `manage` answer with the order's payload: the risk block is not part of that road.
        blind, _ = _p2d_member(self.a, 'gestiona_sin_ver', ['company.view', MANAGE])
        panel = acting(blind).patch(f'/api/admin/orders/{self.order.pk}/fulfillment-status/', {'fulfillment_status': 'preparing'}, format='json')
        app = acting(blind).patch(f'/api/v1/internal/revision-a/orders/{self.order.pk}/fulfillment/', {'fulfillment_status': 'confirmed'}, format='json')
        for response in (panel, app):
            self.assertEqual(response.status_code, 200, response.content)
            self.assertIn('risk', response.json())
            self.assertIsNone(response.json()['risk'])
            self.assertIsNone(response.json()['delivery'])
            self.assertNotIn(risk.SIGNAL_VELOCITY_IP, response.content.decode())
        # With `view` as well, the same routes carry it.
        seen = acting(self.manager).patch(f'/api/admin/orders/{self.order.pk}/fulfillment-status/', {'fulfillment_status': 'pending'}, format='json')
        self.assertEqual(seen.json()['risk']['level'], 'high')


import threading  # noqa: E402

from django.db import connection as db_connection  # noqa: E402
from django.test import TransactionTestCase  # noqa: E402


class TwoAtOnceTest(TransactionTestCase):
    """
    Real commits and real row locks, with two connections: what a `TestCase`
    cannot see. Nothing a migration seeds is needed, so the database is not
    asked to be put back.
    """

    def setUp(self):
        cache.clear()
        self.company = _saas_company('Tienda A', 'dos-a-la-vez', tax_id='20999999990')
        self.user = User.objects.create_user('ana', 'ana@correo.test', PASSWORD, first_name='Ana')
        accounts.mark_email_verified(self.user)

    def together(self, jobs):
        barrier, results = threading.Barrier(len(jobs)), [None] * len(jobs)

        def run(index, job):
            try:
                barrier.wait(timeout=10)
                results[index] = ('ok', job())
            except Exception as failed:  # noqa: BLE001 — the outcome under test
                results[index] = ('refused', failed)
            finally:
                db_connection.close()

        threads = [threading.Thread(target=run, args=(i, job)) for i, job in enumerate(jobs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return results

    def test_two_reviewers_deciding_at_once_leave_one_decision(self):
        first = User.objects.create_user('una', 'una@tienda.test', PASSWORD)
        second = User.objects.create_user('otra', 'otra@tienda.test', PASSWORD)
        for _ in range(5):
            order = _order(self.company, total='900.00', paid=True)
            OrderRiskAssessment.objects.create(
                company=self.company, order=order, level=Level.HIGH, score=70, signals=[], review_status=Review.PENDING,
            )
            results = self.together([
                lambda: risk.review(order=order, company=self.company, actor=first, decision='approve'),
                lambda: risk.review(order=order, company=self.company, actor=second, decision='reject'),
            ])
            outcomes = sorted(kind for kind, _value in results)
            self.assertEqual(outcomes, ['ok', 'refused'], results)
            refused = next(value for kind, value in results if kind == 'refused')
            self.assertIsInstance(refused, risk.ReviewNotPending)
            row = OrderRiskAssessment.objects.get(order=order)
            winner = next(value for kind, value in results if kind == 'ok')
            self.assertEqual((row.review_status, row.reviewed_by_id), (winner.review_status, winner.reviewed_by_id))
            self.assertEqual(AdminAuditLog.objects.filter(action='order_risk_reviewed', target_id=str(order.pk)).count(), 1)

    def test_two_saves_of_one_profile_at_once_are_both_kept(self):
        from store import customer_profile

        customer_profile.update(company=self.company, user=self.user, data={'phone': '987654321'})
        for n in range(5):
            results = self.together([
                lambda: customer_profile.update(company=self.company, user=self.user, data={'first_name': f'Ana {n}'}),
                lambda: customer_profile.update(company=self.company, user=self.user, data={'last_name': f'Torres {n}'}),
            ])
            self.assertEqual([kind for kind, _value in results], ['ok', 'ok'], results)
            record = Customer.objects.get(company=self.company, user=self.user)
            # Without the lock one save would be made on a stale copy and undo the other.
            self.assertEqual((record.first_name, record.last_name), (f'Ana {n}', f'Torres {n}'))

    def test_two_first_saves_at_once_make_one_record(self):
        from store import customer_profile

        other = User.objects.create_user('luis', 'luis@correo.test', PASSWORD, first_name='Luis')
        results = self.together([
            lambda: customer_profile.update(company=self.company, user=other, data={'phone': '987654321'}),
            lambda: customer_profile.update(company=self.company, user=other, data={'last_name': 'Rojas'}),
        ])
        self.assertEqual([kind for kind, _value in results], ['ok', 'ok'], results)
        record = Customer.objects.get(company=self.company, user=other)
        self.assertEqual((record.phone, record.last_name), ('987654321', 'Rojas'))


# =============================================================================
# 8 · after the second independent review
# =============================================================================

class ADocumentTypedAtCheckoutIsNotARecordsDocumentTest(_CheckoutBase):
    """
    P1-1 · a signed-in checkout makes the account's record. It must not be a
    second door to what the profile guards: the record takes the typed document
    only for an account with a proved mailbox, and only if nobody holds it.
    """

    STRANGERS_DNI = '70000044'

    def buy(self, user, **overrides):
        self.n = getattr(self, 'n', 0) + 1
        overrides.setdefault('document_number', self.STRANGERS_DNI)
        response = self.web_checkout(web_session(user), session=f'carrito-doc-{self.n}', **overrides)
        self.assertEqual(response.status_code, 200, response.content)
        return Order.objects.get(pk=response.json()['order_id'])

    def account(self, username='ocupante', *, verified):
        user = User.objects.create_user(username, f'{username}@correo.test', PASSWORD, first_name='Otra')
        if verified:
            accounts.mark_email_verified(user)
        return user

    def test_an_unproved_account_gets_a_record_without_the_document(self):
        user = self.account(verified=False)
        order = self.buy(user)                                              # never paid: it cost nothing

        record = Customer.objects.get(company=self.company, user=user)
        self.assertEqual((record.document_type, record.document_number), ('', ''))
        self.assertEqual(order.customer, record)                            # the sale is still theirs
        self.assertEqual((order.document_type, order.document_number), ('dni', self.STRANGERS_DNI))   # and keeps what was typed
        self.assertFalse(Customer.objects.filter(company=self.company, document_number=self.STRANGERS_DNI).exists())

    def test_the_shop_can_then_file_the_real_person_and_the_account_reads_nothing_of_theirs(self):
        user = self.account(verified=False)
        self.buy(user)
        # Staff file the real holder of that DNI: nothing stands in the way.
        customer_services.assert_document_available(self.company, 'dni', self.STRANGERS_DNI)
        real = Customer.objects.create(
            company=self.company, first_name='Titular', last_name='Real', document_type='dni', document_number=self.STRANGERS_DNI,
        )
        theirs = self.web_checkout(session='carrito-titular', document_number=self.STRANGERS_DNI, customer_email='titular@correo.test')
        guest_order = Order.objects.get(pk=theirs.json()['order_id'])
        self.assertEqual(guest_order.customer, real)
        listed = web_session(user).get('/api/orders/', HTTP_HOST='riesgo-caja.example.com').json()
        self.assertNotIn(guest_order.pk, [row['id'] for row in listed])

    def test_it_is_not_a_way_around_the_profiles_budget_either(self):
        user = self.account(verified=False)
        for number in ('70000051', '70000052', '70000053', '70000054', '70000055'):
            self.buy(user, document_number=number)
        record = Customer.objects.get(company=self.company, user=user)
        self.assertEqual(record.document_number, '')
        self.assertEqual(Customer.objects.filter(company=self.company).exclude(document_number='').count(), 0)

    def test_an_account_with_a_proved_mailbox_keeps_what_it_always_had(self):
        user = self.account('verificada', verified=True)
        order = self.buy(user)
        record = Customer.objects.get(company=self.company, user=user)
        self.assertEqual((record.document_type, record.document_number, order.customer), ('dni', self.STRANGERS_DNI, record))

    def test_a_document_somebody_holds_stays_theirs(self):
        holder = Customer.objects.create(
            company=self.company, first_name='Titular', document_type='dni', document_number=self.STRANGERS_DNI,
        )
        user = self.account('verificada', verified=True)
        order = self.buy(user)
        record = Customer.objects.get(company=self.company, user=user)
        self.assertEqual((record.document_number, order.customer), ('', record))      # their own record, without it
        holder.refresh_from_db()
        self.assertEqual((holder.user, holder.document_number), (None, self.STRANGERS_DNI))
        self.assertEqual(Customer.objects.filter(company=self.company, document_number=self.STRANGERS_DNI).count(), 1)

    def test_a_business_record_made_without_its_ruc_is_still_a_business(self):
        user = self.account(verified=False)
        self.buy(user, document_type='ruc', document_number='20999999990', receipt_type='factura', customer_name='Comercial de Prueba S.A.C.')
        record = Customer.objects.get(company=self.company, user=user)
        self.assertEqual((record.customer_type, record.business_name, record.document_number),
                         ('business', 'Comercial de Prueba S.A.C.', ''))

    def test_a_guest_sale_is_what_it_was(self):
        response = self.web_checkout(session='carrito-invitado', document_number=self.STRANGERS_DNI)
        order = Order.objects.get(pk=response.json()['order_id'])
        self.assertEqual((order.customer.user, order.customer.document_number), (None, self.STRANGERS_DNI))


class ASwitchedOffAccountStaysOffTest(_OwnAddressBase):
    """
    P1-2 · asking for the F7 code leaves nothing that reads as «registered and
    never confirmed». An account somebody switches off afterwards stays off by
    every road that finishes a half-made account.
    """

    RESET_REQUEST = '/api/auth/password-reset/request/'
    RESET_CONFIRM = '/api/auth/password-reset/confirm/'

    def switch_off(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.user.refresh_from_db()

    def is_on(self) -> bool:
        return User.objects.get(pk=self.user.pk).is_active

    def ask_reset(self):
        cache.clear()
        django_mail.outbox = []
        APIClient().post(self.RESET_REQUEST, {'email': 'ana@correo.test'}, format='json')
        return list(django_mail.outbox)

    def test_control_an_account_switched_off_gets_no_recovery_mail(self):
        self.switch_off()
        self.assertFalse(accounts.is_unverified(self.user))
        self.assertEqual(self.ask_reset(), [])

    def test_having_asked_for_a_code_does_not_make_it_a_half_made_account(self):
        self.assertEqual(self.send().status_code, 200)
        self.switch_off()
        self.assertFalse(accounts.is_unverified(self.user))
        self.assertEqual(self.ask_reset(), [])
        self.assertFalse(self.is_on())

    def test_a_recovery_link_it_already_had_does_not_switch_it_back_on(self):
        self.send()
        raw, _row = AccountToken.make(self.user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        self.switch_off()
        cache.clear()
        APIClient().post(self.RESET_CONFIRM, {'token': raw, 'new_password': 'Otra-clave-larga-7'}, format='json')
        self.assertFalse(self.is_on())

    def test_the_token_behind_the_code_is_not_a_verification_link(self):
        made = []
        real = AccountToken.make.__func__

        def remember(cls, *args, **kwargs):
            made.append(real(cls, *args, **kwargs))
            return made[-1]

        with mock.patch.object(AccountToken, 'make', classmethod(remember)):
            self.send()
        [(raw, _row)] = made
        self.switch_off()
        cache.clear()
        response = APIClient().post('/api/auth/verify-email/', {'token': raw}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.is_on())
        self.assertIsNone(verified_at(self.user))

    def test_the_public_resend_and_the_code_without_a_session_do_nothing_for_it(self):
        self.send()
        code = self.code()
        self.switch_off()
        AccountToken.objects.filter(user=self.user).update(created_at=timezone.now() - timedelta(days=3))
        cache.clear()
        django_mail.outbox = []
        APIClient().post('/api/auth/resend-verification/', {'email': 'ana@correo.test'}, format='json')
        self.assertEqual(django_mail.outbox, [])
        AccountToken.objects.filter(user=self.user).update(created_at=timezone.now())
        cache.clear()
        response = APIClient().post('/api/auth/verify-email/code/', {'email': 'ana@correo.test', 'code': code}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.is_on())
        self.assertIsNone(verified_at(self.user))

    def test_its_own_code_is_worth_nothing_once_it_is_off(self):
        from store import verification_codes

        self.send()
        code = self.code()
        self.switch_off()
        self.assertFalse(verification_codes.confirm_own(self.user, code))
        self.assertIsNone(verified_at(self.user))

    def test_an_account_really_registered_and_never_confirmed_is_still_finished_by_its_link(self):
        self.switch_off()
        raw, _row = AccountToken.make(self.user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        self.assertTrue(accounts.is_unverified(self.user))
        cache.clear()
        self.assertEqual(APIClient().post('/api/auth/verify-email/', {'token': raw}, format='json').status_code, 200)
        self.assertTrue(self.is_on())


class TheOwnAddressMailTest(SimpleTestCase):
    """What the F7 mail says when it wears a shop's template: a code, and no way in."""

    def test_it_is_a_valid_message_with_the_code_and_no_button_or_link(self):
        from store.mail import builders, contract

        data = contract.validate(contract.clean(builders.own_email_code(brand='Tienda Uno', name='Ana', code='042913')))
        self.assertEqual(data['codigo']['valor'], '042913')
        self.assertNotIn('boton', data)
        self.assertNotIn('secundario', data)
        self.assertNotIn('http', json.dumps(data))
