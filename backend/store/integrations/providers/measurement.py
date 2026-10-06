"""
Analytics and marketing: Google Analytics 4, Meta and TikTok.

    ONE ID IS PUBLIC, THE REST IS NOT.

Each of the three needs an identifier in the browser — a measurement ID, a pixel
ID — which is as public as a `<script>` tag. That is the ONLY thing the
storefront is told (`runtime_public`). API secrets and access tokens stay in the
secret store and are used from the server.

    NO ADDRESS IS CONFIGURABLE.

Where the scripts come from and where events are sent are constants of the code
(`store.measurement.adapters`, and the frontend adapters). A console field that
took a URL would let a stolen master session point the shop's customers at
somebody else's script; an ID that is checked against its provider's own shape
cannot.

    A TEST NEVER CREATES A REAL EVENT.

Meta and TikTok have a test channel (a «test event code»): with one, the test
sends ONE event that shows only in the provider's test screen. Without one —
and for the pixel-only modes, which have nothing to call — the test says that
nothing was verified, and says how to verify it.
"""
import re
import time
import uuid

from django.conf import settings

from ...measurement import adapters
from .. import registry
from ..registry import ConfigError, Field, Provider, TestOutcome

_GA_ID = re.compile(r'^G-[A-Z0-9]{4,20}$')
_META_PIXEL = re.compile(r'^\d{10,20}$')
_TIKTOK_PIXEL = re.compile(r'^[A-Z0-9]{16,24}$')
_TEST_CODE = re.compile(r'^[A-Za-z0-9_-]{4,40}$')

PIXEL_ONLY = 'pixel_only'
PURCHASE_BROWSER, PURCHASE_SERVER, PURCHASE_BOTH = 'browser', 'server', 'both'

_UNVERIFIED_PIXEL = ('El identificador tiene la forma correcta. No hay nada que comprobar desde el servidor en este '
                     'modo: verifícalo con la extensión de diagnóstico del proveedor en la tienda, tras aceptar las cookies.')


