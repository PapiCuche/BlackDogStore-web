"""
Web sessions that die after 30 minutes without real activity — AUTH-B (SESSION-IDLE-01).

Design and its alternatives: `docs/adr-auth-sessions.md` §2. In short:

    A SESSION IS A ROW, AND EVERY CREDENTIAL OF IT NAMES THE ROW.

Signing in creates an `AuthSession` and puts its `sid` in the refresh token,
BEFORE the access token is derived from it. SimpleJWT copies every claim of the
refresh into the access and keeps them through every rotation, so renewing is
never a new session. `CookieJWTAuthentication` reads the row on every request
and the row, not the token, says whether the session is alive.

    THE ONLY CLOCK IS THE DATABASE'S.

Timestamps are written with `Now()` and every limit is compared in SQL. No
time sent by a browser is believed, and with several servers there is still a
single clock.

    ACTIVITY IS DECLARED, BOUNDED AND NEVER A RENEWAL.

The browser marks a request it made because a PERSON did something with
`X-Session-Activity: 1` (`fetchWithAuth` decides, AUTH-C). The server honours
the mark only when the request also passes CSRF — a GET too — and only once the
answer turned out 2xx/3xx (`WebSessionMiddleware`). It writes at most once per
`AUTH_SESSION_ACTIVITY_GRANULARITY` seconds, never on the renewal, logout or
status routes, and never on a session that is already dead. With a granularity
G the session expires between `idle − G` and `idle` after the last real
activity: never later (ADR §2.5).

    A DEAD SESSION IS A GUEST, AND ITS COOKIES ARE DELETED.

Idle, past its absolute limit, logged out, password changed, account
deactivated, a token with no session, a cookie that is not a token: the
request goes on as a visitor (a public page still loads) and the answer deletes
both cookies. A WRITE is refused instead (401 `session_expired`), so nothing
the browser sent with an account's cookie is ever done as a stranger — except
on the few views that are themselves a way in (`dead_session_is_guest`), such
as signing in again. An access token that merely EXPIRED, of a session that is
still alive, is a 401 as before: renewing fixes it.

    STAFF 8 HOURS, CUSTOMERS 7 DAYS, FROM SIGNING IN.

Neither slides. A customer who becomes staff is tightened at once (signals) and
again at every renewal; nothing ever lengthens a session.

The native app (V1 Bearer) creates no rows and its contract is unchanged; a
token that names a web session is refused there, and a native token is refused
here (`sid` is the line between the two).
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import timedelta

import jwt
from django.conf import settings
from django.db.models import DateTimeField, ExpressionWrapper, F
from django.db.models.functions import Least, Now
from django.middleware.csrf import RejectRequest
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.tokens import RefreshToken

logger = logging.getLogger('store.security')

SID_CLAIM = 'sid'
#: What a client is told when its session is over, whatever the reason.
CODE = 'session_expired'
EXPIRED_DETAIL = 'Sesión expirada.'
ACTIVITY_META = 'HTTP_X_SESSION_ACTIVITY'
ACTIVITY_HEADER = 'X-Session-Activity'
REMAINING_HEADER = 'X-Session-Idle-Remaining'
#: On EVERY answer to a request that presented a session that is over — a guest
#: page included. The cookies it deletes are HttpOnly, so this is the browser's
#: only readable signal; the checkout stops on it instead of going on as a guest.
EXPIRED_HEADER = 'X-Session-Expired'
WARN_BEFORE_SECONDS = 300

_UNSAFE = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})
#: Routes where the activity mark means nothing, whatever the browser sends.
_NEVER_ACTIVITY = ('token_refresh', 'auth-logout', 'auth-session')


# -- limits --------------------------------------------------------------------

def idle_seconds() -> int:
    return int(settings.AUTH_SESSION_IDLE_SECONDS)


def granularity_seconds() -> int:
    return int(settings.AUTH_SESSION_ACTIVITY_GRANULARITY)


def staff_max_seconds() -> int:
    return int(settings.AUTH_STAFF_SESSION_MAX_SECONDS)


def customer_max_seconds() -> int:
    return int(settings.AUTH_CUSTOMER_SESSION_MAX_SECONDS)


def _ago(seconds):
    return Now() - timedelta(seconds=seconds)


# -- who is staff ----------------------------------------------------------------

def is_internal_account(user) -> bool:
    """
    Whether this account can work inside the panel, by any road it has.

    The union of every road the panel opens with: platform authority, an active
    membership of an active company, the legacy bridge of pre-SaaS operators,
    and any legacy staff role. Broader is the safe side here: it only ever
    SHORTENS a session.
    """
    from . import tenancy
    from .models import UserProfile
    from .permissions import get_user_role

    if user is None or not getattr(user, 'pk', None):
        return False
    if user.is_staff or user.is_superuser or tenancy.is_platform_admin(user):
        return True
    if tenancy.active_memberships(user).exists():
        return True
    if tenancy.legacy_catalog_company(user) is not None:
        return True
    return get_user_role(user) not in (None, UserProfile.ROLE_CUSTOMER)


# -- the request's state ---------------------------------------------------------

def _http(request):
    """The Django request under a DRF one, so the middleware sees the same object."""
    return getattr(request, '_request', request)


def mark_dead(request) -> None:
    """This request presented a session that is over: its answer deletes the cookies."""
    _http(request).bd_session_dead = True


def is_dead(request) -> bool:
    return bool(getattr(_http(request), 'bd_session_dead', False))


def current(request):
    """The live `AuthSession` this request authenticated with, or None."""
    return getattr(_http(request), 'bd_session', None)


def _route_name(request) -> str:
    match = getattr(_http(request), 'resolver_match', None)
    return getattr(match, 'url_name', '') or ''


def _passes_csrf(request) -> bool:
    """
    Whether the request carries a valid CSRF token, asked of a GET too.

    The activity mark is only believed from our own pages: a page on another
    site can make a browser send a GET with its cookies, but not with the CSRF
    token, which it cannot read.
    """
    from .authentication import _CSRFCheck, _WithoutForm

    # PRIVATE DJANGO API: `CsrfViewMiddleware._check_token` and `RejectRequest`
    # (Django 4.1+). Django exempts safe methods from CSRF, and the public path
    # (`process_view`) would always say «fine» for a GET. Pinned by
    # `SecondReviewTest.test_the_private_csrf_api_…`: recheck on every Django
    # upgrade (docs/adr-auth-sessions.md §2.10).
    if getattr(_http(request), '_dont_enforce_csrf_checks', False):
        return True
    check = _CSRFCheck(lambda req: None)
    wrapped = _WithoutForm(request)
    check.process_request(wrapped)
    try:
        check._check_token(wrapped)
    except RejectRequest:
        return False
    return True


def wants_activity(request) -> bool:
    """Whether this request, if it succeeds, counts as a person's activity."""
    if _http(request).META.get(ACTIVITY_META) != '1':
        return False
    if _route_name(request) in _NEVER_ACTIVITY:
        return False
    # An unsafe method already passed CSRF in the authentication itself.
    return request.method in _UNSAFE or _passes_csrf(request)


