"""
PDF receipt service (Phase 4.2), tenant-aware from Phase 3.

Generates an internal purchase document (constancia de compra) for paid orders.
This is NOT an electronic receipt (comprobante electrónico SUNAT).

Security rules (hard):
- Never include any payment-gateway identifier, or payment_error.
- Never include raw tokens, cookie values, or secrets.
- Only generate PDF when order.paid is True AND order.status is PAID.
- Raises ValueError if order is not paid.

PDF is generated in memory (bytes) — no file is written to disk.

PHASE 3 — WHOSE NAME IS ON THE DOCUMENT
---------------------------------------
The seller's identity comes from the ORDER, through
`company_settings.order_identity()`, which prefers the snapshot frozen when the
sale happened. A receipt reprinted a year later therefore says what it said the
day it was issued, even if the business has since been renamed or moved.

There are no store constants in this module. A test scans the file to keep it
that way: re-introducing one would put one company's legal identity on every
tenant's paperwork.
"""

from __future__ import annotations

import io
from decimal import Decimal

from . import company_settings as _company_settings

# This disclaimer must appear visibly on every PDF.
DISCLAIMER = (
    "Documento interno de compra. "
    "No válido como comprobante electrónico SUNAT."
)

# NEUTRAL FALLBACK for a tenant that has written no warranty policy. It states
# that terms exist without inventing them — the previous literal was one
# business's actual policy, shown to every other business's customers as theirs.
_GENERIC_WARRANTY_NOTE = (
    "Consulta las condiciones de garantía con la tienda antes de la entrega."
)

