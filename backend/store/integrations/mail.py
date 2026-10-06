"""
The mail backend of the running system.

Every message the application sends goes through Django's mail API, and Django
hands it to this backend. On EACH send it asks which SMTP configuration is
active — the console first, the legacy environment second — so that activating
a configuration in the panel changes where mail goes at once, with no restart.

    panel, active and enabled   →  that SMTP server and that sender
    environment (`EMAIL_*`)     →  as before the console existed
    a development backend       →  console / in-memory, when one was chosen
    nothing                     →  MailNotConfigured

There is no silent fallback to the console backend: in production a message that
cannot be sent is an error that is logged and reported by `ops_status`, never a
verification link written to a log.
"""
import logging

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend
from django.utils.module_loading import import_string

from . import secret_store, service
from .providers import smtp

logger = logging.getLogger('store.integrations')


class MailNotConfigured(Exception):
    """No SMTP configuration is active and none was given to the installation."""


def _legacy_backend(config) -> str:
    """
    The backend the environment names, when it may be used.

    SMTP from the environment only together with its `EMAIL_*` settings (that
    is `config`, resolved from them). Any other backend — the console or the
    in-memory one of development — as it is. And none at all once the console
    holds a configuration that was switched off: off is off.
    """
    legacy = getattr(settings, 'EMAIL_BACKEND_LEGACY', '')
    if not legacy or (config is None and service.has_panel_row('smtp')):
        return ''
    return legacy if (legacy != smtp.SMTP_BACKEND or config is not None) else ''


def _resolve():
    """
    The mail configuration in force, or None.

    A configuration the console holds and this server cannot open — the root key
    was changed, or the database came from another server — is no configuration.
    It is said in the log and by `ops_status`; it is not a reason to fall back to
    what the environment names, and it must not break the request that wanted
    to send a message.
    """
    try:
        return service.resolve('smtp')
    except secret_store.SecretStoreError:
        logger.error('the stored mail configuration cannot be read with this server\'s root key')
        return None


def is_configured() -> bool:
    config = _resolve()
    return config is not None or bool(_legacy_backend(config))


class RuntimeEmailBackend(BaseEmailBackend):
    def _delegate(self):
        """`(backend, sender or None)` for the configuration that is active right now."""
        config = _resolve()
        if config is not None and config.source == 'panel':
            public = config.public
            return SmtpBackend(
                host=public['host'], port=int(public['port']),
                username=public.get('username') or '', password=config.secrets.get('password', ''),
                use_tls=public.get('security') == smtp.STARTTLS,
                use_ssl=public.get('security') == smtp.IMPLICIT_TLS,
                timeout=int(public.get('timeout') or 10), fail_silently=self.fail_silently,
            ), smtp.sender(public)
        legacy = _legacy_backend(config)
        if legacy:
            return import_string(legacy)(fail_silently=self.fail_silently), None
        raise MailNotConfigured(
            'El correo no está configurado: activa un servidor SMTP en Configuración › Integraciones.'
        )

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        try:
            backend, sender = self._delegate()
        except MailNotConfigured:
            logger.error('mail not sent: no SMTP configuration is active (%d message(s))', len(email_messages))
            if self.fail_silently:
                return 0
            raise
        if sender:
            # The call sites name the installation's default sender. With a
            # configuration from the console, the sender is the console's.
            default = getattr(settings, 'DEFAULT_FROM_EMAIL', '')
            for message in email_messages:
                if not message.from_email or message.from_email == default:
                    message.from_email = sender
        return backend.send_messages(email_messages)
