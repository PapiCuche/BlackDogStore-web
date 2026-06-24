"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { StaffGuard } from "../../components/StaffGuard";
import { AdminShell } from "../../components/AdminShell";
import { OrderStatusBadge } from "../../components/OrderStatusBadge";
import { FulfillmentStatusBadge } from "../../components/FulfillmentStatusBadge";
import { FulfillmentStatusSelect } from "../../components/FulfillmentStatusSelect";
import {
  AdminOrderDetail,
  fetchAdminOrderDetail,
  formatAdminDate,
  downloadOrderReceiptPdf,
  resendOrderConfirmationEmail,
} from "../../../lib/admin";
import {
  DOCUMENT_TYPE_LABELS,
  DELIVERY_METHOD_LABELS,
  RECEIPT_TYPE_LABELS,
} from "../../../lib/business";
import type { AuthUser } from "../../../lib/auth";

function OrderDetailContent({ user }: { user: AuthUser }) {
  const { id } = useParams<{ id: string }>();
  const orderId = parseInt(id, 10);

  const [order, setOrder] = useState<AdminOrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pdfDownloading, setPdfDownloading] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [resendLoading, setResendLoading] = useState(false);
  const [resendSuccess, setResendSuccess] = useState<string | null>(null);
  const [resendError, setResendError] = useState<string | null>(null);

  async function handleDownloadPdf() {
    setPdfDownloading(true);
    setPdfError(null);
    try {
      const { blob, filename } = await downloadOrderReceiptPdf(orderId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setPdfError(err instanceof Error ? err.message : "Error al descargar el PDF.");
    } finally {
      setPdfDownloading(false);
    }
  }

  async function handleResendEmail() {
    if (!window.confirm(`¿Reenviar el email de confirmación a ${order?.customer_email}?`)) return;
    setResendLoading(true);
    setResendSuccess(null);
    setResendError(null);
    try {
      const result = await resendOrderConfirmationEmail(orderId);
      setResendSuccess(
        `Email reenviado a ${result.resent_to}${result.had_pdf_attachment ? " (con PDF adjunto)" : " (sin PDF)"}`,
      );
    } catch (err) {
      setResendError(err instanceof Error ? err.message : "Error al reenviar el email.");
    } finally {
      setResendLoading(false);
    }
  }

  const canResendEmail =
    user.role === "admin" || user.role === "superadmin";

  const canManageFulfillment =
    user.role === "sales" ||
    user.role === "inventory" ||
    user.role === "admin" ||
    user.role === "superadmin";

  useEffect(() => {
    fetchAdminOrderDetail(orderId)
      .then(setOrder)
      .catch((err) => setError(err instanceof Error ? err.message : "Error al cargar la orden."))
      .finally(() => setLoading(false));
  }, [orderId]);

  if (loading) {
    return (
      <AdminShell user={user}>
        <p className="text-zinc-500 text-sm">Cargando…</p>
      </AdminShell>
    );
  }

  if (error || !order) {
    return (
      <AdminShell user={user}>
        <p className="text-red-400 text-sm">{error ?? "Orden no encontrada."}</p>
        <Link href="/admin/orders" className="text-zinc-400 hover:text-zinc-100 text-sm mt-4 block">
          ← Volver a órdenes
        </Link>
      </AdminShell>
    );
  }

  const subtotal = parseFloat(order.total) + parseFloat(order.discount_amount);

  return (
    <AdminShell user={user}>
      <div className="space-y-8 max-w-3xl">
        <div>
          <Link
            href="/admin/orders"
            className="text-xs text-zinc-500 hover:text-zinc-300 mb-2 block"
          >
            ← Volver a órdenes
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold text-white">Orden #{order.id}</h1>
            <OrderStatusBadge status={order.status} />
            <FulfillmentStatusBadge status={order.fulfillment_status} />
          </div>
          <p className="mt-1 text-xs text-zinc-500">{formatAdminDate(order.created_at)}</p>
          {order.paid && (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                onClick={handleDownloadPdf}
                disabled={pdfDownloading}
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.10] bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-white/[0.08] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {pdfDownloading ? "Generando…" : "Descargar PDF"}
              </button>
              {pdfError && (
                <p className="text-red-400 text-xs">{pdfError}</p>
              )}
              {canResendEmail && (
                <>
                  <button
                    onClick={handleResendEmail}
                    disabled={resendLoading}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.10] bg-white/[0.04] px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-white/[0.08] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {resendLoading ? "Enviando…" : "Reenviar email de confirmación"}
                  </button>
                  {resendSuccess && (
                    <p className="text-green-400 text-xs">{resendSuccess}</p>
                  )}
                  {resendError && (
                    <p className="text-red-400 text-xs">{resendError}</p>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {/* Summary cards */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 text-sm">
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
            <p className="text-xs text-zinc-500 mb-1">Total</p>
            <p className="text-base font-semibold text-white">S/ {parseFloat(order.total).toFixed(2)}</p>
          </div>
          {parseFloat(order.discount_amount) > 0 && (
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
              <p className="text-xs text-zinc-500 mb-1">Descuento</p>
              <p className="text-base font-semibold text-zinc-300">
                −S/ {parseFloat(order.discount_amount).toFixed(2)}
                {order.coupon_code && (
                  <span className="ml-1.5 text-[10px] text-zinc-500">({order.coupon_code})</span>
                )}
              </p>
            </div>
          )}
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
            <p className="text-xs text-zinc-500 mb-1">Subtotal</p>
            <p className="text-base font-semibold text-zinc-400">S/ {subtotal.toFixed(2)}</p>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
            <p className="text-xs text-zinc-500 mb-1">Ítems</p>
            <p className="text-base font-semibold text-zinc-200">{order.items.length}</p>
          </div>
        </div>

        {/* Customer info */}
        <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
          <h2 className="text-sm font-semibold text-white mb-4">Cliente</h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <div>
              <dt className="text-zinc-500 text-xs mb-0.5">Nombre</dt>
              <dd className="text-zinc-200">{order.customer_name || "—"}</dd>
            </div>
            <div>
              <dt className="text-zinc-500 text-xs mb-0.5">Email</dt>
              <dd className="text-zinc-200">{order.customer_email}</dd>
            </div>
            {order.username && (
              <div>
                <dt className="text-zinc-500 text-xs mb-0.5">Usuario</dt>
                <dd className="text-zinc-200">{order.username}</dd>
              </div>
            )}
            {order.paid_at && (
              <div>
                <dt className="text-zinc-500 text-xs mb-0.5">Pagado el</dt>
                <dd className="text-zinc-200">{formatAdminDate(order.paid_at)}</dd>
              </div>
            )}
          </dl>
        </section>

        {/* Delivery and receipt data */}
        <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
          <h2 className="text-sm font-semibold text-white mb-4">Entrega y comprobante</h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            {order.customer_phone && (
              <div>
                <dt className="text-zinc-500 text-xs mb-0.5">Teléfono</dt>
                <dd className="text-zinc-200">{order.customer_phone}</dd>
              </div>
            )}
            {order.document_type && (
              <div>
                <dt className="text-zinc-500 text-xs mb-0.5">Documento</dt>
                <dd className="text-zinc-200">
                  {DOCUMENT_TYPE_LABELS[order.document_type] ?? order.document_type}
                  {order.document_number && ` — ${order.document_number}`}
                </dd>
              </div>
            )}
            {order.receipt_type && (
              <div>
                <dt className="text-zinc-500 text-xs mb-0.5">Comprobante</dt>
                <dd className="text-zinc-200">
                  {RECEIPT_TYPE_LABELS[order.receipt_type] ?? order.receipt_type}
                </dd>
              </div>
            )}
            {order.delivery_method && (
              <div>
                <dt className="text-zinc-500 text-xs mb-0.5">Método de entrega</dt>
                <dd className="text-zinc-200">
                  {DELIVERY_METHOD_LABELS[order.delivery_method] ?? order.delivery_method}
                </dd>
              </div>
            )}
            {order.address_line && (
              <div className="col-span-2">
                <dt className="text-zinc-500 text-xs mb-0.5">Dirección</dt>
                <dd className="text-zinc-200">{order.address_line}</dd>
              </div>
            )}
            {order.city && (
              <div>
                <dt className="text-zinc-500 text-xs mb-0.5">Ciudad</dt>
                <dd className="text-zinc-200">{order.city}</dd>
              </div>
            )}
            {order.district && (
              <div>
                <dt className="text-zinc-500 text-xs mb-0.5">Distrito</dt>
                <dd className="text-zinc-200">{order.district}</dd>
              </div>
            )}
            {order.reference && (
              <div className="col-span-2">
                <dt className="text-zinc-500 text-xs mb-0.5">Referencia</dt>
                <dd className="text-zinc-200">{order.reference}</dd>
              </div>
            )}
            {order.notes && (
              <div className="col-span-2">
                <dt className="text-zinc-500 text-xs mb-0.5">Notas del cliente</dt>
                <dd className="text-zinc-200 whitespace-pre-wrap">{order.notes}</dd>
              </div>
            )}
            <div>
              <dt className="text-zinc-500 text-xs mb-0.5">Términos aceptados</dt>
              <dd className={order.accepted_terms ? "text-zinc-200" : "text-red-400"}>
                {order.accepted_terms ? "Sí" : "No"}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500 text-xs mb-0.5">Garantía aceptada</dt>
              <dd className={order.accepted_warranty_policy ? "text-zinc-200" : "text-red-400"}>
                {order.accepted_warranty_policy ? "Sí" : "No"}
              </dd>
            </div>
          </dl>
        </section>

        {/* Items */}
        <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
          <h2 className="text-sm font-semibold text-white mb-4">Productos</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.08] text-zinc-400">
                <th className="text-left pb-2 pr-4 font-medium">Producto</th>
                <th className="text-right pb-2 pr-4 font-medium">Precio unit.</th>
                <th className="text-right pb-2 pr-4 font-medium">Cant.</th>
                <th className="text-right pb-2 font-medium">Subtotal</th>
              </tr>
            </thead>
            <tbody>
              {order.items.map((item) => (
                <tr key={item.id} className="border-b border-white/[0.04]">
                  <td className="py-2.5 pr-4 text-zinc-200">{item.product_name}</td>
                  <td className="py-2.5 pr-4 text-right text-zinc-400">
                    S/ {parseFloat(item.price).toFixed(2)}
                  </td>
                  <td className="py-2.5 pr-4 text-right text-zinc-400">{item.quantity}</td>
                  <td className="py-2.5 text-right text-zinc-200">
                    S/ {parseFloat(item.subtotal).toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {/* Email status */}
        <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
          <h2 className="text-sm font-semibold text-white mb-4">Emails transaccionales</h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <div>
              <dt className="text-zinc-500 text-xs mb-0.5">Confirmación al cliente</dt>
              <dd className={order.confirmation_email_sent_at ? "text-zinc-200" : "text-zinc-500"}>
                {order.confirmation_email_sent_at
                  ? formatAdminDate(order.confirmation_email_sent_at)
                  : "Pendiente"}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500 text-xs mb-0.5">Notificación interna</dt>
              <dd className={order.internal_notification_sent_at ? "text-zinc-200" : "text-zinc-500"}>
                {order.internal_notification_sent_at
                  ? formatAdminDate(order.internal_notification_sent_at)
                  : "Pendiente / No configurado"}
              </dd>
            </div>
            {order.email_send_error && (
              <div className="col-span-2">
                <dt className="text-zinc-500 text-xs mb-0.5">Error de envío</dt>
                <dd className="text-red-400 text-xs font-mono whitespace-pre-wrap">{order.email_send_error}</dd>
              </div>
            )}
          </dl>
        </section>

        {/* Fulfillment management */}
        {canManageFulfillment && (
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
            <h2 className="text-sm font-semibold text-white mb-1">Estado de despacho</h2>
            <p className="text-xs text-zinc-500 mb-4">
              Cambia el estado operativo de la orden. El estado de pago no se puede modificar desde aquí.
            </p>
            <FulfillmentStatusSelect
              orderId={order.id}
              current={order.fulfillment_status}
              currentUser={user}
              onChanged={(newStatus) =>
                setOrder((prev) => prev ? { ...prev, fulfillment_status: newStatus } : prev)
              }
            />
          </section>
        )}
      </div>
    </AdminShell>
  );
}

export default function AdminOrderDetailPage() {
  return (
    <StaffGuard>{(user) => <OrderDetailContent user={user} />}</StaffGuard>
  );
}
