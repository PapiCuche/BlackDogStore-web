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
"""
from __future__ import annotations

import io
import textwrap
import unicodedata

ESC = b'\x1b'
GS = b'\x1d'

#: Página de códigos → el número que la selecciona con `ESC t n`.
CODE_PAGES = {'cp437': 0, 'cp850': 2, 'cp1252': 16, 'cp858': 19}

#: Columnas de la letra normal según el ancho del rollo.
COLUMNS = {80: 48, 58: 32}

#: Puntos de ancho imprimibles según el rollo (8 puntos por milímetro).
DOTS = {80: 576, 58: 384}

#: Lo que ninguna página de códigos de una térmica tiene, y por qué se cambia.
_SUBSTITUTES = {'—': '-', '–': '-', '·': '-', '«': '"', '»': '"', '“': '"', '”': '"',
                '‘': "'", '’': "'", '…': '...', ' ': ' '}


class Receipt:
    """Acumula los bytes de un ticket."""

    def __init__(self, *, paper_width_mm: int = 80, encoding: str = 'cp858'):
        self.encoding = encoding if encoding in CODE_PAGES else 'cp858'
        self.columns = COLUMNS.get(paper_width_mm, 48)
        self.dots = DOTS.get(paper_width_mm, 576)
        self._out = bytearray()
        self._out += ESC + b'@'                                   # reinicia la impresora
        self._out += ESC + b't' + bytes([CODE_PAGES[self.encoding]])

    # -- texto ----------------------------------------------------------------

    def _encode(self, text: str) -> bytes:
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
        self._out += ESC + b'a' + {'left': b'\x00', 'center': b'\x01', 'right': b'\x02'}[align]
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
        self._out += b'-' * self.columns + b'\n'
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

    def image(self, png: bytes, *, max_dots: int = 384):
        """Una imagen de un bit por punto, centrada. Negro donde la imagen es oscura."""
        from PIL import Image

        picture = Image.open(io.BytesIO(png)).convert('RGBA')
        paper = Image.new('RGB', picture.size, (255, 255, 255))
        paper.paste(picture, mask=picture.getchannel('A'))
        limit = min(max_dots, self.dots)
        if paper.width > limit:
            paper = paper.resize(
                (limit, max(1, round(paper.height * limit / paper.width))), Image.LANCZOS)
        bits = paper.convert('L').point(lambda v: 0 if v < 128 else 255, mode='1')
        width_bytes = (bits.width + 7) // 8
        rows = bytearray()
        pixels = bits.load()
        for y in range(bits.height):
            for xb in range(width_bytes):
                byte = 0
                for bit in range(8):
                    x = xb * 8 + bit
                    if x < bits.width and pixels[x, y] == 0:
                        byte |= 0x80 >> bit
                rows.append(byte)
        self._out += ESC + b'a\x01'
        self._out += GS + b'v0\x00' + bytes([
            width_bytes & 0xFF, width_bytes >> 8, bits.height & 0xFF, bits.height >> 8])
        self._out += bytes(rows) + b'\n'
        return self

    # -- cierre ---------------------------------------------------------------

    def cut(self):
        self.feed(3)
        self._out += GS + b'V\x42\x00'     # corte parcial tras avanzar
        return self

    def to_bytes(self) -> bytes:
        return bytes(self._out)


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
        r.line('AMBIENTE DE PRUEBAS - SUNAT BETA', align='center', bold=True)
        r.line('SIN VALIDEZ TRIBUTARIA', align='center', bold=True)
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
            f" {item['quantity']} {item['unit_code']} x {money} {item['unit_price_with_tax']:.2f}",
            f"{money} {item['amount']:.2f}")
    r.rule()

    rate = (document.tax_rate * 100).normalize()
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
    """La nota de venta interna, para la térmica. Mismo contenido que su PDF de 80 mm."""
    from ..sales_note_services import build_sales_note_context
    from ..sales_note_services import SALES_NOTE_DISCLAIMER

    ctx = build_sales_note_context(sales_note)
    tax = ctx['tax']
    money = tax['symbol']
    r = Receipt(paper_width_mm=paper_width_mm, encoding=encoding)

    r.line(ctx['store_name'] or ctx['store_legal_name'], align='center', bold=True)
    if ctx['store_legal_name'] and ctx['store_legal_name'] != ctx['store_name']:
        r.line(ctx['store_legal_name'], align='center')
    if ctx['store_ruc']:
        r.line(f"RUC {ctx['store_ruc']}", align='center')
    if ctx['store_address']:
        r.line(ctx['store_address'], align='center')
    if ctx['store_phone']:
        r.line(f"WhatsApp {ctx['store_phone']}", align='center')
    r.rule()

    r.line(ctx['title'], align='center', bold=True)
    r.line(ctx['number'], align='center', bold=True, large=True)
    r.line('Número interno del sistema. No es una serie fiscal.', align='center')
    r.row('Fecha:', ctx['issued_at'])
    r.row('Pedido:', f"#{ctx['order_id']}")
    r.row('Comprobante solicitado:', ctx['receipt_label'])
    r.rule()

    r.line(f"Cliente: {ctx['customer_name']}")
    if ctx['document_number'] and ctx['document_number'] != '—':
        r.line(f"{ctx['document_label']}: {ctx['document_number']}")
    r.line(f"Entrega: {ctx['delivery_label']}")
    if ctx['full_address']:
        r.line(ctx['full_address'])
    r.rule()

    for item in ctx['items']:
        r.line(str(item['name']))
        r.row(f" {item['quantity']} x {money} {item['unit_price']:.2f}",
              f"{money} {item['subtotal']:.2f}")
    r.rule()

    r.row('Subtotal', f"{money} {tax['subtotal']:.2f}")
    if tax['discount_amount'] > 0:
        r.row('Descuento', f"- {money} {tax['discount_amount']:.2f}")
    r.row(tax['base_label'], f"{money} {tax['taxable_amount']:.2f}")
    if tax['shows_tax']:
        r.row(tax['tax_label'], f"{money} {tax['tax_amount']:.2f}")
    r.row('TOTAL', f"{money} {tax['total']:.2f}", bold=True, large=True)
    if ctx['notes']:
        r.rule()
        r.line('Notas', bold=True)
        r.line(str(ctx['notes']))
    r.rule()

    r.line(SALES_NOTE_DISCLAIMER, align='center', bold=True)
    if ctx['warranty_note']:
        r.line(str(ctx['warranty_note']), align='center')
    return r.cut().to_bytes()