_DELIVERY_LABELS = {
    "pickup_store": "Recojo en tienda",
    "delivery_arequipa": "Delivery Arequipa",
    "national_shipping": "Envío nacional",
}
_DOCUMENT_LABELS = {
    "dni": "DNI",
    "ruc": "RUC",
    "ce": "Carnet de Extranjería",
}
_RECEIPT_LABELS = {
    "boleta": "Boleta",
    "factura": "Factura",
}
_FULFILLMENT_LABELS = {
    "pending": "Pendiente",
    "confirmed": "Confirmado",
    "preparing": "En preparación",
    "ready_for_pickup": "Listo para retiro",
    "shipped": "Enviado",
    "delivered": "Entregado",
    "cancelled": "Cancelado operativo",
}


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_order_pdf_context(order) -> dict:
    """
    Returns a plain-data dict for PDF rendering.
    Gateway identifiers are explicitly excluded.
    """
    items = []
    for item in order.items.select_related("product").all():
        price = Decimal(str(item.price))
        subtotal = price * item.quantity
        items.append({
            "product_name": item.product.name,
            "quantity": item.quantity,
            "price": price,
            "subtotal": subtotal,
        })

    receipt_label = _RECEIPT_LABELS.get(order.receipt_type, order.receipt_type)
    document_label = _DOCUMENT_LABELS.get(order.document_type, order.document_type)
    delivery_label = _DELIVERY_LABELS.get(order.delivery_method, order.delivery_method)
    fulfillment_label = _FULFILLMENT_LABELS.get(order.fulfillment_status, order.fulfillment_status)

    address_parts = []
    if order.address_line:
        address_parts.append(order.address_line)
    if order.district:
        address_parts.append(order.district)
    if order.city:
        address_parts.append(order.city)
    full_address = ", ".join(address_parts)

    if order.receipt_type == "boleta":
        title = "Constancia de pedido — Boleta solicitada"
    elif order.receipt_type == "factura":
        title = "Constancia de pedido — Factura solicitada"
    else:
        title = "Constancia de pedido"

    paid_at_str = order.paid_at.strftime("%d/%m/%Y %H:%M") if order.paid_at else "—"
    created_at_str = order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else "—"

    identity = _company_settings.order_identity(order)
    pickup = _company_settings.order_pickup_location(order)

    return {
        # Document metadata
        "title": title,
        "disclaimer": DISCLAIMER,
        "warranty_note": identity.warranty_policy_text or _GENERIC_WARRANTY_NOTE,
        # Order
        "order_id": order.id,
        "created_at": created_at_str,
        "paid_at": paid_at_str,
        "status_label": "Pagado",
        "fulfillment_label": fulfillment_label,
        # Customer
        "customer_name": order.customer_name or "—",
        "customer_email": order.customer_email or "—",
        "customer_phone": order.customer_phone or "—",
        "document_label": document_label,
        "document_number": order.document_number or "—",
        # Delivery
        "delivery_label": delivery_label,
        "full_address": full_address,
        "reference": order.reference or "",
        "notes": order.notes or "",
        # Items
        "items": items,
        # Totals
        "total": Decimal(str(order.total)),
        "discount_amount": Decimal(str(order.discount_amount)),
        "coupon_code": order.coupon_code or "",
        # Receipt
        "receipt_label": receipt_label,
        # Seller identity — from the order's own company, frozen at sale time.
        "store_name": identity.name,
        "store_legal_name": identity.legal_name,
        "store_ruc": identity.tax_id,
        "store_address": identity.legal_address,
        "store_city": identity.city,
        "store_phone": identity.phone,
        "store_email": identity.contact_email,
        # The collection point, kept apart from the legal address: one is who
        # invoices, the other is which door the customer knocks on.
        "pickup_name": pickup.get("name", ""),
        "pickup_address": pickup.get("address", ""),
        "pickup_is_branch": pickup.get("source") == "branch",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_order_receipt_filename(order) -> str:
    """
    A safe ASCII filename for the attachment or download.

    Built from the company SLUG, which is already constrained to a URL-safe
    charset by SlugField, and then filtered again here. The company NAME is
    deliberately not used: it is free text a tenant types, and it would reach a
    Content-Disposition header and a filesystem — the two places where a stray
    slash or quote stops being cosmetic.

    Falls back to `pedido-{id}.pdf` when there is no usable slug.
    """
    slug = _safe_slug(getattr(getattr(order, "company", None), "slug", ""))
    return f"{slug}-pedido-{order.id}.pdf" if slug else f"pedido-{order.id}.pdf"


def _safe_slug(value: str, max_length: int = 40) -> str:
    """Lowercase ASCII letters, digits and hyphens. Everything else is dropped."""
    cleaned = "".join(
        ch for ch in str(value or "").lower()
        if ch.isascii() and (ch.isalnum() or ch == "-")
    )
    return cleaned.strip("-")[:max_length]


# ---------------------------------------------------------------------------
# PDF generator
# ---------------------------------------------------------------------------

def generate_order_receipt_pdf(order) -> bytes:
    """
    Builds and returns the PDF bytes for a paid order.
    Raises ValueError if the order is not paid or status is not PAID.
    """
    from .models import Order  # local import to avoid circular

    if not order.paid or order.status != Order.Status.PAID:
        raise ValueError(
            f"Cannot generate PDF for order {order.pk}: not paid "
            f"(paid={order.paid}, status={order.status!r})"
        )

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    ctx = build_order_pdf_context(order)
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        # PDF metadata carries the tenant's name too — it is what a reader sees
        # in their viewer's title bar and in the document properties.
        title=(
            f"Pedido #{order.id} — {ctx['store_name']}"
            if ctx["store_name"] else f"Pedido #{order.id}"
        ),
        author=ctx["store_name"] or "",
    )

    base = getSampleStyleSheet()
    _h1 = ParagraphStyle(
        "H1", parent=base["Heading1"],
        fontSize=15, spaceAfter=3, leading=19, textColor=colors.HexColor("#111827"),
    )
    _h2 = ParagraphStyle(
        "H2", parent=base["Heading2"],
        fontSize=10, spaceAfter=3, leading=13, textColor=colors.HexColor("#374151"),
    )
    _body = ParagraphStyle(
        "Body", parent=base["Normal"],
        fontSize=9, leading=13, textColor=colors.HexColor("#374151"),
    )
    _small = ParagraphStyle(
        "Small", parent=base["Normal"],
        fontSize=8, leading=11, textColor=colors.HexColor("#6b7280"),
    )

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
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(hdr)
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb"), spaceAfter=8))

    # --- Title ---
    story.append(Paragraph(ctx["title"], _h1))
    story.append(Spacer(1, 6))

    # --- Disclaimer ---
    disc_tbl = Table(
        [[Paragraph(
            f"<b>{ctx['disclaimer']}</b>",
            ParagraphStyle("Disc", parent=base["Normal"], fontSize=9, leading=13,
                           textColor=colors.HexColor("#b91c1c")),
        )]],
        colWidths=[17 * cm],
    )
    disc_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef2f2")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#fca5a5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(disc_tbl)
    story.append(Spacer(1, 12))

    # --- Order info ---
    _tbl_style = TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ])
    story.append(Paragraph("Datos del pedido", _h2))
    story.append(Table([
        ["Número de pedido:", f"#{ctx['order_id']}"],
        ["Fecha de creación:", ctx["created_at"]],
        ["Fecha de pago:", ctx["paid_at"]],
        ["Estado de pago:", ctx["status_label"]],
        ["Estado operativo:", ctx["fulfillment_label"]],
    ], colWidths=[5 * cm, 12 * cm], style=_tbl_style))
    story.append(Spacer(1, 10))

    # --- Customer ---
    story.append(Paragraph("Datos del cliente", _h2))
    story.append(Table([
        ["Nombre:", ctx["customer_name"]],
        ["Email:", ctx["customer_email"]],
        ["Teléfono:", ctx["customer_phone"]],
        [f"{ctx['document_label']}:", ctx["document_number"]],
    ], colWidths=[5 * cm, 12 * cm], style=_tbl_style))
    story.append(Spacer(1, 10))

    # --- Delivery ---
    story.append(Paragraph("Entrega", _h2))
    delivery_rows = [["Método:", ctx["delivery_label"]]]
    if ctx["full_address"]:
        delivery_rows.append(["Dirección:", ctx["full_address"]])
        if ctx["reference"]:
            delivery_rows.append(["Referencia:", ctx["reference"]])
    else:
        delivery_rows.append(["Retiro en:", ctx["store_address"]])
    if ctx["notes"]:
        delivery_rows.append(["Notas:", ctx["notes"]])
    story.append(Table(delivery_rows, colWidths=[5 * cm, 12 * cm], style=_tbl_style))
    story.append(Spacer(1, 10))

    # --- Products table ---
    story.append(Paragraph("Productos", _h2))
    prod_rows = [["Producto", "Cant.", "Precio unit.", "Subtotal"]]
    for item in ctx["items"]:
        prod_rows.append([
            item["product_name"],
            str(item["quantity"]),
            f"S/ {item['price']:.2f}",
            f"S/ {item['subtotal']:.2f}",
        ])
    n_items = len(prod_rows)

    # Discount + total rows
    if ctx["discount_amount"] and ctx["discount_amount"] > Decimal("0"):
        disc_label = f"Descuento ({ctx['coupon_code']})" if ctx["coupon_code"] else "Descuento"
        prod_rows.append(["", "", disc_label, f"-S/ {ctx['discount_amount']:.2f}"])
    prod_rows.append(["", "", "TOTAL", f"S/ {ctx['total']:.2f}"])

    prod_tbl = Table(prod_rows, colWidths=[9 * cm, 2 * cm, 3 * cm, 3 * cm])
    prod_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("GRID", (0, 0), (-1, n_items - 1), 0.25, colors.HexColor("#e5e7eb")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (2, -1), (-1, -1), 0.5, colors.HexColor("#374151")),
    ]))
    story.append(prod_tbl)
    story.append(Spacer(1, 10))

    # --- Receipt type ---
    story.append(Paragraph("Tipo de comprobante solicitado por el cliente", _h2))
    story.append(Table([
        ["Tipo:", ctx["receipt_label"]],
        [f"{ctx['document_label']}:", ctx["document_number"]],
    ], colWidths=[5 * cm, 12 * cm], style=_tbl_style))
    story.append(Spacer(1, 10))

    # --- Warranty ---
    story.append(Paragraph(ctx["warranty_note"], _small))
    story.append(Spacer(1, 16))

    # --- Footer ---
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"), spaceAfter=5))
    story.append(Paragraph(
        f"{ctx['store_name']} · {ctx['store_legal_name']} · RUC {ctx['store_ruc']} · "
        f"{ctx['store_address']} · WhatsApp: {ctx['store_phone']}<br/>"
        f"Documento generado automáticamente.",
        _small,
    ))

    doc.build(story)
    return buffer.getvalue()
