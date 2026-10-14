"""
Sign in with Apple (SOCIAL-AUTH-01): the Services ID, the Team ID, the Key ID
and the private key (.p8) the client secret is signed with.

The private key is the only secret and is sealed like every other. The
client secret Apple asks for is minted from it at each sign-in and lives five
minutes (`store.apple_identity.client_secret`).
"""
from .. import registry
from ..registry import ConfigError, Field, Provider, TestOutcome

ID = 'apple'


class AppleProvider(Provider):
    id = ID
    label = 'Inicio de sesión con Apple'
    category = 'identity'
    scope = registry.SCOPE_PLATFORM
    description = ('El botón «Continuar con Apple» de la tienda. Necesita el Services ID, el Team ID y una '
                   'clave de «Sign in with Apple» (.p8) del Apple Developer Program.')
    fields = (
        Field('services_id', 'Services ID', required=True,
              help='El identificador del servicio web, por ejemplo pe.blackdogstore.web. Es la audiencia '
                   'de cada token.'),
        Field('team_id', 'Team ID', required=True, help='Diez caracteres, arriba a la derecha en Apple Developer.'),
        Field('key_id', 'Key ID', required=True, help='Diez caracteres: el de la clave .p8.'),
        Field('private_key', 'Clave privada (.p8)', required=True, secret=True, kind='textarea',
              help='Pega el contenido completo del archivo .p8. Se guarda cifrada y nunca vuelve a mostrarse.'),
    )

    def clean(self, public, secrets):
        from ... import apple_identity

        errors = {}
        for name in ('team_id', 'key_id'):
            if public.get(name) and not apple_identity.TEN_CHARACTERS.match(public[name]):
                errors[name] = 'Son diez letras mayúsculas o números.'
        if secrets.get('private_key'):
            try:
                apple_identity.load_private_key(secrets['private_key'])
            except ValueError as exc:
                errors['private_key'] = str(exc)
        if errors:
            raise ConfigError(errors)

    def test(self, config, **_options) -> TestOutcome:
        from ... import apple_identity

        if self.missing(config.public, config.secrets):
            return TestOutcome(False, 'incomplete', 'Faltan datos de Sign in with Apple.')
        try:
            apple_identity.client_secret(config)
        except ValueError:
            return TestOutcome(False, 'rejected', 'Con esta clave no se puede firmar el secreto de cliente.')
        if not apple_identity.signing_keys_reachable():
            return TestOutcome(False, 'unavailable', 'El servidor no alcanza las claves públicas de Apple. '
                                                     'Vuelve a intentarlo.')
        # Whether Apple accepts this Services ID with this key is only proven by signing in.
        return TestOutcome(True, 'unverified', 'La clave firma y el servidor alcanza a Apple. Termina de '
                                               'comprobarlo entrando con Apple en la tienda.')


registry.register(AppleProvider())
