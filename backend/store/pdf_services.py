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

#: The unit of measure of an internal document. Everything this catalogue sells
#: is counted in units; the fiscal documents carry SUNAT's own code instead.
UNIT_LABEL = 'UND'


def delivery_label(method: str, city: str = '') -> str:
    """
    How the delivery method is written on a document.

    THE LOCAL DELIVERY NAMES THE TENANT'S CITY. The choice is stored under a
    key that carries the first shop's city; printed as-is it told every other
    tenant's customers their order was being delivered there.
    """
    if method == 'delivery_arequipa':
        city = (city or '').strip()
        return f'Delivery {city}' if city else 'Delivery local'
    return _DELIVERY_LABELS.get(method, method)


def sold_units(order) -> dict:
    """
    The serialized devices that left the stock IN THIS SALE: product id -> units.

    Read from the sale's own Kardex movements (which record the ids of the
    units they took) and from the units still assigned to the order. The
    movement is what makes a REPRINT honest: a device returned later is
    detached from the order, and the paper issued for that sale must still name
    the device it named.

    Never from a product's description or from anything typed into the sale.
    """
    from .models import StockMovement, StockUnit

    ids = set()
    for metadata in StockMovement.objects.filter(
        order=order, movement_type=StockMovement.SALE_EXIT,
    ).values_list('metadata', flat=True):
        for unit_id in (metadata or {}).get('unit_ids') or []:
            if isinstance(unit_id, int):
                ids.add(unit_id)
    ids.update(StockUnit.objects.filter(order=order).values_list('pk', flat=True))
    if not ids:
        return {}

    by_product: dict[int, list[dict]] = {}
    # Inside the order's company: an id in a JSON field is never a pass to
    # another tenant's device.
    for unit in StockUnit.objects.filter(
        company_id=order.company_id, pk__in=ids,
    ).order_by('received_at', 'pk'):
        by_product.setdefault(unit.product_id, []).append({
            'serial_number': unit.serial_number or '',
            'imei': unit.imei or '',
            'imei2': unit.imei2 or '',
        })
    return by_product


def order_lines(order) -> list[dict]:
    """
    The lines of a sale as a document prints them, each with what identifies it.

    `list_price` and `discount` describe the LINE. A sale stores one price per
    line and its discount for the whole order, so today the line discount is
    zero and the order's discount is shown with the totals.
    """
    from .models import ProductBarcode

    items = list(order.items.select_related('product').order_by('pk'))
    codes: dict[int, str] = {}
    for product_id, code in ProductBarcode.objects.filter(
        company_id=order.company_id, product_id__in=[item.product_id for item in items], is_active=True,
    ).order_by('is_primary', '-code').values_list('product_id', 'code'):
        codes[product_id] = code          # the primary one is written last and wins
    units = sold_units(order)

    lines = []
    for number, item in enumerate(items, start=1):
        price = Decimal(str(item.price))
        lines.append({
            'line_number': number,
            'code': codes.get(item.product_id, ''),
            'name': item.product.name if item.product else '—',
            'unit': UNIT_LABEL,
            'list_price': price,
            'discount': Decimal('0.00'),
            'unit_price': price,
            'quantity': item.quantity,
            'subtotal': price * item.quantity,
            'units': units.get(item.product_id, []),
        })
    return lines


def order_presentation(order, *, frozen_logo_key=None) -> dict:
    """
    What every document of a sale shows besides its lines: the branch that sold
    it, how it was paid, who attended, and the shop's logotype.

    `frozen_logo_key` is the logotype a document froze when it was issued
    (`''` = it was issued without one). `None` means the document has no frozen
    copy and the shop's current one is used.
    """
    from . import fiscal_logo

    pickup = _company_settings.order_pickup_location(order)
    is_branch = pickup.get('source') == 'branch'
    if frozen_logo_key is None:
        logo = fiscal_logo.current_png(order.company) if order.company_id else None
    else:
        logo = fiscal_logo.load_key(frozen_logo_key)
    return {
        'logo_png': logo,
        'branch_name': pickup.get('name', '') if is_branch else '',
        'branch_address': pickup.get('address', '') if is_branch else '',
        'branch_phone': pickup.get('phone', '') if is_branch else '',
        'payment_label': order.get_payment_method_display() if order.payment_method else '',
        'channel_label': order.get_sales_channel_display() if order.sales_channel else '',
        'seller_name': order.seller_name_snapshot or '',
    }


