"""
DOC-UX-02 — a SAMPLE of each paper, drawn with the real renderers and no real data.

    render_preview('factura', company=company)  ->  bytes of a PDF

WHY THIS IS NOT A SECOND LAYOUT. Every document here is drawn by the SAME
functions that draw the real ones: `sales_note_document` + `render_a4`,
`order_receipt_document` + `render_a4`, `ticket_pdf_from_ctx`,
`render_fiscal_a4` and `render_fiscal_ticket`. What is different is only the
`ctx` they are given: a fixed, synthetic one, built below. If the real paper
changes, the sample changes with it, because there is nothing to keep in step.

WHAT THE SAMPLE MAY NEVER BE
----------------------------
  * A real document. Nothing is read from an order, a customer, a sales note or a
    fiscal document, and nothing is written: no `pdf_generated_at`, no
    sequence, no `FiscalDocument`, no audit row. `generate_sales_note_pdf`
    (which stamps `pdf_generated_at`) is never called from here.
  * Mistakable for one. EVERY page carries «MUESTRA · NO FISCAL» twice: as a
    diagonal watermark and as a framed band (`document_style.Watermark`, drawn
    by the renderer itself, so no document can leave it out). The number is
    «MUESTRA-000000», the PDF title begins with it, the file is named
    `MUESTRA-….pdf`.
  * A SUNAT payload. The QR of a fiscal sample holds the TEXT «MUESTRA – NO
    ESCANEAR»: it has no `|`-separated fields and cannot be read as the
    standard payload (`fiscal.qr.build_qr_payload`). The digest is «MUESTRA».
  * The company's fiscal identity. A boleta or a factura sample is issued by
    «EMPRESA DE MUESTRA S.A.C.», RUC 20000000000, at a made-up address. The
    company's real RUC and legal name never reach a fiscal sample. «SUNAT BETA»
    appears on it only when the server operates against the BETA environment,
    exactly as on a real one.

WHAT IT MAY BE THE COMPANY'S. The logo (`fiscal_logo.current_png`, which reads
the logo of THAT company and no other) and, on the two internal papers, the
company's own header (name, legal name, RUC, address, phone, e-mail, the
default branch, the warranty text): they are what the person is designing, the
paper says it is a sample, and nothing in them is a customer's.

THE SYNTHETIC LINES are chosen to break a layout: a phone with a made-up
serial and IMEI, an accessory, a name that wraps over many lines and carries a
word with no spaces, and a name with `&` and `<`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from . import document_style as style
from .document_style import Watermark

#: The document kinds the preview accepts, and nothing else.
KINDS = (
    'sales_note', 'order_receipt', 'ticket80_sales_note',
    'boleta', 'factura', 'ticket80_boleta', 'ticket80_factura',
)

SAMPLE_NUMBER = 'MUESTRA-000000'
SAMPLE_TEXT = 'MUESTRA · NO FISCAL'
SAMPLE_MARK = Watermark(lines=(SAMPLE_TEXT,), band=SAMPLE_TEXT)

#: Made up. Nothing here is, or looks like, a real taxpayer.
SAMPLE_ISSUER_RUC = '20000000000'
SAMPLE_ISSUER_NAME = 'EMPRESA DE MUESTRA S.A.C.'
SAMPLE_ISSUER_TRADE_NAME = 'Nombre comercial de muestra'
SAMPLE_ISSUER_ADDRESS = 'Av. de Muestra 123, Ciudad de Muestra'
#: Not a SUNAT payload: no «|», so it cannot be read as the standard fields.
SAMPLE_QR_TEXT = 'MUESTRA – NO ESCANEAR'
SAMPLE_DIGEST = 'MUESTRA'

_SAMPLE_INSTANT = datetime(2026, 1, 1, 12, 0)
_SAMPLE_STAMP = '01/01/2026 12:00'
_RATE = Decimal('0.18')

_LONG_NAME = ('Accesorio de muestra con un nombre tan largo que tiene que partirse en varias líneas '
              'del papel y además ConUnaPalabraSinEspaciosMuyLargaParaProbarElCorteDeLinea1234567890')
_MARKUP_NAME = 'Cargador A&B <65W> de muestra'

#: (name, code, quantity, price WITH tax, unit identifiers)
_UNIT = {'serial_number': 'MUESTRA-SN-0001', 'imei': '000000000000001', 'imei2': '000000000000002'}
_LINES = (
    ('Teléfono de muestra 256 GB', 'MUESTRA-001', 1, Decimal('118.00'), [_UNIT]),
    ('Funda de muestra', 'MUESTRA-002', 2, Decimal('59.00'), []),
    (_LONG_NAME, 'MUESTRA-003', 1, Decimal('29.50'), []),
    (_MARKUP_NAME, 'MUESTRA-004', 1, Decimal('47.20'), []),
)
#: The order-level discount of the internal papers. The lines carry a discount of 0.
_DISCOUNT = Decimal('13.00')


def file_name(kind: str) -> str:
    return f'{SAMPLE_NUMBER}-{kind.replace("_", "-")}.pdf'


def render_preview(kind: str, *, company, extra_lines: int = 0) -> bytes:
    """
    The sample PDF of `kind` for `company`. READ-ONLY.

    `extra_lines` adds that many plain lines (tests use it to get a sample of
    several pages); the endpoint never passes it.
    """
    if kind not in KINDS:
        raise ValueError(f'Unknown document kind: {kind!r}')

    from . import document_layout, fiscal_pdf_services as fiscal, pdf_services, sales_note_services, ticket_services

    lines = _lines(extra_lines)
    if kind == 'sales_note':
        return document_layout.render_a4(
            sales_note_services.sales_note_document(_sales_note_ctx(company, lines)), watermark=SAMPLE_MARK)
    if kind == 'ticket80_sales_note':
        return ticket_services.ticket_pdf_from_ctx(_sales_note_ctx(company, lines), watermark=SAMPLE_MARK)
    if kind == 'order_receipt':
        return document_layout.render_a4(
            pdf_services.order_receipt_document(_receipt_ctx(company, lines)), watermark=SAMPLE_MARK)

    ticket = kind.startswith('ticket80_')
    ctx = _fiscal_ctx(company, kind.removeprefix('ticket80_'), lines)
    if ticket:
        return fiscal.render_fiscal_ticket(ctx, watermark=SAMPLE_MARK)
    return fiscal.render_fiscal_a4(ctx, watermark=SAMPLE_MARK)


# ---------------------------------------------------------------------------
# The synthetic lines
# ---------------------------------------------------------------------------

def _lines(extra: int):
    lines = list(_LINES)
    for n in range(extra):
        lines.append((f'Artículo de muestra {n + 1:03d}', f'MUESTRA-{n + 5:03d}', 1, Decimal('11.80'), []))
    return lines


def _internal_lines(lines) -> list[dict]:
    """The lines as `pdf_services.order_lines` returns them."""
    return [{
        'line_number': number, 'code': code, 'name': name, 'unit': 'UND',
        'list_price': price, 'discount': Decimal('0.00'), 'unit_price': price,
        'quantity': quantity, 'subtotal': price * quantity, 'units': units,
    } for number, (name, code, quantity, price, units) in enumerate(lines, start=1)]


def _identity(company):
    from . import company_settings

    return company_settings.company_identity(company)


def _presentation(company) -> dict:
    """What `order_presentation` gives a real sale: logo, branch, payment, channel, seller."""
    from . import fiscal_logo

    branch = getattr(company, 'default_inventory_branch', None)
    return {
        'logo_png': fiscal_logo.current_png(company),
        'branch_name': getattr(branch, 'name', '') or '',
        'branch_address': getattr(branch, 'address', '') or '',
        'branch_phone': getattr(branch, 'phone', '') or '',
        'payment_label': 'Efectivo',
        'channel_label': 'Mostrador',
        'seller_name': 'Vendedor de muestra',
    }


def _sales_note_ctx(company, lines) -> dict:
    """The same keys as `sales_note_services.build_sales_note_context`, from made-up data."""
    from . import pdf_services
    from .sales_note_services import SALES_NOTE_CONTROL_LINE, tax_context_from_breakdown
    from .tax_services import breakdown_from_total

    items = _internal_lines(lines)
    subtotal = sum((item['subtotal'] for item in items), Decimal('0.00'))
    total = subtotal - _DISCOUNT
    tax = tax_context_from_breakdown(breakdown_from_total(
        total=total, subtotal=subtotal, discount_amount=_DISCOUNT, tax_rate=_RATE, currency='PEN'))
    identity = _identity(company)
    return {
        **_presentation(company),
        'product_count': len(items),
        'unit_count': sum(item['quantity'] for item in items),
        'amount_in_words': style.amount_in_words(tax['total'], tax['currency']),
        'title': 'Nota de venta',
        'stamp': '',
        'control_line': SALES_NOTE_CONTROL_LINE,
        'number': SAMPLE_NUMBER,
        'issued_at': _SAMPLE_STAMP,
        # A string on purpose: the papers print «#» + this.
        'order_id': SAMPLE_NUMBER,
        'customer_name': 'CLIENTE DE MUESTRA',
        'customer_phone': '999 999 999',
        'customer_email': 'cliente@muestra.invalid',
        'document_label': 'DNI',
        'document_number': '00000000',
        'receipt_label': 'Boleta',
        'delivery_label': 'Recojo en tienda',
        'full_address': '',
        'notes': 'Observaciones de muestra.',
        'items': items,
        'discount_amount': _DISCOUNT,
        'total': total,
        'tax': tax,
        'store_name': identity.name,
        'store_legal_name': identity.legal_name,
        'store_ruc': identity.tax_id,
        'store_address': identity.legal_address,
        'store_city': identity.city,
        'store_phone': identity.phone,
        'store_email': identity.contact_email,
        'warranty_note': identity.warranty_policy_text or pdf_services._GENERIC_WARRANTY_NOTE,
        'pickup_name': '',
        'pickup_address': '',
    }


def _receipt_ctx(company, lines) -> dict:
    """The same keys as `pdf_services.build_order_pdf_context`, from made-up data."""
    from . import pdf_services

    items = _internal_lines(lines)
    subtotal = sum((item['subtotal'] for item in items), Decimal('0.00'))
    identity = _identity(company)
    return {
        **_presentation(company),
        'title': 'Constancia de pedido — Boleta solicitada',
        'number': SAMPLE_NUMBER,
        'cancelled': False,
        'disclaimer': pdf_services.DISCLAIMER,
        'warranty_note': identity.warranty_policy_text or pdf_services._GENERIC_WARRANTY_NOTE,
        'order_id': SAMPLE_NUMBER,
        'created_at': _SAMPLE_STAMP,
        'paid_at': _SAMPLE_STAMP,
        'status_label': 'Pagado',
        'fulfillment_label': 'Pendiente',
        'customer_name': 'CLIENTE DE MUESTRA',
        'customer_email': 'cliente@muestra.invalid',
        'customer_phone': '999 999 999',
        'document_label': 'DNI',
        'document_number': '00000000',
        'delivery_label': 'Recojo en tienda',
        'full_address': '',
        'reference': '',
        'notes': 'Observaciones de muestra.',
        'items': [{'product_name': item['name'], 'price': item['unit_price'], **item} for item in items],
        'total': subtotal - _DISCOUNT,
        'discount_amount': _DISCOUNT,
        'coupon_code': 'MUESTRA10',
        'currency': 'PEN',
        'product_count': len(items),
        'unit_count': sum(item['quantity'] for item in items),
        'receipt_label': 'Boleta',
        'store_name': identity.name,
        'store_legal_name': identity.legal_name,
        'store_ruc': identity.tax_id,
        'store_address': identity.legal_address,
        'store_city': identity.city,
        'store_phone': identity.phone,
        'store_email': identity.contact_email,
        'pickup_name': '',
        'pickup_address': '',
        'pickup_is_branch': False,
    }


def _fiscal_ctx(company, kind: str, lines) -> dict:
    """
    The same keys as `fiscal_pdf_services.build_fiscal_context`, from made-up data.

    NOTHING of the company's fiscal identity is used: not its RUC, not its legal
    name, not its address. Only its logo, and the BETA/production state.
    """
    from . import fiscal_logo
    from .fiscal.qr import render_qr_png
    from .fiscal_pdf_services import _TITLE, _customer_block
    from .tax_services import money

    invoice = kind == 'factura'
    document_type = '01' if invoice else '03'
    rows, taxable = [], Decimal('0.00')
    for name, _code, quantity, price_with_tax, _units in lines:
        unit_price = money(price_with_tax / (Decimal('1') + _RATE))
        amount = unit_price * quantity
        taxable += amount
        rows.append({
            'description': name, 'quantity': Decimal(quantity).quantize(Decimal('0.01')),
            'unit_price': unit_price, 'unit_price_with_tax': price_with_tax,
            'amount': amount, 'unit_code': 'NIU',
        })
    tax_amount = money(taxable * _RATE)
    total = taxable + tax_amount
    title = _TITLE[document_type]
    return {
        'title': title,
        'note': None,
        'identifier': SAMPLE_NUMBER,
        'issued_at': _SAMPLE_INSTANT,
        'currency': 'PEN',
        'issuer': {
            'tax_id': SAMPLE_ISSUER_RUC, 'legal_name': SAMPLE_ISSUER_NAME,
            'trade_name': SAMPLE_ISSUER_TRADE_NAME, 'address': SAMPLE_ISSUER_ADDRESS,
        },
        'customer': _customer_block(
            '6' if invoice else '1', '20000000001' if invoice else '00000000',
            'CLIENTE DE MUESTRA S.A.C.' if invoice else 'CLIENTE DE MUESTRA'),
        'legend': 'Representación impresa de la ' + title.lower(),
        'amount_in_words': style.amount_in_words(total, 'PEN'),
        'payment_form': 'Contado',
        'lines': rows,
        'taxable_amount': taxable,
        'tax_amount': tax_amount,
        'total': total,
        # Never «Aceptada por SUNAT»: nothing was sent anywhere.
        'status_text': 'MUESTRA · no emitido, sin validez',
        'is_accepted': False,
        'is_test': _is_beta(),
        'qr_payload': SAMPLE_QR_TEXT,
        'qr_png': render_qr_png(SAMPLE_QR_TEXT),
        'logo_png': fiscal_logo.current_png(company),
        'digest_value': SAMPLE_DIGEST,
        'tax_rate': _RATE,
    }


def _is_beta() -> bool:
    """«SUNAT BETA» only when the server operates against the BETA environment, as a real document does."""
    from .fiscal_config import FiscalConfigError, resolve_environment
    from .models import FiscalEnvironment

    try:
        return resolve_environment() == FiscalEnvironment.BETA
    except FiscalConfigError:
        return False
