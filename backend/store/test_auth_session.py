"""
AUTH-B — SESSION-IDLE-01: a web session dies after 30 minutes without real
activity, whatever the browser keeps doing in the background.

`docs/adr-auth-sessions.md` §2. The rules these tests pin:

  * One `AuthSession` row per signed-in browser. Its `sid` travels as a claim
    in the refresh token, is copied into every access token and survives every
    rotation: renewing is not a new session.
  * The row decides, on every request of the cookie channel. The clock is the
    database's: these tests AGE THE ROW, they never move a clock.
  * Activity is what the browser declares with `X-Session-Activity: 1` (or the
    keepalive). The server records it at most once per 30 s and never from a
    renewal. A dead session does not come back with any header.
  * A dead session (idle, absolute limit, logout, password, deactivated) or an
    unusable cookie is a GUEST: public views serve it, both cookies are deleted,
    protected views answer 401 with `code: session_expired`. A merely expired
    access token of a LIVE session still answers 401, so the browser renews.
  * Staff sessions end 8 h after login; customer sessions 7 days after login.
    Neither slides.
  * The native app (V1 Bearer) is unchanged, and its tokens do not open the
    cookie channel.
"""
import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.functions import Now
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from store.models import AuthSession, Company, Membership, Order, UserProfile
from store.token_revocation import revoke_all_tokens

PASSWORD = 'ValidPass123!'
ACCESS = settings.JWT_COOKIE_ACCESS_NAME
REFRESH = settings.JWT_COOKIE_REFRESH_NAME
ME = '/api/auth/me/'
PUBLIC = '/api/auth/csrf/'
LOGIN = '/api/auth/login/'
REFRESH_URL = '/api/auth/refresh/'
LOGOUT = '/api/auth/logout/'
STATUS = '/api/auth/session/'
KEEPALIVE = '/api/auth/session/keepalive/'
ACTIVITY = {'HTTP_X_SESSION_ACTIVITY': '1'}


def sid_of(raw) -> str:
    """The `sid` claim of a raw token, read without trusting it."""
    return str(AccessToken(raw, verify=False).payload.get('sid', ''))


