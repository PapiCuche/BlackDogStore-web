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


def mark_email_verified(user, *, when=None) -> None:
    """
    Somebody has just PROVED they read this account's mailbox (CLIENT-SEC-01).

    Called by the roads that prove it and by nothing else: the verification
    link, its code, a recovery link that finishes an account, Google naming
    this very address, a staff invitation sent to it. Signing in proves
    nothing, and neither does being active.

    The first proof is the one kept: the date says since when the address is
    known to be read, not when it was last used.
    """
    from .models import UserProfile

    if user is None or not getattr(user, 'pk', None):
        return
    profile, _created = UserProfile.objects.get_or_create(
        user=user, defaults={'role': UserProfile.ROLE_CUSTOMER},
    )
    UserProfile.objects.filter(pk=profile.pk, email_verified_at__isnull=True).update(
        email_verified_at=when or timezone.now(),
    )


def email_verified_at(user):
    """When this account's mailbox was proven, or None: unknown is not verified."""
    from .models import UserProfile

    if user is None or not getattr(user, 'pk', None):
        return None
    # Asked of the database: the profile an authenticated request carries may
    # have been read before the proof was written.
    return UserProfile.objects.filter(user=user).values_list('email_verified_at', flat=True).first()


def with_email(email: str) -> list:
    """Every account under this address, as people write it: no case, no stray spaces."""
    email = (email or '').strip()
    if not email:
        return []
    return list(get_user_model().objects.filter(email__iexact=email).order_by('pk'))


def duplicate_email_groups(model=None) -> list:
    """
    Accounts that share one address, grouped — the past the unique rule cannot accept.

    Grouped BY THE DATABASE, with UPPER(email): the expression of the unique
    rule and of every `email__iexact` in this code. Grouping in Python with
    `.lower()` would miss the pairs the database does see as one address.
    Accounts WITHOUT an address are not a group: there may be many.

    Read-only. Which account of a group stays is a person's decision: one may
    hold the orders and another the password somebody remembers.
    """
    from itertools import groupby

    from django.db.models.functions import Upper

    model = model or get_user_model()
    rows = model.objects.exclude(email='').annotate(address_key=Upper('email')).order_by('address_key', 'pk')
    groups = [list(users) for _key, users in groupby(rows, key=lambda user: user.address_key)]
    return [users for users in groups if len(users) > 1]
