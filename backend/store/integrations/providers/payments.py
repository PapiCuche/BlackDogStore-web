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
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from ...payments import izipay, micuentaweb, reconcile
from .. import payments as payment_adapters
from .. import registry
from ..registry import ChangeBlocked, ConfigError, Field, Provider, TestOutcome

PRODUCTION_WORD = 'PRODUCCION'
#: Typed to go ahead with a change that cuts charges nobody can verify.
INTERRUPT_WORD = 'INTERRUMPIR'
#: How long a buyer may reasonably still be at the card form.
IN_FLIGHT = timedelta(hours=1)
#: How many open attempts one change asks the gateway about before deciding.
GUARD_LIMIT = 100
#: …and for how long: the question is asked inside a master's request, on the
#: one worker there is. What does not fit is not verified, and blocks.
GUARD_BUDGET = timedelta(seconds=20)


class _PaymentProvider(Provider):
    category = 'payments'
    scope = registry.SCOPE_PLATFORM
    pausable = True
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

    # -- a change of gateway with charges open (PAY-SWITCH-PENDING) -----------------
    #
    # The product that CHARGES NOW decides how its open attempts are looked
    # after, whichever provider the change is asked of: activating the other
    # product is a change to the one that charges.

    #: Whether the gateway can be asked about an attempt whose notification
    #: never came. Where it can, a change waits for the answer; where it cannot,
    #: all there is is a master who means it.
    reconciles = False
    #: How far back an open attempt still matters to a change.
    open_window = IN_FLIGHT

    def shop(self, public: dict) -> str:
        """Whose account this configuration charges into. Another shop is another gateway."""
        return ''

    def prepare_change(self, company, *, change, public=None):
        charging = _charging()
        if charging is None or not charging.provider.reconciles or not _unsafe(self, change, public, charging):
            return None
        if not _open_attempts(charging).exists():
            return None
        # With the credentials that are STILL there: after the change nobody
        # could ask. The same question the scheduled reconciliation asks.
        return reconcile.reconcile_micuentaweb(
            window=charging.provider.open_window, limit=GUARD_LIMIT, budget=GUARD_BUDGET)

    def lock_changes(self) -> None:
        payment_adapters.lock_configuration(exclusive=True)

    def guard_change(self, company, *, change, public=None, prepared=None, acknowledged=False):
        if change == 'revoke':
            _refuse_environment_fallback(self)
        charging = _charging()
        if charging is None or not _touches(self, change, charging):
            return []
        if not charging.provider.reconciles:
            return _meant(charging, _open_attempts(charging), 'no_reconciliation', acknowledged, (
                'Hay {charges} con {label} en la última hora. Si el comprador termina de pagar después de este '
                'cambio, la notificación de la pasarela ya no se aceptará y su pedido quedará sin marcar como '
                'pagado: esta pasarela no se puede consultar.'))
        if not _unsafe(self, change, public, charging):
            # The same shop and the same environment with other keys: what is
            # open keeps its notification and can still be asked about.
            return []

        now = timezone.now()
        attempts = list(_open_attempts(charging).select_related('order__company').order_by('created_at'))
        if not attempts:
            return []
        young = [a for a in attempts if a.created_at > now - reconcile.FORM_LIFETIME]
        outcomes = getattr(prepared, 'outcomes', None) or {}
        # The run stopped because nobody could be asked at all: the gateway down,
        # refusing the caller, or no credentials to ask with.
        stopped = prepared is not None and (prepared.unreachable or prepared.refused or prepared.unconfigured)
        unresolved = [a for a in attempts if a not in young and outcomes.get(a.pk) in reconcile.UNRESOLVED]
        unasked = [a for a in attempts if a not in young and a.pk not in outcomes]
        unanswered = [a for a in attempts if a not in young and outcomes.get(a.pk) in reconcile.COULD_NOT_ASK]
        details = {'open': len(attempts), 'can_still_charge': len(young), 'unresolved': len(unresolved)}
        if young:
            # No word gets past this one: the form is alive, the buyer may be
            # typing the card, and after the change nobody would hear of it.
            # What a master can always do instead is pause new payments.
            retry_at = max(a.created_at for a in young) + reconcile.FORM_LIFETIME
            # What a master can do about it depends on where the gateway is
            # configured: a pause is a state of a console row, and a gateway
            # that charges from the server's environment has none.
            way_out = (
                'Pausa los pagos nuevos para que no entren más y vuelve a intentarlo a partir de las {time}.'
                if charging.in_console else
                'Vuelve a intentarlo a partir de las {time}. Esta pasarela cobra con las variables del servidor y '
                'desde aquí no se puede pausar: para poder pausar los pagos nuevos, lleva antes esa configuración '
                'a la consola (en su tarjeta, «Configurado mediante entorno», y actívala: con la misma tienda no '
                'se bloquea).')
            raise _blocked(details, retry_at, (
                'No se puede hacer este cambio todavía: hay {charges} con {label} cuyo formulario aún puede '
                'cobrar. Si el comprador pagara después del cambio, su pedido quedaría cobrado y sin confirmar. '
                + way_out), len(attempts), charging)
        if unresolved:
            retry_at = min(a.created_at for a in unresolved) + charging.provider.open_window
            raise _blocked(details, retry_at, (
                'No se puede hacer este cambio todavía: la pasarela tiene {charges} con {label} sin resolver '
                '(en curso, o con una respuesta que hay que revisar en su panel). Dejan de bloquear a las '
                '{time}; hasta entonces, resuélvelos en el panel de la pasarela y vuelve a intentarlo.'),
                len(unresolved), charging)
        if unasked and not stopped:
            # Decided to be unsafe only now, more open attempts than one change
            # asks about, or no time left to ask: not verified, so not done.
            raise _blocked(details, now + reconcile.MIN_AGE, (
                'No se pudo comprobar con la pasarela el estado de {charges} con {label}. Vuelve a intentarlo '
                'a partir de las {time}.'), len(unasked), charging)
        if unasked or unanswered:
            # COULD NOT ASK. Nobody knows, and waiting does not make anybody
            # know: it has to be meant, and it leaves the attempts on record.
            unknown = sorted(unasked + unanswered, key=lambda a: a.pk)
            return _meant(charging, unknown, 'gateway_unreachable' if stopped else 'gateway_error', acknowledged, (
                'No se pudo preguntar a la pasarela por {charges} con {label}: no responde, rechaza la consulta '
                'o contesta con un error. Vuelve a intentarlo en unos minutos. Si no puedes esperar, esos cobros '
                'quedarán anotados en la auditoría para revisarlos en el panel de la pasarela.'))
        # What is left is old enough that its form cannot charge and the
        # gateway, asked a moment ago, holds nothing for it.
        return []

    def change_notice(self, company=None):
        charging = _charging()
        if charging is None or not charging.provider.reconciles:
            return None
        opened = _open_attempts(charging).count()
        if not opened:
            return None
        return {'count': opened, 'message': (
            f'Hay {_charges(opened)} con {charging.provider.label}. Antes de cambiar de producto, de tienda o de '
            'entorno, o de apagar o revocar esta pasarela, el servidor preguntará por ellos a la pasarela y no '
            'hará el cambio mientras alguno pueda cobrarse todavía o siga sin resolver. Renovar la contraseña '
            'o las claves de la misma tienda no los interrumpe.')}


