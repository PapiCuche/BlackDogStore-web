"use client";

/**
 * Ficha de cliente — Phase 4.
 *
 * Three sections, and one deliberately absent.
 *
 *   RESUMEN            identity, contact, state.
 *   HISTORIAL COMERCIAL orders, with PAID separated from everything else.
 *   NOTAS               internal, never shown to the client.
 *
 * There is NO "Equipos" section. Devices arrive in Phase 5; an empty card
 * promising them would be a claim the product cannot keep, and a card showing
 * "0 equipos" reads as a fact rather than an absent feature.
 *
 * WHY THE HISTORY SHOWS THE ORDER'S OWN DATA
 * ------------------------------------------
 * Each row prints the name, phone and document recorded ON THAT ORDER, not the
 * customer's current details. If somebody moved house, last year's delivery
 * still went to the old address, and a history that quietly rewrote itself to
 * match today's file would be useless for the one question histories get asked:
 * what actually happened.
 */

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../../components/InternalControlGuard";
import { DashboardSection } from "../../components/dashboard-ui";
import { CustomerForm } from "../../components/CustomerForm";
import {
  fetchCustomer,
  updateCustomer,
  type CustomerDetail,
  type CustomerOrderRow,
} from "../../lib/internal-api";

function money(value: string) {
  const n = Number(value);
  return Number.isFinite(n)
    ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" })
    : value;
}

function date(value: string | null) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("es-PE");
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
        {label}
      </p>
      <p className="mt-0.5 text-sm text-foreground/85">{value || "—"}</p>
    </div>
  );
}

function OrderRow({ order }: { order: CustomerOrderRow }) {
  return (
    <tr className="border-b border-bd-border last:border-0">
      <td className="px-4 py-3">
        <Link
          href={`/admin/orders/${order.id}`}
          className="text-foreground/85 transition hover:text-foreground"
        >
          #{order.id}
        </Link>
      </td>
      <td className="px-4 py-3 text-muted">{date(order.created_at)}</td>
      <td className="px-4 py-3">
        {order.paid ? (
          <span className="text-xs text-emerald-400/80">Pagado</span>
        ) : (
          <span className="text-xs text-muted">{order.status}</span>
        )}
      </td>
      <td className="px-4 py-3 text-right font-mono text-xs text-foreground/85">
        {money(order.total)}
      </td>
      <td className="px-4 py-3 text-xs text-muted">{order.customer_name || "—"}</td>
    </tr>
  );
}

