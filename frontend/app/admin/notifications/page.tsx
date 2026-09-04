"use client";

/**
 * The full internal inbox — M12B.
 *
 * NO CAPABILITY GATE, deliberately. This is not administrative data about
 * other people; it is what the platform has told THIS person. Requiring a
 * permission to read your own messages would let an administrator stop
 * somebody seeing their own assignment notice.
 *
 * Every row still links into the module that owns the thing, and that module
 * re-checks tenant, capability and ownership. The inbox is never a way around
 * a permission that was taken away.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  InternalControlGuard,
  type InternalContext,
} from "../components/InternalControlGuard";
import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import { targetHref } from "../components/NotificationBell";

type Row = {
  id: number;
  title: string;
  body: string;
  priority: string;
  target_type: string;
  target_id: number | null;
  read_at: string | null;
  created_at: string;
};

const PRIORITY_STYLES: Record<string, string> = {
  info: "border-bd-border text-muted",
  action: "border-amber-500/30 text-amber-300/80",
  warning: "border-orange-500/30 text-orange-300/80",
  critical: "border-red-500/40 text-red-300",
};

function Inbox({ ctx }: { ctx: InternalContext }) {
  const slug = ctx.dashboard?.company?.slug ?? null;
  const base = slug ? `${API_BASE}/v1/internal/${slug}/notifications` : null;

  const [rows, setRows] = useState<Row[] | null>(null);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!base) return;
    setError(null);
    try {
      const qs = new URLSearchParams({ page: String(page) });
      if (unreadOnly) qs.set("unread", "true");
      const res = await fetchWithAuth(`${base}/?${qs}`);
      if (!res.ok) throw new Error("No se pudo cargar la bandeja.");
      const data = await res.json();
      setRows(data.results ?? []);
      setCount(data.count ?? 0);
    } catch (e: unknown) {
      // An explicit error state, not an empty list. "Nothing here" and "we
      // could not ask" are different facts and must not look the same.
      setRows(null);
      setError(e instanceof Error ? e.message : "No se pudo cargar la bandeja.");
    }
  }, [base, page, unreadOnly]);

  useEffect(() => { void load(); }, [load]);

  async function markRead(id: number) {
    if (!base) return;
    await fetchWithAuth(`${base}/${id}/read/`, { method: "POST" });
    await load();
  }

  async function markAll() {
    if (!base) return;
    setBusy(true);
    try {
      await fetchWithAuth(`${base}/read-all/`, { method: "POST" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (!slug) {
    return (
      <p className="text-sm text-muted">
        Selecciona una empresa para ver tus notificaciones.
      </p>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Notificaciones</h1>
          <p className="mt-1 text-sm text-muted">
            Lo que la plataforma te ha comunicado en {ctx.dashboard?.company?.name}.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void markAll()}
          disabled={busy}
          className="rounded-lg border border-bd-border px-4 py-2 text-sm text-foreground/85 hover:text-foreground disabled:opacity-40"
        >
          Marcar todas como leídas
        </button>
      </div>

      <div className="flex gap-2">
        {([
          { value: false, label: "Todas" },
          { value: true, label: "No leídas" },
        ] as const).map((option) => (
          <button
            key={String(option.value)}
            type="button"
            onClick={() => { setUnreadOnly(option.value); setPage(1); }}
            className={`rounded-lg border px-4 py-2 text-sm ${
              unreadOnly === option.value
                ? "border-bd-border bg-surface-2 text-foreground"
                : "border-bd-border text-muted hover:text-foreground/85"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {error ? (
        <p className="rounded-xl border border-red-500/20 bg-red-500/[0.05] px-4 py-3 text-sm text-red-300">
          {error}
        </p>
      ) : rows === null ? (
        <p className="text-sm text-muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="rounded-xl border border-bd-border bg-surface px-4 py-8 text-center text-sm text-muted">
          {unreadOnly ? "No tienes notificaciones sin leer." : "No tienes notificaciones."}
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((row) => {
            const href = targetHref(row);
            return (
              <li
                key={row.id}
                className={`rounded-xl border px-4 py-3 ${
                  row.read_at
                    ? "border-bd-border bg-transparent opacity-70"
                    : "border-bd-border bg-surface"
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-foreground">{row.title}</p>
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] ${PRIORITY_STYLES[row.priority] ?? PRIORITY_STYLES.info}`}>
                        {row.priority}
                      </span>
                    </div>
                    {row.body ? <p className="mt-1 text-xs text-muted">{row.body}</p> : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {href ? (
                      <Link
                        href={href}
                        onClick={() => void markRead(row.id)}
                        className="rounded-lg border border-bd-border px-3 py-1.5 text-xs text-foreground/85 hover:text-foreground"
                      >
                        Abrir
                      </Link>
                    ) : null}
                    {!row.read_at ? (
                      <button
                        type="button"
                        onClick={() => void markRead(row.id)}
                        className="rounded-lg border border-bd-border px-3 py-1.5 text-xs text-muted hover:text-foreground"
                      >
                        Marcar leída
                      </button>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {count > 20 ? (
        <div className="flex items-center justify-between text-xs text-muted">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="rounded-lg border border-bd-border px-3 py-1.5 disabled:opacity-30"
          >
            Anterior
          </button>
          <span>{count} en total</span>
          <button
            type="button"
            disabled={page * 20 >= count}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-lg border border-bd-border px-3 py-1.5 disabled:opacity-30"
          >
            Siguiente
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default function NotificationsPage() {
  return <InternalControlGuard>{(ctx) => <Inbox ctx={ctx} />}</InternalControlGuard>;
}
