"""
One judge for what a person says about who they are — CLIENT-SEC-01.

A phone and an identity document are typed in three places today, and each
judges them its own way: the web checkout, the app's checkout and the panel's
customer form. This module is the ONE set of rules for what a buyer writes in
their own profile, made of the strictest rule each of those already had:

  * a phone is a Peruvian mobile, as the web checkout accepts it;
  * a DNI is 8 digits; a RUC is 11, with a prefix SUNAT gives and its check
    digit; a foreigner's card is 6 to 12 letters and digits.

IT JUDGES SHAPE, AND SHAPE IS NOT IDENTITY. A number that passes here is a
number that could exist; nothing says it is this person's. Nobody is asked for
a photograph of a document or of their face to buy, and nothing here is a
reason to start.

The three older judges are not changed by this module (they are a debt of
their own); it does not import their decisions except the phone pattern, which
is the web checkout's and must stay one pattern.
"""
from __future__ import annotations

import re

from .models import DocumentType, normalize_customer_phone, normalize_document_number
# The SAME pattern the web checkout judges phones by, imported rather than
# copied: a second one would drift into a number one screen takes and the other
# refuses.
from .serializers import _PERU_PHONE_RE

#: What a buyer is told when a document cannot be theirs to write here because
#: it is already somebody's in this shop. ONE sentence for every such case: it
#: does not say whose it is, nor whether that somebody has an account.
DOCUMENT_REFUSED = 'No pudimos guardar ese documento. Revisa los datos o comunícate con la tienda.'

_DNI = re.compile(r'\d{8}')
_RUC = re.compile(r'(10|15|16|17|20)\d{9}')
_CE = re.compile(r'[A-Z0-9]{6,12}')
_RUC_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


class IdentityError(ValueError):
    """What was typed is not what the field asks for. `field` says which one."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def clean_phone(value) -> str:
    """
    A Peruvian mobile as the CRM keeps it (digits, with the `+` if it had one),
    or '' for no phone. Anything else is refused.
    """
    if value in (None, ''):
        if value is None:
            raise IdentityError('phone', 'Indica un número de teléfono válido.')
        return ''
    if not isinstance(value, str):
        raise IdentityError('phone', 'Indica un número de teléfono válido.')
    if not value.strip():
        return ''
    if not _PERU_PHONE_RE.match(re.sub(r'\s', '', value)):
        raise IdentityError(
            'phone',
            'Teléfono inválido. Ejemplos aceptados: 987654321, +51987654321, +51 987 654 321.',
        )
    return normalize_customer_phone(value)


def phone_key(value) -> str:
    """
    A phone as something to COMPARE, or '': its last nine digits, which is the
    whole of a Peruvian mobile. `987 654 321`, `+51987654321` and `51 987654321`
    are one number written three ways. Never stored, never shown.
    """
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else ''


def ruc_check_digit_matches(number: str) -> bool:
    """
    Whether the eleventh digit of a RUC is the one its first ten produce
    (module 11, weights 5·4·3·2·7·6·5·4·3·2).
    """
    if not isinstance(number, str) or not re.fullmatch(r'\d{11}', number):
        return False
    total = sum(int(digit) * weight for digit, weight in zip(number, _RUC_WEIGHTS))
    expected = 11 - (total % 11)
    # 10 and 11 have no digit of their own: they are written 0 and 1.
    return int(number[10]) == {10: 0, 11: 1}.get(expected, expected)


def clean_document(document_type, document_number) -> tuple[str, str]:
    """
    `(type, number)` as the CRM keeps them, or `('', '')` for no document.

    Both or neither: a number without its type cannot be judged, and a type
    without a number says nothing.
    """
    if not isinstance(document_type, str) or not isinstance(document_number, str):
        raise IdentityError('document_number', 'Documento inválido.')
    kind = document_type.strip()
    number = normalize_document_number(document_number)

    if not kind and not number:
        return '', ''
    if not kind:
        raise IdentityError('document_type', 'Indica el tipo de documento.')
    if kind not in DocumentType.values:
        raise IdentityError('document_type', 'Tipo de documento no válido.')
    if not number:
        raise IdentityError('document_number', 'Indica el número de documento.')

    if kind == DocumentType.DNI:
        if not _DNI.fullmatch(number):
            raise IdentityError('document_number', 'El DNI debe tener exactamente 8 dígitos.')
    elif kind == DocumentType.RUC:
        if not re.fullmatch(r'\d{11}', number):
            raise IdentityError('document_number', 'El RUC debe tener exactamente 11 dígitos.')
        if not _RUC.fullmatch(number) or not ruc_check_digit_matches(number):
            raise IdentityError('document_number', 'El RUC no es válido. Revisa el número.')
    elif not _CE.fullmatch(number):
        raise IdentityError(
            'document_number',
            'El Carnet de Extranjería debe tener entre 6 y 12 caracteres alfanuméricos.',
        )
    return kind, number
