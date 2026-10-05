"""
From what somebody typed at a counter to a number the API can dial.

NOTHING HERE ASSUMES A COUNTRY. A number written with its country code is used
as written. A national number is completed with the calling code the COMPANY
configured — and when the company configured none, it is not guessed: the
message is skipped and the reason is recorded.
"""
from __future__ import annotations

import re

_MIN_DIGITS = 10
_MAX_DIGITS = 15          # E.164
_ASSUMED_INTERNATIONAL = 11


def to_wa_id(raw, default_calling_code: str = '') -> str | None:
    """Digits with country code and no "+", as the Cloud API wants them. Or None."""
    text = str(raw or '').strip()
    digits = re.sub(r'\D', '', text)
    international = text.startswith('+')
    if text.startswith('00'):
        digits, international = digits[2:], True
    if not digits:
        return None

    if international:
        full = digits
    else:
        code = re.sub(r'\D', '', str(default_calling_code or ''))
        if code:
            national = digits.lstrip('0')
            already = digits.startswith(code) and len(digits) >= len(code) + 8
            full = digits if already else code + national
        elif len(digits) >= _ASSUMED_INTERNATIONAL:
            full = digits
        else:
            return None

    return full if _MIN_DIGITS <= len(full) <= _MAX_DIGITS else None


def mask(wa_id: str) -> str:
    """`•••• 4321` — enough to recognise a number, not enough to dial it."""
    digits = re.sub(r'\D', '', str(wa_id or ''))
    return f'•••• {digits[-4:]}' if digits else ''
