from django.conf import settings
from django.http import QueryDict
from django.middleware.csrf import CsrfViewMiddleware
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import AccessToken

from . import auth_sessions
from .models import AuthSession
from .token_revocation import token_is_revoked

_UNSAFE = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})


class _CSRFCheck(CsrfViewMiddleware):
    """CsrfViewMiddleware subclass that returns the rejection reason instead of an HttpResponse."""
    def _reject(self, request, reason):
        return reason


class _WithoutForm:
    """
    The request, minus its form (CSRF-BODY-READ).

    Django looks for the CSRF token in the FORM before it looks at the header,
    and looking at the form means parsing the whole body: a multipart upload is
    written to a temporary file. Here that ran right after the cookie's signature
    was checked and before any permission, so any signed-in account — a
    customer's too — could make the server receive and store an upload on a
    route it would then be refused on, and hold a thread while it arrived.

    This API takes the token from the `X-CSRFToken` header only. Everything else
    is the real request: the middleware reads and writes through.
    """

    __slots__ = ('_request',)

    def __init__(self, request):
        object.__setattr__(self, '_request', request)

    def __getattr__(self, name):
        return getattr(self._request, name)

    def __setattr__(self, name, value):
        setattr(self._request, name, value)

    @property
    def POST(self):
        return QueryDict()


def enforce_csrf(request):
    """
    Run a CSRF check on *request* and raise PermissionDenied if it fails.

    Reusable by any view that needs CSRF enforcement outside of the normal
    CookieJWTAuthentication flow (e.g. LogoutView).  Safe methods (GET, HEAD,
    OPTIONS, TRACE) are automatically skipped by the underlying middleware.

    The token is read from the header and never from the body: see `_WithoutForm`.
    """
    def dummy_get_response(req):  # pragma: no cover
        return None

    check = _CSRFCheck(dummy_get_response)
    request = _WithoutForm(request)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise exceptions.PermissionDenied(f'CSRF Failed: {reason}')


class CookieJWTAuthentication(BaseAuthentication):
    """
    Authenticates via HttpOnly JWT access cookie (blackdog_access) instead of
    the Authorization: Bearer header. Enforces CSRF for all authenticated requests,
    mirroring DRF's SessionAuthentication pattern.

    AUTH-B (SESSION-IDLE-01). The token says WHO; the `AuthSession` row its `sid`
    names says whether the session is still alive (`store/auth_sessions.py`).
    The row is read with the user and the profile in one query, which replaces
    the user query this class already made.

    What an unusable cookie means, decided here and nowhere else:
      * an access token that merely expired, of a LIVE session → 401, so the
        browser renews (as it always did);
      * anything else — dead session, no session, revoked, inactive account, not
        a token at all → the request is a GUEST and its answer deletes both
        cookies. A write is refused instead (401 `session_expired`), unless the
        view is itself a way in (`dead_session_is_guest`).
    """

    def authenticate(self, request):
        raw_token = request.COOKIES.get(settings.JWT_COOKIE_ACCESS_NAME)
        if not raw_token:  # None or empty string — both are invalid tokens
            return None

        try:
            validated_token = AccessToken(raw_token)
        except TokenError as exc:
            payload = auth_sessions.signed_payload(raw_token, 'access')
            if payload is not None and auth_sessions.is_alive(payload):
                # A live session whose access token merely expired. On a way in
                # (sign in, CSRF, renewal) it is a guest — the browser must be
                # able to renew or sign in — and keeps its cookies. Elsewhere a
                # 401, so the browser renews and is recognised.
                if getattr(self._view(request), 'dead_session_is_guest', False):
                    return None
                raise exceptions.AuthenticationFailed(str(exc))
            return self._stale(request)

        enforce_csrf(request)

        user_id = validated_token.get(jwt_settings.USER_ID_CLAIM)
        if user_id is None:
            return self._stale(request)

        # A token with no `sid` names no web session: a native (V1) token, or one
        # issued before AUTH-B. Either way it is not a session of this channel.
        session = auth_sessions.load(validated_token.get(auth_sessions.SID_CLAIM))
        reason = auth_sessions.verdict(session, user_id)
        if reason is not None:
            if reason:
                auth_sessions.end(session, reason)
            return self._stale(request)

        user = session.user
        if not user.is_active:
            auth_sessions.end(session, AuthSession.END_DEACTIVATED)
            return self._stale(request)

        # H4.1.2B — AUTH-REVOCATION-01. Cerrar sesión o cambiar la contraseña
        # invalidaba el refresh y dejaba vivo el access token hasta media hora.
        if token_is_revoked(user, validated_token):
            return self._stale(request)

        auth_sessions.attach(request, session)
        return (user, validated_token)

    @staticmethod
    def _view(request):
        return (getattr(request, 'parser_context', None) or {}).get('view')

    def _stale(self, request):
        """A cookie that will never authenticate again: guest, cookies deleted — or 401 for a write."""
        auth_sessions.mark_dead(request)
        view = self._view(request)
        if request.method in _UNSAFE and not getattr(view, 'dead_session_is_guest', False):
            raise exceptions.AuthenticationFailed('Credenciales inválidas.')
        return None

    def authenticate_header(self, request):
        """Return a non-None value so DRF returns 401 (not 403) for unauthenticated requests."""
        return 'Cookie realm="blackdog"'

    def enforce_csrf(self, request):
        """Delegate to module-level enforce_csrf() (kept for DRF interface compatibility)."""
        enforce_csrf(request)