class _Base(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = User.objects.create_user('sesion', 'sesion@example.test', PASSWORD)

    def browser(self, user=None):
        client = APIClient()
        user = user or self.user
        res = client.post(LOGIN, {'username': user.username, 'password': PASSWORD}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        return client

    def session(self, client) -> AuthSession:
        return AuthSession.objects.get(sid=sid_of(client.cookies[ACCESS].value))

    def idle_for(self, session, **delta):
        AuthSession.objects.filter(pk=session.pk).update(last_activity_at=Now() - timedelta(**delta))

    def last_activity_age(self, session) -> timedelta:
        session.refresh_from_db()
        return timezone.now() - session.last_activity_at

    def assertCookiesCleared(self, res):
        for name in (ACCESS, REFRESH):
            self.assertIn(name, res.cookies, f'{name} no se borró')
            self.assertEqual(res.cookies[name]['max-age'], 0, f'{name} no se borró')

    def assertCookiesUntouched(self, res):
        for name in (ACCESS, REFRESH):
            self.assertNotIn(name, res.cookies)


class IdleWindowTest(_Base):
    def test_29_minutes_without_activity_is_still_valid(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=29)

        self.assertEqual(client.get(ME).status_code, 200)

    def test_29_minutes_30_seconds_is_still_valid(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=29, seconds=29)

        self.assertEqual(client.get(ME).status_code, 200)

    def test_30_minutes_is_expired_and_both_cookies_are_cleared(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=30)

        res = client.get(ME)

        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get('code'), 'session_expired')
        self.assertCookiesCleared(res)
        session.refresh_from_db()
        self.assertIsNotNone(session.ended_at)
        self.assertEqual(session.end_reason, AuthSession.END_IDLE)

    def test_an_expired_session_does_not_come_back_with_the_activity_header(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=31)

        self.assertEqual(client.get(ME, **ACTIVITY).status_code, 401)
        self.assertGreaterEqual(self.last_activity_age(session), timedelta(minutes=31))

    def test_the_answer_does_not_say_why_the_session_died(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        body = client.get(ME).json()

        self.assertNotIn('inactiv', str(body).lower())
        self.assertNotIn('idle', str(body).lower())

    def test_a_public_view_serves_a_dead_session_as_a_guest(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.get(PUBLIC)

        self.assertEqual(res.status_code, 200)
        self.assertCookiesCleared(res)

    def test_a_public_write_with_a_dead_session_is_served_as_a_guest(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.post('/api/auth/register/', {
            'username': 'nuevo', 'email': 'nuevo@example.test',
            'password': PASSWORD, 'password_confirm': PASSWORD,
        }, format='json')

        self.assertEqual(res.status_code, 201, res.content)
        self.assertCookiesCleared(res)

    def test_a_cookie_that_is_not_a_token_is_a_guest_on_a_public_view(self):
        client = APIClient()
        client.cookies[ACCESS] = 'no-es-un-token'

        res = client.get(PUBLIC)

        self.assertEqual(res.status_code, 200)
        self.assertCookiesCleared(res)

    def test_a_cookie_that_is_not_a_token_is_401_on_a_protected_view(self):
        client = APIClient()
        client.cookies[ACCESS] = 'no-es-un-token'

        res = client.get(ME)

        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get('code'), 'session_expired')

    def test_an_expired_access_token_of_a_live_session_still_asks_to_renew(self):
        client = self.browser()
        expired = AccessToken(client.cookies[ACCESS].value)
        expired.set_exp(lifetime=timedelta(seconds=-60))
        client.cookies[ACCESS] = str(expired)

        res = client.get('/api/categories/')

        # 401, not «guest»: renewing fixes it, and a signed-in buyer must never be
        # served as a stranger because a token expired a second ago.
        self.assertEqual(res.status_code, 401)
        self.assertCookiesUntouched(res)

    def test_an_expired_access_token_of_a_dead_session_is_a_guest(self):
        client = self.browser()
        session = self.session(client)
        expired = AccessToken(client.cookies[ACCESS].value)
        expired.set_exp(lifetime=timedelta(seconds=-60))
        client.cookies[ACCESS] = str(expired)
        self.idle_for(session, minutes=30)

        res = client.get(PUBLIC)

        self.assertEqual(res.status_code, 200)
        self.assertCookiesCleared(res)


class RefreshTest(_Base):
    def test_renewing_keeps_the_same_session(self):
        client = self.browser()
        sid = sid_of(client.cookies[ACCESS].value)
        old_refresh = client.cookies[REFRESH].value

        res = client.post(REFRESH_URL)

        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotEqual(res.cookies[REFRESH].value, old_refresh)
        self.assertEqual(sid_of(res.cookies[REFRESH].value), sid)
        self.assertEqual(sid_of(res.cookies[ACCESS].value), sid)
        self.assertEqual(AuthSession.objects.count(), 1)

    def test_renewing_is_not_activity(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=20)

        self.assertEqual(client.post(REFRESH_URL).status_code, 200)

        self.assertGreaterEqual(self.last_activity_age(session), timedelta(minutes=20))

    def test_renewing_with_the_activity_header_is_still_not_activity(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=20)

        self.assertEqual(client.post(REFRESH_URL, **ACTIVITY).status_code, 200)

        self.assertGreaterEqual(self.last_activity_age(session), timedelta(minutes=20))

    def test_a_refresh_after_the_idle_limit_is_refused_without_rotating(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=30)
        before = OutstandingToken.objects.count()

        res = client.post(REFRESH_URL)

        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get('code'), 'session_expired')
        self.assertCookiesCleared(res)
        self.assertEqual(OutstandingToken.objects.count(), before)
        session.refresh_from_db()
        self.assertEqual(session.end_reason, AuthSession.END_IDLE)

    def test_a_refresh_after_the_absolute_limit_is_refused(self):
        client = self.browser()
        session = self.session(client)
        AuthSession.objects.filter(pk=session.pk).update(absolute_expires_at=Now() - timedelta(seconds=1))

        res = client.post(REFRESH_URL)

        self.assertEqual(res.status_code, 401)
        session.refresh_from_db()
        self.assertEqual(session.end_reason, AuthSession.END_ABSOLUTE)

    def test_a_refresh_token_without_a_session_is_refused(self):
        client = APIClient()
        client.cookies[REFRESH] = str(RefreshToken.for_user(self.user))

        res = client.post(REFRESH_URL)

        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get('code'), 'session_expired')
        self.assertCookiesCleared(res)

    def test_a_refresh_used_twice_is_refused_without_touching_the_cookies_of_the_winner(self):
        client = self.browser()
        old_refresh = client.cookies[REFRESH].value
        self.assertEqual(client.post(REFRESH_URL).status_code, 200)

        loser = APIClient()
        loser.cookies[REFRESH] = old_refresh
        res = loser.post(REFRESH_URL)

        self.assertEqual(res.status_code, 401)
        # The cookies belong to whoever renewed first: deleting them here would
        # sign out the tab that won.
        self.assertCookiesUntouched(res)
        self.assertIsNone(self.session(client).ended_at)

    def test_a_customer_given_a_membership_becomes_staff_at_once(self):
        client = self.browser()
        session = self.session(client)
        self.assertEqual(session.kind, AuthSession.KIND_CUSTOMER)
        company = Company.objects.create(name='Tienda B', slug='tienda-b', legal_name='Tienda B S.A.C.',
                                         tax_id='20000000002')

        Membership.objects.create(user=self.user, company=company, role='sales')

        session.refresh_from_db()
        self.assertEqual(session.kind, AuthSession.KIND_STAFF)
        self.assertLessEqual(session.absolute_expires_at - session.created_at, timedelta(hours=8))

    def test_a_staff_session_past_8_hours_cannot_renew_even_if_it_started_as_a_customer(self):
        client = self.browser()
        session = self.session(client)
        AuthSession.objects.filter(pk=session.pk).update(created_at=Now() - timedelta(hours=9))
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.ROLE_SALES)  # no signal: renewal re-checks

        res = client.post(REFRESH_URL)  # the renewal tightens first, then judges

        self.assertEqual(res.status_code, 401)
        session.refresh_from_db()
        self.assertEqual((session.kind, session.end_reason), (AuthSession.KIND_STAFF, AuthSession.END_ABSOLUTE))

    def test_a_customer_session_does_not_slide(self):
        client = self.browser()
        session = self.session(client)
        cap = session.absolute_expires_at

        self.assertEqual(client.post(REFRESH_URL).status_code, 200)
        client.get(ME, **ACTIVITY)

        session.refresh_from_db()
        self.assertEqual(session.absolute_expires_at, cap)

    def test_losing_a_membership_never_lengthens_a_session(self):
        company = Company.objects.create(name='Tienda C', slug='tienda-c', legal_name='Tienda C S.A.C.',
                                         tax_id='20000000003')
        membership = Membership.objects.create(user=self.user, company=company, role='sales')
        client = self.browser()
        session = self.session(client)
        cap = session.absolute_expires_at
        membership.delete()

        self.assertEqual(client.post(REFRESH_URL).status_code, 200)

        session.refresh_from_db()
        self.assertEqual(session.absolute_expires_at, cap)


