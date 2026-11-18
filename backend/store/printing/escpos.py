"""
Un ticket en el idioma de una térmica de 80 mm: ESC/POS.

POR QUÉ NO SE ENVÍA EL PDF. Una térmica de red no sabe leer PDF: recibe bytes
por el puerto 9100 y los imprime según llegan. Convertir el PDF en una imagen y
mandarla punto a punto funciona, pero es lento, depende del modelo y sale
borroso. El texto nativo de la impresora es nítido y rápido, y el código QR lo
dibuja ella misma.

EL CONTENIDO ES EL MISMO QUE EL DEL PDF. Los dos salen del mismo contexto
(`build_fiscal_context`, `build_sales_note_context`): lo que dice el papel de la
térmica es lo que dice el PDF de 80 mm, y ambos son lo que dice el XML firmado.

SIN ADORNO. El logotipo se manda como una imagen de un bit por punto, tal cual,
una vez. No hay sombra que dibujar en una térmica, y no se intenta.

DOC-ESCPOS-LOGO. The sales note prints its logo too (the one frozen with it), in
the box its design gives the 80 mm ticket, at 8 dots per millimetre. No image is
taller than 24 mm (`MAX_IMAGE_HEIGHT_DOTS`), the fiscal one included: a square
logo used to print 48 x 48 mm. A logo that cannot be read prints nothing and
never stops the roll.
"""
from __future__ import annotations

import io
import logging
import textwrap
import unicodedata

logger = logging.getLogger(__name__)

ESC = b'\x1b'
GS = b'\x1d'

#: Página de códigos → el número que la selecciona con `ESC t n`.
CODE_PAGES = {'cp437': 0, 'cp850': 2, 'cp1252': 16, 'cp858': 19}

#: Columnas de la letra normal según el ancho del rollo.
COLUMNS = {80: 48, 58: 32}

#: Puntos de ancho imprimibles según el rollo (8 puntos por milímetro).
DOTS = {80: 576, 58: 384}

#: A 203 dpi head: 8 dots per millimetre.
DOTS_PER_MM = 8
#: DOC-ESCPOS-LOGO: no image on a roll is taller than 24 mm, whatever its shape.
MAX_IMAGE_HEIGHT_DOTS = 24 * DOTS_PER_MM
#: The widest the fiscal ticket's logo prints (48 mm), as it always did.
FISCAL_LOGO_DOTS = 384

_ALIGN = {'left': b'\x00', 'center': b'\x01', 'right': b'\x02'}

#: Lo que ninguna página de códigos de una térmica tiene, y por qué se cambia.
_SUBSTITUTES = {'—': '-', '–': '-', '·': '-', '«': '"', '»': '"', '“': '"', '”': '"',
                '‘': "'", '’': "'", '…': '...', ' ': ' ',
                # DOC-DESIGN-03: what the PDF font draws (WinAnsi) and cp858 lacks. A
                # company's footer or message accepted by the design must not print «?».
                '•': '-', '‰': 'o/oo', '†': '+', '‡': '+', 'Œ': 'OE', 'œ': 'oe', 'ƒ': 'f',
                'ˆ': '^', '‚': ',', '„': '"', '‹': "'", '›': "'"}


