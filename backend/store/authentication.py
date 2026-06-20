from django.conf import settings
from django.contrib.auth.models import User
from django.middleware.csrf import CsrfViewMiddleware
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import AccessToken


class _CSRFCheck(CsrfViewMiddleware):
    """CsrfViewMiddleware subclass that returns the rejection reason instead of an HttpResponse."""
    def _reject(self, request, reason):
        return reason


class CookieJWTAuthentication(BaseAuthentication):
    """
    Authenticates via HttpOnly JWT access cookie (blackdog_access) instead of
    the Authorization: Bearer header. Enforces CSRF for all authenticated requests,
    mirroring DRF's SessionAuthentication pattern.
    """

    def authenticate(self, request):
        raw_token = request.COOKIES.get(settings.JWT_COOKIE_ACCESS_NAME)
        if raw_token is None:
            return None

        try:
            validated_token = AccessToken(raw_token)
        except TokenError as exc:
            raise exceptions.AuthenticationFailed(str(exc))

        self.enforce_csrf(request)

        user_id = validated_token.get(jwt_settings.USER_ID_CLAIM)
        if user_id is None:
            raise exceptions.AuthenticationFailed('Token payload inválido.')

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed('Usuario no encontrado.')

        if not user.is_active:
            raise exceptions.AuthenticationFailed('Usuario inactivo.')

        return (user, validated_token)

    def authenticate_header(self, request):
        """Return a non-None value so DRF returns 401 (not 403) for unauthenticated requests."""
        return 'Cookie realm="blackdog"'

    def enforce_csrf(self, request):
        """Enforce CSRF for cookie-authenticated requests (safe methods are skipped internally)."""
        def dummy_get_response(req):  # pragma: no cover
            return None

        check = _CSRFCheck(dummy_get_response)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise exceptions.PermissionDenied(f'CSRF Failed: {reason}')
