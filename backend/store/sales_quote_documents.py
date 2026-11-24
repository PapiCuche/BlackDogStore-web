"""
SALES-QUOTE-01 — the papers of a commercial quote: A4, 80 mm PDF and ESC/POS for the thermal roll.

    «COTIZACIÓN», its number and revision, the validity, who offers and to whom, the lines with
    their options, the totals with the IGV breakdown, the commercial conditions — and
    «No es comprobante de pago».

DRAWN BY THE SHARED RENDERERS. The A4 is a description handed to `document_layout.render_a4`
(the page every internal paper of a shop shares); the roll uses `ticket_services._Cursor` and
`document_style.ticket_header`; the thermal print `printing.escpos.Receipt`. Identity, logo and
design (logo size and alignment, contact switches, closing message, footer, tax breakdown mode)
are the COMPANY's: nothing here names a shop.

NOT A RECEIPT, AND NOTHING ON IT SAYS IT IS. No fiscal series, no QR, no «representación
impresa»; the only fiscal word on the paper is the negation in the legend
(`document_legends.SALES_QUOTE_LEGEND`). A test runs `document_design.names_fiscal_document`
over everything else the paper prints.

WHAT THE CUSTOMER MAY SEE, ONLY. Never the internal notes, never a cost, never a full IMEI or
serial: a quoted device is written masked.

TRADE-IN-QUOTE-00. A quote with a device offered in part-exchange prints it in its OWN block under the totals
(«Equipo ofrecido en parte de pago (referencial)»): the device as described, the last four digits of its IMEI
masked, the estimated valuation and the estimated balance, and `TRADE_IN_QUOTE_LEGEND`. The quote's totals, IGV
and «TOTAL COTIZADO» are not touched by it. What the customer asked and the valuer's notes never print.

`build_context` reads the database; `quote_document`, `ticket_pdf_from_ctx` and
`escpos_from_ctx` are PURE, so the document preview draws a made-up quote with them.
"""
from __future__ import annotations

import io
from decimal import Decimal

from . import device_identity
from . import document_style as style
from .document_legends import (
    SALES_QUOTE_LEGEND,
    SALES_QUOTE_LEGEND_DETAIL,
    SALES_QUOTE_TITLE,
    TRADE_IN_QUOTE_HEADING,
    TRADE_IN_QUOTE_LEGEND,
)

UNIT_LABEL = 'UND'
CONDITIONS_HEADING = 'CONDICIONES COMERCIALES'

#: The word framed in the header of a paper that is not (or no longer) a live offer.
STAMPS = {
    'draft': 'BORRADOR',
    'rejected': 'RECHAZADA',
    'expired': 'VENCIDA',
    'superseded': 'REEMPLAZADA',
}

_DOCUMENT_LABELS = {'dni': 'DNI', 'ruc': 'RUC', 'ce': 'CE', 'passport': 'Pasaporte', 'other': 'Doc.'}
#: The variant roles a line prints, in this order (`order_line_snapshot.variant_snapshot`).
_ROLES = ('capacity', 'color', 'sim', 'region')


def _masked(value) -> str:
    # Asterisks, not «•»: the roll's type is the PDF standard one and the printer's code page.
    return device_identity.mask(value).replace('•', '*') if value else ''


def variant_details(variant: dict | None) -> str:
    """The options of a line in the catalogue's words: «256 GB · Negro · eSIM · Seminuevo · Grado A»."""
    variant = variant or {}
    parts = [str(variant[role]) for role in _ROLES if variant.get(role)]
    parts += [str(row.get('value')) for row in variant.get('other') or [] if row.get('value')]
    if variant.get('condition') == 'used' and variant.get('condition_label'):
        parts.append(str(variant['condition_label']))
    if variant.get('grade'):
        parts.append(f"Grado {variant['grade']}")
    return ' · '.join(parts)


TRADE_IN_VALUATION_LABEL = 'Valorización estimada'
TRADE_IN_BALANCE_LABEL = 'Saldo estimado a pagar'