def attach(request, session) -> None:
    http = _http(request)
    http.bd_session = session
    http.bd_activity = wants_activity(request)


# -- tokens ----------------------------------------------------------------------

def signed_payload(raw, token_type: str):
    """
    The claims of a token THIS server signed, even if it expired, or None.

    For questions about a session — «is it alive?», «which one do I end?» — the
    expiry of one credential does not matter, the signature does. The blacklist
    is not consulted either: a rotated-away refresh still names its session.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        payload = jwt.decode(
            raw, token_backend.get_verifying_key(raw), algorithms=[token_backend.algorithm],
            options={'verify_exp': False, 'verify_aud': False},
        )
    except Exception:  # noqa: BLE001 — anything but a valid signature is «not ours»
        return None
    if payload.get(jwt_settings.TOKEN_TYPE_CLAIM) != token_type:
        return None
    return payload


# -- reading and judging a session -----------------------------------------------

def _as_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def load(sid, *, lock=False):
    """The row a `sid` names, with the database's `now` beside it, or None."""
    from .models import AuthSession

    sid = _as_uuid(sid)
    if sid is None:
        return None
    rows = AuthSession.objects.annotate(db_now=Now()).filter(sid=sid)
    if lock:
        rows = rows.select_for_update(of=('self',))
    else:
        rows = rows.select_related('user__profile')
    return rows.first()


