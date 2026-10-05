"""
QUOTE-TICKET — el ticket de 80 mm de una cotización APROBADA.

El papel que se entrega cuando el cliente dijo que sí. Reutiliza el trazado de
los demás tickets (`ticket_services._Cursor`): el problema geométrico es el
mismo y resolverlo otra vez daría dos rollos que se desalinean con el tiempo.

    NO ES UN COMPROBANTE DE PAGO, Y LO DICE.

No lleva serie fiscal, no se llama boleta ni factura, y no afirma que se haya
cobrado nada: dice qué se acordó. El cobro tiene sus propios documentos.

    LO QUE IMPRIME ES LO QUE EL CLIENTE YA PUEDE VER.

Ni las notas internas de la cotización, ni la nota que el personal escribió al
registrar la decisión, ni el IMEI o la serie completos: el ticket se queda en
un mostrador, en un bolsillo o en una foto.
"""
from __future__ import annotations

import io
from decimal import Decimal

from django.utils import timezone

from . import device_identity
from .models import RepairQuote, RepairQuoteDecision

DISCLAIMER = 'No es un comprobante de pago ni un documento tributario.'


class QuoteTicketError(Exception):
    """El ticket no se puede emitir. Las vistas lo traducen a 400."""


def _person(user) -> str:
    if user is None:
        return ''
    return (user.get_full_name() or user.username or '').strip()


def _masked(value: str) -> str:
    # Asteriscos y no «•»: la tipografía del rollo es la estándar del PDF.
    return device_identity.mask(value).replace('•', '*')


def filename(quote) -> str:
    return f'cotizacion-{quote.repair_order.number}-r{quote.revision}-ticket80.pdf'


def build_context(quote) -> dict:
    """
    Todo lo que el ticket dice, leído del servidor en este momento.

    Levanta `QuoteTicketError` si la cotización no está aprobada: una
    reemplazada dejó de autorizar nada, y un papel que diga APROBADA sobre un
    precio que ya no vale es justo lo que hay que impedir.
    """
    from . import fiscal_logo
    from .company_settings import company_identity
    from .tax_services import currency_symbol

    if quote.status != RepairQuote.STATUS_APPROVED:
        raise QuoteTicketError(
            'Sólo se imprime el ticket de una cotización aprobada y vigente.'
        )
    decision = RepairQuoteDecision.objects.select_related('recorded_by').filter(
        quote=quote, decision=RepairQuoteDecision.DECISION_APPROVE,
    ).first()
    if decision is None:
        raise QuoteTicketError(
            'Sólo se imprime el ticket de una cotización aprobada y vigente.'
        )

    order = quote.repair_order
    company = order.company
    identity = company_identity(company)
    customer = order.customer
    device = order.device

    name = (
        customer.business_name
        or f'{customer.first_name} {customer.last_name}'.strip()
    )
    return {
        'logo_png': fiscal_logo.current_png(company),
        'store_name': identity.name,
        'store_legal_name': identity.legal_name,
        'store_tax_id': identity.tax_id,
        'store_address': identity.legal_address,
        'store_phone': identity.whatsapp_number or identity.phone,
        'number': order.number,
        'revision': quote.revision,
        'customer_name': name,
        'customer_phone': customer.phone,
        'device': ' '.join(filter(None, (
            device.get_device_type_display(), device.brand, device.model,
        ))) if device is not None else '',
        'serial': _masked(device.serial_number) if device is not None else '',
        'imei': _masked(device.imei) if device is not None else '',
        'items': [
            {
                'description': item.description,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'line_total': item.line_total,
            }
            for item in quote.items.order_by('sort_order', 'pk')
        ],
        'symbol': currency_symbol(quote.currency),
        'subtotal': quote.subtotal,
        'discount': quote.discount_amount or Decimal('0.00'),
        'total': quote.total,
        'customer_notes': quote.customer_notes,
        'channel': decision.get_channel_display(),
        'decided_at': timezone.localtime(decision.decided_at).strftime('%d/%m/%Y %H:%M'),
        # Quien anotó la respuesta; si respondió el propio cliente, quien cotizó.
        'attended_by': _person(decision.recorded_by) or _person(quote.created_by),
        'printed_at': timezone.localtime().strftime('%d/%m/%Y %H:%M'),
    }


