"""
Bearer authentication for the versioned NATIVE client surface — `/api/v1/`.

WHY A SECOND AUTHENTICATION CLASS EXISTS

`CookieJWTAuthentication` is right for the web frontend and wrong for a mobile
app. It reads an HttpOnly cookie and enforces CSRF, both of which exist because a
browser attaches cookies to requests the user did not initiate. A native app has
no cookie jar doing that on its behalf: it holds a token in memory and sends it
deliberately, so CSRF has nothing to protect against and the cookie plumbing is
pure friction.

The two are therefore kept apart rather than merged. Merging them would mean the
web surface starts accepting `Authorization: Bearer`, and that is exactly the
change nobody wants to make by accident.

⚠️  THIS CLASS IS NEVER GLOBAL.

It is NOT in `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES` and must not be
added there. Adding it would silently open every legacy endpoint — `/api/admin/`,
`/api/auth/me/`, every private web view — to a token minted for the mobile
contract. Each private v1 view declares it explicitly instead, so opting an
endpoint into Bearer is a visible line in a diff.
"""
from django.contrib.auth.models import User
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import AccessToken

# The only scheme accepted. Compared case-insensitively because RFC 7235 says the
# scheme is case-insensitive, but nothing else is tolerated: a `Basic` or `Token`
# header is a client talking a contract this surface does not speak.
_SCHEME = b'bearer'


class V1BearerAuthentication(BaseAuthentication):
    """
    Authenticate a native client from `Authorization: Bearer <access token>`.

    Returns `None` (rather than raising) when there is no Bearer header at all —
    DRF then treats the request as anonymous, which is what lets a single view
    serve both authenticated and public callers if it ever needs to.

    Raises `AuthenticationFailed` — a 401 — when a Bearer header IS present but
    unusable. A malformed token is a failed authentication, not an absent one.

    NO CSRF. There is no cookie here, so there is no cross-site request forgery
    to enforce against: an attacker's page cannot make a browser attach a token
    it never had.
    """

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header or header[0].lower() != _SCHEME:
            # Either no credentials, or credentials for a different contract.
            # Both mean "not authenticated by THIS class"; if another class is
            # declared on the view, it still gets its turn.
            return None

        if len(header) == 1:
            raise exceptions.AuthenticationFailed('Credenciales inválidas.')
        if len(header) > 2:
            # A token containing spaces is not a token this surface issued.
            raise exceptions.AuthenticationFailed('Credenciales inválidas.')

        try:
            raw_token = header[1].decode()
        except UnicodeError:
            raise exceptions.AuthenticationFailed('Credenciales inválidas.')

        return self.authenticate_token(raw_token)

    def authenticate_token(self, raw_token):
        """
        Validate one raw access token and resolve its user.

        Every failure answers with the SAME message. "Token expirado",
        "usuario inactivo" and "usuario borrado" are three different facts, and
        telling them apart to an unauthenticated caller narrates the state of an
        account to whoever is holding a stolen or guessed token.

        The token is never logged, never echoed, and never included in the error.
        """
        try:
            validated = AccessToken(raw_token)
        except TokenError:
            raise exceptions.AuthenticationFailed('Credenciales inválidas.')

        user_id = validated.get(jwt_settings.USER_ID_CLAIM)
        if user_id is None:
            raise exceptions.AuthenticationFailed('Credenciales inválidas.')

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed('Credenciales inválidas.')

        # In this installation an inactive user is either deactivated OR has
        # never verified their email — registration creates the account inactive.
        # Both must be refused, and refused identically.
        if not user.is_active:
            raise exceptions.AuthenticationFailed('Credenciales inválidas.')

        return (user, validated)

    def authenticate_header(self, request):
        """Non-None so DRF answers 401 rather than 403 for anonymous callers."""
        return 'Bearer realm="api"'