def verdict(session, user_id):
    """
    None when the session is alive for this user; otherwise why it is not.

    `END_*` reasons are worth recording on the row; '' means there is no row of
    this user to record anything on.
    """
    from .models import AuthSession

    if session is None or str(session.user_id) != str(user_id) or session.channel != AuthSession.CHANNEL_WEB:
        return ''
    if session.ended_at is not None:
        return ''
    if session.db_now >= session.absolute_expires_at:
        return AuthSession.END_ABSOLUTE
    if session.db_now - session.last_activity_at >= timedelta(seconds=idle_seconds()):
        return AuthSession.END_IDLE
    return None


def is_alive(payload) -> bool:
    """For a signed but expired access token: is its session still alive? Ends it if not."""
    session = load(payload.get(SID_CLAIM))
    reason = verdict(session, payload.get(jwt_settings.USER_ID_CLAIM))
    if reason:
        end(session, reason)
    return reason is None


def remaining(session, *, renewed=False) -> int:
    """Whole seconds of inactivity this session has left, as of its read."""
    idle = timedelta(seconds=idle_seconds())
    left = idle if renewed else idle - (session.db_now - session.last_activity_at)
    left = min(left, session.absolute_expires_at - session.db_now)
    return max(0, int(left.total_seconds()))


def status(session, *, renewed=False) -> dict:
    return {
        'idle_remaining_s': remaining(session, renewed=renewed),
        'absolute_remaining_s': max(0, int((session.absolute_expires_at - session.db_now).total_seconds())),
        'idle_timeout_s': idle_seconds(),
        'warn_before_s': WARN_BEFORE_SECONDS,
    }


# -- writing ---------------------------------------------------------------------

def start(user, *, method):
    """Open a session for `user` and return `(session, refresh token carrying its sid)`."""
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

    from .models import AuthSession

    staff = is_internal_account(user)
    cap = staff_max_seconds() if staff else customer_max_seconds()
    session = AuthSession.objects.create(
        sid=uuid.uuid4(), user=user, login_method=method,
        kind=AuthSession.KIND_STAFF if staff else AuthSession.KIND_CUSTOMER,
        absolute_expires_at=ExpressionWrapper(Now() + timedelta(seconds=cap), output_field=DateTimeField()),
    )
    session = AuthSession.objects.annotate(db_now=Now()).get(pk=session.pk)

    refresh = RefreshToken.for_user(user)
    # Set BEFORE anything is derived from it: the access token copies it.
    refresh[SID_CLAIM] = str(session.sid)
    OutstandingToken.objects.filter(jti=refresh[jwt_settings.JTI_CLAIM]).update(token=str(refresh))
    return session, refresh


def touch(session) -> bool:
    """
    Record a person's activity. True when the row was written.

    Conditional, in one statement: only a live session, only if its last mark
    is older than the granularity, never past its idle or absolute limit. Two at
    once write once; a late one cannot bring an ended session back.
    """
    from .models import AuthSession

    return bool(
        AuthSession.objects.filter(
            pk=session.pk, ended_at__isnull=True,
            last_activity_at__lt=_ago(granularity_seconds()),
            last_activity_at__gt=_ago(idle_seconds()),
            absolute_expires_at__gt=Now(),
        ).update(last_activity_at=Now())
    )


def end(session, reason) -> None:
    from .models import AuthSession

    if session is None:
        return
    AuthSession.objects.filter(pk=session.pk, ended_at__isnull=True).update(ended_at=Now(), end_reason=reason)
    logger.info('session_ended user_id=%s reason=%s', session.user_id, reason)


def end_sid(sid, user_id, reason) -> None:
    from .models import AuthSession

    sid = _as_uuid(sid)
    if sid is None or user_id is None:
        return
    ended = AuthSession.objects.filter(sid=sid, user_id=user_id, ended_at__isnull=True).update(
        ended_at=Now(), end_reason=reason,
    )
    if ended:
        logger.info('session_ended user_id=%s reason=%s', user_id, reason)


def end_all(user, reason) -> None:
    from .models import AuthSession

    if user is None or not getattr(user, 'pk', None):
        return
    ended = AuthSession.objects.filter(user=user, ended_at__isnull=True).update(ended_at=Now(), end_reason=reason)
    if ended:
        logger.info('sessions_ended user_id=%s reason=%s count=%s', user.pk, reason, ended)


