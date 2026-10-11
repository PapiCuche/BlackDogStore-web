"""
REGISTER-RACE-01 · one e-mail address, one account — in the database, not only in the form.

Registration asked «is this address taken?» and then created the account. Two
requests at the same instant both heard «no», and both created one. From there
the address could not be verified, could not ask for the verification again and
could not recover its password — every one of those refuses to guess between
two accounts. One burst locked an address out for good.

What these tests hold:

  * the DATABASE refuses a second account under the same address, whatever the
    capitals. Accounts without an address are not affected;
  * a registration that loses the race is answered exactly like one that
    arrived late: «ya está registrado». Never a 500, and nothing half written;
  * a migration that finds duplicates stops and says how many — it merges
    nothing and names nobody;
  * a command lists them for a person to decide.
"""
import io
import threading
from unittest import skipUnless
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail as outbox_module
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from store import accounts
from store.auth_serializers import RegisterSerializer
from store.models import AccountToken

User = get_user_model()

PASSWORD = 'Una-clave-larga-91'
TAKEN = {'email': ['Este correo ya está registrado.']}
INDEX = 'store_user_email_ci_uniq'


def before_the_rule():
    """
    The database as it was before the unique rule, for the rest of this test.

    Other suites keep the defences against two accounts under one address —
    an installation can still carry them from its past, and none of that code
    may answer 500. They build such a pair with this. DDL is transactional on
    the databases the suite runs on: the rule is back when the test ends.
    """
    # From a transactional test the drop would be COMMITTED, for every test after it.
    assert connection.in_atomic_block, 'before_the_rule() is for a TestCase, where it is undone'
    with connection.cursor() as cursor:
        cursor.execute(f'DROP INDEX {INDEX}')


def payload(username, email):
    return {'username': username, 'email': email, 'first_name': 'Ana', 'last_name': 'Torres',
            'password': PASSWORD, 'password_confirm': PASSWORD}


