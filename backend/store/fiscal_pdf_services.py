"""
La representación impresa de la factura electrónica.

ESTO NO ES LA NOTA DE VENTA INTERNA, y no se reutiliza su generador.

`SalesNote` imprime «no válido como comprobante electrónico SUNAT» porque no lo
es. Este documento SÍ pertenece al dominio fiscal, así que ese aviso sería falso
aquí. Compartir el generador habría significado un `if` en cada línea y, tarde o
temprano, un aviso equivocado en uno de los dos papeles.

TODO SALE DEL DOCUMENTO CONGELADO
---------------------------------
El emisor, el adquirente y los importes vienen de `FiscalDocument`, no de
`Company` ni de `Order`. Una factura reimpresa dentro de dos años tiene que
seguir diciendo lo que dijo aunque la empresa se haya cambiado el nombre — es la
misma disciplina de C2.1, aplicada al documento entero.

EL DISEÑO DE LA EMPRESA NO TOCA NADA FISCAL (FISCAL-DESIGN-01)
--------------------------------------------------------------
Del diseño de los documentos (`document_design`) este papel toma sólo el tamaño
y la alineación del logo y, DESPUÉS del bloque obligatorio (totales, QR,
leyenda, estado y valor resumen) y aparte de él, el mensaje de agradecimiento y
el pie. Contactos, títulos, garantía y demás opciones son de la nota de venta y
no llegan aquí. Se lee el diseño vigente en cada impresión: si debe congelarse
al emitir lo decide el asesor tributario.

LA MARCA DE PRUEBAS NO ES DECORACIÓN
------------------------------------
Un comprobante emitido contra el entorno de pruebas de SUNAT no tiene validez
tributaria. Sin una marca inequívoca, alguien lo imprime y lo entrega como
factura real. Va en diagonal sobre el papel y también en texto, porque una marca
de agua se puede perder en una fotocopia mala.
"""

from __future__ import annotations

import io
import logging
from decimal import Decimal

from . import document_design as _document_design
from . import document_legends as _legends
from . import document_style as style
from . import fiscal_logo
from .fiscal.qr import build_qr_payload, render_qr_png
from .models import FiscalDocument, FiscalDocumentStatus, FiscalEnvironment

logger = logging.getLogger(__name__)

_TITLE = {
    '01': 'FACTURA ELECTRÓNICA', '03': 'BOLETA DE VENTA ELECTRÓNICA',
    '07': 'NOTA DE CRÉDITO ELECTRÓNICA', '08': 'NOTA DE DÉBITO ELECTRÓNICA',
}

#: Catálogo N.º 06 → cómo se nombra el documento del adquirente en el papel:
#: (etiqueta en A4, etiqueta en el rollo de 80 mm, etiqueta del nombre).
#:
#: Los Anexos I y II de la RS 114-2019/SUNAT piden el TIPO y el número (campo
#: 11). La etiqueta estaba escrita a mano —«RUC»— y una boleta a un DNI salía
#: diciendo que ese número era un RUC.
_CUSTOMER_DOCUMENT = {
    '6': ('RUC', 'RUC', 'Razón social'),
    '1': ('DNI', 'DNI', 'Cliente'),
    '4': ('Carné de extranjería', 'C.E.', 'Cliente'),
    '7': ('Pasaporte', 'Pasaporte', 'Cliente'),
    'A': ('Cédula diplomática', 'Céd. diplomática', 'Cliente'),
}

#: Cómo dice el XML la forma de pago y cómo se lee en el papel.
_PAYMENT_FORM = {'contado': 'Contado', 'credito': 'Crédito'}


def _customer_block(doc_type: str, doc_number: str, legal_name: str) -> dict:
    """
    Quién compra, con la etiqueta que corresponde a SU documento.

    Sin documento (código `0`, la boleta a consumidor final) no se imprime
    ninguna etiqueta de documento: no hay número que etiquetar.
    """
    labels = _CUSTOMER_DOCUMENT.get((doc_type or '').strip())
    number = (doc_number or '').strip()
    has_document = bool(labels) and number not in ('', '0', '-')
    return {
        'doc_type': doc_type,
        'doc_number': number,
        'legal_name': legal_name,
        'has_document': has_document,
        'doc_label': labels[0] if has_document else '',
        'doc_label_short': labels[1] if has_document else '',
        'name_label': labels[2] if has_document else 'Cliente',
    }


#: Etiqueta del comprobante que una nota modifica, para el «Documento que
#: modifica» del papel. Catálogo N.º 01.
_ORIGINAL_TYPE_LABEL = {'01': 'Factura', '03': 'Boleta de venta'}

#: Sólo se imprime «aceptada» cuando hay constancia. Los demás estados se dicen
#: como son: un papel que afirma una aceptación que no ocurrió es peor que uno
#: que dice «pendiente».
_STATUS_TEXT = {
    FiscalDocumentStatus.ACCEPTED: 'Aceptada por SUNAT',
    FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION:
        'Aceptada por SUNAT con observaciones',
    FiscalDocumentStatus.REJECTED: _legends.FISCAL_REJECTED_STATUS,
    FiscalDocumentStatus.SUBMISSION_ERROR: 'Pendiente de confirmación de SUNAT',
    FiscalDocumentStatus.SIGNED: 'Emitida, pendiente de envío a SUNAT',
    FiscalDocumentStatus.GENERATED: 'Generada, pendiente de firma',
    FiscalDocumentStatus.PENDING: 'Pendiente',
}

