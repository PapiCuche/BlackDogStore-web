"use client";

/**
 * SUNAT-SERIES-PANEL (C3) — the list and the form behind Configuración › Series fiscales.
 * Every rule is the server's (`backend/store/fiscal_series_views.py`); this only shows them.
 */

import { useCallback, useEffect, useState } from "react";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../components/internal-ui";
import {
  FiscalSeriesError, createFiscalSeries, deleteFiscalSeries, fetchFiscalSeries, updateFiscalSeries,
  type FiscalSeriesListing, type FiscalSeriesRow,
} from "../../../lib/fiscal-series";

const ENVIRONMENT_LABEL: Record<string, string> = { beta: "BETA (pruebas)", production: "PRODUCCIÓN" };

function message(error: unknown): string {
  if (error instanceof FiscalSeriesError) {
    const fields = Object.values(error.fields);
    return fields.length ? fields.join(" ") : error.message;
  }
  return "No se pudo completar la acción.";
}

function NewSeriesForm({ listing, onCreated }: { listing: FiscalSeriesListing; onCreated: () => void }) {
  const [branch, setBranch] = useState("");
  const [documentType, setDocumentType] = useState(listing.document_types[0]?.value ?? "01");
  const [series, setSeries] = useState("");
  const [environment, setEnvironment] = useState("beta");
  const [nextNumber, setNextNumber] = useState("1");
  const [active, setActive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const letters = listing.document_types.find((t) => t.value === documentType)?.letters ?? [];

  const submit = async () => {
    if (!listing.company) return;
    setBusy(true);
    setError(null);
    try {
      await createFiscalSeries({
        company: listing.company.id, branch: branch ? Number(branch) : null, document_type: documentType,
        series: series.trim().toUpperCase(), environment, next_number: Number(nextNumber), is_active: active,
      });
      setSeries("");
      onCreated();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      aria-label="Nueva serie"
      className="grid gap-3 rounded-xl border border-bd-border p-4 md:grid-cols-3"
      onSubmit={(event) => { event.preventDefault(); void submit(); }}
    >
      <label className="text-xs text-muted">Ambiente
        <select className={internalInputClass} value={environment} onChange={(e) => setEnvironment(e.target.value)}>
          {listing.environments.map((env) => (
            <option key={env.value} value={env.value}>{ENVIRONMENT_LABEL[env.value] ?? env.label}</option>
          ))}
        </select>
      </label>
      <label className="text-xs text-muted">Tipo
        <select className={internalInputClass} value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
          {listing.document_types.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
        </select>
      </label>
      <label className="text-xs text-muted">Dónde
        <select className={internalInputClass} value={branch} onChange={(e) => setBranch(e.target.value)}>
          <option value="">Toda la empresa</option>
          {listing.branches.map((b) => <option key={b.id} value={b.id}>{`Sucursal ${b.name}`}</option>)}
        </select>
      </label>
      <label className="text-xs text-muted">Serie
        <input
          className={internalInputClass} value={series} maxLength={4} required
          placeholder={`${letters[0] ?? "F"}001`} onChange={(e) => setSeries(e.target.value.toUpperCase())}
        />
        <span className="mt-1 block">{`Cuatro caracteres: ${letters.join(" o ")} y tres letras o dígitos.`}</span>
      </label>
      <label className="text-xs text-muted">Primer número
        <input
          className={internalInputClass} type="number" min={1} max={99999999} value={nextNumber} required
          onChange={(e) => setNextNumber(e.target.value)}
        />
        <span className="mt-1 block">Se puede corregir hasta que se emita el primer comprobante.</span>
      </label>
      <label className="flex items-center gap-2 text-sm text-foreground">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Activa
      </label>
      {environment === "production" ? (
        <p role="note" className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning md:col-span-3">
          Serie de PRODUCCIÓN: no emite nada mientras SUNAT siga en BETA. Cuando se active producción, cada número que
          se use quedará gastado ante SUNAT.
        </p>
      ) : null}
      {error ? <p role="alert" className="text-sm text-danger md:col-span-3">{error}</p> : null}
      <div className="md:col-span-3">
        <button type="submit" className={internalPrimaryButtonClass} disabled={busy || !series.trim()}>Crear serie</button>
      </div>
    </form>
  );
}

/** The series name typed back, or null if the master cancelled. Asked by the server too. */
function typedName(row: FiscalSeriesRow, question: string): string | null {
  const typed = window.prompt(`${question} Escribe ${row.series} para confirmarlo.`);
  return typed === null ? null : typed.trim().toUpperCase();
}

function SeriesRow({ row, onChanged }: { row: FiscalSeriesRow; onChanged: () => void }) {
  const [number, setNumber] = useState(String(row.next_number));
  const [error, setError] = useState<string | null>(null);
  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(message(err));
    }
  };
  return (
    <tr className="border-t border-bd-border align-top">
      <td className="py-2 pr-3 font-mono">{row.series}</td>
      <td className="py-2 pr-3">{row.document_label}</td>
      <td className="py-2 pr-3">{row.branch ? row.branch.name : "Toda la empresa"}</td>
      <td className="py-2 pr-3">{ENVIRONMENT_LABEL[row.environment] ?? row.environment}</td>
      <td className="py-2 pr-3">
        {row.next_number_editable ? (
          <span className="flex gap-2">
            <input
              aria-label={`Siguiente número de ${row.series}`} className={`${internalInputClass} w-28`} type="number"
              min={1} value={number} onChange={(e) => setNumber(e.target.value)}
            />
            <button
              type="button" className={internalButtonClass} disabled={Number(number) === row.next_number}
              onClick={() => void run(() => updateFiscalSeries(row.id, { next_number: Number(number) }))}
            >Guardar</button>
          </span>
        ) : (
          <span title="Ya se emitió con esta serie: el correlativo lo lleva el sistema.">{row.next_number}</span>
        )}
      </td>
      <td className="py-2 pr-3">{row.is_active ? "Activa" : "Inactiva"}</td>
      <td className="py-2">
        <span className="flex flex-wrap gap-2">
          <button
            type="button" className={internalButtonClass}
            onClick={() => {
              if (row.is_active && row.environment === "production") {
                const confirm = typedName(row, "Desactivar una serie de PRODUCCIÓN detiene la emisión con ella.");
                if (confirm === null) return;
                void run(() => updateFiscalSeries(row.id, { is_active: false, confirm }));
                return;
              }
              void run(() => updateFiscalSeries(row.id, { is_active: !row.is_active }));
            }}
          >{row.is_active ? "Desactivar" : "Activar"}</button>
          {row.deletable ? (
            <button
              type="button" className={internalButtonClass}
              onClick={() => {
                const confirm = typedName(row, "La serie se borrará.");
                if (confirm !== null) void run(() => deleteFiscalSeries(row.id, confirm));
              }}
            >Borrar</button>
          ) : null}
        </span>
        {error ? <p role="alert" className="mt-1 text-xs text-danger">{error}</p> : null}
      </td>
    </tr>
  );
}

export function FiscalSeriesPanel() {
  const [companyId, setCompanyId] = useState<number | null>(null);
  const [listing, setListing] = useState<FiscalSeriesListing | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    fetchFiscalSeries(companyId).then(
      (loaded) => { if (!cancelled) { setListing(loaded); setError(null); } },
      (err) => { if (!cancelled) setError(message(err)); },
    );
    return () => { cancelled = true; };
  }, [companyId]);

  useEffect(() => load(), [load]);

  return (
    <div className="space-y-5">
      <p role="note" className="rounded-xl border border-bd-border bg-background px-4 py-3 text-sm text-muted">
        BETA y PRODUCCIÓN usan series distintas, aunque se llamen igual. Una sola serie activa por tipo, ambiente y
        sucursal (o empresa). Una serie con comprobantes emitidos no se borra ni cambia su correlativo: se desactiva.
      </p>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {listing ? (
        <label className="block max-w-md text-xs text-muted">Empresa
          <select
            className={internalInputClass} value={listing.company?.id ?? companyId ?? ""}
            onChange={(e) => setCompanyId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Elige una empresa…</option>
            {listing.companies.map((c) => (
              <option key={c.id} value={c.id}>{c.tax_id ? `${c.name} — RUC ${c.tax_id}` : c.name}</option>
            ))}
          </select>
        </label>
      ) : <p className="text-sm text-muted">Cargando…</p>}
      {listing?.company ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="pb-2 pr-3">Serie</th><th className="pb-2 pr-3">Tipo</th><th className="pb-2 pr-3">Dónde</th>
                  <th className="pb-2 pr-3">Ambiente</th><th className="pb-2 pr-3">Siguiente número</th>
                  <th className="pb-2 pr-3">Estado</th><th className="pb-2" />
                </tr>
              </thead>
              <tbody>
                {listing.series.length === 0 ? (
                  <tr><td colSpan={7} className="py-3 text-muted">Esta empresa no tiene series.</td></tr>
                ) : listing.series.map((row) => (
                  <SeriesRow key={`${row.id}-${row.next_number}-${row.is_active}`} row={row} onChanged={load} />
                ))}
              </tbody>
            </table>
          </div>
          <h2 className="font-display text-base font-semibold text-foreground">Nueva serie</h2>
          <NewSeriesForm key={listing.company.id} listing={listing} onCreated={load} />
        </>
      ) : null}
    </div>
  );
}

