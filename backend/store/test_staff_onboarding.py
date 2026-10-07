"""
STAFF-ONBOARDING-01 · an invited worker can always finish, and only as themselves.

WHAT HAPPENED IN PRODUCTION

A worker opened their invitation, was offered «Crear cuenta», and was then told
the account already existed. The invitation and the registration agree on who
somebody is — both compare the e-mail without case or spaces — so that was never
the disagreement. The road was:

  1. the invitation sent them to the ordinary registration;
  2. where e-mail verification is required (production), that creates an
     INACTIVE account and sends a second e-mail;
  3. from then on the address «is already registered», but the account cannot
     log in, and password recovery ignored it because it was not active.

Nothing in the suite walked that road with verification switched on, which is
why it was not seen. These tests do, over HTTP, the way a browser does.

WHAT THE ROAD IS NOW

  * No account           → registration that carries the invitation: the account
                           is born active, because the link already proves the
                           mailbox. No second e-mail.
  * An account, password → log in, accept. As before.
  * An account, no usable or known password, or never verified
                         → the existing password recovery, which now also serves
                           a never-verified account and brings the worker back
                           to the invitation.

The password is the worker's in every case. Nobody else sets or sees it.
"""
import logging
import re
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store.company_provisioning import provision_company_access_defaults
from store.models import (
    AccountToken, AdminAuditLog, Company, CompanyRole, Membership,
    MembershipRoleAssignment, StaffInvitation, UserProfile,
)
from store.staff_services import create_invitation, resend_invitation

User = get_user_model()

PASSWORD = 'Una-clave-larga-2026!'
OTHER_PASSWORD = 'Otra-clave-distinta-2026!'
WORKER = 'trabajador@empresa.test'
ACCEPT = '/api/staff/invitations/accept/'
REGISTER = '/api/auth/register/'
LOGIN = '/api/auth/login/'
RESET_REQUEST = '/api/auth/password-reset/request/'
RESET_CONFIRM = '/api/auth/password-reset/confirm/'


@contextmanager
def _everything_logged():
    """
    Every line any logger writes while the block runs, formatted as it would be
    read. Attached to each logger by name, because some do not propagate — and a
    check of «no secret in the logs» that cannot see a logger proves nothing
    about it.
    """
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(self.format(record) + ' ' + repr(record.args))

    handler = Keep(level=logging.DEBUG)
    watched = [logging.getLogger()] + [
        logger for logger in logging.root.manager.loggerDict.values() if isinstance(logger, logging.Logger)
    ]
    levels = [(logger, logger.level) for logger in watched]
    for logger in watched:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        for logger, level in levels:
            logger.removeHandler(handler)
            logger.setLevel(level)


def company(slug, name):
    row = Company.objects.create(name=name, slug=slug, legal_name=f'{name} S.A.C.', tax_id='20000000001')
    provision_company_access_defaults(row)
    return row


@override_settings(
    REQUIRE_EMAIL_VERIFICATION=True,                       # as production runs
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FRONTEND_URL='https://tienda.test',
)
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.company = company('onb-a', 'Empresa A')
        self.role = CompanyRole.objects.get(company=self.company, slug='ventas')
        self.admin = User.objects.create_user('onb_admin', 'admin@empresa.test', PASSWORD)
        self.browser = APIClient()

    def invite(self, email=WORKER, **extra):
        _invitation, raw, _created = create_invitation(
            company=self.company, email=email, first_name='Ana', last_name='Torres',
            role_id=self.role.pk, invited_by=self.admin, **extra,
        )
        return raw

    def seen(self, raw, client=None):
        return (client or APIClient()).get(ACCEPT, {'token': raw})

    def register(self, raw=None, *, email=WORKER, username='ana.torres', password=PASSWORD, client=None):
        body = {'username': username, 'email': email, 'password': password, 'password_confirm': password}
        if raw is not None:
            body['invitation_token'] = raw
        return (client or self.browser).post(REGISTER, body, format='json')

    def log_in(self, username='ana.torres', password=PASSWORD, client=None):
        return (client or self.browser).post(LOGIN, {'username': username, 'password': password}, format='json')

    def accept(self, raw, client=None):
        return (client or self.browser).post(ACCEPT, {'token': raw}, format='json')

    def reset_link(self):
        """The link of the last recovery e-mail, as the worker would click it."""
        message = [m for m in mail.outbox if '/auth/reset-password' in m.body][-1]
        return re.search(r'https://tienda\.test(/auth/reset-password\?\S+)', message.body).group(1)

    def reset_token(self):
        return re.search(r'[?&]token=([A-Za-z0-9_\-]+)', self.reset_link()).group(1)

    def membership(self, email=WORKER):
        return Membership.objects.filter(company=self.company, user__email__iexact=email, is_active=True).first()


