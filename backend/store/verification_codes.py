"""
The 6-digit code of the verification e-mail.

The link in that e-mail is the main way to confirm an address: a token nobody
can guess, good for a day. The code beside it is for somebody reading the mail
on one device and registering on another, and it is a far smaller secret — a
million possibilities. Everything here exists to keep it from being guessed:

  * it lasts `CODE_TTL` and takes `MAX_ATTEMPTS` wrong tries, then it is dead;
  * a new code kills the one before it;
  * one account gets a new code at most every `RESEND_WAIT`, and `DAILY_CODES`
    of them in 24 hours — counted on the account, so asking from another
    network changes nothing;
  * only a keyed hash is stored. A bare hash of six digits is reversed by
    trying the million of them; the key is derived from `SECRET_KEY`, so a
    copy of the database alone verifies nobody.

With those numbers an account can be guessed at 25 times a day: one chance in
forty thousand. The code proves what the link proves and nothing more — that
whoever types it reads that mailbox.

It lives on the same row as the link (`AccountToken`): one e-mail, one row, and
using either spends both.

TWO THINGS THAT ARE EASY TO GET WRONG, AND WERE:

  * LOCK ORDER. Issuing and checking both touch the account and its code. Both
    lock THE ACCOUNT FIRST. The other way round in one of them, a right code
    arriving with a «send it again» deadlocked, and one of the two answered 500.
  * A MAIL THAT DID NOT LEAVE COSTS NOTHING. A new code is only made final
    (`settle`) once its mail was handed to the server; otherwise it is taken
    back (`withdraw`), and the wait, the day's budget and the previous code are
    as they were. A mail server that is down must not leave people locked out
    of verification for a day.
"""
import hashlib
import hmac
import re
import secrets
from collections import namedtuple
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from . import accounts
from .models import AccountToken

CODE_TTL = timedelta(minutes=15)
MAX_ATTEMPTS = 5
RESEND_WAIT = timedelta(seconds=60)
DAILY_CODES = 5
LINK_TTL_HOURS = 24

Issued = namedtuple('Issued', 'token code row_id user_id')

_PURPOSE = AccountToken.PURPOSE_EMAIL_VERIFICATION
_SIX_DIGITS = re.compile(r'[0-9]{6}')
_SEPARATORS = '-‐‑‒–—'
_NOBODY = 0          # no account has this id: what is locked and compared when there is nothing to check


def _key() -> bytes:
    # Derived, not the secret itself: one use, one key.
    return hashlib.sha256(b'email-verification-code|' + settings.SECRET_KEY.encode('utf-8')).digest()


def _mac(user_id, code: str) -> str:
    """Bound to the account: the same six digits are a different secret for somebody else."""
    return hmac.new(_key(), f'{user_id}:{code}'.encode('utf-8'), hashlib.sha256).hexdigest()


def normalise(typed) -> str | None:
    """
    What somebody typed or pasted, as six digits — or None if it is not a code.

    People paste «123 456» and «123-456»; spaces and hyphens say nothing. Only
    TEXT is a code: a JSON number has already lost its leading zeros.
    """
    if not isinstance(typed, str):
        return None
    text = ''.join(ch for ch in typed if not ch.isspace() and ch not in _SEPARATORS)
    return text if _SIX_DIGITS.fullmatch(text) else None


def _lock_account(user_id):
    """THE FIRST LOCK, always: see the module docstring."""
    return get_user_model().objects.select_for_update().filter(pk=user_id).first()


