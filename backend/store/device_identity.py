"""
DEVICE-IDENTITY — qué identifica a un equipo, y cuándo es obligatorio.

Sirve a dos dominios con la misma regla: el equipo que un cliente deja en el
taller (`Device`) y la unidad que la tienda tiene para vender (`StockUnit`).

LA REGLA ES POR TIPO, NO «IMEI OBLIGATORIO». Una laptop, una tablet Wi-Fi o un
reloj GPS no tienen IMEI; exigirlo llenaría la base de marcadores. Un teléfono
sí lo tiene, y ahí se pide.

VACÍO ES «NO TIENE». Nunca «N/A», «no tiene», «0000…» ni un número que no
supera su dígito de control: eso son datos falsos que más tarde parecen
duplicados de verdad. Lo que no se pudo leer se deja vacío y se dice por qué.

No conoce marcas ni modelos: la plataforma no es el software de un revendedor
de una marca. Decide por el TIPO de equipo, que es una lista corta y genérica.
"""
from __future__ import annotations

import re

#: Tipos (los de `Device.TYPE_*`) que normalmente llevan número de serie.
SERIAL_REQUIRED_TYPES = frozenset({'phone', 'tablet', 'laptop', 'desktop', 'console', 'wearable'})
#: Tipos que SIEMPRE son celulares. Una tablet o un reloj pueden serlo o no.
IMEI_REQUIRED_TYPES = frozenset({'phone'})

SERIAL_MIN, SERIAL_MAX = 4, 40
PENDING_REASON_MAX = 200

#: Lo que la gente escribe cuando un campo es obligatorio y no tiene el dato.
_PLACEHOLDERS = frozenset({
    'NA', 'N/A', 'N.A', 'N.A.', 'NO', 'NONE', 'NULL', 'NINGUNO', 'NINGUNA', 'NOTIENE',
    'NOAPLICA', 'SINSERIE', 'SINSERIAL', 'SINIMEI', 'SINNUMERO', 'S/N', 'SN', 'XXX', 'XXXX',
    'DESCONOCIDO', 'PENDIENTE', 'ILEGIBLE', 'TEST', 'PRUEBA',
})


class DeviceIdentityError(Exception):
    """Un identificador que no se puede aceptar. `errors`: campo -> [mensajes]."""

    def __init__(self, errors):
        if isinstance(errors, str):
            errors = {'detail': [errors]}
        self.errors = errors
        super().__init__(' '.join(m for messages in errors.values() for m in messages))


def requires_serial(device_type: str) -> bool:
    return device_type in SERIAL_REQUIRED_TYPES


def requires_imei(device_type: str) -> bool:
    return device_type in IMEI_REQUIRED_TYPES


def luhn_check_digit(body: str) -> str:
    """El dígito de control (Luhn) de los 14 primeros dígitos de un IMEI."""
    total = 0
    for index, char in enumerate(reversed(body)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def _is_placeholder(text: str) -> bool:
    compact = re.sub(r'[\s._-]', '', text).upper()
    return (
        compact in _PLACEHOLDERS
        or text.strip().upper() in _PLACEHOLDERS
        or len(set(compact)) <= 1          # «0000000», «-----», «XXXX»
    )


def clean_imei(value, *, label: str = 'El IMEI') -> str:
    """
    Quince dígitos con su dígito de control. Vacío si no se dio.

    Los espacios, guiones y puntos con que se suele dictar se quitan; cualquier
    otra cosa es un error, no algo que arreglar en silencio.
    """
    text = str(value or '').strip()
    if not text:
        return ''
    digits = re.sub(r'[\s.\-/]', '', text)
    if not digits.isdigit():
        raise DeviceIdentityError(
            f'{label} son 15 dígitos. Si el equipo no tiene, deja el campo vacío.'
        )
    if len(digits) != 15:
        raise DeviceIdentityError(f'{label} tiene 15 dígitos; se recibieron {len(digits)}.')
    if len(set(digits)) == 1:
        raise DeviceIdentityError(f'{label} no es válido. Si el equipo no tiene, deja el campo vacío.')
    if luhn_check_digit(digits[:14]) != digits[14]:
        raise DeviceIdentityError(
            f'{label} no supera su dígito de control: revisa que esté bien copiado.'
        )
    return digits


def clean_serial(value) -> str:
    """Mayúsculas y sin espacios. Vacío si no se dio; un marcador es un error."""
    text = str(value or '').strip()
    if not text:
        return ''
    if _is_placeholder(text):
        raise DeviceIdentityError(
            'Eso no es un número de serie. Si el equipo no tiene o no se puede leer, '
            'deja el campo vacío.'
        )
    serial = re.sub(r'\s+', '', text).upper()
    if not SERIAL_MIN <= len(serial) <= SERIAL_MAX:
        raise DeviceIdentityError(
            f'El número de serie tiene entre {SERIAL_MIN} y {SERIAL_MAX} caracteres.'
        )
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9\-/._]*', serial):
        raise DeviceIdentityError('El número de serie sólo lleva letras, números y guiones.')
    return serial


