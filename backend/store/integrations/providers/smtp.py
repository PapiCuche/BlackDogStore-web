"""
Mail over SMTP.

The adapter the rest of the application reaches through Django's mail API (see
`store.integrations.mail`): verification, password recovery, order mail,
invitations and notifications all leave through whatever is active here.
"""
import logging
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from django.conf import settings

from .. import registry
from ..registry import ConfigError, Field, Provider, TestOutcome

logger = logging.getLogger(__name__)

STARTTLS, IMPLICIT_TLS, PLAIN = 'starttls', 'ssl', 'none'
LOOPBACK = {'localhost', '127.0.0.1', '::1'}
SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'


def sender(public: dict) -> str:
    """`Nombre <correo>` or just the address."""
    name = (public.get('from_name') or '').strip()
    address = (public.get('from_email') or '').strip()
    return formataddr((name, address)) if name else address


def test_message(*, sender: str, send_to: str) -> EmailMessage:
    """
    The message that proves the server delivers.

    Where this installation's storefront has an e-mail template it goes dressed
    in it, so whoever asked sees in a real inbox what customers will see. It has
    nothing to press and no link that gives access to anything. Anywhere else,
    and if the template cannot be filled, it is the two plain lines it was: the
    test is of the server, not of the design.
    """
    def envelope() -> EmailMessage:
        message = EmailMessage()
        message['From'] = sender
        message['To'] = send_to
        return message

    try:
        from ... import mail

        if mail.available():
            dressed = mail.render('smtp_test', mail.builders.smtp_test(brand=mail.brand()))
            # Assembled in here too: a subject the mail library refuses is one
            # more way the design can fail, and it must not fail the test.
            message = envelope()
            message['Subject'] = dressed.subject
            message.set_content(dressed.text)
            message.add_alternative(dressed.html, subtype='html')
            return message
    except Exception:  # noqa: BLE001
        logger.exception('el mensaje de prueba no se pudo componer con la plantilla')
    message = envelope()
    message['Subject'] = 'Prueba de correo'
    message.set_content('Este mensaje comprueba que la plataforma puede enviar correo con esta '
                        'configuración.\nNo contiene ningún enlace ni requiere ninguna acción.\n')
    return message


def connect(public: dict, password: str):
    """An authenticated SMTP session for this configuration. The caller closes it."""
    host, port, timeout = public['host'], int(public['port']), int(public.get('timeout') or 10)
    security = public.get('security') or STARTTLS
    if security == IMPLICIT_TLS:
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    try:
        server.ehlo()
        if security == STARTTLS:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if public.get('username'):
            server.login(public['username'], password or '')
    except Exception:
        try:
            server.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    return server


