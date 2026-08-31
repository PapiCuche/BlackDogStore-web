"""
Native authentication — `/api/v1/auth/`.

WHAT THIS IS

The session core for native clients: sign in, refresh, sign out, and read your
own identity. Tokens travel in the BODY, because an app holds them itself.

WHAT THIS IS NOT

Not a replacement for the web contract. `/api/auth/*` is untouched: it still
posts a username, still returns its JWTs in HttpOnly cookies, still enforces
CSRF. Both contracts mint SimpleJWT tokens from the same user table, and that is
the only thing they share.

Not account lifecycle. Registration, email verification, password reset and
change-password are NOT here — they remain web-only (BR-001B). A mobile client
must not present a form for a flow this contract cannot serve.

Not tenant authorization. See `available_companies` below.
"""
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from .tenancy import access_contexts, is_platform_admin, verified_company_relations
from .throttles import LoginThrottle
from .v1_auth_serializers import (
    V1AccessContextSerializer,
    V1CompanyRelationSerializer,
    V1LoginSerializer,
    V1LogoutSerializer,
    V1PlatformSerializer,
    V1RefreshSerializer,
    V1UserSerializer,
)
from .v1_authentication import V1BearerAuthentication

logger = logging.getLogger(__name__)

User = get_user_model()

# ONE message for every way signing in can fail. Wrong password, unknown email,
# deactivated account and unverified account are four different facts, and
# telling them apart lets anyone with a login form enumerate who has an account
# here. The server knows which happened; the caller does not.
INVALID_CREDENTIALS = 'Correo o contraseña incorrectos.'

# A hash to verify against when no user matched, so "unknown email" costs
# roughly the same as "wrong password". Without it, response time answers the
# question the shared error message refuses to.
_DUMMY_HASH = (
    'pbkdf2_sha256$1000000$0000000000000000000000$'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
)


def _identity_payload(user):
    """
    The identity half of every auth response — the same shape everywhere.

    `access_contexts` was ADDED in M4 alongside `available_companies`, not
    instead of it. A shipped client reads the older field and must keep working
    for as long as it is installed; removing it to tidy the payload would break
    every app in the field on the day the server deployed.

    The two answer different questions. `available_companies` says WHICH
    companies this person relates to. `access_contexts` says WHAT they may do
    there — and, crucially, keeps `customer` and `member` apart instead of
    flattening them into a role.
    """
    return {
        'user': V1UserSerializer(user).data,
        'available_companies': V1CompanyRelationSerializer(
            verified_company_relations(user), many=True,
        ).data,
        'access_contexts': V1AccessContextSerializer(access_contexts(user), many=True).data,
        'platform': V1PlatformSerializer({'is_master': is_platform_admin(user)}).data,
    }


def _access_lifetime_seconds():
    return int(jwt_settings.ACCESS_TOKEN_LIFETIME.total_seconds())


def _token_payload(refresh: RefreshToken):
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'expires_in': _access_lifetime_seconds(),
    }


def _resolve_user_by_email(email: str):
    """
    Find the single active-or-not user owning `email`, or None.

    EMAIL IS NOT UNIQUE IN THIS DATABASE. `AUTH_USER_MODEL` is Django's stock
    `User`, whose `email` column carries no unique constraint; registration
    checks for duplicates in the serializer, but that check has a race and says
    nothing about rows created before it existed, by `createsuperuser`, or in the
    admin.

    Adding a unique constraint now would be a migration that FAILS on any
    installation already holding a duplicate — discovered in production, during
    deploy. That is not a risk worth taking for a client convenience, so the
    ambiguity is handled here instead: more than one match is treated as
    unusable and answers exactly like an unknown email.

    Refusing is the safe direction. Picking "the first one" would let whoever
    registered a duplicate address log in as someone else.
    """
    matches = list(User.objects.filter(email__iexact=email)[:2])
    if len(matches) != 1:
        if len(matches) > 1:
            # Operational signal, not a user-facing one. No email, no password:
            # the count and nothing else.
            logger.warning(
                'v1 login: %d users share an email address; refusing to guess.', len(matches),
            )
        return None
    return matches[0]