class ActivitySignalTest(_Base):
    def test_reads_without_the_header_write_nothing(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=10)

        for _ in range(3):
            self.assertEqual(client.get(ME).status_code, 200)

        self.assertGreaterEqual(self.last_activity_age(session), timedelta(minutes=10))

    def test_an_hour_of_polling_does_not_keep_the_session(self):
        client = self.browser()
        session = self.session(client)
        # A poll every 45 s since login: none of them carries the header.
        for minutes in (5, 15, 25, 29):
            self.idle_for(session, minutes=minutes)
            self.assertEqual(client.get(ME).status_code, 200)
        self.idle_for(session, minutes=30)

        self.assertEqual(client.get(ME).status_code, 401)

    def test_a_person_working_renews_the_session(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=25)

        self.assertEqual(client.get(ME, **ACTIVITY).status_code, 200)

        self.assertLess(self.last_activity_age(session), timedelta(seconds=5))
        self.idle_for(session, minutes=29)
        self.assertEqual(client.get(ME).status_code, 200)

    def test_activity_is_written_at_most_once_every_30_seconds(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, seconds=20)

        self.assertEqual(client.get(ME, **ACTIVITY).status_code, 200)

        self.assertGreaterEqual(self.last_activity_age(session), timedelta(seconds=20))

    def test_the_status_does_not_count_even_with_the_header(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=25)

        res = client.get(STATUS, **ACTIVITY)

        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(self.last_activity_age(session), timedelta(minutes=25))
        body = res.json()
        self.assertTrue(290 <= body['idle_remaining_s'] <= 300, body)
        self.assertEqual(body['warn_before_s'], 300)
        self.assertEqual(body['idle_timeout_s'], 1800)
        self.assertGreater(body['absolute_remaining_s'], 0)

    def test_keepalive_renews_and_says_how_long_is_left(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=26)

        res = client.post(KEEPALIVE)

        self.assertEqual(res.status_code, 200, res.content)
        self.assertLess(self.last_activity_age(session), timedelta(seconds=5))
        self.assertGreaterEqual(res.json()['idle_remaining_s'], 1795)

    def test_keepalive_does_not_bring_back_a_dead_session(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=30)

        self.assertEqual(client.post(KEEPALIVE).status_code, 401)
        self.assertGreaterEqual(self.last_activity_age(session), timedelta(minutes=30))

    def test_keepalive_and_status_need_a_session(self):
        self.assertEqual(APIClient().post(KEEPALIVE).status_code, 401)
        self.assertEqual(APIClient().get(STATUS).status_code, 401)

    def test_an_answer_says_how_long_the_session_has_left(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=10)

        res = client.get(ME)

        remaining = int(res['X-Session-Idle-Remaining'])
        self.assertTrue(1190 <= remaining <= 1200, remaining)

    def test_a_guest_answer_says_nothing_about_a_session(self):
        self.assertFalse(APIClient().get(PUBLIC).has_header('X-Session-Idle-Remaining'))


