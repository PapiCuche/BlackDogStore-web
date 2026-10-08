"""
MAIL-TEMPLATE-01, phase 3A · the verification e-mail also carries a 6-digit code.

The link stays the main way: 24 hours, a token nobody can guess. The code is the
convenience for somebody reading the mail on another device, and it is a much
smaller secret — a million possibilities — so everything here is about keeping
it from being guessed:

  * 15 minutes, 5 attempts, and a new code kills the one before;
  * 60 seconds between two codes and 5 codes a day for one account, whoever asks;
  * one answer for every way it can fail, so it never says whether an account
    exists, whether the code was close, or why it was refused;
  * only an HMAC of it is stored, and it is written to no log.

The code proves the same thing the link proves, and nothing more: that whoever
types it reads that mailbox.
"""
import json
import logging
import re
import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail as outbox_module
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store import verification_codes
from store.models import AccountToken, Company

User = get_user_model()

SITE = 'https://tienda.test'
PASSWORD = 'Una-clave-larga-91'
GENERIC = {'detail': 'El código no es válido o ya venció. Pide uno nuevo.'}
VERIFY = '/api/auth/verify-email/code/'
RESEND = '/api/auth/resend-verification/'


@override_settings(
    REQUIRE_EMAIL_VERIFICATION=True, FRONTEND_URL=SITE, DEFAULT_STOREFRONT_COMPANY_SLUG='black-dog-store',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', DEFAULT_FROM_EMAIL='no-reply@tienda.test',
)
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        outbox_module.outbox = []
        self.client = APIClient()

    def register(self, username='ana', email=None):
        cache.clear()
        response = self.client.post('/api/auth/register/', {
            'username': username, 'email': email or f'{username}@correo.test', 'first_name': 'Ana', 'last_name': 'Torres',
            'password': PASSWORD, 'password_confirm': PASSWORD,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()['requires_verification'])
        return User.objects.get(username=username)

    def code_in(self, message) -> str:
        found = re.findall(r'Tu código: (\d{6})\b', message.body)
        self.assertEqual(len(found), 1, message.body)
        return found[0]

    def last_code(self) -> str:
        return self.code_in(outbox_module.outbox[-1])

    def verify(self, email, code):
        cache.clear()                                   # the per-address limit is another test's business
        return self.client.post(VERIFY, json.dumps({'email': email, 'code': code}), content_type='application/json')

    def resend(self, email):
        cache.clear()
        return self.client.post(RESEND, {'email': email}, format='json')

    def age(self, user, **delta):
        """Make everything this account was sent that much older."""
        for token in AccountToken.objects.filter(user=user):
            fields = {'created_at': token.created_at - timedelta(**delta)}
            if token.code_expires_at:
                fields['code_expires_at'] = token.code_expires_at - timedelta(**delta)
            AccountToken.objects.filter(pk=token.pk).update(**fields)

    def assert_refused(self, response):
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), GENERIC)


