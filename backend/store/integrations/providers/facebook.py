"""
Facebook sign-in (SOCIAL-AUTH-01): the app id, the app secret and the Graph API
version.

The flow is an Authorization Code flow with PKCE that this server finishes
(`store.facebook_identity`): the secret never leaves the server and the browser
is told nothing of this configuration but «Facebook is on».
"""
import re

from .. import registry
from ..registry import ConfigError, Field, Provider, TestOutcome

ID = 'facebook'
_APP_ID = re.compile(r'^\d{5,20}$')


class FacebookProvider(Provider):
    id = ID
    label = 'Inicio de sesión con Facebook'
    category = 'identity'
    scope = registry.SCOPE_PLATFORM
    description = ('El botón «Continuar con Facebook» de la tienda. Necesita el ID y la clave secreta '
                   'de la app de Meta; la URI de redirección es la que indica la guía de configuración.')
    fields = (
        Field('app_id', 'ID de la app', required=True,
              help='Meta for Developers › tu app › Configuración de la app › Básica.'),
        Field('app_secret', 'Clave secreta de la app', required=True, secret=True,
              help='Se guarda cifrada y nunca vuelve a mostrarse.'),
        Field('graph_version', 'Versión de la Graph API', default='v23.0',
              help='Por ejemplo v23.0. Meta retira cada versión unos dos años después de publicarla.'),
    )

    def clean(self, public, secrets):
        errors = {}
        if public.get('app_id') and not _APP_ID.match(public['app_id']):
            errors['app_id'] = 'El ID de la app son sólo números.'
        if public.get('graph_version'):
            from ... import facebook_identity

            if not facebook_identity.VERSION.match(public['graph_version']):
                errors['graph_version'] = 'Tiene la forma v23.0.'
        if errors:
            raise ConfigError(errors)

    def test(self, config, **_options) -> TestOutcome:
        from ... import facebook_identity
        from ...social_identity import SocialAuthError

        if self.missing(config.public, config.secrets):
            return TestOutcome(False, 'incomplete', 'Faltan el ID o la clave secreta de la app.')
        try:
            works = facebook_identity.app_token_works(config)
        except SocialAuthError:
            return TestOutcome(False, 'unavailable', 'El servidor no alcanza a Facebook. Vuelve a intentarlo.')
        if not works:
            return TestOutcome(False, 'rejected', 'Facebook no acepta este ID con esta clave secreta.')
        return TestOutcome(True, 'ok', 'Facebook acepta la app. Termina de comprobarlo entrando con Facebook '
                                       'en la tienda.')


registry.register(FacebookProvider())
