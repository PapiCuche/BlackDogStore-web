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
from typing import NamedTuple
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


def quantity(value) -> str:
    """
    A quantity as a person writes it on paper: `3`, not `3.00`; `1.5`, not `1.50`.

    PRESENTATION ONLY. The XML and the stored rows keep the Decimal they had;
    this is what the A4, the 80 mm roll and the thermal print show. A whole
    number never carries decimals and never comes out as `1E+1`.
    """
    try:
        number = Decimal(str(value))
    except Exception:
        return str(value)
    # `normalize` drops the trailing zeros; `'f'` keeps `10` from reading `1E+1`.
    return format(number.normalize(), 'f')


def _winansi(char: str) -> bool:
    """
    Can the built-in Helvetica draw `char`? Its encoding is WinAnsi, which is
    Python's `cp1252` (checked against reportlab's own table for every
    character up to U+3000; the one difference, U+007F, is not text).
    """
    try:
        char.encode('cp1252')
    except UnicodeEncodeError:
        return False
    return True


def printable(text) -> str:
    """
    Text the built-in font can really draw.

    Helvetica covers WinAnsi only. For anything else reportlab does not fail: it
    falls back to ZapfDingbats and draws its «■» — a black box in the middle of
    a product name, on a paper somebody keeps. This replaces such a character
    BEFORE it reaches reportlab, with the first of these that applies:

      1. its compatibility reading, when that is all WinAnsi: `₂` → `2`,
         `№` → `No`, `ﬁ` → `fi` (accents that WinAnsi already has are kept);
      2. nothing, for invisible characters (zero-width space and joiner,
         variation selectors, combining marks, controls);
      3. one `?` — a run of characters with no reading (an emoji with its skin
         tone, a pair of ideograms) becomes ONE `?`, not one per code point.

    A `?` the person typed is left alone. The thermal print does the same in
    its own encoder (`printing.escpos.Receipt._encode`).
    """
    import unicodedata

    out: list[str] = []
    substituted = False                       # the last thing written was a substitute «?»
    for char in str(text if text is not None else ''):
        if _winansi(char):
            out.append(char)
            substituted = False
            continue
        reading = ''.join(c for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c))
        if reading and all(_winansi(c) and unicodedata.category(c)[0] != 'C' for c in reading):
            out.append(reading)
            substituted = False
        elif unicodedata.category(char)[0] in ('C', 'M') or char in '\u2028\u2029':
            continue                          # invisible: it neither draws nor breaks a run of «?»
        elif not substituted:
            out.append('?')
            substituted = True
    return ''.join(out)


def markup(text) -> str:
    """Text made safe for a reportlab Paragraph: a name with `<` or `&` is a name, not markup."""
    return _escape(printable(text))


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


class Watermark(NamedTuple):
    """
    A mark every page of a paper carries, whatever its content (DOC-UX-02).

    `lines` run diagonally across the page, behind the content; `band` is one
    line in a framed strip at the top of every A4 page, or at the top AND the
    bottom of a roll. Both are drawn by the renderer itself, not by the
    document that is being drawn: nothing a document describes can leave them
    out. The preview of a design is the user of this; the fiscal documents keep
    their own SUNAT BETA mark.
    """

    lines: tuple[str, ...] = ()
    band: str = ''


def draw_diagonal(canvas, page_width: float, page_height: float, lines, *,
                  angle: float = -38, size: float = 40, step: float = 1.2,
                  grey: float = 0.45, alpha: float = 0.17) -> None:
    """
    The lines of a watermark across the middle of a page, centred, rotated.

    The type shrinks until the longest line fits the page across its diagonal
    (an 80 mm roll is not an A4). A page much taller than wide — a long roll —
    gets one group for every two page-widths of height, so the mark is never
    missing from the part of a roll somebody photographs.
    """
    import math

    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import stringWidth

    lines = [printable(line) for line in lines if line]
    if not lines:
        return
    longest = max(stringWidth(line, FONT_BOLD, 1) for line in lines)
    size = min(size, 0.85 * page_width / abs(math.cos(math.radians(angle))) / longest)
    groups = max(1, round(page_height / (2.0 * page_width)))

    canvas.saveState()
    canvas.setFont(FONT_BOLD, size)
    canvas.setFillColor(colors.Color(grey, grey, grey, alpha=alpha))
    for group in range(groups):
        canvas.saveState()
        canvas.translate(page_width / 2, page_height * (group + 0.5) / groups)
        canvas.rotate(angle)
        for index, line in enumerate(lines):
            canvas.drawCentredString(0, -index * size * step, line)
        canvas.restoreState()
    canvas.restoreState()


def draw_band(canvas, text: str, x0: float, x1: float, y: float, height: float = 17) -> None:
    """The framed strip of a watermark: shade, rule, one bold line centred."""
    from reportlab.lib import colors

    if not text:
        return
    canvas.saveState()
    canvas.setFillColor(colors.HexColor(SHADE))
    canvas.setStrokeColor(colors.HexColor(RULE))
    canvas.setLineWidth(0.8)
    canvas.rect(x0, y, x1 - x0, height, stroke=1, fill=1)
    canvas.setFillColor(colors.HexColor(INK))
    canvas.setFont(FONT_BOLD, 8.5)
    canvas.drawCentredString((x0 + x1) / 2, y + (height - 8.5) / 2 + 1.5, printable(text))
    canvas.restoreState()


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
