"use client";

// DELIVERY-01 — «Transportistas»: the carriers THIS company works with.
//
// A company's own list. Nothing is seeded: the two suggestions below only fill
// the name field, and a carrier exists when somebody saves it. A carrier is
// switched off, never deleted — orders name the carrier they left with.
// Whether this person may change the list is the server's answer (`can_manage`).

import { useEffect, useState } from "react";
import {
  DeliveryError, createCarrier, fetchCarriers, updateCarrier,
  type Carrier, type CarrierKind,
} from "../../lib/delivery";
import { internalButtonClass, internalInputClass } from "./internal-ui";

/** Names many shops use. They fill the field; they are not carriers until saved. */
const SUGGESTIONS = ["Shalom", "Olva Courier"];

type Draft = { name: string; kind: CarrierKind; tracking_url_template: string };
const EMPTY: Draft = { name: "", kind: "agency", tracking_url_template: "" };

export function CarrierSettings() {
  const [carriers, setCarriers] = useState<Carrier[] | null>(null);
  const [canManage, setCanManage] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // `null`: adding a new one. A number: correcting that carrier.
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  async function reload() {
    const list = await fetchCarriers();
    setCarriers(list.results);
    setCanManage(list.can_manage);
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await fetchCarriers();
        if (cancelled) return;
        setCarriers(list.results);
        setCanManage(list.can_manage);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "No se pudieron cargar los transportistas.");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function run(work: () => Promise<unknown>, after?: () => void) {
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await work();
      after?.();
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
      if (err instanceof DeliveryError) setFieldErrors(err.fields);
    } finally {
      setBusy(false);
    }
  }

  function save(event: React.FormEvent) {
    event.preventDefault();
    const body = {
      name: draft.name.trim(), kind: draft.kind, tracking_url_template: draft.tracking_url_template.trim(),
    };
    void run(
      () => (editing === null ? createCarrier(body) : updateCarrier(editing, body)),
      () => { setEditing(null); setDraft(EMPTY); },
    );
  }

  function edit(carrier: Carrier) {
    setEditing(carrier.id);
    setDraft({ name: carrier.name, kind: carrier.kind, tracking_url_template: carrier.tracking_url_template });
    setError(null);
    setFieldErrors({});
  }

  return (
    <div className="space-y-4">
      <p className="text-xs leading-5 text-muted">
        Las agencias y couriers con los que despachas. Una <strong>agencia</strong> es donde el cliente recoge su
        pedido; un <strong>courier</strong> lo lleva a su puerta. Aparecen al registrar la entrega de un pedido.
      </p>

      {loadError ? <p role="alert" className="text-xs text-danger">{loadError}</p> : null}
      {!carriers && !loadError ? <p role="status" className="text-xs text-muted">Cargando…</p> : null}
      {carriers && carriers.length === 0 ? (
        <p className="text-sm text-muted">Todavía no registraste ningún transportista.</p>
      ) : null}

      {carriers && carriers.length > 0 ? (
        <ul className="divide-y divide-bd-border rounded-lg border border-bd-border">
          {carriers.map((carrier) => (
            <li key={carrier.id} data-testid="carrier-row" className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
              <div className="min-w-0">
                <p className="break-words text-sm font-medium text-foreground">
                  {carrier.name}
                  {!carrier.is_active ? (
                    <span className="ml-2 rounded border border-bd-border px-1.5 py-0.5 text-[11px] font-medium text-muted">Inactivo</span>
                  ) : null}
                </p>
                <p className="text-xs text-muted">
                  {carrier.kind_label}
                  {carrier.tracking_url_template ? " · con seguimiento" : ""}
                </p>
              </div>
              {canManage ? (
                <div className="flex flex-wrap gap-1.5">
                  <button type="button" disabled={busy} onClick={() => edit(carrier)} className={internalButtonClass}
                    aria-label={`Editar ${carrier.name}`}>
                    Editar
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void run(() => updateCarrier(carrier.id, { is_active: !carrier.is_active }))}
                    className={internalButtonClass}
                    aria-label={`${carrier.is_active ? "Desactivar" : "Activar"} ${carrier.name}`}
                  >
                    {carrier.is_active ? "Desactivar" : "Activar"}
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {carriers && canManage ? (
        <form onSubmit={save} noValidate aria-label="Transportista" className="space-y-3 border-t border-bd-border pt-4">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
            {editing === null ? "Agregar transportista" : "Editar transportista"}
          </p>
          {editing === null ? (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              Sugerencias:
              {SUGGESTIONS.map((name) => (
                <button key={name} type="button" onClick={() => setDraft((d) => ({ ...d, name }))} className={internalButtonClass}>
                  {name}
                </button>
              ))}
            </div>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="carrier-name" className="mb-1.5 block text-xs text-muted">Nombre</label>
              <input
                id="carrier-name" value={draft.name} maxLength={80}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                aria-invalid={Boolean(fieldErrors.name)} className={internalInputClass}
              />
              {fieldErrors.name ? <p role="alert" className="mt-1 text-xs text-danger">{fieldErrors.name}</p> : null}
            </div>
            <div>
              <label htmlFor="carrier-kind" className="mb-1.5 block text-xs text-muted">Cómo entrega</label>
              <select
                id="carrier-kind" value={draft.kind}
                onChange={(e) => setDraft((d) => ({ ...d, kind: e.target.value as CarrierKind }))}
                className={internalInputClass}
              >
                <option value="agency">En agencia (el cliente recoge)</option>
                <option value="courier">A domicilio</option>
              </select>
            </div>
          </div>
          <div>
            <label htmlFor="carrier-template" className="mb-1.5 block text-xs text-muted">Dirección de seguimiento (opcional)</label>
            <input
              id="carrier-template" value={draft.tracking_url_template} maxLength={300} inputMode="url"
              placeholder="https://…/{guia}"
              onChange={(e) => setDraft((d) => ({ ...d, tracking_url_template: e.target.value }))}
              aria-invalid={Boolean(fieldErrors.tracking_url_template)}
              aria-describedby="carrier-template-hint" className={internalInputClass}
            />
            <p id="carrier-template-hint" className={`mt-1 text-[11px] ${fieldErrors.tracking_url_template ? "text-danger" : "text-muted"}`}>
              {fieldErrors.tracking_url_template
                ?? "Debe empezar por https://. Escribe {guia} donde va el número de guía; el cliente verá ese enlace en su pedido."}
            </p>
          </div>
          {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
          <div className="flex flex-wrap gap-2">
            <button type="submit" disabled={busy} className={internalButtonClass}>
              {busy ? "Guardando…" : editing === null ? "Agregar transportista" : "Guardar cambios"}
            </button>
            {editing !== null ? (
              <button type="button" disabled={busy} onClick={() => { setEditing(null); setDraft(EMPTY); }} className={internalButtonClass}>
                Cancelar
              </button>
            ) : null}
          </div>
        </form>
      ) : carriers ? (
        <p className="text-xs text-muted">
          Puedes ver esta lista pero no cambiarla. Se requiere la capacidad{" "}
          <code className="text-foreground/85">settings.manage</code>, con acceso a toda la empresa.
        </p>
      ) : null}
    </div>
  );
}
