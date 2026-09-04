"use client";

/**
 * M12C — un comunicado, visto por quien lo recibió.
 *
 * Aquí llega la campana. La notificación trajo `target_type` y `target_id`, no
 * una URL, así que esta ruta la construyó el cliente — y por eso el servidor
 * vuelve a comprobar todo al abrirla: que el comunicado exista, que esté
 * publicado y que a esta persona se le escribiera en ESTA empresa.
 *
 * NO PIDE CAPABILITY, y no es un descuido. Leer un mensaje dirigido a ti no es
 * una autoridad; la bandeja de M12B tampoco pide permiso. Lo que sí hace falta
 * es haber sido destinatario, y la prueba de eso es la fila que se escribió al
 * publicar. Quien ganó el rol la semana pasada no puede leer el comunicado del
 * mes pasado, porque nunca se le escribió nada.
 *
 * El cuerpo se pinta como TEXTO. Nada de `dangerouslySetInnerHTML`: el backend
 * guarda lo que se escribió, tal cual, y quien lo muestra es responsable de
 * tratarlo como texto.
 */

import Link from "next/link";
import { use, useEffect, useState } from "react";
import {
  InternalControlGuard,
  type InternalContext,
} from "../../components/InternalControlGuard";
import {
  type Announcement,
  PRIORITY_LABELS,
  readAnnouncement,
} from "../../../lib/communications";

function View({ ctx, id }: { ctx: InternalContext; id: string }) {
  const slug = ctx.dashboard?.company?.slug ?? null;
  const [announcement, setAnnouncement] = useState<Announcement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    void readAnnouncement(slug, Number(id))
      .then((a) => {
        if (!cancelled) setAnnouncement(a);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "No se pudo abrir el comunicado.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, id]);

  if (error) {
    return (
      <div className="space-y-3">
        <p className="rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
        <Link
          href="/admin/notifications"
          className="text-xs text-muted hover:text-foreground/85"
        >
          Volver a la bandeja
        </Link>
      </div>
    );
  }

  if (!announcement) {
    return <p className="text-xs text-muted">Cargando…</p>;
  }

  return (
    <article className="space-y-4">
      <header className="space-y-1">
        <span className="inline-block rounded bg-sky-500/15 px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-sky-300">
          Comunicado
        </span>
        <h1 className="text-lg font-semibold text-foreground">
          {announcement.title}
        </h1>
        <p className="text-[11px] text-muted">
          {announcement.author}
          {announcement.published_at
            ? ` · ${new Date(announcement.published_at).toLocaleString("es-PE")}`
            : ""}
          {" · "}
          {PRIORITY_LABELS[announcement.priority] ?? announcement.priority}
        </p>
      </header>

      <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/85">
        {announcement.body}
      </p>

      <Link
        href="/admin/notifications"
        className="inline-block text-xs text-muted hover:text-foreground/85"
      >
        Volver a la bandeja
      </Link>
    </article>
  );
}

export default function AnnouncementPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <InternalControlGuard>
      {(ctx: InternalContext) => <View ctx={ctx} id={id} />}
    </InternalControlGuard>
  );
}
