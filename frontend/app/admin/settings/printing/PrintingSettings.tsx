"use client";

/**
 * Impresoras del local, sus agentes y la cola reciente.
 *
 * UNA IMPRESORA ES DE UN LOCAL y vive en su red: aquí se guarda su dirección
 * ahí dentro. Quien llega a ella es el agente —un programa que se deja encendido
 * en el local—, no este panel ni el servidor.
 *
 * EL TOKEN DEL AGENTE SE ENSEÑA UNA VEZ. El servidor sólo guarda su huella; si
 * se pierde, se revoca el agente y se crea otro.
 */

import { useEffect, useState } from "react";
import {
  PRINT_JOB_STATUS_LABEL, PrintingFieldError, createPrintAgent, createPrinter,
  deactivatePrinter, fetchPrintAgents, fetchPrintJobs, fetchPrinters, retryPrintJob,
  revokePrintAgent, type PrintAgent, type PrintJob, type Printer,
} from "../../lib/printing-api";

type Branch = { id: number; name: string };

const INPUT =
  "min-h-11 w-full rounded-lg border border-bd-border bg-background px-3 text-sm text-foreground";
const LABEL = "block text-xs font-semibold uppercase tracking-wider text-muted";
const CARD = "rounded-xl border border-bd-border bg-surface p-5 sm:p-6";
const KIND: Record<string, string> = {
  fiscal_ticket: "Comprobante electrónico", sales_note_ticket: "Nota de venta",
};

