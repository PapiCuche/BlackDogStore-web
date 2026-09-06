"use client";

/**
 * Administración → Escaparate — M12F.
 *
 * LA PANTALLA QUE HACE QUE «PREVENTA IPHONE 17» NO VUELVA A OCURRIR.
 *
 * Hasta aquí, el contenido comercial de la portada vivía compilado: cambiar de
 * campaña exigía tocar un componente y desplegar, y no cambiarla dejaba una
 * promoción caducada en la página principal. Esto es lo que convierte esa
 * operación en un formulario.
 *
 * TRES REGLAS QUE SE VEN EN LA INTERFAZ
 * -------------------------------------
 *   GUARDAR NO PUBLICA. Son dos botones distintos porque son dos decisiones
 *   distintas. Escribir un borrador y anunciarlo al público no pueden ser el
 *   mismo gesto.
 *
 *   ARCHIVAR NO BORRA. La campaña de hace tres años es el historial de lo que
 *   esta tienda anunció.
 *
 *   CAMPOS, NO MARCADO. No hay editor enriquecido, ni HTML, ni Markdown. Quien
 *   escribe pone un título y un texto; no decide cómo se pinta. Un editor que
 *   acepta marcado es un editor que acepta `<script>`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../../components/InternalControlGuard";
import { DashboardSection } from "../../components/dashboard-ui";
import { ListContentEditor } from "./ListContentEditor";
import {
  ContentValidationError,
  actOnStorefrontCampaign,
  createStorefrontCampaign,
  fetchStorefrontCampaigns,
  fetchStorefrontPage,
  updateStorefrontCampaign,
  updateStorefrontPage,
  type StorefrontCampaignRow,
  type StorefrontPageContent,
  type StorefrontSlot,
} from "../../lib/internal-api";

const STATUS_LABEL: Record<string, string> = {
  draft: "Borrador",
  published: "Publicada",
  archived: "Archivada",
};

const EMPTY_PAGE: StorefrontPageContent = {
  hero_eyebrow: "",
  hero_title: "",
  hero_subtitle: "",
  hero_primary_cta_label: "",
  hero_primary_cta_url: "",
  hero_secondary_cta_label: "",
  hero_secondary_cta_url: "",
  services_hero_title: "",
  services_hero_subtitle: "",
  services_warranty_note: "",
};

/** `2026-09-03T10:00:00Z` → `2026-09-03T10:00`, que es lo que pide el input. */
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export default function StorefrontContentPage() {
  return (
    <InternalControlGuard>
      {(ctx) => (
        <AdminShell user={ctx.user}>
          <StorefrontContent ctx={ctx} />
        </AdminShell>
      )}
    </InternalControlGuard>
  );
}