class SmtpProvider(Provider):
    id = 'smtp'
    label = 'Correo SMTP'
    category = 'email'
    scope = registry.SCOPE_PLATFORM
    description = ('El servidor por el que salen los correos de la plataforma: verificación de cuenta, '
                   'recuperación de contraseña, pedidos, invitaciones y avisos.')
    fields = (
        Field('host', 'Servidor SMTP', required=True, kind='host', help='Sólo el nombre: smtp.proveedor.com'),
        Field('port', 'Puerto', required=True, kind='int', default=587, minimum=1, maximum=65535),
        Field('security', 'Seguridad', required=True, kind='choice', default=STARTTLS, choices=(
            (STARTTLS, 'STARTTLS (puerto 587)'),
            (IMPLICIT_TLS, 'SSL/TLS desde el primer byte (puerto 465)'),
            (PLAIN, 'Ninguna (sólo un servidor en esta misma máquina)'),
        )),
        Field('username', 'Usuario'),
        Field('password', 'Contraseña o contraseña de aplicación', secret=True),
        Field('from_email', 'Correo remitente', required=True, kind='email'),
        Field('from_name', 'Nombre del remitente'),
        Field('timeout', 'Espera máxima (segundos)', required=True, kind='int', default=10, minimum=1, maximum=60),
    )

    test_fields = (
        Field('send_to', 'Enviar un correo de prueba a', kind='email',
              help='Opcional. Vacío, sólo se comprueba la conexión y no se envía nada.'),
    )

    def clean(self, public, secrets):
        errors = {}
        security, host, port = public.get('security'), (public.get('host') or '').lower(), public.get('port')
        if security == PLAIN and host and host not in LOOPBACK:
            errors['security'] = ('Sin cifrar, la contraseña del correo viajaría en claro. Sólo se admite para un '
                                  'servidor en esta misma máquina.')
        if port == 465 and security == STARTTLS:
            errors['security'] = 'El puerto 465 cifra desde el primer byte: elige SSL/TLS.'
        if public.get('username') and not secrets.get('password'):
            errors['password'] = 'Falta la contraseña de ese usuario.'
        if secrets.get('password') and not public.get('username'):
            errors['username'] = 'Falta el usuario al que pertenece esa contraseña.'
        if errors:
            raise ConfigError(errors)

    def test(self, config, send_to=None, **_options) -> TestOutcome:
        """
        Connect, negotiate TLS and sign in; optionally deliver one message. A
        failure is reported as what KIND of failure it was. The server's own text
        is never passed on: some servers repeat the user name in it.
        """
        public = config.public
        try:
            server = connect(public, config.secrets.get('password', ''))
        # ORDER MATTERS: smtplib's exceptions and ssl's are all OSError subclasses,
        # so the specific ones are named before the general one.
        except smtplib.SMTPAuthenticationError:
            return TestOutcome(False, 'auth_failed', 'El servidor rechazó el usuario o la contraseña.')
        except smtplib.SMTPNotSupportedError:
            return TestOutcome(False, 'tls_invalid', 'El servidor no ofrece el cifrado elegido.')
        except smtplib.SMTPServerDisconnected as exc:
            if 'timed out' in str(exc):
                return TestOutcome(False, 'timeout', 'El servidor no respondió a tiempo.')
            return TestOutcome(False, 'refused', 'El servidor cerró la conexión.')
        except smtplib.SMTPException:
            return TestOutcome(False, 'refused', 'El servidor no aceptó la conexión.')
        except ssl.SSLError:
            return TestOutcome(False, 'tls_invalid', 'El cifrado con el servidor falló: certificado o protocolo no válidos.')
        except (socket.timeout, TimeoutError):
            return TestOutcome(False, 'timeout', 'El servidor no respondió a tiempo.')
        except UnicodeError:
            return TestOutcome(False, 'auth_failed', 'El usuario o la contraseña llevan caracteres que el protocolo no admite.')
        except OSError:
            return TestOutcome(False, 'unreachable', 'No se pudo llegar al servidor: revisa el nombre y el puerto.')
        try:
            if not send_to:
                return TestOutcome(True, 'ok', 'Conexión correcta: el servidor aceptó el cifrado y las credenciales.')
            message = test_message(sender=sender(public), send_to=send_to)
            try:
                refused = server.send_message(message)
            except (smtplib.SMTPException, OSError):
                return TestOutcome(False, 'send_failed', 'El servidor aceptó la conexión pero no el mensaje de prueba.')
            if refused:
                return TestOutcome(False, 'send_failed', 'El servidor rechazó al destinatario del mensaje de prueba.')
            return TestOutcome(True, 'ok', 'Correcto: el mensaje de prueba se entregó al servidor de correo.')
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass

    def from_env(self, company=None):
        """The `EMAIL_*` settings of an installation that has not moved to the console."""
        if getattr(settings, 'EMAIL_BACKEND_LEGACY', '') != SMTP_BACKEND or not getattr(settings, 'EMAIL_HOST', ''):
            return None
        if getattr(settings, 'EMAIL_USE_SSL', False):
            security = IMPLICIT_TLS
        elif getattr(settings, 'EMAIL_USE_TLS', False):
            security = STARTTLS
        else:
            security = PLAIN
        public = {
            'host': settings.EMAIL_HOST, 'port': int(settings.EMAIL_PORT), 'security': security,
            'username': settings.EMAIL_HOST_USER, 'from_email': settings.DEFAULT_FROM_EMAIL, 'from_name': '',
            'timeout': int(getattr(settings, 'EMAIL_TIMEOUT', 10) or 10),
        }
        return public, {'password': settings.EMAIL_HOST_PASSWORD}


registry.register(SmtpProvider())