def build_order_pdf_context(order) -> dict:
    """
    Returns a plain-data dict for PDF rendering.
    Gateway identifiers are explicitly excluded.
    """
    lines = order_lines(order)
    items = [{
        "product_name": line['name'],
        "quantity": line['quantity'],
        "price": line['unit_price'],
        "subtotal": line['subtotal'],
        **line,
    } for line in lines]

    identity = _company_settings.order_identity(order)
    receipt_label = _RECEIPT_LABELS.get(order.receipt_type, order.receipt_type)
    document_label = _DOCUMENT_LABELS.get(order.document_type, order.document_type)
    delivery_label_text = delivery_label(order.delivery_method, identity.city)
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

    pickup = _company_settings.order_pickup_location(order)

    return {
        **order_presentation(order),
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
        "delivery_label": delivery_label_text,
        "full_address": full_address,
        "reference": order.reference or "",
        "notes": order.notes or "",
        # Items
        "items": items,
        # Totals
        "total": Decimal(str(order.total)),
        "discount_amount": Decimal(str(order.discount_amount)),
        "coupon_code": order.coupon_code or "",
        "currency": order.currency or "PEN",
        "product_count": len(lines),
        "unit_count": sum(line['quantity'] for line in lines),
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

    Laid out by `document_layout.render_a4`, the page every internal document
    of a shop shares.
    """
    from .models import Order  # local import to avoid circular

    if not order.paid or order.status != Order.Status.PAID:
        raise ValueError(
            f"Cannot generate PDF for order {order.pk}: not paid "
            f"(paid={order.paid}, status={order.status!r})"
        )

    from . import document_layout, document_style as style
    from .tax_services import currency_symbol

    ctx = build_order_pdf_context(order)
    symbol = currency_symbol(ctx["currency"])
    subtotal = sum((item["subtotal"] for item in ctx["items"]), Decimal("0.00"))

    totals = []
    if ctx["discount_amount"] > 0:
        totals.append(("Subtotal", style.money(symbol, subtotal)))
        label = f"Descuento ({ctx['coupon_code']})" if ctx["coupon_code"] else "Descuento"
        totals.append((label, f"- {style.money(symbol, ctx['discount_amount'])}"))

    delivery = [("Entrega", ctx["delivery_label"])]
    if ctx["full_address"]:
        delivery.append(("Dirección", ctx["full_address"]))
        delivery.append(("Referencia", ctx["reference"]))
    elif ctx["pickup_address"]:
        delivery.append(("Retiro en", ctx["pickup_address"]))

    # The title says what the customer ASKED for; it is not that document.
    title, _, requested = ctx["title"].partition(" — ")
    return document_layout.render_a4({
        "title": title.upper(),
        "number": f"Pedido #{ctx['order_id']}",
        "number_note": requested,
        # PDF metadata carries the tenant's name too — it is what a reader sees
        # in their viewer's title bar and in the document properties.
        "pdf_title": (
            f"Pedido #{order.id} — {ctx['store_name']}" if ctx["store_name"] else f"Pedido #{order.id}"
        ),
        "author": ctx["store_name"] or "",
        "logo_png": ctx["logo_png"],
        "identity": {
            "name": ctx["store_name"], "legal_name": ctx["store_legal_name"], "tax_id": ctx["store_ruc"],
            "address": ctx["store_address"], "city": ctx["store_city"],
            "branch": ctx["branch_name"], "branch_address": ctx["branch_address"],
            "phone": ctx["store_phone"], "email": ctx["store_email"],
        },
        "parties": [
            ("Cliente", [
                ("Nombre", ctx["customer_name"]),
                ("Documento", f"{ctx['document_label']} {ctx['document_number']}".strip()),
                ("Teléfono", ctx["customer_phone"]),
                ("Correo", ctx["customer_email"]),
            ]),
            ("Datos del pedido", [
                ("Pedido", f"#{ctx['order_id']}"),
                ("Creado", ctx["created_at"]),
                ("Pagado", ctx["paid_at"]),
                ("Tipo de pago", ctx["payment_label"]),
                ("Estado", f"{ctx['status_label']} · {ctx['fulfillment_label']}"),
                *delivery,
                ("Comprobante solicitado", ctx["receipt_label"]),
            ]),
        ],
        "lines": [{
            "number": item["line_number"], "code": item["code"], "description": item["name"],
            "units": item["units"], "unit": item["unit"], "list_price": item["list_price"],
            "discount": item["discount"], "unit_price": item["unit_price"],
            "quantity": item["quantity"], "amount": item["subtotal"],
        } for item in ctx["items"]],
        "summary": [f"Productos: {ctx['product_count']}   ·   Unidades: {ctx['unit_count']}"],
        "amount_in_words": style.amount_in_words(ctx["total"], ctx["currency"]),
        "totals": totals,
        "total": style.money(symbol, ctx["total"]),
        "legal": ctx["disclaimer"],
        "notes": ctx["notes"],
        "footnotes": [ctx["warranty_note"]] if ctx["warranty_note"] else [],
        "footer": "  ·  ".join(part for part in (
            ctx["store_name"], f"RUC {ctx['store_ruc']}" if ctx["store_ruc"] else "",
            f"Pedido #{ctx['order_id']}",
        ) if part),
    })
