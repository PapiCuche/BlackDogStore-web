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

LA MARCA DE PRUEBAS NO ES DECORACIÓN
------------------------------------
Un comprobante emitido contra el entorno de pruebas de SUNAT no tiene validez
tributaria. Sin una marca inequívoca, alguien lo imprime y lo entrega como
factura real. Va en diagonal sobre el papel y también en texto, porque una marca
de agua se puede perder en una fotocopia mala.
"""

from __future__ import annotations

import io
from decimal import Decimal

from .fiscal.qr import build_qr_payload, render_qr_png
from .models import FiscalDocument, FiscalDocumentStatus, FiscalEnvironment

_TITLE = {'01': 'FACTURA ELECTRÓNICA', '03': 'BOLETA DE VENTA ELECTRÓNICA'}

#: Sólo se imprime «aceptada» cuando hay constancia. Los demás estados se dicen
#: como son: un papel que afirma una aceptación que no ocurrió es peor que uno
#: que dice «pendiente».
_STATUS_TEXT = {
    FiscalDocumentStatus.ACCEPTED: 'Aceptada por SUNAT',
    FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION:
        'Aceptada por SUNAT con observaciones',
    FiscalDocumentStatus.REJECTED: 'RECHAZADA POR SUNAT — sin validez tributaria',
    FiscalDocumentStatus.SUBMISSION_ERROR: 'Pendiente de confirmación de SUNAT',
    FiscalDocumentStatus.SIGNED: 'Emitida, pendiente de envío a SUNAT',
    FiscalDocumentStatus.GENERATED: 'Generada, pendiente de firma',
    FiscalDocumentStatus.PENDING: 'Pendiente',
}

BETA_WARNING = (
    'AMBIENTE DE PRUEBAS — SUNAT BETA · SIN VALIDEZ TRIBUTARIA'
)


class FiscalPdfError(Exception):
    """No se puede representar este documento todavía."""


def _document_lines(document: FiscalDocument):
    """
    Las líneas del comprobante, reconstruidas desde la venta.

    Se leen de `OrderItem` porque el documento fiscal no las duplica: duplicar
    filas que ya existen crea dos verdades sobre lo mismo. Lo que sí está
    congelado —y es lo que SUNAT recibió— son los totales.
    """
    tasa = Decimal('1') + document.tax_rate
    for item in document.order.items.select_related('product').all():
        con_impuesto = Decimal(str(item.price))
        sin_impuesto = (con_impuesto / tasa).quantize(Decimal('0.01'))
        yield {
            'description': item.product.name if item.product else 'PRODUCTO',
            'quantity': item.quantity,
            'unit_price': sin_impuesto,
            'unit_price_with_tax': con_impuesto,
            'amount': (sin_impuesto * item.quantity).quantize(Decimal('0.01')),
        }


def build_fiscal_context(document: FiscalDocument) -> dict:
    """Los datos de impresión. Del documento congelado, no de tablas vivas."""
    if not document.digest_value:
        raise FiscalPdfError(
            'El comprobante todavía no está firmado: sin DigestValue no hay QR '
            'posible, y un comprobante sin QR no es una representación impresa.'
        )

    qr_payload = build_qr_payload(
        issuer_tax_id=document.issuer_tax_id,
        document_type=document.document_type,
        series=document.series,
        number=document.number,
        tax_amount=document.tax_amount,
        total=document.total,
        issue_date=document.issued_at.date(),
        customer_doc_type=document.customer_doc_type,
        customer_doc_number=document.customer_doc_number,
        digest_value=document.digest_value,
    )
    return {
        'title': _TITLE.get(document.document_type, 'COMPROBANTE ELECTRÓNICO'),
        'identifier': document.document_id,
        'issued_at': document.issued_at,
        'currency': document.currency,
        'issuer': {
            'tax_id': document.issuer_tax_id,
            'legal_name': document.issuer_legal_name,
            'trade_name': document.issuer_trade_name,
            'address': document.issuer_address,
        },
        'customer': {
            'doc_number': document.customer_doc_number,
            'legal_name': document.customer_legal_name,
        },
        'lines': list(_document_lines(document)),
        'taxable_amount': document.taxable_amount,
        'tax_amount': document.tax_amount,
        'total': document.total,
        'status_text': _STATUS_TEXT.get(document.status, document.get_status_display()),
        'is_accepted': document.is_accepted,
        'is_test': document.environment != FiscalEnvironment.PRODUCTION,
        'qr_payload': qr_payload,
        'qr_png': render_qr_png(qr_payload),
        'digest_value': document.digest_value,
    }


def _symbol(currency: str) -> str:
    from .tax_services import currency_symbol

    return currency_symbol(currency)


def generate_fiscal_pdf(document: FiscalDocument) -> bytes:
    """La representación impresa A4. En memoria; no se escribe nada en disco."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (
        HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    ctx = build_fiscal_context(document)
    cur = _symbol(ctx['currency'])
    buffer = io.BytesIO()

    def marca_de_pruebas(canvas, _doc):
        """
        La marca en diagonal. Se dibuja en CADA página, no sólo en la primera:
        una factura de varias hojas con el aviso sólo al principio deja las demás
        pareciendo válidas.
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
        canvas.drawCentredString(0, 0, 'SIN VALIDEZ TRIBUTARIA')
        canvas.drawCentredString(0, -42, 'SUNAT BETA')
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        title=f"{ctx['identifier']} — {ctx['issuer']['legal_name']}",
        author=ctx['issuer']['legal_name'],
    )

    base = getSampleStyleSheet()
    h1 = ParagraphStyle('FH1', parent=base['Heading1'], fontSize=13, leading=16,
                        spaceAfter=2, textColor=colors.HexColor('#111827'))
    h2 = ParagraphStyle('FH2', parent=base['Heading2'], fontSize=9.5, leading=12,
                        spaceAfter=2, textColor=colors.HexColor('#374151'))
    body = ParagraphStyle('FBody', parent=base['Normal'], fontSize=8.5, leading=12,
                          textColor=colors.HexColor('#374151'))
    small = ParagraphStyle('FSmall', parent=base['Normal'], fontSize=7,
                           leading=9.5, textColor=colors.HexColor('#6b7280'))

    story = []

    # --- cabecera: emisor a la izquierda, el recuadro fiscal a la derecha ---
    emisor = [f"<b>{ctx['issuer']['legal_name']}</b>"]
    if ctx['issuer']['trade_name']:
        emisor.append(ctx['issuer']['trade_name'])
    if ctx['issuer']['address']:
        emisor.append(ctx['issuer']['address'])

    recuadro = Table(
        [[Paragraph(f"RUC {ctx['issuer']['tax_id']}", h2)],
         [Paragraph(f"<b>{ctx['title']}</b>", h2)],
         [Paragraph(f"<b>{ctx['identifier']}</b>", h1)]],
        colWidths=[7 * cm],
    )
    recuadro.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#111827')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))

    cabecera = Table(
        [[Paragraph('<br/>'.join(emisor), body), recuadro]],
        colWidths=[9.5 * cm, 7.5 * cm],
    )
    cabecera.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(cabecera)
    story.append(Spacer(1, 10))

    if ctx['is_test']:
        aviso = Table([[Paragraph(f"<b>{BETA_WARNING}</b>", ParagraphStyle(
            'FBeta', parent=base['Normal'], fontSize=8.5, leading=11,
            textColor=colors.HexColor('#b91c1c')))]], colWidths=[17 * cm])
        aviso.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fef2f2')),
            ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#fca5a5')),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(aviso)
        story.append(Spacer(1, 8))

    # --- adquirente y fecha ---
    story.append(Paragraph('Adquirente', h2))
    datos = Table([
        ['RUC:', ctx['customer']['doc_number']],
        ['Razón social:', ctx['customer']['legal_name']],
        ['Fecha de emisión:', ctx['issued_at'].strftime('%d/%m/%Y %H:%M')],
        ['Moneda:', ctx['currency']],
    ], colWidths=[4 * cm, 13 * cm])
    datos.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#374151')),
    ]))
    story.append(datos)
    story.append(Spacer(1, 10))

    # --- detalle ---
    filas = [['Cant.', 'Descripción', 'V. unitario', 'P. unitario', 'Importe']]
    for linea in ctx['lines']:
        filas.append([
            str(linea['quantity']),
            Paragraph(str(linea['description']), body),
            f"{cur} {linea['unit_price']:.2f}",
            f"{cur} {linea['unit_price_with_tax']:.2f}",
            f"{cur} {linea['amount']:.2f}",
        ])
    detalle = Table(filas, colWidths=[1.6 * cm, 7.4 * cm, 2.7 * cm, 2.7 * cm, 2.6 * cm],
                    repeatRows=1)
    detalle.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f3f4f6')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#e5e7eb')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#374151')),
    ]))
    story.append(detalle)
    story.append(Spacer(1, 8))

    porcentaje = (document.tax_rate * 100).normalize()
    totales = Table([
        ['Op. gravada:', f"{cur} {ctx['taxable_amount']:.2f}"],
        [f'IGV ({porcentaje:f}%):', f"{cur} {ctx['tax_amount']:.2f}"],
        ['IMPORTE TOTAL:', f"{cur} {ctx['total']:.2f}"],
    ], colWidths=[14 * cm, 3 * cm])
    totales.setStyle(TableStyle([
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#111827')),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(totales)
    story.append(Spacer(1, 12))

    # --- QR: parte inferior, como exige el numeral 6.4.4 ---
    qr = Image(io.BytesIO(ctx['qr_png']), width=3.6 * cm, height=3.6 * cm)
    pie = Table([[qr, Paragraph(
        f"Representación impresa del comprobante electrónico.<br/>"
        f"Estado: {ctx['status_text']}<br/>"
        f"Valor resumen: {ctx['digest_value']}", small)]],
        colWidths=[4.2 * cm, 12.8 * cm])
    pie.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'BOTTOM')]))
    story.append(HRFlowable(width='100%', thickness=0.5,
                            color=colors.HexColor('#e5e7eb'), spaceAfter=6))
    story.append(pie)

    doc.build(story, onFirstPage=marca_de_pruebas, onLaterPages=marca_de_pruebas)
    resultado = buffer.getvalue()
    buffer.close()
    return resultado


def generate_fiscal_ticket_pdf(document: FiscalDocument) -> bytes:
    """
    La representación impresa en rollo de 80 mm.

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

    from .ticket_services import TICKET_MARGIN_MM, TICKET_WIDTH_MM, _Cursor

    ctx = build_fiscal_context(document)
    cur = _symbol(ctx['currency'])

    width = float(TICKET_WIDTH_MM) * mm
    margin = float(TICKET_MARGIN_MM) * mm
    content = width - 2 * margin
    qr_side = 28 * mm

    def trazar(pdf, alto):
        cursor = _Cursor(pdf, margin, alto - margin, content)

        cursor.line(ctx['issuer']['legal_name'], size=9.5, bold=True, align='center')
        if ctx['issuer']['trade_name']:
            cursor.line(ctx['issuer']['trade_name'], size=6.5, align='center')
        cursor.line(f"RUC {ctx['issuer']['tax_id']}", size=6.5, align='center')
        if ctx['issuer']['address']:
            cursor.line(ctx['issuer']['address'], size=6.5, align='center')
        cursor.rule()

        cursor.line(ctx['title'], size=8.5, bold=True, align='center')
        cursor.line(ctx['identifier'], size=10, bold=True, align='center')
        if ctx['is_test']:
            # Antes que nada. Un ticket de pruebas que parece real es peor que
            # uno feo.
            cursor.line('AMBIENTE DE PRUEBAS — SUNAT BETA', size=7,
                        bold=True, align='center')
            cursor.line('SIN VALIDEZ TRIBUTARIA', size=7, bold=True, align='center')
        cursor.rule()

        cursor.row('Fecha:', ctx['issued_at'].strftime('%d/%m/%Y %H:%M'), size=6.5)
        cursor.row('RUC cliente:', ctx['customer']['doc_number'], size=6.5)
        cursor.line(ctx['customer']['legal_name'], size=6.5)
        cursor.rule()

        for linea in ctx['lines']:
            cursor.line(str(linea['description']), size=7)
            cursor.row(
                f"  {linea['quantity']} x {cur} {linea['unit_price']:.2f}",
                f"{cur} {linea['amount']:.2f}", size=7,
            )
        cursor.rule()

        porcentaje = (document.tax_rate * 100).normalize()
        cursor.row('Op. gravada', f"{cur} {ctx['taxable_amount']:.2f}", size=7)
        cursor.row(f'IGV ({porcentaje:f}%)', f"{cur} {ctx['tax_amount']:.2f}", size=7)
        cursor.gap(1)
        cursor.row('TOTAL', f"{cur} {ctx['total']:.2f}", size=10, bold=True)
        cursor.rule()

        cursor.line(f"Estado: {ctx['status_text']}", size=6, align='center')
        cursor.gap(2)

        # El QR va abajo, como exige el numeral 6.4.4.
        if pdf is not None:
            from reportlab.lib.utils import ImageReader

            pdf.drawImage(
                ImageReader(io.BytesIO(ctx['qr_png'])),
                margin + (content - qr_side) / 2, cursor.y - qr_side,
                width=qr_side, height=qr_side, mask='auto',
            )
        cursor.gap(qr_side + 4)
        cursor.line('Representación impresa del comprobante electrónico.',
                    size=5.5, align='center')
        cursor.gap(margin)
        return cursor.used + margin

    # Dos pasadas: medir y dibujar, para que la impresora no escupa papel en
    # blanco. Mismo motivo y mismo mecanismo que el ticket de la nota interna.
    alto = trazar(None, 0.0)
    buffer = io.BytesIO()
    pdf = pdfcanvas.Canvas(buffer, pagesize=(width, alto))
    pdf.setTitle(ctx['identifier'])
    pdf.setAuthor(ctx['issuer']['legal_name'])
    trazar(pdf, alto)
    pdf.showPage()
    pdf.save()
    resultado = buffer.getvalue()
    buffer.close()
    return resultado
