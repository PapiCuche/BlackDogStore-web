"use client";

import { actionLabel, formatAdminDate, type AuditLogEntry } from "../../lib/admin";

type Props = {
  logs: AuditLogEntry[];
};

function MetadataSummary({ metadata }: { metadata: Record<string, unknown> }) {
  const entries = Object.entries(metadata).slice(0, 4);
  if (entries.length === 0) return <span className="text-muted">—</span>;

  return (
    <dl className="space-y-0.5 text-[10px]">
      {entries.map(([key, value]) => (
        <div key={key} className="flex gap-1">
          <dt className="text-muted">{key}:</dt>
          <dd className="max-w-[140px] truncate text-foreground/70">
            {String(value).slice(0, 60)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function AuditLogTable({ logs }: Props) {
  if (logs.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-bd-border py-12 text-center text-sm text-muted">
        No hay registros de auditoría.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-bd-border bg-surface">
      <table className="w-full min-w-[58rem] text-sm">
        <thead>
          <tr className="border-b border-bd-border text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-muted">
            <th className="px-4 py-3">Fecha</th>
            <th className="px-4 py-3">Actor</th>
            <th className="px-4 py-3">Acción</th>
            <th className="px-4 py-3">Objetivo</th>
            <th className="px-4 py-3">Detalle</th>
            <th className="px-4 py-3">IP</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <tr key={log.id} className="border-b border-bd-border/70 transition last:border-0 hover:bg-foreground/[0.025]">
              <td className="whitespace-nowrap px-4 py-3 text-xs text-muted">{formatAdminDate(log.created_at)}</td>
              <td className="px-4 py-3 font-medium text-foreground">{log.actor ?? <span className="text-muted">—</span>}</td>
              <td className="px-4 py-3">
                <span className="rounded-full border border-bd-border px-2.5 py-1 text-[10px] font-semibold text-foreground">
                  {actionLabel(log.action)}
                </span>
              </td>
              <td className="px-4 py-3 text-xs text-muted">
                {log.target_type}
                {log.target_id ? <span className="ml-1">#{log.target_id}</span> : null}
              </td>
              <td className="px-4 py-3"><MetadataSummary metadata={log.metadata} /></td>
              <td className="px-4 py-3 text-xs text-muted">{log.ip_address ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