def _site_url() -> str:
    return (getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/') + '/'


def _outcome(answer, verified: str) -> TestOutcome:
    """A provider's answer as what the console shows. Its own words never get here."""
    if answer.ok:
        return TestOutcome(True, 'ok', verified)
    return TestOutcome(False, answer.kind, {
        adapters.AUTH_FAILED: 'El proveedor rechazó el token de acceso, o no tiene permiso sobre este píxel.',
        adapters.INVALID: 'El proveedor no aceptó el evento de prueba: revisa el identificador y el código de prueba.',
        adapters.UNAVAILABLE: 'No se pudo hablar con el proveedor. Vuelve a intentarlo.',
    }.get(answer.kind, 'La prueba no se pudo completar.'))


class _Measurement(Provider):
    scope = registry.SCOPE_PLATFORM
    #: The consent category a visitor has to accept before this provider sees anything.
    consent = ''

    def runtime_public(self, config):
        raise NotImplementedError


class GoogleAnalyticsProvider(_Measurement):
    id = 'google_analytics'
    label = 'Google Analytics 4'
    category = 'analytics'
    consent = 'analytics'
    description = ('Mide las visitas y el embudo de compra de la tienda. Sólo se carga para quien acepta las cookies '
                   'de analítica.')
    fields = (
        Field('measurement_id', 'ID de medición', required=True,
              help='Empieza por G-. En Google Analytics: Administrar › Flujos de datos › el flujo web.'),
        Field('api_secret', 'Secreto de API de Measurement Protocol', secret=True,
              help='Opcional. Con él, la compra la envía el servidor cuando el pago se confirma, y no depende de que '
                   'el comprador vuelva a la tienda. Se crea en el mismo flujo de datos.'),
    )

    def clean(self, public, secrets):
        if public.get('measurement_id') and not _GA_ID.match(public['measurement_id']):
            raise ConfigError({'measurement_id': 'No es un ID de medición de GA4: empieza por G- y sigue con letras '
                                                 'mayúsculas y números.'})

    def runtime_public(self, config):
        return {
            'measurement_id': config.get('measurement_id'),
            'purchase': PURCHASE_SERVER if config.get('api_secret') else PURCHASE_BROWSER,
        }

    def test(self, config, **_options) -> TestOutcome:
        if self.missing(config.public, config.secrets):
            return TestOutcome(False, 'incomplete', 'Falta el ID de medición.')
        secret = config.get('api_secret')
        if not secret:
            return TestOutcome(True, 'unverified', (
                'El ID tiene la forma correcta. Google no ofrece cómo comprobarlo desde el servidor: ábrelo en '
                'Informes › Tiempo real y visita la tienda aceptando las cookies de analítica.'))
        answer = adapters.ga_validate(config.get('measurement_id'), secret, {
            'client_id': f'{int(time.time())}.{uuid.uuid4().int % 10 ** 9}',
            'events': [{'name': 'page_view', 'params': {'engagement_time_msec': 1}}],
        })
        if not answer.ok:
            return _outcome(answer, '')
        # Google's validation server says, in its own documentation, that it checks
        # neither the measurement ID nor the API secret.
        return TestOutcome(True, 'unverified', (
            'Google aceptó la forma del evento, pero su servidor de validación no comprueba que el ID ni el secreto '
            'sean los de tu propiedad. Verifícalo con una compra de prueba en Informes › Tiempo real.'))


class _PixelProvider(_Measurement):
    category = 'marketing'
    consent = 'marketing'
    #: Name of the public field that carries the pixel's identifier.
    pixel_field = ''
    pixel_pattern = None
    pixel_error = ''
    server_mode = ''

    def clean(self, public, secrets):
        errors = {}
        if public.get(self.pixel_field) and not self.pixel_pattern.match(public[self.pixel_field]):
            errors[self.pixel_field] = self.pixel_error
        if public.get('test_event_code') and not _TEST_CODE.match(public['test_event_code']):
            errors['test_event_code'] = 'Es el código corto que da el proveedor en su pantalla de eventos de prueba.'
        if public.get('mode') == self.server_mode and not secrets.get('access_token'):
            errors['access_token'] = 'Este modo envía eventos desde el servidor: necesita el token de acceso.'
        if errors:
            raise ConfigError(errors)

    def server_side(self, config) -> bool:
        return config.get('mode') == self.server_mode and bool(config.get('access_token'))

    def runtime_public(self, config):
        return {
            self.pixel_field: config.get(self.pixel_field),
            'purchase': PURCHASE_BOTH if self.server_side(config) else PURCHASE_BROWSER,
        }

    def _send_test(self, config):
        raise NotImplementedError

    def test(self, config, **_options) -> TestOutcome:
        if self.missing(config.public, config.secrets):
            return TestOutcome(False, 'incomplete', 'Falta el identificador del píxel.')
        if config.get('mode') != self.server_mode:
            return TestOutcome(True, 'unverified', _UNVERIFIED_PIXEL)
        if not config.get('access_token'):
            return TestOutcome(False, 'incomplete', 'Falta el token de acceso.')
        if not config.get('test_event_code'):
            # Without the provider's test channel the only way to try the token is a real event.
            return TestOutcome(True, 'unverified', (
                'No se envió nada: sin un código de evento de prueba, probar el token crearía un evento real en tu '
                'cuenta. Añade el código que da el proveedor en su pantalla de eventos de prueba y vuelve a probar.'))
        return _outcome(self._send_test(config), (
            'El proveedor recibió el evento de prueba: aparece en su pantalla de eventos de prueba y no cuenta como '
            'una visita real. Quita el código de prueba antes de abrir la tienda.'))


class MetaProvider(_PixelProvider):
    id = 'meta'
    label = 'Meta'
    description = ('El píxel de Meta (Facebook e Instagram) y, opcionalmente, su API de conversiones. Sólo se usa '
                   'con quien acepta las cookies de marketing.')
    pixel_field = 'pixel_id'
    pixel_pattern = _META_PIXEL
    pixel_error = 'El ID del píxel de Meta son sólo dígitos (entre 10 y 20).'
    server_mode = 'pixel_and_capi'
    fields = (
        Field('mode', 'Modo', required=True, kind='choice', default=PIXEL_ONLY, choices=(
            (PIXEL_ONLY, 'Sólo píxel (desde el navegador)'),
            ('pixel_and_capi', 'Píxel y API de conversiones (la compra también desde el servidor)'),
        )),
        Field('pixel_id', 'ID del píxel', required=True, help='En Meta Events Manager: Orígenes de datos › el píxel.'),
        Field('access_token', 'Token de acceso de la API de conversiones', secret=True,
              help='Sólo para el modo con API de conversiones. Se genera en la configuración del píxel.'),
        Field('test_event_code', 'Código de evento de prueba',
              help='Opcional y temporal. Mientras esté puesto, lo que envía el servidor aparece sólo en «Eventos de '
                   'prueba» de Meta.'),
    )

    def _send_test(self, config):
        return adapters.meta_send(config.get('pixel_id'), config.get('access_token'), [{
            'event_name': 'PageView', 'event_time': int(time.time()), 'event_id': f'config-test.{uuid.uuid4().hex}',
            'action_source': 'website', 'event_source_url': _site_url(),
            'user_data': {'client_user_agent': 'integration-console-test'},
        }], config.get('test_event_code'))


class TikTokProvider(_PixelProvider):
    id = 'tiktok'
    label = 'TikTok'
    description = ('El píxel de TikTok y, opcionalmente, su Events API. Sólo se usa con quien acepta las cookies de '
                   'marketing.')
    pixel_field = 'pixel_code'
    pixel_pattern = _TIKTOK_PIXEL
    pixel_error = 'El código del píxel de TikTok son entre 16 y 24 letras mayúsculas y números.'
    server_mode = 'pixel_and_events_api'
    fields = (
        Field('mode', 'Modo', required=True, kind='choice', default=PIXEL_ONLY, choices=(
            (PIXEL_ONLY, 'Sólo píxel (desde el navegador)'),
            ('pixel_and_events_api', 'Píxel y Events API (la compra también desde el servidor)'),
        )),
        Field('pixel_code', 'Código del píxel', required=True,
              help='En TikTok Ads Manager: Herramientas › Eventos › el píxel web.'),
        Field('access_token', 'Token de acceso de Events API', secret=True,
              help='Sólo para el modo con Events API. Se genera en la configuración del píxel.'),
        Field('test_event_code', 'Código de evento de prueba',
              help='Opcional y temporal. Mientras esté puesto, lo que envía el servidor aparece sólo en «Test Events» '
                   'de TikTok.'),
    )

    def _send_test(self, config):
        return adapters.tiktok_send(config.get('pixel_code'), config.get('access_token'), [{
            'event': 'ViewContent', 'event_time': int(time.time()), 'event_id': f'config-test.{uuid.uuid4().hex}',
            'user': {'user_agent': 'integration-console-test'},
            'page': {'url': _site_url()},
        }], config.get('test_event_code'))


registry.register(GoogleAnalyticsProvider())
registry.register(MetaProvider())
registry.register(TikTokProvider())
