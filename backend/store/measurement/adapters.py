"""
The only code that talks to Google Analytics, Meta and TikTok from the server.

    WHERE EVENTS GO IS DECIDED HERE, IN CODE.

The three endpoints below are constants taken from each provider's own
documentation. Nothing a person types in the console — and nothing stored in the
database — can change the host a credential is sent to: the console holds IDs
and keys, never addresses.

    Google   Measurement Protocol (GA4)   developers.google.com/analytics/devguides/collection/protocol/ga4
    Meta     Conversions API              developers.facebook.com/docs/marketing-api/conversions-api
    TikTok   Events API 2.0               business-api.tiktok.com/portal/docs?id=1771100865818625

Every failure is reduced to a KIND before it leaves this module. A provider's
own error text is never passed on: Meta and TikTok both quote the request in
their messages, and the request carries the access token.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

GA_COLLECT = 'https://www.google-analytics.com/mp/collect'
GA_VALIDATE = 'https://www.google-analytics.com/debug/mp/collect'
META_GRAPH = 'https://graph.facebook.com'
META_GRAPH_VERSION = 'v25.0'
TIKTOK_TRACK = 'https://business-api.tiktok.com/open_api/v1.3/event/track/'
TIMEOUT_SECONDS = 8

OK, AUTH_FAILED, INVALID, UNAVAILABLE = 'ok', 'auth_failed', 'invalid', 'unavailable'

#: Graph error codes that mean the token is not accepted (190) or lacks permission.
_META_AUTH_CODES = frozenset({'190', '102', '10', '200', '294'})
#: TikTok business codes: 401xx are about the access token and its permissions.
_TIKTOK_AUTH_PREFIX = '401'


@dataclass(frozen=True)
class Answer:
    """What a provider did with a request: a kind, and whether trying again can help."""

    kind: str
    retryable: bool = False
    #: The provider's own code, for an operator to look up. Never its message.
    code: str = ''

    @property
    def ok(self) -> bool:
        return self.kind == OK


def _post(url: str, body: dict, headers: dict | None = None, timeout: float = TIMEOUT_SECONDS):
    """`(http status, parsed JSON or None)`. Raises only what `urlopen` raises."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read() or b''
    try:
        return 200, json.loads(raw) if raw else {}
    except ValueError:
        return 200, None


def _error_payload(exc: urllib.error.HTTPError) -> dict:
    try:
        parsed = json.loads(exc.read() or b'{}')
    except (ValueError, AttributeError, OSError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _network_failure() -> Answer:
    return Answer(UNAVAILABLE, retryable=True)


# -- Google Analytics 4 -----------------------------------------------------------------

def ga_url(base: str, measurement_id: str, api_secret: str) -> str:
    return f'{base}?' + urllib.parse.urlencode({'measurement_id': measurement_id, 'api_secret': api_secret})


def ga_validate(measurement_id: str, api_secret: str, payload: dict) -> Answer:
    """
    Google's validation server: it checks the SHAPE of a payload and records
    nothing. By Google's own words it validates neither the measurement ID nor
    the API secret, so a clean answer proves the payload, not the credentials.
    """
    try:
        _status, parsed = _post(ga_url(GA_VALIDATE, measurement_id, api_secret), payload)
    except urllib.error.HTTPError as exc:
        return Answer(UNAVAILABLE if exc.code >= 500 or exc.code == 429 else INVALID, retryable=exc.code >= 500)
    except (urllib.error.URLError, TimeoutError, OSError):
        return _network_failure()
    messages = parsed.get('validationMessages') if isinstance(parsed, dict) else None
    if messages:
        code = str((messages[0] or {}).get('validationCode') or '')[:40] if isinstance(messages[0], dict) else ''
        return Answer(INVALID, code=code)
    return Answer(OK)


def ga_send(measurement_id: str, api_secret: str, payload: dict) -> Answer:
    """The Measurement Protocol answers 2xx to anything it received, malformed or not."""
    try:
        _post(ga_url(GA_COLLECT, measurement_id, api_secret), payload)
    except urllib.error.HTTPError as exc:
        retry = exc.code >= 500 or exc.code == 429
        return Answer(UNAVAILABLE if retry else INVALID, retryable=retry, code=str(exc.code))
    except (urllib.error.URLError, TimeoutError, OSError):
        return _network_failure()
    return Answer(OK)


# -- Meta Conversions API ---------------------------------------------------------------

def meta_send(pixel_id: str, access_token: str, events: list, test_event_code: str = '') -> Answer:
    """
    POST /{pixel}/events. The token travels in the BODY: a URL ends up in the
    access log of every proxy on the way, and in a library's error message.
    """
    body = {'data': events, 'access_token': access_token}
    if test_event_code:
        body['test_event_code'] = test_event_code
    try:
        _status, parsed = _post(f'{META_GRAPH}/{META_GRAPH_VERSION}/{pixel_id}/events', body)
    except urllib.error.HTTPError as exc:
        error = _error_payload(exc).get('error')
        code = str((error or {}).get('code') or '') if isinstance(error, dict) else ''
        if exc.code in (401, 403) or code in _META_AUTH_CODES:
            return Answer(AUTH_FAILED, code=code)
        if exc.code >= 500 or exc.code == 429:
            return Answer(UNAVAILABLE, retryable=True, code=code)
        return Answer(INVALID, code=code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return _network_failure()
    received = parsed.get('events_received') if isinstance(parsed, dict) else None
    return Answer(OK) if received else Answer(INVALID)


# -- TikTok Events API 2.0 --------------------------------------------------------------

def tiktok_send(pixel_code: str, access_token: str, events: list, test_event_code: str = '') -> Answer:
    """POST /event/track/. TikTok answers HTTP 200 with its own `code`: 0 is the only success."""
    body = {'event_source': 'web', 'event_source_id': pixel_code, 'data': events}
    if test_event_code:
        body['test_event_code'] = test_event_code
    try:
        _status, parsed = _post(TIKTOK_TRACK, body, headers={'Access-Token': access_token})
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return Answer(AUTH_FAILED, code=str(exc.code))
        retry = exc.code >= 500 or exc.code == 429
        return Answer(UNAVAILABLE if retry else INVALID, retryable=retry, code=str(exc.code))
    except (urllib.error.URLError, TimeoutError, OSError):
        return _network_failure()
    code = str(parsed.get('code')) if isinstance(parsed, dict) and parsed.get('code') is not None else ''
    if code == '0':
        return Answer(OK)
    if code.startswith(_TIKTOK_AUTH_PREFIX):
        return Answer(AUTH_FAILED, code=code)
    if code.startswith('5') or code == '40100':          # their side, or a rate limit
        return Answer(UNAVAILABLE, retryable=True, code=code)
    return Answer(INVALID, code=code)
