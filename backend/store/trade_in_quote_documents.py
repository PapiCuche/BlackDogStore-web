"""
TRADE-IN VALUATION PRO · Phase 5b — the papers of a trade-in quote: A4, 80 mm PDF and ESC/POS for the thermal roll
(docs/adr-trade-in-valuation.md §9).

    «COTIZACIÓN DE RECEPCIÓN DE EQUIPO USADO», its number and revision, the exact validity (shown in the company's
    time zone), the device (IMEI or serial MASKED), its verdict as printed (APTO / APTO CON DESCUENTOS — never
    «requiere aprobación»: only an issued quote prints), the value offered, the difference when it is a part-exchange,
    the company's conditions — and «No es comprobante de pago ni constancia de compra».

FROM THE FROZEN SNAPSHOT, ALWAYS. `build_context` reads the REVISION's own columns (the values, the printable verdict,
the conditions copied when it was written) and the company's identity; it never recalculates. A reprint is the same
revision's paper. Only the stamp follows the revision's state (VENCIDA, REEMPLAZADA…).

NEVER ON THE PAPER: the refurbishment cost, the resale estimate, the margin, the tariff's base or maximum, the
deductions, any note or reason, a full IMEI or serial, the customer's document. No fiscal series and no QR (the public
link is phase 5c).

Drawn by the shared renderers (`document_layout.render_a4`, `ticket_services._Cursor`, `printing.escpos.Receipt`);
identity, logo and design are the company's: nothing here names a shop. `quote_document`, `ticket_pdf_from_ctx` and
`escpos_from_ctx` are PURE.
"""
from __future__ import annotations

import io
from decimal import Decimal

from django.utils import timezone

from . import document_style as style
from .document_legends import (
    TRADE_IN_DIFFERENCE_NOTE,
    TRADE_IN_RECEPTION_LEGEND,
    TRADE_IN_RECEPTION_LEGEND_DETAIL,
    TRADE_IN_RECEPTION_TITLE,
)

CONDITIONS_HEADING = 'CONDICIONES'
OFFER_LABEL = 'VALOR OFRECIDO'
#: The statuses whose paper may be printed: the quote was handed to the customer at some point.
PRINTABLE = frozenset({'issued', 'accepted', 'declined', 'expired', 'superseded'})
STAMPS = {'expired': 'VENCIDA', 'superseded': 'REEMPLAZADA', 'declined': 'NO ACEPTADA'}
VERDICTS = {'eligible': 'APTO', 'eligible_with_deductions': 'APTO CON DESCUENTOS'}


class DocumentError(Exception):
    """This revision has no paper (it was never issued)."""


def _masked(last4: str) -> str:
    return f'****{last4}' if last4 else ''


def filename(quote, kind: str = 'a4') -> str:
    from .pdf_services import _safe_slug

    slug = _safe_slug(getattr(quote.company, 'slug', ''))
    base = f'cotizacion-recepcion-{_safe_slug(quote.label.lower(), 60)}'
    suffix = '-ticket80' if kind == 'ticket80' else ''
    return f'{slug}-{base}{suffix}.pdf' if slug else f'{base}{suffix}.pdf'


def _local(company, value) -> str:
    from .trade_in_tariff import company_timezone

    if value is None:
        return ''
    return timezone.localtime(value, company_timezone(company)).strftime('%d/%m/%Y %H:%M')


def build_context(quote) -> dict:
    """Everything the papers say, from the revision's frozen columns and the company's identity."""
    from . import fiscal_logo
    from .company_settings import company_identity
    from .document_design import design_for

    status = quote.effective_status
    if quote.issued_at is None or status not in PRINTABLE:
        raise DocumentError('Sólo se imprime una cotización emitida.')
    company = quote.company
    identity = company_identity(company)
    branch = quote.branch
    assessment = quote.assessment
    check = assessment.device_check
    customer = assessment.customer
    device = ' '.join(part for part in (assessment.brand, assessment.model_name, assessment.capacity,
                                         assessment.sim, assessment.region) if part)
    identifiers = [label for label in (
        f'IMEI {_masked(check.imei_last4)}' if check.imei_last4 else '',
        f'IMEI 2 {_masked(check.imei2_last4)}' if check.imei2_last4 else '',
        f'Serie {_masked(check.serial_last4)}' if check.serial_last4 and not check.imei_last4 else '',
    ) if label]
    symbol = 'S/' if quote.currency == 'PEN' else quote.currency
    trading = quote.target_price is not None
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
        'issued_at': _local(company, quote.issued_at),
        'valid_until': _local(company, quote.valid_until),
        'customer_name': customer.display_name if customer is not None else '',
        'device': device, 'identifiers': identifiers,
        'verdict': VERDICTS.get(quote.printable_eligibility, ''),
        'symbol': symbol, 'currency': quote.currency,
        'offered': quote.offered_value,
        'target_price': quote.target_price if trading else None,
        'difference': quote.difference if trading else None,
        'conditions': quote.conditions,
        'stamp': STAMPS.get(status, ''),
    }


