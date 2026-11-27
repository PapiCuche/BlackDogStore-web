"""
SUNAT — facturación electrónica.

    ONE configuration for the installation; BETA by default, PRODUCTION only gated.

The endpoint is a table in the code (`fiscal_config.resolve_provider`) and the console
has no address to type: it chooses an ENVIRONMENT, and only two exist.

PRODUCTION (C3, docs/sunat-estado-2026-11.md §6) is chosen here by a MASTER, typing
`PRODUCCION <RUC>`, and only takes effect when:

- the installation allows it: `FISCAL_PRODUCTION_ENABLED` (env, off by default, set by
  the operator under its own GO);
- `fiscal_preflight --target production` is GO for the one company with that RUC,
  judged with the credentials and certificate being activated.

Without the switch, choosing production is refused with the sentence that names the GO.
Once production runs, going back to BETA is refused while production documents exist:
the way to stop is switching the integration off, which stops issuing without changing
the environment of anything already issued.

Credentials are the installation's (`SCOPE_PLATFORM`); `fiscal_config` checks on every
signature that the company's RUC is the SOL's, so only that company issues.

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
from ..registry import ChangeBlocked, ConfigError, Field, Provider, TestOutcome

ID = 'sunat'
ENABLE_WORD = 'EMITIR'
#: Typed, followed by the RUC, to switch SUNAT to PRODUCTION: `PRODUCCION 20123456789`.
PRODUCTION_WORD = 'PRODUCCION'
BETA = 'beta'
PRODUCTION = 'production'
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
    supported_modes = ('test', 'production')
    #: PAUSE stops only NEW documents (`fiscal_config.new_documents_paused`): what was
    #: issued keeps being sent, consulted and reconciled, and RC/RA go on. It is the
    #: normal way to stop production; switching off is the emergency one.
    pausable = True
    #: A refused activation is written to the audit trail too (C3): «somebody tried to
    #: switch SUNAT to production and could not» is something the owner has to see.
    audit_refusals = True
    description = ('Las credenciales SOL y el certificado con que se firman y envían los comprobantes. '
                   'BETA (pruebas) por defecto. PRODUCCIÓN sólo se activa con FISCAL_PRODUCTION_ENABLED '
                   'en el servidor y fiscal_preflight en GO.')
    fields = (
        Field('environment', 'Entorno de SUNAT', required=True, kind='choice', default=BETA,
              choices=((BETA, 'BETA (pruebas, sin validez tributaria)'),
                       (PRODUCTION, 'PRODUCCIÓN (comprobantes reales)')),
              help='PRODUCCIÓN exige el GO del propietario, FISCAL_PRODUCTION_ENABLED y el preflight en GO.'),
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
        from ... import fiscal_config

        production = fiscal_config.console_environment(public) == PRODUCTION
        return 'production' if production else 'test'

    def health_note(self) -> str:
        from ... import fiscal_config

        if fiscal_config.production_enabled():
            return 'Este servidor permite emitir en PRODUCCIÓN (FISCAL_PRODUCTION_ENABLED encendido).'
        return ''

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
        if self.mode(public, secrets) == 'production':
            word = f'{PRODUCTION_WORD} {str(public.get("ruc") or "").strip()}'
            return word, (
                'Vas a emitir comprobantes REALES ante SUNAT (PRODUCCIÓN) con este RUC: cada número de serie '
                'se gasta para siempre y cada comprobante tiene validez tributaria. Sólo procede con el GO '
                '«GO activación SUNAT producción <empresa>» del propietario. '
                f'Escribe {word} para confirmarlo.')
        return ENABLE_WORD, (
            'Vas a habilitar la emisión de comprobantes electrónicos hacia SUNAT (entorno BETA, de pruebas) '
            f'en esta instalación. Escribe {ENABLE_WORD} para confirmarlo.')

    def activation_guard(self, public, secrets, payload):
        """
        Production first: whatever is typed, it is refused while the installation does not
        allow it or the preflight is not GO. Then the typed text.
        """
        from ... import fiscal_config
        from .. import service

        running = _production_chosen()
        active = service.row(ID)
        if (active is not None and str(public.get('ruc') or '').strip() != str(active.public.get('ruc') or '').strip()
                and _issued_in_production()):
            # The RUC is who issued them: another one could not send, consult or void them.
            raise ConfigError({'ruc': (
                'Ya hay comprobantes de PRODUCCIÓN emitidos con este RUC: no se cambia por otro, o quedarían '
                'sin quien los envíe, consulte o dé de baja.')})
        if self.mode(public, secrets) == 'production':
            # Already in production (running, paused or switched off): changing keys or
            # switching back on does not wait on the queue or the deadlines, which are
            # exactly what may need it running again (review of #468).
            problems = fiscal_config.production_activation_problems(public, secrets, staying=running)
            if problems:
                more = f' (y {len(problems) - 1} más)' if len(problems) > 1 else ''
                raise ConfigError({'environment': problems[0] + more})
        elif running and _issued_in_production():
            raise ConfigError({'environment': (
                'Ya hay comprobantes de PRODUCCIÓN: no se vuelve a BETA, porque sus envíos, notas y bajas '
                'pendientes quedarían sin ambiente. Para dejar de emitir, ponla en Pausa (lo normal: lo emitido se '
                'sigue enviando) o desactívala (emergencia).')})
        super().activation_guard(public, secrets, payload)

    def guard_change(self, company, *, change, public=None, prepared=None, acknowledged=False):
        """
        Revoking deletes the credentials and the certificate: with production documents
        they would be left with nothing to send, consult or reconcile them. Refused, not
        asked for a word. Pause (normal) or switch off (emergency) instead.
        """
        if change == 'revoke' and _issued_in_production():
            raise ChangeBlocked(
                'Hay comprobantes de PRODUCCIÓN: revocar borraría las credenciales con que se envían, consultan y '
                'concilian. Para detener la emisión, ponla en pausa (o desactívala en una emergencia).')
        return NotImplemented

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
        if self.mode(config.public, config.secrets) == 'production':
            problems = fiscal_config.production_activation_problems(
                config.public, config.secrets, staying=_production_chosen())
            if problems:
                return TestOutcome(False, 'unavailable', problems[0])
            return TestOutcome(True, 'ok', (
                f'Certificado «{metadata.subject[:120]}» vigente hasta el {until}. Entorno PRODUCCIÓN de SUNAT: '
                'FISCAL_PRODUCTION_ENABLED encendido y fiscal_preflight --target production en GO. '
                'La clave SOL se comprueba al emitir: no se envió nada a SUNAT.'))
        try:
            fiscal_config.intended_environment()
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


def _production_chosen() -> bool:
    """Whether the active configuration (on, paused or off) is in production mode."""
    from ... import fiscal_config
    from .. import service

    active = service.row(ID)
    return active is not None and fiscal_config.console_environment(active.public) == PRODUCTION


def _issued_in_production() -> bool:
    """Whether anything was ever issued against SUNAT PRODUCTION from this installation."""
    from ... import fiscal_config

    return fiscal_config.production_issued()


registry.register(SunatProvider())