def trade_in_context(quote) -> dict | None:
    """The part-exchange block as the paper shows it, or None. Plain data; never the asked price or the notes."""
    from .models import SalesQuoteTradeIn

    block = SalesQuoteTradeIn.objects.filter(quote_id=quote.pk).first()
    if block is None:
        return None
    return {
        'rows': [
            ('Equipo', block.device_description),
            ('Capacidad', block.capacity),
            ('Color', block.color),
            ('Estado declarado', dict(block.CONDITION_CHOICES).get(block.condition, block.condition)),
            ('Batería', f'{block.battery_health} %' if block.battery_health is not None else ''),
            ('Accesorios', block.accessories),
            # Only the last four digits are ever stored: asterisks stand for the rest.
            ('IMEI', f'****{block.imei_last4}' if block.imei_last4 else ''),
        ],
        'valuation': block.estimated_valuation,
        'balance': quote.total - block.estimated_valuation,
    }


def _trade_in_figures(ctx) -> list[tuple[str, str]]:
    trade_in = ctx['trade_in']
    symbol = ctx['tax']['symbol']
    return [
        ('Total cotizado', style.money(symbol, ctx['tax']['total'])),
        (TRADE_IN_VALUATION_LABEL, f"- {style.money(symbol, trade_in['valuation'])}"),
        (TRADE_IN_BALANCE_LABEL, style.money(symbol, trade_in['balance'])),
    ]


def filename(quote, kind: str = 'a4') -> str:
    from .pdf_services import _safe_slug

    slug = _safe_slug(getattr(quote.company, 'slug', ''))
    base = f'cotizacion-{_safe_slug(quote.label.lower(), 60)}'
    suffix = '-ticket80' if kind == 'ticket80' else ''
    return f'{slug}-{base}{suffix}.pdf' if slug else f'{base}{suffix}.pdf'


def build_context(quote) -> dict:
    """Everything the quote's papers say, read now from the quote (frozen money) and the company (identity)."""
    from . import fiscal_logo
    from .company_settings import company_identity
    from .document_design import design_for
    from .sales_note_services import tax_context_from_breakdown
    from .tax_services import TaxBreakdown

    company = quote.company
    identity = company_identity(company)
    branch = quote.branch
    items = []
    for number, item in enumerate(quote.items.select_related('stock_unit').order_by('sort_order', 'pk'), start=1):
        unit = item.stock_unit
        details = [text for text in (variant_details(item.variant_snapshot),) if text]
        if item.on_order:
            details.append('Bajo pedido: plazo de entrega según disponibilidad del proveedor.')
        items.append({
            'line_number': number, 'code': item.code, 'name': item.description, 'details': details,
            'units': [{'serial_number': _masked(unit.serial_number), 'imei': _masked(unit.imei),
                       'imei2': _masked(unit.imei2)}] if unit is not None else [],
            'unit': UNIT_LABEL, 'quantity': item.quantity, 'unit_price': item.unit_price,
            'amount': item.line_total,
        })
    tax = tax_context_from_breakdown(TaxBreakdown(
        currency=quote.currency, subtotal=quote.subtotal, discount_amount=quote.discount_amount,
        taxable_amount=quote.taxable_amount, tax_amount=quote.tax_amount, tax_rate=quote.tax_rate,
        tax_treatment=quote.tax_treatment, total=quote.total,
    ))
    document_label = _DOCUMENT_LABELS.get(quote.customer_document_type, quote.customer_document_type.upper())
    return {
        'logo_png': fiscal_logo.current_png(company),
        'design': design_for(company),
        'store_name': identity.name, 'store_legal_name': identity.legal_name, 'store_ruc': identity.tax_id,
        'store_address': identity.legal_address, 'store_city': identity.city,
        'store_phone': (getattr(branch, 'phone', '') or identity.phone or identity.whatsapp_number),
        'store_email': getattr(branch, 'email', '') or identity.contact_email,
        'store_website': identity.website_url,
        'branch_name': getattr(branch, 'name', '') or '', 'branch_address': getattr(branch, 'address', '') or '',
        'number': quote.label, 'revision': quote.revision,
        'issued_at': style.local_stamp(quote.created_at),
        'valid_until': quote.valid_until.strftime('%d/%m/%Y') if quote.valid_until else '',
        'seller_name': quote.seller_name_snapshot,
        'customer_name': quote.customer_name,
        'customer_document': (f'{document_label} {quote.customer_document_number}'.strip()
                              if quote.customer_document_number else ''),
        'customer_phone': quote.customer_phone, 'customer_email': quote.customer_email,
        'items': items,
        'tax': tax,
        'discount_label': (f'Descuento ({quote.discount_reason})' if quote.discount_reason
                           else 'Descuento promocional' if quote.discount_source == 'promotion' else 'Descuento'),
        'conditions': quote.conditions,
        'stamp': STAMPS.get(quote.effective_status, ''),
        'trade_in': trade_in_context(quote),
    }


