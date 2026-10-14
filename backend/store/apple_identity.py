"""
SOCIAL-AUTH-01 — «Continuar con Apple»: who a Sign in with Apple proves.

The web flow with `response_mode=form_post`:

  1. `authorize_url` sends the browser to Apple with `state` and `nonce`.
  2. Apple POSTs `code` (and, the first time only, the person's name) to the
     callback. That POST only hands the code over to the attempt; the browser
     is sent on to a GET of the callback, which is where the attempt is taken
     out (see `social_identity.hand_over`).
  3. `identify` redeems the code ONCE at Apple's token endpoint, authenticated
     with a client secret that is an ES256 JWT signed with the team's key.
  4. The `id_token` THAT endpoint returns is verified: RS256 only, against
     Apple's published keys, issuer appleid.apple.com, audience = our Services
     ID, not expired, and carrying the nonce of this attempt. The id_token that
     came in the browser's POST is never read.

No PKCE: Apple does not document it for this flow. State, nonce and the
signed client secret do its job.

    THE E-MAIL MAY BE A RELAY, AND MAY BE MISSING.

Apple names an address the first time only, and it may be a private relay
(…@privaterelay.appleid.com) that forwards to the person. It is used — as the
account's contact address, verified — only when `email_verified` says so. The
account works without one; checkout asks for an address as it always has.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import time
from urllib.parse import urlencode

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .social_identity import SocialAuthError, SocialIdentity, redirect_uri

logger = logging.getLogger(__name__)

ID = 'apple'
CONSOLE_PROVIDER = ID
ISSUER = 'https://appleid.apple.com'
AUTHORIZE_URL = 'https://appleid.apple.com/auth/authorize'
TOKEN_URL = 'https://appleid.apple.com/auth/token'
JWKS_URL = 'https://appleid.apple.com/auth/keys'
SCOPE = 'name email'
TIMEOUT = 10
CLIENT_SECRET_TTL = 300
TEN_CHARACTERS = re.compile(r'^[A-Z0-9]{10}$')

_jwks_client = None


def config():
    """The configuration in force NOW (console), or None when Apple is off."""
    from .integrations import service

    resolved = service.resolve(CONSOLE_PROVIDER)
    if resolved is None or not all(
        resolved.get(name) for name in ('services_id', 'team_id', 'key_id', 'private_key')
    ):
        return None
    return resolved


def is_enabled() -> bool:
    return config() is not None


def authorize_url(conf, begun) -> str:
    return AUTHORIZE_URL + '?' + urlencode({
        'client_id': conf.get('services_id'),
        'redirect_uri': redirect_uri(ID),
        'response_type': 'code',
        'scope': SCOPE,
        'response_mode': 'form_post',
        'state': begun.state,
        'nonce': begun.nonce,
    })


def load_private_key(pem):
    """The team's key (.p8) as a P-256 key, or ValueError."""
    try:
        key = serialization.load_pem_private_key(str(pem).strip().encode(), password=None)
    except (TypeError, ValueError):
        raise ValueError('No es una clave privada PEM (.p8).') from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('La clave de Sign in with Apple es de curva P-256 (ES256).')
    return key


def client_secret(conf, *, now=None) -> str:
    """The short-lived ES256 JWT Apple takes as a client secret."""
    issued = int(now if now is not None else time.time())
    return jwt.encode(
        {'iss': conf.get('team_id'), 'iat': issued, 'exp': issued + CLIENT_SECRET_TTL,
         'aud': ISSUER, 'sub': conf.get('services_id')},
        load_private_key(conf.get('private_key')), algorithm='ES256',
        headers={'kid': conf.get('key_id')},
    )


def _post(url: str, data: dict) -> dict:
    """One call to Apple's token endpoint. With `_signing_key`, all the network here."""
    try:
        response = requests.post(url, data=data, timeout=TIMEOUT, headers={'Accept': 'application/json'})
        body = response.json()
    except (requests.RequestException, ValueError):
        raise SocialAuthError() from None
    if not isinstance(body, dict):
        raise SocialAuthError()
    return body


def _keys():
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600, timeout=5)
    return _jwks_client


def _signing_key(token: str):
    return _keys().get_signing_key_from_jwt(token).key


def signing_keys_reachable() -> bool:
    try:
        return bool(_keys().get_signing_keys())
    except jwt.PyJWKClientError:
        return False


def verify(token, *, audience: str, nonce: str) -> dict:
    """The claims of an Apple id_token for this app and this attempt, or SocialAuthError."""
    if not audience or not isinstance(token, str) or token.count('.') != 2:
        raise SocialAuthError()
    try:
        # The algorithm is not the token's to choose: read before any key is fetched.
        if jwt.get_unverified_header(token).get('alg') != 'RS256':
            raise SocialAuthError()
        claims = jwt.decode(
            token, _signing_key(token), algorithms=['RS256'], audience=audience, issuer=ISSUER,
            leeway=30, options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']},
        )
    except SocialAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 — every failure is the same refusal
        logger.info('id_token de Apple rechazado (%s)', type(exc).__name__)
        raise SocialAuthError() from None
    sent = claims.get('nonce')
    if not isinstance(sent, str) or not nonce or not hmac.compare_digest(sent.encode(), nonce.encode()):
        raise SocialAuthError()
    if not isinstance(claims.get('sub'), str) or not claims['sub']:
        raise SocialAuthError()
    return claims


def _name(posted) -> tuple[str, str]:
    """The name Apple posts on the first authorization only. Cosmetic; never identity."""
    try:
        user = json.loads((posted or {}).get('user') or '{}')
        name = user.get('name') or {}
        first, last = name.get('firstName'), name.get('lastName')
    except (TypeError, ValueError, AttributeError):
        return '', ''
    return (
        first.strip()[:150] if isinstance(first, str) else '',
        last.strip()[:150] if isinstance(last, str) else '',
    )


def identify(conf, *, code, attempt) -> SocialIdentity:
    """The person this code proves, or SocialAuthError."""
    if not isinstance(code, str) or not code or len(code) > 4096:
        raise SocialAuthError()
    tokens = _post(TOKEN_URL, {
        'client_id': conf.get('services_id'), 'client_secret': client_secret(conf),
        'code': code, 'grant_type': 'authorization_code', 'redirect_uri': redirect_uri(ID),
    })
    claims = verify(tokens.get('id_token'), audience=str(conf.get('services_id')), nonce=attempt.nonce)

    email = claims.get('email')
    email = email.strip().lower()[:254] if isinstance(email, str) and '@' in email else ''
    verified = claims.get('email_verified') in (True, 'true')
    first, last = _name(attempt.posted)
    return SocialIdentity(
        provider=ID, subject=claims['sub'][:255],
        email=email if verified else '', email_seen=email if verified else '',
        first_name=first, last_name=last,
    )