BETA_WARNING = _legends.FISCAL_BETA_WARNING


class FiscalPdfError(Exception):
    """No se puede representar este documento todavía."""


def _document_lines(rep):
    """
    Las líneas del comprobante, leídas del XML FIRMADO (FISCAL-04).

    NO se reconstruyen desde `OrderItem`/`Product`: tras la firma el comprobante
    es inmutable y su representación debe decir lo que dice el XML que SUNAT
    recibió, no lo que digan hoy tablas que pueden haber cambiado.
    """
    for line in rep.lines:
        yield {
            'description': line.description,
            'quantity': line.quantity,
            'unit_price': line.unit_price,
            'unit_price_with_tax': line.unit_price_with_tax,
            'amount': line.line_amount,
            'unit_code': line.unit_code,
            # FISCAL-A4-PROMO-LINES: the line's `00` discount (`cac:AllowanceCharge/cbc:Amount`),
            # or None when the line carries none.
            'discount': line.line_discount,
        }


def build_fiscal_context(document: FiscalDocument) -> dict:
    """Los datos de impresión, DERIVADOS DEL XML FIRMADO (autoridad de
    representación), no de tablas vivas ni del timestamp técnico de la fila."""
    if not document.signed_xml:
        raise FiscalPdfError(
            'El comprobante todavía no está firmado: sin XML firmado no hay QR '
            'ni representación impresa posible.'
        )

    from datetime import datetime
    from datetime import time as _time

    from .fiscal.representation import parse_signed_invoice_for_representation

    rep = parse_signed_invoice_for_representation(document.signed_xml)

    # FISCAL-03: la fecha LEGAL es la del XML firmado (cbc:IssueDate/IssueTime),
    # no `document.issued_at` (creación de fila). El QR también la toma de ahí.
    qr_payload = build_qr_payload(
        issuer_tax_id=rep.issuer_tax_id or document.issuer_tax_id,
        document_type=document.document_type,
        series=document.series,
        number=document.number,
        tax_amount=rep.tax_amount,
        total=rep.payable_amount,
        issue_date=rep.issue_date,
        customer_doc_type=document.customer_doc_type,
        customer_doc_number=rep.customer_doc_number or document.customer_doc_number,
        digest_value=rep.digest_value or document.digest_value,
    )
    legal_datetime = datetime.combine(rep.issue_date, rep.issue_time or _time(0, 0, 0))

    # Si es una nota (07/08): qué comprobante modifica y por qué. Del documento,
    # no de una columna nueva; `None` en un comprobante normal, de modo que la
    # plantilla sólo dibuja el bloque cuando existe.
    note = None
    if document.original_document_id:
        original = document.original_document
        note = {
            'reference': original.document_id,
            'reference_type': _ORIGINAL_TYPE_LABEL.get(
                original.document_type, 'Comprobante'),
            'reason_code': document.note_reason_code,
            'reason_description': document.note_reason_description,
        }

    return {
        'title': _TITLE.get(document.document_type, 'COMPROBANTE ELECTRÓNICO'),
        'note': note,
        'identifier': rep.document_id or document.document_id,
        'issued_at': legal_datetime,
        'currency': rep.currency or document.currency,
        'issuer': {
            'tax_id': rep.issuer_tax_id or document.issuer_tax_id,
            'legal_name': rep.issuer_legal_name or document.issuer_legal_name,
            'trade_name': document.issuer_trade_name,
            'address': document.issuer_address,
        },
        'customer': _customer_block(
            document.customer_doc_type,
            rep.customer_doc_number or document.customer_doc_number,
            rep.customer_legal_name or document.customer_legal_name,
        ),
        # Campos 62 (factura) y 58 (boleta): la leyenda nombra el comprobante.
        'legend': _legends.fiscal_representation_legend(
            _TITLE.get(document.document_type, 'comprobante electrónico')),
        'amount_in_words': rep.amount_in_words,
        'payment_form': _PAYMENT_FORM.get(rep.payment_form.strip().lower(), rep.payment_form),
        'lines': list(_document_lines(rep)),
        'taxable_amount': rep.taxable_amount,
        'tax_amount': rep.tax_amount,
        'total': rep.payable_amount,
        'status_text': _STATUS_TEXT.get(document.status, document.get_status_display()),
        'is_accepted': document.is_accepted,
        'is_test': document.environment != FiscalEnvironment.PRODUCTION,
        'qr_payload': qr_payload,
        'qr_png': render_qr_png(qr_payload),
        # El logotipo CONGELADO con este comprobante, no el vigente de la tienda.
        'logo_png': fiscal_logo.load(document),
        'digest_value': rep.digest_value or document.digest_value,
        # La tasa con la que se emitió: del documento congelado, leída AQUÍ para
        # que dibujar no necesite el modelo (DOC-UX-02).
        'tax_rate': document.tax_rate,
        # FISCAL-DESIGN-01: el diseño VIGENTE de la empresa (no se congela al
        # emitir: está pendiente del asesor). Sólo cambia el logo y añade el
        # mensaje y el pie después del bloque obligatorio; ningún dato fiscal.
        'design': _document_design.design_for_company_id(document.company_id),
    }