class IzipayCheckoutProvider(_PaymentProvider):
    id = izipay.CONSOLE_PROVIDER
    label = 'Izipay · SDK web / Checkout'
    legacy_code = izipay.PROVIDER

    def shop(self, public):
        return (public.get('merchant_code') or '').strip()
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
        # As the adapter reads it (`izipay.credentials_from`): neither case nor
        # surrounding blanks make it another environment.
        word = str(public.get('environment') or '').strip().lower()
        return {'sandbox': 'test', 'production': 'production'}.get(word, '')

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
    reconciles = True
    #: As far back as the reconciliation looks: after that nothing arrives on its own.
    open_window = reconcile.WINDOW

    def shop(self, public):
        return (public.get('shop_id') or '').strip()

    def interruption_warning(self, company=None) -> str:
        # No typed word for this product: the server asks the gateway and
        # decides (`guard_change`). The console says so in `change_notice`.
        return ''
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
        api_url = (public.get('api_url') or micuentaweb.DEFAULT_API_URL).strip()
        if (urlsplit(api_url).hostname or '') not in micuentaweb.OFFICIAL_API_HOSTS:
            # Every call carries the password: it goes to the gateway's own server.
            errors['api_url'] = f'Tiene que ser la dirección oficial: {micuentaweb.DEFAULT_API_URL}'
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


class _Charging:
    """The product that charges now, and the public half of what it charges with."""

    def __init__(self, provider, public, in_console):
        self.provider, self.public, self.in_console = provider, public, in_console


def _charging():
    """Who charges NOW — from the console or from the environment — or None."""
    from ...models import IntegrationConfig

    adapter = payment_adapters.adapter(payment_adapters.active_code())
    provider = registry.get(adapter.provider_id) if adapter is not None else None
    if provider is None:
        return None
    active = IntegrationConfig.objects.filter(
        provider=provider.id, slot=IntegrationConfig.SLOT_ACTIVE, company__isnull=True).first()
    if active is not None:
        return _Charging(provider, dict(active.public), True)
    legacy = provider.from_env()
    return _Charging(provider, dict(legacy[0]), False) if legacy is not None else None


