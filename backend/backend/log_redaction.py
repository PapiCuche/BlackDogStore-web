"""
LOG-REDACT: keep credentials and personal identifiers out of the logs.

A URL is logged twice in production: by gunicorn's access log (request line and
referer) and by Django when it refuses a request ("Not Found: /path"). Some URLs
ARE credentials — a repair's tracking link, a staff invitation, the token Meta
sends to verify a webhook — and some carry what a person typed into a search box:
an IMEI, a document number, a phone.

  · a path keeps its shape and loses the token;
  · a query string keeps its keys, and the values of the keys listed here.

Default deny on purpose: a parameter nobody thought about is hidden, not shown.

No Django import: gunicorn loads this before the application exists.
"""
import logging
import re

MASK = '[redactado]'

#: Routes whose next segment is the credential itself.
_PATH_TOKEN = re.compile(r'(/(?:api/v1/tracking|seguimiento)/)[^/?#\s"]+')

#: Query keys whose value says how a list was asked for, never who or what.
SAFE_QUERY_KEYS = frozenset({
    'page', 'page_size', 'limit', 'offset', 'ordering', 'order', 'sort',
    'branch', 'company', 'product', 'category', 'status', 'stage', 'type', 'kind',
    'scope', 'formato', 'format', 'tab', 'view', 'period', 'group_by', 'days',
    'from', 'to', 'date_from', 'date_to', 'start', 'end', 'year', 'month',
    'active', 'is_active', 'movement_type', 'payment_method', 'channel',
    'hub.mode',
})


def redact_path(text: str) -> str:
    return _PATH_TOKEN.sub(lambda match: match.group(1) + MASK, text)


def redact_query(query: str) -> str:
    parts = []
    for pair in query.split('&'):
        key, separator, value = pair.partition('=')
        if separator and value and key.lower() not in SAFE_QUERY_KEYS:
            pair = f'{key}={MASK}'
        parts.append(pair)
    return '&'.join(parts)


def redact_uri(uri: str) -> str:
    """A path, a path with its query string, or an absolute URL (a referer)."""
    if not uri:
        return uri
    path, separator, query = uri.partition('?')
    return redact_path(path) + (separator + redact_query(query) if separator else '')


def redact_request_line(line: str) -> str:
    """`GET /path?query HTTP/1.1`, as the access log writes it."""
    parts = line.split(' ')
    if len(parts) == 3:
        return f'{parts[0]} {redact_uri(parts[1])} {parts[2]}'
    return redact_path(line)


class RedactingFilter(logging.Filter):
    """
    For the application's own log lines. Django names the path of every request
    it refuses; a rate-limited read of a tracking link would write the link.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a malformed record is the handler's problem, not ours
            return True
        cleaned = redact_path(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True
