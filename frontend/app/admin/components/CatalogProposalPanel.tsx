"use client";

/**
 * INV-FAST-B1 — «Proponer lo que falta». Para las filas que el catálogo no conoce, el servidor propone el modelo,
 * las opciones y las variantes que faltan; la persona marca lo que aprueba y sólo eso se crea (oculto de la web,
 * sin precio publicable hasta que se fije uno). Nada se guarda en el navegador.
 */
import { useRef, useState } from "react";
import { newReceiptKey } from "../lib/inventory-receipts";
import {
  applyProposal, approvable, approvalCounts, identityExtras, requestProposal, toggleModel, toggleVariant,
  type Approval, type PlanVariant, type Proposal, type ProposalRow,
} from "../lib/inventory-catalog-proposal";

type Props = {
  /** The rows the catalogue does not know yet. */
  rows: ProposalRow[];
  companyId?: number | null;
  /** `products.manage` and `inventory.adjust`: what the server asks to create. */
  allowed: boolean;
  /** Called after creating: the rows are searched again and enter the draft. */
  onApplied: () => void;
};

/** The key is a secret-quality random value or nothing: the browser that cannot make one does not create. */
const newKey = () => newReceiptKey();

export function CatalogProposalPanel({ rows, companyId = null, allowed, onApplied }: Props) {
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [approval, setApproval] = useState<Approval>({ models: [], variants: [] });
  const [prices, setPrices] = useState<Record<string, string>>({});
  // Variants the person says carry no IMEI (a tablet, a watch). Off by default: a device carries an IMEI.
  const [withoutImei, setWithoutImei] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  // One key per request: a retry or a double click of the SAME approval is the same request. A changed approval
  // (another tick, another price) is another request and gets its own key, so it is never mistaken for a replay.
  const key = useRef<{ value: string; body: string } | null>(null);
  const working = useRef(false);

  if (!rows.length && !proposal && !done) return null;
  if (!allowed) {
    return (
      <p className="text-xs text-muted">
        Crear modelos y variantes desde la lista requiere permiso para gestionar productos y ajustar inventario.
      </p>
    );
  }

  async function propose() {
    if (working.current) return;
    working.current = true;
    setBusy(true); setError(""); setDone("");
    try {
      const answer = await requestProposal(rows, companyId);
      setProposal(answer);
      // Nothing is approved until the person ticks it («Seleccionar todo» is explicit).
      setApproval({ models: [], variants: [] });
      setWithoutImei([]);
      setPrices(Object.fromEntries(answer.plan.variants.filter(v => v.suggested_price).map(v => [v.ref, v.suggested_price as string])));
      key.current = null;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudo proponer el catálogo.");
    } finally {
      working.current = false; setBusy(false);
    }
  }

  async function create() {
    if (!proposal || working.current) return;
    working.current = true;
    setBusy(true); setError("");
    try {
      const body = JSON.stringify([rows.map((r) => r.id), approval, prices, withoutImei]);
      if (key.current?.body !== body) {
        const value = newKey();
        if (!value) throw new Error("Este navegador no puede generar la clave de seguridad para crear el catálogo. Usa un navegador actualizado con conexión segura (https).");
        key.current = { value, body };
      }
      const answer = await applyProposal(rows, approval, prices, key.current.value, companyId, withoutImei);
      const made = answer.created;
      setDone(`Creado: ${made.models.length} modelo(s), ${made.options.length} opción(es), ${made.variants.length} variante(s). `
        + "Quedan ocultas de la web hasta que fijes su precio y las publiques.");
      setProposal(null);
      onApplied();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudo crear lo aprobado.");
    } finally {
      working.current = false; setBusy(false);
    }
  }

  const plan = proposal?.plan;
  const counts = plan ? approvalCounts(plan, approval) : null;
  const missing = plan ? approvable(plan) : { models: [], variants: [] };
  const everything = plan ? missing.variants.length + missing.models.length : 0;
  const selected = approval.variants.length + approval.models.length;
  const skipped = proposal?.rows.filter(row => !row.variant_ref) ?? [];
  // Data of the text that is not an option of the catalogue: said for each row that becomes a variant.
  const extras = (proposal?.rows ?? []).flatMap(item => {
    const source = rows.find(row => String(row.id) === item.key);
    const text = item.variant_ref && source ? identityExtras(source) : "";
    return text ? [{ key: item.key, text }] : [];
  });

  return (
    <section aria-label="Proponer lo que falta" className="space-y-3 rounded-lg border border-bd-border p-3 text-xs">
      <div>
        <h4 className="text-sm font-semibold">Lo que falta en el catálogo</h4>
        <p className="mt-1 text-muted">
          {rows.length} fila(s) no tienen variante. El servidor propone qué crear; no se crea nada hasta que lo apruebes.
          Lo creado queda oculto de la web.
        </p>
      </div>
      {!plan ? (
        rows.length ? (
          <button type="button" onClick={() => void propose()} disabled={busy}
            className="min-h-11 rounded-lg border border-bd-border px-3 py-1.5 font-semibold disabled:opacity-40">
            {busy ? "Proponiendo…" : "Proponer lo que falta"}
          </button>
        ) : null
      ) : (
        <div className="space-y-3">
          <p role="status">
            {proposal!.counts.models_missing} modelo(s), {proposal!.counts.options_missing} opción(es) y{" "}
            {proposal!.counts.variants_missing} variante(s) por crear · {proposal!.counts.variants_existing} variante(s) ya existen
            {skipped.length ? ` · ${skipped.length} fila(s) sin propuesta` : ""}
          </p>
          <label className="flex min-h-11 items-center gap-2 font-semibold">
            <input type="checkbox" className="h-5 w-5" checked={everything > 0 && selected === everything}
              onChange={event => setApproval(event.target.checked ? missing : { models: [], variants: [] })} />
            Seleccionar todo
          </label>
          <ul className="space-y-3" aria-label="Propuesta por modelo">
            {plan.models.map(model => {
              const variants = plan.variants.filter(variant => variant.model_ref === model.ref);
              if (!variants.length && model.status === "existing") return null;
              return (
                <li key={model.ref} className="space-y-1 rounded border border-bd-border p-2">
                  <label className="flex min-h-11 items-center gap-2 font-semibold">
                    {model.status === "missing" ? (
                      <input type="checkbox" className="h-5 w-5" aria-label={`Crear el modelo ${model.name}`}
                        checked={approval.models.includes(model.ref)}
                        onChange={event => setApproval(all => toggleModel(plan, all, model.ref, event.target.checked))} />
                    ) : null}
                    {model.name}
                    <span className="font-normal text-muted">{model.status === "missing" ? "(modelo nuevo)" : "(el modelo ya existe)"}</span>
                  </label>
                  <ul className="space-y-1 pl-5">
                    {variants.map(variant => (
                      <VariantRow key={variant.ref} variant={variant} approval={approval}
                        price={prices[variant.ref] ?? ""} plan={plan}
                        noImei={withoutImei.includes(variant.ref)}
                        onNoImei={on => setWithoutImei(all => (on ? [...new Set([...all, variant.ref])] : all.filter(ref => ref !== variant.ref)))}
                        onToggle={on => setApproval(all => toggleVariant(plan, all, variant.ref, on))}
                        onPrice={value => setPrices(all => ({ ...all, [variant.ref]: value }))} />
                    ))}
                  </ul>
                </li>
              );
            })}
          </ul>
          {skipped.length ? (
            <ul aria-label="Filas sin propuesta" className="space-y-1 text-warning">
              {skipped.map(row => <li key={row.key}>{`Registro ${row.key}: ${row.reason}`}</li>)}
            </ul>
          ) : null}
          {extras.length ? (
            <ul aria-label="Datos que no distinguen la variante" className="space-y-1 text-warning">
              {extras.map(item => (
                <li key={item.key}>
                  {`Registro ${item.key}: trae ${item.text}. No es una opción del catálogo: la variante se crea sin ese dato. Revísalo.`}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => void create()} disabled={busy || !selected}
              className="min-h-11 rounded-lg bg-primary px-3 py-1.5 font-semibold text-background disabled:opacity-40">
              {busy ? "Creando…" : "Crear lo aprobado"}
            </button>
            <span className="text-muted">
              Se crearán {counts!.models} modelo(s), {counts!.options} opción(es) y {counts!.variants} variante(s).
            </span>
            <button type="button" onClick={() => setProposal(null)} disabled={busy}
              className="min-h-11 rounded-lg border border-bd-border px-3 py-1.5 font-semibold">
              Cancelar
            </button>
          </div>
        </div>
      )}
      {error ? <p role="alert" className="text-danger">{error}</p> : null}
      {done ? <p role="status" className="text-success">{done}</p> : null}
    </section>
  );
}

function VariantRow({ variant, approval, price, plan, noImei, onToggle, onPrice, onNoImei }: {
  variant: PlanVariant; approval: Approval; price: string; plan: Proposal["plan"]; noImei: boolean;
  onToggle: (on: boolean) => void; onPrice: (value: string) => void; onNoImei: (on: boolean) => void;
}) {
  const options = variant.creates.options.map(ref => plan.options.find(option => option.ref === ref)?.label).filter(Boolean);
  return (
    <li className="flex flex-wrap items-center gap-2">
      {variant.status === "missing" ? (
        <label className="flex min-h-11 items-center gap-2">
          <input type="checkbox" className="h-5 w-5" aria-label={`Crear la variante ${variant.suggested_name}`}
            checked={approval.variants.includes(variant.ref)} onChange={event => onToggle(event.target.checked)} />
          {variant.suggested_name}
        </label>
      ) : <span>{variant.suggested_name} <span className="text-muted">(ya existe)</span></span>}
      <span className="text-muted">{variant.row_keys.length} fila(s)</span>
      {options.length ? <span className="text-muted">crea la opción {options.join(", ")}</span> : null}
      {variant.status === "missing" ? (
        <label className="flex min-h-11 items-center gap-1 text-muted">
          <input type="checkbox" className="h-5 w-5" aria-label={`Sin IMEI: ${variant.suggested_name}`} checked={noImei}
            onChange={event => onNoImei(event.target.checked)} />
          Sin IMEI
        </label>
      ) : null}
      {variant.status === "missing" ? (
        <label className="flex items-center gap-1">
          <span className="sr-only">Precio de venta de {variant.suggested_name}</span>
          <input inputMode="decimal" placeholder="Precio (opcional)" value={price}
            aria-label={`Precio de venta de ${variant.suggested_name}`}
            onChange={event => onPrice(event.target.value.replace(/[^0-9.]/g, "").slice(0, 11))}
            className="w-28 rounded border border-bd-border bg-background p-1" />
        </label>
      ) : null}
    </li>
  );
}
