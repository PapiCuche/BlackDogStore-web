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
  4. SERVER  when that notification never came: POST {server}/api-payment/V4/
             Order/Get {"orderId": "..."} with the same Authorization lists
             the transactions the gateway holds for the reference.

WHAT IS BELIEVED
----------------
The IPN, and only after its signature verifies. The browser is ALSO handed
a signed copy of the answer after paying — signed with a different key, the
«clave HMAC-SHA-256» — and that copy is for the page, not for the ledger: this
adapter refuses anything not signed with the password (`kr-hash-key=password`),
so replaying the browser's copy to the notification endpoint pays nothing.

And what the gateway answers when THIS SERVER asks it (`Order/Get`), over TLS
and with the password. That answer carries no signature and no `orderStatus`,
so it is read more narrowly than a notification: see `parse_order_transactions`.

`kr-answer` IS NOT RE-SERIALISED. It is verified as the exact string received;
parsing and dumping it again would change its bytes and its HMAC. The one
reading besides that is the official sample's own (ipn.php): `\\/` turned back
into `/` before the hash. What is parsed is always the text the hash covered.

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
GET_ORDER_PATH = '/api-payment/V4/Order/Get'

#: The mode is not a setting of its own: the gateway decides it by which keys
#: are used. A TEST password with a production public key (or the reverse) is a
#: deployment mistake that must stop the checkout, not surface as a declined card.
_PASSWORD_PREFIX = {'testpassword_': 'test', 'prodpassword_': 'production'}

#: `orderStatus` values of the `V4/Payment` object.
STATUS_PAID = 'PAID'
STATUS_UNPAID = 'UNPAID'
STATUS_RUNNING = 'RUNNING'

#: The `detailedStatus` of a debit whose money is held or already captured.
#: A transaction's `status` is also PAID for a card verification (ACCEPTED) and
#: for a pre-authorisation (PRE_AUTHORISED), and those are never captured.
MONEY_STATUSES = ('AUTHORISED', 'CAPTURED')
OPERATION_DEBIT = 'DEBIT'


class MiCuentaWebError(Exception):
    """Talking to the gateway failed. Never carries a credential or a raw payload."""


class MiCuentaWebUnreachable(MiCuentaWebError):
    """The gateway did not answer, or answered that it is the one that is broken."""


def _is_refusal(exc) -> bool:
    """An HTTP 4xx is the gateway answering no. Everything else is no answer."""
    return isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500


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


_CONSOLE_FIELDS = {
    'shop_id': 'MICUENTAWEB_SHOP_ID', 'password': 'MICUENTAWEB_PASSWORD', 'public_key': 'MICUENTAWEB_PUBLIC_KEY',
    'api_url': 'MICUENTAWEB_API_URL', 'currency': 'MICUENTAWEB_CURRENCY', 'ipn_url': 'MICUENTAWEB_IPN_URL',
}
CONSOLE_PROVIDER = 'izipay_micuentaweb'


def _configured_values() -> dict:
    """The console first, the `MICUENTAWEB_*` settings as the fallback. Read on every call."""
    from ..integrations import secret_store, service

    try:
        config = service.resolve_panel(CONSOLE_PROVIDER)
    except secret_store.SecretStoreError as exc:
        raise MiCuentaWebError(str(exc)) from None
    if config is not None:
        return {name: config.get(field, '') for field, name in _CONSOLE_FIELDS.items()}
    if service.has_panel_row(CONSOLE_PROVIDER):
        # Switched off in the console. The environment is not a way round that.
        raise MiCuentaWebError('Esta pasarela está desactivada en Configuración › Integraciones.')
    return {name: getattr(settings, name, '') for name in _CONSOLE_FIELDS.values()}


def load_credentials() -> MiCuentaWebCredentials:
    """Read the configured shop, or refuse. Fails closed and names what is missing."""
    return credentials_from(_configured_values())


