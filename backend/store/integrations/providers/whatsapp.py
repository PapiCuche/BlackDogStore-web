"""
WhatsApp Business (Meta's Cloud API), one configuration PER COMPANY.

A master administers every company's from the console; the company itself still
decides, on its own settings screen, whether notices go out, in which language
and with which templates — and a customer still has to have said yes.

`store.messaging` reads what is activated here before it looks at the
environment-variable references of `manage.py configure_whatsapp`.
"""
import json
import re
import urllib.error
import urllib.request

from django.conf import settings

from ... import messaging
from ...messaging.whatsapp_cloud import GRAPH
from .. import registry
from ..registry import ConfigError, Field, Provider, TestOutcome

_DIGITS = re.compile(r'^\d{5,40}$')
TIMEOUT_SECONDS = 8


class WhatsAppCloudProvider(Provider):
    id = messaging.CONSOLE_PROVIDER
    label = 'WhatsApp Business'
    category = 'messaging'
    scope = registry.SCOPE_COMPANY
    description = ('La API oficial de WhatsApp Business (Meta) con la que una empresa avisa a sus clientes. '
                   'Cada empresa tiene su número y sus credenciales.')
    fields = (
        Field('phone_number_id', 'Identificador del número', required=True,
              help='El «Phone number ID» de Meta: sólo dígitos. No es el número de teléfono.'),
        Field('business_account_id', 'Identificador de la cuenta de WhatsApp Business',
              help='Opcional. El «WhatsApp Business Account ID».'),
        Field('access_token', 'Token de acceso', secret=True, required=True,
              help='El token permanente de un usuario del sistema, con permiso de mensajería.'),
        Field('app_secret', 'Secreto de la aplicación', secret=True, required=True,
              help='Con él se comprueba la firma de cada aviso que Meta envía al webhook.'),
        Field('verify_token', 'Token de verificación del webhook', secret=True, required=True,
              help='El texto que se escribe en Meta al suscribir el webhook. Lo eliges tú.'),
    )

    def clean(self, public, secrets):
        errors = {}
        for name in ('phone_number_id', 'business_account_id'):
            if public.get(name) and not _DIGITS.match(public[name]):
                errors[name] = 'Es un identificador de Meta: sólo dígitos, sin espacios ni signos.'
        if errors:
            raise ConfigError(errors)

    def on_activated(self, company, public):
        # The row the company's own screen and the webhook look for. Created off:
        # turning notices on stays the company's decision.
        from ...models import CompanyMessagingSettings

        CompanyMessagingSettings.objects.get_or_create(company=company)

    def test(self, config, **_options) -> TestOutcome:
        if self.missing(config.public, config.secrets):
            return TestOutcome(False, 'incomplete', 'Faltan el identificador del número o alguna de las tres credenciales.')
        kind = getattr(settings, 'WHATSAPP_PROVIDER', 'cloud_api')
        if kind == 'fake':
            return TestOutcome(True, 'ok', 'Proveedor simulado en este entorno: la configuración es coherente y no se llamó a Meta.')
        if kind != 'cloud_api':
            return TestOutcome(False, 'unavailable', 'El envío por WhatsApp está desactivado en este servidor.')
        return self._ask_meta(config.public['phone_number_id'], config.secrets['access_token'])

    def _ask_meta(self, number: str, token: str) -> TestOutcome:
        """
        Read the number's own record. A GET: nobody receives a message, and it
        proves the two things a send needs — the token works and it can see
        this number.

        Nothing Meta answers is shown: its error messages can quote the token.
        """
        version = getattr(settings, 'WHATSAPP_GRAPH_API_VERSION', 'v21.0')
        request = urllib.request.Request(
            f'{GRAPH}/{version}/{number}?fields=id,display_phone_number,verified_name',
            headers={'Authorization': f'Bearer {token}'}, method='GET',
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return TestOutcome(False, 'auth_failed', 'Meta rechazó el token de acceso.')
            if 400 <= exc.code < 500 and exc.code != 429:
                return TestOutcome(False, 'invalid', 'Meta no reconoce ese identificador de número con este token.')
            return TestOutcome(False, 'unavailable', 'Meta no está disponible ahora. Vuelve a intentarlo.')
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return TestOutcome(False, 'unavailable', 'No se pudo hablar con Meta. Vuelve a intentarlo.')
        if not isinstance(body, dict) or str(body.get('id') or '') != number:
            return TestOutcome(False, 'invalid', 'Meta no reconoce ese identificador de número con este token.')
        return TestOutcome(True, 'ok', 'Conectado: Meta reconoce el número con este token. No se envió ningún mensaje.')

    def from_env(self, company=None):
        """The references of `configure_whatsapp`, when they resolve to something."""
        from ...models import CompanyMessagingSettings

        config = CompanyMessagingSettings.objects.filter(company=company).first() if company else None
        if config is None:
            return None
        secrets = {which: messaging.reference(config, which) for which in messaging.CREDENTIALS}
        if not config.whatsapp_phone_number_id and not any(secrets.values()):
            return None
        public = {
            'phone_number_id': config.whatsapp_phone_number_id,
            'business_account_id': config.whatsapp_business_account_id,
        }
        return public, secrets


registry.register(WhatsAppCloudProvider())
