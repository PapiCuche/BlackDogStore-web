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

from .models import AccountToken


def is_unverified(user) -> bool:
    """Registered, and the mailbox never confirmed. Not «inactive for some reason»."""
    if user is None or user.is_active:
        return False
    tokens = AccountToken.objects.filter(
        user=user, purpose=AccountToken.PURPOSE_EMAIL_VERIFICATION,
    )
    return tokens.exists() and not tokens.filter(used_at__isnull=False).exists()


def with_email(email: str) -> list:
    """Every account under this address, as people write it: no case, no stray spaces."""
    email = (email or '').strip()
    if not email:
        return []
    return list(get_user_model().objects.filter(email__iexact=email).order_by('pk'))
