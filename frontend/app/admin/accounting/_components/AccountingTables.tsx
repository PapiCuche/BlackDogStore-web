"use client";

/**
 * The three tables. Each lives in its own horizontal scroller, so a narrow
 * screen scrolls the table, never the page. Every label and state comes from
 * the server; a code without a local word is shown as the code itself rather
 * than guessed.
 */
import Link from "next/link";
import {
  CHANNEL_LABELS,
  COMMERCIAL_STATE_LABELS,
  EVENT_LABELS,
  SEVERITY_LABELS,
  money,
  type FiscalAnswer,
  type OperationRow,
  type OperationsAnswer,
  type PendingAnswer,
  type PendingRow,
} from "../../lib/accounting-api";

const th = "px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-widest text-muted";
const td = "px-3 py-2.5 align-top";

function when(value: string | null, timeZone: string) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  try {
    return date.toLocaleString("es-PE", { timeZone, dateStyle: "short", timeStyle: "short" });
  } catch {
    return date.toLocaleString("es-PE");
  }
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-xl border border-bd-border bg-surface px-5 py-8 text-center text-sm text-muted">{children}</p>
  );
}

function Pill({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "warning" | "danger" | "success" }) {
  const tones = {
    muted: "border-bd-border text-muted",
    warning: "border-warning-border bg-warning-surface text-warning",
    danger: "border-danger-border bg-danger-surface text-danger",
    success: "border-bd-border text-success",
  };
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${tones[tone]}`}>
      {children}
    </span>
  );
}

function OperationLink({ row }: { row: Pick<OperationRow, "kind" | "order_id" | "repair_order_id" | "key"> }) {
  if (row.kind === "sale" && row.order_id) {
    return <Link href={`/admin/orders/${row.order_id}`} className="font-mono text-xs underline-offset-2 hover:underline">{row.key}</Link>;
  }
  return <span className="font-mono text-xs">{row.key}</span>;
}

export function OperationsTable({ answer }: { answer: OperationsAnswer }) {
  if (!answer.results.length) return <Empty>No hay operaciones en este período con estos filtros.</Empty>;
  const tz = answer.period.timezone;
  return (
    <div className="overflow-x-auto rounded-xl border border-bd-border bg-surface" tabIndex={0} aria-label="Tabla de operaciones">
      <table className="w-full min-w-[56rem] text-sm">
        <caption className="sr-only">Operaciones del período, una fila por operación y otra por cada ajuste</caption>
        <thead className="border-b border-bd-border">
          <tr>
            <th scope="col" className={th}>Fecha</th>
            <th scope="col" className={th}>Operación</th>
            <th scope="col" className={th}>Canal · sucursal</th>
            <th scope="col" className={th}>Cliente</th>
            <th scope="col" className={th}>Documento</th>
            <th scope="col" className={th}>Estado</th>
            <th scope="col" className={`${th} text-right`}>Importe</th>
          </tr>
        </thead>
        <tbody>
          {answer.results.map((row) => (
            <tr key={`${row.key}-${row.row_type}-${row.event}-${row.date}`} className="border-b border-bd-border/70 last:border-0">
              <td className={`${td} whitespace-nowrap text-muted`}>{when(row.date, tz)}</td>
              <td className={td}>
                <OperationLink row={row} />
                <div className="mt-1 flex flex-wrap gap-1">
                  <Pill>{EVENT_LABELS[row.event] ?? row.event}</Pill>
                  {row.row_type === "adjustment" ? <Pill tone="warning">Ajuste</Pill> : null}
                  {row.excluded_from_income ? <Pill tone="warning">Fuera del ingreso (prueba)</Pill> : null}
                </div>
              </td>
              <td className={td}>
                {CHANNEL_LABELS[row.channel] ?? row.channel}
                <div className="text-xs text-muted">{row.branch?.name ?? "Sin sucursal"}</div>
              </td>
              <td className={td}>
                {row.customer.name || "—"}
                {row.customer.document_number ? (
                  <div className="font-mono text-xs text-muted">{row.customer.document_number}</div>
                ) : null}
              </td>
              <td className={td}>
                {row.primary_document ? (
                  <span>
                    {row.primary_document.kind === "nota_interna" ? "Nota interna " : ""}
                    <span className="font-mono text-xs">{row.primary_document.number}</span>
                  </span>
                ) : (
                  <Pill tone="danger">Sin documento</Pill>
                )}
                {row.documents.length > 1 ? (
                  <div className="text-xs text-muted">{row.documents.length} documentos</div>
                ) : null}
              </td>
              <td className={td}>
                {COMMERCIAL_STATE_LABELS[row.commercial_state] ?? row.commercial_state}
                {row.flags.length ? (
                  <div className="text-xs text-muted">{row.flags.length} aviso(s) en la vista C</div>
                ) : null}
              </td>
              <td className={`${td} text-right font-mono tabular-nums`}>{money(row.amount, row.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FiscalTable({ answer }: { answer: FiscalAnswer }) {
  const testing = answer.environment === "beta";
  return (
    <div className="space-y-3">
      {testing ? (
        <p role="note" className="rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning">
          PRUEBAS (BETA): estos documentos no tienen validez tributaria y no se suman a producción.
        </p>
      ) : null}
      <p className="text-xs text-muted">
        La firma de las constancias (CDR) no está verificada como de SUNAT: se comprueba su integridad, no su autenticidad.
      </p>
      {!answer.results.length ? (
        <Empty>No hay comprobantes emitidos en este período con estos filtros.</Empty>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-bd-border bg-surface" tabIndex={0} aria-label="Tabla de comprobantes">
          <table className="w-full min-w-[56rem] text-sm">
            <caption className="sr-only">Comprobantes electrónicos del período y su estado ante SUNAT</caption>
            <thead className="border-b border-bd-border">
              <tr>
                <th scope="col" className={th}>Emisión</th>
                <th scope="col" className={th}>Comprobante</th>
                <th scope="col" className={th}>Cliente</th>
                <th scope="col" className={th}>Estado ante SUNAT</th>
                <th scope="col" className={th}>Constancia</th>
                <th scope="col" className={`${th} text-right`}>Total</th>
              </tr>
            </thead>
            <tbody>
              {answer.results.map((row) => (
                <tr key={row.id} className="border-b border-bd-border/70 last:border-0">
                  <td className={`${td} whitespace-nowrap text-muted`}>{when(row.issued_at, answer.period.timezone)}</td>
                  <td className={td}>
                    <div>{row.type_label}</div>
                    <div className="font-mono text-xs">{row.document_id}</div>
                    {row.original ? <div className="text-xs text-muted">sobre {row.original}</div> : null}
                    {row.test_environment ? <Pill tone="warning">PRUEBAS</Pill> : null}
                  </td>
                  <td className={td}>
                    {row.customer.name || "—"}
                    <div className="font-mono text-xs text-muted">{row.customer.document_number}</div>
                  </td>
                  <td className={td}>
                    <Pill tone={row.state.code === "rejected" ? "danger" : row.state.settled ? "success" : "warning"}>
                      {row.state.label}
                    </Pill>
                    {row.summary ? <div className="mt-1 font-mono text-xs text-muted">{row.summary.identifier}</div> : null}
                    {row.sunat.code ? <div className="text-xs text-muted">Código {row.sunat.code}</div> : null}
                  </td>
                  <td className={td}>
                    {row.cdr.present ? (
                      <>
                        <span>Con CDR</span>
                        {row.cdr.hash_ok === false ? (
                          <div className="text-xs text-danger">La huella guardada no coincide</div>
                        ) : null}
                      </>
                    ) : (
                      <span className="text-muted">Sin CDR</span>
                    )}
                  </td>
                  <td className={`${td} text-right font-mono tabular-nums`}>{money(row.total, row.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const SEVERITY_TONE: Record<PendingRow["severity"], "danger" | "warning" | "muted"> = {
  regularizar: "danger",
  revisar: "warning",
  info: "muted",
};

export function PendingTable({ answer }: { answer: PendingAnswer }) {
  if (!answer.results.length) return <Empty>No hay pendientes ni inconsistencias en este período.</Empty>;
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">
        Una nota de venta interna no tiene validez tributaria: la venta sigue sin comprobante. Esta vista describe lo que
        falta; se regulariza desde el pedido, con sus propios permisos.
      </p>
      <ul className="flex flex-wrap gap-2" aria-label="Avisos por código">
        {Object.entries(answer.counts).map(([code, n]) => (
          <li key={code}><Pill>{code} · {n}</Pill></li>
        ))}
      </ul>
      <div className="overflow-x-auto rounded-xl border border-bd-border bg-surface" tabIndex={0} aria-label="Tabla de pendientes">
        <table className="w-full min-w-[48rem] text-sm">
          <caption className="sr-only">Notas internas, pendientes e inconsistencias del período</caption>
          <thead className="border-b border-bd-border">
            <tr>
              <th scope="col" className={th}>Gravedad</th>
              <th scope="col" className={th}>Qué falta</th>
              <th scope="col" className={th}>Operación</th>
              <th scope="col" className={th}>Documento</th>
              <th scope="col" className={th}>Fecha</th>
            </tr>
          </thead>
          <tbody>
            {answer.results.map((row) => (
              <tr key={`${row.key}-${row.code}`} className="border-b border-bd-border/70 last:border-0">
                <td className={td}><Pill tone={SEVERITY_TONE[row.severity]}>{SEVERITY_LABELS[row.severity] ?? row.severity}</Pill></td>
                <td className={td}>
                  {row.message}
                  <div className="font-mono text-[11px] text-muted">{row.code}</div>
                </td>
                <td className={td}>
                  <OperationLink row={{ ...row, order_id: row.order_id ?? undefined, repair_order_id: row.repair_order_id ?? undefined }} />
                  <div className="text-xs text-muted">{row.branch?.name ?? "Sin sucursal"}</div>
                </td>
                <td className={td}><span className="font-mono text-xs">{row.document ?? "—"}</span></td>
                <td className={`${td} whitespace-nowrap text-muted`}>{when(row.date, answer.period.timezone)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