class TheEmailTest(_Base):
    def test_the_dressed_email_carries_the_link_and_the_code(self):
        user = self.register()
        [message] = outbox_module.outbox
        html = message.alternatives[0][0]
        code = self.code_in(message)
        self.assertRegex(message.body, rf'{SITE}/auth/verify-email\?token=[A-Za-z0-9_-]{{40,}}')
        self.assertIn('class="btn"', html)                               # the link is the main way
        self.assertIn(f'>{code}</', html)
        self.assertIn('letter-spacing:8px', html)                        # in the template's own code block
        self.assertIn('15 minutos', message.body)
        self.assertNotIn(code, message.subject)                          # a subject shows on a locked screen
        self.assertEqual(user.account_tokens.count(), 1)

    def test_the_plain_email_carries_it_too(self):
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=''):
            self.register()
        [message] = outbox_module.outbox
        self.assertEqual(message.alternatives, [])
        self.assertRegex(message.body, rf'{SITE}/auth/verify-email\?token=')
        self.assertEqual(len(self.code_in(message)), 6)

    def test_only_a_keyed_hash_of_the_code_is_kept(self):
        user = self.register()
        code = self.last_code()
        token = user.account_tokens.get()
        self.assertRegex(token.code_hash, r'^[0-9a-f]{64}$')
        for value in AccountToken.objects.filter(pk=token.pk).values().get().values():
            self.assertNotEqual(str(value), code)
        # Not a bare hash either: a million candidates would be reversed in a second.
        import hashlib
        self.assertNotEqual(token.code_hash, hashlib.sha256(code.encode()).hexdigest())
        self.assertNotEqual(token.code_hash, hashlib.sha256(f'{user.pk}:{code}'.encode()).hexdigest())

    def test_a_code_is_six_digits_and_keeps_its_zeros(self):
        with mock.patch('store.verification_codes.secrets.randbelow', return_value=42):
            self.register()
        self.assertEqual(self.last_code(), '000042')


