"""
Internal sales note service — Phase 6.0.

A SalesNote is an INTERNAL document for a paid order. It is NOT a SUNAT
electronic receipt, NOT fiscal numbering and carries no tax validity. Every PDF
must state that visibly.

Hard rules:
  - Only PAID orders get a note. pending_payment / failed / expired / cancelled
    are rejected.
  - One note per order (OneToOne) — get_or_create_sales_note is idempotent.
  - The internal correlativo is allocated by store.sequences, from the series
    that belongs to the order's own company (and branch, under branch scope).
  - The PDF never contains stripe_session_id, stripe_payment_intent_id,
    payment_error, tokens, cookies or secrets.
  - Issuing a note never touches payment state and never touches inventory.
"""

from __future__ import annotations

import io
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import company_settings as _company_settings
from . import sequences as _sequences
from .models import Order, SalesNote
from .pdf_services import (
    _DELIVERY_LABELS,
    _DOCUMENT_LABELS,
    _GENERIC_WARRANTY_NOTE,
    _RECEIPT_LABELS,
    _safe_slug,
)

# Must appear visibly on every generated sales-note PDF.
SALES_NOTE_DISCLAIMER = (
    "Documento interno de venta. "
    "No válido como comprobante electrónico SUNAT."
)

_TITLE = "Nota de venta interna"


class SalesNoteError(Exception):
    """Business-rule violation. Views map this to HTTP 400."""


# ---------------------------------------------------------------------------
# Create / fetch
# ---------------------------------------------------------------------------

def get_or_create_sales_note(order: Order, actor=None) -> tuple[SalesNote, bool]:
    """
    Return (note, created). Idempotent — an order never gets a second note.

    Raises SalesNoteError if the order is not paid.
    """
    if not order.paid or order.status != Order.Status.PAID:
        raise SalesNoteError(
            'Solo se puede emitir una nota de venta interna para órdenes pagadas.'
        )

    existing = SalesNote.objects.filter(order=order).first()
    if existing:
        return existing, False

    with transaction.atomic():
        # LOCK ORDER: order first, sequence second. Every issuing path uses this
        # order and nothing uses the reverse — see store/sequences.py.
        #
        # The order lock is what makes issuance idempotent under concurrency:
        # two simultaneous requests for the SAME order both arrive here, one
        # waits, and the waiter finds the note already created and consumes NO
        # number. Allocating before this check would burn an ordinal on a note
        # that is never written.
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        existing = SalesNote.objects.filter(order=locked_order).first()
        if existing:
            return existing, False

        sequence = _sequences.resolve_sequence_for_order(locked_order)
        value, number = _sequences.allocate(sequence)

        note = SalesNote.objects.create(
            order=locked_order,
            sequence=sequence,
            sequence_value=value,
            number=number,
            status=SalesNote.STATUS_ISSUED,
            issued_at=timezone.now(),
            created_by=actor if getattr(actor, 'is_authenticated', False) else None,
            metadata={
                'order_id': locked_order.pk,
                'sequence_id': sequence.pk,
                'branch_id': sequence.branch_id,
            },
        )

    return note, True


