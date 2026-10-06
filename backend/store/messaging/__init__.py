"""
Messaging providers, and how a company's configuration becomes one.

    WHERE A COMPANY'S CREDENTIALS COME FROM

1. The console (Configuración › Integraciones › WhatsApp › <empresa>): a master
   typed them, they are encrypted in the secret store, and they belong to that
   company and to no other.
2. Otherwise, REFERENCES. `CompanyMessagingSettings` stores the NAME of an
   environment variable for each secret (`manage.py configure_whatsapp`), and
   only names in the `WHATSAPP_` namespace are accepted: a row that somehow said
   `SECRET_KEY` or `DATABASE_URL` resolves to nothing.

A company the console has taken over never falls back to the references, not
even when the console configuration is switched off: off is off.

This module is the only place that turns either into a value.
"""
from __future__ import annotations

import logging
import os
import re

from django.conf import settings

from .base import MessageResult, NotConfigured, ProviderError

__all__ = [
    'MessageResult', 'NotConfigured', 'ProviderError', 'CREDENTIALS',
    'credential', 'credentials_state', 'is_valid_reference', 'phone_number_id', 'provider_for',
    'reference', 'source', 'CONSOLE_PROVIDER',
]

logger = logging.getLogger(__name__)

#: The id this integration has in the provider registry of the console.
CONSOLE_PROVIDER = 'whatsapp_cloud'

_REFERENCE = re.compile(r'^WHATSAPP_[A-Z0-9_]{2,55}$')

#: What each credential is called outside, and the column that references it.
CREDENTIALS = {
    'access_token': 'whatsapp_access_token_env',
    'app_secret': 'whatsapp_app_secret_env',
    'verify_token': 'whatsapp_verify_token_env',
}


def is_valid_reference(name) -> bool:
    return bool(_REFERENCE.match(str(name or '')))


def _console(config) -> tuple:
    """
    `(taken, public, secrets)` for this settings row's company.

    Read once per settings object: a send asks for the number and the token, a
    webhook for the secret, and each object lives for one request or one job.
    """
    cached = getattr(config, '_console_configuration', None)
    if cached is None:
        from ..integrations import secret_store, service

        active = service.row(CONSOLE_PROVIDER, config.company)
        if active is None:
            cached = (False, {}, {})
        elif not active.enabled:
            cached = (True, {}, {})
        else:
            try:
                cached = (True, dict(active.public), service.open_secrets(active))
            except secret_store.SecretStoreError:
                # Without the root key nothing can be read. It is not a reason
                # to go back to credentials the console replaced.
                logger.error('WhatsApp: no se pudo abrir la configuración de la empresa %s', config.company_id)
                cached = (True, {}, {})
        config._console_configuration = cached
    return cached


def source(config) -> str:
    """'panel' or 'env' — who answers for this company's credentials."""
    return 'panel' if _console(config)[0] else 'env'


def reference(config, which: str) -> str:
    """What the environment holds behind this company's reference, or ''."""
    name = getattr(config, CREDENTIALS[which], '') or ''
    if not is_valid_reference(name):
        return ''
    return (os.environ.get(name) or '').strip()


def credential(config, which: str) -> str:
    """The secret itself, or '' when it is not configured."""
    taken, _public, secrets = _console(config)
    if taken:
        return str(secrets.get(which) or '').strip()
    return reference(config, which)


def phone_number_id(config) -> str:
    """The number this company sends from, as the provider identifies it."""
    taken, public, _secrets = _console(config)
    if taken:
        return str(public.get('phone_number_id') or '').strip()
    return config.whatsapp_phone_number_id


def credentials_state(config) -> dict:
    """Which credentials resolve. Booleans only — this is what an API may show."""
    return {which: bool(credential(config, which)) for which in CREDENTIALS}


def provider_for(config):
    """The provider this company sends through, or `NotConfigured`."""
    number = phone_number_id(config)
    if not number:
        raise NotConfigured('falta el identificador del número')
    token = credential(config, 'access_token')
    if not token:
        raise NotConfigured('falta el token de acceso')

    kind = getattr(settings, 'WHATSAPP_PROVIDER', 'cloud_api')
    if kind == 'fake':
        from .fake import FakeProvider

        return FakeProvider(phone_number_id=number)
    if kind == 'cloud_api':
        from .whatsapp_cloud import CloudApiProvider

        return CloudApiProvider(
            phone_number_id=number, access_token=token,
            api_version=getattr(settings, 'WHATSAPP_GRAPH_API_VERSION', 'v21.0'),
        )
    raise NotConfigured('el envío por WhatsApp está desactivado en este entorno')