class VerifyingTest(_Base):
    def test_the_right_code_finishes_the_account(self):
        user = self.register()
        response = self.verify('ana@correo.test', self.last_code())
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.account_tokens.get().used_at)
        cache.clear()
        login = self.client.post('/api/auth/login/', {'username': 'ana', 'password': PASSWORD}, format='json')
        self.assertEqual(login.status_code, 200, login.content)

    def test_it_is_read_as_people_type_and_paste_it(self):
        for position, typed in enumerate(('{a} {b}', '{a}-{b}', ' {a}{b} ', '{a} - {b}', '{a} {b}')):
            with self.subTest(typed):
                user = self.register(f'ana{position}')
                code = self.last_code()
                response = self.verify(f'  ANA{position}@Correo.Test ', typed.format(a=code[:3], b=code[3:]))
                self.assertEqual(response.status_code, 200, response.content)
                user.refresh_from_db()
                self.assertTrue(user.is_active)

    def test_every_refusal_is_the_same_answer(self):
        """Wrong code, no such account, an account already verified, something that is not a code."""
        self.register()
        code = self.last_code()
        wrong = f'{(int(code) + 1) % 10**6:06d}'
        active = User.objects.create_user('activa', 'activa@correo.test', PASSWORD)
        self.assertTrue(active.is_active)
        answers = [
            self.verify('ana@correo.test', wrong),
            self.verify('nadie@correo.test', code),
            self.verify('activa@correo.test', code),
            self.verify('ana@correo.test', '12345'),
            self.verify('ana@correo.test', '1234567'),
            self.verify('ana@correo.test', 'abcdef'),
            self.verify('ana@correo.test', ''),
            self.verify('', code),
            self.verify('no-es-un-correo', code),
        ]
        for response in answers:
            self.assert_refused(response)
        self.assertEqual(len({response.content for response in answers}), 1)
        self.assertFalse(User.objects.get(username='ana').is_active)

    def test_five_wrong_tries_and_the_code_is_dead_the_link_is_not(self):
        user = self.register()
        code = self.last_code()
        link_token = re.search(r'token=([A-Za-z0-9_-]+)', outbox_module.outbox[-1].body).group(1)
        wrong = f'{(int(code) + 1) % 10**6:06d}'
        for _ in range(5):
            self.assert_refused(self.verify('ana@correo.test', wrong))
        self.assert_refused(self.verify('ana@correo.test', code))          # the right one, too late
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(user.account_tokens.get().code_attempts, 5)

        response = self.client.post('/api/auth/verify-email/', {'token': link_token}, format='json')
        self.assertEqual(response.status_code, 200, response.content)

    def test_four_wrong_tries_leave_one(self):
        user = self.register()
        code = self.last_code()
        wrong = f'{(int(code) + 1) % 10**6:06d}'
        for _ in range(4):
            self.assert_refused(self.verify('ana@correo.test', wrong))
        self.assertEqual(self.verify('ana@correo.test', code).status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_something_that_is_not_a_code_costs_no_attempt(self):
        """A typo of the form is not a guess: only six digits are compared, and counted."""
        user = self.register()
        for typed in ('12345', 'abcdef', '', '1234567'):
            self.assert_refused(self.verify('ana@correo.test', typed))
        self.assertEqual(user.account_tokens.get().code_attempts, 0)

    def test_after_fifteen_minutes_the_code_is_over_and_the_link_is_not(self):
        user = self.register()
        code = self.last_code()
        link_token = re.search(r'token=([A-Za-z0-9_-]+)', outbox_module.outbox[-1].body).group(1)
        self.age(user, minutes=16)
        self.assert_refused(self.verify('ana@correo.test', code))
        self.assertEqual(self.client.post('/api/auth/verify-email/', {'token': link_token}, format='json').status_code, 200)

    def test_fourteen_minutes_is_still_in_time(self):
        user = self.register()
        self.age(user, minutes=14)
        self.assertEqual(self.verify('ana@correo.test', self.last_code()).status_code, 200)

    def test_it_works_once(self):
        self.register()
        code = self.last_code()
        self.assertEqual(self.verify('ana@correo.test', code).status_code, 200)
        self.assert_refused(self.verify('ana@correo.test', code))

    def test_the_link_spends_the_code(self):
        self.register()
        code = self.last_code()
        link_token = re.search(r'token=([A-Za-z0-9_-]+)', outbox_module.outbox[-1].body).group(1)
        self.assertEqual(self.client.post('/api/auth/verify-email/', {'token': link_token}, format='json').status_code, 200)
        self.assert_refused(self.verify('ana@correo.test', code))

    def test_a_code_is_its_accounts_and_nobody_elses(self):
        with mock.patch('store.verification_codes.secrets.randbelow', return_value=123456):
            self.register('ana')
        with mock.patch('store.verification_codes.secrets.randbelow', return_value=654321):
            luis = self.register('luis')
        self.assert_refused(self.verify('luis@correo.test', '123456'))
        luis.refresh_from_db()
        self.assertFalse(luis.is_active)
        # The same digits for two accounts are two different secrets.
        with mock.patch('store.verification_codes.secrets.randbelow', return_value=123456):
            self.register('eva')
        hashes = AccountToken.objects.filter(user__username__in=('ana', 'eva')).values_list('code_hash', flat=True)
        self.assertEqual(len(set(hashes)), 2)

    def test_two_accounts_under_one_address_are_not_guessed_between(self):
        self.register('ana')
        code = self.last_code()
        User.objects.create_user('ana.vieja', 'ANA@correo.test', PASSWORD, is_active=False)
        self.assert_refused(self.verify('ana@correo.test', code))

    def test_a_password_reset_token_is_not_a_place_to_try_codes(self):
        """Even if a row of another purpose somehow carried the code of an account still waiting."""
        user = self.register()
        code = self.last_code()
        verification = user.account_tokens.get()
        _raw, reset = AccountToken.make(user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        self.assertEqual((reset.code_hash, reset.code_expires_at), ('', None))
        AccountToken.objects.filter(pk=reset.pk).update(
            code_hash=verification.code_hash, code_expires_at=verification.code_expires_at)
        AccountToken.objects.filter(pk=verification.pk).update(code_hash='', code_expires_at=None)

        self.assert_refused(self.verify('ana@correo.test', code))
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_a_code_does_not_outlive_its_link(self):
        user = self.register()
        code = self.last_code()
        AccountToken.objects.filter(user=user).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assert_refused(self.verify('ana@correo.test', code))

    def test_digits_of_another_script_are_not_a_code_and_cost_nothing(self):
        user = self.register()
        code = self.last_code()
        for typed in (''.join(chr(0xFF10 + int(d)) for d in code),          # fullwidth
                      ''.join(chr(0x0660 + int(d)) for d in code)):          # Arabic-Indic
            self.assert_refused(self.verify('ana@correo.test', typed))
        self.assertEqual(user.account_tokens.get().code_attempts, 0)
        self.assertEqual(self.verify('ana@correo.test', code).status_code, 200)

    def test_only_text_is_a_code(self):
        """A JSON number has already lost its zeros, and «-654321» is not what the mail said."""
        with mock.patch('store.verification_codes.secrets.randbelow', return_value=654321):
            user = self.register()
        for typed in (654321, -654321, 654321.0, True, ['654321'], {'code': '654321'}, None):
            self.assert_refused(self.verify('ana@correo.test', typed))
        self.assertEqual(user.account_tokens.get().code_attempts, 0)
        self.assertEqual(self.verify('ana@correo.test', '654321').status_code, 200)

    def test_an_address_no_database_can_hold_is_one_more_refusal(self):
        self.register()
        for email in ('ana@correo.test\x00', '\x00', 'a' * 5000 + '@correo.test', ['ana@correo.test'], 7, None):
            self.assert_refused(self.verify(email, '123456'))

    def test_only_an_account_nobody_ever_confirmed_is_finished_by_a_code(self):
        """
        «Inactive» is not the question. An account that was verified and later
        switched off is not ours to switch back on, and neither is one that
        never had a verification to answer.
        """
        user = self.register()
        self.assertEqual(self.verify('ana@correo.test', self.last_code()).status_code, 200)
        User.objects.filter(pk=user.pk).update(is_active=False)              # switched off afterwards, by whoever
        outbox_module.outbox = []
        self.age(user, hours=25)

        self.resend('ana@correo.test')
        self.assertEqual(outbox_module.outbox, [])                           # no code is minted for it
        self.assertEqual(user.account_tokens.count(), 1)

        # …and if a live code existed anyway, it would not revive the account.
        with mock.patch('store.verification_codes.secrets.randbelow', return_value=135790):
            issued = verification_codes.issue(user)
        self.assert_refused(self.verify('ana@correo.test', issued.code))
        user.refresh_from_db()
        self.assertFalse(user.is_active)

        never_asked = User.objects.create_user('sin.verificar', 'sin@correo.test', PASSWORD, is_active=False)
        self.resend('sin@correo.test')
        self.assertEqual(outbox_module.outbox, [])
        self.assertFalse(AccountToken.objects.filter(user=never_asked).exists())

    def test_a_code_spends_every_link_the_account_was_sent(self):
        with mock.patch('store.verification_codes.secrets.randbelow', side_effect=[111111, 222222]):
            user = self.register()
            first_link = re.search(r'token=([A-Za-z0-9_-]+)', outbox_module.outbox[-1].body).group(1)
            self.age(user, seconds=61)
            self.resend('ana@correo.test')
        self.assertEqual(self.verify('ana@correo.test', '222222').status_code, 200)

        self.assertFalse(user.account_tokens.filter(used_at__isnull=True).exists())
        User.objects.filter(pk=user.pk).update(is_active=False)
        again = self.client.post('/api/auth/verify-email/', {'token': first_link}, format='json')
        self.assertEqual(again.status_code, 400, again.content)
        user.refresh_from_db()
        self.assertFalse(user.is_active)


class ResendingTest(_Base):
    def test_a_new_code_kills_the_one_before(self):
        with mock.patch('store.verification_codes.secrets.randbelow', side_effect=[111111, 222222]):
            user = self.register()
            first = self.last_code()
            self.age(user, seconds=61)
            self.assertEqual(self.resend('ana@correo.test').status_code, 200)
        self.assertEqual(len(outbox_module.outbox), 2)
        second = self.last_code()
        self.assertEqual((first, second), ('111111', '222222'))
        self.assert_refused(self.verify('ana@correo.test', first))
        self.assertEqual(self.verify('ana@correo.test', second).status_code, 200)

    def test_the_one_before_is_dead_even_with_the_same_digits(self):
        user = self.register()
        self.age(user, seconds=61)
        self.resend('ana@correo.test')
        old, new = user.account_tokens.order_by('pk')
        self.assertEqual((old.code_hash, old.code_expires_at), ('', None))
        self.assertTrue(new.code_hash)

    def test_within_a_minute_nothing_new_is_sent_and_nothing_is_said(self):
        user = self.register()
        first = self.last_code()
        for seconds in (0, 30, 29):
            self.age(user, seconds=seconds)
            response = self.resend('ana@correo.test')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), self.resend('nadie@correo.test').json())
        self.assertEqual(len(outbox_module.outbox), 1)
        self.assertEqual(user.account_tokens.count(), 1)
        self.assertEqual(self.verify('ana@correo.test', first).status_code, 200)       # and the first still works

    def test_five_codes_a_day_for_one_account_whoever_asks(self):
        user = self.register()                                # the first of the day
        for _ in range(4):
            self.age(user, minutes=2)
            self.resend('ana@correo.test')
        self.assertEqual(len(outbox_module.outbox), 5)

        for _ in range(3):                                    # from any network: the cache is cleared each time
            self.age(user, minutes=2)
            response = self.resend('ana@correo.test')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), self.resend('nadie@correo.test').json())
        self.assertEqual(len(outbox_module.outbox), 5)
        self.assertEqual(user.account_tokens.count(), 5)

        self.age(user, hours=24)                              # tomorrow
        self.resend('ana@correo.test')
        self.assertEqual(len(outbox_module.outbox), 6)

    def test_asking_for_a_password_reset_does_not_use_up_the_day(self):
        user = self.register()
        for _ in range(6):
            AccountToken.make(user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        self.age(user, minutes=2)
        self.resend('ana@correo.test')
        self.assertEqual(len(outbox_module.outbox), 2)


class DeliveryTest(_Base):
    """A mail that did not leave must not cost what a mail costs."""

    def failing(self):
        from django.core.mail.message import EmailMessage

        return mock.patch.object(EmailMessage, 'send', side_effect=TimeoutError('sin respuesta'))

    def test_a_resend_that_was_not_delivered_spends_neither_the_wait_nor_the_day_nor_the_old_code(self):
        user = self.register()
        first = self.last_code()
        with self.failing(), self.assertLogs('store.emails', level='ERROR'):
            for _ in range(6):
                self.age(user, seconds=61)
                self.assertEqual(self.resend('ana@correo.test').status_code, 200)
        self.assertEqual(len(outbox_module.outbox), 1)
        self.assertEqual(user.account_tokens.count(), 1)                     # nothing was kept of what never left

        # The server is back: the very next request is served, at once.
        self.resend('ana@correo.test')
        self.assertEqual(len(outbox_module.outbox), 2)

        self.assertNotEqual(self.last_code(), '')
        del first

    def test_the_old_code_still_works_while_the_new_one_cannot_be_delivered(self):
        user = self.register()
        first = self.last_code()
        self.age(user, seconds=61)
        with self.failing(), self.assertLogs('store.emails', level='ERROR'):
            self.resend('ana@correo.test')
        self.assertEqual(self.verify('ana@correo.test', first).status_code, 200)

    def test_a_registration_whose_mail_failed_keeps_its_row_and_can_ask_again(self):
        """Without that row the account would not even count as «never verified»."""
        with self.failing(), self.assertLogs('store.emails', level='ERROR'):
            user = self.register()
        self.assertEqual(outbox_module.outbox, [])
        self.assertEqual(user.account_tokens.count(), 1)
        from store import accounts
        self.assertTrue(accounts.is_unverified(user))

        self.age(user, seconds=61)
        self.resend('ana@correo.test')
        self.assertEqual(len(outbox_module.outbox), 1)
        self.assertEqual(self.verify('ana@correo.test', self.last_code()).status_code, 200)

    def test_at_the_daily_limit_the_last_code_is_still_alive(self):
        user = self.register()
        for _ in range(4):
            self.age(user, minutes=2)
            self.resend('ana@correo.test')
        last = self.last_code()
        self.age(user, minutes=2)
        self.resend('ana@correo.test')                                       # the sixth: nothing is sent
        self.assertEqual(len(outbox_module.outbox), 5)
        self.assertEqual(self.verify('ana@correo.test', last).status_code, 200)


class GuardsTest(_Base):
    def test_one_address_cannot_try_without_end(self):
        self.register()
        cache.clear()
        statuses = [self.client.post(VERIFY, {'email': 'ana@correo.test', 'code': '000000'}, format='json').status_code
                    for _ in range(12)]
        self.assertIn(429, statuses)
        self.assertEqual(statuses[0], 400)

    def test_the_code_is_written_to_no_log(self):
        from django.core.mail.message import EmailMessage

        records = []

        class Keep(logging.Handler):
            def emit(self, record):
                records.append(self.format(record) + repr(record.args))

        handler = Keep(level=logging.DEBUG)
        handler.setFormatter(logging.Formatter('%(message)s\n%(exc_text)s'))
        watched = [logging.getLogger()] + [
            logger for logger in logging.root.manager.loggerDict.values() if isinstance(logger, logging.Logger)]
        for logger in watched:
            logger.addHandler(handler)
        try:
            with mock.patch('store.verification_codes.secrets.randbelow', return_value=774411):
                with mock.patch.object(EmailMessage, 'send', side_effect=TimeoutError('sin respuesta')):
                    self.register('ana')
                with mock.patch('store.mail.service.chevron.render', side_effect=RuntimeError('boom')):
                    self.register('luis')
                self.register('eva')
                self.verify('eva@correo.test', '774412')
                self.verify('eva@correo.test', '774411')
        finally:
            for logger in watched:
                logger.removeHandler(handler)
        self.assertTrue(records)
        self.assertNotIn('774411', '\n'.join(records))
        self.assertNotIn('774412', '\n'.join(records))

    def test_a_code_tried_too_often_is_written_down_without_the_code(self):
        with mock.patch('store.verification_codes.secrets.randbelow', return_value=774411):
            user = self.register()
        with self.assertLogs('store.security', level='WARNING') as captured:
            for _ in range(5):
                self.verify('ana@correo.test', '000000')
        logged = '\n'.join(captured.output)
        self.assertIn('verify_code_exhausted', logged)
        self.assertIn(f'user_id={user.pk}', logged)
        for secret in ('774411', '000000', 'ana@correo.test'):
            self.assertNotIn(secret, logged)

    def test_the_mail_says_the_code_is_not_to_be_given_to_anybody(self):
        self.register()
        self.assertIn('No lo compartas', outbox_module.outbox[-1].body)
        with override_settings(DEFAULT_STOREFRONT_COMPANY_SLUG=''):
            self.register('luis')
        self.assertIn('No lo compartas', outbox_module.outbox[-1].body)

    def test_an_invited_person_is_sent_no_code_because_nothing_is_left_to_prove(self):
        """STAFF-ONBOARDING-01 stands: the invitation already answered the question."""
        from store.models import CompanyRole
        from store.staff_services import create_invitation

        pilot = Company.objects.get(slug='black-dog-store')
        admin = User.objects.create_user('admin', 'admin@tienda.test', 'x')
        _invitation, raw, _ = create_invitation(
            company=pilot, email='luis@correo.test', first_name='Luis', last_name='Paz',
            role_id=CompanyRole.objects.get(company=pilot, slug='ventas').pk, invited_by=admin)
        cache.clear()
        response = self.client.post('/api/auth/register/', {
            'username': 'luis', 'email': 'luis@correo.test', 'first_name': 'Luis', 'last_name': 'Paz',
            'password': PASSWORD, 'password_confirm': PASSWORD, 'invitation_token': raw,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertFalse(response.json()['requires_verification'])
        self.assertEqual(outbox_module.outbox, [])
        self.assertFalse(AccountToken.objects.filter(user__username='luis').exists())

    def test_the_numbers_are_the_ones_the_owner_asked_for(self):
        self.assertEqual(verification_codes.CODE_TTL, timedelta(minutes=15))
        self.assertEqual(verification_codes.MAX_ATTEMPTS, 5)
        self.assertEqual(verification_codes.RESEND_WAIT, timedelta(seconds=60))
        self.assertEqual(verification_codes.DAILY_CODES, 5)


@override_settings(
    REQUIRE_EMAIL_VERIFICATION=True, FRONTEND_URL=SITE, DEFAULT_STOREFRONT_COMPANY_SLUG='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class ConcurrencyTest(TransactionTestCase):
    """Real commits and real row locks: what a `TestCase` cannot see."""

    serialized_rollback = True

    def setUp(self):
        cache.clear()
        outbox_module.outbox = []
        with mock.patch('store.verification_codes.secrets.randbelow', return_value=246810):
            response = APIClient().post('/api/auth/register/', {
                'username': 'ana', 'email': 'ana@correo.test', 'first_name': 'Ana', 'last_name': 'Torres',
                'password': PASSWORD, 'password_confirm': PASSWORD,
            }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.user = User.objects.get(username='ana')

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

    def test_the_right_code_and_a_resend_at_once_never_block_each_other(self):
        """One locks the account and then the code; the other must not do it the other way round."""
        for round_ in range(12):
            # A fresh account-in-waiting each round: one code, sent two minutes ago.
            User.objects.filter(pk=self.user.pk).update(is_active=False)
            AccountToken.objects.filter(user=self.user).delete()
            with mock.patch('store.verification_codes.secrets.randbelow', return_value=100000 + round_):
                issued = verification_codes.issue(self.user)
            AccountToken.objects.filter(user=self.user).update(created_at=timezone.now() - timedelta(minutes=2))

            def send_it_again():
                # What the resend endpoint does: a new code, and — its mail gone — the old one killed.
                again = verification_codes.issue(self.user)
                if again is not None:
                    verification_codes.settle(again)
                return again

            results = self.together([
                lambda: verification_codes.check('ana@correo.test', issued.code),
                send_it_again,
            ])

            for result in results:
                self.assertNotIsInstance(result, Exception, repr(result))
            # Whichever went first, nothing is left half done: verified, or a new code waiting.
            self.user.refresh_from_db()
            self.assertTrue(self.user.is_active or AccountToken.objects.filter(user=self.user).count() == 2)

    def test_many_wrong_guesses_at_once_are_still_five(self):
        results = self.together([lambda: verification_codes.check('ana@correo.test', '000000')] * 12)
        self.assertEqual([r for r in results if r is not None], [])
        self.assertEqual(AccountToken.objects.get(user=self.user).code_attempts, 5)
        self.assertIsNone(verification_codes.check('ana@correo.test', '246810'))       # the right one, too late

    def test_the_right_code_many_times_at_once_finishes_the_account_once(self):
        results = self.together([lambda: verification_codes.check('ana@correo.test', '246810')] * 8)
        self.assertEqual(len([r for r in results if r is not None and not isinstance(r, Exception)]), 1, results)

    def test_many_resends_at_once_send_one(self):
        AccountToken.objects.filter(user=self.user).update(created_at=timezone.now() - timedelta(minutes=2))
        results = self.together([lambda: verification_codes.issue(self.user)] * 8)
        self.assertEqual(len([r for r in results if r is not None and not isinstance(r, Exception)]), 1, results)
        self.assertEqual(AccountToken.objects.filter(user=self.user).count(), 2)
