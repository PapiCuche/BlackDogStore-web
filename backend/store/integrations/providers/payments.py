"""
Izipay's two products, as the console configures them.

They are two providers of the `payments` category, and that category is
exclusive: activating one switches the other off in the same act. Which one is
active is what the checkout asks (`store.integrations.payments.active_code`).

TEST AND PRODUCTION ARE NEVER GUESSED. «SDK web / Checkout» says its environment
in a field; «Mi Cuenta Web» says it in the keys themselves (testpassword_… /
prodpassword_…). Activating a PRODUCTION configuration takes the word
PRODUCCION typed by the master: it cannot happen by leaving a default.

THE TEST CHARGES NOTHING. It asks the gateway's TEST environment for what a
checkout would ask first — a session token, or a form token for S/ 1.80 in test
mode — and stops. Production keys are checked for coherence only and are never
sent anywhere from here.
"""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.utils import timezone

from ...payments import izipay, micuentaweb
from .. import registry
from ..registry import ConfigError, Field, Provider, TestOutcome

PRODUCTION_WORD = 'PRODUCCION'
#: How long a buyer may reasonably still be at the card form.
IN_FLIGHT = timedelta(hours=1)


class _PaymentProvider(Provider):
    category = 'payments'
    scope = registry.SCOPE_PLATFORM
    exclusive_in_category = True
    supported_modes = ('test', 'production')
    #: `PAYMENT_PROVIDER` value of the legacy environment configuration.
    legacy_code = ''

    def activation_confirmation(self, public, secrets):
        if self.mode(public, secrets) != 'production':
            return None
        return PRODUCTION_WORD, (
            f'Vas a activar claves de PRODUCCIÓN: se cobrará dinero real. Escribe {PRODUCTION_WORD} para confirmarlo.')

    def interruption_warning(self, company=None) -> str:
        """Buyers who are at the card form right now, opened through this product."""
        from ...models import PaymentTransaction

        opened = PaymentTransaction.objects.filter(
            provider=self.legacy_code, status=PaymentTransaction.Status.PENDING,
            created_at__gte=timezone.now() - IN_FLIGHT,
        ).count()
        if not opened:
            return ''
        charges = '1 cobro abierto' if opened == 1 else f'{opened} cobros abiertos'
        return (f'Hay {charges} con {self.label} en la última hora. Si el comprador termina de pagar después de '
                'este cambio, la notificación de la pasarela ya no se aceptará y su pedido quedará sin marcar como pagado.')

    def _legacy_selected(self) -> bool:
        return (getattr(settings, 'PAYMENT_PROVIDER', '') or '').strip().lower() == self.legacy_code


