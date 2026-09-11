"""
Authentication for the INTERNAL v1 surface — `/api/v1/internal/<company_slug>/…`.

WHY THIS EXISTS (docs/adr-auth-v1-internal.md)

The internal surface was built for native clients and accepted only
`Authorization: Bearer`. The web panel authenticates with HttpOnly cookies and
has no token it could send — nor should it: an access token readable by
JavaScript is exactly what the cookie design exists to prevent. So every web
screen that talks to this surface (technical service, evidence, notifications,
communications) answered 401 while the same person's session was valid.

The same surface now serves both clients, and it does so under ONE RULE:

    ONE REQUEST, ONE AUTHENTICATION CHANNEL.

      Authorization header present  →  the header channel, and only it.
      access cookie present         →  the cookie channel, and only it.
      both present                  →  401. No identity is chosen.
      neither                       →  anonymous; IsAuthenticated answers 401.

WHY NOT SIMPLY STACK THE TWO EXISTING CLASSES

DRF tries authenticators in order: the first result wins and the first
exception aborts. Executed against this codebase, neither order is safe:

    [Bearer, Cookie]  Bearer of A + cookie of B  → authenticated as A,
                      silently, and without CSRF on a mutation.
    [Cookie, Bearer]  INVALID Bearer + cookie    → falls back to the cookie.

A caller that presents a credential explicitly must get that credential
evaluated or refused — never a quiet switch to a different identity. Deciding
which of two identities "wins" is not a decision this surface should make.

WHAT THIS CLASS DOES NOT DO

It validates no JWT, reads no user, checks no CSRF and resolves no tenant. It
decides WHICH existing authenticator runs, and refuses when that is ambiguous:

  · `V1BearerAuthentication` keeps the native contract: no CSRF, one uniform
    error message.
  · `CookieJWTAuthentication` keeps the web contract: CSRF enforced inside
    `authenticate()` for every unsafe method. Its PermissionDenied (403) passes
    through untouched — a CSRF failure is not an authentication failure and is
    never turned into a 401.
  · Membership, capabilities and branch scope stay in V1InternalSurfaceMixin,
    which only ever reads `request.user` and therefore answers the same way
    whichever channel authenticated it.

⚠️  DECLARED ONLY BY V1InternalSurfaceMixin. Not global, not on `/api/admin/`,
not on `/api/v1/customer/`, `/api/v1/auth/` or `/api/v1/platform/`.
"""
from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from .authentication import CookieJWTAuthentication
from .v1_authentication import V1BearerAuthentication

# The native contract's wording, kept for both channels on this surface. "Token
# expired", "user inactive" and "user not found" are three facts about an
# account, and none of them is owed to a caller who failed to authenticate.
_INVALID = 'Credenciales inválidas.'

_BEARER = b'bearer'


class V1InternalAuthentication(BaseAuthentication):
    """Exactly one channel per request, chosen by what the caller PRESENTED."""

    def authenticate(self, request):
        # PRESENCE, decided before anything is validated. An explicit header —
        # even an empty or malformed one — is a statement from the caller about
        # how it wants to be authenticated, and it closes the cookie path.
        header_presented = 'HTTP_AUTHORIZATION' in request.META
        # An EMPTY cookie is not a credential: `CookieJWTAuthentication` already
        # treats it as absent, and this keeps that semantics.
        cookie_presented = bool(request.COOKIES.get(settings.JWT_COOKIE_ACCESS_NAME))

        if header_presented and cookie_presented:
            raise exceptions.AuthenticationFailed(_INVALID)

        if header_presented:
            return self._authenticate_header(request)

        if cookie_presented:
            try:
                return CookieJWTAuthentication().authenticate(request)
            except exceptions.AuthenticationFailed:
                # Only authentication failures are normalised. PermissionDenied
                # — the CSRF refusal — is deliberately not caught here.
                raise exceptions.AuthenticationFailed(_INVALID)

        return None

    def _authenticate_header(self, request):
        try:
            parts = get_authorization_header(request).split()
        except UnicodeError:
            raise exceptions.AuthenticationFailed(_INVALID)

        # `Basic`, `Token`, `Digest`, an empty header or garbage: all refused.
        # `V1BearerAuthentication` returns None for a foreign scheme so another
        # class may try — which is exactly the fallback this surface forbids.
        if not parts or parts[0].lower() != _BEARER:
            raise exceptions.AuthenticationFailed(_INVALID)

        result = V1BearerAuthentication().authenticate(request)
        if result is None:  # pragma: no cover — the scheme was checked above
            raise exceptions.AuthenticationFailed(_INVALID)
        return result

    def authenticate_header(self, request):
        """
        The v1 challenge, unchanged for native clients.

        It names the contract of this URL space, not a requirement on the web:
        the browser authenticates here by cookie and never reads this header.
        """
        return 'Bearer realm="api"'
