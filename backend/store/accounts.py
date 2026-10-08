"""
What an account IS, asked in one place.

An account that is not active is, in this codebase, one thing only: somebody
registered it and never confirmed the mailbox. Nothing else switches an account
off — a worker who leaves loses a `Membership`, not their identity. But «not
active» does not SAY that, and the day something else switches an account off,
whatever finishes a half-made account must not also switch that one back on.

So the question is asked precisely: was this account registered and never, ever
confirmed? Registration under e-mail verification always leaves a verification
token; confirming it marks one as used. An inactive account with tokens and none
of them used is unverified. Any other inactive account is not ours to revive.
"""
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import AccountToken


def is_unverified(user) -> bool:
    """Registered, and the mailbox never confirmed. Not «inactive for some reason»."""
    if user is None or user.is_active:
        return False
    tokens = AccountToken.objects.filter(
        user=user, purpose=AccountToken.PURPOSE_EMAIL_VERIFICATION,
    )
    return tokens.exists() and not tokens.filter(used_at__isnull=False).exists()


def mark_verified(user) -> None:
    """
    The mailbox has been confirmed by some other road (a recovery link read in
    it). Spend the verification links so the account is never again taken for
    one nobody confirmed.
    """
    AccountToken.objects.filter(
        user=user, purpose=AccountToken.PURPOSE_EMAIL_VERIFICATION, used_at__isnull=True,
    ).update(used_at=timezone.now())


def with_email(email: str) -> list:
    """Every account under this address, as people write it: no case, no stray spaces."""
    email = (email or '').strip()
    if not email:
        return []
    return list(get_user_model().objects.filter(email__iexact=email).order_by('pk'))


def duplicate_email_groups(model=None) -> list:
    """
    Accounts that share one address, grouped — the past the unique rule cannot accept.

    Compared as people write an address: no capitals, no stray spaces. Accounts
    WITHOUT an address are not a group: there may be many and that is allowed.

    Read-only. Which account of a group stays is a person's decision: one may
    hold the orders and another the password somebody remembers.
    """
    from collections import defaultdict

    model = model or get_user_model()
    groups = defaultdict(list)
    for user in model.objects.exclude(email='').order_by('pk'):
        key = (user.email or '').strip().lower()
        if key:
            groups[key].append(user)
    return [users for _key, users in sorted(groups.items()) if len(users) > 1]