class IzipayCheckoutProvider(_PaymentProvider):
    id = izipay.CONSOLE_PROVIDER
    label = 'Izipay · SDK web / Checkout'
    legacy_code = izipay.PROVIDER
    description = 'El producto de developers.izipay.pe: código de comercio, clave pública, API key y clave hash.'
    fields = (
        Field('environment', 'Entorno', required=True, kind='choice', default='sandbox',
              choices=(('sandbox', 'TEST (sandbox)'), ('production', 'PRODUCCIÓN'))),
        Field('merchant_code', 'Código de comercio', required=True),
        Field('public_key', 'Clave pública', required=True),
        Field('api_key', 'API key', secret=True, required=True),
        Field('hash_key', 'Clave hash (firma de notificaciones)', secret=True, required=True),
        Field('token_url', 'Dirección del token de sesión', required=True, kind='url'),
        Field('currency', 'Moneda', default='PEN'),
        Field('ipn_url', 'Dirección de notificación', kind='url',
              help='https://<dominio>/api/payments/izipay/notification/. Vacía: la del panel de Izipay.'),
    )

    def _values(self, public, secrets):
        merged = {**public, **secrets}
        return {name: merged.get(field, '') for field, name in izipay._CONSOLE_FIELDS.items()}

    def mode(self, public, secrets):
        return {'sandbox': 'test', 'production': 'production'}.get(public.get('environment', ''), '')

    def clean(self, public, secrets):
        if public.get('environment') == 'production' and 'sandbox' in (public.get('token_url') or '').lower():
            raise ConfigError({'token_url': 'Entorno PRODUCCIÓN con una dirección de sandbox.'})

    def test(self, config, **_options) -> TestOutcome:
        try:
            credentials = izipay.credentials_from(self._values(config.public, config.secrets))
        except izipay.IzipayError:
            return TestOutcome(False, 'incomplete', 'Las credenciales están incompletas o no son coherentes.')
        if credentials.environment != 'sandbox':
            return _unverified_production()
        from ... import checkout_services

        # What a checkout would send first, for an order that exists nowhere.
        order = SimpleNamespace(
            pk=0, customer_id=None, total=Decimal('1.00'), customer_name='Prueba de configuración',
            customer_email='prueba@example.com', customer_phone='', address_line='', city='', district='',
            document_type='', document_number='',
        )
        transaction_id = izipay.new_transaction_id()
        payload = checkout_services.build_payment_config(
            order, credentials=credentials, transaction_id=transaction_id, order_number=izipay.new_order_number())
        try:
            token = izipay.request_session_token(
                credentials=credentials, transaction_id=transaction_id, payload=payload, timeout=15.0)
        except izipay.IzipayError as exc:
            return _gateway_refusal(isinstance(exc, izipay.IzipayUnreachable))
        if not token:
            return TestOutcome(False, 'invalid', 'Izipay no entregó un token de sesión con estas credenciales.')
        return TestOutcome(True, 'ok', (
            'Conectado: el entorno TEST de Izipay entregó un token de sesión con esta API key. No se hizo ningún cargo. '
            'La clave hash no se puede comprobar así: la comprueba la notificación de un pago de prueba.'))

    def from_env(self, company=None):
        if not self._legacy_selected():
            return None
        values = {name: (getattr(settings, name, '') or '') for name in izipay._CONSOLE_FIELDS.values()}
        if not any(values[n] for n in ('IZIPAY_MERCHANT_CODE', 'IZIPAY_API_KEY', 'IZIPAY_HASH_KEY', 'IZIPAY_PUBLIC_KEY')):
            return None
        by_field = {field: values[name] for field, name in izipay._CONSOLE_FIELDS.items()}
        secrets = {name: by_field.pop(name) for name in ('api_key', 'hash_key')}
        return by_field, secrets


