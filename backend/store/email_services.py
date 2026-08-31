"""
Transactional email service — order confirmation and internal new-sale alert.

Phase 4.1: order confirmation to customer + internal new-sale notification.
Phase 3: TENANT-AWARE. Every name, address, tax id and phone in a message comes
from the ORDER's own company, through store.company_settings. There are no store
constants in this module any more, and a test scans the file to keep it that way.

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

from . import company_settings as _company_settings
from . import pdf_services as _pdf_services  # module ref allows test patches

logger = logging.getLogger(__name__)

# NEUTRAL FALLBACK for a company that has not written a warranty policy.
#
# It states that terms exist without inventing them. The rejected alternative
# was the previous literal — "la garantía se aplicará según la condición del
# producto" — which is one business's policy presented to every tenant's
# customers as if it were theirs.
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

    # PHASE 3 — the seller's identity comes from the ORDER, not from this module.
    #
    # `order_identity()` prefers the snapshot frozen when the sale happened, so a
    # confirmation resent next year says what it said the day it was sent. See
    # store/company_settings.py.
    identity = _company_settings.order_identity(order)
    pickup = _company_settings.order_pickup_location(order)

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
        # Seller identity — per tenant, never a module constant.
        "store_name": identity.name,
        "store_address": identity.legal_address,
        "store_city": identity.city,
        "store_phone": identity.phone,
        "store_whatsapp_link": identity.whatsapp_link,
        "store_whatsapp_number": identity.whatsapp_number,
        "store_ruc": identity.tax_id,
        "store_legal_name": identity.legal_name,
        "store_email": identity.contact_email,
        "warranty_note": identity.warranty_policy_text or _GENERIC_WARRANTY_NOTE,
        "warranty_url": identity.warranty_policy_url,
        # Where the customer collects. Distinct from the legal address: one is
        # who invoices, the other is which door to knock on.
        "pickup_name": pickup.get("name", ""),
        "pickup_address": pickup.get("address", ""),
        "pickup_is_branch": pickup.get("source") == "branch",
    }


# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------

def _build_customer_text(ctx: dict) -> str:
    lines = [
        f"Hola {ctx['customer_name']},",
        "",
        f"Tu pago ha sido confirmado. Gracias por tu compra en {ctx['store_name']}."
        if ctx["store_name"] else "Tu pago ha sido confirmado. Gracias por tu compra.",
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
    elif ctx["pickup_address"] or ctx["pickup_name"]:
        pickup = " — ".join(p for p in (ctx["pickup_name"], ctx["pickup_address"]) if p)
        lines.append(f"Punto de retiro: {pickup}")
    lines.append("")
    if ctx["notes"]:
        lines.append(f"Tus notas: {ctx['notes']}")
        lines.append("")
    lines.append("Nuestro equipo se comunicará contigo para coordinar la entrega.")
    if ctx["store_phone"]:
        lines.append(f"WhatsApp: {ctx['store_phone']}")
    lines.append("")
    lines.append(ctx["warranty_note"])
    if ctx["warranty_url"]:
        lines.append(ctx["warranty_url"])
    lines.append("")
    lines.append("─────────────────────────────")
    # Every line of the signature is omitted when the tenant has not configured
    # it. A blank is a visible gap somebody can fix; a borrowed value is not.
    for value, prefix in (
        (ctx["store_name"], ""),
        (ctx["store_legal_name"], ""),
        (ctx["store_ruc"], "RUC "),
        (ctx["store_address"], ""),
        (ctx["store_phone"], "Tel: "),
    ):
        if value:
            lines.append(f"{prefix}{value}")
    return "\n".join(lines)


def _build_customer_html(ctx: dict) -> str:
    # ESCAPE EVERYTHING. Before Phase 3 the store values were module constants
    # and were interpolated raw; now they are TENANT INPUT, typed into a settings
    # form by one company and rendered inside another person's email client. An
    # unescaped company name is an HTML injection with a text field in front of
    # it, so every value below goes through _h() — no exceptions, including the
    # ones that "come from us".
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
    elif ctx["pickup_address"] or ctx["pickup_name"]:
        pickup = " — ".join(p for p in (ctx["pickup_name"], ctx["pickup_address"]) if p)
        address_html = (
            f"<p style='margin:4px 0'><strong>Punto de retiro:</strong> {_h(pickup)}</p>"
        )

    notes_html = ""
    if ctx["notes"]:
        notes_html = f"<p style='margin:12px 0;padding:12px;background:#f9fafb;border-radius:6px'><strong>Tus notas:</strong> {_h(ctx['notes'])}</p>"

    # The WhatsApp href is built by company_settings.build_whatsapp_link() from a
    # digits-only field, so it can only ever be an https://wa.me/ URL. It is
    # still escaped, and the whole block disappears when the tenant configured
    # no number rather than rendering an empty link.
    whatsapp_html = ""
    if ctx["store_whatsapp_link"]:
        whatsapp_html = (
            f"<p style=\"margin:8px 0 0;color:#166534\">WhatsApp: "
            f"<a href=\"{_h(ctx['store_whatsapp_link'])}\" style=\"color:#166534\">"
            f"{_h(ctx['store_phone'] or ctx['store_whatsapp_number'])}</a></p>"
        )
    elif ctx["store_phone"]:
        whatsapp_html = (
            f"<p style=\"margin:8px 0 0;color:#166534\">Teléfono: "
            f"{_h(ctx['store_phone'])}</p>"
        )

    # Each part is dropped when empty: an incomplete tenant shows a shorter
    # signature, never another company's details.
    signature_parts = [
        _h(part) for part in (
            ctx["store_name"], ctx["store_legal_name"],
            f"RUC {ctx['store_ruc']}" if ctx["store_ruc"] else "",
        ) if part
    ]
    contact_parts = [
        _h(part) for part in (
            ctx["store_address"],
            f"Tel: {ctx['store_phone']}" if ctx["store_phone"] else "",
        ) if part
    ]
    signature_html = " · ".join(signature_parts)
    if contact_parts:
        signature_html += "<br>" + " · ".join(contact_parts)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;color:#111827;max-width:600px;margin:0 auto;padding:24px">

<div style="background:#111827;padding:20px 24px;border-radius:8px 8px 0 0">
  <h1 style="color:#ffffff;margin:0;font-size:20px">{_h(ctx['store_name'])}</h1>
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
    {whatsapp_html}
  </div>

  <p style="color:#6b7280;font-size:13px">{_h(ctx['warranty_note'])}</p>

  <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0">
  <p style="color:#9ca3af;font-size:12px;margin:0">{signature_html}</p>
</div>

</body>
</html>"""


