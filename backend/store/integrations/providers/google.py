"""
Google sign-in: the OAuth client ID, and nothing else.

The flow is Google Identity Services with an ID token the backend verifies
(`store.google_identity`). It has no client secret, so none is asked for and
none is stored. The client ID is public: the browser receives it to draw the
button, and it is the audience every token is checked against.
"""
import re

from django.conf import settings

from .. import registry
from ..registry import ConfigError, Field, Provider, TestOutcome

ID = 'google'
_CLIENT_ID = re.compile(r'^\d{6,}-[a-z0-9]{8,}\.apps\.googleusercontent\.com$')


class GoogleProvider(Provider):
    id = ID
    label = 'Inicio de sesión con Google'
    category = 'identity'
    scope = registry.SCOPE_PLATFORM
    description = ('El botón «Continuar con Google» de la tienda. Sólo necesita el ID de cliente de OAuth: '
                   'este flujo no usa secreto de cliente.')
    fields = (
        Field('client_id', 'ID de cliente de OAuth', required=True,
              help='Termina en .apps.googleusercontent.com. En Google Cloud, el origen de la tienda '
                   'tiene que estar entre los «orígenes de JavaScript autorizados» de este cliente.'),
    )

    def clean(self, public, secrets):
        if public.get('client_id') and not _CLIENT_ID.match(public['client_id']):
            raise ConfigError({'client_id': 'No es un ID de cliente de Google: '
                                            '<números>-<letras>.apps.googleusercontent.com'})

    def test(self, config, **_options) -> TestOutcome:
        from ... import google_identity

        if self.missing(config.public, config.secrets):
            return TestOutcome(False, 'incomplete', 'Falta el ID de cliente.')
        # What signing in needs from this server: Google's public keys. Whether
        # the ID is the one of the owner's project is only proven by signing in.
        if not google_identity.signing_keys_reachable():
            return TestOutcome(False, 'unavailable', 'El servidor no alcanza las claves públicas de Google. Vuelve a intentarlo.')
        return TestOutcome(True, 'ok', 'El ID tiene la forma correcta y el servidor alcanza las claves de Google. '
                                       'Termina de comprobarlo iniciando sesión con Google en la tienda.')

    def from_env(self, company=None):
        value = (getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '') or '').strip()
        return ({'client_id': value}, {}) if value else None


registry.register(GoogleProvider())