def _fit(png: bytes, max_width: float, max_height: float) -> tuple[float, float]:
    """El tamaño al que cabe la imagen en la caja, sin deformarla ni ampliarla de más."""
    from reportlab.lib.utils import ImageReader

    width, height = ImageReader(io.BytesIO(png)).getSize()
    scale = min(max_width / width, max_height / height)
    return width * scale, height * scale


def _symbol(currency: str) -> str:
    from .tax_services import currency_symbol

    return currency_symbol(currency)


def generate_fiscal_pdf(document: FiscalDocument) -> bytes:
    """La representación impresa A4. En memoria; no se escribe nada en disco."""
    return render_fiscal_a4(build_fiscal_context(document))


#: FISCAL-DESIGN-01. The A4 header: the issuer's column, a gutter, the fiscal box (RUC · title · number).
#: 9.6 + 0.4 + 7.0 = the 17 cm between the margins. The widest logo a design allows (90 mm) fits the column.
A4_ISSUER_COLUMN_CM = 9.6
_A4_GUTTER_CM = 0.4
_A4_BOX_CM = 7.0
#: Cant. · Und. · Descripción · V. unitario · P. unitario · Importe. An amount of 9 999 999.99 fits its column.
_A4_LINE_WIDTHS_CM = (1.4, 1.2, 6.9, 2.5, 2.5, 2.5)
#: FISCAL-A4-PROMO-LINES. When a line carries a discount: Cant. · Und. · Descripción · P. unitario ·
#: V. unitario · Dscto. · V. venta, so that Cant. × V. unitario − Dscto. = V. venta reads left to right.
_A4_PROMO_LINE_WIDTHS_CM = (1.1, 1.0, 4.9, 2.5, 2.5, 2.5, 2.5)
#: The smallest size a long unit value (ten decimals) is shrunk to so it stays whole in its column.
_A4_MIN_FIGURE_SIZE = 6.0


def _xml_figure(value: Decimal) -> str:
    """
    A figure with the decimals the signed XML gives it, never fewer than two.

    A unit value is n(12,10) in the XML (`33.8133333333`): rounded to two decimals, Cant. × V. unitario
    no longer gives the line's value (3 × 33.81 = 101.43, the XML says 101.44). Printed as the XML
    states it, never adjusted.
    """
    value = Decimal(value)
    return format(value, 'f') if value.as_tuple().exponent < -2 else f'{value:.2f}'


def _promo_line_rows(ctx: dict, styles: dict, cur: str) -> list[list]:
    """
    The rows of the A4 table of a document whose lines carry a discount (FISCAL-A4-PROMO-LINES).

    Every figure is an element of the signed XML, none is recomputed:

    * P. unitario: `cac:PricingReference/cac:AlternativeConditionPrice/cbc:PriceAmount` (type 01), the
      price paid per unit, WITH IGV and after the discount;
    * V. unitario: `cac:Price/cbc:PriceAmount`, the unit value WITHOUT IGV and BEFORE the discount;
    * Dscto.: `cac:AllowanceCharge/cbc:Amount` (code 00), without IGV; empty on a line without one;
    * V. venta: `cbc:LineExtensionAmount`, the line's value without IGV, after the discount — what
      «Op. gravada» adds up.
    """
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Paragraph

    base = styles['amount']

    def figure(text: str, width_cm: float):
        # A long figure is shrunk to fit whole, down to `_A4_MIN_FIGURE_SIZE`; only past that does it wrap.
        available = width_cm * cm - 8                     # the cell's 4 + 4 points of padding
        needed = stringWidth(text, base.fontName, base.fontSize)
        if needed <= available:
            return Paragraph(text, base)
        size = max(_A4_MIN_FIGURE_SIZE, base.fontSize * available / needed)
        return Paragraph(text, ParagraphStyle(f'{base.name}Fit', parent=base, fontSize=size))

    widths = _A4_PROMO_LINE_WIDTHS_CM
    rows = [['Cant.', 'Und.', 'Descripción', 'P. unitario', 'V. unitario', 'Dscto.', 'V. venta']]
    for linea in ctx['lines']:
        discount = linea.get('discount')
        rows.append([
            style.quantity(linea['quantity']),
            style.printable(linea['unit_code']),
            Paragraph(style.markup(linea['description']), styles['cell']),
            figure(f"{cur} {_xml_figure(linea['unit_price_with_tax'])}", widths[3]),
            figure(f"{cur} {_xml_figure(linea['unit_price'])}", widths[4]),
            figure(f"{cur} {_xml_figure(discount)}", widths[5]) if discount else '',
            figure(f"{cur} {_xml_figure(linea['amount'])}", widths[6]),
        ])
    return rows


