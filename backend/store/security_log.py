"""
Security event log — AUTH-LOGGING-01.

No login channel recorded an attempt, failed or successful. A burst of wrong
passwords against one account, or a sign-in from somewhere new, left no trace
anywhere: the throttle answered 429 and that was the only evidence, and it was
gone as soon as the window closed.

These are log records, not database rows. The audit log (`AdminAuditLog`) is
for what an authenticated member DID inside a company; a failed login belongs to
no company and to no user, and writing a row for each one would hand an
anonymous caller a way to fill a table.

NEVER LOGGED: the password, a token, or a cookie.
"""
import logging

from .client_ip import get_client_ip

logger = logging.getLogger('store.security')

_MAX_IDENTIFIER = 150


def _clean(value) -> str:
    """
    What the caller typed as their name, made safe to put on one log line.

    Control characters are dropped: a line break inside a value would let an
    anonymous caller write records that look like they came from the system.
    """
    text = '' if value is None else str(value)
    text = ''.join(ch for ch in text if ch.isprintable())
    return text[:_MAX_IDENTIFIER]


def login_failed(request, *, channel: str, identifier) -> None:
    logger.warning(
        'login_failed channel=%s ip=%s identifier=%r',
        channel, get_client_ip(request) or '-', _clean(identifier),
    )


def login_succeeded(request, *, channel: str, user) -> None:
    logger.info(
        'login_ok channel=%s ip=%s user_id=%s',
        channel, get_client_ip(request) or '-', user.pk,
    )