function StorefrontContent({ ctx }: { ctx: InternalContext }) {
  // `selectedCompanyId` es lo que el master eligió explícitamente; null cuando
  // el usuario pertenece a una sola empresa y el backend la resuelve por sus
  // membresías. Nunca se envía un id que el panel no haya recibido del backend.
  const companyId = ctx.selectedCompanyId;
  const canManage = (ctx.dashboard?.access.capabilities ?? []).includes("company.manage");

  const [slots, setSlots] = useState<StorefrontSlot[]>([]);
  const [campaigns, setCampaigns] = useState<StorefrontCampaignRow[]>([]);
  const [page, setPage] = useState<StorefrontPageContent>(EMPTY_PAGE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<StorefrontCampaignRow | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, pageData] = await Promise.all([
        fetchStorefrontCampaigns(companyId),
        fetchStorefrontPage(companyId),
      ]);
      setSlots(list.slots);
      setCampaigns(list.results);
      setPage(pageData.page);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar.");
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => {
    void load();
  }, [load]);

  const grouped = useMemo(() => {
    const by: Record<string, StorefrontCampaignRow[]> = {
      published: [], draft: [], archived: [],
    };
    for (const c of campaigns) by[c.status]?.push(c);
    return by;
  }, [campaigns]);

  async function act(id: number, action: "publish" | "archive") {
    try {
      await actOnStorefrontCampaign(id, action, companyId);
      setNotice(action === "publish" ? "Campaña publicada." : "Campaña archivada.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo completar.");
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-black uppercase tracking-tight text-foreground">
          Escaparate
        </h1>
        <p className="mt-1 text-sm text-muted text-pretty">
          Portada y campañas de tu tienda pública. Los cambios se ven sin
          desplegar nada.
        </p>
      </header>

      {/*
        EL AVISO NO ES DECORACIÓN. Quien tiene acceso a esta pantalla puede
        escribir una afirmación comercial que la aplicación no puede verificar.
        No se puede impedir; sí se puede recordar antes de escribirla.
      */}
      <p
        role="note"
        className="rounded-xl border border-bd-border bg-surface px-4 py-3 text-xs leading-5 text-muted"
      >
        Publica únicamente afirmaciones verificadas y vigentes. Precios, plazos,
        capacidades y garantías aparecen tal cual los escribas.
      </p>

      {/*
        §36 — LA ESPERA SE DICE, NO SE DESCUBRE.

        La configuración pública se sirve con una caché de 60 segundos. Sin
        avisar, quien publica recarga la tienda, no ve su cambio y vuelve a
        publicar pensando que algo falló. No se invalida la caché al guardar
        porque hoy es una cabecera HTTP: quien la respeta es el navegador o un
        CDN, y no hay mecanismo de invalidación selectiva por tenant que se
        pueda usar sin vaciar la de todos.
      */}
      <p className="text-xs leading-5 text-muted">
        Los cambios publicados tardan hasta un minuto en verse en la tienda.
      </p>

      {error ? (
        <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p aria-live="polite" className="rounded-xl border border-success-border bg-success-surface px-4 py-3 text-sm text-success">
          {notice}
        </p>
      ) : null}

      {loading ? (
        <p className="text-sm text-muted">Cargando…</p>
      ) : (
        <>
          <DashboardSection title="Portada">
            <PageForm
              value={page}
              readOnly={!canManage}
              onSaved={(next) => {
                setPage(next);
                setNotice("Portada actualizada.");
              }}
              companyId={companyId}
            />
          </DashboardSection>

          {/*
            M12F.1 — el contenido de la página de servicios.

            Aquí vivían compiladas varias afirmaciones que el proyecto no
            respalda: una cifra de dispositivos reparados sin fuente, una
            garantía universal que contradice al manual del propio taller y una
            certificación de la que no hay documento. Como datos, quien las
            escribe responde por ellas — y por eso el aviso de arriba.
          */}
          <DashboardSection title="Servicios">
            <ListContentEditor
              kind="services" companyId={companyId}
              canManage={canManage} onNotice={setNotice}
            />
          </DashboardSection>

          <DashboardSection title="Preguntas frecuentes">
            <ListContentEditor
              kind="faqs" companyId={companyId}
              canManage={canManage} onNotice={setNotice}
            />
          </DashboardSection>

          <DashboardSection title="Métricas">
            <p className="mb-4 text-xs leading-5 text-muted">
              Publica sólo cifras verificadas y vigentes. Mientras no haya
              ninguna, el bloque no aparece en la web — que es preferible a una
              cifra que nadie pueda respaldar.
            </p>
            <ListContentEditor
              kind="metrics" companyId={companyId}
              canManage={canManage} onNotice={setNotice}
            />
          </DashboardSection>

          <DashboardSection
            title="Campañas"
            action={
              canManage && !creating ? (
                <button
                  type="button"
                  onClick={() => { setCreating(true); setEditing(null); }}
                  className="min-h-11 rounded-full bg-foreground px-5 text-sm font-semibold text-background transition-colors hover:bg-foreground/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                >
                  Nueva campaña
                </button>
              ) : null
            }
          >
            {creating ? (
              <CampaignForm
                slots={slots}
                companyId={companyId}
                onCancel={() => setCreating(false)}
                onSaved={async () => {
                  setCreating(false);
                  setNotice("Borrador creado. Aún no está publicado.");
                  await load();
                }}
              />
            ) : null}

            {editing ? (
              <CampaignForm
                slots={slots}
                companyId={companyId}
                campaign={editing}
                onCancel={() => setEditing(null)}
                onSaved={async () => {
                  setEditing(null);
                  setNotice("Cambios guardados. Publicar es un paso aparte.");
                  await load();
                }}
              />
            ) : null}

            {(["published", "draft", "archived"] as const).map((group) => (
              <div key={group} className="mt-6 first:mt-0">
                <h3 className="text-[10px] font-bold uppercase tracking-[0.25em] text-muted">
                  {STATUS_LABEL[group]}
                </h3>
                {grouped[group].length === 0 ? (
                  <p className="mt-2 text-sm text-muted">Ninguna.</p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {grouped[group].map((c) => (
                      <CampaignRow
                        key={c.id}
                        campaign={c}
                        slots={slots}
                        canManage={canManage}
                        onEdit={() => { setEditing(c); setCreating(false); }}
                        onAct={act}
                      />
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </DashboardSection>
        </>
      )}
    </div>
  );
}

function CampaignRow({
  campaign, slots, canManage, onEdit, onAct,
}: {
  campaign: StorefrontCampaignRow;
  slots: StorefrontSlot[];
  canManage: boolean;
  onEdit: () => void;
  onAct: (id: number, action: "publish" | "archive") => void;
}) {
  const slotLabel = slots.find((s) => s.value === campaign.slot)?.label ?? campaign.slot;
  return (
    <li className="rounded-xl border border-bd-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        {/* `min-w-0` para que el título largo pueda truncarse en vez de
            ensanchar la fila y sacar los botones de la pantalla. */}
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold text-foreground">{campaign.title}</p>
          <p className="mt-0.5 text-xs text-muted">
            {slotLabel}
            {campaign.status === "published" ? (
              campaign.is_active
                ? " · visible ahora"
                : " · publicada, fuera de su ventana"
            ) : null}
          </p>
          {campaign.starts_at || campaign.ends_at ? (
            <p className="mt-1 text-xs text-muted">
              {campaign.starts_at ? `Desde ${new Date(campaign.starts_at).toLocaleString("es-PE")}` : ""}
              {campaign.starts_at && campaign.ends_at ? " · " : ""}
              {campaign.ends_at ? `hasta ${new Date(campaign.ends_at).toLocaleString("es-PE")}` : ""}
            </p>
          ) : null}
        </div>
        {canManage ? (
          <div className="flex flex-wrap gap-2">
            {campaign.status !== "archived" ? (
              <button
                type="button"
                onClick={onEdit}
                className="min-h-11 rounded-full border border-bd-border px-4 text-xs font-semibold text-foreground transition-colors hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                Editar
              </button>
            ) : null}
            {campaign.status === "draft" ? (
              <button
                type="button"
                onClick={() => onAct(campaign.id, "publish")}
                className="min-h-11 rounded-full bg-foreground px-4 text-xs font-semibold text-background transition-colors hover:bg-foreground/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                Publicar
              </button>
            ) : null}
            {campaign.status !== "archived" ? (
              <button
                type="button"
                onClick={() => onAct(campaign.id, "archive")}
                className="min-h-11 rounded-full border border-bd-border px-4 text-xs font-semibold text-muted transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                Archivar
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}

const FIELD_CLASS =
  "mt-1 w-full rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

function Field({
  label, name, value, onChange, errors, type = "text", as = "input", hint, ...rest
}: {
  label: string;
  name: string;
  value: string;
  onChange: (name: string, value: string) => void;
  errors?: Record<string, string[]>;
  type?: string;
  as?: "input" | "textarea" | "select";
  hint?: string;
  children?: React.ReactNode;
  maxLength?: number;
}) {
  const id = `f-${name}`;
  const problem = errors?.[name];
  const Tag = as;
  return (
    <label htmlFor={id} className="block">
      <span className="text-xs font-semibold uppercase tracking-wider text-muted">
        {label}
      </span>
      <Tag
        id={id}
        name={name}
        {...(as === "input" ? { type } : {})}
        {...(as === "textarea" ? { rows: 3 } : {})}
        value={value}
        onChange={(e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
          onChange(name, e.target.value)
        }
        aria-invalid={problem ? true : undefined}
        aria-describedby={problem ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={FIELD_CLASS}
        {...rest}
      />
      {hint && !problem ? (
        <span id={`${id}-hint`} className="mt-1 block text-xs text-muted">{hint}</span>
      ) : null}
      {problem ? (
        <span id={`${id}-error`} className="mt-1 block text-xs text-danger">
          {problem.join(" ")}
        </span>
      ) : null}
    </label>
  );
}

function PageForm({
  value, readOnly, companyId, onSaved,
}: {
  value: StorefrontPageContent;
  readOnly: boolean;
  companyId: number | null;
  onSaved: (next: StorefrontPageContent) => void;
}) {
  const [draft, setDraft] = useState(value);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => setDraft(value), [value]);

  const set = (name: string, v: string) =>
    setDraft((d) => ({ ...d, [name]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErrors({});
    try {
      const res = await updateStorefrontPage(draft, companyId);
      onSaved(res.page);
    } catch (err) {
      if (err instanceof ContentValidationError) setErrors(err.errors);
      else setErrors({ __all__: [err instanceof Error ? err.message : "Error"] });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
      <Field label="Línea superior" name="hero_eyebrow" value={draft.hero_eyebrow}
        onChange={set} errors={errors} maxLength={80}
        hint="Vacío usa el nombre de la empresa y la ciudad." />
      <Field label="Titular" name="hero_title" value={draft.hero_title}
        onChange={set} errors={errors} as="textarea" maxLength={160}
        hint="Un salto de línea = una línea del titular." />
      <div className="sm:col-span-2">
        <Field label="Texto" name="hero_subtitle" value={draft.hero_subtitle}
          onChange={set} errors={errors} as="textarea" maxLength={400} />
      </div>
      <Field label="Botón principal — texto" name="hero_primary_cta_label"
        value={draft.hero_primary_cta_label} onChange={set} errors={errors} maxLength={40} />
      <Field label="Botón principal — destino" name="hero_primary_cta_url"
        value={draft.hero_primary_cta_url} onChange={set} errors={errors}
        hint="Ruta del sitio (/product), URL http(s), tel: o mailto:" />
      <Field label="Botón secundario — texto" name="hero_secondary_cta_label"
        value={draft.hero_secondary_cta_label} onChange={set} errors={errors} maxLength={40} />
      <Field label="Botón secundario — destino" name="hero_secondary_cta_url"
        value={draft.hero_secondary_cta_url} onChange={set} errors={errors} />

      <div className="sm:col-span-2">
        <Field label="Servicios — titular" name="services_hero_title"
          value={draft.services_hero_title} onChange={set} errors={errors}
          as="textarea" maxLength={160}
          hint="Un salto de línea = una línea del titular." />
      </div>
      <div className="sm:col-span-2">
        <Field label="Servicios — texto" name="services_hero_subtitle"
          value={draft.services_hero_subtitle} onChange={set} errors={errors}
          as="textarea" maxLength={400} />
      </div>
      <div className="sm:col-span-2">
        {/*
          LA NOTA DE GARANTÍA. Existe porque la página afirmaba que todos los
          servicios llevan seis meses, y eso contradice al manual del propio
          taller: la cobertura de una reparación depende del trabajo y del
          repuesto. Vacío no publica nada, que es lo correcto mientras nadie
          escriba la política real.
        */}
        <Field label="Servicios — nota de garantía" name="services_warranty_note"
          value={draft.services_warranty_note} onChange={set} errors={errors}
          as="textarea" maxLength={600}
          hint="Vacío no publica ninguna garantía. Escribe sólo la que puedas sostener." />
      </div>

      {errors.__all__ ? (
        <p role="alert" className="text-sm text-danger sm:col-span-2">{errors.__all__.join(" ")}</p>
      ) : null}

      {!readOnly ? (
        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={saving}
            className="min-h-11 rounded-full bg-foreground px-6 text-sm font-semibold text-background transition-colors hover:bg-foreground/85 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          >
            {saving ? "Guardando…" : "Guardar portada"}
          </button>
        </div>
      ) : null}
    </form>
  );
}

function CampaignForm({
  slots, companyId, campaign, onCancel, onSaved,
}: {
  slots: StorefrontSlot[];
  companyId: number | null;
  campaign?: StorefrontCampaignRow;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState({
    slot: campaign?.slot ?? slots[0]?.value ?? "",
    badge: campaign?.badge ?? "",
    title: campaign?.title ?? "",
    subtitle: campaign?.subtitle ?? "",
    body: campaign?.body ?? "",
    image_url: campaign?.image_url ?? "",
    cta_label: campaign?.cta_label ?? "",
    cta_url: campaign?.cta_url ?? "",
    secondary_cta_label: campaign?.secondary_cta_label ?? "",
    secondary_cta_url: campaign?.secondary_cta_url ?? "",
    priority: String(campaign?.priority ?? 0),
    starts_at: toLocalInput(campaign?.starts_at ?? null),
    ends_at: toLocalInput(campaign?.ends_at ?? null),
  });
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [saving, setSaving] = useState(false);

  const set = (name: string, v: string) => setDraft((d) => ({ ...d, [name]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErrors({});
    const payload = {
      ...draft,
      priority: Number(draft.priority) || 0,
      starts_at: fromLocalInput(draft.starts_at),
      ends_at: fromLocalInput(draft.ends_at),
    } as unknown as Partial<StorefrontCampaignRow>;
    try {
      if (campaign) await updateStorefrontCampaign(campaign.id, payload, companyId);
      else await createStorefrontCampaign(payload, companyId);
      onSaved();
    } catch (err) {
      if (err instanceof ContentValidationError) setErrors(err.errors);
      else setErrors({ __all__: [err instanceof Error ? err.message : "Error"] });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-6 grid gap-4 rounded-xl border border-bd-border bg-surface p-4 sm:grid-cols-2"
    >
      <label htmlFor="f-slot" className="block">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted">Posición</span>
        <select
          id="f-slot"
          value={draft.slot}
          onChange={(e) => set("slot", e.target.value)}
          className={FIELD_CLASS}
        >
          {slots.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      </label>
      <Field label="Etiqueta" name="badge" value={draft.badge} onChange={set}
        errors={errors} maxLength={40} hint="Ej.: Preventa" />
      <Field label="Título" name="title" value={draft.title} onChange={set}
        errors={errors} maxLength={120} />
      <Field label="Subtítulo" name="subtitle" value={draft.subtitle} onChange={set}
        errors={errors} maxLength={200} />
      <div className="sm:col-span-2">
        <Field label="Texto" name="body" value={draft.body} onChange={set}
          errors={errors} as="textarea" maxLength={600} />
      </div>
      <Field label="Imagen" name="image_url" value={draft.image_url} onChange={set}
        errors={errors} hint="Ruta del sitio o URL http(s)." />
      <Field label="Prioridad" name="priority" value={draft.priority} onChange={set}
        errors={errors} type="number"
        hint="Desempata si dos campañas comparten posición." />
      <Field label="Botón — texto" name="cta_label" value={draft.cta_label}
        onChange={set} errors={errors} maxLength={40} />
      <Field label="Botón — destino" name="cta_url" value={draft.cta_url}
        onChange={set} errors={errors} />
      <Field label="Botón 2 — texto" name="secondary_cta_label"
        value={draft.secondary_cta_label} onChange={set} errors={errors} maxLength={40} />
      <Field label="Botón 2 — destino" name="secondary_cta_url"
        value={draft.secondary_cta_url} onChange={set} errors={errors} />
      <Field label="Empieza" name="starts_at" value={draft.starts_at} onChange={set}
        errors={errors} type="datetime-local"
        hint="Vacío = sin límite por ese lado." />
      <Field label="Termina" name="ends_at" value={draft.ends_at} onChange={set}
        errors={errors} type="datetime-local"
        hint="La campaña desaparece sola al llegar esta fecha." />

      {errors.__all__ ? (
        <p role="alert" className="text-sm text-danger sm:col-span-2">{errors.__all__.join(" ")}</p>
      ) : null}

      <div className="flex flex-wrap gap-3 sm:col-span-2">
        <button
          type="submit"
          disabled={saving}
          className="min-h-11 rounded-full bg-foreground px-6 text-sm font-semibold text-background transition-colors hover:bg-foreground/85 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          {saving ? "Guardando…" : campaign ? "Guardar cambios" : "Crear borrador"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="min-h-11 rounded-full border border-bd-border px-6 text-sm font-semibold text-muted transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          Cancelar
        </button>
        {/* Guardar NO publica, y se dice aquí para que nadie lo descubra
            mirando la portada. */}
        <p className="w-full text-xs text-muted">
          Guardar no publica. Publicar es una acción aparte en la lista.
        </p>
      </div>
    </form>
  );
}