def _refuse_environment_fallback(provider) -> None:
    """
    Revoking the LAST active payment row of the console hands the decision back
    to the server's environment: whatever keys are there would start charging —
    another gateway, maybe production — without anybody having activated them,
    tested them or typed a word. That is not what «revocar» says it does, so it
    is not done: the master switches the configuration off instead, or the
    server's variables are removed first.
    """
    from ...models import IntegrationConfig

    if not IntegrationConfig.objects.filter(
            provider=provider.id, slot=IntegrationConfig.SLOT_ACTIVE, company__isnull=True).exists():
        return                                  # only a draft: nothing that decides is being removed
    if payment_adapters.console_rows().exclude(provider=provider.id).exists():
        return                                  # the console still decides
    fallback = payment_adapters.environment_fallback()
    if fallback is None:
        return
    legacy, public = fallback
    mode = 'PRODUCCIÓN' if legacy.mode(public, {}) == 'production' else 'TEST'
    raise ChangeBlocked(
        f'No se revoca: sin esta configuración la tienda pasaría a cobrar con las claves de las variables del '
        f'servidor ({legacy.label}, {mode}), que nadie activó desde aquí. Para dejar de cobrar, desactívala o '
        'pausa los pagos. Para revocarla, quita antes esas variables del servidor.',
        {'code': 'environment_fallback'},
    )


def _touches(provider, change, charging) -> bool:
    """Whether this change is a change to what charges at all."""
    if change == 'activate':
        return True                     # it replaces the product that charges, or what it charges with
    # Switching off or revoking only matters for the one that charges, and only
    # when it charges from the console: there is nothing of the environment here to remove.
    return provider.id == charging.provider.id and charging.in_console


def _unsafe(provider, change, public, charging) -> bool:
    """
    Whether, after this change, an attempt that is open now could no longer be
    confirmed or asked about: another product, the one that charges switched off
    or removed, another shop, or the other environment (TEST ↔ PRODUCTION).
    """
    if not _touches(provider, change, charging):
        return False
    if change != 'activate':
        return True
    if provider.id != charging.provider.id:
        return True
    public = public or {}
    return (provider.shop(public) != charging.provider.shop(charging.public)
            or provider.mode(public, {}) != charging.provider.mode(charging.public, {}))


def _origin(charging):
    """The shop and the environment of what charges, as an attempt records them."""
    return charging.provider.shop(charging.public), charging.provider.mode(charging.public, {}) or 'test'


def _open_attempts(charging):
    """
    The attempts a change of gateway has to look after: opened with the product
    that charges, with ITS shop and environment. One opened with another shop
    or in the other environment was already cut off by the change that left it
    behind (and is on record there): this gateway cannot confirm it, so it is
    not this change's to wait for.
    """
    from ...models import PaymentTransaction

    return PaymentTransaction.objects.filter(
        reconcile.origin_filter(*_origin(charging)),
        provider=charging.provider.legacy_code, status=PaymentTransaction.Status.PENDING,
        created_at__gte=timezone.now() - charging.provider.open_window,
    )


def attempts_of_another_origin(since):
    """Open attempts of the product that charges that were opened with ANOTHER shop or environment."""
    from ...models import PaymentTransaction

    charging = _charging()
    if charging is None:
        return PaymentTransaction.objects.none()
    return PaymentTransaction.objects.filter(
        provider=charging.provider.legacy_code, status=PaymentTransaction.Status.PENDING, created_at__gte=since,
    ).exclude(reconcile.origin_filter(*_origin(charging)))


def _charges(count: int) -> str:
    return '1 cobro abierto' if count == 1 else f'{count} cobros abiertos'


def _blocked(details, retry_at, sentence, count, charging) -> ChangeBlocked:
    message = sentence.format(charges=_charges(count), label=charging.provider.label,
                              time=timezone.localtime(retry_at).strftime('%H:%M'))
    return ChangeBlocked(message, {
        'code': 'open_payments', 'open_payments': {**details, 'retry_at': retry_at.isoformat()},
    })


def _meant(charging, attempts, reason, acknowledged, sentence):
    """
    Charges nobody can verify: the change goes ahead only if the master typed
    the word, and then says which attempts it cut off — one record per company,
    ids only.
    """
    attempts = list(attempts.select_related('order__company') if hasattr(attempts, 'select_related') else attempts)
    if not attempts:
        return []
    if not acknowledged:
        raise ConfigError({'acknowledge': (
            sentence.format(charges=_charges(len(attempts)), label=charging.provider.label)
            + f' Espera a que terminen, o escribe {INTERRUPT_WORD} para seguir de todos modos.')})
    by_company = {}
    for attempt in attempts:
        by_company.setdefault(attempt.order.company, []).append(attempt)
    return [
        (owner, {
            'interrupted': charging.provider.id, 'reason': reason,
            'attempts': [a.pk for a in theirs], 'orders': sorted({a.order_id for a in theirs}),
        })
        for owner, theirs in by_company.items()
    ]


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