def get_sales_note_filename(sales_note: SalesNote) -> str:
    """
    Safe ASCII filename for the download.

    Built from the company slug, filtered again here — the same rule as the
    order receipt. The tenant's free-text NAME never reaches a
    Content-Disposition header.
    """
    company = getattr(sales_note.order, 'company', None)
    slug = _safe_slug(getattr(company, 'slug', ''))
    number = _safe_slug(sales_note.number) or 'nota'
    return f'{slug}-nota-venta-{number}.pdf' if slug else f'nota-venta-{number}.pdf'


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def build_sales_note_context(sales_note: SalesNote) -> dict:
    """
    Plain-data dict for PDF rendering.

    Deliberately excludes every Stripe identifier and payment_error — the PDF is
    handed to customers and staff and must not leak payment internals.
    """
    order = sales_note.order

    items = [
        {
            'name': item.product.name if item.product else '—',
            'quantity': item.quantity,
            'unit_price': Decimal(str(item.price)),
            'subtotal': Decimal(str(item.price)) * item.quantity,
        }
        for item in order.items.select_related('product').all()
    ]

    address_parts = [p for p in (order.address_line, order.district, order.city) if p]

    # The seller on this note is the ORDER's company, frozen at sale time
    # (Phase 3), and `number` comes from that company's own series (Phase 2E).
    # Neither WHO issued this note nor WHAT it is numbered is a module constant.
    #
    # The stored string is read back verbatim, never re-derived from the series:
    # a prefix changed after issuance must not retroactively rewrite a document
    # somebody is already holding.
    identity = _company_settings.order_identity(order)
    pickup = _company_settings.order_pickup_location(order)

    return {
        'title': _TITLE,
        'disclaimer': SALES_NOTE_DISCLAIMER,
        'number': sales_note.number,
        'issued_at': (
            sales_note.issued_at.strftime('%d/%m/%Y %H:%M') if sales_note.issued_at else '—'
        ),
        'order_id': order.pk,
        'customer_name': order.customer_name or '—',
        'customer_phone': order.customer_phone or '—',
        'customer_email': order.customer_email or '—',
        'document_label': _DOCUMENT_LABELS.get(order.document_type, '—'),
        'document_number': order.document_number or '—',
        'receipt_label': _RECEIPT_LABELS.get(order.receipt_type, '—'),
        'delivery_label': _DELIVERY_LABELS.get(order.delivery_method, '—'),
        'full_address': ', '.join(address_parts),
        'notes': order.notes or '',
        'items': items,
        'discount_amount': Decimal(str(order.discount_amount)),
        'total': Decimal(str(order.total)),
        'store_name': identity.name,
        'store_legal_name': identity.legal_name,
        'store_ruc': identity.tax_id,
        'store_address': identity.legal_address,
        'store_city': identity.city,
        'store_phone': identity.phone,
        'store_email': identity.contact_email,
        'warranty_note': identity.warranty_policy_text or _GENERIC_WARRANTY_NOTE,
        'pickup_name': pickup.get('name', ''),
        'pickup_address': pickup.get('address', ''),
    }