def credentials_from(configured: dict) -> MiCuentaWebCredentials:
    """The same rules for a shop from the console and for one from the environment."""
    values = {
        name: (configured.get(name) or '').strip()
        for name in ('MICUENTAWEB_SHOP_ID', 'MICUENTAWEB_PASSWORD', 'MICUENTAWEB_PUBLIC_KEY')
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

    api_url = (configured.get('MICUENTAWEB_API_URL') or DEFAULT_API_URL).strip().rstrip('/')
    if not api_url.startswith('https://'):
        raise MiCuentaWebError('MICUENTAWEB_API_URL debe ser https.')

    return MiCuentaWebCredentials(
        environment=environment, shop_id=shop_id, password=password, public_key=public_key,
        api_url=api_url,
        currency=(configured.get('MICUENTAWEB_CURRENCY') or 'PEN').strip().upper(),
        ipn_url=(configured.get('MICUENTAWEB_IPN_URL') or '').strip(),
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
    try:
        expected = sign(kr_answer, key).encode('ascii')
        received = str(kr_hash).strip().lower().encode('utf-8')
    except UnicodeError:
        return False
    return hmac.compare_digest(expected, received)


def signed_text(kr_answer: str, kr_hash: str, key: str) -> str | None:
    """
    The text this hash covers, or None when it covers nothing that was received.

    As received, first. Then as the official sample computes it (ipn.php):
    `str_replace('\\/', '/', kr-answer)` before the HMAC, because a message may
    travel with its slashes escaped. Both are the same JSON; whichever one the
    key signed is the one that gets parsed, so nothing unsigned is ever read.
    """
    if verify_signature(kr_answer, kr_hash, key):
        return kr_answer
    unescaped = kr_answer.replace('\\/', '/')
    if unescaped != kr_answer and verify_signature(unescaped, kr_hash, key):
        return unescaped
    return None


def _post(credentials: MiCuentaWebCredentials, path: str, body: dict, *, timeout: float, doing: str) -> dict:
    """
    One call to a web service of the gateway, and its answer as a dict.

    The only network code in this module and the only place the password is
    used. It runs on the server because the password creates payments: a
    browser holding it could create them for any amount.
    """
    token = base64.b64encode(f'{credentials.shop_id}:{credentials.password}'.encode('utf-8')).decode('ascii')
    request = urllib.request.Request(
        credentials.api_url + path,
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
        logger.error('Mi Cuenta Web: fallo de red al %s (%s)', doing, type(exc).__name__)
        kind = MiCuentaWebError if _is_refusal(exc) else MiCuentaWebUnreachable
        raise kind('No se pudo contactar a la pasarela de pago.') from None

    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.error('Mi Cuenta Web: la respuesta al %s no es JSON', doing)
        raise MiCuentaWebUnreachable('Respuesta inválida de la pasarela de pago.') from None
    return parsed if isinstance(parsed, dict) else {}


def create_payment(*, credentials: MiCuentaWebCredentials, order_id: str, amount: Decimal,
                   customer_email: str = '', timeout: float = 15.0) -> str:
    """Ask the gateway for the `formToken` that lets the browser draw ONE payment."""
    body = {
        'amount': amount_in_cents(amount),
        'currency': credentials.currency,
        'orderId': order_id,
    }
    if customer_email:
        body['customer'] = {'email': customer_email}
    if credentials.ipn_url:
        body['ipnTargetUrl'] = credentials.ipn_url

    parsed = _post(credentials, CREATE_PAYMENT_PATH, body, timeout=timeout, doing='crear el pago')
    answer = parsed.get('answer')
    form_token = answer.get('formToken') if isinstance(answer, dict) else None
    if parsed.get('status') != 'SUCCESS' or not form_token:
        code = answer.get('errorCode') if isinstance(answer, dict) else ''
        # The code, never the message: gateway messages can echo the request.
        logger.error('Mi Cuenta Web: pago no creado (errorCode=%s)', str(code or '-')[:20])
        raise MiCuentaWebError('La pasarela de pago no creó el pago.')
    return str(form_token)


def get_order_transactions(*, credentials: MiCuentaWebCredentials, order_id: str,
                           timeout: float = 15.0) -> dict:
    """
    Ask the gateway what it holds for ONE reference (`Order/Get`).

    Returns its `answer` (a `V4/OrderTransactions`), unread. An answer that is
    an error is `MiCuentaWebError`: the documentation does not say which error
    means «nothing under that reference», so none of them is taken for it.
    """
    parsed = _post(credentials, GET_ORDER_PATH, {'orderId': order_id}, timeout=timeout,
                   doing='consultar el pedido')
    answer = parsed.get('answer')
    if parsed.get('status') != 'SUCCESS' or not isinstance(answer, dict):
        code = answer.get('errorCode') if isinstance(answer, dict) else ''
        logger.warning('Mi Cuenta Web: consulta sin respuesta útil (errorCode=%s)', str(code or '-')[:20])
        raise MiCuentaWebError('La pasarela de pago no respondió a la consulta.')
    return answer


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
    #: False for what the gateway answered when asked (`Order/Get`): believed
    #: for whom it was asked of, not for a signature. Recorded as such.
    signed: bool = True

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
    signed = signed_text(kr_answer, kr_hash, credentials.password)
    if signed is None:
        raise MiCuentaWebError('Firma inválida.')

    try:
        answer = json.loads(signed)
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


def parse_order_transactions(answer, credentials: MiCuentaWebCredentials, order_id: str):
    """
    Reduce an `Order/Get` answer to the result a notification would have given,
    or None when the gateway holds nothing for the reference.

    READ MORE NARROWLY THAN A NOTIFICATION, because it has less in it: no
    signature and no `orderStatus`, only the list of transactions. So it is the
    transactions that have to say «paid», and anything they leave open raises
    `MiCuentaWebError` and decides nothing:

      * the answer is about OUR shop and THIS reference, and so is every
        transaction in it;
      * every transaction is of the environment the configured keys are of — a
        TEST transaction is not money for a production shop, nor the reverse;
      * only debits. A refund or a card verification under the reference is a
        story this cannot tell on its own;
      * paid means ONE debit with `status` PAID whose `detailedStatus` says the
        money is held or captured, for the whole amount of its order.

    The amount, the currency and the shop it returns are then compared with
    what the shop opened by the same code that judges a notification.
    """
    if not isinstance(answer, dict):
        raise MiCuentaWebError('La respuesta de la consulta no es un objeto.')
    shop_id = str(answer.get('shopId') or '').strip()
    if shop_id != credentials.shop_id:
        raise MiCuentaWebError('La respuesta de la consulta es de otra tienda.')
    if str(answer.get('orderId') or '').strip() != order_id:
        raise MiCuentaWebError('La respuesta de la consulta es de otra referencia.')
    transactions = answer.get('transactions')
    if not isinstance(transactions, list):
        raise MiCuentaWebError('La respuesta de la consulta no trae transacciones.')
    if not transactions:
        return None

    read = []
    for transaction in transactions:
        details = transaction.get('orderDetails') if isinstance(transaction, dict) else None
        if not isinstance(details, dict):
            raise MiCuentaWebError('Transacción sin orderDetails en la consulta.')
        if str(details.get('orderId') or '').strip() != order_id:
            raise MiCuentaWebError('Transacción de otra referencia en la consulta.')
        if str(transaction.get('shopId') or shop_id).strip() != shop_id:
            raise MiCuentaWebError('Transacción de otra tienda en la consulta.')
        mode = str(details.get('mode') or '').strip().upper()
        if mode != credentials.gateway_mode:
            raise MiCuentaWebError('La transacción es de otro entorno (TEST / PRODUCCIÓN).')
        if str(transaction.get('operationType') or '').strip().upper() != OPERATION_DEBIT:
            raise MiCuentaWebError('Hay una operación que no es un cobro bajo esta referencia.')
        cents, total = transaction.get('amount'), details.get('orderTotalAmount')
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (cents, total)):
            raise MiCuentaWebError('Importe no entero en la consulta.')
        read.append((transaction, details, str(transaction.get('status') or '').strip().upper()))

    paid = [entry for entry in read if entry[2] == STATUS_PAID]
    if len(paid) > 1:
        raise MiCuentaWebError('Hay más de un cobro pagado bajo esta referencia.')
    if paid:
        transaction, details, status = paid[0]
        if str(transaction.get('detailedStatus') or '').strip().upper() not in MONEY_STATUSES:
            raise MiCuentaWebError('La transacción pagada no es un cobro autorizado ni capturado.')
        if transaction['amount'] != details['orderTotalAmount']:
            raise MiCuentaWebError('El cobro no es por el importe completo del pedido.')
        charged = str(transaction.get('currency') or '').strip().upper()
        if charged != str(details.get('orderCurrency') or '').strip().upper():
            raise MiCuentaWebError('El cobro y su pedido no están en la misma moneda.')
    elif all(entry[2] == STATUS_UNPAID for entry in read):
        transaction, details, status = read[-1]
    else:
        # Something is still open at the gateway: not an answer yet.
        transaction, details, status = read[-1][0], read[-1][1], STATUS_RUNNING

    card = (transaction.get('transactionDetails') or {}).get('cardDetails') if isinstance(
        transaction.get('transactionDetails'), dict) else None
    authorization = (card or {}).get('authorizationResponse') if isinstance(card, dict) else None
    return NotificationResult(
        transaction_id=order_id,
        order_number=order_id,
        amount=(Decimal(details['orderTotalAmount']) / 100).quantize(Decimal('0.01')),
        currency=str(details.get('orderCurrency') or '').strip().upper(),
        merchant_code=shop_id,
        response_code=status,
        pay_method=str(transaction.get('paymentMethodType') or '').strip(),
        authorization_code=str((authorization or {}).get('authorizationNumber') or '').strip()
        if isinstance(authorization, dict) else '',
        reference_number='',
        unique_id=str(transaction.get('uuid') or '').strip(),
        state_message=str(transaction.get('detailedStatus') or '').strip(),
        mode=credentials.gateway_mode,
        signed=False,
    )
