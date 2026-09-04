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
      {entries.map(([k, v]) => (
        <div key={k} className="flex gap-1">
          <dt className="text-muted">{k}:</dt>
          <dd className="truncate max-w-[120px] text-muted">
            {String(v).slice(0, 60)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function AuditLogTable({ logs }: Props) {
  if (logs.length === 0) {
    return (
      <div className="rounded-xl border border-bd-border bg-surface py-12 text-center text-muted">
        No hay registros de auditoría.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-bd-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-bd-border bg-surface text-left text-xs font-semibold uppercase tracking-wider text-muted">
            <th className="px-4 py-3">Fecha</th>
            <th className="px-4 py-3">Actor</th>
            <th className="px-4 py-3">Acción</th>
            <th className="px-4 py-3">Objetivo</th>
            <th className="px-4 py-3">Detalle</th>
            <th className="px-4 py-3">IP</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log, i) => (
            <tr
              key={log.id}
              className={`border-b border-bd-border transition hover:bg-surface ${
                i % 2 === 0 ? "" : "bg-surface"
              }`}
            >
              <td className="px-4 py-3 text-xs text-muted whitespace-nowrap">
                {formatAdminDate(log.created_at)}
              </td>
              <td className="px-4 py-3 font-medium text-foreground/85">
                {log.actor ?? <span className="text-muted">—</span>}
              </td>
              <td className="px-4 py-3">
                <span className="rounded border border-bd-border px-1.5 py-0.5 text-[10px] font-medium text-foreground/85">
                  {actionLabel(log.action)}
                </span>
              </td>
              <td className="px-4 py-3 text-xs text-muted">
                {log.target_type}
                {log.target_id && (
                  <span className="ml-1 text-muted">#{log.target_id}</span>
                )}
              </td>
              <td className="px-4 py-3">
                <MetadataSummary metadata={log.metadata} />
              </td>
              <td className="px-4 py-3 text-xs text-muted">
                {log.ip_address ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
