"""
Messaging providers, and how a company's configuration becomes one.

    CREDENTIALS ARE REFERENCES.

`CompanyMessagingSettings` stores the NAME of an environment variable for each
secret. This module is the only place that turns a name into a value, and it
only accepts names in the `WHATSAPP_` namespace: a row that somehow said
`SECRET_KEY` or `DATABASE_URL` resolves to nothing.
"""
from __future__ import annotations

import os
import re

from django.conf import settings

from .base import MessageResult, NotConfigured, ProviderError

__all__ = [
    'MessageResult', 'NotConfigured', 'ProviderError', 'CREDENTIALS',
    'credential', 'credentials_state', 'is_valid_reference', 'provider_for',
]

_REFERENCE = re.compile(r'^WHATSAPP_[A-Z0-9_]{2,55}$')

#: What each credential is called outside, and the column that references it.
CREDENTIALS = {
    'access_token': 'whatsapp_access_token_env',
    'app_secret': 'whatsapp_app_secret_env',
    'verify_token': 'whatsapp_verify_token_env',
}


def is_valid_reference(name) -> bool:
    return bool(_REFERENCE.match(str(name or '')))


def credential(config, which: str) -> str:
    """The secret itself, or '' when the reference or the variable is missing."""
    name = getattr(config, CREDENTIALS[which], '') or ''
    if not is_valid_reference(name):
        return ''
    return (os.environ.get(name) or '').strip()


def credentials_state(config) -> dict:
    """Which credentials resolve. Booleans only — this is what an API may show."""
    return {which: bool(credential(config, which)) for which in CREDENTIALS}


def provider_for(config):
    """The provider this company sends through, or `NotConfigured`."""
    if not config.whatsapp_phone_number_id:
        raise NotConfigured('falta el identificador del número')
    token = credential(config, 'access_token')
    if not token:
        raise NotConfigured('falta el token de acceso')

    kind = getattr(settings, 'WHATSAPP_PROVIDER', 'cloud_api')
    if kind == 'fake':
        from .fake import FakeProvider

        return FakeProvider(phone_number_id=config.whatsapp_phone_number_id)
    if kind == 'cloud_api':
        from .whatsapp_cloud import CloudApiProvider

        return CloudApiProvider(
            phone_number_id=config.whatsapp_phone_number_id, access_token=token,
            api_version=getattr(settings, 'WHATSAPP_GRAPH_API_VERSION', 'v21.0'),
        )
    raise NotConfigured('el envío por WhatsApp está desactivado en este entorno')