class Receipt:
    """Acumula los bytes de un ticket."""

    def __init__(self, *, paper_width_mm: int = 80, encoding: str = 'cp858'):
        self.encoding = encoding if encoding in CODE_PAGES else 'cp858'
        self.columns = COLUMNS.get(paper_width_mm, 48)
        self.dots = DOTS.get(paper_width_mm, 576)
        #: DOC-DESIGN-04: blank lines around each rule (the «amplio» density of a sales note). The head
        #: has one line height, so there is nothing tighter than 0.
        self.rule_padding = 0
        self._out = bytearray()
        self._out += ESC + b'@'                                   # reinicia la impresora
        self._out += ESC + b't' + bytes([CODE_PAGES[self.encoding]])

    # -- texto ----------------------------------------------------------------

    def _encode(self, text: str) -> bytes:
        # NINGÚN CARÁCTER DE CONTROL. El nombre, la dirección y las notas los
        # escribe el comprador, y para la impresora un ESC o un GS dentro del
        # texto es una orden: abrir el cajón, cortar, apagarse. Las órdenes las
        # pone este módulo; del texto sólo sale texto.
        text = ''.join(' ' if unicodedata.category(c) in ('Cc', 'Cf') else c for c in text)
        for odd, plain in _SUBSTITUTES.items():
            text = text.replace(odd, plain)
        try:
            return text.encode(self.encoding)
        except UnicodeEncodeError:
            # Lo que la página no tiene pierde el acento antes que el carácter.
            stripped = ''.join(
                c if _fits(c, self.encoding)
                else unicodedata.normalize('NFKD', c).encode('ascii', 'ignore').decode() or '?'
                for c in text)
            return stripped.encode(self.encoding, 'replace')

    def _style(self, *, align: str, bold: bool, large: bool):
        self._out += ESC + b'a' + _ALIGN[align]
        self._out += ESC + b'E' + (b'\x01' if bold else b'\x00')
        self._out += GS + b'!' + (b'\x11' if large else b'\x00')   # doble ancho y alto

    def line(self, text, *, align='left', bold=False, large=False):
        """Un renglón; si no cabe, se parte entre palabras (y dentro de una, si hace falta)."""
        width = self.columns // 2 if large else self.columns
        self._style(align=align, bold=bold, large=large)
        for piece in textwrap.wrap(str(text), width=width, break_long_words=True) or ['']:
            self._out += self._encode(piece) + b'\n'
        return self

    def row(self, left, right, *, bold=False, large=False):
        """Etiqueta a la izquierda y valor a la derecha, en el mismo renglón."""
        width = self.columns // 2 if large else self.columns
        left, right = str(left), str(right)
        room = width - len(right) - 1
        if room < 4:
            self.line(left, bold=bold, large=large)
            return self.line(right, align='right', bold=bold, large=large)
        pieces = textwrap.wrap(left, width=room, break_long_words=True) or ['']
        self._style(align='left', bold=bold, large=large)
        for piece in pieces[:-1]:
            self._out += self._encode(piece) + b'\n'
        last = pieces[-1]
        self._out += self._encode(last + ' ' * (width - len(last) - len(right)) + right) + b'\n'
        return self

    def rule(self):
        self._style(align='left', bold=False, large=False)
        pad = b'\n' * self.rule_padding
        self._out += pad + b'-' * self.columns + b'\n' + pad
        return self

    def feed(self, lines: int = 1):
        self._out += ESC + b'd' + bytes([max(0, min(lines, 10))])
        return self

    # -- QR e imagen ----------------------------------------------------------

    def qr(self, payload: str, *, module: int = 6):
        """El QR lo dibuja la impresora: se le dan los datos, no los puntos."""
        data = payload.encode('utf-8')
        size = len(data) + 3
        self._out += ESC + b'a\x01'
        self._out += GS + b'(k\x04\x00\x31\x41\x32\x00'                    # modelo 2
        self._out += GS + b'(k\x03\x00\x31\x43' + bytes([module])         # tamaño del módulo
        self._out += GS + b'(k\x03\x00\x31\x45\x31'                       # corrección M
        self._out += GS + b'(k' + bytes([size & 0xFF, size >> 8]) + b'\x31\x50\x30' + data
        self._out += GS + b'(k\x03\x00\x31\x51\x30'                       # imprimir
        self._out += b'\n'
        return self

    def image(self, png: bytes | None, *, max_dots: int = FISCAL_LOGO_DOTS,
              max_height: int = MAX_IMAGE_HEIGHT_DOTS, align: str = 'center', enlarge: bool = False):
        """
        Una imagen de un bit por punto. Negro donde la imagen es oscura.

        It fits a box of `max_dots` x `max_height` dots keeping its proportions (never wider than the
        paper, never taller than `MAX_IMAGE_HEIGHT_DOTS`); `enlarge` lets a small one grow to the box,
        as the PDF does. `align`: left, center or right. Nothing printable (no logo, one that cannot be
        read, a blank one): nothing is sent.
        """
        bits = _raster(png, max_width=min(max_dots, self.dots),
                       max_height=min(max_height, MAX_IMAGE_HEIGHT_DOTS), enlarge=enlarge)
        if bits is None:
            return self
        width_bytes = (bits.width + 7) // 8
        self._out += ESC + b'a' + _ALIGN.get(align, _ALIGN['center'])
        self._out += GS + b'v0\x00' + bytes([
            width_bytes & 0xFF, width_bytes >> 8, bits.height & 0xFF, bits.height >> 8])
        # Packed rows, the first dot in the high bit, 1 = ink: PIL's «1;I». A row is padded with paper.
        self._out += bits.tobytes('raw', '1;I') + b'\n'
        return self

    # -- cierre ---------------------------------------------------------------

    def cut(self):
        self.feed(3)
        self._out += GS + b'V\x42\x00'     # corte parcial tras avanzar
        return self

    def to_bytes(self) -> bytes:
        return bytes(self._out)