def harden(user) -> None:
    """Every open session of `user` becomes staff, capped 8 h after it began. Never lengthens."""
    if user is None or not getattr(user, 'pk', None):
        return
    harden_users([user.pk])


def harden_users(user_ids) -> None:
    """`harden`, for several accounts at once (a company switched on)."""
    from .models import AuthSession

    AuthSession.objects.filter(user_id__in=list(user_ids), ended_at__isnull=True).update(
        kind=AuthSession.KIND_STAFF,
        absolute_expires_at=Least(
            F('absolute_expires_at'),
            ExpressionWrapper(F('created_at') + timedelta(seconds=staff_max_seconds()),
                              output_field=DateTimeField()),
        ),
    )


# -- cookies ---------------------------------------------------------------------

def _cookie_base():
    return {
        'httponly': settings.JWT_COOKIE_HTTPONLY,
        'samesite': settings.JWT_COOKIE_SAMESITE,
        'secure': settings.JWT_COOKIE_SECURE,
        'path': '/',
    }


def set_cookies(response, session, access_token, refresh_token=None) -> None:
    """Write the session's cookies; neither outlives the session's absolute limit."""
    from .models import AuthSession

    cap = staff_max_seconds() if session.kind == AuthSession.KIND_STAFF else customer_max_seconds()
    left = math.ceil((session.absolute_expires_at - timezone.now()).total_seconds())
    ceiling = max(0, min(cap, left))
    response.set_cookie(
        settings.JWT_COOKIE_ACCESS_NAME, access_token,
        max_age=min(int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()), ceiling),
        **_cookie_base(),
    )
    if refresh_token is not None:
        response.set_cookie(
            settings.JWT_COOKIE_REFRESH_NAME, refresh_token,
            max_age=min(int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()), ceiling),
            **_cookie_base(),
        )


def delete_cookies(response) -> None:
    for name in (settings.JWT_COOKIE_ACCESS_NAME, settings.JWT_COOKIE_REFRESH_NAME):
        response.delete_cookie(name, path='/', samesite=settings.JWT_COOKIE_SAMESITE)


def issue_web_session(response, user, *, method):
    """THE way a web sign-in ends: a session row, its sid in the tokens, the cookies."""
    session, refresh = start(user, method=method)
    set_cookies(response, session, str(refresh.access_token), str(refresh))
    return session


def expired_response(request):
    """The answer to a session that is over. Its cookies go with it (middleware)."""
    from rest_framework.response import Response

    mark_dead(request)
    return Response({'detail': EXPIRED_DETAIL, 'code': CODE}, status=401)


# -- framework glue ----------------------------------------------------------------

def exception_handler(exc, context):
    """DRF's, plus `code: session_expired` on a 401 that a dead session caused."""
    from rest_framework.views import exception_handler as drf_exception_handler

    response = drf_exception_handler(exc, context)
    request = context.get('request')
    if (
        response is not None and response.status_code == 401 and request is not None
        and is_dead(request) and isinstance(response.data, dict)
    ):
        response.data['code'] = CODE
    return response


class WebSessionMiddleware:
    """
    After the view: record activity (only for an answer that succeeded), say how
    long the session has left, and delete the cookies of a dead one.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        session = getattr(request, 'bd_session', None)
        if session is not None:
            renewed = bool(getattr(request, 'bd_activity_done', False))
            if getattr(request, 'bd_activity', False) and 200 <= response.status_code < 400 and not renewed:
                # The request is done and committed: failing to record the
                # activity must not turn its answer into an error. The session
                # simply expires on time, which is the safe side.
                try:
                    renewed = touch(session)
                except Exception:  # noqa: BLE001
                    logger.exception('session activity could not be recorded (user_id=%s)', session.user_id)
            response[REMAINING_HEADER] = str(remaining(session, renewed=renewed))
        if getattr(request, 'bd_session_dead', False):
            response[EXPIRED_HEADER] = '1'
            for name in (settings.JWT_COOKIE_ACCESS_NAME, settings.JWT_COOKIE_REFRESH_NAME):
                # A sign-in answered in this same response wins over the dead cookie.
                if name not in response.cookies:
                    response.delete_cookie(name, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        return response


def never_activity_paths() -> list:
    """For tests and docs: the routes where the activity mark is ignored."""
    return [reverse(name) for name in _NEVER_ACTIVITY]