class MiCuentaWebProvider(_PaymentProvider):
    id = micuentaweb.CONSOLE_PROVIDER
    label = 'Izipay · Mi Cuenta Web'
    legacy_code = micuentaweb.PROVIDER
    description = 'La API REST V4 de secure.micuentaweb.pe: usuario, contraseña y clave pública del mismo entorno.'
    fields = (
        Field('shop_id', 'Usuario (identificador de la tienda)', required=True),
        Field('public_key', 'Clave pública', required=True, help='Tiene la forma <usuario>:<clave pública>.'),
        Field('password', 'Contraseña', secret=True, required=True,
              help='La de TEST empieza por testpassword_; la de PRODUCCIÓN, por prodpassword_.'),
        Field('hmac_key', 'Clave HMAC-SHA-256', secret=True,
              help='Firma la copia que recibe el navegador. La plataforma no la usa para confirmar pagos.'),
        Field('api_url', 'Dirección de la API', kind='url', default=micuentaweb.DEFAULT_API_URL),
        Field('currency', 'Moneda', default='PEN'),
        Field('ipn_url', 'Dirección de notificación', kind='url',
              help='https://<dominio>/api/payments/micuentaweb/notification/. Vacía: la del Back Office.'),
    )

    def _values(self, public, secrets):
        merged = {**public, **secrets}
        return {name: merged.get(field, '') for field, name in micuentaweb._CONSOLE_FIELDS.items()}

    def mode(self, public, secrets):
        key = (public.get('public_key') or '').partition(':')[2]
        if not key:
            return ''
        return 'test' if key.startswith('testpublickey_') else 'production'

    def clean(self, public, secrets):
        errors = {}
        shop, _, key = (public.get('public_key') or '').partition(':')
        if public.get('public_key') and (shop != public.get('shop_id') or not key):
            errors['public_key'] = 'Tiene que tener la forma <usuario>:<clave pública>, con el usuario de esta tienda.'
        password = secrets.get('password') or ''
        if password and not password.startswith(('testpassword_', 'prodpassword_')):
            errors['password'] = 'No es la contraseña de TEST (testpassword_…) ni la de PRODUCCIÓN (prodpassword_…).'
        elif password and key and key.startswith('testpublickey_') != password.startswith('testpassword_'):
            errors['password'] = 'La contraseña y la clave pública son de entornos distintos (TEST y PRODUCCIÓN).'
        if errors:
            raise ConfigError(errors)

    def test(self, config, **_options) -> TestOutcome:
        try:
            credentials = micuentaweb.credentials_from(self._values(config.public, config.secrets))
        except micuentaweb.MiCuentaWebError:
            return TestOutcome(False, 'incomplete', 'Las credenciales están incompletas o no son coherentes.')
        if credentials.environment != 'test':
            return _unverified_production()
        try:
            token = micuentaweb.create_payment(
                credentials=credentials, order_id=micuentaweb.new_order_id(), amount=Decimal('1.80'),
                customer_email='prueba@example.com', timeout=15.0)
        except micuentaweb.MiCuentaWebError as exc:
            return _gateway_refusal(isinstance(exc, micuentaweb.MiCuentaWebUnreachable))
        if not token:
            return TestOutcome(False, 'invalid', 'La pasarela no entregó un formToken con estas credenciales.')
        return TestOutcome(True, 'ok', 'Conectado: el entorno TEST entregó un formToken. No se hizo ningún cargo.')

    def from_env(self, company=None):
        if not self._legacy_selected():
            return None
        values = {name: (getattr(settings, name, '') or '') for name in micuentaweb._CONSOLE_FIELDS.values()}
        if not any(values[n] for n in ('MICUENTAWEB_SHOP_ID', 'MICUENTAWEB_PASSWORD', 'MICUENTAWEB_PUBLIC_KEY')):
            return None
        by_field = {field: values[name] for field, name in micuentaweb._CONSOLE_FIELDS.items()}
        secrets = {'password': by_field.pop('password'), 'hmac_key': getattr(settings, 'MICUENTAWEB_HMAC_KEY', '') or ''}
        return by_field, secrets


def _unverified_production() -> TestOutcome:
    """
    Production keys are never sent anywhere from a test: there is no call that
    proves them without touching real money. So the result says exactly that,
    and is not «Correcto».
    """
    return TestOutcome(True, 'unverified', (
        'Claves de PRODUCCIÓN coherentes entre sí, pero no se han verificado: desde aquí no se envían a la '
        'pasarela ni se hace ningún cargo. Valida antes las de TEST y, nada más activar éstas, haz un pago real '
        'pequeño y comprueba que el pedido queda pagado.'))


def _gateway_refusal(unreachable: bool) -> TestOutcome:
    """
    Two sentences, chosen by the TYPE of the adapter's error and never by its
    text: nothing a gateway replied reaches the console, and a reworded message
    in an adapter cannot turn "try again" into "these keys are wrong".
    """
    if unreachable:
        return TestOutcome(False, 'unavailable', 'No se pudo hablar con el entorno TEST de la pasarela. Vuelve a intentarlo.')
    return TestOutcome(False, 'invalid', 'La pasarela rechazó estas credenciales.')


registry.register(IzipayCheckoutProvider())
registry.register(MiCuentaWebProvider())