def _raster(png: bytes | None, *, max_width: int, max_height: int, enlarge: bool):
    """
    `png` as a 1-bit image (PIL mode «1») ready for a thermal head, or None when there is nothing to print.

      * Transparent is paper: the image is laid on white through its alpha, so the colour hidden
        under a transparent pixel (often black) never prints.
      * A pale logo is stretched (autocontrast) so its darkest tone prints black instead of vanishing.
      * A flat logo is cut at the middle grey: clean edges. One with many mid-tones (a photograph,
        a gradient) is dithered (Floyd-Steinberg), which a 203 dpi head renders as grey.
    """
    if not png:
        return None
    from PIL import Image, ImageOps

    try:
        picture = Image.open(io.BytesIO(png))
        picture.load()
        picture = picture.convert('RGBA')
    except Exception:  # noqa: BLE001 - a logo never stops a print
        logger.warning('Logotipo ilegible para la térmica; el ticket sale sin él.', exc_info=True)
        return None
    paper = Image.new('RGB', picture.size, (255, 255, 255))
    paper.paste(picture, mask=picture.getchannel('A'))

    scale = min(max_width / paper.width, max_height / paper.height)
    if not enlarge:
        scale = min(scale, 1.0)
    size = (max(1, min(max_width, round(paper.width * scale))),
            max(1, min(max_height, round(paper.height * scale))))
    if size != paper.size:
        paper = paper.resize(size, Image.LANCZOS)

    grey = ImageOps.autocontrast(paper.convert('L'))
    histogram = grey.histogram()
    if not sum(histogram[:128]):
        return None                                      # all paper: no empty image is sent
    midtones = sum(histogram[64:192]) / (grey.width * grey.height)
    if midtones > 0.25:
        return grey.convert('1', dither=Image.Dither.FLOYDSTEINBERG)
    return grey.point(lambda v: 0 if v < 128 else 255, mode='1')


def _fits(char: str, encoding: str) -> bool:
    try:
        char.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


# ---------------------------------------------------------------------------
# Los documentos
# ---------------------------------------------------------------------------