def generate_sales_note_pdf(sales_note: SalesNote) -> bytes:
    """
    Build the internal sales-note PDF in memory. No file is written to disk.

    Raises SalesNoteError if the underlying order is no longer paid.
    """
    order = sales_note.order
    if not order.paid or order.status != Order.Status.PAID:
        raise SalesNoteError(
            f'No se puede generar el PDF de la nota {sales_note.number}: la orden no está pagada.'
        )

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    ctx = build_sales_note_context(sales_note)
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=(
            f"{ctx['number']} — {ctx['store_name']}"
            if ctx['store_name'] else ctx['number']
        ),
        author=ctx['store_name'] or '',
    )

    base = getSampleStyleSheet()
    _h1 = ParagraphStyle('NH1', parent=base['Heading1'], fontSize=15, spaceAfter=3,
                         leading=19, textColor=colors.HexColor('#111827'))
    _h2 = ParagraphStyle('NH2', parent=base['Heading2'], fontSize=10, spaceAfter=3,
                         leading=13, textColor=colors.HexColor('#374151'))
    _body = ParagraphStyle('NBody', parent=base['Normal'], fontSize=9, leading=13,
                           textColor=colors.HexColor('#374151'))
    _small = ParagraphStyle('NSmall', parent=base['Normal'], fontSize=8, leading=11,
                            textColor=colors.HexColor('#6b7280'))

    story = []

    # --- Store header ---
    hdr = Table(
        [[
            Paragraph(f"<b>{ctx['store_name']}</b>", _h1),
            Paragraph(
                f"{ctx['store_legal_name']}<br/>"
                f"RUC {ctx['store_ruc']}<br/>"
                f"{ctx['store_address']}<br/>"
                f"WhatsApp: {ctx['store_phone']}",
                _body,
            ),
        ]],
        colWidths=[9 * cm, 8 * cm],
    )
    hdr.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    story.append(hdr)
    story.append(HRFlowable(width='100%', thickness=1,
                            color=colors.HexColor('#e5e7eb'), spaceAfter=8))

    # --- Title + internal number ---
    story.append(Paragraph(f"{ctx['title']} — {ctx['number']}", _h1))
    story.append(Paragraph(
        'Número interno del sistema. No es una serie fiscal.', _small,
    ))
    story.append(Spacer(1, 6))

    # --- Disclaimer (must stay visible) ---
    disc = Table(
        [[Paragraph(
            f"<b>{ctx['disclaimer']}</b>",
            ParagraphStyle('NDisc', parent=base['Normal'], fontSize=9, leading=13,
                           textColor=colors.HexColor('#b91c1c')),
        )]],
        colWidths=[17 * cm],
    )
    disc.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fef2f2')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#fca5a5')),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(disc)
    story.append(Spacer(1, 12))

    _info_style = TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#374151')),
    ])

    # --- Document / customer info ---
    story.append(Paragraph('Datos de la venta', _h2))
    info = Table(
        [
            ['Número interno:', ctx['number']],
            ['Fecha de emisión:', ctx['issued_at']],
            ['Pedido:', f"#{ctx['order_id']}"],
            ['Comprobante solicitado:', ctx['receipt_label']],
        ],
        colWidths=[5 * cm, 12 * cm],
    )
    info.setStyle(_info_style)
    story.append(info)
    story.append(Spacer(1, 10))

    story.append(Paragraph('Cliente', _h2))
    cust_rows = [
        ['Nombre:', ctx['customer_name']],
        ['Teléfono:', ctx['customer_phone']],
        ['Documento:', f"{ctx['document_label']} {ctx['document_number']}"],
        ['Entrega:', ctx['delivery_label']],
    ]
    if ctx['full_address']:
        cust_rows.append(['Dirección:', ctx['full_address']])
    cust = Table(cust_rows, colWidths=[5 * cm, 12 * cm])
    cust.setStyle(_info_style)
    story.append(cust)
    story.append(Spacer(1, 12))

    # --- Items ---
    story.append(Paragraph('Productos', _h2))
    rows = [['Producto', 'Cant.', 'P. unitario', 'Subtotal']]
    for it in ctx['items']:
        rows.append([
            Paragraph(str(it['name']), _body),
            str(it['quantity']),
            f"S/ {it['unit_price']:.2f}",
            f"S/ {it['subtotal']:.2f}",
        ])

    items_tbl = Table(rows, colWidths=[9 * cm, 2 * cm, 3 * cm, 3 * cm], repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f3f4f6')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#e5e7eb')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#374151')),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 8))

    # --- Totals ---
    total_rows = []
    if ctx['discount_amount'] > 0:
        total_rows.append(['Descuento:', f"- S/ {ctx['discount_amount']:.2f}"])
    total_rows.append(['TOTAL:', f"S/ {ctx['total']:.2f}"])

    totals = Table(total_rows, colWidths=[14 * cm, 3 * cm])
    totals.setStyle(TableStyle([
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#111827')),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(totals)

    if ctx['notes']:
        story.append(Spacer(1, 10))
        story.append(Paragraph('Notas', _h2))
        story.append(Paragraph(str(ctx['notes']), _body))

    # --- Footer ---
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width='100%', thickness=0.5,
                            color=colors.HexColor('#e5e7eb'), spaceAfter=6))
    story.append(Paragraph(
        f"{ctx['store_name']} · {ctx['store_legal_name']} · RUC {ctx['store_ruc']} · "
        f"{ctx['store_address']}, {ctx['store_city']} · WhatsApp: {ctx['store_phone']}<br/>"
        f"{ctx['disclaimer']}",
        _small,
    ))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    SalesNote.objects.filter(pk=sales_note.pk).update(pdf_generated_at=timezone.now())

    return pdf_bytes
