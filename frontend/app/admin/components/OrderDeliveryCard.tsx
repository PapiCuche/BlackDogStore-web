"use client";

// DELIVERY-01 — «Entrega»: how an order reaches its buyer, as a record of its
// own beside the order.
//
// Everything shown is the server's, and so is whether THIS person may write it
// (`can_edit`) and which modes the order can move between (`allowed_modes`).
// Saving writes the record and nothing else: not the order's delivery method,
// not its total — the freight is information — and not its dispatch state.

import { useEffect, useState } from "react";
import { formatAdminDate } from "../../lib/admin";
import {
  DELIVERY_MODE_LABELS, DeliveryError, FREIGHT_PAID_BY_LABELS, fetchCarriers, safeTrackingUrl, updateOrderDelivery,
  type Carrier, type OrderDelivery, type OrderDeliveryChanges,
} from "../../lib/delivery";
import { internalButtonClass, internalInputClass } from "./internal-ui";

type Props = {
  orderId: number;
  /** Null for whoever may not see orders. `exists: false` is an order with no record yet. */
  delivery: OrderDelivery | null | undefined;
  /** Given the record the server answered with after a save. Never faked here. */
  onSaved: (delivery: OrderDelivery) => void;
};

type Draft = {
  mode: string; carrier: string; agency_name: string; agency_locality: string;
  recipient_name: string; recipient_phone: string; recipient_document_type: string; recipient_document_number: string;
  freight_amount: string; freight_paid_by: string; waybill_number: string; tracking_code: string; tracking_url: string;
  shipped_at: string;
};