class NewWorkerTest(_Base):
    """A · the address has no account."""

    def test_the_invitation_says_there_is_no_account_yet(self):
        body = self.seen(self.invite()).json()
        self.assertEqual(body['account_state'], 'none')
        self.assertFalse(body['requires_authentication'])

    def test_registering_with_the_invitation_gives_an_account_that_works_at_once(self):
        raw = self.invite()
        response = self.register(raw)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertFalse(response.json()['requires_verification'])
        self.assertTrue(User.objects.get(email=WORKER).is_active)
        self.assertEqual([m.subject for m in mail.outbox if 'erific' in m.subject], [])

    def test_the_whole_road_ends_in_the_right_membership(self):
        raw = self.invite()
        self.register(raw)
        self.assertEqual(self.log_in().status_code, 200)
        accepted = self.accept(raw)
        self.assertEqual(accepted.status_code, 200, accepted.content)
        membership = self.membership()
        self.assertIsNotNone(membership)
        self.assertEqual(membership.company_id, self.company.pk)
        self.assertEqual(
            list(MembershipRoleAssignment.objects.filter(membership=membership, is_active=True).values_list('role_id', flat=True)),
            [self.role.pk],
        )
        self.assertEqual(Membership.objects.filter(user__email=WORKER).count(), 1)
        self.assertEqual(self.seen(raw).status_code, 404)             # the link is spent

    def test_the_address_is_compared_as_people_write_it(self):
        """K · upper case and stray spaces are the same person."""
        raw = self.invite('  Trabajador@Empresa.TEST ')
        self.assertEqual(StaffInvitation.objects.get().email, WORKER)
        response = self.register(raw, email='TRABAJADOR@empresa.test')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(User.objects.get(email=WORKER).is_active)

    def test_the_invitation_vouches_for_its_own_address_and_no_other(self):
        """Holding a link for one mailbox proves nothing about another one."""
        raw = self.invite()
        response = self.register(raw, email='otra@empresa.test')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['requires_verification'])
        self.assertFalse(User.objects.get(email='otra@empresa.test').is_active)

    def test_a_link_that_no_longer_serves_vouches_for_nothing(self):
        for spoil in ('invented', 'revoked', 'expired', 'resent'):
            with self.subTest(spoil):
                cache.clear()
                email = f'{spoil}@empresa.test'
                raw = self.invite(email)
                invitation = StaffInvitation.objects.get(email=email)
                if spoil == 'invented':
                    raw = 'x' * 64
                elif spoil == 'revoked':
                    invitation.status = StaffInvitation.STATUS_REVOKED
                    invitation.save(update_fields=['status'])
                elif spoil == 'expired':
                    invitation.expires_at = timezone.now() - timezone.timedelta(minutes=1)
                    invitation.save(update_fields=['expires_at'])
                else:
                    resend_invitation(invitation)                     # G · the old link dies
                response = self.register(raw, email=email, username=f'u_{spoil}', client=APIClient())
                self.assertEqual(response.status_code, 201)
                self.assertFalse(User.objects.get(email=email).is_active, spoil)

    def test_without_an_invitation_registration_is_what_it_was(self):
        response = self.register(None, email='cliente@correo.test', username='cliente')
        self.assertTrue(response.json()['requires_verification'])
        self.assertFalse(User.objects.get(email='cliente@correo.test').is_active)
        self.assertEqual(len([m for m in mail.outbox if 'erific' in m.subject]), 1)

    def test_the_password_rules_are_not_relaxed_for_an_invited_worker(self):
        raw = self.invite()
        response = self.register(raw, password='12345678')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email=WORKER).exists())


