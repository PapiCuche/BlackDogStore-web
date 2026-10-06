"""
SUNAT — facturación electrónica.

    ONE configuration for the installation, and ONE environment: BETA.

That is what the code can do today (`store.fiscal_config`): the endpoint is a
table in the code, production fails closed, and `resolve_credentials` hands the
same credentials to every company. So the console declares platform scope, has
no environment to choose and no address to type. `fiscal_config` already takes
`company` everywhere: the day each company has its own, this provider becomes
company-scoped and the callers do not change.

The signing certificate is the PKCS#12 SUNAT's providers hand out. It travels
as base64 in the JSON body, is checked before anything is stored, and lives
only inside the sealed secrets: there is no file, no path and no URL to it.
"""
import base64
import binascii
import re

from django.conf import settings

from ...fiscal.certificate import CertificateError, inspect_pkcs12
from .. import registry
from ..registry import ConfigError, Field, Provider, TestOutcome

ID = 'sunat'
ENABLE_WORD = 'EMITIR'
MAX_CERTIFICATE_BYTES = 64 * 1024
_RUC = re.compile(r'^\d{11}$')


def certificate_bytes(secrets: dict) -> bytes:
    try:
        return base64.b64decode(secrets.get('certificate_p12') or '', validate=True)
    except (ValueError, binascii.Error):
        raise CertificateError('El certificado guardado no se puede leer.') from None


def certificate_password(secrets: dict):
    password = secrets.get('certificate_password') or ''
    return password.encode('utf-8') if password else None


class SunatProvider(Provider):
    id = ID
    label = 'SUNAT · Facturación electrónica'
    category = 'fiscal'
    scope = registry.SCOPE_PLATFORM
    supported_modes = ('test',)
    description = ('Las credenciales SOL y el certificado con que se firman y envían los comprobantes. '
                   'Esta versión opera sólo el entorno BETA (pruebas) de SUNAT.')
    fields = (
        Field('ruc', 'RUC del emisor', required=True, help='Once dígitos.'),
        Field('sol_user', 'Usuario SOL', required=True, help='El usuario secundario creado para facturación electrónica.'),
        Field('sol_password', 'Clave SOL', secret=True, required=True),
        Field('certificate_p12', 'Certificado digital (.p12 / .pfx)', secret=True, required=True, kind='file',
              maximum=MAX_CERTIFICATE_BYTES,
              help='El archivo PKCS#12 con el certificado y su clave privada. Se guarda cifrado y no se puede descargar.'),
        Field('certificate_password', 'Contraseña del certificado', secret=True,
              help='La que protege el archivo .p12. Vacía si no tiene.'),
    )

    def mode(self, public, secrets):
        return 'test'

    def clean(self, public, secrets):
        errors = {}
        if public.get('ruc') and not _RUC.match(public['ruc']):
            errors['ruc'] = 'Un RUC tiene once dígitos.'
        if secrets.get('certificate_p12'):
            try:
                inspect_pkcs12(certificate_bytes(secrets), certificate_password(secrets))
            except CertificateError as exc:
                # The library's own sentence: it names neither the password nor the content.
                errors['certificate_p12'] = str(exc)
        if errors:
            raise ConfigError(errors)

    def activation_confirmation(self, public, secrets):
        return ENABLE_WORD, (
            'Vas a habilitar la emisión de comprobantes electrónicos hacia SUNAT (entorno BETA, de pruebas) '
            f'en esta instalación. Escribe {ENABLE_WORD} para confirmarlo.')

    def test(self, config, **_options) -> TestOutcome:
        """
        Everything that can be known without sending SUNAT a document: the
        certificate opens with its password, has its key and is in force.
        """
        from ... import fiscal_config

        if self.missing(config.public, config.secrets):
            return TestOutcome(False, 'incomplete', 'Faltan el RUC, el usuario o la clave SOL, o el certificado.')
        try:
            metadata = inspect_pkcs12(certificate_bytes(config.secrets), certificate_password(config.secrets))
        except CertificateError as exc:
            return TestOutcome(False, 'invalid', str(exc))
        until = metadata.not_after.strftime('%d/%m/%Y')
        validity = metadata.validity()
        if validity == 'expired':
            return TestOutcome(False, 'invalid', f'El certificado venció el {until}.')
        if validity != 'valid':
            return TestOutcome(False, 'invalid',
                               f'El certificado todavía no es válido: empieza el {metadata.not_before.strftime("%d/%m/%Y")}.')
        try:
            fiscal_config.resolve_environment()
        except fiscal_config.FiscalConfigError as exc:
            return TestOutcome(False, 'unavailable', str(exc))
        return TestOutcome(True, 'ok', (
            f'Certificado «{metadata.subject[:120]}» vigente hasta el {until}. Entorno BETA de SUNAT (pruebas). '
            'La clave SOL se comprueba al emitir: no se envió nada a SUNAT.'))

    def from_env(self, company=None):
        if not getattr(settings, 'FISCAL_ENABLED', False):
            return None
        ruc = (getattr(settings, 'FISCAL_SOL_RUC', '') or '').strip()
        user = (getattr(settings, 'FISCAL_SOL_USER', '') or '').strip()
        password = getattr(settings, 'FISCAL_SOL_PASSWORD', '') or ''
        if not (ruc or user or password):
            return None
        secrets = {'sol_password': password}
        path = (getattr(settings, 'FISCAL_CERT_P12_PATH', '') or '').strip()
        if path:
            try:
                with open(path, 'rb') as handle:
                    data = handle.read(MAX_CERTIFICATE_BYTES + 1)
            except OSError:
                data = b''
            if data and len(data) <= MAX_CERTIFICATE_BYTES:
                secrets['certificate_p12'] = base64.b64encode(data).decode()
                secrets['certificate_password'] = getattr(settings, 'FISCAL_CERT_P12_PASSWORD', '') or ''
        return {'ruc': ruc, 'sol_user': user}, secrets


registry.register(SunatProvider())