def _figures(ctx) -> list[tuple[str, str]]:
    rows = [('Valor ofrecido por su equipo', style.money(ctx['symbol'], ctx['offered']))]
    if ctx.get('target_price') is not None:
        rows.append(('Precio del equipo que compra', style.money(ctx['symbol'], ctx['target_price'])))
        rows.append(('Diferencia a pagar (referencial)', style.money(ctx['symbol'], ctx['difference'])))
    return rows


def quote_document(ctx: dict) -> dict:
    """The A4 description of a trade-in quote, from its plain-data context. PURE."""
    from . import document_design

    design = ctx.get('design') or document_design.DEFAULT
    details = list(ctx['identifiers'])
    if ctx['verdict']:
        details.append(f"Evaluación: {ctx['verdict']}")
    return {
        'title': TRADE_IN_RECEPTION_TITLE,
        'stamp': ctx.get('stamp') or '',
        'number': ctx['number'],
        'number_note': f"Válida hasta el {ctx['valid_until']}",
        'pdf_title': f"{ctx['number']} — {TRADE_IN_RECEPTION_TITLE} — {ctx['store_name']}".rstrip(' —'),
        'author': ctx['store_name'] or '',
        'logo_png': ctx['logo_png'],
        'identity': document_design.a4_identity(ctx, design),
        **document_design.a4_slots(design),
        'parties': [
            ('Cliente', [('Nombre', ctx['customer_name'])]),
            ('Datos de la cotización', [
                ('Número', ctx['number']),
                ('Emitida', ctx['issued_at']),
                ('Válida hasta', ctx['valid_until']),
                ('Sucursal', ctx['branch_name']),
            ]),
        ],
        'lines': [{
            'number': 1, 'code': '', 'description': ctx['device'], 'units': [], 'details': details,
            'unit': 'UND', 'list_price': ctx['offered'], 'discount': Decimal('0.00'), 'unit_price': ctx['offered'],
            'quantity': 1, 'amount': ctx['offered'],
        }],
        'hide_zero_columns': True,
        'summary': [],
        'amount_in_words': style.amount_in_words(ctx['offered'], ctx['currency']),
        'totals': _figures(ctx)[1:],
        'total': style.money(ctx['symbol'], ctx['offered']),
        'total_label': OFFER_LABEL,
        'legal': TRADE_IN_RECEPTION_LEGEND,
        'notes': ctx['conditions'],
        'notes_heading': CONDITIONS_HEADING,
        'footnotes': [TRADE_IN_RECEPTION_LEGEND_DETAIL]
        + ([TRADE_IN_DIFFERENCE_NOTE] if ctx.get('target_price') is not None else []),
        'footer': '  ·  '.join(part for part in (
            ctx['store_name'], f"RUC {ctx['store_ruc']}" if ctx['store_ruc'] else '', ctx['number'],
        ) if part),
    }


def render_a4_pdf(quote) -> bytes:
    from . import document_layout

    return document_layout.render_a4(quote_document(build_context(quote)))


def _lay_out(pdf, ctx: dict, content: float, margin: float, page_height: float) -> float:
    from .ticket_services import _Cursor

    design = ctx['design']
    cur = _Cursor(pdf, margin, page_height - margin, content)
    contact = design.contact(phone=ctx['store_phone'], email=ctx['store_email'], address=ctx['store_address'],
                             website=ctx['store_website'], roll=True)
    style.ticket_header(
        cur, logo_png=ctx['logo_png'], name=ctx['store_name'], legal_name=ctx['store_legal_name'],
        tax_id=ctx['store_ruc'], address=contact['address'], branch=ctx['branch_name'], phone=contact['phone'],
        email=contact['email'], website=contact['website'], logo_box_mm=design.ticket_logo_box_mm,
        logo_align=design.logo_ticket_align,
    )
    cur.rule()
    cur.line(TRADE_IN_RECEPTION_TITLE, size=9.5, bold=True, align='center')
    if ctx.get('stamp'):
        cur.line(ctx['stamp'], size=11, bold=True, align='center')
    cur.line(ctx['number'], size=12, bold=True, align='center')
    cur.gap(2)
    cur.row('Emitida:', ctx['issued_at'], size=7)
    cur.row('Válida hasta:', ctx['valid_until'], size=7, bold=True)
    cur.rule()
    if ctx['customer_name']:
        cur.line(f"Cliente: {ctx['customer_name']}", size=7)
        cur.rule()
    cur.line(ctx['device'], size=7.5, bold=True)
    for identifier in ctx['identifiers']:
        cur.line(identifier, size=6.5)
    if ctx['verdict']:
        cur.line(f"Evaluación: {ctx['verdict']}", size=7)
    cur.rule()
    for label, value in _figures(ctx):
        cur.row(label, value, size=7.5, bold=label.startswith('Valor'))
    if ctx.get('target_price') is not None:
        cur.line(TRADE_IN_DIFFERENCE_NOTE, size=6.5)
    if ctx['conditions']:
        cur.rule()
        cur.line('Condiciones', size=7, bold=True)
        for text in str(ctx['conditions']).split('\n'):
            if text.strip():
                cur.line(text, size=6.5)
    cur.rule()
    cur.line(TRADE_IN_RECEPTION_LEGEND, size=8, bold=True, align='center')
    cur.line(TRADE_IN_RECEPTION_LEGEND_DETAIL, size=6.5, align='center')
    if design.footer_text:
        cur.line(design.footer_text, size=6.5, align='center')
    cur.gap(margin)
    return cur.used + margin


