"""
Transactional email service for Black Dog Store.

Phase 4.1: order confirmation to customer + internal new-sale notification.

Idempotency: confirmation_email_sent_at / internal_notification_sent_at flags
prevent duplicate sends if the Stripe webhook fires more than once.

Security rules (hard):
- Never include stripe_session_id, stripe_payment_intent_id, or payment_error.
- Never include raw tokens or cookie values.
- Only send when order.paid is True AND order.status is PAID.
- Only send once per order (flag check before send, flag set after success).
"""

from __future__ import annotations

import html as _html
import logging
import traceback
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from . import pdf_services as _pdf_services  # module ref allows test patches

logger = logging.getLogger(__name__)

_STORE_NAME = "Black Dog Store"
_STORE_ADDRESS = "Octavio Muñoz Najar 238, Tienda 104"
_STORE_PHONE = "+51 936 449 536"
_STORE_WHATSAPP_LINK = "https://wa.me/51936449536"
_STORE_RUC = "20610159886"
_STORE_LEGAL_NAME = "CMAU CORP E.I.R.L."

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


def _h(value: str) -> str:
    """HTML-escape user-supplied data for safe embedding in email HTML."""
    return _html.escape(str(value), quote=True)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_order_confirmation_context(order) -> dict:
    """
    Returns a plain-data dict (no model references) safe for use in email templates.
    Stripe fields are explicitly excluded.
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

    delivery_label = _DELIVERY_LABELS.get(order.delivery_method, order.delivery_method)
    document_label = _DOCUMENT_LABELS.get(order.document_type, order.document_type)
    receipt_label = _RECEIPT_LABELS.get(order.receipt_type, order.receipt_type)

    address_parts = []
    if order.address_line:
        address_parts.append(order.address_line)
    if order.district:
        address_parts.append(order.district)
    if order.city:
        address_parts.append(order.city)
    full_address = ", ".join(address_parts) if address_parts else ""

    return {
        "order_id": order.id,
        "customer_name": order.customer_name or "Cliente",
        "customer_email": order.customer_email,
        "customer_phone": order.customer_phone,
        "document_label": document_label,
        "document_number": order.document_number,
        "receipt_label": receipt_label,
        "delivery_label": delivery_label,
        "full_address": full_address,
        "reference": order.reference,
        "notes": order.notes,
        "items": items,
        # Cast explicitly — DecimalField may arrive as str if object was not re-fetched from DB.
        "total": Decimal(str(order.total)),
        "discount_amount": Decimal(str(order.discount_amount)),
        "coupon_code": order.coupon_code,
        "paid_at": order.paid_at,
        # Store constants
        "store_name": _STORE_NAME,
        "store_address": _STORE_ADDRESS,
        "store_phone": _STORE_PHONE,
        "store_whatsapp_link": _STORE_WHATSAPP_LINK,
        "store_ruc": _STORE_RUC,
        "store_legal_name": _STORE_LEGAL_NAME,
    }


# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------

def _build_customer_text(ctx: dict) -> str:
    lines = [
        f"Hola {ctx['customer_name']},",
        "",
        "Tu pago ha sido confirmado. Gracias por tu compra en Black Dog Store.",
        "",
        f"Número de pedido: #{ctx['order_id']}",
        "",
        "─────────────────────────────",
        "PRODUCTOS",
        "─────────────────────────────",
    ]
    for item in ctx["items"]:
        lines.append(
            f"  {item['product_name']}  x{item['quantity']}  "
            f"S/ {item['price']:.2f}  →  S/ {item['subtotal']:.2f}"
        )
    lines.append("─────────────────────────────")
    if ctx["discount_amount"] and ctx["discount_amount"] > Decimal("0"):
        lines.append(f"Descuento ({ctx['coupon_code']}): -S/ {ctx['discount_amount']:.2f}")
    lines.append(f"TOTAL: S/ {ctx['total']:.2f}")
    lines.append("")
    lines.append(f"Comprobante: {ctx['receipt_label']}")
    lines.append(f"Documento: {ctx['document_label']} {ctx['document_number']}")
    lines.append("")
    lines.append(f"Método de entrega: {ctx['delivery_label']}")
    if ctx["full_address"]:
        lines.append(f"Dirección: {ctx['full_address']}")
        if ctx["reference"]:
            lines.append(f"Referencia: {ctx['reference']}")
    else:
        lines.append(f"Punto de retiro: {ctx['store_address']}")
    lines.append("")
    if ctx["notes"]:
        lines.append(f"Tus notas: {ctx['notes']}")
        lines.append("")
    lines.extend([
        "Nuestro equipo se comunicará contigo para coordinar la entrega.",
        f"WhatsApp: {ctx['store_phone']}",
        "",
        "La garantía se aplicará según la condición del producto y los términos "
        "informados al momento de la compra.",
        "",
        "─────────────────────────────",
        f"{ctx['store_name']}",
        f"{ctx['store_legal_name']}",
        f"RUC {ctx['store_ruc']}",
        f"{ctx['store_address']}",
        f"Tel: {ctx['store_phone']}",
    ])
    return "\n".join(lines)


def _build_customer_html(ctx: dict) -> str:
    # Escape all user-supplied fields before embedding in HTML.
    # Our own store constants (_STORE_*) are static and safe without escaping.
    items_rows = ""
    for item in ctx["items"]:
        items_rows += (
            f"<tr>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e5e7eb'>{_h(item['product_name'])}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e5e7eb;text-align:center'>{item['quantity']}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e5e7eb;text-align:right'>S/ {item['price']:.2f}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e5e7eb;text-align:right'>S/ {item['subtotal']:.2f}</td>"
            f"</tr>"
        )

    discount_row = ""
    if ctx["discount_amount"] and ctx["discount_amount"] > Decimal("0"):
        discount_row = (
            f"<tr><td colspan='3' style='padding:6px 12px;text-align:right;color:#6b7280'>Descuento ({_h(ctx['coupon_code'])})</td>"
            f"<td style='padding:6px 12px;text-align:right;color:#6b7280'>-S/ {ctx['discount_amount']:.2f}</td></tr>"
        )

    address_html = ""
    if ctx["full_address"]:
        address_html = f"<p style='margin:4px 0'><strong>Dirección:</strong> {_h(ctx['full_address'])}</p>"
        if ctx["reference"]:
            address_html += f"<p style='margin:4px 0'><strong>Referencia:</strong> {_h(ctx['reference'])}</p>"
    else:
        address_html = (
            f"<p style='margin:4px 0'><strong>Punto de retiro:</strong> {ctx['store_address']}</p>"
        )

    notes_html = ""
    if ctx["notes"]:
        notes_html = f"<p style='margin:12px 0;padding:12px;background:#f9fafb;border-radius:6px'><strong>Tus notas:</strong> {_h(ctx['notes'])}</p>"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;color:#111827;max-width:600px;margin:0 auto;padding:24px">

<div style="background:#111827;padding:20px 24px;border-radius:8px 8px 0 0">
  <h1 style="color:#ffffff;margin:0;font-size:20px">{ctx['store_name']}</h1>
</div>

<div style="border:1px solid #e5e7eb;border-top:none;padding:24px;border-radius:0 0 8px 8px">
  <h2 style="margin:0 0 8px">Pedido #{ctx['order_id']} confirmado</h2>
  <p style="color:#6b7280;margin:0 0 24px">Hola <strong>{_h(ctx['customer_name'])}</strong>, tu pago ha sido recibido.</p>

  <table style="width:100%;border-collapse:collapse;margin-bottom:16px">
    <thead>
      <tr style="background:#f9fafb">
        <th style="padding:8px 12px;text-align:left;font-size:13px;color:#6b7280">Producto</th>
        <th style="padding:8px 12px;text-align:center;font-size:13px;color:#6b7280">Cant.</th>
        <th style="padding:8px 12px;text-align:right;font-size:13px;color:#6b7280">Precio unit.</th>
        <th style="padding:8px 12px;text-align:right;font-size:13px;color:#6b7280">Subtotal</th>
      </tr>
    </thead>
    <tbody>{items_rows}</tbody>
    <tfoot>
      {discount_row}
      <tr style="background:#f9fafb">
        <td colspan="3" style="padding:8px 12px;text-align:right;font-weight:bold">Total</td>
        <td style="padding:8px 12px;text-align:right;font-weight:bold">S/ {ctx['total']:.2f}</td>
      </tr>
    </tfoot>
  </table>

  <div style="display:grid;gap:8px;margin-bottom:20px">
    <p style="margin:4px 0"><strong>Comprobante:</strong> {ctx['receipt_label']}</p>
    <p style="margin:4px 0"><strong>Documento:</strong> {ctx['document_label']} {_h(ctx['document_number'])}</p>
    <p style="margin:4px 0"><strong>Teléfono:</strong> {_h(ctx['customer_phone'])}</p>
  </div>

  <div style="border-top:1px solid #e5e7eb;padding-top:16px;margin-bottom:20px">
    <p style="margin:0 0 8px;font-weight:bold">Entrega</p>
    <p style="margin:4px 0"><strong>Método:</strong> {ctx['delivery_label']}</p>
    {address_html}
  </div>

  {notes_html}

  <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:16px;margin-bottom:20px">
    <p style="margin:0 0 4px;font-weight:bold;color:#166534">¿Qué sigue?</p>
    <p style="margin:0;color:#166534">Nuestro equipo se comunicará contigo para coordinar la entrega.</p>
    <p style="margin:8px 0 0;color:#166534">WhatsApp: <a href="{ctx['store_whatsapp_link']}" style="color:#166534">{ctx['store_phone']}</a></p>
  </div>

  <p style="color:#6b7280;font-size:13px">La garantía se aplicará según la condición del producto y los términos informados al momento de la compra.</p>

  <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0">
  <p style="color:#9ca3af;font-size:12px;margin:0">{ctx['store_name']} · {ctx['store_legal_name']} · RUC {ctx['store_ruc']}<br>
  {ctx['store_address']} · Tel: {ctx['store_phone']}</p>
</div>

</body>
</html>"""