class ExistingAccountTest(_Base):
    """B · the address already has an account, and its owner knows the password."""

    def setUp(self):
        super().setUp()
        self.person = User.objects.create_user('ana.torres', 'Trabajador@Empresa.test', PASSWORD)

    def test_the_invitation_says_to_log_in_and_never_to_create_another_account(self):
        body = self.seen(self.invite()).json()
        self.assertEqual(body['account_state'], 'active')
        self.assertTrue(body['requires_authentication'])

    def test_a_second_account_for_the_same_address_cannot_be_made_with_the_invitation(self):
        """I · the road that ended in «ya está registrado» is closed at its start, and stays shut here."""
        raw = self.invite()
        response = self.register(raw, username='otra.ana')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.filter(email__iexact=WORKER).count(), 1)

    def test_logging_in_and_accepting(self):
        raw = self.invite()
        self.assertEqual(self.log_in().status_code, 200)
        self.assertEqual(self.accept(raw).status_code, 200)
        self.assertEqual(self.membership().user_id, self.person.pk)

    def test_the_link_alone_is_not_enough(self):
        raw = self.invite()
        self.assertEqual(self.accept(raw, APIClient()).status_code, 401)
        self.assertIsNone(self.membership())

    def test_somebody_else_logged_in_cannot_accept_it(self):
        """D · asked of the server, not of a missing button."""
        raw = self.invite()
        User.objects.create_user('intruso', 'intruso@empresa.test', PASSWORD)
        other = APIClient()
        self.assertEqual(self.log_in('intruso', client=other).status_code, 200)
        response = self.accept(raw, other)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(WORKER, response.content.decode())
        self.assertEqual(Membership.objects.filter(company=self.company).count(), 0)

    def test_accepting_twice_makes_one_membership(self):
        raw = self.invite()
        self.log_in()
        self.assertEqual(self.accept(raw).status_code, 200)
        self.assertEqual(self.accept(raw).status_code, 404)
        self.assertEqual(Membership.objects.filter(company=self.company, user=self.person).count(), 1)