function CustomerDetailContent({
  ctx,
  customerId,
}: {
  ctx: InternalContext;
  customerId: number;
}) {
  const companyId = ctx.selectedCompanyId;
  const [customer, setCustomer] = useState<CustomerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setCustomer(await fetchCustomer(customerId, companyId));
  }, [customerId, companyId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // Inside the async body, not the effect body: setting state synchronously
      // while the effect runs schedules a second render before the first has
      // committed, which is what react-hooks/set-state-in-effect warns about.
      setLoading(true);
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar el cliente.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  async function toggleActive() {
    if (!customer) return;
    setBusy(true);
    try {
      setCustomer(
        await updateCustomer(customer.id, companyId, { is_active: !customer.is_active }),
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el estado.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <p className="py-8 text-sm text-muted">Cargando ficha…</p>
      </AdminShell>
    );
  }
  if (error || !customer) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-5 py-4 text-sm text-red-400">
          {error ?? "Cliente no encontrado."}
        </div>
        <Link
          href="/admin/customers"
          className="mt-4 inline-block text-sm text-muted underline underline-offset-2"
        >
          Volver a clientes
        </Link>
      </AdminShell>
    );
  }

  const paidOrders = customer.orders.filter((o) => o.paid);
  const otherOrders = customer.orders.filter((o) => !o.paid);

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-8">
        <div>
          <Link
            href="/admin/customers"
            className="text-xs text-muted transition hover:text-foreground/85"
          >
            ← Clientes
          </Link>
          <div className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
            <h1 className="font-display text-xl font-semibold text-foreground">
              {customer.display_name}
            </h1>
            {!customer.is_active ? (
              <span className="rounded border border-bd-border px-2 py-0.5 text-xs text-muted">
                Archivado
              </span>
            ) : null}
          </div>
        </div>

        <DashboardSection
          title="Resumen"
          action={
            customer.can_manage && !editing ? (
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="rounded-lg border border-bd-border px-3 py-1.5 text-sm text-foreground transition hover:border-bd-border hover:text-foreground"
                >
                  Editar
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void toggleActive()}
                  className="rounded-lg border border-bd-border px-3 py-1.5 text-sm text-muted transition hover:border-bd-border hover:text-foreground disabled:opacity-40"
                >
                  {customer.is_active ? "Archivar" : "Reactivar"}
                </button>
              </div>
            ) : null
          }
        >
          {editing ? (
            <div className="rounded-xl border border-bd-border bg-surface p-5">
              <CustomerForm
                companyId={companyId}
                customer={customer}
                onCancel={() => setEditing(false)}
                onSaved={() => {
                  setEditing(false);
                  void load();
                }}
              />
            </div>
          ) : (
            <div className="grid gap-5 rounded-xl border border-bd-border bg-surface p-5 sm:grid-cols-2 lg:grid-cols-3">
              <Field
                label="Tipo"
                value={customer.customer_type === "business" ? "Empresa" : "Persona"}
              />
              <Field
                label="Documento"
                value={
                  customer.document_number
                    ? `${customer.document_type_label}: ${customer.document_number}`
                    : ""
                }
              />
              <Field
                label="Cuenta"
                value={customer.has_account ? "Tiene cuenta en la plataforma" : "Sin cuenta"}
              />
              <Field label="Teléfono" value={customer.phone} />
              <Field label="Email" value={customer.email} />
              <Field
                label="Dirección"
                value={[customer.address_line, customer.district, customer.city]
                  .filter(Boolean)
                  .join(", ")}
              />
            </div>
          )}
        </DashboardSection>

        <DashboardSection
          title="Historial comercial"
          description="Pedidos del e-commerce vinculados a esta ficha."
        >
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="rounded-xl border border-bd-border bg-surface p-4">
              <p className="text-[11px] uppercase tracking-widest text-muted">
                Compras pagadas
              </p>
              <p className="mt-1 text-lg text-foreground">{customer.summary.paid_orders}</p>
            </div>
            <div className="rounded-xl border border-bd-border bg-surface p-4">
              <p className="text-[11px] uppercase tracking-widest text-muted">
                Total pagado
              </p>
              <p className="mt-1 text-lg text-foreground">
                {money(customer.summary.paid_amount)}
              </p>
            </div>
            <div className="rounded-xl border border-bd-border bg-surface p-4">
              <p className="text-[11px] uppercase tracking-widest text-muted">
                Última compra
              </p>
              <p className="mt-1 text-lg text-foreground">
                {date(customer.summary.last_purchase_at)}
              </p>
            </div>
          </div>

          {customer.orders.length === 0 ? (
            <p className="rounded-xl border border-bd-border bg-surface px-5 py-8 text-center text-sm text-muted">
              Esta ficha todavía no tiene pedidos vinculados.
            </p>
          ) : (
            <div className="space-y-4">
              {paidOrders.length ? (
                <div className="overflow-x-auto rounded-xl border border-bd-border">
                  <table className="w-full min-w-[38rem] text-left text-sm">
                    <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
                      <tr>
                        <th className="px-4 py-3 font-semibold">Pedido</th>
                        <th className="px-4 py-3 font-semibold">Fecha</th>
                        <th className="px-4 py-3 font-semibold">Estado</th>
                        <th className="px-4 py-3 text-right font-semibold">Total</th>
                        <th className="px-4 py-3 font-semibold">Comprador (histórico)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paidOrders.map((o) => (
                        <OrderRow key={o.id} order={o} />
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {otherOrders.length ? (
                <div>
                  <p className="mb-2 text-[11px] uppercase tracking-widest text-muted">
                    Pedidos no pagados — no cuentan como ventas
                  </p>
                  <div className="overflow-x-auto rounded-xl border border-bd-border">
                    <table className="w-full min-w-[38rem] text-left text-sm">
                      <tbody>
                        {otherOrders.map((o) => (
                          <OrderRow key={o.id} order={o} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}

              {customer.orders_truncated ? (
                <p className="text-xs text-muted">
                  Mostrando los pedidos más recientes de un total de{" "}
                  {customer.summary.orders_total}.
                </p>
              ) : null}
            </div>
          )}
        </DashboardSection>

        <DashboardSection
          title="Notas internas"
          description="Sólo para el equipo. El cliente nunca las ve."
        >
          <div className="rounded-xl border border-bd-border bg-surface p-5">
            {customer.notes ? (
              <p className="whitespace-pre-wrap text-sm text-foreground/85">{customer.notes}</p>
            ) : (
              <p className="text-sm text-muted">Sin notas.</p>
            )}
          </div>
        </DashboardSection>
      </div>
    </AdminShell>
  );
}

export default function CustomerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <InternalControlGuard>
      {(ctx) => <CustomerDetailContent ctx={ctx} customerId={Number(id)} />}
    </InternalControlGuard>
  );
}
