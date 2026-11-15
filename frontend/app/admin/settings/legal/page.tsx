"use client";

/**
 * Administración → Configuración → Políticas legales — LEGAL-POLICIES-01.
 *
 * Three documents of THIS company: privacy, cookies, and WhatsApp/CRM. Same rules
 * as the storefront editor:
 *   GUARDAR NO PUBLICA. «Guardar borrador» and «Publicar» are two decisions.
 *   TEXTO PLANO. «# », «## », «- » and blank lines give headings, lists and
 *   paragraphs; nothing is interpreted as HTML.
 * The backend checks `company.manage` on every write; this screen only decides
 * what it offers.
 */

import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../../components/InternalControlGuard";
import { LegalText } from "../../../components/LegalText";
import { LEGAL_PAGES, type LegalKind } from "../../../lib/legal-policies";
import {
  LegalPolicyError,
  fetchLegalPolicies,
  publishLegalPolicy,
  saveLegalPolicyDraft,
  unpublishLegalPolicy,
  type AdminLegalPolicies,
  type AdminLegalPolicy,
} from "../../lib/legal-policies-api";

const KINDS: LegalKind[] = ["privacy", "cookies", "whatsapp"];

const BUTTON = "min-h-11 rounded-full px-5 text-sm font-semibold transition-colors disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";
const PRIMARY = `${BUTTON} bg-foreground text-background hover:bg-foreground/85`;
const SECONDARY = `${BUTTON} border border-bd-border text-foreground hover:bg-surface`;

export default function LegalPoliciesPage() {
  return (
    <InternalControlGuard>
      {(ctx) => (
        <AdminShell user={ctx.user}>
          <LegalPolicies ctx={ctx} />
        </AdminShell>
      )}
    </InternalControlGuard>
  );
}

function LegalPolicies({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.selectedCompanyId;
  const [data, setData] = useState<AdminLegalPolicies | null>(null);
  const [active, setActive] = useState<LegalKind>("privacy");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await fetchLegalPolicies(companyId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar.");
    }
  }, [companyId]);

  useEffect(() => {
    void load();
  }, [load]);

  const replace = (row: AdminLegalPolicy) =>
    setData((d) => (d ? { ...d, policies: d.policies.map((p) => (p.kind === row.kind ? row : p)) } : d));

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-wider text-muted">Configuración</p>
        <h1 className="mt-1 text-2xl font-bold">Políticas legales</h1>
        <p className="mt-2 text-sm text-muted">
          Los textos que se publican en la tienda de {data?.company.name ?? "esta empresa"}. Guardar no publica:
          la página pública sólo muestra lo que publicas.
        </p>
      </header>

      {error ? <p role="alert" className="rounded-xl border border-danger/40 p-3 text-sm text-danger">{error}</p> : null}

      {data ? (
        <>
          <div role="tablist" aria-label="Documento" className="flex flex-wrap gap-2">
            {KINDS.map((kind) => {
              const row = data.policies.find((p) => p.kind === kind);
              return (
                <button
                  key={kind}
                  type="button"
                  role="tab"
                  id={`legal-tab-${kind}`}
                  aria-selected={active === kind}
                  aria-controls={`legal-panel-${kind}`}
                  onClick={() => setActive(kind)}
                  className={`${BUTTON} border ${active === kind ? "border-foreground bg-foreground text-background" : "border-bd-border"}`}
                >
                  {LEGAL_PAGES[kind].title}
                  <span className="ml-2 text-xs font-normal opacity-80">
                    {row?.is_published ? (row.has_unpublished_changes ? "· cambios sin publicar" : "· publicada") : "· sin publicar"}
                  </span>
                </button>
              );
            })}
          </div>
          {KINDS.map((kind) => {
            const row = data.policies.find((p) => p.kind === kind);
            return row ? (
              <div key={kind} role="tabpanel" id={`legal-panel-${kind}`} aria-labelledby={`legal-tab-${kind}`} hidden={active !== kind}>
                <PolicyEditor
                  key={`${kind}-${data.company.id}`}
                  row={row}
                  maxLength={data.max_length}
                  canManage={data.can_manage}
                  companyId={companyId}
                  onSaved={replace}
                />
              </div>
            ) : null;
          })}
        </>
      ) : !error ? <p className="text-sm text-muted">Cargando…</p> : null}
    </div>
  );
}

