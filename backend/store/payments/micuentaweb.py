"""
The «Mi Cuenta Web» adapter — Izipay's REST API V4 (Lyra / Krypton client).

    https://secure.micuentaweb.pe/doc/es-PE/rest/V4.0/javascript/redirection/presentation.html

A DIFFERENT OFFICIAL PRODUCT from the one `izipay.py` speaks to (the «SDK web /
Checkout» documented at developers.izipay.pe): other credentials, another
script in the browser, another way to create a payment and another signature.
An installation uses ONE of them, chosen by `PAYMENT_PROVIDER`; they are never
mixed in a checkout.

THE FLOW, AS THE DOCUMENTATION STATES IT
----------------------------------------
  1. SERVER  POST {server}/api-payment/V4/Charge/CreatePayment
             Authorization: Basic base64(usuario:contraseña)
             {"amount": <integer, smallest unit>, "currency": "PEN",
              "orderId": "...", "customer": {"email": "..."}}
             → {"status": "SUCCESS", "answer": {"formToken": "..."}}
  2. BROWSER the Krypton client draws the form with the PUBLIC key and the
             formToken. The card is typed into the gateway's fields; nothing of
             it reaches this backend.
  3. SERVER  the gateway POSTs the IPN (form fields) to our endpoint:
             kr-answer (a JSON string), kr-hash, kr-hash-algorithm,
             kr-hash-key, kr-answer-type.
             kr-hash = hex HMAC-SHA256(kr-answer) keyed with the PASSWORD.

WHAT IS BELIEVED
----------------
Only the IPN, and only after its signature verifies. The browser is ALSO handed
a signed copy of the answer after paying — signed with a different key, the
«clave HMAC-SHA-256» — and that copy is for the page, not for the ledger: this
adapter refuses anything not signed with the password (`kr-hash-key=password`),
so replaying the browser's copy to the notification endpoint pays nothing.

`kr-answer` IS NOT RE-SERIALISED. It is verified as the exact string received;
parsing and dumping it again would change its bytes and its HMAC.

VERIFIED WITHOUT CREDENTIALS: everything above, against a fake gateway that
checks the published contract. NOT VERIFIED until somebody runs the smoke test
with TEST keys (`MiCuentaWebSandboxSmokeTest`): the live endpoint's acceptance
of exactly this request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings

from .izipay import new_transaction_id

logger = logging.getLogger(__name__)

PROVIDER = 'micuentaweb'

DEFAULT_API_URL = 'https://api.micuentaweb.pe'
CREATE_PAYMENT_PATH = '/api-payment/V4/Charge/CreatePayment'

#: The mode is not a setting of its own: the gateway decides it by which keys
#: are used. A TEST password with a production public key (or the reverse) is a
#: deployment mistake that must stop the checkout, not surface as a declined card.
_PASSWORD_PREFIX = {'testpassword_': 'test', 'prodpassword_': 'production'}

#: `orderStatus` values of the `V4/Payment` object.
STATUS_PAID = 'PAID'
STATUS_UNPAID = 'UNPAID'


class MiCuentaWebError(Exception):
    """Talking to the gateway failed. Never carries a credential or a raw payload."""


@dataclass(frozen=True)
class MiCuentaWebCredentials:
    """
    One shop. `shop_id` and `public_key` are safe in a browser; `password` is
    not: it creates payments and verifies notifications, and never leaves the
    backend.
    """

    environment: str        # 'test' | 'production', derived from the keys
    shop_id: str
    password: str
    public_key: str
    api_url: str
    currency: str
    ipn_url: str

    @property
    def merchant_code(self) -> str:
        """The name the shared notification checks use for "our account"."""
        return self.shop_id

    @property
    def gateway_mode(self) -> str:
        """How the gateway names this environment inside a notification."""
        return 'TEST' if self.environment == 'test' else 'PRODUCTION'


def load_credentials() -> MiCuentaWebCredentials:
    """Read the configured shop, or refuse. Fails closed and names what is missing."""
    values = {
        'MICUENTAWEB_SHOP_ID': (getattr(settings, 'MICUENTAWEB_SHOP_ID', '') or '').strip(),
        'MICUENTAWEB_PASSWORD': (getattr(settings, 'MICUENTAWEB_PASSWORD', '') or '').strip(),
        'MICUENTAWEB_PUBLIC_KEY': (getattr(settings, 'MICUENTAWEB_PUBLIC_KEY', '') or '').strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise MiCuentaWebError('Mi Cuenta Web no está configurado. Faltan: ' + ', '.join(missing) + '.')

    password = values['MICUENTAWEB_PASSWORD']
    environment = next(
        (mode for prefix, mode in _PASSWORD_PREFIX.items() if password.startswith(prefix)), None,
    )
    if environment is None:
        raise MiCuentaWebError(
            'MICUENTAWEB_PASSWORD debe ser la contraseña de TEST (testpassword_…) o la de '
            'PRODUCCIÓN (prodpassword_…).'
        )

    shop_id = values['MICUENTAWEB_SHOP_ID']
    public_key = values['MICUENTAWEB_PUBLIC_KEY']
    owner, _, key = public_key.partition(':')
    if owner != shop_id or not key:
        raise MiCuentaWebError('MICUENTAWEB_PUBLIC_KEY debe tener la forma <usuario>:<clave pública>.')
    if key.startswith('testpublickey_') != (environment == 'test'):
        raise MiCuentaWebError(
            'MICUENTAWEB_PASSWORD y MICUENTAWEB_PUBLIC_KEY son de entornos distintos (TEST y PRODUCCIÓN).'
        )

    api_url = (getattr(settings, 'MICUENTAWEB_API_URL', '') or DEFAULT_API_URL).strip().rstrip('/')
    if not api_url.startswith('https://'):
        raise MiCuentaWebError('MICUENTAWEB_API_URL debe ser https.')

    return MiCuentaWebCredentials(
        environment=environment, shop_id=shop_id, password=password, public_key=public_key,
        api_url=api_url,
        currency=(getattr(settings, 'MICUENTAWEB_CURRENCY', '') or 'PEN').strip().upper(),
        ipn_url=(getattr(settings, 'MICUENTAWEB_IPN_URL', '') or '').strip(),
    )


def new_order_id() -> str:
    """
    A fresh `orderId`, unique per ATTEMPT and generated here.

    It is what the notification is resolved by, and the possession token the
    payment-status page presents, so it is never accepted from a client.
    """
    return new_transaction_id()


def amount_in_cents(amount: Decimal) -> int:
    """
    Money as this API takes it: an INTEGER in the smallest unit (S/ 1,80 → 180).

    From `Order.total` quantised once. Never through a float: 149.90 * 100 is
    14989.999… in binary, and that cent is a payment that fails its own check.
    """
    return int((Decimal(amount).quantize(Decimal('0.01')) * 100).to_integral_value())


def sign(kr_answer: str, key: str) -> str:
    """hex( HMAC-SHA256( kr-answer, key ) ) — the documented algorithm."""
    return hmac.new(key.encode('utf-8'), kr_answer.encode('utf-8'), hashlib.sha256).hexdigest()


def verify_signature(kr_answer: str, kr_hash: str, key: str) -> bool:
    """
    Constant-time, on bytes. An empty key verifies nothing: a deployment that
    lost its password must reject notifications, not accept whatever arrives.
    """
    if not key or not kr_hash or not kr_answer:
        return False
    return hmac.compare_digest(
        sign(kr_answer, key).encode('ascii'), str(kr_hash).strip().lower().encode('utf-8'),
    )


def create_payment(*, credentials: MiCuentaWebCredentials, order_id: str, amount: Decimal,
                   customer_email: str = '', timeout: float = 15.0) -> str:
    """
    Ask the gateway for the `formToken` that lets the browser draw ONE payment.

    The only network call in this module and the only place the password is
    used. It runs on the server because the password creates payments: a
    browser holding it could create them for any amount.
    """
    body = {
        'amount': amount_in_cents(amount),
        'currency': credentials.currency,
        'orderId': order_id,
    }
    if customer_email:
        body['customer'] = {'email': customer_email}
    if credentials.ipn_url:
        body['ipnTargetUrl'] = credentials.ipn_url

    token = base64.b64encode(f'{credentials.shop_id}:{credentials.password}'.encode('utf-8')).decode('ascii')
    request = urllib.request.Request(
        credentials.api_url + CREATE_PAYMENT_PATH,
        data=json.dumps(body).encode('utf-8'),
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Basic {token}',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8', 'replace')
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.error('Mi Cuenta Web: fallo de red al crear el pago (%s)', type(exc).__name__)
        raise MiCuentaWebError('No se pudo contactar a la pasarela de pago.') from None

    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.error('Mi Cuenta Web: la respuesta al crear el pago no es JSON')
        raise MiCuentaWebError('Respuesta inválida de la pasarela de pago.') from None

    answer = parsed.get('answer') if isinstance(parsed, dict) else None
    form_token = answer.get('formToken') if isinstance(answer, dict) else None
    if not isinstance(parsed, dict) or parsed.get('status') != 'SUCCESS' or not form_token:
        code = answer.get('errorCode') if isinstance(answer, dict) else ''
        # The code, never the message: gateway messages can echo the request.
        logger.error('Mi Cuenta Web: pago no creado (errorCode=%s)', str(code or '-')[:20])
        raise MiCuentaWebError('La pasarela de pago no creó el pago.')
    return str(form_token)


@dataclass(frozen=True)
class NotificationResult:
    """
    An IPN, reduced to what came out of the SIGNED `kr-answer`.

    Same attribute names as the Checkout adapter's result, so the code that
    decides whether an order may be paid is one piece of code for both.
    """

    transaction_id: str      # our orderId
    order_number: str        # our orderId
    amount: Decimal          # in soles, from the integer the gateway signed
    currency: str
    merchant_code: str       # shopId
    response_code: str       # orderStatus
    pay_method: str
    authorization_code: str
    reference_number: str
    unique_id: str           # the gateway's transaction uuid
    state_message: str       # detailedStatus
    mode: str                # TEST | PRODUCTION

    @property
    def authorized(self) -> bool:
        return self.response_code == STATUS_PAID

    @property
    def settled(self) -> bool:
        """Whether this is an ANSWER about the payment, and not news that it is still open."""
        return self.response_code in (STATUS_PAID, STATUS_UNPAID)


def parse_notification(form, credentials: MiCuentaWebCredentials) -> NotificationResult:
    """
    Verify an IPN and return what it actually says. Raises `MiCuentaWebError`
    for anything that is not a well-formed message signed with the PASSWORD.
    """
    def field(name: str) -> str:
        value = form.get(name) if hasattr(form, 'get') else None
        return value if isinstance(value, str) else ''

    kr_answer = field('kr-answer')
    kr_hash = field('kr-hash')
    if not kr_answer or not kr_hash:
        raise MiCuentaWebError('Notificación sin kr-answer o sin kr-hash.')
    if field('kr-hash-algorithm') != 'sha256_hmac':
        raise MiCuentaWebError('Algoritmo de firma no admitido.')
    # `password` is the key of the server-to-server notification. The browser's
    # copy says `sha256_hmac` here and is signed with another key: not ours to
    # believe on this endpoint.
    if field('kr-hash-key') != 'password':
        raise MiCuentaWebError('La notificación no viene firmada con la contraseña.')
    if not verify_signature(kr_answer, kr_hash, credentials.password):
        raise MiCuentaWebError('Firma inválida.')

    try:
        answer = json.loads(kr_answer)
    except (ValueError, TypeError):
        raise MiCuentaWebError('kr-answer no es JSON válido.') from None
    if not isinstance(answer, dict):
        raise MiCuentaWebError('kr-answer no es un objeto.')

    details = answer.get('orderDetails')
    if not isinstance(details, dict):
        raise MiCuentaWebError('kr-answer sin orderDetails.')

    order_id = str(details.get('orderId') or '').strip()
    if not order_id:
        raise MiCuentaWebError('kr-answer sin orderId.')

    cents = details.get('orderTotalAmount')
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise MiCuentaWebError('Importe no entero en kr-answer.')
    try:
        amount = (Decimal(cents) / 100).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise MiCuentaWebError('Importe inválido en kr-answer.') from None

    # A TEST payment is not money. With production keys configured, an answer
    # that says TEST (or says nothing) cannot pay an order, however it is signed.
    mode = str(details.get('mode') or '').strip().upper()
    if mode != credentials.gateway_mode:
        raise MiCuentaWebError('La notificación es de otro entorno (TEST / PRODUCCIÓN).')

    status = str(answer.get('orderStatus') or '').strip().upper()
    if not status:
        raise MiCuentaWebError('kr-answer sin orderStatus.')

    transactions = answer.get('transactions')
    last = transactions[0] if isinstance(transactions, list) and transactions and isinstance(transactions[0], dict) else {}
    card = (last.get('transactionDetails') or {}).get('cardDetails') if isinstance(last.get('transactionDetails'), dict) else None
    authorization = (card or {}).get('authorizationResponse') if isinstance(card, dict) else None

    return NotificationResult(
        transaction_id=order_id,
        order_number=order_id,
        amount=amount,
        currency=str(details.get('orderCurrency') or '').strip().upper(),
        merchant_code=str(answer.get('shopId') or '').strip(),
        response_code=status,
        pay_method=str(last.get('paymentMethodType') or '').strip(),
        authorization_code=str((authorization or {}).get('authorizationNumber') or '').strip()
        if isinstance(authorization, dict) else '',
        reference_number='',
        unique_id=str(last.get('uuid') or '').strip(),
        state_message=str(last.get('detailedStatus') or '').strip(),
        mode=mode,
    )