def clean_pending_reason(value) -> str:
    text = ' '.join(str(value or '').split())
    if len(text) > PENDING_REASON_MAX:
        raise DeviceIdentityError(
            {'identifiers_pending_reason': [f'El motivo admite hasta {PENDING_REASON_MAX} caracteres.']}
        )
    return text


def validate(*, device_type: str, serial_number='', imei='', imei2='', pending_reason='') -> dict:
    """
    Los identificadores de un equipo, limpios, o `DeviceIdentityError` con el
    campo que falla.

    `pending_reason` es la salida para lo que no se pudo leer: con un motivo,
    un identificador obligatorio puede quedar vacío. Sin motivo, no.
    """
    errors: dict[str, list[str]] = {}
    cleaned = {'serial_number': '', 'imei': '', 'imei2': ''}

    for name, cleaner in (
        ('serial_number', clean_serial),
        ('imei', clean_imei),
        ('imei2', lambda v: clean_imei(v, label='El segundo IMEI')),
    ):
        try:
            cleaned[name] = cleaner({'serial_number': serial_number, 'imei': imei, 'imei2': imei2}[name])
        except DeviceIdentityError as exc:
            errors[name] = [str(exc)]

    reason = ''
    try:
        reason = clean_pending_reason(pending_reason)
    except DeviceIdentityError as exc:
        errors.update(exc.errors)

    if 'imei2' not in errors and cleaned['imei2']:
        if not cleaned['imei'] and 'imei' not in errors:
            errors['imei2'] = ['El segundo IMEI acompaña al primero: indica primero el IMEI principal.']
        elif cleaned['imei2'] == cleaned['imei']:
            errors['imei2'] = ['El segundo IMEI es distinto del primero.']

    if not reason:
        if requires_serial(device_type) and not cleaned['serial_number'] and 'serial_number' not in errors:
            errors['serial_number'] = [
                'Indica el número de serie. Si no se puede leer, explica por qué en '
                '«Motivo por el que falta».'
            ]
        if requires_imei(device_type) and not cleaned['imei'] and 'imei' not in errors:
            errors['imei'] = [
                'Un teléfono lleva IMEI. Si no se puede leer, explica por qué en '
                '«Motivo por el que falta».'
            ]

    if errors:
        raise DeviceIdentityError(errors)

    complete = (
        (not requires_serial(device_type) or bool(cleaned['serial_number']))
        and (not requires_imei(device_type) or bool(cleaned['imei']))
    )
    # El motivo sólo se guarda si de verdad falta algo: con todo completo no
    # explica nada.
    cleaned['identifiers_pending_reason'] = '' if complete else reason
    return cleaned


def mask(value, *, visible: int = 4) -> str:
    """«•••••••••••1234»: para el cliente y para un mensaje. Nunca el número entero."""
    text = str(value or '')
    if not text:
        return ''
    shown = min(visible, max(1, len(text) // 2))
    return '•' * (len(text) - shown) + text[-shown:]
