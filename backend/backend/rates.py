"""
Throttle rates that come from the environment.

A DRF rate is a text like `6/min`. DRF parses it WHEN A REQUEST ARRIVES, so a
typo in the environment would be a 500 on every request of that view, long
after the server started "fine". Here it is checked once, at start-up: a value
that is not a rate falls back to the default and says so in the log.

It falls back rather than refusing to start because every rate read here is an
optional knob with a safe default. Taking the whole shop down for a typo in one
of them would cost more than the typo.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: What `rest_framework.throttling.SimpleRateThrottle.parse_rate` can use: a
#: positive whole number, a slash, and a period it knows by its first letter.
_RATE = re.compile(r'[1-9]\d{0,6}/(s|sec|second|m|min|minute|h|hour|d|day)')


def rate_from_env(name: str, value, default: str) -> str:
    """`value` (what the environment holds under `name`) as a DRF rate, or `default`."""
    text = str(value or '').strip()
    if not text:
        return default
    if _RATE.fullmatch(text):
        return text
    # The value is not echoed: an environment file is not a place to quote from.
    logger.warning(
        '%s is not a rate like "6/min" (a positive number, a slash, and s, min, hour or day): '
        'using the default, %s.', name, default,
    )
    return default