@transaction.atomic
def issue(user) -> Issued | None:
    """
    A new verification link with its code, or None when this account must wait.

    None is not an error and is never told to whoever asked: the answer to
    «send it again» is the same whether something was sent or not.

    The previous code is NOT killed here. It dies when this one's mail has left
    (`settle`); if it did not, `withdraw` takes this one back. Meanwhile only
    the newest code of an account is ever accepted, so two are never good at once.
    """
    _lock_account(user.pk)
    now = timezone.now()
    sent = AccountToken.objects.filter(user=user, purpose=_PURPOSE)
    latest = sent.order_by('-created_at', '-pk').first()
    if latest is not None and now - latest.created_at < RESEND_WAIT:
        return None
    if sent.filter(created_at__gt=now - timedelta(hours=24)).count() >= DAILY_CODES:
        return None

    raw_token, row = AccountToken.make(user, _PURPOSE, ttl_hours=LINK_TTL_HOURS)
    code = f'{secrets.randbelow(10 ** 6):06d}'
    row.code_hash = _mac(user.pk, code)
    row.code_expires_at = now + CODE_TTL
    row.save(update_fields=['code_hash', 'code_expires_at'])
    return Issued(raw_token, code, row.pk, user.pk)


@transaction.atomic
def settle(issued: Issued) -> None:
    """The mail left. Every older code of the account is over."""
    _lock_account(issued.user_id)
    (AccountToken.objects
     .filter(user_id=issued.user_id, purpose=_PURPOSE)
     .exclude(pk=issued.row_id).exclude(code_hash='')
     .update(code_hash='', code_expires_at=None))


@transaction.atomic
def withdraw(issued: Issued) -> None:
    """
    The mail did not leave: nobody has this link or this code. Taken back, so
    the wait, the day's budget and the previous code are what they were.

    Only for a RESEND. The row a registration makes stays even if its mail
    failed: it is what says the account was registered and never confirmed
    (`accounts.is_unverified`), and without it nothing could finish the account.
    """
    _lock_account(issued.user_id)
    AccountToken.objects.filter(pk=issued.row_id, user_id=issued.user_id, used_at__isnull=True).delete()


def _one_address(email) -> str:
    """An address as text a database can look up, or '' — which finds nobody."""
    if not isinstance(email, str) or '\x00' in email or len(email) > 254:
        return ''
    return email


def check(email, typed, *, on_exhausted=None):
    """
    The account this code finishes, now active — or None.

    None for every reason alike: no such account, an account already verified
    or switched off for another reason, two accounts under the address, not a
    code, a wrong code, an old one, one tried too often. The caller gives one
    answer for all of them.

    Whatever was asked, this locks an account row and a code row and compares
    one HMAC — for nobody, when there is nobody — so that how long it takes says
    as little as possible about which of those it was.
    """
    code = normalise(typed)
    waiting = [user for user in accounts.with_email(_one_address(email)) if not user.is_active]
    candidate = waiting[0] if code is not None and len(waiting) == 1 else None
    user_id = candidate.pk if candidate is not None else _NOBODY
    now = timezone.now()
    with transaction.atomic():
        # The account first, then its code: the same order as `issue`.
        user = _lock_account(user_id)
        row = (
            AccountToken.objects.select_for_update()
            .filter(user_id=user_id, purpose=_PURPOSE, used_at__isnull=True)
            .exclude(code_hash='')
            .order_by('-created_at', '-pk')
            .first()
        )
        expected = row.code_hash if row is not None else _mac(_NOBODY, '000000')
        matches = hmac.compare_digest(expected, _mac(user_id, code or '000001'))
        if (
            user is None or row is None
            # «Inactive» is not the question: registered and never confirmed is.
            or not accounts.is_unverified(user)
            or row.code_expires_at is None or row.code_expires_at <= now
            or row.expires_at <= now or row.code_attempts >= MAX_ATTEMPTS
        ):
            return None
        if not matches:
            # Counted on the row, where asking again from elsewhere cannot undo it.
            row.code_attempts += 1
            row.save(update_fields=['code_attempts'])
            if row.code_attempts >= MAX_ATTEMPTS and on_exhausted is not None:
                on_exhausted(user)
            return None
        # Every link and code the account was sent, not only this one.
        accounts.mark_verified(user)
        user.is_active = True
        user.save(update_fields=['is_active'])
    return user