# ---------------------------------------------------------------------------
# A4
# ---------------------------------------------------------------------------

def _totals(ctx) -> list[tuple[str, str]]:
    tax = ctx['tax']
    symbol = tax['symbol']
    rows = [('Subtotal', style.money(symbol, tax['subtotal']))]
    if tax['discount_amount'] > 0:
        rows.append((ctx['discount_label'], f"- {style.money(symbol, tax['discount_amount'])}"))
    for label, amount in ctx['design'].tax_rows(tax):
        rows.append((label, style.money(symbol, amount)))
    return rows


def quote_document(ctx: dict) -> dict:
    """The A4 description of a quote, from its plain-data context. PURE."""
    from . import document_design

    design = ctx.get('design') or document_design.DEFAULT
    tax = ctx['tax']
    quantity = sum(int(item['quantity']) for item in ctx['items'])
    return {
        'title': SALES_QUOTE_TITLE,
        'stamp': ctx.get('stamp') or '',
        'number': ctx['number'],
        'number_note': f"Válida hasta el {ctx['valid_until']}" if ctx['valid_until'] else '',
        'pdf_title': f"{ctx['number']} — {SALES_QUOTE_TITLE} — {ctx['store_name']}".rstrip(' —'),
        'author': ctx['store_name'] or '',
        'logo_png': ctx['logo_png'],
        'identity': document_design.a4_identity(ctx, design),
        **document_design.a4_slots(design),
        'parties': [
            ('Cliente', [
                ('Nombre', ctx['customer_name']),
                ('Documento', ctx['customer_document']),
                ('Teléfono', ctx['customer_phone']),
                ('Correo', ctx['customer_email']),
            ]),
            ('Datos de la cotización', [
                ('Número', ctx['number']),
                ('Fecha', ctx['issued_at']),
                ('Válida hasta', ctx['valid_until']),
                ('Atendido por', ctx['seller_name'] if design.show_seller else ''),
                ('Sucursal', ctx['branch_name']),
            ]),
        ],
        'lines': [{
            'number': item['line_number'], 'code': item['code'], 'description': item['name'],
            'units': item['units'], 'details': item['details'], 'unit': item['unit'],
            'list_price': item['unit_price'], 'discount': Decimal('0.00'), 'unit_price': item['unit_price'],
            'quantity': item['quantity'], 'amount': item['amount'],
        } for item in ctx['items']],
        # A quote has no line discount: the till has none. The P. LISTA / DSCTO. columns go.
        'hide_zero_columns': True,
        'summary': [f"Productos: {len(ctx['items'])}   ·   Unidades: {quantity}"],
        'amount_in_words': style.amount_in_words(tax['total'], tax['currency']),
        'totals': _totals(ctx),
        'total': style.money(tax['symbol'], tax['total']),
        'total_label': 'TOTAL COTIZADO',
        'legal': SALES_QUOTE_LEGEND,
        'sections': ([{
            'heading': TRADE_IN_QUOTE_HEADING, 'rows': ctx['trade_in']['rows'],
            'figures': _trade_in_figures(ctx), 'legend': TRADE_IN_QUOTE_LEGEND,
        }] if ctx.get('trade_in') else []),
        'notes': ctx['conditions'],
        'notes_heading': CONDITIONS_HEADING,
        'footnotes': [SALES_QUOTE_LEGEND_DETAIL],
        'footer': '  ·  '.join(part for part in (
            ctx['store_name'], f"RUC {ctx['store_ruc']}" if ctx['store_ruc'] else '', ctx['number'],
        ) if part),
    }


def render_a4_pdf(quote) -> bytes:
    from . import document_layout

    return document_layout.render_a4(quote_document(build_context(quote)))


# ---------------------------------------------------------------------------
# 80 mm PDF
# ---------------------------------------------------------------------------