def _design(ctx: dict):
    """
    The company design the paper is drawn with (FISCAL-DESIGN-01), or the defaults.

    A design changes the logo's size and alignment and adds the thank-you
    message and the footer AFTER the mandatory block. It never writes in the
    fiscal data: no option is the issuer, the buyer, a number, an amount, the
    legend, the status, the QR or a BETA mark.
    """
    from .document_design import DEFAULT

    return ctx.get('design') or DEFAULT


def _messages(design) -> tuple[list[str], str]:
    """
    The thank-you lines and the footer this paper prints, under its mandatory block.

    Each passes the CURRENT screen of its field (`document_design.design_text_problem`: printable,
    no fiscal document's name, no echo of the fiscal block). A row saved before a rule existed may
    hold a text that fails it: it is left out of the fiscal paper, never printed, and logged.
    """
    printed = {}
    for field, value in (('thank_you_message', design.thank_you_message), ('footer_text', design.footer_text)):
        if not value:
            printed[field] = ''
            continue
        problem = _document_design.design_text_problem(field, value)
        if problem:
            logger.warning('FISCAL-DESIGN-01: %s of the company design not printed on a fiscal paper: %s',
                           field, problem)
            value = ''
        printed[field] = value
    thanks = printed['thank_you_message']
    return (thanks.split('\n') if thanks else []), printed['footer_text']


def _a4_styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle

    def make(name, **kwargs):
        kwargs.setdefault('fontName', style.FONT)
        kwargs.setdefault('textColor', colors.HexColor(style.BODY))
        return ParagraphStyle(name, **kwargs)

    ink = colors.HexColor(style.INK)
    muted = colors.HexColor(style.MUTED)
    return {
        'issuer_name': make('FIssuerName', fontName=style.FONT_BOLD, fontSize=12, leading=15, textColor=ink),
        'issuer': make('FIssuer', fontSize=8.5, leading=11.5),
        'box_ruc': make('FBoxRuc', fontName=style.FONT_BOLD, fontSize=10.5, leading=13,
                        alignment=TA_CENTER, textColor=ink),
        'box_title': make('FBoxTitle', fontName=style.FONT_BOLD, fontSize=11, leading=14,
                          alignment=TA_CENTER, textColor=ink),
        'box_number': make('FBoxNumber', fontName=style.FONT_BOLD, fontSize=16, leading=20,
                           alignment=TA_CENTER, textColor=ink),
        'heading': make('FHeading', fontName=style.FONT_BOLD, fontSize=8.5, leading=11, textColor=ink,
                        spaceAfter=3),
        'label': make('FLabel', fontName=style.FONT_BOLD, fontSize=8.5, leading=11.5, textColor=ink),
        'value': make('FValue', fontSize=8.5, leading=11.5),
        'cell': make('FCell', fontSize=8.5, leading=11),
        'amount': make('FAmount', fontSize=8.5, leading=11, alignment=TA_RIGHT),
        'words': make('FWords', fontName=style.FONT_BOLD, fontSize=8.5, leading=12, textColor=ink),
        'legend': make('FLegend', fontName=style.FONT_BOLD, fontSize=8.5, leading=11.5, textColor=ink),
        'status': make('FStatus', fontSize=8.5, leading=11.5),
        'digest': make('FDigest', fontSize=7.5, leading=10, textColor=muted),
        'beta': make('FBeta', fontSize=8.5, leading=11, textColor=colors.HexColor('#b91c1c')),
        'thanks': make('FThanks', fontName=style.FONT_BOLD, fontSize=10, leading=13.5,
                       alignment=TA_CENTER, textColor=ink),
        'footer': make('FFooter', fontSize=7.5, leading=10, alignment=TA_CENTER, textColor=muted),
    }


