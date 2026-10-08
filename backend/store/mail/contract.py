"""
The shape of the data one message is made of — `ejemplos/*.json` is the contract.

Two things happen here, in this order:

  clean()     drops what is not there. A key with None, '', [] or {} disappears,
              because the template shows a block when its key EXISTS: an empty
              string would print an empty box.
  validate()  refuses what would print wrong or dangerous: a key the template
              does not know, a number nobody formatted, a link that does not go
              where ours go.
"""
import re
from urllib.parse import urlsplit

from django.conf import settings

ROOT_KEYS = frozenset({
    'asunto', 'preheader', 'momento_oro', 'imagen', 'etiqueta', 'titulo', 'saludo',
    'parrafos', 'codigo', 'progreso', 'detalle', 'datos', 'aviso', 'boton',
    'secundario', 'firma', 'motivo', 'baja_url', 'anio',
})
REQUIRED = ('asunto', 'titulo', 'parrafos', 'motivo', 'anio')

# Names the template reads INSIDE a list. A key of the same name at the top
# would leak into every row that lacks it: Mustache looks outwards.
_ROW_KEYS = frozenset({'nombre', 'valor', 'nota', 'texto', 'fecha', 'hecho', 'actual', 'pendiente'})

_URL_PATHS = (
    ('imagen', 'url'), ('imagen', 'enlace'), ('boton', 'url'), ('secundario', 'url'), ('baja_url',),
)


class ContractError(ValueError):
    """The data cannot be turned into a message as it is."""


def _says_nothing(value) -> bool:
    """None, False, or an empty text, list or dict. A number is never «nothing»: it is a mistake, and is told."""
    return value is None or value is False or (isinstance(value, (str, list, dict)) and not value)


def _half_row(original, cleaned) -> bool:
    """A row that was given a value and has none left: «Referencia:» with nothing after it."""
    return isinstance(original, dict) and 'valor' in original and 'valor' not in cleaned


def clean(value):
    """
    The same data without the keys that say nothing.

    A row of a list that loses its value goes whole: a label with nothing next
    to it says less than no row at all.
    """
    if isinstance(value, dict):
        kept = {key: clean(item) for key, item in value.items()}
        return {key: item for key, item in kept.items() if not _says_nothing(item)}
    if isinstance(value, (list, tuple)):
        rows = [(item, clean(item)) for item in value]
        return [cleaned for original, cleaned in rows if not _says_nothing(cleaned) and not _half_row(original, cleaned)]
    if isinstance(value, str):
        return value.strip()
    return value


# A host as a browser and a mail client both read it: dotted labels, nothing else.
_HOST = re.compile(r'^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$')


def _trusted_url(url: str) -> bool:
    """
    Ours, or https to a host that can be read. Never `javascript:`, never plain
    http to somewhere else.

    The site's own address is trusted whatever its scheme, because the operator
    wrote it: on a developer's machine it is http://localhost.

    Another site is allowed — a courier's tracking page, WhatsApp — so this does
    not say WHERE a link goes. It says the link goes where it appears to:
    `https://tienda.test@evil.test` and `https://\\evil.test` do not.
    """
    if not isinstance(url, str) or any(ch.isspace() or ord(ch) < 32 for ch in url) or '\\' in url:
        return False
    site = (getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    if site and (url == site or url.startswith(site + '/')):
        return True
    if not url.startswith('https://'):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port                                # a port that is not a number raises here
    except ValueError:
        return False
    host = parts.netloc if port is None else parts.netloc.rsplit(':', 1)[0]
    return bool(_HOST.match(host.lower()))                       # no user@, no [v6], no empty host


def _only_text(value, where):
    if isinstance(value, dict):
        for key, item in value.items():
            _only_text(item, f'{where}.{key}')
    elif isinstance(value, list):
        for position, item in enumerate(value):
            _only_text(item, f'{where}[{position}]')
    elif value is not True and not isinstance(value, str):
        raise ContractError(f'{where}: se esperaba texto ya formateado, no {type(value).__name__}')


def validate(data: dict) -> dict:
    """`data` itself, after checking it. Raises ContractError naming what is wrong."""
    unknown = set(data) - ROOT_KEYS
    if unknown:
        raise ContractError(f'claves que la plantilla no conoce: {sorted(unknown)}')
    if set(data) & _ROW_KEYS:
        raise ContractError(f'claves reservadas a las filas: {sorted(set(data) & _ROW_KEYS)}')
    missing = [key for key in REQUIRED if key not in data]
    if missing:
        raise ContractError(f'faltan: {missing}')
    _only_text(data, 'datos')

    paragraphs = data['parrafos']
    if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 3:
        raise ContractError('parrafos: de 1 a 3')
    steps = (data.get('progreso') or {}).get('pasos') or []
    if sum(1 for step in steps if step.get('actual')) > 1:
        raise ContractError('progreso: sólo un paso puede estar en curso')
    for step in steps:
        if sum(1 for state in ('hecho', 'actual', 'pendiente') if step.get(state)) != 1:
            raise ContractError('progreso: cada paso tiene un estado, y sólo uno')

    for path in _URL_PATHS:
        value = data
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None and not _trusted_url(value):
            raise ContractError(f'{".".join(path)}: enlace no permitido')
    return data
