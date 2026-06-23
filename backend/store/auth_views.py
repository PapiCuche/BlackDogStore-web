import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .auth_serializers import (
    RegisterSerializer, UserSerializer,
    VerifyEmailSerializer, ResendVerificationSerializer,
    PasswordResetRequestSerializer, PasswordResetConfirmSerializer,
    ChangePasswordSerializer,
)
from .authentication import enforce_csrf
from .emails import send_verification_email, send_password_reset_email
from .models import AccountToken
from .permissions import get_user_role
from .throttles import (
    LoginThrottle, RegisterThrottle,
    ResendVerificationThrottle, PasswordResetRequestThrottle,
    PasswordResetConfirmThrottle, ChangePasswordThrottle,
)

logger = logging.getLogger(__name__)


def _set_auth_cookies(response, access_token, refresh_token=None):
    """Write JWT tokens to HttpOnly cookies on a DRF Response object."""
    base = {
        'httponly': settings.JWT_COOKIE_HTTPONLY,
        'samesite': settings.JWT_COOKIE_SAMESITE,
        'secure': settings.JWT_COOKIE_SECURE,
        'path': '/',
    }
    response.set_cookie(
        settings.JWT_COOKIE_ACCESS_NAME,
        access_token,
        max_age=int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
        **base,
    )
    if refresh_token is not None:
        response.set_cookie(
            settings.JWT_COOKIE_REFRESH_NAME,
            refresh_token,
            max_age=int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()),
            **base,
        )


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        if settings.REQUIRE_EMAIL_VERIFICATION:
            user.is_active = False
            user.save(update_fields=['is_active'])
            raw_token, _ = AccountToken.make(user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
            send_verification_email(user, raw_token)
            return Response(
                {
                    'detail': 'Registro completado. Revisa tu correo para verificar tu cuenta.',
                    'requires_verification': True,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {
                'detail': 'Registro completado.',
                'requires_verification': False,
                'user': UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """Validates credentials and sets JWT tokens in HttpOnly cookies (not response body)."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        serializer = TokenObtainPairSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0])

        data = serializer.validated_data
        response = Response({
            'detail': 'Login correcto.',
            'user': UserSerializer(serializer.user).data,
        })
        _set_auth_cookies(response, data['access'], data['refresh'])
        return response


class RefreshView(APIView):
    """Reads the refresh cookie, issues a new access cookie (and rotated refresh if enabled)."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        refresh_cookie = request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        if not refresh_cookie:
            return Response(
                {'detail': 'No se encontró el refresh token.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = TokenRefreshSerializer(data={'refresh': refresh_cookie})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0])

        data = serializer.validated_data
        response = Response({'detail': 'Token renovado.'})
        _set_auth_cookies(
            response,
            data['access'],
            data.get('refresh'),  # present only when ROTATE_REFRESH_TOKENS=True
        )
        return response


class LogoutView(APIView):
    """
    Clears both JWT cookies, ending the session.

    authentication_classes = [] so that CookieJWTAuthentication never runs
    for this endpoint.  This allows logout to succeed even when the access
    token has already expired or is otherwise invalid.  CSRF is enforced
    manually in post() instead.

    CSRF enforcement: if any auth cookie is present in the request, a valid
    X-CSRFToken header is required.  This prevents logout-CSRF attacks where
    an attacker forces an authenticated user to log out.  When no auth cookies
    are present (already logged out or expired) the request is allowed through
    unconditionally so the cookie cleanup still runs.

    Blacklisting: the refresh token is blacklisted before clearing cookies so
    it cannot be reused after logout, even within its remaining TTL.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        has_auth_cookie = bool(
            request.COOKIES.get(settings.JWT_COOKIE_ACCESS_NAME) or
            request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        )
        if has_auth_cookie:
            enforce_csrf(request)  # raises PermissionDenied → 403 on failure

        # Blacklist the refresh token so it cannot be reused after logout.
        # Failures (expired, invalid, already blacklisted) are silenced — cookie
        # clearing must always succeed regardless of token state.
        refresh_cookie = request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        if refresh_cookie:
            try:
                RefreshToken(refresh_cookie).blacklist()
            except TokenError:
                pass

        response = Response({'detail': 'Sesión cerrada.'})
        response.delete_cookie(settings.JWT_COOKIE_ACCESS_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        response.delete_cookie(settings.JWT_COOKIE_REFRESH_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        return response


class CsrfView(APIView):
    """Sets the csrftoken cookie so the frontend can read it for X-CSRFToken headers."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        get_token(request)  # populates csrftoken cookie (not HttpOnly)
        return Response({'detail': 'CSRF cookie configurado.'})


class UserDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        data = UserSerializer(user).data
        data['role'] = get_user_role(user)
        data['is_staff'] = user.is_staff or user.is_superuser
        return Response(data)


class VerifyEmailView(APIView):
    """
    POST with {token} — activates the user account associated with the email verification token.
    The token is single-use and expires in 24 hours.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data['token']

        try:
            account_token = AccountToken.consume(raw_token, AccountToken.PURPOSE_EMAIL_VERIFICATION)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = account_token.user
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=['is_active'])

        return Response({'detail': 'Correo verificado correctamente. Ya puedes iniciar sesión.'})


class ResendVerificationView(APIView):
    """
    POST with {email} — resends the verification email.
    Always returns a generic message (anti-enumeration).
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ResendVerificationThrottle]

    _GENERIC_RESPONSE = {'detail': 'Si el correo existe y no está verificado, recibirás un nuevo enlace.'}

    def post(self, request):
        serializer = ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email__iexact=email, is_active=False)
        except User.DoesNotExist:
            return Response(self._GENERIC_RESPONSE)

        raw_token, _ = AccountToken.make(user, AccountToken.PURPOSE_EMAIL_VERIFICATION, ttl_hours=24)
        send_verification_email(user, raw_token)
        return Response(self._GENERIC_RESPONSE)


class PasswordResetRequestView(APIView):
    """
    POST with {email} — sends a password reset link to the user's email.
    Always returns a generic message (anti-enumeration).
    Only sends to active users (inactive = not yet verified).
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetRequestThrottle]

    _GENERIC_RESPONSE = {'detail': 'Si el correo existe, enviaremos instrucciones para restablecer tu contraseña.'}

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            return Response(self._GENERIC_RESPONSE)

        raw_token, _ = AccountToken.make(user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        send_password_reset_email(user, raw_token)
        return Response(self._GENERIC_RESPONSE)


class PasswordResetConfirmView(APIView):
    """
    POST with {token, new_password} — validates the reset token, changes the password,
    blacklists any active refresh token, and clears auth cookies.
    Does not auto-login after reset.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetConfirmThrottle]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        try:
            account_token = AccountToken.consume(raw_token, AccountToken.PURPOSE_PASSWORD_RESET)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = account_token.user
        user.set_password(new_password)
        user.save(update_fields=['password'])

        # Blacklist any active refresh token from this session
        refresh_cookie = request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        if refresh_cookie:
            try:
                RefreshToken(refresh_cookie).blacklist()
            except TokenError:
                pass

        response = Response({'detail': 'Contraseña restablecida. Inicia sesión con tu nueva contraseña.'})
        response.delete_cookie(settings.JWT_COOKIE_ACCESS_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        response.delete_cookie(settings.JWT_COOKIE_REFRESH_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        return response


class ChangePasswordView(APIView):
    """
    POST with {current_password, new_password} — changes the authenticated user's password.
    Requires valid access cookie + CSRF (enforced automatically by CookieJWTAuthentication).
    Blacklists the current refresh token and clears cookies — user must re-login.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ChangePasswordThrottle]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        current_password = serializer.validated_data['current_password']
        new_password = serializer.validated_data['new_password']

        if not request.user.check_password(current_password):
            return Response(
                {'detail': 'Contraseña actual incorrecta.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.user.set_password(new_password)
        request.user.save(update_fields=['password'])

        # Blacklist the current refresh token — all sessions are invalidated
        refresh_cookie = request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        if refresh_cookie:
            try:
                RefreshToken(refresh_cookie).blacklist()
            except TokenError:
                pass

        response = Response({'detail': 'Contraseña cambiada. Por favor, inicia sesión nuevamente.'})
        response.delete_cookie(settings.JWT_COOKIE_ACCESS_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        response.delete_cookie(settings.JWT_COOKIE_REFRESH_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        return response