class UnknownPasswordTest(_Base):
    """C · the account exists and its owner cannot log in with a password."""

    def recover(self, raw, new_password=OTHER_PASSWORD):
        self.browser.post(RESET_REQUEST, {'email': WORKER, 'next': f'/invitacion?token={raw}'}, format='json')
        response = self.browser.post(RESET_CONFIRM, {'token': self.reset_token(), 'new_password': new_password}, format='json')
        # A reset closes every session of the account, to the second. Nobody
        # logs in within the same second as they reset; a test does.
        UserProfile.objects.filter(user__email__iexact=WORKER).update(
            tokens_valid_after=timezone.now() - timezone.timedelta(seconds=5),
        )
        return response

    def test_recovery_brings_the_worker_back_to_the_invitation(self):
        User.objects.create_user('ana.torres', WORKER, PASSWORD)
        raw = self.invite()
        response = self.browser.post(RESET_REQUEST, {'email': WORKER, 'next': f'/invitacion?token={raw}'}, format='json')
        self.assertEqual(response.status_code, 200)
        link = self.reset_link()
        self.assertIn('token=', link)
        self.assertIn(f'next=%2Finvitacion%3Ftoken%3D{raw}', link)

    def test_a_forgotten_password_is_replaced_and_the_invitation_accepted(self):
        User.objects.create_user('ana.torres', WORKER, PASSWORD)
        raw = self.invite()
        self.assertEqual(self.recover(raw).status_code, 200)
        self.assertEqual(self.log_in(password=PASSWORD).status_code, 401)
        self.assertEqual(self.log_in(password=OTHER_PASSWORD).status_code, 200)
        self.assertEqual(self.accept(raw).status_code, 200)
        self.assertIsNotNone(self.membership())

    def test_an_account_without_a_usable_password_gets_one(self):
        """J · «Continuar con Google» leaves an account that has never had a password."""
        person = User(username='ana.google', email=WORKER)
        person.set_unusable_password()
        person.save()
        raw = self.invite()
        self.assertEqual(self.seen(raw).json()['account_state'], 'active')
        recovered = self.recover(raw)
        self.assertEqual(recovered.status_code, 200)
        # Somebody who never chose a password does not know they have a user name.
        self.assertEqual(recovered.json()['username'], 'ana.google')
        person.refresh_from_db()
        self.assertTrue(person.has_usable_password())
        self.assertEqual(self.log_in('ana.google', OTHER_PASSWORD).status_code, 200)
        self.assertEqual(self.accept(raw).status_code, 200)

    def test_the_account_production_left_half_made_can_be_finished(self):
        """
        I · THE REGRESSION. Registered the ordinary way, never verified: it
        could not log in, could not register again and was not sent a recovery
        e-mail. Now the invitation says so and recovery finishes the account.
        """
        self.register(None)                                           # the old road: inactive
        person = User.objects.get(email=WORKER)
        self.assertFalse(person.is_active)
        raw = self.invite()
        self.assertEqual(self.seen(raw).json()['account_state'], 'unverified')
        self.assertEqual(self.log_in().status_code, 401)

        self.assertEqual(self.recover(raw).status_code, 200)
        person.refresh_from_db()
        self.assertTrue(person.is_active)
        self.assertEqual(self.log_in(password=PASSWORD).status_code, 401)   # whoever set the first one is out
        self.assertEqual(self.log_in(password=OTHER_PASSWORD).status_code, 200)
        self.assertEqual(self.accept(raw).status_code, 200)
        self.assertEqual(self.membership().user_id, person.pk)

    def test_a_never_verified_account_can_recover_without_any_invitation(self):
        self.register(None)
        self.browser.post(RESET_REQUEST, {'email': WORKER}, format='json')
        response = self.browser.post(RESET_CONFIRM, {'token': self.reset_token(), 'new_password': OTHER_PASSWORD}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.get(email=WORKER).is_active)

    def test_an_account_switched_off_after_being_verified_is_not_switched_back_on(self):
        """Recovery finishes an account nobody ever confirmed. It is not a way back in."""
        self.register(None)
        person = User.objects.get(email=WORKER)
        AccountToken.objects.filter(user=person, purpose=AccountToken.PURPOSE_EMAIL_VERIFICATION).update(used_at=timezone.now())
        raw = self.invite()
        before = len(mail.outbox)
        response = self.browser.post(RESET_REQUEST, {'email': WORKER, 'next': f'/invitacion?token={raw}'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), before)
        person.refresh_from_db()
        self.assertFalse(person.is_active)

    def test_two_accounts_with_one_address_get_the_same_quiet_answer(self):
        """A legacy duplicate used to be a 500 here."""
        User.objects.create_user('ana.uno', WORKER, PASSWORD)
        User.objects.create_user('ana.dos', WORKER.upper(), PASSWORD)
        before = len(mail.outbox)
        response = self.browser.post(RESET_REQUEST, {'email': WORKER}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), before)


