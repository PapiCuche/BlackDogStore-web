"use client";

/**
 * Create / edit a customer — Phase 4.
 *
 * The form changes shape with `customer_type`, because a person and a business
 * are identified by different things. Showing "Razón social" next to "Apellido"
 * and letting the user work out which applies is how records end up with the
 * company name typed into the surname field.
 *
 * Errors come back from the backend and are rendered under the field they
 * belong to. The one case worth special handling is the 409: a document that
 * already identifies somebody here is not a validation failure the user should
 * fix by editing the number — it means the record they are trying to create
 * already exists, so the form offers to open it.
 */

import Link from "next/link";
import { useState } from "react";
import {
  createCustomer,
  updateCustomer,
  type CustomerDetail,
  type CustomerRow,
  type CustomerType,
  type CustomerWrite,
} from "../lib/internal-api";

const FIELD =
  "w-full rounded-lg border bg-background/40 px-3 py-2 text-sm text-foreground outline-none transition focus:border-bd-border disabled:opacity-50";
const BORDER = "border-bd-border";
const LABEL =
  "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted";

const DOCUMENT_TYPES = [
  { value: "", label: "Sin documento" },
  { value: "dni", label: "DNI" },
  { value: "ruc", label: "RUC" },
  { value: "ce", label: "Carnet de Extranjería" },
];

type Draft = Required<Omit<CustomerWrite, "is_active">>;

function draftFrom(customer: CustomerDetail | null): Draft {
  return {
    customer_type: customer?.customer_type ?? "person",
    first_name: customer?.first_name ?? "",
    last_name: customer?.last_name ?? "",
    business_name: customer?.business_name ?? "",
    document_type: customer?.document_type ?? "",
    document_number: customer?.document_number ?? "",
    phone: customer?.phone ?? "",
    email: customer?.email ?? "",
    address_line: customer?.address_line ?? "",
    district: customer?.district ?? "",
    city: customer?.city ?? "",
    notes: customer?.notes ?? "",
  };
}