def _lay_out(pdf, ctx: dict, content: float, margin: float, page_height: float, watermark=None) -> float:
    from .ticket_services import _Cursor, roll_band

    design = ctx['design']
    tax = ctx['tax']
    money = tax['symbol']
    cur = _Cursor(pdf, margin, page_height - margin, content)
    roll_band(cur, watermark)
    contact = design.contact(phone=ctx['store_phone'], email=ctx['store_email'], address=ctx['store_address'],
                             website=ctx['store_website'], roll=True)
    style.ticket_header(
        cur, logo_png=ctx['logo_png'], name=ctx['store_name'], legal_name=ctx['store_legal_name'],
        tax_id=ctx['store_ruc'], address=contact['address'], branch=ctx['branch_name'], phone=contact['phone'],
        email=contact['email'], website=contact['website'], logo_box_mm=design.ticket_logo_box_mm,
        logo_align=design.logo_ticket_align,
    )
    cur.rule()
    cur.line(SALES_QUOTE_TITLE, size=10.5, bold=True, align='center')
    if ctx.get('stamp'):
        cur.line(ctx['stamp'], size=11, bold=True, align='center')
    cur.line(ctx['number'], size=12, bold=True, align='center')
    cur.gap(2)
    cur.row('Fecha:', ctx['issued_at'], size=7)
    if ctx['valid_until']:
        cur.row('Válida hasta:', ctx['valid_until'], size=7, bold=True)
    if ctx['seller_name'] and design.show_seller:
        cur.row('Atendido por:', ctx['seller_name'], size=7)
    cur.rule()
    if ctx['customer_name']:
        cur.line(f"Cliente: {ctx['customer_name']}", size=7)
    if ctx['customer_document']:
        cur.line(ctx['customer_document'], size=7)
    if ctx['customer_phone']:
        cur.line(f"Teléfono: {ctx['customer_phone']}", size=7)
    if ctx['customer_name'] or ctx['customer_document'] or ctx['customer_phone']:
        cur.rule()
    for item in ctx['items']:
        cur.line(str(item['name']), size=7.5, bold=True)
        for detail in item['details']:
            cur.line(detail, size=6.5)
        for unit in item['units']:
            for identifier in style.unit_identifiers(unit):
                cur.line(identifier, size=6.5)
        cur.row(f"  {style.quantity(item['quantity'])} x {money} {Decimal(item['unit_price']):.2f}",
                f"{money} {Decimal(item['amount']):.2f}", size=7.5)
    cur.rule()
    for label, value in _totals(ctx):
        cur.row(label, value, size=7.5)
    cur.row('TOTAL', f"{money} {Decimal(tax['total']):.2f}", size=10.5, bold=True)
    if ctx.get('trade_in'):
        cur.rule()
        cur.line(TRADE_IN_QUOTE_HEADING, size=7.5, bold=True)
        for label, value in ctx['trade_in']['rows']:
            if value:
                cur.line(f'{label}: {value}', size=6.5)
        for label, value in _trade_in_figures(ctx):
            cur.row(label, value, size=7.5, bold=label == TRADE_IN_BALANCE_LABEL)
        cur.line(TRADE_IN_QUOTE_LEGEND, size=6.5, bold=True)
    if ctx['conditions']:
        cur.rule()
        cur.line('Condiciones comerciales', size=7, bold=True)
        for text in str(ctx['conditions']).split('\n'):
            if text.strip():
                cur.line(text, size=6.5)
    cur.rule()
    cur.line(SALES_QUOTE_LEGEND, size=8, bold=True, align='center')
    cur.line(SALES_QUOTE_LEGEND_DETAIL, size=6.5, align='center')
    if design.thank_you_message:
        cur.gap(2)
        for text in design.message_lines('thank_you'):
            cur.line(text, size=7.5, bold=True, align='center')
    if design.footer_text:
        cur.line(design.footer_text, size=6.5, align='center')
    roll_band(cur, watermark, bottom=True)
    cur.gap(margin)
    return cur.used + margin


