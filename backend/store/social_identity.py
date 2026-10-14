"""
SOCIAL-AUTH-01 — what «Continuar con Google / Facebook / Apple» share.

Google came first (`google_identity`, `google_views`) and does not change: an
ID token from Google Identity Services, verified here, with its own endpoints.
Facebook and Apple are REDIRECT flows: the browser goes to the provider and
comes back to a callback with a code that only this server can redeem. This
module is the part of those flows that is not about either provider.

    THE ATTEMPT LIVES ON THE SERVER, IS USED ONCE AND BELONGS TO ONE BROWSER.

`begin` mints a `state`, a `nonce` and a PKCE verifier and keeps them in the
cache under the state's hash for ten minutes. The browser gets the state (it
travels to the provider and back) and, in an HttpOnly cookie, a separate
binding secret whose hash the attempt remembers. `finish` takes the attempt
out exactly once, and only for the browser holding that cookie: a callback URL
somebody else started — login CSRF — or one replayed is refused before any
code is sent anywhere.

    A NEW IDENTITY NEVER ATTACHES ITSELF TO AN ACCOUNT THAT ALREADY EXISTS.

The identity is provider + the provider's stable subject id. When the address a
provider names already has an account here, the answer is `link_required`: sign
in with that account as usual and link the provider from «Mi cuenta», with the
password. Google keeps its own owner-approved road (409 + password, DEC-GOOGLE-01).

    FACEBOOK AND APPLE OPEN BUYER ACCOUNTS, AND NOTHING ELSE.

An account that is staff, superuser, holds an internal role or has ever had a
`Membership` cannot be entered through them, and nothing they do creates any of
those. (Google is outside this rule: invited workers already sign in with it.)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Every provider an account can be linked to, in the order screens show them.
PROVIDERS = ('google', 'facebook', 'apple')
LABELS = {'google': 'Google', 'facebook': 'Facebook', 'apple': 'Apple'}
#: The ones that go through `begin` / `finish` (Google has its own flow).
REDIRECT_PROVIDERS = ('facebook', 'apple')

ATTEMPT_COOKIE = 'blackdog_social'
ATTEMPT_COOKIE_PATH = '/api/auth/'
ATTEMPT_TTL = 600

INTENT_SIGN_IN = 'sign_in'
INTENT_LINK = 'link'

PRIVATE_RELAY_DOMAIN = 'privaterelay.appleid.com'

#: What a callback answers, by name. The screen has a sentence for each.
REFUSED = 'refused'
CANCELLED = 'cancelled'
LINK_REQUIRED = 'link_required'
NOT_ALLOWED = 'not_allowed'
INACTIVE = 'inactive'
ALREADY_LINKED = 'already_linked'

_NEXT = re.compile(r'^/(?![/\\])[^\s\\]{0,500}$')


class SocialAuthError(Exception):
    """Something does not prove what it has to. Never says which check failed."""


@dataclass(frozen=True)
class SocialIdentity:
    provider: str
    subject: str
    #: An address the provider VOUCHES for. '' when it does not, or gave none.
    email: str = ''
    #: Whatever address the provider named, verified or not: kept for a person
    #: reading the link (`email_at_link`) and compared, never authenticated.
    email_seen: str = ''
    first_name: str = ''
    last_name: str = ''


@dataclass(frozen=True)
class Begun:
    state: str
    binding: str
    verifier: str
    nonce: str

    @property
    def challenge(self) -> str:
        return pkce_challenge(self.verifier)


@dataclass(frozen=True)
class Attempt:
    provider: str
    intent: str
    user_id: int | None
    next_path: str
    verifier: str
    nonce: str
    #: What the provider POSTed to the callback (Apple's form_post), or None.
    posted: dict | None = field(default=None)


# -- small pieces ------------------------------------------------------------------

def _now() -> float:
    return time.time()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()


def is_private_relay(email) -> bool:
    """An address Apple made up to forward to the person («Ocultar mi correo»)."""
    return isinstance(email, str) and email.strip().lower().endswith('@' + PRIVATE_RELAY_DOMAIN)


def safe_next(raw) -> str:
    """A path on THIS site to land on afterwards, or ''. Never another origin."""
    if not isinstance(raw, str) or not _NEXT.match(raw):
        return ''
    return raw


def _frontend() -> str:
    return str(settings.FRONTEND_URL).rstrip('/')


def redirect_uri(provider_id: str) -> str:
    """
    The callback registered at the provider. From settings, never from the
    request: a forged Host header must not be able to choose where a code goes.
    """
    return f'{_frontend()}/api/auth/{provider_id}/callback/'


def landing(path: str, **params) -> str:
    """An address of the storefront, with what the screen has to know."""
    params = {key: value for key, value in params.items() if value}
    return f'{_frontend()}{path}' + (f'?{urlencode(params)}' if params else '')


# -- the providers -----------------------------------------------------------------

def redirect_provider(provider_id):
    """The adapter of a redirect provider (`config`, `authorize_url`, `identify`), or None."""
    from . import apple_identity, facebook_identity

    return {'facebook': facebook_identity, 'apple': apple_identity}.get(provider_id)


def is_enabled(provider_id: str) -> bool:
    if provider_id == 'google':
        from . import google_identity

        return google_identity.is_enabled()
    adapter = redirect_provider(provider_id)
    return adapter is not None and adapter.config() is not None


# -- the attempt -------------------------------------------------------------------

def _key(state: str) -> str:
    return 'social-attempt:' + _digest(state)


def _used_key(state: str) -> str:
    return 'social-attempt-used:' + _digest(state)


def _posted_key(state: str) -> str:
    return 'social-attempt-posted:' + _digest(state)


def begin(provider_id: str, *, intent: str = INTENT_SIGN_IN, user_id=None, next_path: str = '') -> Begun:
    begun = Begun(
        state=secrets.token_urlsafe(32), binding=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(48), nonce=secrets.token_urlsafe(32),
    )
    cache.set(_key(begun.state), {
        'provider': provider_id, 'intent': intent, 'user_id': user_id, 'next': next_path,
        'verifier': begun.verifier, 'nonce': begun.nonce, 'binding': _digest(begun.binding),
        'created': _now(),
    }, ATTEMPT_TTL)
    return begun


def _valid_state(state) -> bool:
    return isinstance(state, str) and 16 <= len(state) <= 128


def hand_over(provider_id: str, state, posted: dict) -> str | None:
    """
    Apple's form_post: keep what was posted for the attempt it names, under a
    one-time hand-off secret that is returned ONLY to the browser that posted
    (in the 303 to the GET). None when there is no such attempt, or it was
    already handed over: the first post of an attempt is the only one.

    This step never sees the attempt's cookie (a cross-site POST does not carry
    a SameSite=Lax one), so it opens nothing. Without the secret, the browser
    holding the cookie — the one that STARTED the attempt — could otherwise
    redeem a code Apple posted from another person's browser.
    """
    if not _valid_state(state):
        return None
    record = cache.get(_key(state))
    if record is None or record.get('provider') != provider_id:
        return None
    handoff = secrets.token_urlsafe(32)
    if not cache.add(_posted_key(state), {'posted': dict(posted), 'handoff': _digest(handoff)}, ATTEMPT_TTL):
        return None
    return handoff


def finish(provider_id: str, state, binding, *, handoff=None) -> Attempt:
    """
    The attempt this state opened, taken out once, for this browser only.

    ANY callback that reaches a live state SPENDS it, before the browser is
    checked: a return without the cookie (the victim's browser, when somebody
    else started the attempt) burns it, so the browser that has the cookie
    cannot finish it afterwards with the code that came back.

    `handoff` — Apple only — is the secret `hand_over` gave the posting browser.
    """
    if not _valid_state(state):
        raise SocialAuthError()
    record = cache.get(_key(state))
    if record is None:
        raise SocialAuthError()
    # The single use: whoever adds the mark first has it. A second arrival —
    # the same callback replayed, or two at once — finds it there.
    if not cache.add(_used_key(state), 1, ATTEMPT_TTL):
        raise SocialAuthError()
    cache.delete(_key(state))
    stashed = cache.get(_posted_key(state))
    cache.delete(_posted_key(state))
    if record.get('provider') != provider_id:
        raise SocialAuthError()
    if _now() - float(record.get('created') or 0) > ATTEMPT_TTL:
        raise SocialAuthError()
    presented = binding if isinstance(binding, str) else ''
    if not hmac.compare_digest(str(record.get('binding')), _digest(presented)):
        raise SocialAuthError()
    posted = None
    if handoff is not None:
        given = handoff if isinstance(handoff, str) else ''
        if not stashed or not hmac.compare_digest(str(stashed.get('handoff')), _digest(given)):
            raise SocialAuthError()
        posted = stashed.get('posted')
    return Attempt(
        provider=provider_id, intent=record['intent'], user_id=record.get('user_id'),
        next_path=safe_next(record.get('next')), verifier=record['verifier'], nonce=record['nonce'],
        posted=posted,
    )


def set_attempt_cookie(response, begun: Begun) -> None:
    response.set_cookie(
        ATTEMPT_COOKIE, begun.binding, max_age=ATTEMPT_TTL, httponly=True,
        secure=settings.JWT_COOKIE_SECURE, samesite='Lax', path=ATTEMPT_COOKIE_PATH,
    )


def clear_attempt_cookie(response) -> None:
    response.delete_cookie(ATTEMPT_COOKIE, path=ATTEMPT_COOKIE_PATH, samesite='Lax')


# -- accounts ------------------------------------------------------------------------

def is_privileged(user) -> bool:
    """
    Whether this account is more than a buyer, by ANY of the roads this codebase
    has: Django's flags, an internal role on the profile, or a membership of a
    company — active or not, a person who once worked somewhere is not a buyer
    whose Facebook may open that account.
    """
    from .models import Membership, UserProfile

    if user is None or user.is_staff or user.is_superuser:
        return True
    if not getattr(user, 'pk', None):
        return False
    if Membership.objects.filter(user=user).exists():
        return True
    role = UserProfile.objects.filter(user=user).values_list('role', flat=True).first()
    return role not in (None, UserProfile.ROLE_CUSTOMER)


def _username_for(identity: SocialIdentity) -> str:
    """A unique username nobody chose: the address's local part, or the provider's name."""
    User = get_user_model()
    seed = identity.email.split('@')[0] if identity.email else identity.provider
    base = re.sub(r'[^a-z0-9._-]', '', seed.lower())[:24] or identity.provider
    candidate = base if identity.email else f'{base}-{secrets.token_hex(3)}'
    while User.objects.filter(username=candidate).exists():
        candidate = f'{base}-{secrets.token_hex(3)}'
    return candidate


@dataclass(frozen=True)
class Outcome:
    user: object = None
    error: str = ''
    created: bool = False


def _may_enter(user) -> Outcome:
    if not user.is_active:
        return Outcome(error=INACTIVE)
    if is_privileged(user):
        return Outcome(error=NOT_ALLOWED)
    return Outcome(user=user)


def _linked(identity: SocialIdentity):
    from .models import ExternalIdentity

    return ExternalIdentity.objects.select_related('user').filter(
        provider=identity.provider, subject=identity.subject,
    ).first()


def _address_taken(identity: SocialIdentity) -> bool:
    address = identity.email or identity.email_seen
    return bool(address) and get_user_model().objects.filter(email__iexact=address).exists()


def sign_in(identity: SocialIdentity) -> Outcome:
    """The buyer account this identity opens — found, or made now — or why not."""
    from . import accounts
    from .models import ExternalIdentity

    linked = _linked(identity)
    if linked is not None:
        return _may_enter(linked.user)
    if _address_taken(identity):
        return Outcome(error=LINK_REQUIRED)

    User = get_user_model()
    try:
        with transaction.atomic():
            user = User(
                username=_username_for(identity), email=identity.email,
                first_name=identity.first_name, last_name=identity.last_name,
            )
            # No password is invented: the account is entered with the provider
            # until its owner sets one through «¿Olvidaste tu contraseña?».
            user.set_unusable_password()
            user.save()
            ExternalIdentity.objects.create(
                user=user, provider=identity.provider, subject=identity.subject,
                email_at_link=(identity.email or identity.email_seen)[:254],
            )
    except IntegrityError:
        # Two tabs finishing the same first sign-in, or the address registered
        # with a password in the same instant (REGISTER-RACE-01).
        linked = _linked(identity)
        if linked is not None:
            return _may_enter(linked.user)
        return Outcome(error=LINK_REQUIRED if _address_taken(identity) else REFUSED)
    if identity.email:
        # The provider vouched for this very address (Apple's `email_verified`).
        accounts.mark_email_verified(user)
    return Outcome(user=user, created=True)


def link(user, identity: SocialIdentity) -> str:
    """Attach the identity to this signed-in account. '' when done, else the reason."""
    from .models import ExternalIdentity

    if not user.is_active:
        return REFUSED
    if is_privileged(user):
        return NOT_ALLOWED
    linked = _linked(identity)
    if linked is not None:
        return '' if linked.user_id == user.pk else ALREADY_LINKED
    try:
        with transaction.atomic():
            ExternalIdentity.objects.create(
                user=user, provider=identity.provider, subject=identity.subject,
                email_at_link=(identity.email or identity.email_seen)[:254],
            )
    except IntegrityError:
        return ALREADY_LINKED
    return ''


def touch(identity: SocialIdentity) -> None:
    from .models import ExternalIdentity

    ExternalIdentity.objects.filter(provider=identity.provider, subject=identity.subject).update(
        last_login_at=timezone.now(),
    )