export function CustomerForm({
  companyId,
  customer,
  onSaved,
  onCancel,
}: {
  companyId: number | null;
  /** null = create. */
  customer: CustomerDetail | null;
  onSaved: (saved: CustomerDetail, duplicates: CustomerRow[]) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(customer));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<CustomerRow | null>(null);
  const [saving, setSaving] = useState(false);

  const isBusiness = draft.customer_type === "business";

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  async function save() {
    setSaving(true);
    setErrors({});
    setError(null);
    setConflict(null);
    try {
      // The fields of the shape NOT chosen are sent empty rather than omitted,
      // so switching a record from business to person actually clears the
      // business name instead of leaving it behind, invisible, in the database.
      const payload: CustomerWrite = {
        ...draft,
        business_name: isBusiness ? draft.business_name : "",
        first_name: isBusiness ? "" : draft.first_name,
        last_name: isBusiness ? "" : draft.last_name,
      };
      const saved = customer
        ? await updateCustomer(customer.id, companyId, payload)
        : await createCustomer(companyId, payload);
      const duplicates =
        (saved as CustomerDetail & { possible_duplicates?: CustomerRow[] })
          .possible_duplicates ?? [];
      onSaved(saved, duplicates);
    } catch (err) {
      const fields = (err as { fields?: Record<string, string> }).fields;
      const existing = (err as { conflict?: CustomerRow }).conflict;
      if (existing) setConflict(existing);
      if (fields) setErrors(fields);
      if (!fields && !existing) {
        setError(err instanceof Error ? err.message : "No se pudo guardar.");
      } else if (existing) {
        setError(err instanceof Error ? err.message : "Ya existe ese documento.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <fieldset>
        <legend className={LABEL}>Tipo de cliente</legend>
        <div className="flex flex-wrap gap-4">
          {(
            [
              ["person", "Persona"],
              ["business", "Empresa"],
            ] as [CustomerType, string][]
          ).map(([value, label]) => (
            <label key={value} className="flex items-center gap-2 text-sm text-foreground/85">
              <input
                type="radio"
                name="customer-type"
                checked={draft.customer_type === value}
                disabled={saving}
                onChange={() => set("customer_type", value)}
              />
              {label}
            </label>
          ))}
        </div>
      </fieldset>

      {isBusiness ? (
        <div>
          <label className={LABEL} htmlFor="cf-business">
            Razón social
          </label>
          <input
            id="cf-business"
            className={`${FIELD} ${errors.business_name ? "border-danger-border" : BORDER}`}
            value={draft.business_name}
            maxLength={200}
            disabled={saving}
            onChange={(e) => set("business_name", e.target.value)}
          />
          {errors.business_name ? (
            <p className="mt-1.5 text-xs text-danger">{errors.business_name}</p>
          ) : null}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className={LABEL} htmlFor="cf-first">
              Nombre
            </label>
            <input
              id="cf-first"
              className={`${FIELD} ${errors.first_name ? "border-danger-border" : BORDER}`}
              value={draft.first_name}
              maxLength={120}
              disabled={saving}
              onChange={(e) => set("first_name", e.target.value)}
            />
            {errors.first_name ? (
              <p className="mt-1.5 text-xs text-danger">{errors.first_name}</p>
            ) : null}
          </div>
          <div>
            <label className={LABEL} htmlFor="cf-last">
              Apellido
            </label>
            <input
              id="cf-last"
              className={`${FIELD} ${BORDER}`}
              value={draft.last_name}
              maxLength={120}
              disabled={saving}
              onChange={(e) => set("last_name", e.target.value)}
            />
          </div>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={LABEL} htmlFor="cf-doctype">
            Tipo de documento
          </label>
          <select
            id="cf-doctype"
            className={`${FIELD} ${errors.document_type ? "border-danger-border" : BORDER}`}
            value={draft.document_type}
            disabled={saving}
            onChange={(e) => set("document_type", e.target.value)}
          >
            {DOCUMENT_TYPES.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
          {errors.document_type ? (
            <p className="mt-1.5 text-xs text-danger">{errors.document_type}</p>
          ) : (
            <p className="mt-1.5 text-[11px] text-muted">
              Opcional. Un cliente puede atenderse sin documento.
            </p>
          )}
        </div>
        <div>
          <label className={LABEL} htmlFor="cf-docnumber">
            Número de documento
          </label>
          <input
            id="cf-docnumber"
            className={`${FIELD} ${errors.document_number ? "border-danger-border" : BORDER} font-mono`}
            value={draft.document_number}
            maxLength={20}
            disabled={saving}
            onChange={(e) => set("document_number", e.target.value)}
          />
          {errors.document_number ? (
            <p className="mt-1.5 text-xs text-danger">{errors.document_number}</p>
          ) : null}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={LABEL} htmlFor="cf-phone">
            Teléfono
          </label>
          <input
            id="cf-phone"
            className={`${FIELD} ${errors.phone ? "border-danger-border" : BORDER}`}
            value={draft.phone}
            maxLength={30}
            disabled={saving}
            onChange={(e) => set("phone", e.target.value)}
          />
          {errors.phone ? (
            <p className="mt-1.5 text-xs text-danger">{errors.phone}</p>
          ) : null}
        </div>
        <div>
          <label className={LABEL} htmlFor="cf-email">
            Email
          </label>
          <input
            id="cf-email"
            type="email"
            className={`${FIELD} ${errors.email ? "border-danger-border" : BORDER}`}
            value={draft.email}
            disabled={saving}
            onChange={(e) => set("email", e.target.value)}
          />
          {errors.email ? (
            <p className="mt-1.5 text-xs text-danger">{errors.email}</p>
          ) : null}
        </div>
      </div>

      <div>
        <label className={LABEL} htmlFor="cf-address">
          Dirección
        </label>
        <input
          id="cf-address"
          className={`${FIELD} ${BORDER}`}
          value={draft.address_line}
          maxLength={300}
          disabled={saving}
          onChange={(e) => set("address_line", e.target.value)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={LABEL} htmlFor="cf-district">
            Distrito
          </label>
          <input
            id="cf-district"
            className={`${FIELD} ${BORDER}`}
            value={draft.district}
            maxLength={100}
            disabled={saving}
            onChange={(e) => set("district", e.target.value)}
          />
        </div>
        <div>
          <label className={LABEL} htmlFor="cf-city">
            Ciudad
          </label>
          <input
            id="cf-city"
            className={`${FIELD} ${BORDER}`}
            value={draft.city}
            maxLength={100}
            disabled={saving}
            onChange={(e) => set("city", e.target.value)}
          />
        </div>
      </div>

      <div>
        <label className={LABEL} htmlFor="cf-notes">
          Notas internas
        </label>
        <textarea
          id="cf-notes"
          rows={3}
          className={`${FIELD} ${errors.notes ? "border-danger-border" : BORDER}`}
          value={draft.notes}
          maxLength={2000}
          disabled={saving}
          onChange={(e) => set("notes", e.target.value)}
        />
        <p className="mt-1.5 text-[11px] text-muted">
          Sólo para el equipo. El cliente nunca las ve.
        </p>
      </div>

      {conflict ? (
        <div className="rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm">
          <p className="text-warning">{error}</p>
          <Link
            href={`/admin/customers/${conflict.id}`}
            className="mt-1.5 inline-block text-xs text-warning underline underline-offset-2"
          >
            Abrir la ficha de {conflict.display_name}
          </Link>
        </div>
      ) : error ? (
        <p className="text-sm text-danger">{error}</p>
      ) : null}

      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={saving}
          onClick={() => void save()}
          className="rounded-lg border border-bd-border px-4 py-2 text-sm font-medium text-foreground transition hover:border-bd-border hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saving ? "Guardando…" : customer ? "Guardar cambios" : "Crear cliente"}
        </button>
        <button
          type="button"
          disabled={saving}
          onClick={onCancel}
          className="text-sm text-muted transition hover:text-foreground/85"
        >
          Cancelar
        </button>
      </div>
    </div>
  );
}