def _quantity(value) -> str:
    text = f'{Decimal(value):f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _lay_out(pdf, ctx: dict, content: float, margin: float, page_height: float) -> float:
    from reportlab.lib.units import mm

    from .fiscal_pdf_services import _fit
    from .ticket_services import _Cursor

    cur = _Cursor(pdf, margin, page_height - margin, content)
    money = ctx['symbol']

    if ctx['logo_png']:
        logo_w, logo_h = _fit(ctx['logo_png'], 42 * mm, 16 * mm)
        if pdf is not None:
            from reportlab.lib.utils import ImageReader

            pdf.drawImage(
                ImageReader(io.BytesIO(ctx['logo_png'])),
                margin + (content - logo_w) / 2, cur.y - logo_h,
                width=logo_w, height=logo_h,
            )
        cur.gap(logo_h + 3)

    cur.line(ctx['store_name'] or ctx['store_legal_name'], size=10, bold=True, align='center')
    if ctx['store_legal_name'] and ctx['store_legal_name'] != ctx['store_name']:
        cur.line(ctx['store_legal_name'], size=6.5, align='center')
    if ctx['store_tax_id']:
        cur.line(f"RUC {ctx['store_tax_id']}", size=6.5, align='center')
    if ctx['store_address']:
        cur.line(ctx['store_address'], size=6.5, align='center')
    if ctx['store_phone']:
        cur.line(f"Tel. {ctx['store_phone']}", size=6.5, align='center')

    cur.rule()

    cur.line('COTIZACIÓN DE SERVICIO', size=8.5, bold=True, align='center')
    cur.line(f"{ctx['number']} · Rev. {ctx['revision']}", size=9, bold=True, align='center')
    cur.gap(1)
    cur.line('APROBADA', size=11, bold=True, align='center')
    cur.gap(2)
    cur.row('Aprobada el:', ctx['decided_at'], size=6.5)
    cur.row('Medio:', ctx['channel'], size=6.5)
    if ctx['attended_by']:
        cur.row('Atendido por:', ctx['attended_by'], size=6.5)

    cur.rule()

    cur.line(f"Cliente: {ctx['customer_name']}", size=6.5)
    if ctx['customer_phone']:
        cur.line(f"Teléfono: {ctx['customer_phone']}", size=6.5)
    if ctx['device']:
        cur.line(f"Equipo: {ctx['device']}", size=6.5)
    if ctx['serial']:
        cur.row('Serie:', ctx['serial'], size=6.5)
    if ctx['imei']:
        cur.row('IMEI:', ctx['imei'], size=6.5)

    cur.rule()

    for item in ctx['items']:
        cur.line(str(item['description']), size=7)
        cur.row(
            f"  {_quantity(item['quantity'])} x {money} {item['unit_price']:.2f}",
            f"{money} {item['line_total']:.2f}", size=7,
        )

    cur.rule()

    if ctx['discount'] > 0:
        cur.row('Subtotal', f"{money} {ctx['subtotal']:.2f}", size=7)
        cur.row('Descuento', f"- {money} {ctx['discount']:.2f}", size=7)
        cur.gap(1)
    cur.row('TOTAL', f"{money} {ctx['total']:.2f}", size=10, bold=True)

    if ctx['customer_notes']:
        cur.rule()
        cur.line(str(ctx['customer_notes']), size=6.5)

    cur.rule()
    cur.line(DISCLAIMER, size=6.5, bold=True, align='center')
    cur.gap(1)
    cur.line(f"Impreso el {ctx['printed_at']}", size=6, align='center')

    cur.gap(margin)
    return cur.used + margin


def generate_pdf(quote) -> bytes:
    """El ticket, en memoria. Dos pasadas: medir, y dibujar a esa altura exacta."""
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    from .ticket_services import TICKET_MARGIN_MM, TICKET_WIDTH_MM

    ctx = build_context(quote)

    width = float(TICKET_WIDTH_MM) * mm
    margin = float(TICKET_MARGIN_MM) * mm
    content = width - 2 * margin
    height = _lay_out(None, ctx, content, margin, 0.0)

    buffer = io.BytesIO()
    pdf = pdfcanvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f"Cotización {ctx['number']} — {ctx['store_name']}".rstrip(' —'))
    pdf.setAuthor(ctx['store_name'] or '')
    _lay_out(pdf, ctx, content, margin, height)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
