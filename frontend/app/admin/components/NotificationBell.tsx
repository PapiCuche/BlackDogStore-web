"use client";

/**
 * The unread badge and its preview — M12B.
 *
 * READS ONE NUMBER, NOT A PAGE. The badge asks `unread-count/`, which answers
 * with a COUNT; downloading twenty rows to display "3" would make every screen
 * in the panel pay for a number.
 *
 * NO POLLING LOOP. There is no WebSocket or SSE in this project and M12B does
 * not introduce one. The count is fetched when the shell mounts and again when
 * the tab regains focus — which is when a person actually looks at it. A timer
 * hitting the server every few seconds for every open panel is a cost with no
 * matching benefit.
 *
 * THE LIST IS A PREVIEW, NOT AUTHORISATION. Following one goes to the module
 * that owns the thing, and that module checks tenant, capability and ownership
 * again. A notification is an intention to navigate.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";

type Notification = {
  id: number;
  title: string;
  body: string;
  priority: string;
  source?: string;
  target_type: string;
  target_id: number | null;
  read_at: string | null;
  created_at: string;
};

/**
 * Where a notification points, built from structured fields.
 *
 * The server never sends a URL. It sends what the thing IS, and the client
 * decides how to reach it — so a stored route can never become a redirect
 * somebody chose.
 */
export function targetHref(n: Pick<Notification, "target_type" | "target_id">) {
  if (!n.target_id) return null;
  if (n.target_type === "repair_order") return `/admin/service/orders/${n.target_id}`;
  if (n.target_type === "order") return `/admin/orders`;
  // M12C. La bandeja sigue siendo UNA. Un comunicado no estrena badge ni
  // buzón propio: llega como cualquier otra notificación y su `target_type`
  // decide a dónde lleva.
  if (n.target_type === "announcement") return `/admin/communications/${n.target_id}`;
  return null;
}

/** Etiqueta visible cuando el aviso lo escribió una persona, no un evento. */
export function sourceLabel(n: Pick<Notification, "source">) {
  return n.source === "announcement" ? "Comunicado" : null;
}

export function NotificationBell({ slug }: { slug: string | null }) {
  const [unread, setUnread] = useState<number | null>(null);
  const [items, setItems] = useState<Notification[] | null>(null);
  const [open, setOpen] = useState(false);

  const base = slug ? `${API_BASE}/v1/internal/${slug}/notifications` : null;

  const loadCount = useCallback(async () => {
    if (!base) return;
    try {
      const res = await fetchWithAuth(`${base}/unread-count/`);
      if (!res.ok) return;
      setUnread((await res.json()).unread ?? 0);
    } catch {
      // A badge that cannot load is not an error worth interrupting anybody
      // with. It stays unknown, which is honest, rather than showing 0.
    }
  }, [base]);

  useEffect(() => {
    void loadCount();
    const onFocus = () => void loadCount();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [loadCount]);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || !base) return;
    setItems(null);
    try {
      const res = await fetchWithAuth(`${base}/?page_size=6`);
      setItems(res.ok ? (await res.json()).results ?? [] : []);
    } catch {
      setItems([]);
    }
  }

  async function markRead(id: number) {
    if (!base) return;
    await fetchWithAuth(`${base}/${id}/read/`, { method: "POST" });
    setItems((current) =>
      current?.map((n) => (n.id === id ? { ...n, read_at: new Date().toISOString() } : n)) ?? null,
    );
    void loadCount();
  }

  if (!slug) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => void toggle()}
        aria-label="Notificaciones"
        className="relative rounded-lg border border-bd-border p-2 text-muted transition hover:text-foreground"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9" />
        </svg>
        {/* `null` means "not loaded yet" and shows nothing. Rendering 0 while
            loading would state a fact the client does not have. */}
        {unread !== null && unread > 0 ? (
          <span className="absolute -right-1 -top-1 min-w-[18px] rounded-full bg-red-500 px-1 text-[10px] font-semibold leading-[18px] text-on-status">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="absolute right-0 z-50 mt-2 w-80 rounded-xl border border-bd-border bg-surface p-2 shadow-2xl">
          <div className="flex items-center justify-between px-2 py-1.5">
            <span className="text-xs font-semibold text-foreground/85">Notificaciones</span>
            <Link
              href="/admin/notifications"
              onClick={() => setOpen(false)}
              className="text-[11px] text-muted hover:text-foreground"
            >
              Ver todas
            </Link>
          </div>

          {items === null ? (
            <p className="px-2 py-6 text-center text-xs text-muted">Cargando…</p>
          ) : items.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-muted">No tienes notificaciones.</p>
          ) : (
            <ul className="max-h-80 overflow-y-auto">
              {items.map((n) => {
                const href = targetHref(n);
                const inner = (
                  <div className={`rounded-lg px-2 py-2 ${n.read_at ? "opacity-60" : "bg-surface"}`}>
                    {sourceLabel(n) ? (
                      <span className="mb-0.5 inline-block rounded bg-info-surface px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-info">
                        {sourceLabel(n)}
                      </span>
                    ) : null}
                    <p className="text-xs font-medium text-foreground">{n.title}</p>
                    {n.body ? <p className="mt-0.5 text-[11px] text-muted">{n.body}</p> : null}
                  </div>
                );
                return (
                  <li key={n.id}>
                    {href ? (
                      <Link href={href} onClick={() => { setOpen(false); void markRead(n.id); }}>
                        {inner}
                      </Link>
                    ) : (
                      <button type="button" className="w-full text-left" onClick={() => void markRead(n.id)}>
                        {inner}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