def fiscal_ticket(document, *, paper_width_mm: int = 80, encoding: str = 'cp858') -> bytes:
    """
    El comprobante electrónico, para la térmica.

    El mismo contenido y en el mismo orden que `generate_fiscal_ticket_pdf`:
    los dos leen `build_fiscal_context`.
    """
    from ..document_legends import FISCAL_BETA_ROLL_LINES
    from ..document_style import quantity
    from ..fiscal_pdf_services import _symbol, build_fiscal_context

    ctx = build_fiscal_context(document)
    money = _symbol(ctx['currency'])
    r = Receipt(paper_width_mm=paper_width_mm, encoding=encoding)

    if ctx['logo_png']:
        r.image(ctx['logo_png'])
    r.line(ctx['issuer']['legal_name'], align='center', bold=True)
    if ctx['issuer']['trade_name']:
        r.line(ctx['issuer']['trade_name'], align='center')
    r.line(f"RUC {ctx['issuer']['tax_id']}", align='center')
    if ctx['issuer']['address']:
        r.line(ctx['issuer']['address'], align='center')
    r.rule()

    r.line(ctx['title'], align='center', bold=True)
    r.line(ctx['identifier'], align='center', bold=True, large=True)
    if ctx['is_test']:
        # «—» sale como «-»: la página de códigos no lo tiene (`_SUBSTITUTES`).
        for beta_line in FISCAL_BETA_ROLL_LINES:
            r.line(beta_line, align='center', bold=True)
    r.rule()

    if ctx.get('note'):
        note = ctx['note']
        r.row('Modifica:', f"{note['reference_type']} {note['reference']}")
        reason = note['reason_description'] or ''
        if note['reason_code']:
            reason = f"({note['reason_code']}) {reason}".strip()
        if reason:
            r.line(f'Motivo: {reason}')
        r.rule()

    customer = ctx['customer']
    r.row('Fecha:', ctx['issued_at'].strftime('%d/%m/%Y %H:%M'))
    if customer['has_document']:
        r.row(f"{customer['doc_label_short']}:", customer['doc_number'])
    r.line(f"{customer['name_label']}: {customer['legal_name']}")
    if ctx['payment_form']:
        r.line(f"Forma de pago: {ctx['payment_form']}")
    r.rule()

    r.row('Cant. Und. x P. unit.', 'V. venta')
    for item in ctx['lines']:
        r.line(str(item['description']))
        r.row(
            f" {quantity(item['quantity'])} {item['unit_code']} x {money} {item['unit_price_with_tax']:.2f}",
            f"{money} {item['amount']:.2f}")
    r.rule()

    rate = (ctx['tax_rate'] * 100).normalize()
    r.row('Op. gravada', f"{money} {ctx['taxable_amount']:.2f}")
    r.row(f'IGV ({rate:f}%)', f"{money} {ctx['tax_amount']:.2f}")
    r.row('TOTAL', f"{money} {ctx['total']:.2f}", bold=True, large=True)
    if ctx['amount_in_words']:
        r.line(f"SON: {ctx['amount_in_words']}")
    r.rule()

    r.line(f"Estado: {ctx['status_text']}", align='center')
    r.qr(ctx['qr_payload'])
    r.line(f"Valor resumen: {ctx['digest_value']}", align='center')
    r.line(f"{ctx['legend']}.", align='center')
    return r.cut().to_bytes()