def ticket_pdf_from_ctx(ctx: dict) -> bytes:
    """The 80 mm roll. PURE. Two passes: measure, then draw at that exact height."""
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    from .ticket_services import TICKET_MARGIN_MM, TICKET_WIDTH_MM

    width = float(TICKET_WIDTH_MM) * mm
    margin = float(TICKET_MARGIN_MM) * mm
    content = width - 2 * margin
    height = _lay_out(None, ctx, content, margin, 0.0)
    buffer = io.BytesIO()
    pdf = pdfcanvas.Canvas(buffer, pagesize=(width, height), invariant=1)
    pdf.setTitle(f"{ctx['number']} — {ctx['store_name']}" if ctx['store_name'] else ctx['number'])
    pdf.setAuthor(ctx['store_name'] or '')
    _lay_out(pdf, ctx, content, margin, height)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def render_ticket_pdf(quote) -> bytes:
    return ticket_pdf_from_ctx(build_context(quote))


def escpos_from_ctx(ctx: dict, *, paper_width_mm: int = 80, encoding: str = 'cp858', dots: int | None = None) -> bytes:
    """The same quote for the thermal printer. PURE. NOT VERIFIED ON A PRINTER."""
    from .printing.escpos import DOTS_PER_MM, Receipt

    design = ctx['design']
    r = Receipt(paper_width_mm=paper_width_mm, encoding=encoding, dots=dots)
    contact = design.contact(phone=ctx['store_phone'], email=ctx['store_email'], address=ctx['store_address'],
                             website=ctx['store_website'], roll=True)
    if ctx.get('logo_png'):
        box_width, box_height = design.ticket_logo_box_mm
        r.image(ctx['logo_png'], max_dots=round(box_width * DOTS_PER_MM),
                max_height=round(box_height * DOTS_PER_MM), align=design.logo_ticket_align, enlarge=True)
    r.line(ctx['store_name'] or ctx['store_legal_name'], align='center', bold=True)
    if ctx['store_ruc']:
        r.line(f"RUC {ctx['store_ruc']}", align='center')
    if contact['address']:
        r.line(contact['address'], align='center')
    if ctx['branch_name']:
        r.line(f"Sucursal: {ctx['branch_name']}", align='center')
    if contact['phone']:
        r.line(f"Tel. {contact['phone']}", align='center')
    r.rule()
    r.line(TRADE_IN_RECEPTION_TITLE, align='center', bold=True)
    if ctx.get('stamp'):
        r.line(ctx['stamp'], align='center', bold=True, large=True)
    r.line(ctx['number'], align='center', bold=True, large=True)
    r.row('Emitida:', ctx['issued_at'])
    r.row('Valida hasta:', ctx['valid_until'], bold=True)
    r.rule()
    if ctx['customer_name']:
        r.line(f"Cliente: {ctx['customer_name']}")
        r.rule()
    r.line(ctx['device'], bold=True)
    for identifier in ctx['identifiers']:
        r.line(identifier)
    if ctx['verdict']:
        r.line(f"Evaluacion: {ctx['verdict']}")
    r.rule()
    for label, value in _figures(ctx):
        r.row(label, value, bold=label.startswith('Valor'))
    if ctx.get('target_price') is not None:
        r.line(TRADE_IN_DIFFERENCE_NOTE)
    if ctx['conditions']:
        r.rule()
        r.line('Condiciones', bold=True)
        for text in str(ctx['conditions']).split('\n'):
            if text.strip():
                r.line(text)
    r.rule()
    r.line(TRADE_IN_RECEPTION_LEGEND, align='center', bold=True)
    r.line(TRADE_IN_RECEPTION_LEGEND_DETAIL, align='center')
    if design.footer_text:
        r.line(design.footer_text, align='center')
    return r.cut().to_bytes()


def escpos_ticket(quote, **options) -> bytes:
    return escpos_from_ctx(build_context(quote), **options)