function PolicyEditor({
  row, maxLength, canManage, companyId, onSaved,
}: {
  row: AdminLegalPolicy;
  maxLength: number;
  canManage: boolean;
  companyId: number | null;
  onSaved: (row: AdminLegalPolicy) => void;
}) {
  const [text, setText] = useState(row.draft_text);
  const [busy, setBusy] = useState<null | "save" | "publish" | "unpublish">(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const dirty = text !== row.draft_text;
  const page = LEGAL_PAGES[row.kind];

  async function run(kind: "save" | "publish" | "unpublish") {
    setBusy(kind);
    setErrors([]);
    setNotice(null);
    try {
      let saved = row;
      if (kind === "save" || (kind === "publish" && dirty)) {
        saved = await saveLegalPolicyDraft(row.kind, text, companyId);
        onSaved(saved);
        setText(saved.draft_text);
      }
      if (kind === "publish") saved = await publishLegalPolicy(row.kind, companyId);
      if (kind === "unpublish") saved = await unpublishLegalPolicy(row.kind, companyId);
      onSaved(saved);
      setNotice(kind === "save" ? "Borrador guardado. Todavía no es público." :
        kind === "publish" ? "Publicada. La página pública ya muestra este texto." :
          "Publicación retirada. El borrador se conserva.");
    } catch (err) {
      if (err instanceof LegalPolicyError) {
        const fieldErrors = Object.values(err.errors).flat();
        setErrors(fieldErrors.length ? fieldErrors : [err.message]);
      } else {
        setErrors([err instanceof Error ? err.message : "No se pudo guardar."]);
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border border-bd-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm">
          {row.is_published
            ? `Publicada${row.published_at ? ` el ${new Date(row.published_at).toLocaleString("es-PE", { timeZone: "America/Lima" })}` : ""}.`
            : "Sin publicar: la página pública muestra el texto de respaldo."}
          {row.has_unpublished_changes && row.is_published ? " Hay cambios guardados sin publicar." : ""}
        </p>
        <a href={page.href} target="_blank" rel="noopener noreferrer" className="text-sm underline underline-offset-4">
          Abrir página pública ({page.href})
        </a>
      </div>

      <label htmlFor={`legal-text-${row.kind}`} className="block">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted">Texto ({page.title})</span>
        <textarea
          id={`legal-text-${row.kind}`}
          value={text}
          onChange={(e) => setText(e.target.value)}
          readOnly={!canManage}
          maxLength={maxLength}
          rows={22}
          aria-invalid={errors.length > 0}
          aria-describedby={`legal-hint-${row.kind}`}
          className="mt-1 block w-full rounded-xl border border-bd-border bg-background p-3 font-mono text-sm leading-6"
        />
      </label>
      <p id={`legal-hint-${row.kind}`} className="text-xs text-muted">
        Texto plano, sin HTML. «# Título» y «## Subtítulo» crean encabezados, «- » una lista y una línea en blanco un
        párrafo nuevo. {text.length} / {maxLength} caracteres. No se publica un texto con campos como «[RUC]».
      </p>

      {errors.length ? (
        <div role="alert" className="rounded-xl border border-danger/40 p-3 text-sm text-danger">
          {errors.map((message) => <p key={message}>{message}</p>)}
        </div>
      ) : null}
      {notice ? <p role="status" className="text-sm text-success">{notice}</p> : null}

      <div className="flex flex-wrap gap-3">
        {canManage ? (
          <>
            <button type="button" className={SECONDARY} disabled={busy !== null || !dirty} onClick={() => void run("save")}>
              {busy === "save" ? "Guardando…" : "Guardar borrador"}
            </button>
            <button type="button" className={PRIMARY} disabled={busy !== null || !text.trim()} onClick={() => void run("publish")}>
              {busy === "publish" ? "Publicando…" : dirty ? "Guardar y publicar" : "Publicar"}
            </button>
            {row.is_published ? (
              <button type="button" className={SECONDARY} disabled={busy !== null} onClick={() => void run("unpublish")}>
                {busy === "unpublish" ? "Retirando…" : "Retirar publicación"}
              </button>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-muted">Sólo lectura: necesitas permiso para gestionar la empresa.</p>
        )}
        <button type="button" className={SECONDARY} aria-expanded={preview} onClick={() => setPreview((v) => !v)}>
          {preview ? "Ocultar vista previa" : "Vista previa"}
        </button>
      </div>

      {preview ? (
        <div className="rounded-xl border border-bd-border bg-background p-4 sm:p-6" aria-label="Vista previa">
          <p className="mb-4 text-xs font-semibold uppercase tracking-wider text-muted">Vista previa del texto en pantalla</p>
          <h2 className="font-display text-2xl font-bold">{page.title}</h2>
          <div className="mt-4">
            {text.trim() ? <LegalText text={text} /> : <p className="text-sm text-muted">Sin texto.</p>}
          </div>
        </div>
      ) : null}
    </section>
  );
}