def render_fiscal_a4(ctx: dict, *, watermark=None) -> bytes:
    """
    La representación impresa A4 desde su contexto de datos planos.

    PURA: no lee el modelo (la tasa viaja en `ctx['tax_rate']` y el diseño en
    `ctx['design']`), no toca la base ni el reloj. `generate_fiscal_pdf` la llama
    con `build_fiscal_context`; la vista previa con un contexto de muestra.
    `watermark` (un `document_style.Watermark`, opcional) es la marca de
    «MUESTRA»: se dibuja en cada página junto a —no en lugar de— la de SUNAT BETA.

    FISCAL-DESIGN-01, en este orden: emisor (con el logo del tamaño y la
    alineación del diseño) y el recuadro fiscal; la franja BETA; el documento
    que modifica; el adquirente con la fecha, la moneda y la forma de pago; el
    detalle (su cabecera se repite en cada hoja); el importe en letras y los
    totales; el bloque obligatorio (QR, leyenda, estado y valor resumen, que no
    se separan entre hojas). Sólo DESPUÉS, y aparte, el mensaje de
    agradecimiento y el pie del diseño.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    cur = _symbol(ctx['currency'])
    design = _design(ctx)
    styles = _a4_styles()
    ink, hairline, shade = (colors.HexColor(style.RULE), colors.HexColor(style.HAIRLINE),
                            colors.HexColor(style.SHADE))
    usable = 17 * cm
    buffer = io.BytesIO()

    def marca_de_muestra(canvas, _doc):
        """La marca de «MUESTRA» de la vista previa, detrás del contenido de cada página."""
        if watermark is not None:
            style.draw_diagonal(canvas, A4[0], A4[1], watermark.lines)
            style.draw_band(canvas, watermark.band, 2 * cm, A4[0] - 2 * cm, A4[1] - 1.15 * cm)

    def marca_de_pruebas(canvas):
        """
        La marca en diagonal. Se dibuja en CADA página, no sólo en la primera:
        una factura de varias hojas con el aviso sólo al principio deja las demás
        pareciendo válidas.

        ENCIMA del contenido, al cerrar la página: debajo, el blanco opaco del QR
        (su zona de silencio) tapaba «SIN» y en la hoja se leía «VALIDEZ
        TRIBUTARIA». Translúcida como siempre, el contenido se sigue leyendo.
        """
        if not ctx['is_test']:
            return
        canvas.saveState()
        # 34 pt y no 46: a mayor tamaño la marca cruzaba el valor resumen, que
        # es información con significado legal y tiene que poder leerse.
        canvas.setFont('Helvetica-Bold', 34)
        canvas.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.16))
        canvas.translate(A4[0] / 2, A4[1] / 2)
        canvas.rotate(38)
        top, bottom = _legends.FISCAL_BETA_DIAGONAL
        canvas.drawCentredString(0, 0, top)
        canvas.drawCentredString(0, -42, bottom)
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        title=f"{ctx['identifier']} — {ctx['issuer']['legal_name']}",
        author=ctx['issuer']['legal_name'],
    )

    story = []

    # --- cabecera: emisor a la izquierda, el recuadro fiscal a la derecha ---
    # Texto, no marcado: todo lo que escribe una persona entra a un
    # `Paragraph` por `style.markup`. Sin eso, `Case <b>Pro` levantaba
    # ValueError y la factura no se podía imprimir (DOC-UX-02).
    emisor = []
    if ctx['logo_png']:
        box_width, box_height = design.a4_logo_box_cm
        ancho, alto = _fit(ctx['logo_png'], box_width * cm, box_height * cm)
        # Tal cual y una sola vez: sin sombra, sin marco y sin máscara. La caja del
        # diseño ya trae el tope de alto; `_fit` no deforma.
        emisor += [Image(io.BytesIO(ctx['logo_png']), width=ancho, height=alto), Spacer(1, 6)]
    emisor.append(Paragraph(style.markup(ctx['issuer']['legal_name']), styles['issuer_name']))
    detalle_emisor = [style.markup(part) for part in (ctx['issuer']['trade_name'], ctx['issuer']['address'])
                      if part]
    if detalle_emisor:
        emisor.append(Paragraph('<br/>'.join(detalle_emisor), styles['issuer']))

    recuadro = Table(
        [[Paragraph(f"RUC {style.markup(ctx['issuer']['tax_id'])}", styles['box_ruc'])],
         [Paragraph(style.markup(ctx['title']), styles['box_title'])],
         [Paragraph(style.markup(ctx['identifier']), styles['box_number'])]],
        colWidths=[_A4_BOX_CM * cm],
    )
    recuadro.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1.1, ink),
        ('BACKGROUND', (0, 1), (-1, 1), shade),
        ('LINEABOVE', (0, 1), (-1, 1), 0.5, hairline),
        ('LINEBELOW', (0, 1), (-1, 1), 0.5, hairline),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))

    cabecera = Table(
        [[emisor, recuadro]],
        colWidths=[(A4_ISSUER_COLUMN_CM + _A4_GUTTER_CM) * cm, _A4_BOX_CM * cm],
    )
    cabecera.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), _A4_GUTTER_CM * cm),
        # La alineación del logo es la de la celda (una celda ignora el `hAlign`
        # de lo que contiene). Los párrafos ocupan la columna entera, así que el
        # texto del emisor sigue alineado a la izquierda.
        *([('ALIGN', (0, 0), (0, 0), {'center': 'CENTER', 'right': 'RIGHT'}[design.logo_a4_align])]
          if design.logo_a4_align in ('center', 'right') else []),
    ]))
    story.append(cabecera)
    story.append(Spacer(1, 12))

    if ctx['is_test']:
        aviso = Table([[Paragraph(f"<b>{BETA_WARNING}</b>", styles['beta'])]], colWidths=[usable])
        aviso.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fef2f2')),
            ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#fca5a5')),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(aviso)
        story.append(Spacer(1, 10))

    def pares(filas, label_width, width):
        """Etiqueta y valor; el valor se parte en renglones en lugar de cortarse."""
        tabla = Table(
            [[Paragraph(style.markup(label), styles['label']), Paragraph(style.markup(value), styles['value'])]
             for label, value in filas],
            colWidths=[label_width, width - label_width])
        tabla.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
        ]))
        return tabla

    def recuadro_suave(contenido, widths):
        tabla = Table([contenido], colWidths=widths)
        reglas = [
            ('BOX', (0, 0), (-1, -1), 0.6, hairline),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]
        if len(widths) > 1:
            reglas.append(('LINEAFTER', (0, 0), (-2, -1), 0.6, hairline))
        tabla.setStyle(TableStyle(reglas))
        return tabla

    inner = 14 / cm      # los 8 + 6 pt de relleno de un recuadro, en cm

    # --- documento que modifica (sólo en una nota) ---
    if ctx.get('note'):
        n = ctx['note']
        motivo = n['reason_description'] or ''
        if n['reason_code']:
            motivo = f"({n['reason_code']}) {motivo}".strip()
        story.append(recuadro_suave([pares([
            ('Documento que modifica:', f"{n['reference_type']} {n['reference']}"),
            ('Motivo:', motivo),
        ], 4 * cm, (17 - inner) * cm)], [usable]))
        story.append(Spacer(1, 10))

    # --- adquirente a la izquierda; fecha, moneda y forma de pago a la derecha ---
    story.append(Paragraph('Adquirente', styles['heading']))
    cliente = ctx['customer']
    filas_cliente = []
    if cliente['has_document']:
        filas_cliente.append((f"{cliente['doc_label']}:", cliente['doc_number']))
    filas_cliente.append((f"{cliente['name_label']}:", cliente['legal_name']))
    filas_emision = [('Fecha de emisión:', ctx['issued_at'].strftime('%d/%m/%Y %H:%M')),
                     ('Moneda:', ctx['currency'])]
    if ctx['payment_form']:
        filas_emision.append(('Forma de pago:', ctx['payment_form']))
    izquierda, derecha = A4_ISSUER_COLUMN_CM, 17 - A4_ISSUER_COLUMN_CM
    story.append(recuadro_suave(
        [pares(filas_cliente, 3.3 * cm, (izquierda - inner) * cm),
         pares(filas_emision, 2.9 * cm, (derecha - inner) * cm)],
        [izquierda * cm, derecha * cm]))
    story.append(Spacer(1, 12))

    # --- detalle: la cabecera se repite en cada hoja ---
    # FISCAL-A4-PROMO-LINES: con un descuento de línea en el XML, la tabla lo muestra («Dscto.») y la
    # última columna es el valor de venta; sin ninguno, la tabla de siempre, idéntica.
    if any(linea.get('discount') for linea in ctx['lines']):
        filas, anchos = _promo_line_rows(ctx, styles, cur), _A4_PROMO_LINE_WIDTHS_CM
    else:
        filas, anchos = [['Cant.', 'Und.', 'Descripción', 'V. unitario', 'P. unitario', 'Importe']], _A4_LINE_WIDTHS_CM
        for linea in ctx['lines']:
            filas.append([
                # 3, no 3.00: sólo la presentación; el XML conserva su Decimal.
                style.quantity(linea['quantity']),
                style.printable(linea['unit_code']),
                Paragraph(style.markup(linea['description']), styles['cell']),
                Paragraph(f"{cur} {linea['unit_price']:.2f}", styles['amount']),
                Paragraph(f"{cur} {linea['unit_price_with_tax']:.2f}", styles['amount']),
                Paragraph(f"{cur} {linea['amount']:.2f}", styles['amount']),
            ])
    detalle = Table(filas, colWidths=[width * cm for width in anchos], repeatRows=1)
    detalle.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), style.FONT_BOLD),
        ('FONTSIZE', (0, 0), (-1, 0), 7.5),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor(style.INK)),
        ('BACKGROUND', (0, 0), (-1, 0), shade),
        ('LINEABOVE', (0, 0), (-1, 0), 0.9, ink),
        ('LINEBELOW', (0, 0), (-1, 0), 0.9, ink),
        ('FONTNAME', (0, 1), (-1, -1), style.FONT),
        ('FONTSIZE', (0, 1), (-1, -1), 8.5),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor(style.BODY)),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, hairline),
        ('LINEBELOW', (0, -1), (-1, -1), 0.9, ink),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (1, -1), 'CENTER'),
        ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 4.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(detalle)
    story.append(Spacer(1, 10))

    # --- importe en letras a la izquierda, totales a la derecha, el total destacado ---
    porcentaje = (ctx['tax_rate'] * 100).normalize()
    cifras = Table([
        ['Op. gravada:', f"{cur} {ctx['taxable_amount']:.2f}"],
        [f'IGV ({porcentaje:f}%):', f"{cur} {ctx['tax_amount']:.2f}"],
        ['IMPORTE TOTAL:', f"{cur} {ctx['total']:.2f}"],
    ], colWidths=[3.7 * cm, 3.5 * cm])
    cifras.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -2), style.FONT),
        ('FONTSIZE', (0, 0), (-1, -2), 9),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor(style.INK)),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -2), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -2), 2.5),
        ('FONTNAME', (0, -1), (-1, -1), style.FONT_BOLD),
        ('FONTSIZE', (0, -1), (0, -1), 10),
        ('FONTSIZE', (1, -1), (1, -1), 13),
        ('BACKGROUND', (0, -1), (-1, -1), shade),
        ('LINEABOVE', (0, -1), (-1, -1), 1.1, ink),
        ('LINEBELOW', (0, -1), (-1, -1), 1.1, ink),
        ('TOPPADDING', (0, -1), (-1, -1), 7),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    letras = ([Paragraph(f"SON: {style.markup(ctx['amount_in_words'])}", styles['words'])]
              if ctx['amount_in_words'] else [])
    totales = Table([[letras, cifras]], colWidths=[usable - 7.2 * cm, 7.2 * cm])
    totales.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 0.5 * cm),
    ]))
    # --- QR: parte inferior, como exige el numeral 6.4.4. El QR, la leyenda, el
    # estado y el valor resumen van juntos, y con los totales: nunca se reparten
    # entre dos hojas. ---
    qr = Image(io.BytesIO(ctx['qr_png']), width=3.6 * cm, height=3.6 * cm)
    qr.hAlign = 'LEFT'
    textos = [
        Paragraph(f"{style.markup(ctx['legend'])}.", styles['legend']),
        Paragraph(f"Estado: {style.markup(ctx['status_text'])}", styles['status']),
        Paragraph(f"Valor resumen: {style.markup(ctx['digest_value'])}", styles['digest']),
    ]
    pie = Table([[qr, textos]], colWidths=[4.2 * cm, usable - 4.2 * cm])
    pie.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEABOVE', (0, 0), (-1, 0), 0.5, hairline),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(KeepTogether([totales, Spacer(1, 14), pie]))

    # --- después del bloque obligatorio, y aparte: el mensaje y el pie del
    # diseño. FUERA del grupo de arriba: un mensaje largo nunca empuja los
    # totales y el QR a una hoja sin RUC, serie ni franja BETA; si no cabe, es
    # él quien pasa solo a la hoja siguiente. ---
    gracias, pie_de_pagina = _messages(design)
    if gracias or pie_de_pagina:
        cierre = []
        if gracias:
            cierre.append(Paragraph('<br/>'.join(style.markup(line) for line in gracias), styles['thanks']))
        if pie_de_pagina:
            if gracias:
                cierre.append(Spacer(1, 4))
            cierre.append(Paragraph(style.markup(pie_de_pagina), styles['footer']))
        mensajes = Table([[cierre]], colWidths=[usable])
        mensajes.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 0.4, hairline),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 9),
        ]))
        story += [Spacer(1, 12), mensajes]

    # `afterPage` corre al cerrar cada hoja, después de todo su contenido.
    doc.afterPage = lambda: marca_de_pruebas(doc.canv)
    doc.build(story, onFirstPage=marca_de_muestra, onLaterPages=marca_de_muestra)
    resultado = buffer.getvalue()
    buffer.close()
    return resultado


def generate_fiscal_ticket_pdf(document: FiscalDocument) -> bytes:
    """La representación impresa en rollo de 80 mm. Ver `render_fiscal_ticket`."""
    return render_fiscal_ticket(build_fiscal_context(document))


def render_fiscal_ticket(ctx: dict, *, watermark=None) -> bytes:
    """
    La representación impresa en rollo de 80 mm, desde su contexto de datos planos.

    PURA, como `render_fiscal_a4`: la tasa viaja en `ctx['tax_rate']`.

    NO ES EL A4 ENCOGIDO. Son 7,2 cm útiles y una sola columna: el detalle se
    dispone en dos renglones por artículo —nombre arriba, cantidad e importe
    abajo— porque cinco columnas en ese ancho parten los nombres en pedazos
    ilegibles.

    Reutiliza `store.fiscal.ticket`… no: reutiliza el mismo trazado acotado que
    la nota interna, porque el problema geométrico es idéntico y resolverlo dos
    veces produciría dos tickets que se desalinean con el tiempo. Lo que NO
    comparte es el contenido: aquél dice que no es un comprobante SUNAT; éste lo
    es.
    """
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    from .ticket_services import TICKET_MARGIN_MM, TICKET_WIDTH_MM, _Cursor, roll_band

    cur = _symbol(ctx['currency'])
    design = _design(ctx)
    # Una vez, no en cada pasada: el rollo se mide y se dibuja con los mismos textos.
    mensajes = _messages(design)

    width = float(TICKET_WIDTH_MM) * mm
    margin = float(TICKET_MARGIN_MM) * mm
    content = width - 2 * margin
    qr_side = 28 * mm

    def trazar(pdf, alto):
        cursor = _Cursor(pdf, margin, alto - margin, content)
        roll_band(cursor, watermark)

        if ctx['logo_png']:
            # Tal cual y una sola vez: sin sombra ni máscara. La caja (con su tope
            # de alto) y la alineación son las del diseño; centrado por defecto.
            box_width, box_height = design.ticket_logo_box_mm
            logo_w, logo_h = _fit(ctx['logo_png'], box_width * mm, box_height * mm)
            if pdf is not None:
                from reportlab.lib.utils import ImageReader

                x = {'left': margin, 'right': margin + content - logo_w}.get(
                    design.logo_ticket_align, margin + (content - logo_w) / 2)
                pdf.drawImage(
                    ImageReader(io.BytesIO(ctx['logo_png'])),
                    x, cursor.y - logo_h,
                    width=logo_w, height=logo_h,
                )
            cursor.gap(logo_h + 4)

        cursor.line(ctx['issuer']['legal_name'], size=10, bold=True, align='center')
        if ctx['issuer']['trade_name']:
            cursor.line(ctx['issuer']['trade_name'], size=7, align='center')
        cursor.line(f"RUC {ctx['issuer']['tax_id']}", size=7.5, bold=True, align='center')
        if ctx['issuer']['address']:
            cursor.line(ctx['issuer']['address'], size=6.5, align='center')
        cursor.rule(dashed=False)

        cursor.line(ctx['title'], size=9, bold=True, align='center')
        cursor.line(ctx['identifier'], size=12, bold=True, align='center')
        if ctx['is_test']:
            # Antes que nada. Un ticket de pruebas que parece real es peor que
            # uno feo.
            for beta_line in _legends.FISCAL_BETA_ROLL_LINES:
                cursor.line(beta_line, size=7, bold=True, align='center')
        cursor.rule(dashed=False)

        if ctx.get('note'):
            n = ctx['note']
            cursor.row('Modifica:', f"{n['reference_type']} {n['reference']}",
                       size=7)
            if n['reason_code'] or n['reason_description']:
                motivo = n['reason_description'] or ''
                if n['reason_code']:
                    motivo = f"({n['reason_code']}) {motivo}".strip()
                cursor.line(f"Motivo: {motivo}", size=7)
            cursor.rule()

        cursor.row('Fecha:', ctx['issued_at'].strftime('%d/%m/%Y %H:%M'), size=7)
        cliente = ctx['customer']
        if cliente['has_document']:
            cursor.row(f"{cliente['doc_label_short']}:", cliente['doc_number'], size=7)
        cursor.line(f"{cliente['name_label']}: {cliente['legal_name']}", size=7)
        if ctx['payment_form']:
            cursor.line(f"Forma de pago: {ctx['payment_form']}", size=7)
        cursor.rule()

        # Cantidad, unidad y PRECIO DE VENTA unitario (con IGV) a la izquierda;
        # el valor de venta de la línea (sin IGV) a la derecha, que es lo que
        # suman los totales de abajo.
        cursor.row('Cant. Und. x P. unit.', 'V. venta', size=6.5, bold=True)
        cursor.gap(1)
        for numero, linea in enumerate(ctx['lines']):
            if numero:
                cursor.gap(2)
            cursor.line(str(linea['description']), size=7.5)
            cursor.row(
                f"  {style.quantity(linea['quantity'])} {linea['unit_code']} x "
                f"{cur} {linea['unit_price_with_tax']:.2f}".replace('  x', ' x'),
                f"{cur} {linea['amount']:.2f}", size=7.5,
            )
        cursor.rule()

        porcentaje = (ctx['tax_rate'] * 100).normalize()
        cursor.row('Op. gravada', f"{cur} {ctx['taxable_amount']:.2f}", size=7.5)
        cursor.row(f'IGV ({porcentaje:f}%)', f"{cur} {ctx['tax_amount']:.2f}", size=7.5)
        cursor.gap(2)
        cursor.row('TOTAL', f"{cur} {ctx['total']:.2f}", size=11, bold=True)
        if ctx['amount_in_words']:
            cursor.gap(1)
            cursor.line(f"SON: {ctx['amount_in_words']}", size=6.5)
        cursor.rule(dashed=False)

        cursor.line(f"Estado: {ctx['status_text']}", size=6.5, align='center')
        cursor.gap(3)

        # El QR va abajo, como exige el numeral 6.4.4.
        if pdf is not None:
            from reportlab.lib.utils import ImageReader

            pdf.drawImage(
                ImageReader(io.BytesIO(ctx['qr_png'])),
                margin + (content - qr_side) / 2, cursor.y - qr_side,
                width=qr_side, height=qr_side, mask='auto',
            )
        cursor.gap(qr_side + 4)
        cursor.line(f"Valor resumen: {ctx['digest_value']}", size=5.5, align='center')
        cursor.line(f"{ctx['legend']}.", size=6, align='center')

        # Después del bloque obligatorio, y aparte: el mensaje y el pie del diseño.
        gracias, pie_de_pagina = mensajes
        if gracias or pie_de_pagina:
            cursor.rule()
            for renglon in gracias:
                cursor.line(renglon, size=8, bold=True, align='center')
            if pie_de_pagina:
                if gracias:
                    cursor.gap(2)
                cursor.line(pie_de_pagina, size=6.5, align='center')
        roll_band(cursor, watermark, bottom=True)
        cursor.gap(margin)
        return cursor.used + margin

    # Dos pasadas: medir y dibujar, para que la impresora no escupa papel en
    # blanco. Mismo motivo y mismo mecanismo que el ticket de la nota interna.
    alto = trazar(None, 0.0)
    buffer = io.BytesIO()
    pdf = pdfcanvas.Canvas(buffer, pagesize=(width, alto))
    pdf.setTitle(ctx['identifier'])
    pdf.setAuthor(ctx['issuer']['legal_name'])
    if watermark is not None:
        style.draw_diagonal(pdf, width, alto, watermark.lines)
    trazar(pdf, alto)
    pdf.showPage()
    pdf.save()
    resultado = buffer.getvalue()
    buffer.close()
    return resultado
