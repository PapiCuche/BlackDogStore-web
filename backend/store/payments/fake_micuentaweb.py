"""
A fake «Mi Cuenta Web» (Izipay, REST API V4) — a TEST HARNESS, not a provider.

It stands where the socket is and checks the contract the official
documentation states for `Charge/CreatePayment`, and it signs notifications the
way the gateway does. It also sends what somebody intercepting a message would
send: edited after signing, signed with another key, signed with the key meant
for the browser, replayed.

There is no setting that turns this on and no route that reaches it. It cannot
mark an order paid by any path a deployment exposes.

Contract source (official): https://secure.micuentaweb.pe/doc/es-PE/rest/V4.0/
  · POST {server}/api-payment/V4/Charge/CreatePayment
  · Authorization: Basic base64(<usuario>:<contraseña>)
  · amount as an integer in the smallest unit of the currency (S/ 1,80 → 180)
  · answer.formToken on success; status "ERROR" with answer.errorCode otherwise
  · IPN: form fields kr-answer, kr-hash, kr-hash-algorithm, kr-hash-key,
    kr-answer-type; kr-hash = hex HMAC-SHA256(kr-answer) with the PASSWORD
  · POST {server}/api-payment/V4/Order/Get with {"orderId": ...} and the same
    Authorization; answer.transactions is the list of what the gateway holds
    for that reference (a `V4/OrderTransactions`, which carries NO orderStatus)
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import io
import json
import re
import urllib.error
from unittest import mock

URLOPEN_PATH = 'urllib.request.urlopen'
CREATE_PATH = '/api-payment/V4/Charge/CreatePayment'
GET_ORDER_PATH = '/api-payment/V4/Order/Get'
#: What the documentation accepts in an `orderId`.
ORDER_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


class _Reply:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeMiCuentaWeb:
    """
    One shop at the fake gateway.

    `mode` decides how CreatePayment answers:
        'ok'          {"status": "SUCCESS", "answer": {"formToken": ...}}
        'error'       {"status": "ERROR", "answer": {"errorCode": "INT_905", ...}}
        'not_json'    an HTML error page
        'http_error'  HTTP 500
        'unreachable' the connection fails
        'timeout'     the gateway never answers

    `Order/Get` answers with whatever `hold()` left for that reference: an empty
    list when nothing was ever tried, or — with `order_get_mode = 'error'` — the
    error the documentation leaves unspecified for a reference it does not know.
    """

    def __init__(self, *, shop_id, password, hmac_key, api_url, currency='PEN'):
        self.shop_id = shop_id
        self.password = password
        self.hmac_key = hmac_key
        self.api_url = api_url.rstrip('/')
        self.currency = currency
        self.mode = 'ok'
        self.form_token = 'fake-form-token'
        self.requests: list[dict] = []
        self.violations: list[str] = []
        self.held: dict[str, list[dict]] = {}
        self.order_get_mode = 'ok'

    @classmethod
    def from_settings(cls, values: dict) -> 'FakeMiCuentaWeb':
        return cls(
            shop_id=values['MICUENTAWEB_SHOP_ID'], password=values['MICUENTAWEB_PASSWORD'],
            hmac_key=values.get('MICUENTAWEB_HMAC_KEY', 'fake-hmac-key'),
            api_url=values.get('MICUENTAWEB_API_URL', 'https://api.micuentaweb.pe'),
            currency=values.get('MICUENTAWEB_CURRENCY', 'PEN'),
        )

    @property
    def gateway_mode(self) -> str:
        return 'TEST' if self.password.startswith('testpassword_') else 'PRODUCTION'

    # -- CreatePayment --------------------------------------------------------

    @contextlib.contextmanager
    def online(self):
        with mock.patch(URLOPEN_PATH, side_effect=self.urlopen):
            yield self

    def urlopen(self, request, timeout=None):
        headers = {key.lower(): value for key, value in request.header_items()}
        try:
            body = json.loads((request.data or b'').decode('utf-8'))
        except ValueError:
            body = None
        self.requests.append({
            'url': request.full_url, 'method': request.get_method(),
            'headers': headers, 'body': body, 'timeout': timeout,
        })
        if self.mode == 'unreachable':
            raise urllib.error.URLError(ConnectionRefusedError(61, 'Connection refused'))
        if self.mode == 'timeout':
            raise TimeoutError('timed out')
        if self.mode == 'http_error':
            raise urllib.error.HTTPError(request.full_url, 500, 'Server Error', {}, io.BytesIO(b''))
        if self.mode == 'not_json':
            return _Reply(b'<html><body>502 Bad Gateway</body></html>')

        asking = request.full_url.endswith(GET_ORDER_PATH)
        broken = self._contract_violations(request, headers, body, timeout, asking=asking)
        if broken:
            self.violations.extend(broken)
            return _Reply(json.dumps({
                'status': 'ERROR',
                'answer': {'errorCode': 'INT_905', 'errorMessage': 'invalid credentials'},
            }).encode())
        if asking:
            return self._order_get(body)
        if self.mode == 'error':
            return _Reply(json.dumps({
                'status': 'ERROR',
                'answer': {'errorCode': 'PSP_099', 'errorMessage': 'too many results'},
            }).encode())
        return _Reply(json.dumps({
            'status': 'SUCCESS', 'answer': {'formToken': self.form_token, '_type': 'V4/Charge/PaymentForm'},
        }).encode())

    def _order_get(self, body) -> _Reply:
        if self.order_get_mode == 'error':
            return _Reply(json.dumps({
                'status': 'ERROR',
                'answer': {'errorCode': 'PSP_000', 'errorMessage': 'no such order'},
            }).encode())
        order_id = body['orderId']
        return _Reply(json.dumps({
            'status': 'SUCCESS', 'webService': 'Order/Get',
            'answer': {
                'shopId': self.shop_id, 'orderId': order_id,
                'transactions': self.held.get(order_id, []), '_type': 'V4/OrderTransactions',
            },
        }).encode())

    def _contract_violations(self, request, headers, body, timeout, *, asking=False) -> list[str]:
        found = []
        if request.get_method() != 'POST':
            found.append('the web services are called with POST')
        if asking:
            if request.full_url != self.api_url + GET_ORDER_PATH:
                found.append('an order is asked for at {server}/api-payment/V4/Order/Get')
        elif request.full_url != self.api_url + CREATE_PATH:
            found.append('the payment is created at {server}/api-payment/V4/Charge/CreatePayment')
        if not request.full_url.startswith('https://'):
            found.append('the server is HTTPS')
        expected = 'Basic ' + base64.b64encode(f'{self.shop_id}:{self.password}'.encode()).decode()
        if headers.get('authorization') != expected:
            found.append('Authorization is HTTP Basic with user:password')
        if 'application/json' not in headers.get('content-type', ''):
            found.append('the body is declared as JSON')
        if not timeout or timeout > 30:
            found.append('the call has a timeout')
        if not isinstance(body, dict):
            return found + ['the body is a JSON object']
        if asking:
            if not ORDER_ID.match(str(body.get('orderId') or '')):
                found.append('Order/Get takes the orderId, in the characters the documentation accepts')
            if set(body) - {'orderId', 'operationType'}:
                found.append('Order/Get takes orderId and operationType, nothing else')
            return found
        amount = body.get('amount')
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            found.append('amount is a positive INTEGER in the smallest unit of the currency')
        if body.get('currency') != self.currency:
            found.append('currency is the ISO code')
        if not str(body.get('orderId') or ''):
            found.append('the body carries orderId')
        return found

    # -- notifications --------------------------------------------------------

    def answer(self, *, order_id, amount_cents, status='PAID', currency=None, shop_id=None,
               mode=None, uuid='9d6b9c5d4f7e4f0e8a1c2b3d4e5f6a7b') -> dict:
        """The `V4/Payment` object the gateway describes a payment with."""
        detailed = {'PAID': 'AUTHORISED', 'UNPAID': 'REFUSED', 'RUNNING': 'AUTHORISED_TO_VALIDATE'}.get(status, status)
        return {
            'shopId': shop_id or self.shop_id,
            'orderCycle': 'OPEN' if status == 'RUNNING' else 'CLOSED',
            'orderStatus': status,
            'serverDate': '2026-10-05T15:00:00+00:00',
            'orderDetails': {
                'orderTotalAmount': amount_cents, 'orderEffectiveAmount': amount_cents,
                'orderCurrency': currency or self.currency, 'mode': mode or self.gateway_mode,
                'orderId': order_id, '_type': 'V4/OrderDetails',
            },
            'customer': {'email': 'ana@example.com', '_type': 'V4/Customer/Customer'},
            'transactions': [{
                'shopId': shop_id or self.shop_id, 'uuid': uuid, 'amount': amount_cents,
                'currency': currency or self.currency, 'paymentMethodType': 'CARD',
                'status': status, 'detailedStatus': detailed, 'operationType': 'DEBIT',
                'transactionDetails': {'cardDetails': {'authorizationResponse': {
                    'authorizationNumber': '3fe205', '_type': 'V4/PaymentMethod/Details/Cards/CardAuthorizationResponse',
                }}},
                '_type': 'V4/PaymentTransaction',
            }],
            '_type': 'V4/Payment',
        }

    # -- what the gateway holds for a reference (Order/Get) ---------------------

    def transaction(self, *, order_id, amount_cents, status='PAID', detailed=None, operation='DEBIT',
                    currency=None, shop_id=None, mode=None, order_total=None,
                    uuid='9d6b9c5d4f7e4f0e8a1c2b3d4e5f6a7b') -> dict:
        """One `V4/Transaction` of the list `Order/Get` returns."""
        detailed = detailed or {
            'PAID': 'AUTHORISED', 'UNPAID': 'REFUSED', 'RUNNING': 'AUTHORISED_TO_VALIDATE'}.get(status, status)
        return {
            'shopId': shop_id or self.shop_id, 'uuid': uuid, 'paymentMethodType': 'CARD',
            'status': status, 'detailedStatus': detailed, 'operationType': operation,
            'amount': amount_cents, 'currency': currency or self.currency,
            'creationDate': '2026-10-05T15:00:00+00:00',
            'orderDetails': {
                'orderTotalAmount': amount_cents if order_total is None else order_total,
                'orderEffectiveAmount': amount_cents if order_total is None else order_total,
                'orderCurrency': currency or self.currency, 'mode': mode or self.gateway_mode,
                'orderId': order_id, '_type': 'V4/OrderDetails',
            },
            'transactionDetails': {'cardDetails': {'authorizationResponse': {
                'authorizationNumber': '3fe205', '_type': 'V4/PaymentMethod/Details/Cards/CardAuthorizationResponse',
            }}},
            '_type': 'V4/Transaction',
        }

    def hold(self, order_id, *transactions) -> None:
        """What `Order/Get` will list for this reference from now on."""
        self.held[order_id] = list(transactions)

    @staticmethod
    def sign(kr_answer: str, key: str) -> str:
        """Hex HMAC-SHA256 of the exact `kr-answer` string. Computed here, not by the adapter."""
        return hmac.new(key.encode('utf-8'), kr_answer.encode('utf-8'), hashlib.sha256).hexdigest()

    def ipn(self, answer: dict, *, key=None, hash_key='password', algorithm='sha256_hmac',
            escaped_slashes=False) -> dict:
        """
        The form fields of an instant payment notification.

        `escaped_slashes`: the message travels with `\\/` where the signed text
        has `/`. The official sample (ipn.php) undoes exactly that before it
        computes the hash, so the hash here is of the text WITHOUT the escapes.
        """
        signed = json.dumps(answer, separators=(',', ':'), ensure_ascii=False)
        kr_answer = signed.replace('/', '\\/') if escaped_slashes else signed
        return {
            'kr-answer': kr_answer,
            'kr-hash': self.sign(signed, self.password if key is None else key),
            'kr-hash-algorithm': algorithm,
            'kr-hash-key': hash_key,
            'kr-answer-type': 'V4/Payment',
        }

    def ipn_edited_after_signing(self, answer: dict, **changes) -> dict:
        """Signed, then altered: what an interceptor can produce without the key."""
        form = self.ipn(answer)
        tampered = json.loads(form['kr-answer'])
        tampered['orderDetails'].update(changes)
        form['kr-answer'] = json.dumps(tampered, separators=(',', ':'), ensure_ascii=False)
        return form

    def browser_return(self, answer: dict) -> dict:
        """What the BROWSER is given after paying: signed with the HMAC key, not the password."""
        return self.ipn(answer, key=self.hmac_key, hash_key='sha256_hmac')
