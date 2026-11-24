"use client";

/**
 * Where a picture comes from — the provenance fields of a finish picture (CATALOG-REFERENCE-01 · PR E) and, since
 * IMG-GATE-01, of a picture of a product's own gallery. One component so both ask the same thing the same way.
 */

import { useId } from "react";

import type { Provenance, SourceKind } from "../lib/catalog-presentation-api";
import { internalInputClass } from "./internal-ui";

/** What a person chooses when uploading; the server sends its own labels (`source_kinds`), these are the fallback. */
export const SOURCE_KIND_LABELS: Record<Exclude<SourceKind, "unknown">, string> = {
  own_photo: "Foto propia",
  supplier_authorized: "Autorizada por proveedor o distribuidor",
  licensed_stock: "Banco con licencia comercial",
  demo: "Recurso demostrativo",
};

export function ProvenanceFields({
  value, onChange, errors, kinds, disabled, firstFieldRef,
}: {
  value: Provenance;
  onChange: (value: Provenance) => void;
  errors: Partial<Record<keyof Provenance, string>>;
  kinds: { value: SourceKind; label: string }[];
  disabled?: boolean;
  /** The origin select, for a host that moves focus into the form when it opens. */
  firstFieldRef?: React.Ref<HTMLSelectElement>;
}) {
  const id = useId();
  const field = (name: keyof Provenance) => ({
    id: `${id}-${name}`,
    "aria-invalid": errors[name] ? true : undefined,
    "aria-describedby": errors[name] ? `${id}-${name}-error` : undefined,
  });
  const error = (name: keyof Provenance) => (errors[name] ? (
    <p id={`${id}-${name}-error`} className="mt-1 text-xs text-danger">{errors[name]}</p>
  ) : null);
  const licenseRequired = value.source_kind !== "" && value.source_kind !== "own_photo";
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div>
        <label htmlFor={`${id}-source_kind`} className="block text-xs text-muted">Origen de la imagen (obligatorio)</label>
        <select
          ref={firstFieldRef}
          {...field("source_kind")}
          value={value.source_kind}
          disabled={disabled}
          required
          onChange={(e) => onChange({ ...value, source_kind: e.target.value as SourceKind })}
          className={`mt-1.5 ${internalInputClass}`}
        >
          <option value="">Elige el origen…</option>
          {kinds.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
        </select>
        {error("source_kind")}
      </div>
      <div>
        <label htmlFor={`${id}-license`} className="block text-xs text-muted">
          Licencia o autorización{licenseRequired ? " (obligatoria)" : value.source_kind === "own_photo" ? " (opcional en foto propia)" : ""}
        </label>
        <input
          {...field("license")}
          value={value.license}
          disabled={disabled}
          maxLength={255}
          required={licenseRequired}
          placeholder={value.source_kind === "demo" ? "Uso demostrativo autorizado por…" : "Autorización escrita del distribuidor, 2026-11"}
          onChange={(e) => onChange({ ...value, license: e.target.value })}
          className={`mt-1.5 ${internalInputClass}`}
        />
        {error("license")}
      </div>
      <div>
        <label htmlFor={`${id}-source_url`} className="block text-xs text-muted">Dirección de origen (opcional)</label>
        <input
          {...field("source_url")}
          type="url"
          inputMode="url"
          value={value.source_url}
          disabled={disabled}
          maxLength={500}
          placeholder="https://…"
          onChange={(e) => onChange({ ...value, source_url: e.target.value })}
          className={`mt-1.5 ${internalInputClass}`}
        />
        {error("source_url")}
      </div>
      <div>
        <label htmlFor={`${id}-attribution`} className="block text-xs text-muted">Atribución (opcional)</label>
        <input
          {...field("attribution")}
          value={value.attribution}
          disabled={disabled}
          maxLength={255}
          placeholder="Foto: …"
          onChange={(e) => onChange({ ...value, attribution: e.target.value })}
          className={`mt-1.5 ${internalInputClass}`}
        />
        {error("attribution")}
      </div>
    </div>
  );
}

