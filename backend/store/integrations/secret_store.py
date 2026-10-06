"""
The secret store: the one place where a credential becomes ciphertext and back.

A credential typed into the console is sealed here before it reaches the
database, and opened here — and only here — when an adapter needs it.

THE ROOT KEY IS NOT IN THE DATABASE. It is `APP_CONFIG_ENCRYPTION_KEY`, a value
of the deployment like `SECRET_KEY`, and the console cannot read or change it.
A copy of the database without that key holds no usable credential. Losing the
key loses the stored credentials: they have to be typed again.

NO CRYPTOGRAPHY OF OUR OWN. Sealing is Fernet (AES-128-CBC with HMAC-SHA256,
authenticated) from the `cryptography` package, through `MultiFernet` so that
the key can be rotated: the current key seals, the current and the previous
ones open.

A SEALED VALUE BELONGS TO ONE ROW. What is sealed is not just the secrets but
the secrets together with the row they are for (provider, company, slot). The
text of one row pasted into another opens, authenticates — and is refused.
"""
import base64
import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

KEY_SETTING = 'APP_CONFIG_ENCRYPTION_KEY'
PREVIOUS_SETTING = 'APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS'


class SecretStoreError(Exception):
    """The store cannot seal or open. Its message never contains a secret."""


class SecretStoreUnavailable(SecretStoreError):
    """There is no usable root key in this deployment."""


def _development_key() -> bytes:
    """
    Development only. Without a root key a developer's machine derives one from
    SECRET_KEY, so the console works out of the box. Production never does this:
    a key derived from another secret is not a second secret.
    """
    derived = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=b'integration-secret-store/development-only',
    ).derive(settings.SECRET_KEY.encode('utf-8'))
    return base64.urlsafe_b64encode(derived)


def _keys() -> list:
    current = (getattr(settings, KEY_SETTING, '') or '').strip()
    previous = [k.strip() for k in (getattr(settings, PREVIOUS_SETTING, '') or '').split(',') if k.strip()]
    if not current:
        if getattr(settings, 'DEBUG', False):
            return [_development_key()]
        raise SecretStoreUnavailable(
            f'Falta {KEY_SETTING} en el servidor: sin la clave raíz no se puede guardar ni leer '
            'ninguna credencial. Se genera una vez y se guarda junto a SECRET_KEY.'
        )
    return [current.encode('ascii', 'ignore'), *(k.encode('ascii', 'ignore') for k in previous)]


def _fernet() -> MultiFernet:
    try:
        return MultiFernet([Fernet(key) for key in _keys()])
    except (ValueError, TypeError) as exc:
        raise SecretStoreUnavailable(
            f'{KEY_SETTING} no es una clave válida. Genera una con '
            '`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.'
        ) from exc


def is_available() -> bool:
    try:
        _fernet()
    except SecretStoreError:
        return False
    return True


def seal(context: str, secrets: dict) -> str:
    """The secrets of ONE row, as text that only this deployment can open."""
    if not secrets:
        return ''
    payload = json.dumps({'for': context, 'secrets': secrets}, sort_keys=True).encode('utf-8')
    return _fernet().encrypt(payload).decode('ascii')


def open_sealed(context: str, token: str) -> dict:
    if not token:
        return {}
    try:
        payload = json.loads(_fernet().decrypt(token.encode('ascii')))
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise SecretStoreError(
            'Las credenciales guardadas no se pueden abrir con la clave raíz de este servidor.'
        ) from exc
    if payload.get('for') != context:
        raise SecretStoreError('Las credenciales guardadas no pertenecen a esta integración.')
    return dict(payload.get('secrets') or {})
