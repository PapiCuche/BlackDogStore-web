"use client";

/**
 * LABEL-01 — los códigos de un producto: verlos, copiarlos y descargarlos.
 *
 * Quien tiene `products.manage` (lo confirma el servidor con `can_manage`) puede
 * agregar un código escrito o escaneado, que se guarda tal cual, y pedir uno
 * interno si el producto no tiene ninguno. El panel nunca inventa un código ni
 * cambia uno que ya existe: eso lo decide el servidor.
 */

import { useEffect, useState } from "react";

import {
  addProductCode,
  codesAsText,
  fetchProductCodes,
  generateProductCode,
  type ProductCode,
} from "../lib/product-codes-api";
import { internalButtonClass, internalInputClass } from "./internal-ui";

export function ProductCodesPanel({
  productId, canManage, companyId,
}: {
  productId: number;
  canManage: boolean;
  companyId?: number | null;
}) {
  const [codes, setCodes] = useState<ProductCode[] | null>(null);
  const [serverManage, setServerManage] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    fetchProductCodes(productId, companyId)
      .then((data) => {
        if (!alive) return;
        setCodes(data.results);
        setServerManage(Boolean(data.can_manage));
      })
      .catch((err) => {
        if (!alive) return;
        setCodes([]);
        setError(err instanceof Error ? err.message : "No se pudieron cargar los códigos.");
      });
    return () => { alive = false; };
  }, [productId, companyId]);

  const manage = canManage && serverManage;

  function reset() {
    setError(null);
    setNotice(null);
    setWarnings([]);
  }

  async function copy(code: string) {
    reset();
    try {
      await navigator.clipboard.writeText(code);
      setNotice(`Código ${code} copiado.`);
    } catch {
      setError("No se pudo copiar. Selecciona el código y cópialo a mano.");
    }
  }

  function download() {
    reset();
    if (!codes?.some((c) => c.is_active)) return;
    const blob = new Blob([codesAsText(codes)], { type: "text/plain;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href;
    link.download = `codigos-producto-${productId}.txt`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Algunos navegadores leen la dirección después del clic: se libera luego.
    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    reset();
    const code = draft.trim();
    if (!code) return;
    setBusy(true);
    try {
      const data = await addProductCode(productId, code, companyId);
      setCodes(data.results);
      setWarnings(data.warnings ?? []);
      setDraft("");
      setNotice(`Código ${code} agregado.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo agregar el código.");
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    reset();
    setBusy(true);
    try {
      const data = await generateProductCode(productId, companyId);
      setCodes(data.results);
      setNotice(data.generated && data.code ? `Se asignó el código ${data.code}.` : "El producto ya tenía código.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo generar el código.");
    } finally {
      setBusy(false);
    }
  }

  const headingId = `product-codes-${productId}`;
  const hasActive = Boolean(codes?.some((c) => c.is_active));

  return (
    <section className="rounded-xl border border-bd-border bg-surface p-5 sm:p-6" aria-labelledby={headingId}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id={headingId} className="text-sm font-semibold text-foreground">Códigos</h2>
          <p className="mt-1 text-xs text-muted">
            Los que escanea la caja. Se guardan tal cual y nunca se cambian al editar ni al imprimir.
          </p>
        </div>
        <button type="button" className={internalButtonClass} onClick={download} disabled={!hasActive}>
          Descargar
        </button>
      </div>

      {codes === null ? (
        <p className="mt-4 text-sm text-muted">Cargando códigos…</p>
      ) : codes.length === 0 ? (
        <p className="mt-4 text-sm text-muted">Este producto no tiene códigos.</p>
      ) : (
        <ul className="mt-4 divide-y divide-bd-border rounded-lg border border-bd-border">
          {codes.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
              <span className={`min-w-0 break-all font-mono text-sm ${c.is_active ? "text-foreground" : "text-muted line-through"}`}>
                {c.code}
              </span>
              <span className="text-[11px] text-muted">{c.symbology_label}</span>
              {c.is_primary ? (
                <span className="rounded-full border border-bd-border px-2 py-0.5 text-[11px] font-semibold text-foreground">Principal</span>
              ) : null}
              {!c.is_active ? (
                <span className="rounded-full border border-bd-border px-2 py-0.5 text-[11px] text-muted">Desactivado</span>
              ) : null}
              <button
                type="button"
                className={`ml-auto ${internalButtonClass} px-2.5 py-1.5 text-xs`}
                onClick={() => void copy(c.code)}
                aria-label={`Copiar el código ${c.code}`}
              >
                Copiar
              </button>
            </li>
          ))}
        </ul>
      )}

      {manage && codes !== null ? (
        <div className="mt-4 space-y-3">
          <form onSubmit={add} className="flex flex-wrap items-end gap-3">
            <label className="block min-w-0 flex-1 basis-56 text-xs text-muted">
              Agregar un código
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={64}
                autoComplete="off"
                spellCheck={false}
                disabled={busy}
                className={`mt-1.5 font-mono ${internalInputClass}`}
              />
            </label>
            <button type="submit" className={internalButtonClass} disabled={busy || !draft.trim()}>
              Agregar
            </button>
          </form>
          {codes.length === 0 ? (
            <button type="button" className={internalButtonClass} onClick={() => void generate()} disabled={busy}>
              Asignar código interno
            </button>
          ) : null}
        </div>
      ) : null}

      {error ? <p role="alert" className="mt-3 text-xs text-danger">{error}</p> : null}
      {notice ? <p role="status" className="mt-3 text-xs text-success">{notice}</p> : null}
      {warnings.length ? (
        <ul role="status" className="mt-3 list-disc space-y-1 pl-5 text-xs text-warning">
          {warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      ) : null}
    </section>
  );
}