def sales_note_ticket(sales_note, *, paper_width_mm: int = 80, encoding: str = 'cp858') -> bytes:
    """La nota de venta interna, para la térmica. Mismo contenido que su PDF de 80 mm; sin QR."""
    from ..document_style import unit_identifiers
    from ..sales_note_services import (
        LINE_DISCOUNT_LABEL, LINE_NET_LABEL, REFERENCE_HEADING, build_sales_note_context,
    )

    ctx = build_sales_note_context(sales_note)
    tax = ctx['tax']
    money = tax['symbol']
    r = Receipt(paper_width_mm=paper_width_mm, encoding=encoding)
    # DOC-DESIGN-03: the company's design chooses the contact lines and adds its
    # closing message and footer, as on the 80 mm PDF. NOT VERIFIED ON A PRINTER.
    design = ctx['design']
    contact = design.contact(phone=ctx['store_phone'], email=ctx['store_email'],
                             address=ctx['store_address'], website=ctx['store_website'], roll=True)
    r.rule_padding = 1 if design.density == 'airy' else 0

    if ctx.get('logo_png'):
        # DOC-ESCPOS-LOGO: the logo frozen with the note, in the 80 mm ticket's box and alignment.
        box_width, box_height = design.ticket_logo_box_mm
        r.image(ctx['logo_png'], max_dots=round(box_width * DOTS_PER_MM),
                max_height=round(box_height * DOTS_PER_MM), align=design.logo_ticket_align, enlarge=True)
    r.line(ctx['store_name'] or ctx['store_legal_name'], align='center', bold=True)
    if ctx['store_legal_name'] and ctx['store_legal_name'] != ctx['store_name']:
        r.line(ctx['store_legal_name'], align='center')
    if ctx['store_ruc']:
        r.line(f"RUC {ctx['store_ruc']}", align='center')
    if contact['address']:
        r.line(contact['address'], align='center')
    if ctx.get('branch_name'):
        r.line(f"Sucursal: {ctx['branch_name']}", align='center')
    if contact['phone']:
        r.line(f"Tel. {contact['phone']}", align='center')
    if contact['email']:
        r.line(contact['email'], align='center')
    if contact['website']:
        r.line(contact['website'], align='center')
    r.rule()

    r.line(ctx['title'].upper(), align='center', bold=True)
    if ctx.get('stamp'):
        # SALES-NOTE-CONTROL-01: a void note says so on the roll as well — a
        # ticket queued before the void still prints marked.
        r.line(ctx['stamp'], align='center', bold=True, large=True)
    r.line(ctx['number'], align='center', bold=True, large=True)
    r.row('Fecha:', ctx['issued_at'])
    if design.show_order:
        r.row('Pedido:', f"#{ctx['order_id']}")
    # Sin «Comprobante solicitado», diga lo que diga `show_requested_receipt`
    # (DOC-LEGEND-01): la nota no nombra ningún comprobante.
    if ctx.get('payment_label'):
        r.row('Tipo de pago:', ctx['payment_label'])
    if ctx.get('seller_name') and design.show_seller:
        r.row('Atendido por:', ctx['seller_name'])
    r.rule()

    r.line(f"Cliente: {ctx['customer_name']}")
    if ctx['document_number'] and ctx['document_number'] != '—':
        r.line(f"{ctx['document_label']}: {ctx['document_number']}")
    r.line(f"Entrega: {ctx['delivery_label']}")
    if ctx['full_address'] and design.show_delivery_address:
        r.line(ctx['full_address'])
    r.rule()

    for item in ctx['items']:
        r.line(str(item['name']))
        if item.get('code'):
            r.line(f"Cód. {item['code']}")
        for unit in item.get('units') or []:
            for identifier in unit_identifiers(unit):
                r.line(identifier)
        # DOC-NV-DISCOUNT: list price; a promoted line adds its discount and what is left.
        r.row(f" {item['quantity']} x {money} {item['list_price']:.2f}",
              f"{money} {item['regular_amount']:.2f}")
        if item['discount'] > 0:
            r.row(f" {LINE_DISCOUNT_LABEL} {item['discount_reason']}".rstrip(),
                  f"- {money} {item['discount']:.2f}")
            r.row(f" {LINE_NET_LABEL}", f"{money} {item['subtotal']:.2f}")
    r.rule()

    r.row('Subtotal', f"{money} {tax['subtotal']:.2f}")
    for label, amount in ctx['discount_rows']:
        r.row(label, f"- {money} {amount:.2f}")
    reference = design.tax_rows(tax)
    if reference:
        r.line(REFERENCE_HEADING, bold=True)
    for label, amount in reference:
        r.row(label, f"{money} {amount:.2f}")
    r.row('TOTAL', f"{money} {tax['total']:.2f}", bold=True, large=design.emphasize_total)
    if ctx.get('amount_in_words') and design.show_amount_words:
        r.line(f"SON: {ctx['amount_in_words']}")
    for label, amount in ctx['cash_rows']:
        r.row(label, f"{money} {amount:.2f}")
    if ctx.get('product_count') and design.show_counts:
        r.row('Productos / unidades', f"{ctx['product_count']} / {ctx['unit_count']}")

    def notes():
        if ctx['notes']:
            r.rule()
            r.line('Notas', bold=True)
            r.line(str(ctx['notes']))

    def separator():
        # Sin leyenda (DOC-LEGEND-01): sólo el filete que cierra los totales.
        r.rule()

    def warranty():
        if ctx['warranty_note']:
            r.line(str(ctx['warranty_note']), align='center')

    def message(block):
        # DOC-DESIGN-04: a message of the company, line by line; the closing one in bold.
        for text in design.message_lines(block):
            r.line(text, align='center', bold=block == 'thank_you')

    # DOC-DESIGN-04: the blocks after the totals in the company's order, as on the 80 mm PDF. The
    # closing rule stays where the legal line was: after the notes when they come first, else first.
    separated = False
    for block in design.closing_order:
        if block != 'notes' and not separated:
            separator()
            separated = True
        {'notes': notes, 'warranty': warranty}.get(block, lambda: message(block))()
    if not separated:
        separator()
    if design.footer_text:
        r.line(design.footer_text, align='center')
    return r.cut().to_bytes()
