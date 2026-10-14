"""
SOCIAL-AUTH-01 — «Continuar con Facebook»: who a Facebook login proves.

Authorization Code flow, server side, with PKCE (S256) and `state`:

  1. `authorize_url` sends the browser to Facebook's dialog with the state, the
     PKCE challenge and the callback registered for this site.
  2. The callback brings a code. `identify` redeems it ONCE, from this server,
     with the app secret and the PKCE verifier only this server knows.
  3. The user token is checked with `debug_token` — issued for THIS app, valid,
     and for which person — before `/me` (signed with `appsecret_proof`) is
     believed, and `/me` must be that same person.
  4. The user token is then dropped. Nothing of Facebook's is stored but the
     person's id.

    FACEBOOK'S E-MAIL IS NOT VERIFIED HERE.

The Graph API says nothing about whether the address was confirmed, so it never
becomes the account's address and never proves a mailbox: it is kept in
`email_at_link` and compared, so that an address that already has an account
asks to link instead of making a second account.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from urllib.parse import urlencode

import requests

from .social_identity import SocialAuthError, SocialIdentity, redirect_uri

logger = logging.getLogger(__name__)

ID = 'facebook'
CONSOLE_PROVIDER = ID
DIALOG = 'https://www.facebook.com'
GRAPH = 'https://graph.facebook.com'
DEFAULT_VERSION = 'v23.0'
VERSION = re.compile(r'^v\d{1,2}\.\d$')
SCOPE = 'public_profile,email'
TIMEOUT = 10


def config():
    """The configuration in force NOW (console), or None when Facebook is off."""
    from .integrations import service

    resolved = service.resolve(CONSOLE_PROVIDER)
    if resolved is None or not resolved.get('app_id') or not resolved.get('app_secret'):
        return None
    return resolved


def is_enabled() -> bool:
    return config() is not None


def version(conf) -> str:
    value = str(conf.get('graph_version') or '').strip()
    return value if VERSION.match(value) else DEFAULT_VERSION


def authorize_url(conf, begun) -> str:
    return f'{DIALOG}/{version(conf)}/dialog/oauth?' + urlencode({
        'client_id': conf.get('app_id'),
        'redirect_uri': redirect_uri(ID),
        'state': begun.state,
        'response_type': 'code',
        'scope': SCOPE,
        'code_challenge': begun.challenge,
        'code_challenge_method': 'S256',
    })


def _get(url: str, params: dict) -> dict:
    """One call to the Graph API. All the network this module touches."""
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
        body = response.json()
    except (requests.RequestException, ValueError):
        raise SocialAuthError() from None
    if not isinstance(body, dict):
        raise SocialAuthError()
    return body


def app_token_works(conf) -> bool:
    """Whether Facebook accepts this app id and secret (the console's test)."""
    body = _get(f'{GRAPH}/{version(conf)}/oauth/access_token', {
        'client_id': conf.get('app_id'), 'client_secret': conf.get('app_secret'),
        'grant_type': 'client_credentials',
    })
    return isinstance(body.get('access_token'), str) and bool(body['access_token'])


def _text(value, limit) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ''


def identify(conf, *, code, attempt) -> SocialIdentity:
    """The person this code proves, or SocialAuthError."""
    if not isinstance(code, str) or not code or len(code) > 4096:
        raise SocialAuthError()
    graph = f'{GRAPH}/{version(conf)}'
    app_id = str(conf.get('app_id'))
    secret = str(conf.get('app_secret'))

    exchanged = _get(f'{graph}/oauth/access_token', {
        'client_id': app_id, 'redirect_uri': redirect_uri(ID), 'client_secret': secret,
        'code': code, 'code_verifier': attempt.verifier,
    })
    access = exchanged.get('access_token')
    if not isinstance(access, str) or not access:
        logger.info('facebook: el código no se pudo canjear')
        raise SocialAuthError()

    debug = _get(f'{graph}/debug_token', {'input_token': access, 'access_token': f'{app_id}|{secret}'})
    data = debug.get('data')
    if (
        not isinstance(data, dict) or str(data.get('app_id')) != app_id
        or data.get('is_valid') is not True or not data.get('user_id')
    ):
        logger.info('facebook: token de usuario no válido para esta app')
        raise SocialAuthError()

    proof = hmac.new(secret.encode(), access.encode(), hashlib.sha256).hexdigest()
    me = _get(f'{graph}/me', {
        'fields': 'id,first_name,last_name,email', 'access_token': access, 'appsecret_proof': proof,
    })
    subject = me.get('id')
    if not isinstance(subject, str) or not subject or subject != str(data.get('user_id')):
        raise SocialAuthError()
    email = _text(me.get('email'), 254).lower()
    return SocialIdentity(
        provider=ID, subject=subject[:255], email='',
        email_seen=email if '@' in email else '',
        first_name=_text(me.get('first_name'), 150), last_name=_text(me.get('last_name'), 150),
    )