class ReturnAddressTest(_Base):
    """The recovery e-mail carries the worker back to an invitation, and nowhere else."""

    def setUp(self):
        super().setUp()
        User.objects.create_user('ana.torres', WORKER, PASSWORD)

    def test_only_an_invitation_address_travels_in_the_email(self):
        for unsafe in (
            'https://evil.test/invitacion?token=' + 'a' * 64, '//evil.test', '/admin',
            '/invitacion?token=' + 'a' * 64 + '&next=//evil.test', '/invitacion?token=a b',
            '/invitacion?token=', '/auth?next=/invitacion?token=' + 'a' * 64, 'javascript:alert(1)',
            '/invitacion?token=' + 'a' * 500,
        ):
            with self.subTest(unsafe):
                cache.clear()
                mail.outbox = []
                response = self.browser.post(RESET_REQUEST, {'email': WORKER, 'next': unsafe}, format='json')
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('next=', self.reset_link())

    def test_the_answer_is_the_same_whoever_asks(self):
        known = self.browser.post(RESET_REQUEST, {'email': WORKER, 'next': '/invitacion?token=' + 'a' * 64}, format='json')
        unknown = APIClient().post(RESET_REQUEST, {'email': 'nadie@empresa.test', 'next': '/invitacion?token=' + 'a' * 64}, format='json')
        self.assertEqual((known.status_code, known.json()), (unknown.status_code, unknown.json()))


class DisclosureTest(_Base):
    """F, L · what the road tells, and to whom."""

    def test_a_link_that_does_not_serve_says_nothing_about_any_account(self):
        User.objects.create_user('ana.torres', WORKER, PASSWORD)
        response = self.seen('x' * 64)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(set(response.json()), {'detail'})

    def test_the_invitation_tells_about_its_own_address_only(self):
        User.objects.create_user('otra', 'otra@empresa.test', PASSWORD)
        body = self.seen(self.invite()).json()
        self.assertEqual(body['account_state'], 'none')
        self.assertNotIn('otra', str(body))

    def test_another_company_knows_nothing_of_this_invitation(self):
        """E · the worker's account in company A gives nobody in company B a way in."""
        other = company('onb-b', 'Empresa B')
        raw = self.invite()
        self.register(raw)
        self.log_in()
        self.accept(raw)
        self.assertEqual(Membership.objects.filter(company=other).count(), 0)
        self.assertEqual(Membership.objects.filter(user__email=WORKER).values_list('company_id', flat=True).get(), self.company.pk)

    def test_no_password_or_token_leaves_in_a_response_a_log_or_the_audit(self):
        raw = self.invite()
        with _everything_logged() as records:
            registered = self.register(raw)
            logged = self.log_in()
            self.browser.post(RESET_REQUEST, {'email': WORKER, 'next': f'/invitacion?token={raw}'}, format='json')
            reset_token = self.reset_token()
            accepted = self.accept(raw)
        self.assertEqual(accepted.status_code, 200)
        # The capture is real: the login it watched is in it.
        self.assertTrue(any('login_ok' in line for line in records), records)
        said = ' '.join(r.content.decode() for r in (registered, logged, accepted, self.seen(raw)))
        written = '\n'.join(records) + str(list(AdminAuditLog.objects.values('action', 'metadata')))
        for secret in (PASSWORD, raw, reset_token):
            self.assertNotIn(secret, said)
            self.assertNotIn(secret, written)
        self.assertEqual(
            list(AdminAuditLog.objects.filter(action='staff_invitation_accepted').values_list('company_id', flat=True)),
            [self.company.pk],
        )

    def test_a_session_still_needs_its_csrf_token(self):
        raw = self.invite()
        self.register(raw)
        strict = APIClient(enforce_csrf_checks=True)
        self.assertEqual(self.log_in(client=strict).status_code, 200)
        self.assertTrue(strict.cookies['blackdog_access']['httponly'])
        self.assertEqual(strict.post(ACCEPT, {'token': raw}, format='json').status_code, 403)
        self.assertIsNone(self.membership())