/** An ISO instant as the value of a `datetime-local` field, in the browser's own time. */
function localValue(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function draftOf(d: OrderDelivery): Draft {
  return {
    mode: d.mode, carrier: d.carrier === null ? "" : String(d.carrier), agency_name: d.agency_name,
    agency_locality: d.agency_locality, recipient_name: d.recipient_name, recipient_phone: d.recipient_phone,
    recipient_document_type: d.recipient_document_type, recipient_document_number: d.recipient_document_number,
    freight_amount: d.freight_amount ?? "", freight_paid_by: d.freight_paid_by, waybill_number: d.waybill_number,
    tracking_code: d.tracking_code, tracking_url: d.tracking_url, shipped_at: localValue(d.shipped_at),
  };
}

/** Only what changed travels: a field that is not sent is not touched. */
function changesOf(draft: Draft, before: Draft): OrderDeliveryChanges {
  const changes: Record<string, unknown> = {};
  for (const key of Object.keys(draft) as (keyof Draft)[]) {
    const value = draft[key].trim();
    if (value === before[key].trim()) continue;
    if (key === "carrier") changes.carrier = value ? Number(value) : null;
    else if (key === "freight_amount") changes.freight_amount = value ? value.replace(",", ".") : null;
    else if (key === "shipped_at") changes.shipped_at = value ? new Date(value).toISOString() : null;
    else changes[key] = value;
  }
  // A document is its type AND its number.
  if ("recipient_document_type" in changes || "recipient_document_number" in changes) {
    changes.recipient_document_type = draft.recipient_document_type;
    changes.recipient_document_number = draft.recipient_document_type ? draft.recipient_document_number.trim() : "";
  }
  return changes as OrderDeliveryChanges;
}

export function OrderDeliveryCard({ orderId, delivery, onSaved }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [carriers, setCarriers] = useState<Carrier[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const canEdit = Boolean(delivery?.can_edit);

  // The company's carriers, asked for only by whoever is about to choose one.
  useEffect(() => {
    if (!editing) return;
    let cancelled = false;
    void (async () => {
      try {
        const list = await fetchCarriers();
        if (!cancelled) setCarriers(list.results);
      } catch {
        // Without the list the carrier simply cannot be changed; the rest still can.
      }
    })();
    return () => { cancelled = true; };
  }, [editing]);

  if (!delivery) return null;
  // A sale at the till has no delivery: nothing to show and nothing to write.
  if (!delivery.exists && !canEdit) return null;

  function open() {
    setDraft(draftOf(delivery!));
    setEditing(true);
    setSaved(false);
    setError(null);
    setFieldErrors({});
  }

  function set(field: keyof Draft, value: string) {
    setDraft((current) => (current ? { ...current, [field]: value } : current));
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft) return;
    const changes = changesOf(draft, draftOf(delivery!));
    setError(null);
    setFieldErrors({});
    if (Object.keys(changes).length === 0 && delivery!.exists) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      onSaved(await updateOrderDelivery(orderId, changes));
      setEditing(false);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar la entrega.");
      if (err instanceof DeliveryError) setFieldErrors(err.fields);
    } finally {
      setSaving(false);
    }
  }

  const link = safeTrackingUrl(delivery.tracking_url);
  const modes = delivery.allowed_modes ?? [];
  // The carriers a person can choose: the active ones, plus the one already on
  // the record even if it was switched off since.
  const choosable = carriers.filter((c) => c.is_active || c.id === delivery.carrier);

  function text(name: keyof Draft, label: string, extra: React.InputHTMLAttributes<HTMLInputElement> = {}) {
    const id = `delivery-${name}`;
    return (
      <div>
        <label htmlFor={id} className="mb-1.5 block text-xs text-muted">{label}</label>
        <input
          id={id} value={draft?.[name] ?? ""} onChange={(e) => set(name, e.target.value)}
          aria-invalid={Boolean(fieldErrors[name])} className={internalInputClass} {...extra}
        />
        {fieldErrors[name] ? <p role="alert" className="mt-1 text-xs text-danger">{fieldErrors[name]}</p> : null}
      </div>
    );
  }

  function row(label: string, value: React.ReactNode) {
    return value ? (
      <div>
        <dt className="mb-0.5 text-xs text-muted">{label}</dt>
        <dd className="break-words text-foreground">{value}</dd>
      </div>
    ) : null;
  }

  return (
    <section data-testid="order-delivery-card" aria-labelledby="order-delivery-title" className="rounded-xl border border-bd-border bg-surface p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="order-delivery-title" className="text-sm font-semibold text-foreground">Entrega</h2>
        {canEdit && !editing ? (
          <button type="button" onClick={open} className={internalButtonClass}>
            {delivery.exists ? "Editar entrega" : "Registrar entrega"}
          </button>
        ) : null}
      </div>

      {!editing ? (
        delivery.exists ? (
          <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            {row("Modalidad", delivery.mode_label || DELIVERY_MODE_LABELS[delivery.mode])}
            {row("Recibe", [delivery.recipient_name, delivery.recipient_phone].filter(Boolean).join(" · "))}
            {row("Documento de quien recibe", delivery.recipient_document_number
              ? `${delivery.recipient_document_type.toUpperCase()} ${delivery.recipient_document_number}` : "")}
            {row("Dirección", [delivery.address_line, delivery.district, delivery.city, delivery.department].filter(Boolean).join(", "))}
            {row("Transportista", delivery.carrier_name)}
            {row("Agencia", [delivery.agency_name, delivery.agency_locality].filter(Boolean).join(" · "))}
            {row("Flete (informativo)", delivery.freight_amount !== null
              ? `S/ ${delivery.freight_amount}${delivery.freight_paid_by ? ` · paga: ${FREIGHT_PAID_BY_LABELS[delivery.freight_paid_by] ?? delivery.freight_paid_by}` : ""}`
              : "")}
            {row("Guía", delivery.waybill_number)}
            {row("Clave de recojo", delivery.tracking_code)}
            {row("Seguimiento", link
              ? <a href={link} target="_blank" rel="noopener noreferrer" className="font-semibold underline underline-offset-4">Abrir seguimiento</a>
              : "")}
            {row("Enviado el", delivery.shipped_at ? formatAdminDate(delivery.shipped_at) : "")}
            {row("Última edición", delivery.updated_by
              ? `${delivery.updated_by}${delivery.updated_at ? ` · ${formatAdminDate(delivery.updated_at)}` : ""}` : "")}
          </dl>
        ) : (
          <p className="mt-3 text-sm text-muted">Este pedido todavía no tiene un registro de entrega.</p>
        )
      ) : draft ? (
        <form onSubmit={save} noValidate aria-label="Entrega del pedido" className="mt-4 space-y-3">
          <p className="text-xs text-muted">
            Esto no cambia el total del pedido ni su estado de despacho: el flete es un dato informativo.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="delivery-mode" className="mb-1.5 block text-xs text-muted">Modalidad</label>
              <select
                id="delivery-mode" value={draft.mode} onChange={(e) => set("mode", e.target.value)}
                disabled={modes.length <= 1 && Boolean(draft.mode)}
                aria-invalid={Boolean(fieldErrors.mode)} className={internalInputClass}
              >
                {!draft.mode ? <option value="">Sin definir</option> : null}
                {modes.map((mode) => <option key={mode} value={mode}>{DELIVERY_MODE_LABELS[mode] ?? mode}</option>)}
              </select>
              {fieldErrors.mode ? <p role="alert" className="mt-1 text-xs text-danger">{fieldErrors.mode}</p> : null}
            </div>
            <div>
              <label htmlFor="delivery-carrier" className="mb-1.5 block text-xs text-muted">Transportista</label>
              <select
                id="delivery-carrier" value={draft.carrier} onChange={(e) => set("carrier", e.target.value)}
                aria-invalid={Boolean(fieldErrors.carrier)} className={internalInputClass}
              >
                <option value="">Sin transportista</option>
                {choosable.map((carrier) => (
                  <option key={carrier.id} value={carrier.id}>{carrier.name} — {carrier.kind_label}</option>
                ))}
              </select>
              {fieldErrors.carrier ? <p role="alert" className="mt-1 text-xs text-danger">{fieldErrors.carrier}</p> : null}
            </div>
            {text("agency_name", "Sede de la agencia", { maxLength: 120 })}
            {text("agency_locality", "Localidad de la agencia", { maxLength: 120 })}
            {text("recipient_name", "Quién recibe", { maxLength: 255 })}
            {text("recipient_phone", "Teléfono de quien recibe", { inputMode: "tel", maxLength: 30 })}
            <div>
              <label htmlFor="delivery-recipient_document_type" className="mb-1.5 block text-xs text-muted">Documento de quien recibe</label>
              <select
                id="delivery-recipient_document_type" value={draft.recipient_document_type}
                onChange={(e) => set("recipient_document_type", e.target.value)} className={internalInputClass}
              >
                <option value="">Sin documento</option>
                <option value="dni">DNI</option>
                <option value="ruc">RUC</option>
                <option value="ce">Carnet de Extranjería</option>
              </select>
              {fieldErrors.recipient_document_type
                ? <p role="alert" className="mt-1 text-xs text-danger">{fieldErrors.recipient_document_type}</p> : null}
            </div>
            {draft.recipient_document_type ? text("recipient_document_number", "Número de documento", { maxLength: 20 }) : <div />}
            {text("freight_amount", "Flete (S/, informativo)", { inputMode: "decimal", placeholder: "Sin flete" })}
            <div>
              <label htmlFor="delivery-freight_paid_by" className="mb-1.5 block text-xs text-muted">Quién paga el flete</label>
              <select
                id="delivery-freight_paid_by" value={draft.freight_paid_by}
                onChange={(e) => set("freight_paid_by", e.target.value)} className={internalInputClass}
              >
                <option value="">Sin indicar</option>
                {Object.entries(FREIGHT_PAID_BY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </div>
            {text("waybill_number", "Número de guía", { maxLength: 60 })}
            {text("tracking_code", "Clave de recojo o código", { maxLength: 80 })}
            <div className="sm:col-span-2">
              {text("tracking_url", "Dirección de seguimiento (opcional)", { inputMode: "url", maxLength: 500, placeholder: "https://…" })}
              <p className="mt-1 text-[11px] text-muted">
                Si la dejas vacía y el transportista tiene seguimiento, se arma sola con el número de guía. El cliente la ve en su pedido.
              </p>
            </div>
            {text("shipped_at", "Enviado el", { type: "datetime-local" })}
          </div>

          {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
          <div className="flex flex-wrap gap-2">
            <button type="submit" disabled={saving} className={internalButtonClass}>
              {saving ? "Guardando…" : "Guardar entrega"}
            </button>
            <button type="button" disabled={saving} onClick={() => setEditing(false)} className={internalButtonClass}>
              Cancelar
            </button>
          </div>
        </form>
      ) : null}

      {saved && !editing ? <p role="status" className="mt-3 text-xs text-muted">Entrega guardada.</p> : null}
    </section>
  );
}
