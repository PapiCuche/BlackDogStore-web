"""
The Izipay adapter — the ONLY module in this project that knows how Izipay
talks.

Everything provider-specific lives behind this boundary: endpoint selection,
credential loading, the session-token call, signature verification and the
shape of a notification. Above it, the domain speaks in `PaymentTransaction`,
`Order` and money, and would not have to change if the provider did.

WHAT IS SIGNED, EXACTLY
-----------------------
Izipay signs ONE thing: the `payloadHttp` string. The notification body also
carries a decoded `response` object that is *convenient* and *unsigned* — it
can be edited by anyone who can reach our endpoint without invalidating
anything, because no signature covers it.

So the rule this module enforces, and the reason `NotificationResult` exists at
all: every authoritative value — amount, currency, order number, merchant,
transaction id, response code — is read from INSIDE the parsed `payloadHttp`.
The outer envelope is used for exactly two things: to obtain `payloadHttp` and
to obtain `signature`. Nothing else in it is believed.

Official documentation consulted (Izipay Developers, web-core / Checkout SDK):
  https://developers.izipay.pe/web-core/quickstart/
  https://developers.izipay.pe/web-core/modalidades/parameters/
  https://developers.izipay.pe/web-core/modalidades/firma-validation/
  https://developers.izipay.pe/web-core/notifications/
  https://developers.izipay.pe/web-core/codes-and-responses/
  https://developers.izipay.pe/credentials/
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings

logger = logging.getLogger(__name__)

PROVIDER = 'izipay'

# Sandbox and production are DIFFERENT ENVIRONMENTS, never inferred from DEBUG.
# These two URLs are quoted verbatim from the official quickstart; the frontend
# holds the same pair and picks by name, so no URL ever travels as data.
SDK_URLS = {
    'sandbox': 'https://sandbox-checkout.izipay.pe/payments/v1/js/index.js',
    'production': 'https://checkout.izipay.pe/payments/v1/js/index.js',
}

ENVIRONMENTS = frozenset(SDK_URLS)

# The only value Izipay documents as an authorised payment in the response-code
# table for this product.
#
# `P66` also reads "Operación exitosa / OK / approved" in that table, and it is
# DELIBERATELY NOT HERE. The two errors are not symmetric: accepting a code that
# does not mean "money captured" ships goods for free, while rejecting one that
# does leaves a real payment sitting in PENDING_PAYMENT where the notification
# record and the operator both can see it. The recoverable failure is the one to
# choose while `P66` is unconfirmed with Izipay. Widening this set is a one-line
# change in one place, which is the whole reason `is_authorized()` exists.
AUTHORIZED_RESPONSE_CODES = frozenset({'00'})

# Izipay's own guidance: the signature is not present/meaningful for these two,
# because they describe a failure to complete the exchange rather than its
# outcome. They are never authorisations, so this module refuses them outright
# instead of trying to verify a signature that was never generated.
NON_SIGNED_RESPONSE_CODES = frozenset({'021', 'COMMUNICATION_ERROR'})

# `transactionId` is String, length 5-40 (official parameter table) and every
# documented example is numeric, so digits are the format that is certainly
# accepted. 20 digits is ~66 bits — collision-resistant, and unguessable enough
# to be the possession token the payment-status endpoint checks.
TRANSACTION_ID_DIGITS = 20
# `orderNumber` must be unique per ATTEMPT, not per order: response code P69 is
# "Número de orden duplicado". Retrying a rejected payment therefore needs a new
# one, which is why it belongs to PaymentTransaction and not to Order.
ORDER_NUMBER_DIGITS = 12


class IzipayError(Exception):
    """Talking to Izipay failed. Never carries a credential or a raw payload."""


@dataclass(frozen=True)
class IzipayCredentials:
    """
    What one merchant account is.

    A VALUE, deliberately, not a read of `settings` scattered through the code:
    the day a tenant gets its own merchant account, this is the thing that comes
    from somewhere else, and every caller already takes it as an argument.

    `merchant_code` and `public_key` are safe in a browser — Izipay documents
    both as public. `api_key` and `hash_key` are not, and never leave the
    backend: one mints session tokens, the other verifies signatures.
    """

    environment: str
    merchant_code: str
    api_key: str
    hash_key: str
    public_key: str
    token_url: str
    currency: str

    @property
    def sdk_url(self) -> str:
        return SDK_URLS[self.environment]


def load_credentials() -> IzipayCredentials:
    """
    Read the configured merchant account, or refuse.

    Fails closed and loudly. A payment page that renders with a missing key
    would fail later, in front of a buyer, with a provider error nobody can act
    on.
    """
    environment = (settings.IZIPAY_ENV or '').strip().lower()
    if environment not in ENVIRONMENTS:
        raise IzipayError(
            f"IZIPAY_ENV debe ser uno de {sorted(ENVIRONMENTS)}; se recibió "
            f"{environment!r}."
        )

    token_url = (settings.IZIPAY_TOKEN_URL or '').strip()
    missing = [
        name for name, value in (
            ('IZIPAY_MERCHANT_CODE', settings.IZIPAY_MERCHANT_CODE),
            ('IZIPAY_API_KEY', settings.IZIPAY_API_KEY),
            ('IZIPAY_HASH_KEY', settings.IZIPAY_HASH_KEY),
            ('IZIPAY_PUBLIC_KEY', settings.IZIPAY_PUBLIC_KEY),
            ('IZIPAY_TOKEN_URL', token_url),
        ) if not (value or '').strip()
    ]
    if missing:
        raise IzipayError(
            'Izipay no está configurado. Faltan: ' + ', '.join(missing) + '.'
        )

    if not token_url.startswith('https://'):
        raise IzipayError('IZIPAY_TOKEN_URL debe ser https.')

    # One-directional guard against the deployment mistake that actually costs
    # money: a production install still pointed at the sandbox would take real
    # orders that were never really charged. The reverse is harmless, and test
    # hosts are not reliably named "sandbox", so it is not checked.
    if environment == 'production' and 'sandbox' in token_url.lower():
        raise IzipayError(
            'IZIPAY_ENV=production con un IZIPAY_TOKEN_URL de sandbox.'
        )

    return IzipayCredentials(
        environment=environment,
        merchant_code=settings.IZIPAY_MERCHANT_CODE.strip(),
        api_key=settings.IZIPAY_API_KEY.strip(),
        hash_key=settings.IZIPAY_HASH_KEY.strip(),
        public_key=settings.IZIPAY_PUBLIC_KEY.strip(),
        token_url=token_url,
        currency=(settings.IZIPAY_CURRENCY or '').strip().upper(),
    )


def new_transaction_id() -> str:
    """A fresh `transactionId`. Server-side only — never accepted from a client."""
    return ''.join(secrets.choice('0123456789') for _ in range(TRANSACTION_ID_DIGITS))


def new_order_number() -> str:
    """A fresh `orderNumber`, unique per attempt (see P69)."""
    return ''.join(secrets.choice('0123456789') for _ in range(ORDER_NUMBER_DIGITS))


def format_amount(amount: Decimal) -> str:
    """
    Money as Izipay is documented to take it: a decimal STRING, `'149.00'`.

    Never a float. The value handed over is `Order.total` quantised once, so the
    figure the buyer authorises is the figure the order says — not a sum of
    per-line roundings that can miss it by a cent.
    """
    return str(Decimal(amount).quantize(Decimal('0.01')))


def is_authorized(response_code: str) -> bool:
    """
    Did Izipay authorise this payment?

    ONE definition, in one place. The alternative — `if code == '00'` written out
    at each call site — is how a view ends up disagreeing with the notification
    handler about what "paid" means.
    """
    return (response_code or '').strip() in AUTHORIZED_RESPONSE_CODES


def sign(payload_http: str, hash_key: str) -> str:
    """
    base64( HMAC-SHA256( payloadHttp, claveHash ) ) — the documented algorithm.

    Present so tests can compute a REAL signature instead of mocking the check,
    and so the notification handler and the tests cannot drift apart.
    """
    digest = hmac.new(
        hash_key.encode('utf-8'), payload_http.encode('utf-8'), hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode('ascii')


def verify_signature(payload_http: str, signature: str, hash_key: str) -> bool:
    """
    Constant-time comparison of the received signature against ours.

    `compare_digest`, not `==`: the naive comparison returns as soon as two
    bytes differ, and that timing is a measurable oracle an attacker can walk to
    forge a signature one byte at a time.

    An empty key returns False rather than verifying against ''. A deployment
    that lost its hash key must reject notifications, not accept whatever
    arrives.
    """
    if not hash_key or not signature or not payload_http:
        return False
    return hmac.compare_digest(sign(payload_http, hash_key), signature)


@dataclass(frozen=True)
class NotificationResult:
    """
    A notification, reduced to the values that came out of the SIGNED bytes.

    Every field here was read from inside `payloadHttp`. Nothing from the
    unsigned envelope survives into this object, so code that holds one cannot
    accidentally trust the wrong copy of `amount`.
    """

    transaction_id: str
    order_number: str
    amount: Decimal
    currency: str
    merchant_code: str
    response_code: str
    pay_method: str
    authorization_code: str
    reference_number: str
    unique_id: str
    state_message: str

    @property
    def authorized(self) -> bool:
        return is_authorized(self.response_code)


def parse_notification(body: dict, credentials: IzipayCredentials) -> NotificationResult:
    """
    Verify a notification and return what it actually says.

    Raises `IzipayError` on anything that is not a well-formed, correctly signed
    message. It answers only "is this genuinely from Izipay, and what does it
    state" — whether the order may be paid is the domain's decision, made
    against the database.

    THE PAYLOAD IS NOT RE-SERIALISED. `payloadHttp` is taken as the exact string
    Izipay sent and signed. Parsing it to a dict and dumping it again would
    reorder keys, change spacing and re-escape non-ASCII — "Operación" alone
    would be enough — and produce a different byte sequence with a different
    HMAC. Decoding the JSON *string value* is lossless; re-encoding the object
    is not.
    """
    if not isinstance(body, dict):
        raise IzipayError('Notificación con cuerpo inválido.')

    payload_http = body.get('payloadHttp')
    signature = body.get('signature')
    if not isinstance(payload_http, str) or not payload_http:
        raise IzipayError('Notificación sin payloadHttp.')
    if not isinstance(signature, str) or not signature:
        raise IzipayError('Notificación sin signature.')

    # Refused before any signature work: these codes describe a failed exchange
    # and never carry an authorisation.
    outer_code = body.get('code')
    if isinstance(outer_code, str) and outer_code.strip() in NON_SIGNED_RESPONSE_CODES:
        raise IzipayError('Notificación sin resultado de autorización.')

    if not verify_signature(payload_http, signature, credentials.hash_key):
        raise IzipayError('Firma inválida.')

    # --- from here on, and ONLY from here on, the content is authoritative ---
    try:
        signed = json.loads(payload_http)
    except (ValueError, TypeError):
        raise IzipayError('payloadHttp no es JSON válido.')
    if not isinstance(signed, dict):
        raise IzipayError('payloadHttp no es un objeto.')

    response = signed.get('response')
    if not isinstance(response, dict):
        raise IzipayError('payloadHttp sin objeto response.')

    orders = response.get('order')
    if not isinstance(orders, list) or not orders or not isinstance(orders[0], dict):
        raise IzipayError('payloadHttp sin datos de orden.')
    order_data = orders[0]

    transaction_id = str(signed.get('transactionId') or '').strip()
    if not transaction_id:
        raise IzipayError('payloadHttp sin transactionId.')

    raw_amount = str(order_data.get('amount') or '').strip()
    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, ValueError):
        raise IzipayError('Importe no numérico en payloadHttp.')

    merchant = response.get('merchant')
    merchant_code = ''
    if isinstance(merchant, dict):
        merchant_code = str(merchant.get('merchantCode') or '').strip()

    return NotificationResult(
        transaction_id=transaction_id,
        order_number=str(order_data.get('orderNumber') or '').strip(),
        amount=amount,
        currency=str(order_data.get('currency') or '').strip().upper(),
        merchant_code=merchant_code,
        response_code=str(signed.get('code') or '').strip(),
        pay_method=str(response.get('payMethod') or '').strip(),
        authorization_code=str(order_data.get('codeAuth') or '').strip(),
        reference_number=str(order_data.get('referenceNumber') or '').strip(),
        unique_id=str(order_data.get('uniqueId') or '').strip(),
        state_message=str(order_data.get('stateMessage') or '').strip(),
    )


def request_session_token(
    *,
    credentials: IzipayCredentials,
    transaction_id: str,
    payload: dict,
    timeout: float = 15.0,
) -> str:
    """
    Ask Izipay for the session token that authorises the SDK to render a form.

    THE ONLY NETWORK CALL IN THIS MODULE, and the only place the API key is
    used. It runs on the server because the key mints tokens: a browser holding
    it could mint them for any amount, for any order, forever.

    stdlib `urllib` rather than a new dependency. This is one JSON POST, and a
    hardening phase is a poor moment to widen the supply chain for the
    convenience of a nicer call signature.
    """
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        credentials.token_url,
        data=data,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            # Documented as required on this flow: the correlation id that ties
            # the token, the payment and the later notification together.
            'transactionId': transaction_id,
            'Authorization': credentials.api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8', 'replace')
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Deliberately not `str(exc)` into the caller's message: a urllib error
        # can quote the request, and the request carries the API key.
        logger.error('Izipay: fallo de red al pedir el token de sesión (%s)', type(exc).__name__)
        raise IzipayError('No se pudo contactar a la pasarela de pago.')

    try:
        parsed = json.loads(raw)
    except ValueError:
        raise IzipayError('Respuesta inválida de la pasarela de pago.')

    token = _extract_token(parsed)
    if not token:
        # The provider's own code, when it gave one, is useful to an operator
        # and harmless to log. The body is not logged: it is a credentialed
        # response.
        logger.error('Izipay: token de sesión no emitido (code=%s)', _extract_code(parsed))
        raise IzipayError('La pasarela de pago no emitió un token de sesión.')
    return token


def _extract_token(parsed) -> str:
    """
    Pull the session token out of the provider's envelope.

    Tolerant on PURPOSE, and only here. The token response is the one part of
    this integration whose exact envelope could not be read from a public page,
    so this accepts the documented shapes rather than inventing one and failing
    on a field name. Nothing security-relevant rests on it: a wrong guess yields
    no token and the payment simply does not start.
    """
    if not isinstance(parsed, dict):
        return ''
    for key in ('token', 'sessionToken', 'authorization'):
        value = parsed.get(key)
        if isinstance(value, str) and value:
            return value
    for container in ('response', 'answer', 'data', 'result'):
        nested = parsed.get(container)
        if isinstance(nested, dict):
            found = _extract_token(nested)
            if found:
                return found
    return ''


def _extract_code(parsed) -> str:
    if isinstance(parsed, dict):
        for key in ('code', 'codigo', 'status'):
            value = parsed.get(key)
            if isinstance(value, (str, int)):
                return str(value)
    return ''
