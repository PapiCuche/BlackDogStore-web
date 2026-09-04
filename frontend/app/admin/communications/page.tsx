"use client";

/**
 * M12C — comunicados internos.
 *
 * Un comunicado no es un chat. Se redacta, se decide a quién va, se confirma y
 * se publica; después ya no se toca. La interfaz refleja esa asimetría en vez
 * de disimularla: mientras es borrador todo es editable, y en cuanto se publica
 * los campos desaparecen en lugar de quedarse deshabilitados prometiendo algo.
 *
 * EL BACKEND ES LA AUTORIDAD. Este módulo no decide quién publica ni a quién
 * llega; si el servidor responde 403 se muestra tal cual. Ocultar un botón es
 * cortesía, no seguridad.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  InternalControlGuard,
  type InternalContext,
} from "../components/InternalControlGuard";
import {
  type Announcement,
  type AnnouncementPreview,
  type AnnouncementStats,
  type AudienceKind,
  AUDIENCE_LABELS,
  PRIORITY_LABELS,
  announcementStats,
  cancelAnnouncement,
  createDraft,
  describeAudience,
  listAnnouncements,
  previewAnnouncement,
  publishAnnouncement,
  updateDraft,
} from "../../lib/communications";

const STATUS_LABELS: Record<string, string> = {
  draft: "Borrador",
  published: "Publicado",
  cancelled: "Descartado",
};

const AUDIENCE_KINDS: AudienceKind[] = [
  "all_company",
  "branch",
  "role",
  "capability",
  "user",
];

type Tab = "draft" | "published";

function Panel({ ctx }: { ctx: InternalContext }) {
  const slug = ctx.dashboard?.company?.slug ?? null;

  const [tab, setTab] = useState<Tab>("draft");
  const [rows, setRows] = useState<Announcement[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [selected, setSelected] = useState<Announcement | null>(null);

  const load = useCallback(async () => {
    if (!slug) return;
    try {
      const data = await listAnnouncements(slug, tab);
      setRows(data.results);
      setError(null);
    } catch (err) {
      // Un fallo se dice. Una lista vacía y un error de red se ven igual en
      // pantalla y no son lo mismo en absoluto.
      setRows([]);
      setError(err instanceof Error ? err.message : "No se pudo cargar.");
    }
  }, [slug, tab]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!slug) {
    return <p className="text-sm text-muted">Selecciona una empresa.</p>;
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Comunicados</h1>
          <p className="text-xs text-muted">
            Mensajes internos para el personal de {ctx.dashboard?.company?.name}.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setSelected(null);
            setComposing(true);
          }}
          className="rounded-lg bg-info-solid px-3 py-1.5 text-xs font-medium text-on-status hover:opacity-90"
        >
          Nuevo comunicado
        </button>
      </header>

      <nav className="flex gap-1 border-b border-bd-border">
        {(["draft", "published"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-xs ${
              tab === t
                ? "border-b-2 border-sky-400 text-foreground"
                : "text-muted hover:text-foreground/85"
            }`}
          >
            {t === "draft" ? "Borradores" : "Publicados"}
          </button>
        ))}
      </nav>

      {error ? (
        <p className="rounded-lg border border-danger-border bg-danger-surface px-3 py-2 text-xs text-danger">
          {error}
        </p>
      ) : null}

      {composing ? (
        <Composer
          slug={slug}
          onClose={() => setComposing(false)}
          onDone={() => {
            setComposing(false);
            setTab("draft");
            void load();
          }}
        />
      ) : null}

      {selected ? (
        <Detail
          slug={slug}
          announcement={selected}
          onClose={() => setSelected(null)}
          onChanged={() => {
            setSelected(null);
            void load();
          }}
        />
      ) : null}

      {rows === null ? (
        <p className="text-xs text-muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted">
          {tab === "draft" ? "No hay borradores." : "Todavía no se publicó nada."}
        </p>
      ) : (
        <ul className="divide-y divide-bd-border rounded-xl border border-bd-border">
          {rows.map((a) => (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => setSelected(a)}
                className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-3 text-left hover:bg-surface"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-foreground">{a.title}</p>
                  <p className="mt-0.5 text-[11px] text-muted">
                    {a.author} · {STATUS_LABELS[a.status] ?? a.status} ·{" "}
                    {PRIORITY_LABELS[a.priority] ?? a.priority}
                    {a.published_at
                      ? ` · ${new Date(a.published_at).toLocaleString("es-PE")}`
                      : ""}
                  </p>
                </div>
                {a.status === "published" ? (
                  <span className="shrink-0 text-[11px] text-muted">
                    {a.recipient_count} destinatarios
                  </span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Composer({
  slug,
  onClose,
  onDone,
}: {
  slug: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [priority, setPriority] = useState("info");
  const [kind, setKind] = useState<AudienceKind>("all_company");
  const [target, setTarget] = useState("");
  const [preview, setPreview] = useState<AnnouncementPreview | null>(null);
  const [draft, setDraft] = useState<Announcement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsTarget = kind !== "all_company";
  const audience = useMemo(() => {
    const entry: Record<string, unknown> = { kind };
    if (kind === "branch") entry.branch_id = Number(target) || 0;
    if (kind === "role") entry.role_id = Number(target) || 0;
    if (kind === "capability") entry.capability_code = target.trim();
    if (kind === "user") entry.user_id = Number(target) || 0;
    return [entry];
  }, [kind, target]);

  async function buildPreview() {
    setBusy(true);
    setError(null);
    try {
      const created =
        draft ?? (await createDraft(slug, { title, body, priority }));
      await updateDraft(slug, created.id, { audience });
      const p = await previewAnnouncement(slug, created.id);
      setDraft(created);
      setPreview(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo calcular.");
    } finally {
      setBusy(false);
    }
  }

  async function confirmPublish() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      await publishAnnouncement(slug, draft.id);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo publicar.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
      <h2 className="text-sm font-medium text-foreground">Nuevo comunicado</h2>

      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        maxLength={140}
        placeholder="Título"
        className="w-full rounded-lg border border-bd-border bg-transparent px-3 py-2 text-sm text-foreground placeholder:text-muted"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        maxLength={4000}
        rows={6}
        placeholder="Cuerpo del mensaje"
        className="w-full rounded-lg border border-bd-border bg-transparent px-3 py-2 text-sm text-foreground placeholder:text-muted"
      />
      <p className="text-[11px] text-muted">{body.length} / 4000</p>

      <div className="flex flex-wrap gap-2">
        <select
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
          className="rounded-lg border border-bd-border bg-transparent px-2 py-1.5 text-xs text-foreground"
        >
          {Object.entries(PRIORITY_LABELS).map(([v, label]) => (
            <option key={v} value={v} className="bg-surface">
              {label}
            </option>
          ))}
        </select>
        <select
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as AudienceKind);
            setTarget("");
            setPreview(null);
          }}
          className="rounded-lg border border-bd-border bg-transparent px-2 py-1.5 text-xs text-foreground"
        >
          {AUDIENCE_KINDS.map((k) => (
            <option key={k} value={k} className="bg-surface">
              {AUDIENCE_LABELS[k]}
            </option>
          ))}
        </select>
        {needsTarget ? (
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder={
              kind === "capability" ? "código de capacidad" : "identificador"
            }
            className="rounded-lg border border-bd-border bg-transparent px-2 py-1.5 text-xs text-foreground placeholder:text-muted"
          />
        ) : null}
      </div>

      {error ? (
        <p className="rounded-lg border border-danger-border bg-danger-surface px-3 py-2 text-xs text-danger">
          {error}
        </p>
      ) : null}

      {preview ? (
        <div className="space-y-2 rounded-lg border border-warning-border bg-warning-surface p-3">
          <p className="text-xs font-medium text-warning">
            Confirma antes de publicar
          </p>
          <dl className="space-y-1 text-[11px] text-foreground/85">
            <div className="flex justify-between gap-3">
              <dt className="text-muted">Título</dt>
              <dd className="truncate">{title}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted">Prioridad</dt>
              <dd>{PRIORITY_LABELS[priority]}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted">Audiencia</dt>
              <dd>{AUDIENCE_LABELS[kind]}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted">Empresas</dt>
              <dd>{preview.company_count}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-muted">Destinatarios</dt>
              <dd>{preview.recipient_count}</dd>
            </div>
          </dl>
          <p className="text-[11px] text-muted">
            Una vez publicado no se puede editar ni retirar. Una corrección es
            un comunicado nuevo.
          </p>
          <button
            type="button"
            disabled={busy || preview.recipient_count === 0}
            onClick={() => void confirmPublish()}
            className="rounded-lg bg-warning-surface px-3 py-1.5 text-xs font-medium text-background disabled:opacity-40"
          >
            Publicar a {preview.recipient_count} personas
          </button>
        </div>
      ) : null}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || !title.trim() || !body.trim()}
          onClick={() => void buildPreview()}
          className="rounded-lg border border-bd-border px-3 py-1.5 text-xs text-foreground disabled:opacity-40"
        >
          {preview ? "Recalcular" : "Revisar destinatarios"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-3 py-1.5 text-xs text-muted hover:text-foreground/85"
        >
          Cancelar
        </button>
      </div>
    </section>
  );
}

function Detail({
  slug,
  announcement,
  onClose,
  onChanged,
}: {
  slug: string;
  announcement: Announcement;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [stats, setStats] = useState<AnnouncementStats | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (announcement.status !== "published") return;
    void announcementStats(slug, announcement.id)
      .then(setStats)
      .catch(() => setStats(null));
  }, [slug, announcement]);

  async function discard() {
    setBusy(true);
    setError(null);
    try {
      await cancelAnnouncement(slug, announcement.id);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo descartar.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-sm font-medium text-foreground">
          {announcement.title}
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-muted hover:text-foreground/85"
        >
          Cerrar
        </button>
      </div>

      <p className="whitespace-pre-wrap text-sm text-foreground/85">
        {announcement.body}
      </p>

      <p className="text-[11px] text-muted">
        {describeAudience(announcement.audience)}
      </p>

      {stats ? (
        <div className="grid grid-cols-4 gap-2 rounded-lg border border-bd-border p-2 text-center">
          {[
            ["Destinatarios", stats.recipients],
            ["Leídos", stats.read],
            ["Sin leer", stats.unread],
            ["% leído", `${stats.read_pct}%`],
          ].map(([label, value]) => (
            <div key={String(label)}>
              <p className="text-sm text-foreground">{value}</p>
              <p className="text-[10px] text-muted">{label}</p>
            </div>
          ))}
        </div>
      ) : null}

      {error ? (
        <p className="text-xs text-danger">{error}</p>
      ) : null}

      {announcement.status === "draft" ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => void discard()}
          className="rounded-lg border border-bd-border px-3 py-1.5 text-xs text-muted disabled:opacity-40"
        >
          Descartar borrador
        </button>
      ) : (
        <p className="text-[11px] text-muted">
          Publicado. No se edita ni se retira: una corrección es un comunicado
          nuevo.
        </p>
      )}
    </section>
  );
}

export default function CommunicationsPage() {
  return (
    <InternalControlGuard>
      {(ctx: InternalContext) => <Panel ctx={ctx} />}
    </InternalControlGuard>
  );
}
