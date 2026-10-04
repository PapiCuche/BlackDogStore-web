"""
A fake Izipay, for tests. Nothing in the product imports this module.

WHY IT EXISTS
-------------
The integration has two halves and the suite only exercised one of them for
real. Notifications were always signed and verified with a real HMAC. The token
request never left a `patch()`: its URL, its two custom headers, its body and
the envelope of the reply had no test at all, because the only way to run them
was to hold credentials for Izipay's sandbox.

This is the other half. It stands where Izipay stands, on both sides:

  * as the TOKEN ENDPOINT. It replaces the socket, not the function. Everything
    `request_session_token` builds reaches `FakeIzipay.urlopen`, which checks it
    against the documented contract and answers the way Izipay answers. A
    request that breaks the contract gets no token, exactly as it would in
    production, and the reason is kept in `violations`.

  * as the NOTIFIER. It signs what it sends with the merchant's hash key, using
    its own HMAC and not the adapter's, so a bug in the adapter cannot sign its
    own homework. It can also send what an attacker or a broken network would
    send: a payload edited after signing, a signature made with another key, an
    envelope that contradicts the signed payload, the same message twice.

WHAT IT IS NOT
--------------
Not a payment provider and not a way to mark orders as paid outside a test: it
has no switch in settings and no route. The real sandbox still needs real
credentials; see `IzipaySandboxSmokeTest`.

NO REAL CREDENTIALS. The keys below are the strings the tests configure. They
open nothing.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import io
import json
import urllib.error
from decimal import Decimal
from unittest import mock

#: Where the adapter opens its only socket.
URLOPEN_PATH = 'store.payments.izipay.urllib.request.urlopen'

AUTHORIZED = '00'
DECLINED = 'A02'


class _Reply(io.BytesIO):
    """What `urlopen` returns: readable, and usable in a `with` block."""

    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeIzipay:
    """
    One merchant's account at the fake gateway.

    `token_mode` decides how the token endpoint answers:
        'ok'          {"code": "00", "response": {"token": ...}}
        'flat'        {"token": ...}
        'no_token'    a well-formed answer that carries no token
        'not_json'    an HTML error page
        'http_error'  HTTP 500
        'unreachable' the connection fails
        'timeout'     the gateway never answers
    """

    def __init__(self, *, merchant_code, api_key, hash_key, token_url, currency='PEN'):
        self.merchant_code = merchant_code
        self.api_key = api_key
        self.hash_key = hash_key
        self.token_url = token_url
        self.currency = currency
        self.token_mode = 'ok'
        self.token = 'fake-session-token'
        #: Every request the adapter made: {'url', 'method', 'headers', 'body', 'timeout'}.
        self.requests: list[dict] = []
        #: Why a request was refused. Empty when the adapter kept the contract.
        self.violations: list[str] = []
        self._unique = 1429000

    @classmethod
    def from_settings(cls, settings_dict: dict) -> 'FakeIzipay':
        """The account the test settings describe."""
        return cls(
            merchant_code=settings_dict['IZIPAY_MERCHANT_CODE'],
            api_key=settings_dict['IZIPAY_API_KEY'],
            hash_key=settings_dict['IZIPAY_HASH_KEY'],
            token_url=settings_dict['IZIPAY_TOKEN_URL'],
            currency=settings_dict.get('IZIPAY_CURRENCY', 'PEN'),
        )

    # -- the token endpoint ---------------------------------------------------

    @contextlib.contextmanager
    def online(self):
        """Put this fake where the socket is, for the duration of the block."""
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

        if self.token_mode == 'unreachable':
            raise urllib.error.URLError('connection refused')
        if self.token_mode == 'timeout':
            raise TimeoutError('timed out')
        if self.token_mode == 'http_error':
            raise urllib.error.HTTPError(request.full_url, 500, 'Server Error', {}, io.BytesIO(b''))
        if self.token_mode == 'not_json':
            return _Reply(b'<html><body>502 Bad Gateway</body></html>')

        broken = self._contract_violations(request, headers, body, timeout)
        if broken:
            self.violations.extend(broken)
            # What a gateway does with a request it does not accept: an error
            # code and no token. It never explains itself in detail.
            return _Reply(json.dumps({'code': '401', 'message': 'Solicitud no válida'}).encode())

        if self.token_mode == 'no_token':
            return _Reply(json.dumps({'code': '21', 'message': 'Comercio no habilitado'}).encode())
        if self.token_mode == 'flat':
            return _Reply(json.dumps({'token': self.token}).encode())
        return _Reply(json.dumps({
            'code': AUTHORIZED, 'message': 'OK', 'response': {'token': self.token},
        }).encode())

    def _contract_violations(self, request, headers, body, timeout) -> list[str]:
        """The token contract the adapter itself declares, one sentence per clause."""
        found = []
        if request.get_method() != 'POST':
            found.append('the token is requested with POST')
        if request.full_url != self.token_url:
            found.append('the token is requested at the configured URL')
        if not request.full_url.startswith('https://'):
            found.append('the token URL is HTTPS')
        if headers.get('authorization') != self.api_key:
            found.append('Authorization carries the API key, with no scheme in front')
        if 'application/json' not in headers.get('content-type', ''):
            found.append('the body is declared as JSON')
        if not timeout or timeout > 30:
            found.append('the call has a timeout')
        if not isinstance(body, dict):
            return found + ['the body is a JSON object']

        transaction_id = str(body.get('transactionId', ''))
        if not transaction_id:
            found.append('the body carries transactionId')
        if headers.get('transactionid') != transaction_id:
            found.append('the transactionId header equals the one in the body')
        if body.get('action') != 'pay':
            found.append('action is "pay"')
        if str(body.get('merchantCode', '')) != self.merchant_code:
            found.append('merchantCode is this merchant')

        order = body.get('order')
        if not isinstance(order, dict):
            return found + ['the body carries an order']
        if not str(order.get('orderNumber', '')):
            found.append('the order has an orderNumber')
        if order.get('currency') != self.currency:
            found.append('the order is in the merchant currency')
        amount = order.get('amount')
        if not isinstance(amount, str) or '.' not in amount or len(amount.rsplit('.', 1)[1]) != 2:
            found.append('the amount is a string with two decimals')
        else:
            try:
                if Decimal(amount) <= 0:
                    found.append('the amount is positive')
            except ArithmeticError:
                found.append('the amount is a number')
        if not order.get('processType'):
            found.append('the order has a processType')
        if not str(order.get('dateTimeTransaction', '')).isdigit():
            found.append('dateTimeTransaction is a numeric timestamp')

        # WHAT IS NOT CHECKED, on purpose: the rest of the body. Izipay's public
        # reference for Token/Generate could not be read (it is a client-side
        # application), so which further fields it requires is not known here.
        # Enforcing a guess would make the suite certify the guess. Only the
        # real sandbox settles it: see IzipaySandboxSmokeTest.
        return found

    # -- notifications --------------------------------------------------------

    def sign(self, payload_http: str, *, hash_key: str | None = None) -> str:
        """base64(HMAC-SHA256(hash key, payloadHttp)). Not the adapter's code."""
        key = (self.hash_key if hash_key is None else hash_key).encode('utf-8')
        return base64.b64encode(
            hmac.new(key, payload_http.encode('utf-8'), hashlib.sha256).digest()
        ).decode('ascii')

    def payload(self, *, transaction_id, order_number, amount, currency=None,
                code=AUTHORIZED, merchant_code=None, pay_method='CARD',
                include_merchant=True) -> str:
        """The exact string Izipay signs: `payloadHttp`."""
        self._unique += 1
        authorised = code == AUTHORIZED
        response = {
            'payMethod': pay_method,
            'order': [{
                'payMethodAuthorization': pay_method,
                'codeAuth': '831000' if authorised else '',
                'currency': currency or self.currency,
                'amount': str(amount),
                'installment': '00',
                'deferred': '0',
                'orderNumber': order_number,
                'stateMessage': 'Autorizado' if authorised else 'Rechazado',
                'dateTransaction': '20260902',
                'timeTransaction': '174837',
                'uniqueId': str(self._unique),
                'referenceNumber': str(self._unique + 4900000),
            }],
        }
        if include_merchant:
            response['merchant'] = {
                'merchantCode': self.merchant_code if merchant_code is None else merchant_code,
                'facilitatorCode': '',
            }
        return json.dumps({
            'code': code,
            'message': 'Operación exitosa' if authorised else 'Rechazado',
            'messageUser': 'Operación exitosa' if authorised else 'Rechazado',
            'messageUserEng': 'Successful' if authorised else 'Rejected',
            'transactionId': transaction_id,
            'response': response,
        }, ensure_ascii=False)

    def envelope(self, payload_http: str, *, signature: str | None = None) -> dict:
        """What Izipay POSTs: the signed string, its signature, and a copy in clear."""
        parsed = json.loads(payload_http)
        return {
            'code': parsed.get('code'),
            'message': parsed.get('message'),
            'messageUser': parsed.get('messageUser'),
            'messageUserEng': parsed.get('messageUserEng'),
            'response': parsed.get('response'),
            'payloadHttp': payload_http,
            'signature': self.sign(payload_http) if signature is None else signature,
            'transactionId': parsed.get('transactionId'),
        }

    def notification(self, attempt, *, code=AUTHORIZED, **overrides) -> dict:
        """A genuine notification about `attempt` (a PaymentTransaction)."""
        fields = {
            'transaction_id': attempt.transaction_id,
            'order_number': attempt.order_number,
            'amount': attempt.amount,
            'currency': attempt.currency or self.currency,
            'code': code,
        }
        fields.update(overrides)
        return self.envelope(self.payload(**fields))

    def tampered(self, envelope: dict, mutate) -> dict:
        """
        The same message with its signed payload edited and the ORIGINAL
        signature kept — what someone who intercepted it could send.

        `mutate` receives the decoded payload and changes it in place.
        """
        payload = json.loads(envelope['payloadHttp'])
        mutate(payload)
        forged = dict(envelope)
        forged['payloadHttp'] = json.dumps(payload, ensure_ascii=False)
        forged['response'] = payload.get('response')
        forged['code'] = payload.get('code')
        return forged

    def signed_by_someone_else(self, envelope: dict, *, hash_key='another-merchants-key') -> dict:
        """The same message, signed correctly with a key that is not ours."""
        forged = dict(envelope)
        forged['signature'] = self.sign(envelope['payloadHttp'], hash_key=hash_key)
        return forged

    def deliver(self, client, envelope, *, url='/api/payments/izipay/notification/'):
        """POST a notification the way Izipay does: raw JSON, no session."""
        return client.post(url, data=json.dumps(envelope), content_type='application/json')