export function PrintingSettings({
  companyId, branches, canManage,
}: {
  companyId: number | null;
  branches: Branch[];
  canManage: boolean;
}) {
  const [printers, setPrinters] = useState<Printer[] | null>(null);
  const [agents, setAgents] = useState<PrintAgent[]>([]);
  const [jobs, setJobs] = useState<PrintJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string[]>>({});
  const [newToken, setNewToken] = useState<{ name: string; token: string } | null>(null);
  const [printer, setPrinter] = useState({ branch: branches[0]?.id ?? 0, name: "", host: "", auto: true });
  const [agent, setAgent] = useState({ branch: branches[0]?.id ?? 0, name: "" });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [p, j, a] = await Promise.all([
          fetchPrinters(companyId), fetchPrintJobs(companyId),
          // Los agentes son configuración: quien sólo opera la caja no los pide.
          canManage ? fetchPrintAgents(companyId) : Promise.resolve([]),
        ]);
        if (cancelled) return;
        setPrinters(p); setJobs(j); setAgents(a);
      } catch (err) {
        if (!cancelled) {
          setPrinters([]);
          setError(err instanceof Error ? err.message : "No se pudo cargar la impresión.");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [companyId, canManage]);

  function fail(err: unknown) {
    if (err instanceof PrintingFieldError) setFields(err.fields);
    else setError(err instanceof Error ? err.message : "No se pudo completar.");
  }

  async function addPrinter(event: React.FormEvent) {
    event.preventDefault();
    setFields({}); setError(null);
    try {
      const created = await createPrinter(companyId, {
        branch: printer.branch, name: printer.name.trim(), host: printer.host.trim(),
        auto_print: printer.auto,
      });
      setPrinters((rows) => [...(rows ?? []), created]);
      setPrinter((p) => ({ ...p, name: "", host: "" }));
    } catch (err) { fail(err); }
  }

  async function addAgent(event: React.FormEvent) {
    event.preventDefault();
    setFields({}); setError(null); setNewToken(null);
    try {
      const { token, ...created } = await createPrintAgent(companyId, {
        branch: agent.branch, name: agent.name.trim(),
      });
      setAgents((rows) => [...rows, created]);
      setNewToken({ name: created.name, token });
      setAgent((a) => ({ ...a, name: "" }));
    } catch (err) { fail(err); }
  }

  async function removePrinter(row: Printer) {
    try {
      await deactivatePrinter(companyId, row.id);
      setPrinters((rows) => (rows ?? []).map((p) => (p.id === row.id ? { ...p, is_active: false } : p)));
    } catch (err) { fail(err); }
  }

  async function removeAgent(row: PrintAgent) {
    try {
      await revokePrintAgent(companyId, row.id);
      setAgents((rows) => rows.map((a) => (a.id === row.id ? { ...a, is_active: false } : a)));
    } catch (err) { fail(err); }
  }

  async function retry(job: PrintJob) {
    try {
      const updated = await retryPrintJob(companyId, job.id);
      setJobs((rows) => rows.map((j) => (j.id === updated.id ? updated : j)));
    } catch (err) { fail(err); }
  }

  const fieldError = (name: string) =>
    fields[name]?.length ? <p role="alert" className="mt-1 text-xs text-danger">{fields[name].join(" ")}</p> : null;

  if (printers === null) return <p className="text-sm text-muted">Cargando impresoras…</p>;

  return (
    <div className="space-y-8">
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}

      <section className={CARD} aria-labelledby="printers-title">
        <h2 id="printers-title" className="text-base font-semibold text-foreground">Impresoras</h2>
        <p className="mt-1 text-sm text-muted">
          Térmicas de 80 mm conectadas a la red del local, por cable o Wi-Fi.
        </p>
        {printers.length === 0 ? (
          <p className="mt-4 text-sm text-muted">Todavía no hay ninguna impresora.</p>
        ) : (
          <ul className="mt-4 divide-y divide-bd-border">
            {printers.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="font-medium text-foreground [overflow-wrap:anywhere]">{row.name}</p>
                  <p className="text-xs text-muted">
                    {row.branch_name} · {row.host}:{row.port} · {row.paper_width_mm} mm ·{" "}
                    {!row.is_active ? "Desactivada" : row.auto_print ? "Automática" : "Sólo reimpresión"}
                  </p>
                </div>
                {canManage && row.is_active ? (
                  <button type="button" onClick={() => void removePrinter(row)}
                    aria-label={`Desactivar ${row.name}`}
                    className="min-h-11 px-2 text-sm text-muted underline-offset-4 hover:text-danger hover:underline">
                    Desactivar
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}

        {canManage ? (
          <form onSubmit={(e) => void addPrinter(e)} className="mt-5 grid gap-4 sm:grid-cols-2">
            <div>
              <label className={LABEL} htmlFor="printer-branch">Sucursal de la impresora</label>
              <select id="printer-branch" className={INPUT} value={printer.branch}
                onChange={(e) => setPrinter((p) => ({ ...p, branch: Number(e.target.value) }))}>
                {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
              {fieldError("branch")}
            </div>
            <div>
              <label className={LABEL} htmlFor="printer-name">Nombre de la impresora</label>
              <input id="printer-name" className={INPUT} value={printer.name} required
                onChange={(e) => setPrinter((p) => ({ ...p, name: e.target.value }))} />
              {fieldError("name")}
            </div>
            <div>
              <label className={LABEL} htmlFor="printer-host">Dirección en la red del local</label>
              <input id="printer-host" className={INPUT} value={printer.host} required
                placeholder="192.168.1.50" inputMode="url" autoComplete="off"
                onChange={(e) => setPrinter((p) => ({ ...p, host: e.target.value }))} />
              {fieldError("host")}
            </div>
            <label className="flex min-h-11 items-center gap-2 self-end text-sm text-foreground">
              <input type="checkbox" checked={printer.auto}
                onChange={(e) => setPrinter((p) => ({ ...p, auto: e.target.checked }))} />
              Imprimir sola cada venta de este local
            </label>
            {fieldError("auto_print")}
            <div className="sm:col-span-2">
              <button type="submit"
                className="min-h-11 rounded-lg bg-foreground px-5 text-sm font-semibold text-background">
                Añadir impresora
              </button>
            </div>
          </form>
        ) : null}
      </section>

      {canManage ? (
        <section className={CARD} aria-labelledby="agents-title">
          <h2 id="agents-title" className="text-base font-semibold text-foreground">Agentes</h2>
          <p className="mt-1 text-sm text-muted">
            El programa que se deja encendido en el local y entrega los tickets a la impresora.
          </p>
          {newToken ? (
            <div role="status" className="mt-4 rounded-lg border border-bd-border bg-surface-2 p-4 text-sm">
              <p className="font-medium text-foreground">Token de «{newToken.name}»</p>
              <code className="mt-2 block rounded bg-background p-2 text-xs [overflow-wrap:anywhere]">
                {newToken.token}
              </code>
              <p className="mt-2 text-muted">
                Cópialo ahora en la configuración del agente. No se volverá a mostrar.
              </p>
            </div>
          ) : null}
          <ul className="mt-4 divide-y divide-bd-border">
            {agents.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="font-medium text-foreground [overflow-wrap:anywhere]">{row.name}</p>
                  <p className="text-xs text-muted">
                    {row.branch_name} · {row.token_hint}… ·{" "}
                    {!row.is_active ? "Revocado" : row.last_seen_at ? "Conectado alguna vez" : "Nunca se conectó"}
                  </p>
                </div>
                {row.is_active ? (
                  <button type="button" onClick={() => void removeAgent(row)}
                    aria-label={`Revocar ${row.name}`}
                    className="min-h-11 px-2 text-sm text-muted underline-offset-4 hover:text-danger hover:underline">
                    Revocar
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
          <form onSubmit={(e) => void addAgent(e)} className="mt-5 grid gap-4 sm:grid-cols-2">
            <div>
              <label className={LABEL} htmlFor="agent-branch">Sucursal del agente</label>
              <select id="agent-branch" className={INPUT} value={agent.branch}
                onChange={(e) => setAgent((a) => ({ ...a, branch: Number(e.target.value) }))}>
                {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </div>
            <div>
              <label className={LABEL} htmlFor="agent-name">Nombre del agente</label>
              <input id="agent-name" className={INPUT} value={agent.name} required
                onChange={(e) => setAgent((a) => ({ ...a, name: e.target.value }))} />
              {fieldError("name")}
            </div>
            <div className="sm:col-span-2">
              <button type="submit"
                className="min-h-11 rounded-lg bg-foreground px-5 text-sm font-semibold text-background">
                Crear agente
              </button>
            </div>
          </form>
        </section>
      ) : null}

      <section className={CARD} aria-labelledby="jobs-title">
        <h2 id="jobs-title" className="text-base font-semibold text-foreground">Cola reciente</h2>
        {jobs.length === 0 ? (
          <p className="mt-4 text-sm text-muted">No hay tickets recientes.</p>
        ) : (
          <ul className="mt-4 divide-y divide-bd-border">
            {jobs.map((job) => (
              <li key={job.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="font-medium text-foreground">
                    Pedido #{job.order} · {KIND[job.kind] ?? job.kind}
                  </p>
                  <p className="text-xs text-muted">
                    {job.printer_name} · <span>{PRINT_JOB_STATUS_LABEL[job.status] ?? job.status}</span>
                  </p>
                  {job.last_error ? <p className="text-xs text-danger [overflow-wrap:anywhere]">{job.last_error}</p> : null}
                </div>
                {job.status === "failed" || job.status === "cancelled" ? (
                  <button type="button" onClick={() => void retry(job)}
                    className="min-h-11 rounded-lg border border-bd-border px-4 text-sm font-semibold text-foreground">
                    Reenviar
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