def _build_internal_text(ctx: dict, order_id: int, admin_url: str) -> str:
    lines = [
        f"Nueva venta pagada — Pedido #{order_id}",
        "=" * 40,
        f"Cliente:    {ctx['customer_name']}",
        f"Email:      {ctx['customer_email']}",
        f"Teléfono:   {ctx['customer_phone']}",
        f"Documento:  {ctx['document_label']} {ctx['document_number']}",
        f"Comprobante:{ctx['receipt_label']}",
        f"Entrega:    {ctx['delivery_label']}",
    ]
    if ctx["full_address"]:
        lines.append(f"Dirección:  {ctx['full_address']}")
        if ctx["reference"]:
            lines.append(f"Referencia: {ctx['reference']}")
    lines.append("")
    lines.append("PRODUCTOS:")
    for item in ctx["items"]:
        lines.append(f"  {item['product_name']}  x{item['quantity']}  S/ {item['price']:.2f}")
    lines.append("")
    if ctx["discount_amount"] and ctx["discount_amount"] > Decimal("0"):
        lines.append(f"Descuento ({ctx['coupon_code']}): -S/ {ctx['discount_amount']:.2f}")
    lines.append(f"TOTAL: S/ {ctx['total']:.2f}")
    if ctx["notes"]:
        lines.append(f"\nNotas del cliente:\n{ctx['notes']}")
    if admin_url:
        lines.append(f"\nPanel admin: {admin_url}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------

def send_order_confirmation_email(order) -> bool:
    """
    Sends confirmation email to the customer with a PDF receipt attached.
    Returns True if email was sent (PDF attachment is best-effort).
    Does nothing if order is not paid or email already sent.
    """
    from .models import Order  # local import to avoid circular
    if not order.paid or order.status != Order.Status.PAID:
        return False
    if order.confirmation_email_sent_at is not None:
        return False  # idempotency guard

    ctx = build_order_confirmation_context(order)
    subject = f"Confirmación de pedido #{order.id} — {_STORE_NAME}"
    text_body = _build_customer_text(ctx)
    html_body = _build_customer_html(ctx)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[order.customer_email],
    )
    msg.attach_alternative(html_body, "text/html")

    # Attach PDF receipt (best-effort: email is still sent if PDF generation fails)
    try:
        pdf_bytes = _pdf_services.generate_order_receipt_pdf(order)
        msg.attach(_pdf_services.get_order_receipt_filename(order), pdf_bytes, "application/pdf")
    except Exception:
        pdf_err = traceback.format_exc(limit=3)
        logger.exception("PDF generation failed for order %s; sending email without attachment", order.pk)
        # Record in email_send_error so staff can see PDF was skipped
        existing = Order.objects.filter(pk=order.pk).values_list("email_send_error", flat=True).first() or ""
        pdf_note = f"pdf_skip: {str(pdf_err)[:200]}"
        new_error = (f"{existing}; {pdf_note}" if existing else pdf_note)[:500]
        Order.objects.filter(pk=order.pk).update(email_send_error=new_error)

    msg.send()
    return True