def _order_subject(prefix: str, order, ctx: dict) -> str:
    """
    "Confirmación de pedido #12 — Empresa A".

    The company name is appended only when there is one: a subject ending in a
    dangling em dash tells the recipient the system is broken, and inventing a
    name would tell them something worse.
    """
    base = f"{prefix} #{order.id}"
    name = (ctx.get("store_name") or "").strip()
    return f"{base} — {name}" if name else base


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
    subject = _order_subject("Confirmación de pedido", order, ctx)
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


def resend_order_confirmation_email(order) -> dict:
    """
    Manual resend of customer confirmation email from admin panel.

    Intentionally bypasses confirmation_email_sent_at idempotency guard.
    PDF attachment is best-effort — if PDF fails, email still sends.
    Updates confirmation_email_sent_at on success.
    Does NOT send internal notification.
    Does NOT modify paid, status, total, inventory, or Stripe fields.

    Returns {"had_pdf": bool}.
    Raises on SMTP failure — caller must handle.
    """
    from .models import Order  # local import to avoid circular

    if not order.paid or order.status != Order.Status.PAID:
        raise ValueError(
            f"Cannot resend email for order {order.pk}: not paid "
            f"(paid={order.paid}, status={order.status!r})"
        )

    ctx = build_order_confirmation_context(order)
    subject = _order_subject("Confirmación de pedido", order, ctx)
    text_body = _build_customer_text(ctx)
    html_body = _build_customer_html(ctx)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[order.customer_email],
    )
    msg.attach_alternative(html_body, "text/html")

    had_pdf = False
    try:
        pdf_bytes = _pdf_services.generate_order_receipt_pdf(order)
        msg.attach(_pdf_services.get_order_receipt_filename(order), pdf_bytes, "application/pdf")
        had_pdf = True
    except Exception:
        pdf_err = traceback.format_exc(limit=3)
        logger.exception(
            "PDF generation failed for order %s during manual resend; sending email without attachment",
            order.pk,
        )
        existing = Order.objects.filter(pk=order.pk).values_list("email_send_error", flat=True).first() or ""
        pdf_note = f"resend_pdf_skip: {str(pdf_err)[:150]}"
        new_error = (f"{existing}; {pdf_note}" if existing else pdf_note)[:500]
        Order.objects.filter(pk=order.pk).update(email_send_error=new_error)

    # Raises on SMTP failure — propagated to caller (view returns 502)
    msg.send()

    Order.objects.filter(pk=order.pk).update(confirmation_email_sent_at=timezone.now())
    return {"had_pdf": had_pdf}


def send_internal_order_notification(order) -> bool:
    """
    Send the new-sale alert to THIS ORDER'S OWN COMPANY.

    PHASE 3 — THE ROUTING CHANGED, AND IT IS A DELIBERATE BREAK.

    This used to read one platform-wide `settings.ORDER_NOTIFICATION_EMAIL`. On a
    multi-tenant install that is a data leak with a stamp of approval: a second
    company's sales — customer name, phone, address, what they bought — would
    have been announced in the pilot's inbox, because the pilot's address was the
    only one there was.

    The recipient now comes from `CompanySettings.order_notification_email` of
    the order's company. There is NO platform fallback. A company that has not
    configured an address gets no alert, and that is the correct failure: an
    operator noticing silence can fix it, whereas an alert already delivered to
    the wrong company cannot be recalled.

    Migration 0027 copies the existing global value into the pilot's settings, so
    this installation keeps receiving exactly what it received before.

    Returns True if sent, False if skipped or not configured.
    """
    from .models import Order  # local import to avoid circular

    notification_email = _company_settings.order_notification_recipient(order)
    if not notification_email:
        logger.info(
            "No internal notification for order %s: company %s has no "
            "order_notification_email configured",
            order.pk, getattr(order, "company_id", None),
        )
        return False
    if not order.paid or order.status != Order.Status.PAID:
        return False
    if order.internal_notification_sent_at is not None:
        return False  # idempotency guard

    ctx = build_order_confirmation_context(order)
    frontend_url = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    admin_url = f"{frontend_url}/admin/orders/{order.id}" if frontend_url else ""

    subject = _order_subject("Nueva venta pagada", order, ctx)
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
