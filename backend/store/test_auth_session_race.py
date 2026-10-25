"""
AUTH-B — what happens when two requests about one session arrive at once.

Real threads against real PostgreSQL, so the row lock is the real one. Each
test makes the dangerous interleaving happen on purpose: one request is held at
the instant it matters, with a timeout, while the other runs. If the code under
test serialises the two (as it must), the held request simply waits for the
timeout and the other one finds the result of the first.

  * Two renewals with the SAME refresh token: exactly one wins (H-6 of the ADR).
    SimpleJWT checks the blacklist and writes it without a lock, so without the
    session lock both used to rotate.
  * A renewal racing a logout: whatever wins, the tokens the renewal issued are
    dead once the logout is done.
  * Activity racing a logout: a late activity write never brings an ended
    session back.
"""
import threading
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.db.models.functions import Now
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import AccessToken

from store import auth_sessions
from store.models import AuthSession

PASSWORD = 'ValidPass123!'
ACCESS = settings.JWT_COOKIE_ACCESS_NAME
REFRESH = settings.JWT_COOKIE_REFRESH_NAME
HOLD = 1.5  # seconds a held request waits for the other one


def _login(username):
    client = APIClient()
    res = client.post('/api/auth/login/', {'username': username, 'password': PASSWORD}, format='json')
    assert res.status_code == 200, res.content
    return client


def _in_thread(target, results, key):
    def run():
        try:
            results[key] = target()
        finally:
            connection.close()
    thread = threading.Thread(target=run)
    thread.start()
    return thread


class _RaceBase(TransactionTestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = User.objects.create_user('carrera', 'carrera@example.test', PASSWORD)


class ConcurrentRefreshTest(_RaceBase):
    def test_the_same_refresh_token_renews_once(self):
        browser = _login('carrera')
        raw_refresh = browser.cookies[REFRESH].value
        both_inside = threading.Barrier(2, timeout=HOLD)
        real_validate = TokenRefreshSerializer.validate

        def held_validate(serializer, attrs):
            try:
                both_inside.wait()
            except threading.BrokenBarrierError:
                pass  # the other one is (correctly) waiting outside
            return real_validate(serializer, attrs)

        def renew():
            client = APIClient()
            client.cookies[REFRESH] = raw_refresh
            return client.post('/api/auth/refresh/').status_code

        results = {}
        with mock.patch.object(TokenRefreshSerializer, 'validate', held_validate):
            threads = [_in_thread(renew, results, n) for n in range(2)]
            for thread in threads:
                thread.join()

        self.assertEqual(sorted(results.values()), [200, 401], results)


class RefreshAgainstLogoutTest(_RaceBase):
    def test_tokens_issued_by_a_renewal_die_with_the_logout(self):
        browser = _login('carrera')
        renewal_started = threading.Event()
        logout_done = threading.Event()
        real_validate = TokenRefreshSerializer.validate

        def held_validate(serializer, attrs):
            renewal_started.set()
            logout_done.wait(HOLD)
            return real_validate(serializer, attrs)

        def renew():
            client = APIClient()
            client.cookies[REFRESH] = browser.cookies[REFRESH].value
            res = client.post('/api/auth/refresh/')
            return res.cookies[ACCESS].value if res.status_code == 200 else None

        def logout():
            renewal_started.wait(HOLD)
            client = APIClient()
            client.cookies[ACCESS] = browser.cookies[ACCESS].value
            client.cookies[REFRESH] = browser.cookies[REFRESH].value
            status = client.post('/api/auth/logout/').status_code
            logout_done.set()
            return status

        results = {}
        with mock.patch.object(TokenRefreshSerializer, 'validate', held_validate):
            threads = [_in_thread(renew, results, 'renew'), _in_thread(logout, results, 'logout')]
            for thread in threads:
                thread.join()

        self.assertEqual(results['logout'], 200)
        issued = results['renew']
        if issued is not None:
            late = APIClient()
            late.cookies[ACCESS] = issued
            self.assertEqual(late.get('/api/auth/me/').status_code, 401)
        self.assertEqual(AuthSession.objects.get().end_reason, AuthSession.END_LOGOUT)


class ActivityAgainstLogoutTest(_RaceBase):
    def test_a_late_activity_never_brings_an_ended_session_back(self):
        browser = _login('carrera')
        sid = AccessToken(browser.cookies[ACCESS].value)['sid']
        AuthSession.objects.filter(sid=sid).update(last_activity_at=Now() - timedelta(minutes=10))
        checked = threading.Event()
        logout_done = threading.Event()
        real_touch = auth_sessions.touch

        def held_touch(*args, **kwargs):
            checked.set()
            logout_done.wait(HOLD)
            return real_touch(*args, **kwargs)

        def work():
            client = APIClient()
            client.cookies[ACCESS] = browser.cookies[ACCESS].value
            return client.get('/api/auth/me/', HTTP_X_SESSION_ACTIVITY='1').status_code

        def logout():
            checked.wait(HOLD)
            client = APIClient()
            client.cookies[REFRESH] = browser.cookies[REFRESH].value
            status = client.post('/api/auth/logout/').status_code
            logout_done.set()
            return status

        results = {}
        with mock.patch.object(auth_sessions, 'touch', held_touch):
            threads = [_in_thread(work, results, 'work'), _in_thread(logout, results, 'logout')]
            for thread in threads:
                thread.join()

        session = AuthSession.objects.get(sid=sid)
        self.assertEqual(session.end_reason, AuthSession.END_LOGOUT)
        after = APIClient()
        after.cookies[ACCESS] = browser.cookies[ACCESS].value
        self.assertEqual(after.get('/api/auth/me/').status_code, 401)
