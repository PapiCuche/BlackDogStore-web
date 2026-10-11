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
from django.db import connection

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


def environment_of(credentials) -> str:
    """
    'test' or 'production', whatever word each product uses for it.

    Anything that does not say PRODUCTION is TEST: a payment is only money when
    its keys say so.
    """
    return 'production' if getattr(credentials, 'environment', '') == 'production' else 'test'


def configured_in_console(candidate: PaymentAdapter) -> bool:
    """Whether what this adapter charges with was put there by a master, in the console."""
    return IntegrationConfig.objects.filter(
        provider=candidate.provider_id, slot=IntegrationConfig.SLOT_ACTIVE, company__isnull=True,
    ).exists()


def test_keys_of_the_console(candidate: PaymentAdapter, credentials) -> bool:
    """
    TEST keys that a master activated in the console — on a shop that may be
    published. A payment made with them is not money, and the cards that make
    one are public: such a configuration is for whoever is testing it, not for
    buyers.

    Keys read from the ENVIRONMENT are not this: that is how the simulated
    gateways of the test suites and of the rehearsal are set up, on
    installations that are not a shop.
    """
    return environment_of(credentials) == 'test' and configured_in_console(candidate)


#: One number for «the payment configuration of this installation», as
#: PostgreSQL's advisory locks want it.
_CONFIGURATION_LOCK = 0x70617931            # 'pay1'


def lock_configuration(*, exclusive: bool) -> None:
    """
    Hold the payment configuration still until this transaction ends.

    A change of gateway takes it EXCLUSIVE; opening an attempt takes it SHARED.
    So a change sees every attempt that was opened before it, and an attempt
    that comes after it is opened against what the change left — never a
    checkout that read the old gateway and wrote its attempt under the new one.

    PostgreSQL only. Elsewhere (the SQLite of a development machine) it does
    nothing: there is one writer at a time there anyway, and no shop.
    """
    if connection.vendor != 'postgresql':
        return
    function = 'pg_advisory_xact_lock' if exclusive else 'pg_advisory_xact_lock_shared'
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {function}(%s)', [_CONFIGURATION_LOCK])


def still_charging(credentials) -> bool:
    """Whether these credentials, loaded a moment ago, are still exactly what charges."""
    try:
        candidate = adapter_for(credentials)
        return active_code() == candidate.code and candidate.load_credentials() == credentials
    except (LookupError, *ERRORS):
        return False
