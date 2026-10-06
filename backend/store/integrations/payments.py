"""
Payment gateways, as the checkout sees them.

The checkout does not know Izipay. It asks here for THE payment adapter that is
active and talks to it through four things: load the credentials, open an
attempt, describe the session to the browser, and (in the notification views)
recognise its own notifications.

    CheckoutService  →  active adapter  →  Izipay «SDK web / Checkout»
                                            Izipay «Mi Cuenta Web»
                                            …the next one, registered here

ONE AT A TIME. `active_code()` is the single place that decides which: the
console when a master has activated a payment provider there, the
`PAYMENT_PROVIDER` setting otherwise. A new gateway is an adapter registered
here plus its provider declaration in `providers/payments.py`; the checkout, the
orders and the notification views do not change.

The code stored on each payment attempt (`PaymentTransaction.provider`) is the
adapter's `code`, and is what a notification is matched against.
"""
from django.conf import settings

from ..models import IntegrationConfig
from ..payments import izipay, micuentaweb

_ADAPTERS = {}


class PaymentAdapter:
    #: Stored on every attempt and used in the notification route.
    code = ''
    #: Its declaration in the provider registry (the console).
    provider_id = ''
    credentials_type = None
    error_type = Exception

    def load_credentials(self):
        raise NotImplementedError

    def start_attempt(self, order, credentials):
        raise NotImplementedError

    def session_payload(self, payment) -> dict:
        raise NotImplementedError


class IzipayCheckoutAdapter(PaymentAdapter):
    code = izipay.PROVIDER
    provider_id = izipay.CONSOLE_PROVIDER
    credentials_type = izipay.IzipayCredentials
    error_type = izipay.IzipayError

    def load_credentials(self):
        return izipay.load_credentials()

    def start_attempt(self, order, credentials):
        from .. import checkout_services
        return checkout_services._start_izipay_attempt(order, credentials)

    def session_payload(self, payment) -> dict:
        return {
            'provider': payment.provider,
            'environment': payment.environment,
            'transaction_id': payment.transaction_id,
            'authorization': payment.authorization,
            'merchant_code': payment.merchant_code,
            'public_key': payment.public_key,
            'config': payment.config,
        }


class MiCuentaWebAdapter(PaymentAdapter):
    code = micuentaweb.PROVIDER
    provider_id = micuentaweb.CONSOLE_PROVIDER
    credentials_type = micuentaweb.MiCuentaWebCredentials
    error_type = micuentaweb.MiCuentaWebError

    def load_credentials(self):
        return micuentaweb.load_credentials()

    def start_attempt(self, order, credentials):
        from .. import checkout_services
        return checkout_services._start_micuentaweb_attempt(order, credentials)

    def session_payload(self, payment) -> dict:
        # One shape per product, never the union: a browser told `micuentaweb`
        # gets a form token and a public key, and none of the other SDK's fields.
        return {
            'provider': payment.provider,
            'environment': payment.environment,
            'transaction_id': payment.transaction_id,
            'form_token': payment.form_token,
            'public_key': payment.public_key,
        }


def register(adapter: PaymentAdapter) -> None:
    _ADAPTERS[adapter.code] = adapter


register(IzipayCheckoutAdapter())
register(MiCuentaWebAdapter())

#: What loading any adapter's credentials may raise.
ERRORS = tuple(adapter.error_type for adapter in _ADAPTERS.values())


def adapter(code: str):
    return _ADAPTERS.get(code)


def adapter_for(credentials):
    for candidate in _ADAPTERS.values():
        if isinstance(credentials, candidate.credentials_type):
            return candidate
    raise LookupError('No payment adapter takes these credentials.')


def by_provider_id(provider_id: str):
    return next((a for a in _ADAPTERS.values() if a.provider_id == provider_id), None)


def active_code() -> str:
    """
    The code of the one adapter that charges now, or '' for none.

    Once the console holds an active row for ANY payment provider it is the
    console that decides — including «all of them switched off», which is none,
    not a fall back to the environment.
    """
    ids = {a.provider_id: a.code for a in _ADAPTERS.values()}
    rows = list(IntegrationConfig.objects.filter(
        provider__in=ids, slot=IntegrationConfig.SLOT_ACTIVE, company__isnull=True,
    ).values_list('provider', 'enabled'))
    if rows:
        return next((ids[provider] for provider, enabled in rows if enabled), '')
    return (getattr(settings, 'PAYMENT_PROVIDER', '') or '').strip().lower()
