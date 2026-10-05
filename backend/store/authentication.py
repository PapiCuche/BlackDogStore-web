from django.conf import settings
from django.contrib.auth.models import User
from django.http import QueryDict
from django.middleware.csrf import CsrfViewMiddleware
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import AccessToken

from .token_revocation import token_is_revoked


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
    """

    def authenticate(self, request):
        raw_token = request.COOKIES.get(settings.JWT_COOKIE_ACCESS_NAME)
        if not raw_token:  # None or empty string — both are invalid tokens
            return None

        try:
            validated_token = AccessToken(raw_token)
        except TokenError as exc:
            raise exceptions.AuthenticationFailed(str(exc))

        enforce_csrf(request)

        user_id = validated_token.get(jwt_settings.USER_ID_CLAIM)
        if user_id is None:
            raise exceptions.AuthenticationFailed('Token payload inválido.')

        try:
            # `select_related('profile')`: la comprobación de revocación lee el
            # sello del perfil, y sin esto costaría una consulta extra por
            # petición autenticada.
            user = User.objects.select_related('profile').get(pk=user_id)
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed('Usuario no encontrado.')

        if not user.is_active:
            raise exceptions.AuthenticationFailed('Usuario inactivo.')

        # H4.1.2B — AUTH-REVOCATION-01. Cerrar sesión o cambiar la contraseña
        # invalidaba el refresh y dejaba vivo el access token hasta media hora.
        if token_is_revoked(user, validated_token):
            raise exceptions.AuthenticationFailed('Credenciales inválidas.')

        return (user, validated_token)

    def authenticate_header(self, request):
        """Return a non-None value so DRF returns 401 (not 403) for unauthenticated requests."""
        return 'Cookie realm="blackdog"'

    def enforce_csrf(self, request):
        """Delegate to module-level enforce_csrf() (kept for DRF interface compatibility)."""
        enforce_csrf(request)
