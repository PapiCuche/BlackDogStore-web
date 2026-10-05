"""
DOCUMENT-DESIGN — one visual system for every paper a shop hands over.

A sales note, its 80 mm ticket, the order receipt and the quote ticket are
papers of ONE shop. They were four layouts written at four moments: different
headers, different sizes, a logotype on one and not on the others. This module
is what they now share — the palette, the type scale, the money format, the
amount in words, how a serialized unit is written and how a shop introduces
itself at the top of a roll.

WHAT IS NOT HERE: any shop. Every name, tax id, address, logotype and legal text
comes from the Company, its Branch, the Order or the tenant's settings. A
constant with a brand in it would print one tenant's identity on another's
paper.

GREY, ON PURPOSE. Most of these are printed on a monochrome laser or a thermal
head. Hierarchy is carried by size, weight, rules and white space; nothing
depends on a colour surviving the printer.

THE FONTS ARE THE BUILT-IN ONES (Helvetica). Embedding a typeface would look a
little finer and would make every document heavier, slower to produce and
impossible to read back in a test without a PDF library.
"""

from __future__ import annotations

import io
from decimal import Decimal
from xml.sax.saxutils import escape as _escape

FONT = 'Helvetica'
FONT_BOLD = 'Helvetica-Bold'

#: Text, strongest to weakest.
INK = '#111827'
BODY = '#1f2937'
MUTED = '#4b5563'
#: Lines, strongest to weakest, and the only fill.
RULE = '#111827'
HAIRLINE = '#c7cbd1'
SHADE = '#f1f2f4'

#: The box a logotype must fit in, per format (points are derived by the caller).
LOGO_A4_CM = (5.4, 2.1)
LOGO_TICKET_MM = (42, 16)

NBSP = ' '


def local_stamp(moment, fmt: str = '%d/%m/%Y %H:%M') -> str:
    """
    An instant, written the way the shop's clock read it.

    The database keeps instants in UTC and hands them back in UTC. Printed as
    stored, a sale at 21:30 in Lima is dated 02:30 of the next day
    (DOC-TIMEZONE). Every date a document shows to a person goes through here.
    """
    from datetime import datetime

    from django.utils import timezone

    if moment is None:
        return '—'
    if isinstance(moment, datetime) and timezone.is_aware(moment):
        moment = timezone.localtime(moment)
    return moment.strftime(fmt)


def money(symbol: str, amount) -> str:
    """`S/ 1499.00`. The symbol, never the ISO code, and always two decimals."""
    return f'{symbol} {Decimal(amount):.2f}'


def plain(amount) -> str:
    """An amount inside a column whose heading already says what it is."""
    return f'{Decimal(amount):.2f}'


def markup(text) -> str:
    """Text made safe for a reportlab Paragraph: a name with `<` or `&` is a name, not markup."""
    return _escape(str(text if text is not None else ''))


def fit(png: bytes, max_width: float, max_height: float) -> tuple[float, float]:
    """The size at which an image fits a box, undistorted."""
    from reportlab.lib.utils import ImageReader

    width, height = ImageReader(io.BytesIO(png)).getSize()
    scale = min(max_width / width, max_height / height)
    return width * scale, height * scale


def unit_identifiers(unit: dict) -> list[str]:
    """
    How ONE physical device is written under its line: `Serie:`, `IMEI:`, `IMEI 2:`.

    Only what the unit has. A laptop prints no IMEI line; nothing is filled in
    with a placeholder.
    """
    parts = []
    if unit.get('serial_number'):
        parts.append(f"Serie: {unit['serial_number']}")
    if unit.get('imei'):
        parts.append(f"IMEI: {unit['imei']}")
    if unit.get('imei2'):
        parts.append(f"IMEI 2: {unit['imei2']}")
    return parts


def amount_in_words(total: Decimal, currency: str) -> str:
    """
    The amount in words, as a Peruvian document writes it:
    `CIENTO CINCUENTA CON 00/100 SOLES`.

    Implemented here and not with a dependency because it is thirty lines and
    Peruvian Spanish has its rules —«veintiuno», «cien» against «ciento»— that
    a generic i18n library gets wrong unless configured.
    """
    unidades = ('', 'UNO', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE',
                'OCHO', 'NUEVE', 'DIEZ', 'ONCE', 'DOCE', 'TRECE', 'CATORCE',
                'QUINCE', 'DIECISEIS', 'DIECISIETE', 'DIECIOCHO', 'DIECINUEVE',
                'VEINTE')
    decenas = ('', '', 'VEINTI', 'TREINTA', 'CUARENTA', 'CINCUENTA', 'SESENTA',
               'SETENTA', 'OCHENTA', 'NOVENTA')
    centenas = ('', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS',
                'QUINIENTOS', 'SEISCIENTOS', 'SETECIENTOS', 'OCHOCIENTOS',
                'NOVECIENTOS')

    def hasta_999(n: int) -> str:
        if n == 0:
            return ''
        if n == 100:
            return 'CIEN'
        c, resto = divmod(n, 100)
        d, u = divmod(resto, 10)
        partes = [centenas[c]] if c else []
        if resto <= 20:
            if resto:
                partes.append(unidades[resto])
        elif d == 2:
            partes.append(f'VEINTI{unidades[u].lower().upper()}' if u else 'VEINTE')
        else:
            partes.append(decenas[d] + (f' Y {unidades[u]}' if u else ''))
        return ' '.join(p for p in partes if p)

    entero = int(total)
    centimos = int((total - entero) * 100)

    if entero == 0:
        letras = 'CERO'
    else:
        millones, resto = divmod(entero, 1_000_000)
        miles, unidad = divmod(resto, 1000)
        trozos = []
        if millones:
            trozos.append('UN MILLON' if millones == 1
                          else f'{hasta_999(millones)} MILLONES')
        if miles:
            trozos.append('MIL' if miles == 1 else f'{hasta_999(miles)} MIL')
        if unidad:
            trozos.append(hasta_999(unidad))
        letras = ' '.join(trozos)

    moneda = 'SOLES' if currency == 'PEN' else currency
    return f'{letras} CON {centimos:02d}/100 {moneda}'


def ticket_header(cur, *, logo_png, name, legal_name='', tax_id='', address='',
                  branch='', phone='') -> None:
    """
    How a shop introduces itself at the top of an 80 mm roll — the same on the
    sales ticket and on the quote ticket.

    `cur` is a `ticket_services._Cursor`: with no canvas it only measures, so
    the measured height and the drawn height cannot drift apart.
    """
    from reportlab.lib.units import mm

    if logo_png:
        width, height = fit(logo_png, LOGO_TICKET_MM[0] * mm, LOGO_TICKET_MM[1] * mm)
        if cur.pdf is not None:
            from reportlab.lib.utils import ImageReader

            cur.pdf.drawImage(
                ImageReader(io.BytesIO(logo_png)),
                cur.left + (cur.width - width) / 2, cur.y - height,
                width=width, height=height,
            )
        cur.gap(height + 3)

    cur.line(name or legal_name, size=10, bold=True, align='center')
    if legal_name and legal_name != name:
        cur.line(legal_name, size=6.5, align='center')
    if tax_id:
        cur.line(f'RUC {tax_id}', size=6.5, align='center')
    if address:
        cur.line(address, size=6.5, align='center')
    if branch:
        cur.line(f'Sucursal: {branch}', size=6.5, align='center')
    if phone:
        cur.line(f'Tel. {phone}', size=6.5, align='center')
