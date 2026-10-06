"""
GOOGLE-AUTH — verifying a Google ID token, and nothing else.

The browser gets an ID token from Google Identity Services and hands it over.
This module answers one question about it: is this a token Google issued, for
THIS application, to a person whose e-mail Google verified, in a sign-in that
started on THIS site, that has not been used already?

    EVERYTHING IS CHECKED HERE. NOTHING THE BROWSER SAYS IS BELIEVED.

  * signature   RS256 only, against Google's published keys. The algorithm is
                pinned: a token that names `none` or an HMAC is refused before
                any key is fetched.
  * audience    `GOOGLE_OAUTH_CLIENT_ID`. A valid Google token minted for some
                other site is a valid token for that other site.
  * issuer      accounts.google.com.
  * expiry      with thirty seconds of clock tolerance.
  * nonce       a value this server signed minutes ago AND set as an HttpOnly
                cookie in the browser that asked. The token must carry it and
                the browser must present it — so a token obtained elsewhere
                cannot be replayed into somebody else's browser.
  * one use     a nonce that already opened a session opens no second one.

Every refusal raises the same `GoogleIdentityError`, with no reason attached:
which check failed is nobody's business outside the server log.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass

import jwt
from django.conf import settings
from django.core import signing
from django.core.cache import cache

logger = logging.getLogger(__name__)

JWKS_URL = 'https://www.googleapis.com/oauth2/v3/certs'
ISSUERS = ('https://accounts.google.com', 'accounts.google.com')
NONCE_MAX_AGE = 600
NONCE_COOKIE = 'blackdog_gnonce'
_NONCE_SALT = 'store.google-identity.nonce'

_jwks_client = None

#: The id this integration has in the provider registry of the console.
CONSOLE_PROVIDER = 'google'


class GoogleIdentityError(Exception):
    """The token does not prove what it has to. Views answer 400, always the same."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    first_name: str
    last_name: str
    nonce: str


def client_id() -> str:
    """
    The OAuth client ID in force NOW, or '' when sign-in with Google is off.

    The console first (Configuración › Integraciones › Google), then
    `GOOGLE_OAUTH_CLIENT_ID` for an installation that has not used the console.
    Read at every request: changing it there needs no restart.
    """
    from .integrations import service

    config = service.resolve(CONSOLE_PROVIDER)
    return str(config.get('client_id') or '').strip() if config is not None else ''


def is_enabled() -> bool:
    return bool(client_id())


def issue_nonce() -> str:
    """A fresh, signed, time-stamped value for one sign-in attempt."""
    return signing.TimestampSigner(salt=_NONCE_SALT).sign(secrets.token_urlsafe(18))


def _nonce_is_ours(nonce: str) -> bool:
    try:
        signing.TimestampSigner(salt=_NONCE_SALT).unsign(nonce, max_age=NONCE_MAX_AGE)
    except (signing.BadSignature, TypeError):
        return False
    return True


def _nonce_key(nonce: str) -> str:
    return 'google-nonce:' + hashlib.sha256(nonce.encode()).hexdigest()


def consume_nonce(nonce: str) -> bool:
    """Mark the attempt as used. False when it already was: a replay."""
    return bool(cache.add(_nonce_key(nonce), 1, NONCE_MAX_AGE))


def _keys():
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600, timeout=5)
    return _jwks_client


def _signing_key(credential: str):
    """Google's public key for this token. With the one below, all that touches the network."""
    return _keys().get_signing_key_from_jwt(credential).key


def signing_keys_reachable() -> bool:
    """Whether this server can fetch the keys a sign-in is verified with."""
    try:
        return bool(_keys().get_signing_keys())
    except jwt.PyJWKClientError:
        return False


def verify(credential, *, browser_nonce: str) -> GoogleIdentity:
    """The person this token proves, or `GoogleIdentityError`."""
    audience = client_id()
    if not audience or not isinstance(credential, str) or credential.count('.') != 2:
        raise GoogleIdentityError()
    try:
        # Read the header BEFORE fetching any key: the algorithm is not the
        # token's to choose.
        if jwt.get_unverified_header(credential).get('alg') != 'RS256':
            raise GoogleIdentityError()
        claims = jwt.decode(
            credential, _signing_key(credential), algorithms=['RS256'],
            audience=audience, issuer=list(ISSUERS), leeway=30,
            options={'require': ['exp', 'iss', 'aud', 'sub']},
        )
    except GoogleIdentityError:
        raise
    except Exception as exc:  # noqa: BLE001 — any failure is the same refusal
        logger.info('token de Google rechazado (%s)', type(exc).__name__)
        raise GoogleIdentityError() from None

    nonce = claims.get('nonce')
    if (
        not isinstance(nonce, str) or not browser_nonce
        or not hmac.compare_digest(nonce.encode(), str(browser_nonce).encode())
        or not _nonce_is_ours(nonce)
    ):
        raise GoogleIdentityError()

    subject = claims.get('sub')
    email = claims.get('email')
    if not isinstance(subject, str) or not subject or not isinstance(email, str) or '@' not in email:
        raise GoogleIdentityError()
    # An unverified address is one anybody could have typed at Google.
    if claims.get('email_verified') not in (True, 'true'):
        raise GoogleIdentityError()

    return GoogleIdentity(
        subject=subject[:255], email=email.strip().lower()[:254],
        first_name=str(claims.get('given_name') or '')[:150],
        last_name=str(claims.get('family_name') or '')[:150],
        nonce=nonce,
    )
