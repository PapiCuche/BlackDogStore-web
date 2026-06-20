from django.conf import settings
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer

from .auth_serializers import RegisterSerializer, UserSerializer
from .throttles import LoginThrottle, RegisterThrottle


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
    """Clears both JWT cookies, ending the session."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
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
        serializer = UserSerializer(request.user)
        return Response(serializer.data)
