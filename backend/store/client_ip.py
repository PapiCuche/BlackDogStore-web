"""
Who the client is, at the network level — Phase 0.3 / P0-B.

THE PROBLEM THIS SOLVES
-----------------------
`X-Forwarded-For` is a header. Headers come from whoever is talking to us, and
whoever is talking to us may be an attacker. So the question "what is the
client's IP?" has no answer at all unless somebody first says how many proxies
sit in front of this process and can therefore be believed.

Before this module, three different answers coexisted:

  · DRF throttling used `BaseThrottle.get_ident()` with `NUM_PROXIES` unset,
    which in DRF 3.17 means "if X-Forwarded-For is present, use the WHOLE
    header as the identity". A client sending a different value on each request
    got a fresh throttle bucket every time — the rate limit was decorative.

  · `AdminAuditLog.log()` took `xff.split(',')[0]`, the LEFTMOST entry. That is
    the position furthest from us and the one an attacker controls completely:
    anybody could choose which IP address their actions were recorded under.

  · Everything else used `REMOTE_ADDR`, which is the only value in the request
    that the transport itself guarantees.

THE POLICY
----------
One setting decides, for the whole application:

    TRUSTED_PROXY_COUNT = 0     (default)
        Believe nobody. The client IP is `REMOTE_ADDR` — the peer that actually
        opened the socket. Any `X-Forwarded-For` is ignored entirely.

    TRUSTED_PROXY_COUNT = N > 0
        The operator asserts that EXACTLY N proxies sit in front of this
        process, each appending its own view of the caller to
        `X-Forwarded-For`. The client is then the Nth entry from the RIGHT,
        because the rightmost entries are the ones written by infrastructure we
        control; everything to the left of them was supplied by the caller and
        may be fabricated.

WHY THE DEFAULT IS ZERO
-----------------------
Zero is the only value that is safe without knowing the deployment. It can be
wrong in one direction — behind an undeclared proxy every client looks like the
proxy, so they share a rate-limit bucket and the limit becomes too strict. It
cannot be wrong in the other direction, which is the one that matters: no header
a stranger sends can ever change who we think they are.

`TRUSTED_PROXY_COUNT = 1` while the proxy does NOT append to `X-Forwarded-For`
is the dangerous configuration, and it is dangerous in a way that looks correct:
with one proxy declared and nothing appended, the rightmost entry IS the value
the client supplied. Declaring a proxy count is therefore a statement about what
the proxy DOES, not about how many hops exist.

THIS IS THE SAME RULE DRF USES
------------------------------
`NUM_PROXIES` is set from `TRUSTED_PROXY_COUNT` in settings, so throttling and
auditing resolve the client identically. Two subsystems disagreeing about who
the caller is would mean the log says one thing and the rate limiter another,
which is worse than either being wrong on its own.
"""

from __future__ import annotations

from django.conf import settings


def trusted_proxy_count() -> int:
    """How many proxies in front of this process may be believed."""
    try:
        return max(0, int(getattr(settings, 'TRUSTED_PROXY_COUNT', 0) or 0))
    except (TypeError, ValueError):
        # An unparseable configuration must not silently become "trust
        # everything". Falling back to zero keeps the failure closed.
        return 0


def get_client_ip(request) -> str | None:
    """
    The caller's IP under the configured trust policy, or None.

    Returns `None` rather than a guess when nothing trustworthy is available —
    a null in an audit row is a fact ("we do not know"), while a fabricated
    address is a lie that someone will later act on.
    """
    meta = getattr(request, 'META', None) or {}
    remote_addr = (meta.get('REMOTE_ADDR') or '').strip() or None

    count = trusted_proxy_count()
    if count == 0:
        return remote_addr

    forwarded = (meta.get('HTTP_X_FORWARDED_FOR') or '').strip()
    if not forwarded:
        # Declared proxies that sent no header: either the request did not come
        # through them, or they are misconfigured. Either way the only value
        # left with any guarantee behind it is the socket peer.
        return remote_addr

    entries = [part.strip() for part in forwarded.split(',') if part.strip()]
    if not entries:
        return remote_addr

    # The Nth from the right, matching DRF's `get_ident`. If the chain is
    # shorter than the declared count, take the leftmost entry available rather
    # than reading past the start of the list.
    return entries[-min(count, len(entries))]