class ChannelSeparationTest(_Base):
    def test_a_native_token_does_not_open_the_cookie_channel(self):
        native = APIClient().post('/api/v1/auth/login/', {
            'email': self.user.email, 'password': PASSWORD}, format='json')
        self.assertEqual(native.status_code, 200, native.content)
        client = APIClient()
        client.cookies[ACCESS] = native.json()['access']

        self.assertEqual(client.get(ME).status_code, 401)

    def test_the_native_contract_is_unchanged_and_opens_no_session(self):
        native = APIClient().post('/api/v1/auth/login/', {
            'email': self.user.email, 'password': PASSWORD}, format='json')
        app = APIClient()
        app.credentials(HTTP_AUTHORIZATION=f'Bearer {native.json()["access"]}')

        self.assertEqual(app.get('/api/v1/auth/me/').status_code, 200)
        self.assertEqual(AuthSession.objects.count(), 0)
        renewed = APIClient().post('/api/v1/auth/refresh/', {'refresh': native.json()['refresh']}, format='json')
        self.assertEqual(renewed.status_code, 200, renewed.content)

    def test_a_cookie_token_without_a_session_is_refused_and_its_cookies_cleared(self):
        client = APIClient()
        client.cookies[ACCESS] = str(RefreshToken.for_user(self.user).access_token)

        res = client.get(ME)

        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get('code'), 'session_expired')
        self.assertCookiesCleared(res)

    def test_a_sid_less_cookie_on_a_public_view_is_a_guest(self):
        client = APIClient()
        client.cookies[ACCESS] = str(RefreshToken.for_user(self.user).access_token)

        res = client.get(PUBLIC)

        self.assertEqual(res.status_code, 200)
        self.assertCookiesCleared(res)

    def test_a_web_refresh_cannot_be_renewed_as_a_native_one(self):
        client = self.browser()

        res = APIClient().post('/api/v1/auth/refresh/', {'refresh': client.cookies[REFRESH].value}, format='json')

        self.assertEqual(res.status_code, 401)
        self.assertNotIn('access', res.json())

    def test_a_web_access_token_does_not_open_the_native_surface(self):
        client = self.browser()
        app = APIClient()
        app.credentials(HTTP_AUTHORIZATION=f'Bearer {client.cookies[ACCESS].value}')

        self.assertEqual(app.get('/api/v1/auth/me/').status_code, 401)
        self.assertEqual(app.get('/api/v1/internal/no-existe/context/').status_code, 401)

    def test_a_token_naming_somebody_elses_session_is_refused(self):
        other = User.objects.create_user('otra', 'otra@example.test', PASSWORD)
        theirs = self.session(self.browser(other))
        forged = RefreshToken.for_user(self.user)
        forged['sid'] = str(theirs.sid)
        client = APIClient()
        client.cookies[ACCESS] = str(forged.access_token)

        self.assertEqual(client.get(ME).status_code, 401)

    def test_a_session_that_does_not_exist_is_refused(self):
        forged = RefreshToken.for_user(self.user)
        forged['sid'] = str(uuid.uuid4())
        client = APIClient()
        client.cookies[ACCESS] = str(forged.access_token)

        self.assertEqual(client.get(ME).status_code, 401)

    def test_the_web_session_of_an_internal_route_is_the_same_rule(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.get('/api/v1/internal/no-existe/context/')

        self.assertEqual(res.status_code, 401)


class EndOfSessionTest(_Base):
    def test_logout_ends_the_session_and_its_rotated_tokens_die_too(self):
        client = self.browser()
        session = self.session(client)
        first_access = client.cookies[ACCESS].value
        self.assertEqual(client.post(REFRESH_URL).status_code, 200)

        self.assertEqual(client.post(LOGOUT).status_code, 200)

        session.refresh_from_db()
        self.assertEqual(session.end_reason, AuthSession.END_LOGOUT)
        # Never revoked by `jti` (it was not the one presented at logout), and
        # still within its lifetime: the session is what kills it.
        stale = APIClient()
        stale.cookies[ACCESS] = first_access
        self.assertEqual(stale.get(ME).status_code, 401)

    def test_logout_with_only_the_refresh_cookie_still_ends_the_session(self):
        client = self.browser()
        session = self.session(client)
        bare = APIClient()
        bare.cookies[REFRESH] = client.cookies[REFRESH].value

        self.assertEqual(bare.post(LOGOUT).status_code, 200)

        session.refresh_from_db()
        self.assertEqual(session.end_reason, AuthSession.END_LOGOUT)

    def test_changing_the_password_ends_every_session(self):
        laptop, phone = self.browser(), self.browser()

        res = laptop.post('/api/auth/change-password/', {
            'current_password': PASSWORD, 'new_password': 'OtraClave456!'}, format='json')

        self.assertEqual(res.status_code, 200, res.content)
        reasons = set(AuthSession.objects.values_list('end_reason', flat=True))
        self.assertEqual(reasons, {AuthSession.END_PASSWORD})
        self.assertEqual(phone.get(ME).status_code, 401)

    def test_revoking_everything_ends_every_session(self):
        self.browser()
        self.browser()

        revoke_all_tokens(self.user)

        self.assertFalse(AuthSession.objects.filter(ended_at__isnull=True).exists())

    def test_a_deactivated_account_loses_its_session(self):
        client = self.browser()
        session = self.session(client)
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        self.assertEqual(client.get(ME).status_code, 401)

        session.refresh_from_db()
        self.assertEqual(session.end_reason, AuthSession.END_DEACTIVATED)

    def test_a_deactivated_account_cannot_renew(self):
        client = self.browser()
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        self.assertEqual(client.post(REFRESH_URL).status_code, 401)


class MultipleSessionsTest(_Base):
    def test_two_devices_are_two_sessions_that_expire_on_their_own(self):
        laptop, phone = self.browser(), self.browser()
        self.assertNotEqual(self.session(laptop).sid, self.session(phone).sid)

        self.idle_for(self.session(laptop), minutes=30)

        self.assertEqual(laptop.get(ME).status_code, 401)
        self.assertEqual(phone.get(ME).status_code, 200)

    def test_logging_out_one_device_leaves_the_other(self):
        laptop, phone = self.browser(), self.browser()

        laptop.post(LOGOUT)

        self.assertEqual(phone.get(ME).status_code, 200)

    def test_two_tabs_share_one_session(self):
        browser = self.browser()
        tab_a, tab_b = APIClient(), APIClient()
        for tab in (tab_a, tab_b):
            tab.cookies[ACCESS] = browser.cookies[ACCESS].value
        session = self.session(browser)
        self.idle_for(session, minutes=25)

        tab_a.get(ME, **ACTIVITY)
        self.idle_for(session, minutes=29)  # not real time: tab B reads the same row

        self.assertEqual(tab_b.get(ME).status_code, 200)

    def test_operators_of_two_companies_have_separate_sessions(self):
        a = Company.objects.create(name='A', slug='emp-a', legal_name='A S.A.C.', tax_id='20000000004')
        b = Company.objects.create(name='B', slug='emp-b', legal_name='B S.A.C.', tax_id='20000000005')
        other = User.objects.create_user('operadora', 'op@example.test', PASSWORD)
        Membership.objects.create(user=self.user, company=a, role='sales')
        Membership.objects.create(user=other, company=b, role='sales')
        mine, theirs = self.browser(), self.browser(other)

        mine.post(LOGOUT)

        self.assertEqual(theirs.get(ME).status_code, 200)
        self.assertEqual(self.session(theirs).user, other)


class AbsoluteLimitTest(_Base):
    def staff(self):
        company = Company.objects.create(name='S', slug='emp-s', legal_name='S S.A.C.', tax_id='20000000006')
        Membership.objects.create(user=self.user, company=company, role='sales')

    def test_a_staff_session_ends_8_hours_after_login(self):
        self.staff()
        session = self.session(self.browser())

        self.assertEqual(session.kind, AuthSession.KIND_STAFF)
        self.assertEqual(session.absolute_expires_at - session.created_at, timedelta(hours=8))

    def test_platform_admins_and_legacy_internal_roles_are_staff(self):
        admin = User.objects.create_superuser('raiz', 'raiz@example.test', PASSWORD)
        legacy = User.objects.create_user('legado', 'legado@example.test', PASSWORD)
        UserProfile.objects.update_or_create(user=legacy, defaults={'role': UserProfile.ROLE_INVENTORY})

        self.assertEqual(self.session(self.browser(admin)).kind, AuthSession.KIND_STAFF)
        self.assertEqual(self.session(self.browser(legacy)).kind, AuthSession.KIND_STAFF)

    def test_a_customer_session_ends_7_days_after_login(self):
        session = self.session(self.browser())

        self.assertEqual(session.kind, AuthSession.KIND_CUSTOMER)
        self.assertEqual(session.absolute_expires_at - session.created_at, timedelta(days=7))

    def test_activity_does_not_carry_a_session_past_its_limit(self):
        self.staff()
        client = self.browser()
        session = self.session(client)
        AuthSession.objects.filter(pk=session.pk).update(absolute_expires_at=Now() - timedelta(seconds=1))

        self.assertEqual(client.get(ME, **ACTIVITY).status_code, 401)

        session.refresh_from_db()
        self.assertEqual(session.end_reason, AuthSession.END_ABSOLUTE)

    def test_the_cookies_do_not_outlive_the_session(self):
        self.staff()
        res = APIClient().post(LOGIN, {'username': self.user.username, 'password': PASSWORD}, format='json')

        self.assertEqual(res.cookies[REFRESH]['max-age'], 8 * 3600)
        self.assertEqual(res.cookies[ACCESS]['max-age'], 1800)


class SingleIssuerTest(_Base):
    def test_a_password_login_opens_a_session(self):
        self.browser()

        session = AuthSession.objects.get()
        self.assertEqual((session.user, session.login_method, session.channel),
                         (self.user, AuthSession.METHOD_PASSWORD, AuthSession.CHANNEL_WEB))

    def test_no_code_writes_session_cookies_outside_the_issuer(self):
        """Every web login goes through `auth_sessions`: a cookie without a `sid` is a dead session."""
        store = Path(settings.BASE_DIR) / 'store'
        writers = []
        for path in store.rglob('*.py'):
            if path.name.startswith('test') or 'migrations' in path.parts:
                continue
            text = path.read_text(encoding='utf-8')
            if re.search(r'set_cookie\(\s*settings\.JWT_COOKIE_(ACCESS|REFRESH)_NAME', text) or \
                    re.search(r'^(?!def ).*_set_auth_cookies\(', text, re.MULTILINE):
                writers.append(path.relative_to(store).as_posix())

        self.assertEqual(sorted(writers), ['auth_sessions.py'])


class CheckoutExpiryTest(_Base):
    def test_a_dead_session_never_places_an_order_on_the_account(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.post('/api/payments/create-checkout-session/', {}, format='json')

        self.assertNotEqual(res.status_code, 200)
        self.assertFalse(Order.objects.filter(user=self.user).exists())


class FiscalExpiryTest(_Base):
    def test_a_dead_session_reaches_no_fiscal_action(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.post('/api/admin/orders/999999/fiscal-document/', {}, format='json')

        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get('code'), 'session_expired')


class ReviewFollowUpTest(_Base):
    """The cases the independent review of the ADR asked for (#180)."""

    def test_signing_in_over_a_dead_sessions_cookie_works(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.post(LOGIN, {'username': self.user.username, 'password': PASSWORD}, format='json')

        self.assertEqual(res.status_code, 200, res.content)
        self.assertGreater(res.cookies[ACCESS]['max-age'], 0)  # the new session's, not deleted
        self.assertEqual(AuthSession.objects.filter(ended_at__isnull=True).count(), 1)

    def test_the_csrf_cookie_is_handed_out_over_a_dead_sessions_cookie(self):
        client = APIClient()
        client.cookies[ACCESS] = 'no-es-un-token'

        res = client.get(PUBLIC)

        self.assertEqual(res.status_code, 200)
        self.assertIn('csrftoken', res.cookies)

    def test_the_cart_serves_a_dead_session_as_a_guest(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.post('/api/cart/', {'session_key': 'x' * 32}, format='json')

        self.assertNotEqual(res.status_code, 401)

    def test_the_checkout_refuses_a_dead_session(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.post('/api/payments/create-checkout-session/', {}, format='json')

        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get('code'), 'session_expired')
        self.assertCookiesCleared(res)

    def test_logout_with_a_rotated_away_refresh_still_ends_the_session(self):
        client = self.browser()
        session = self.session(client)
        old_refresh = client.cookies[REFRESH].value
        self.assertEqual(client.post(REFRESH_URL).status_code, 200)
        stale = APIClient()
        stale.cookies[REFRESH] = old_refresh  # blacklisted by the rotation

        self.assertEqual(stale.post(LOGOUT).status_code, 200)

        session.refresh_from_db()
        self.assertEqual(session.end_reason, AuthSession.END_LOGOUT)
        self.assertEqual(client.post(REFRESH_URL).status_code, 401)

    def test_logout_with_an_expired_access_still_ends_the_session(self):
        client = self.browser()
        session = self.session(client)
        expired = AccessToken(client.cookies[ACCESS].value)
        expired.set_exp(lifetime=timedelta(seconds=-60))
        bare = APIClient()
        bare.cookies[ACCESS] = str(expired)

        self.assertEqual(bare.post(LOGOUT).status_code, 200)

        session.refresh_from_db()
        self.assertEqual(session.end_reason, AuthSession.END_LOGOUT)

    def test_a_forged_sid_on_logout_ends_nothing(self):
        client = self.browser()
        session = self.session(client)
        forged = RefreshToken.for_user(self.user)
        forged['sid'] = str(session.sid)
        tampered = str(forged)[:-4] + 'AAAA'
        bare = APIClient()
        bare.cookies[REFRESH] = tampered

        bare.post(LOGOUT)

        session.refresh_from_db()
        self.assertIsNone(session.ended_at)

    def test_the_activity_header_of_a_get_without_the_csrf_token_is_ignored(self):
        client = self.browser()
        session = self.session(client)
        strict = APIClient(enforce_csrf_checks=True)
        strict.cookies[ACCESS] = client.cookies[ACCESS].value
        self.idle_for(session, minutes=10)

        self.assertEqual(strict.get(ME, **ACTIVITY).status_code, 200)

        self.assertGreaterEqual(self.last_activity_age(session), timedelta(minutes=10))

    def test_the_activity_header_of_a_get_with_the_csrf_token_counts(self):
        client = self.browser()
        session = self.session(client)
        strict = APIClient(enforce_csrf_checks=True)
        strict.get(PUBLIC)
        strict.cookies[ACCESS] = client.cookies[ACCESS].value
        self.idle_for(session, minutes=10)

        res = strict.get(ME, HTTP_X_CSRFTOKEN=strict.cookies['csrftoken'].value, **ACTIVITY)

        self.assertEqual(res.status_code, 200)
        self.assertLess(self.last_activity_age(session), timedelta(seconds=5))

    def test_only_an_answer_that_succeeded_is_activity(self):
        client = self.browser()
        session = self.session(client)
        self.idle_for(session, minutes=10)

        res = client.get('/api/admin/orders/999999/', **ACTIVITY)

        self.assertGreaterEqual(res.status_code, 400)
        self.assertGreaterEqual(self.last_activity_age(session), timedelta(minutes=10))

    def test_the_header_means_nothing_on_the_renewal_logout_and_status_routes(self):
        from store import auth_sessions

        self.assertEqual(
            sorted(auth_sessions.never_activity_paths()),
            sorted([REFRESH_URL, LOGOUT, STATUS]),
        )

    def test_no_other_site_may_send_the_activity_header(self):
        from corsheaders.defaults import default_headers

        allowed = [h.lower() for h in getattr(settings, 'CORS_ALLOW_HEADERS', default_headers)]
        self.assertNotIn('x-session-activity', allowed)

    def test_a_dead_session_writes_nothing_in_the_inventory(self):
        from store.models import StockMovement

        client = self.browser()
        self.idle_for(self.session(client), minutes=30)
        before = StockMovement.objects.count()

        res = client.post('/api/admin/products/1/inventory-adjust/', {'delta': 5}, format='json')

        self.assertEqual(res.status_code, 401)
        self.assertEqual(StockMovement.objects.count(), before)

    def test_the_session_cannot_be_read_is_a_refusal_not_a_pass(self):
        from unittest import mock

        from django.db import DatabaseError

        client = self.browser()
        client.raise_request_exception = False
        with mock.patch('store.auth_sessions.load', side_effect=DatabaseError('caída')):
            res = client.get(ME)

        self.assertNotEqual(res.status_code, 200)

    def test_a_legacy_bridge_operator_is_staff(self):
        from store import auth_sessions, tenancy

        Company.objects.create(name='Piloto', slug='piloto', legal_name='Piloto S.A.C.', tax_id='20000000009')
        operator = User.objects.create_user('pre_saas', 'pre@example.test', PASSWORD)
        UserProfile.objects.filter(user=operator).update(role=UserProfile.ROLE_SALES)
        operator.refresh_from_db()
        self.assertIsNotNone(tenancy.legacy_catalog_company(operator))

        self.assertTrue(auth_sessions.is_internal_account(operator))
        self.assertEqual(self.session(self.browser(operator)).kind, AuthSession.KIND_STAFF)

    def test_becoming_a_legacy_operator_hardens_open_sessions_at_once(self):
        session = self.session(self.browser())

        profile = UserProfile.objects.get(user=self.user)
        profile.role = UserProfile.ROLE_INVENTORY
        profile.save()

        session.refresh_from_db()
        self.assertEqual(session.kind, AuthSession.KIND_STAFF)


class SettingsGuardTest(TestCase):
    """Production may make a session SHORTER, never longer (settings.py, AUTH-B)."""

    def _load(self, **env):
        import os
        import subprocess
        import sys

        script = 'import django, os; os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings"); django.setup()'
        base = {'PATH': os.environ['PATH'], 'DEBUG': 'False', 'SECRET_KEY': 'x' * 60,
                'ALLOWED_HOSTS': 'tienda.example.pe', 'CORS_ALLOWED_ORIGINS': 'https://tienda.example.pe',
                'DATABASE_URL': 'postgres://u:p@localhost:5432/x', 'FRONTEND_URL': 'https://tienda.example.pe',
                'CHECKOUT_RETURN_URL': 'https://tienda.example.pe',
                'EMAIL_BACKEND': 'django.core.mail.backends.console.EmailBackend',
                'APP_CONFIG_ENCRYPTION_KEY': 'Tm9Fc1JlYWxOb0VzUmVhbE5vRXNSZWFsTm9Fc1JlYWw=',
                'PYTHONPATH': str(Path(settings.BASE_DIR))}
        return subprocess.run([sys.executable, '-c', script], cwd=settings.BASE_DIR, capture_output=True,
                              text=True, env={**base, **env}, timeout=60)

    def test_the_defaults_load_in_production(self):
        result = self._load()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_a_longer_idle_limit_is_refused_in_production(self):
        result = self._load(AUTH_SESSION_IDLE_SECONDS='3600')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('30 minutes idle', result.stderr)

    def test_a_coarser_activity_granularity_is_refused_in_production(self):
        result = self._load(AUTH_SESSION_ACTIVITY_GRANULARITY='60')
        self.assertNotEqual(result.returncode, 0)

    def test_a_shorter_limit_is_allowed(self):
        result = self._load(AUTH_SESSION_IDLE_SECONDS='900')
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])


class TouchGuardTest(_Base):
    """`touch` on its own: the request may have been judged alive a moment before it died."""

    def test_a_late_touch_does_not_bring_back_an_idle_session(self):
        from store import auth_sessions

        session = self.session(self.browser())
        self.idle_for(session, minutes=31)

        self.assertFalse(auth_sessions.touch(session))
        self.assertGreaterEqual(self.last_activity_age(session), timedelta(minutes=31))

    def test_a_late_touch_does_not_carry_a_session_past_its_limit(self):
        from store import auth_sessions

        session = self.session(self.browser())
        self.idle_for(session, minutes=5)
        AuthSession.objects.filter(pk=session.pk).update(absolute_expires_at=Now() - timedelta(seconds=1))

        self.assertFalse(auth_sessions.touch(session))

    def test_a_late_touch_does_not_bring_back_an_ended_session(self):
        from store import auth_sessions

        session = self.session(self.browser())
        self.idle_for(session, minutes=5)
        AuthSession.objects.filter(pk=session.pk).update(ended_at=Now(), end_reason=AuthSession.END_LOGOUT)

        self.assertFalse(auth_sessions.touch(session))

    def test_a_legacy_technician_without_membership_is_staff(self):
        from store import auth_sessions

        technician = User.objects.create_user('tecnico_legado', 'tec@example.test', PASSWORD)
        UserProfile.objects.filter(user=technician).update(role=UserProfile.ROLE_TECHNICIAN)
        technician.refresh_from_db()

        self.assertTrue(auth_sessions.is_internal_account(technician))


class WarningMomentTest(_Base):
    def test_the_warning_comes_in_the_last_sixth_of_the_idle_limit(self):
        from django.test import override_settings

        client = self.browser()
        with override_settings(AUTH_SESSION_IDLE_SECONDS=120, AUTH_SESSION_ACTIVITY_GRANULARITY=5):
            body = client.get(STATUS).json()

        self.assertEqual((body['idle_timeout_s'], body['warn_before_s']), (120, 20))


class SecondReviewTest(_Base):
    """The independent review of #181: P2-1 and the P3s."""

    def test_a_dead_cookie_on_a_guest_read_says_so_in_a_readable_header(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.get('/api/cart/', {'session_key': 'x' * 32})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['X-Session-Expired'], '1')
        self.assertCookiesCleared(res)

    def test_every_answer_to_a_dead_session_carries_the_header(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)
        cookies = {name: client.cookies[name].value for name in (ACCESS, REFRESH)}

        for method, url in (('get', PUBLIC), ('get', ME), ('post', REFRESH_URL), ('get', '/api/categories/')):
            again = APIClient()  # each one still holding the dead cookies
            for name, value in cookies.items():
                again.cookies[name] = value
            res = getattr(again, method)(url)
            self.assertEqual(res.get('X-Session-Expired'), '1', (url, res.status_code))

    def test_a_live_session_carries_no_such_header(self):
        client = self.browser()

        self.assertFalse(client.get(ME).has_header('X-Session-Expired'))

    def test_a_cross_origin_page_of_ours_may_read_the_session_headers(self):
        exposed = [h.lower() for h in getattr(settings, 'CORS_EXPOSE_HEADERS', [])]
        self.assertIn('x-session-expired', exposed)
        self.assertIn('x-session-idle-remaining', exposed)

    def test_activating_a_company_tightens_its_members_open_sessions_at_once(self):
        company = Company.objects.create(name='Dormida', slug='dormida', legal_name='Dormida S.A.C.',
                                         tax_id='20000000011', is_active=False)
        Membership.objects.create(user=self.user, company=company, role='sales')
        session = self.session(self.browser())
        self.assertEqual(session.kind, AuthSession.KIND_CUSTOMER)  # an inactive company opens no panel

        company.is_active = True
        company.save()

        session.refresh_from_db()
        self.assertEqual(session.kind, AuthSession.KIND_STAFF)
        self.assertEqual(session.absolute_expires_at - session.created_at, timedelta(hours=8))

    def test_a_failed_activity_write_never_turns_a_done_request_into_an_error(self):
        from unittest import mock

        from django.db import DatabaseError

        client = self.browser()
        self.idle_for(self.session(client), minutes=5)
        with mock.patch('store.auth_sessions.touch', side_effect=DatabaseError('caída')):
            res = client.get(ME, **ACTIVITY)

        self.assertEqual(res.status_code, 200)

    def test_an_expired_access_of_a_live_session_is_a_guest_on_the_ways_in(self):
        client = self.browser()
        expired = AccessToken(client.cookies[ACCESS].value)
        expired.set_exp(lifetime=timedelta(seconds=-60))
        client.cookies[ACCESS] = str(expired)

        csrf = client.get(PUBLIC)
        renewed = client.post(REFRESH_URL)
        signed_in = client.post(LOGIN, {'username': self.user.username, 'password': PASSWORD}, format='json')

        self.assertEqual(csrf.status_code, 200)
        self.assertNotIn(ACCESS, csrf.cookies)  # a live session keeps its cookies
        self.assertEqual(renewed.status_code, 200, renewed.content)
        self.assertEqual(signed_in.status_code, 200, signed_in.content)

    def test_the_private_csrf_api_the_activity_check_relies_on_is_still_there(self):
        """`_passes_csrf` uses Django internals; a Django upgrade that moves them must fail HERE."""
        from django.middleware.csrf import CsrfViewMiddleware, RejectRequest  # noqa: F401

        self.assertTrue(callable(getattr(CsrfViewMiddleware, '_check_token', None)))


class ThirdReviewTest(_Base):
    """#181 review at 8ca9002: signing in over a session."""

    def test_signing_in_over_a_live_session_ends_the_one_the_browser_had(self):
        client = self.browser()
        first = self.session(client)

        res = client.post(LOGIN, {'username': self.user.username, 'password': PASSWORD}, format='json')

        self.assertEqual(res.status_code, 200)
        first.refresh_from_db()
        self.assertEqual(first.end_reason, AuthSession.END_LOGOUT)
        self.assertEqual(AuthSession.objects.filter(ended_at__isnull=True).count(), 1)

    def test_signing_in_as_somebody_else_ends_the_previous_persons_session(self):
        other = User.objects.create_user('segunda', 'segunda@example.test', PASSWORD)
        client = self.browser()
        theirs = self.session(client)

        client.post(LOGIN, {'username': other.username, 'password': PASSWORD}, format='json')

        theirs.refresh_from_db()
        self.assertIsNotNone(theirs.ended_at)
        self.assertEqual(self.session(client).user, other)

    def test_a_sign_in_over_a_dead_cookie_does_not_say_the_session_expired(self):
        client = self.browser()
        self.idle_for(self.session(client), minutes=30)

        res = client.post(LOGIN, {'username': self.user.username, 'password': PASSWORD}, format='json')

        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.has_header('X-Session-Expired'))
        self.assertGreater(res.cookies[ACCESS]['max-age'], 0)


class SameBrowserOnlyTest(_Base):
    """Owner, 2026-10-25: a sign-in ends the previous session of THIS browser, never another device's."""

    def test_a_password_sign_in_leaves_the_other_device_signed_in(self):
        laptop, phone = self.browser(), self.browser()
        theirs = self.session(phone)

        laptop.post(LOGIN, {'username': self.user.username, 'password': PASSWORD}, format='json')

        theirs.refresh_from_db()
        self.assertIsNone(theirs.ended_at)
        self.assertEqual(phone.get(ME).status_code, 200)
        self.assertEqual(laptop.get(ME).status_code, 200)


class GoogleSignInOverASessionTest(_Base):
    def setUp(self):
        super().setUp()
        from unittest import mock

        from store import google_identity
        from store.test_google_auth import CLIENT_ID, KEY

        self.client_id = CLIENT_ID
        patcher = mock.patch.object(google_identity, '_signing_key', return_value=KEY.public_key())
        patcher.start()
        self.addCleanup(patcher.stop)
        override = self.settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID)
        override.enable()
        self.addCleanup(override.disable)
        self.user.email = 'ana@example.com'
        self.user.save()
        from store.models import ExternalIdentity
        ExternalIdentity.objects.create(user=self.user, provider='google', subject='109876543210',
                                        email_at_link='ana@example.com')

    def google(self, client):
        import time

        import jwt as pyjwt

        from store.test_google_auth import KEY

        nonce = client.get('/api/auth/google/config/').json()['nonce']
        now = int(time.time())
        token = pyjwt.encode({
            'iss': 'https://accounts.google.com', 'aud': self.client_id, 'sub': '109876543210',
            'email': 'ana@example.com', 'email_verified': True, 'iat': now, 'exp': now + 600, 'nonce': nonce,
        }, KEY, algorithm='RS256', headers={'kid': 'prueba'})
        return client.post('/api/auth/google/', {'credential': token}, format='json')

    def test_a_google_sign_in_ends_this_browsers_previous_session(self):
        client = self.browser()
        first = self.session(client)

        res = self.google(client)

        self.assertEqual(res.status_code, 200, res.content)
        first.refresh_from_db()
        self.assertEqual(first.end_reason, AuthSession.END_LOGOUT)
        self.assertEqual(self.session(client).login_method, AuthSession.METHOD_GOOGLE)

    def test_a_google_sign_in_leaves_the_other_device_signed_in(self):
        laptop, phone = self.browser(), self.browser()

        self.google(laptop)

        self.assertIsNone(self.session(phone).ended_at)
        self.assertEqual(phone.get(ME).status_code, 200)