def ticket_pdf_from_ctx(ctx: dict, *, watermark=None) -> bytes:
    """The 80 mm roll of a quote. PURE. Two passes: measure, then draw at that exact height."""
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    from .ticket_services import TICKET_MARGIN_MM, TICKET_WIDTH_MM

    width = float(TICKET_WIDTH_MM) * mm
    margin = float(TICKET_MARGIN_MM) * mm
    content = width - 2 * margin
    height = _lay_out(None, ctx, content, margin, 0.0, watermark)
    buffer = io.BytesIO()
    pdf = pdfcanvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f"{ctx['number']} — {ctx['store_name']}" if ctx['store_name'] else ctx['number'])
    pdf.setAuthor(ctx['store_name'] or '')
    if watermark is not None:
        style.draw_diagonal(pdf, width, height, watermark.lines)
    _lay_out(pdf, ctx, content, margin, height, watermark)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def render_ticket_pdf(quote) -> bytes:
    return ticket_pdf_from_ctx(build_context(quote))


# ---------------------------------------------------------------------------
# ESC/POS (thermal roll, through the print queue)
# ---------------------------------------------------------------------------

def escpos_from_ctx(ctx: dict, *, paper_width_mm: int = 80, encoding: str = 'cp858', dots: int | None = None) -> bytes:
    """The same quote for the thermal printer. PURE. NOT VERIFIED ON A PRINTER."""
    from .printing.escpos import DOTS_PER_MM, Receipt

    design = ctx['design']
    tax = ctx['tax']
    money = tax['symbol']
    r = Receipt(paper_width_mm=paper_width_mm, encoding=encoding, dots=dots)
    contact = design.contact(phone=ctx['store_phone'], email=ctx['store_email'], address=ctx['store_address'],
                             website=ctx['store_website'], roll=True)
    if ctx.get('logo_png'):
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
    if ctx['branch_name']:
        r.line(f"Sucursal: {ctx['branch_name']}", align='center')
    if contact['phone']:
        r.line(f"Tel. {contact['phone']}", align='center')
    if contact['email']:
        r.line(contact['email'], align='center')
    r.rule()
    r.line(SALES_QUOTE_TITLE, align='center', bold=True)
    if ctx.get('stamp'):
        r.line(ctx['stamp'], align='center', bold=True, large=True)
    r.line(ctx['number'], align='center', bold=True, large=True)
    r.row('Fecha:', ctx['issued_at'])
    if ctx['valid_until']:
        r.row('Valida hasta:', ctx['valid_until'], bold=True)
    if ctx['seller_name'] and design.show_seller:
        r.row('Atendido por:', ctx['seller_name'])
    r.rule()
    if ctx['customer_name']:
        r.line(f"Cliente: {ctx['customer_name']}")
    if ctx['customer_document']:
        r.line(ctx['customer_document'])
    if ctx['customer_phone']:
        r.line(f"Telefono: {ctx['customer_phone']}")
    r.rule()
    for item in ctx['items']:
        r.line(str(item['name']), bold=True)
        for detail in item['details']:
            r.line(detail)
        for unit in item['units']:
            for identifier in style.unit_identifiers(unit):
                r.line(identifier)
        r.row(f" {style.quantity(item['quantity'])} x {money} {Decimal(item['unit_price']):.2f}",
              f"{money} {Decimal(item['amount']):.2f}")
    r.rule()
    for label, value in _totals(ctx):
        r.row(label, value)
    r.row('TOTAL', f"{money} {Decimal(tax['total']):.2f}", bold=True, large=design.emphasize_total)
    if ctx.get('trade_in'):
        r.rule()
        r.line(TRADE_IN_QUOTE_HEADING, bold=True)
        for label, value in ctx['trade_in']['rows']:
            if value:
                r.line(f'{label}: {value}')
        for label, value in _trade_in_figures(ctx):
            r.row(label, value, bold=label == TRADE_IN_BALANCE_LABEL)
        r.line(TRADE_IN_QUOTE_LEGEND, bold=True)
    if ctx['conditions']:
        r.rule()
        r.line('Condiciones comerciales', bold=True)
        for text in str(ctx['conditions']).split('\n'):
            if text.strip():
                r.line(text)
    r.rule()
    r.line(SALES_QUOTE_LEGEND, align='center', bold=True)
    r.line(SALES_QUOTE_LEGEND_DETAIL, align='center')
    for text in design.message_lines('thank_you'):
        r.line(text, align='center', bold=True)
    if design.footer_text:
        r.line(design.footer_text, align='center')
    return r.cut().to_bytes()


def escpos_ticket(quote, **options) -> bytes:
    return escpos_from_ctx(build_context(quote), **options)