class V1LoginView(APIView):
    """
    POST `{ "email", "password" }` → tokens in the BODY.

    No `Set-Cookie`. A native client stores its own credentials — the access
    token in memory, the refresh token in the platform keystore — and a cookie
    here would be a second, invisible copy of a credential nobody reads.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        serializer = V1LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        password = serializer.validated_data['password']

        user = _resolve_user_by_email(email)

        if user is None:
            # Spend the time anyway. See _DUMMY_HASH.
            check_password(password, _DUMMY_HASH)
            return Response(
                {'detail': INVALID_CREDENTIALS}, status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.check_password(password):
            return Response(
                {'detail': INVALID_CREDENTIALS}, status=status.HTTP_401_UNAUTHORIZED,
            )

        # Checked AFTER the password, so an attacker cannot learn that an
        # address exists by watching this branch answer faster.
        if not user.is_active:
            return Response(
                {'detail': INVALID_CREDENTIALS}, status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)
        return Response({**_token_payload(refresh), **_identity_payload(user)})


class V1RefreshView(APIView):
    """
    POST `{ "refresh" }` → a new access token and a ROTATED refresh token.

    `ROTATE_REFRESH_TOKENS` and `BLACKLIST_AFTER_ROTATION` are already on for the
    whole project, so the token that was sent is dead once this returns. The
    client must persist the new refresh token BEFORE using the new access token,
    or a crash in between leaves it holding a blacklisted credential.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = V1RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            token = RefreshToken(serializer.validated_data['refresh'])
        except TokenError:
            # Expired, malformed, blacklisted and forged all answer the same.
            # A 500 here would be a stack trace handed to an anonymous caller.
            return Response(
                {'detail': 'Sesión expirada.'}, status=status.HTTP_401_UNAUTHORIZED,
            )

        user_id = token.get(jwt_settings.USER_ID_CLAIM)
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {'detail': 'Sesión expirada.'}, status=status.HTTP_401_UNAUTHORIZED,
            )

        # A deactivated account must not be able to extend its session simply
        # because it was holding a valid refresh token when it was switched off.
        if not user.is_active:
            return Response(
                {'detail': 'Sesión expirada.'}, status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            token.blacklist()
        except AttributeError:  # pragma: no cover — blacklist app is installed
            pass

        return Response(_token_payload(RefreshToken.for_user(user)))


class V1MeView(APIView):
    """
    GET → the caller's own identity and verified company relations.

    This is what a cold start calls after refreshing: the app deliberately does
    not persist the profile, so there is exactly one place that says who is
    signed in, and it is the server.
    """

    authentication_classes = [V1BearerAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(_identity_payload(request.user))


class V1LogoutView(APIView):
    """
    POST `{ "refresh" }` → always 200.

    BEST EFFORT, BY DESIGN. The client clears its own credentials first and then
    tells the server; a phone that lost signal at the wrong moment must still end
    up signed out locally. So this never fails the caller:

      - no refresh token sent          → 200
      - expired, malformed, blacklisted → 200
      - a valid token                   → blacklisted, 200

    No access token is required. Requiring one would make it impossible to end a
    session precisely when the access token has expired, which is exactly when a
    user is most likely to give up and hit "sign out".

    The uniform answer is also what stops this becoming an oracle: a caller
    cannot probe which refresh tokens are still live by watching status codes.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = V1LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = (serializer.validated_data.get('refresh') or '').strip()

        if raw:
            try:
                RefreshToken(raw).blacklist()
            except (TokenError, AttributeError):
                # Already dead, or never valid. Either way the session is over,
                # which is the outcome the caller asked for.
                pass

        return Response({'detail': 'Sesión cerrada.'})