def send_internal_order_notification(order) -> bool:
    """
    Sends new-sale notification to ORDER_NOTIFICATION_EMAIL.
    Returns True if sent, False if skipped or not configured.
    """
    from .models import Order  # local import to avoid circular
    notification_email = getattr(settings, "ORDER_NOTIFICATION_EMAIL", "").strip()
    if not notification_email:
        return False
    if not order.paid or order.status != Order.Status.PAID:
        return False
    if order.internal_notification_sent_at is not None:
        return False  # idempotency guard

    ctx = build_order_confirmation_context(order)
    frontend_url = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    admin_url = f"{frontend_url}/admin/orders/{order.id}" if frontend_url else ""

    subject = f"Nueva venta pagada #{order.id} — {_STORE_NAME}"
    text_body = _build_internal_text(ctx, order.id, admin_url)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[notification_email],
    )
    msg.send()
    return True


def send_order_emails_after_payment(order_pk: int) -> None:
    """
    Entry point called via transaction.on_commit() after payment is confirmed.

    Sends both the customer confirmation and the internal notification.
    Failures are logged and stored in email_send_error — they never raise
    to avoid breaking the already-confirmed payment.
    """
    from .models import Order  # local import to avoid circular

    try:
        order = Order.objects.prefetch_related("items__product").get(pk=order_pk)
    except Order.DoesNotExist:
        logger.error("send_order_emails_after_payment: Order pk=%s not found.", order_pk)
        return

    errors = []

    # --- Customer confirmation ---
    if order.confirmation_email_sent_at is None:
        try:
            sent = send_order_confirmation_email(order)
            if sent:
                order.confirmation_email_sent_at = timezone.now()
                order.save(update_fields=["confirmation_email_sent_at"])
        except Exception:
            err = traceback.format_exc(limit=3)
            logger.exception("Failed to send order confirmation email for order %s", order_pk)
            errors.append(f"confirmation: {str(err)[:300]}")

    # --- Internal notification ---
    if order.internal_notification_sent_at is None:
        try:
            sent = send_internal_order_notification(order)
            if sent:
                order.internal_notification_sent_at = timezone.now()
                order.save(update_fields=["internal_notification_sent_at"])
        except Exception:
            err = traceback.format_exc(limit=3)
            logger.exception("Failed to send internal order notification for order %s", order_pk)
            errors.append(f"internal: {str(err)[:300]}")

    if errors:
        combined = "; ".join(errors)
        # Append to existing errors so prior failed-attempt logs are preserved.
        existing = Order.objects.filter(pk=order_pk).values_list("email_send_error", flat=True).first() or ""
        new_error = (f"{existing}; {combined}" if existing else combined)[:500]
        Order.objects.filter(pk=order_pk).update(email_send_error=new_error)