@override_settings(REQUIRE_EMAIL_VERIFICATION=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TheDatabaseRuleTest(TestCase):
    def test_a_second_account_under_the_same_address_is_refused_whatever_the_capitals(self):
        User.objects.create_user('ana', 'ana@correo.test', PASSWORD)
        for same in ('ana@correo.test', 'ANA@correo.test', 'Ana@Correo.Test'):
            with self.subTest(same), self.assertRaises(IntegrityError), transaction.atomic():
                User.objects.create_user(f'otra_{len(same)}_{same[:3]}', same, PASSWORD)
        self.assertEqual(User.objects.filter(email__iexact='ana@correo.test').count(), 1)

    def test_accounts_without_an_address_are_as_many_as_they_were(self):
        for position in range(3):
            User.objects.create_user(f'sin_correo_{position}', '', PASSWORD)
        self.assertEqual(User.objects.filter(email='').count(), 3)

    def test_the_rule_compares_as_every_lookup_in_the_code_does(self):
        """
        `email__iexact` is UPPER(email) = UPPER(%s) in PostgreSQL. An index on
        LOWER(email) is NOT the same rule: on a UTF-8 database «ınfo@» and
        «info@» are one address for every lookup (UPPER of both is «INFO@») and
        two for LOWER — two accounts, and the address locked out again. The
        index has to be on the expression the lookups use.
        """
        if connection.vendor != 'postgresql':
            self.skipTest('the expression is checked where production runs')
        with connection.cursor() as cursor:
            cursor.execute('SELECT indexdef FROM pg_indexes WHERE indexname = %s', [INDEX])
            definition = cursor.fetchone()[0].lower()
        self.assertIn('upper(', definition)
        self.assertNotIn('lower(', definition)
        self.assertIn('unique', definition)
        # …and the lookups still compile to that same expression.
        self.assertIn('UPPER(', str(User.objects.filter(email__iexact='x@y.z').query).upper())

    def test_another_address_is_another_account(self):
        User.objects.create_user('ana', 'ana@correo.test', PASSWORD)
        User.objects.create_user('ana2', 'ana+2@correo.test', PASSWORD)
        self.assertEqual(User.objects.count(), 2)


@override_settings(REQUIRE_EMAIL_VERIFICATION=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class RegisteringTest(TestCase):
    def setUp(self):
        cache.clear()
        outbox_module.outbox = []
        self.client = APIClient()

    def register(self, username, email):
        cache.clear()
        return self.client.post('/api/auth/register/', payload(username, email), format='json')

    def test_the_one_that_arrives_late_is_told_it_exists(self):
        self.assertEqual(self.register('ana', 'ana@correo.test').status_code, 201)
        late = self.register('ana2', 'ANA@correo.test')
        self.assertEqual(late.status_code, 400)
        self.assertEqual(late.json(), TAKEN)

    def test_the_one_that_loses_the_race_is_told_the_same_and_leaves_nothing_behind(self):
        """
        The race, held still: the «is it taken?» of the form says no — as it
        does for the request that asked a moment before the other one saved.
        """
        self.assertEqual(self.register('ana', 'ana@correo.test').status_code, 201)
        tokens, mails = AccountToken.objects.count(), len(outbox_module.outbox)

        with mock.patch.object(RegisterSerializer, 'validate_email', lambda self, value: value.lower().strip()):
            lost = self.register('ana2', 'Ana@correo.test')

        self.assertEqual(lost.status_code, 400, lost.content)
        self.assertEqual(lost.json(), TAKEN)
        self.assertFalse(User.objects.filter(username='ana2').exists())
        self.assertEqual(AccountToken.objects.count(), tokens)             # no token for an account that is not there
        self.assertEqual(len(outbox_module.outbox), mails)                 # and no mail

    def test_losing_the_race_for_a_username_is_said_as_a_username_taken(self):
        self.assertEqual(self.register('ana', 'ana@correo.test').status_code, 201)
        taken = self.register('ana', 'otra@correo.test').json()            # what the form says when it arrives late

        with mock.patch('rest_framework.validators.UniqueValidator.__call__', lambda *a, **k: None):
            lost = self.register('ana', 'tercera@correo.test')

        self.assertEqual(lost.status_code, 400, lost.content)
        self.assertEqual(lost.json(), taken)
        self.assertFalse(User.objects.filter(email='tercera@correo.test').exists())

    def test_an_address_is_plain_letters(self):
        """
        «ınfo@tienda.com» and «ana@gmaıl.com» pass for an address and read like
        another one. Whoever registers the look-alike first leaves the real
        owner unable to register, and the recovery mail of the real address
        goes to the look-alike. An account's address is ASCII.
        """
        for position, address in enumerate(('ınfo@correo.test', 'ana@gmaıl.test', 'ſara@correo.test', 'peña@correo.test', 'ＡＮＡ@correo.test')):
            with self.subTest(address):
                response = self.register(f'rara{position}', address)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('email', response.json())
                self.assertNotEqual(response.json(), TAKEN)                 # it is not «taken»: it is not accepted
        self.assertFalse(User.objects.filter(username__startswith='rara').exists())
        self.assertEqual(self.register('normal', 'Ana.Torres+tienda@correo.test').status_code, 201)

    def test_something_else_the_database_refuses_is_not_disguised_as_taken(self):
        """
        Only the two rules about WHO it is are answered as «ya existe». Any other
        refusal of the database is a fault of ours: it must be loud, not a
        polite 400 that would hide a registration broken for everybody.
        """
        from django.db import IntegrityError as DatabaseRefusal

        with mock.patch('store.verification_codes.issue', side_effect=DatabaseRefusal('null value in column "x" violates not-null constraint')):
            self.client.raise_request_exception = False
            response = self.register('ana', 'ana@correo.test')
        self.assertEqual(response.status_code, 500)
        self.assertFalse(User.objects.filter(username='ana').exists())      # and still nothing half written

    def test_the_answer_uses_what_the_form_understood_not_what_was_typed(self):
        self.assertEqual(self.register('ana', 'ana@correo.test').status_code, 201)
        with mock.patch.object(RegisterSerializer, 'validate_email', lambda self, value: value.lower().strip()):
            lost = self.register('ana2', '  ANA@Correo.Test  ')
        self.assertEqual(lost.status_code, 400)
        self.assertEqual(lost.json(), TAKEN)

    def test_after_losing_the_address_still_works_for_its_one_account(self):
        self.register('ana', 'ana@correo.test')
        with mock.patch.object(RegisterSerializer, 'validate_email', lambda self, value: value.lower().strip()):
            self.register('ana2', 'ana@correo.test')

        self.assertEqual(len(accounts.with_email('ana@correo.test')), 1)
        cache.clear()
        outbox_module.outbox = []
        self.client.post('/api/auth/password-reset/request/', {'email': 'ana@correo.test'}, format='json')
        self.assertEqual(len(outbox_module.outbox), 1)                     # recovery is not silent: there is one account to send to


@override_settings(REQUIRE_EMAIL_VERIFICATION=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@skipUnless(connection.vendor == 'postgresql', 'several writers at once need a database that takes them')
class AtTheSameInstantTest(TransactionTestCase):
    """The real thing: several requests at once, real commits."""

    def together(self, jobs):
        barrier = threading.Barrier(len(jobs))
        results = [None] * len(jobs)

        def run(position, job):
            try:
                barrier.wait(timeout=10)
                results[position] = job()
            except Exception as exc:  # noqa: BLE001
                results[position] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(position, job)) for position, job in enumerate(jobs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return results

    def test_five_registrations_of_one_address_make_one_account(self):
        cache.clear()

        def register(position):
            response = APIClient().post('/api/auth/register/', payload(f'ana{position}', 'Ana@correo.test'), format='json')
            return response.status_code, response.json()

        results = self.together([lambda position=position: register(position) for position in range(5)])

        for result in results:
            self.assertNotIsInstance(result, Exception, repr(result))
        statuses = sorted(status for status, _body in results)
        self.assertEqual(statuses, [201, 400, 400, 400, 400], results)
        for status, body in results:
            if status == 400:
                self.assertEqual(body, TAKEN)
        self.assertEqual(User.objects.filter(email__iexact='ana@correo.test').count(), 1)
        # The one account can go on: it has its verification, and only it has one.
        self.assertEqual(AccountToken.objects.count(), 1)


class WhatIsAlreadyThereTest(TestCase):
    """Duplicates from before the rule: found, counted, listed — and left for a person."""

    def duplicates(self):
        before_the_rule()
        first = User.objects.create_user('ana', 'ana@correo.test', PASSWORD)
        second = User.objects.create_user('ana_otra', 'ANA@correo.test', PASSWORD, is_active=False)
        User.objects.create_user('luis', 'luis@correo.test', PASSWORD)
        User.objects.create_user('sin1', '', PASSWORD)
        User.objects.create_user('sin2', '', PASSWORD)
        return first, second

    def test_they_are_grouped_by_address_without_capitals_and_empty_ones_are_not_a_group(self):
        first, second = self.duplicates()
        groups = accounts.duplicate_email_groups()
        self.assertEqual([[user.pk for user in group] for group in groups], [[first.pk, second.pk]])

    def test_without_duplicates_there_is_nothing_to_list(self):
        User.objects.create_user('ana', 'ana@correo.test', PASSWORD)
        self.assertEqual(accounts.duplicate_email_groups(), [])

    def test_the_migration_stops_and_says_how_many_and_names_nobody(self):
        import importlib

        from django.apps import apps

        self.duplicates()
        migration = importlib.import_module('store.migrations.0115_user_email_unique_ci')
        with self.assertRaises(RuntimeError) as stopped, connection.schema_editor(atomic=False) as editor:
            migration.create_rule(apps, editor)
        message = str(stopped.exception)
        self.assertRegex(message, r'\b1 correo\(s\)')
        self.assertIn('list_duplicate_emails', message)
        for personal in ('ana@correo.test', 'ANA@correo.test', 'ana_otra'):
            self.assertNotIn(personal, message)
        self.assertEqual(User.objects.count(), 5)                          # it merged and deleted nothing

    def test_the_migration_goes_through_when_there_are_none(self):
        import importlib

        from django.apps import apps

        before_the_rule()
        User.objects.create_user('ana', 'ana@correo.test', PASSWORD)
        with connection.schema_editor(atomic=False) as editor:
            importlib.import_module('store.migrations.0115_user_email_unique_ci').create_rule(apps, editor)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user('ana2', 'ANA@correo.test', PASSWORD)      # the rule is there again

    def test_a_duplicate_that_appears_while_migrating_stops_it_without_naming_anybody(self):
        """Between counting and creating the rule the old web is still serving."""
        import importlib

        from django.apps import apps

        migration = importlib.import_module('store.migrations.0115_user_email_unique_ci')
        self.duplicates()
        with mock.patch.object(migration, '_repeated', return_value=0), \
                self.assertRaises(RuntimeError) as stopped, transaction.atomic(), \
                connection.schema_editor(atomic=False) as editor:
            migration.create_rule(apps, editor)
        message = str(stopped.exception) + repr(stopped.exception.__cause__) + repr(stopped.exception.__context__ if not stopped.exception.__suppress_context__ else '')
        for personal in ('ana@correo.test', 'ANA@CORREO.TEST', 'ANA@correo.test'):
            self.assertNotIn(personal, message)
        self.assertIn('list_duplicate_emails', str(stopped.exception))

    def test_the_command_lists_them_for_a_person_to_decide(self):
        first, second = self.duplicates()
        out = io.StringIO()
        call_command('list_duplicate_emails', stdout=out)
        text = out.getvalue()

        self.assertIn('1 correo', text)
        self.assertIn(f'id={first.pk}', text)
        self.assertIn(f'id={second.pk}', text)
        self.assertIn('a***@correo.test', text)                            # recognisable to its owner, not copied whole
        for whole in ('ana@correo.test', 'ANA@correo.test'):
            self.assertNotIn(whole, text)
        self.assertNotIn('luis', text)
        self.assertEqual(User.objects.count(), 5)                          # read-only

    def test_a_short_name_is_not_printed_whole(self):
        before_the_rule()
        User.objects.create_user('x1', 'al@correo.test', PASSWORD)
        User.objects.create_user('x2', 'AL@correo.test', PASSWORD)
        out = io.StringIO()
        call_command('list_duplicate_emails', stdout=out)
        self.assertIn('a***@correo.test', out.getvalue())
        self.assertNotIn('al***', out.getvalue())
        self.assertNotIn('al@', out.getvalue().lower())

    def test_the_command_says_so_when_there_are_none(self):
        User.objects.create_user('ana', 'ana@correo.test', PASSWORD)
        out = io.StringIO()
        call_command('list_duplicate_emails', stdout=out)
        self.assertIn('0 correos', out.getvalue())
