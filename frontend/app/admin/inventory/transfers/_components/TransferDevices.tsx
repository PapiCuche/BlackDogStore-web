"use client";

// SERIAL-TRANSFER-01 — devices travel by READING them (docs/adr-serial-transfer.md §8).
//
// Draft:      «Escanear equipo» adds a device of the origin (IMEI, serial or internal code); «Quitar» takes it out.
// In transit: «Verificar llegada» reads each device again at the destination (ST-D2). «Recibir» waits for all of them
//             (the page asks `devicesPending`); the server refuses a receipt with devices left to read anyway.
// The server decides every read and says why it refuses; this screen only shows it.

import { useId, useState } from "react";
import { internalInputClass } from "../../../components/internal-ui";
import {
  addTransferUnit, removeTransferUnit, verifyTransferUnit, type StockTransfer,
} from "../../../../lib/inventory";

type Method = "scanner" | "manual";

/** Devices of the transfer still to be read at the destination. */
export function devicesPending(transfer: StockTransfer): number {
  return transfer.items
    .filter((item) => item.is_serialized)
    .reduce((n, item) => n + (item.units ?? []).filter((u) => !u.received_at).length, 0);
}

export function TransferDevices({ transfer, mayTransfer, onUpdated }: {
  transfer: StockTransfer;
  mayTransfer: boolean;
  onUpdated: (transfer: StockTransfer) => void;
}) {
  const inputId = useId();
  const [code, setCode] = useState("");
  const [manual, setManual] = useState(false);
  const [working, setWorking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const lines = transfer.items.filter((item) => item.is_serialized);
  const total = lines.reduce((n, item) => n + (item.units ?? []).length, 0);
  const verified = total - devicesPending(transfer);
  const draft = transfer.status === "draft";
  const travelling = transfer.status === "in_transit";
  const reads = mayTransfer && (draft || travelling);

  async function act(action: () => Promise<StockTransfer>) {
    setWorking(true);
    setProblem(null);
    try {
      onUpdated(await action());
      setCode("");
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "No se pudo leer el equipo.");
    } finally {
      setWorking(false);
    }
  }

  function read() {
    const text = code.trim();
    if (!text || working) return;
    const method: Method = manual ? "manual" : "scanner";
    void act(() => (draft ? addTransferUnit(transfer.id, text, method) : verifyTransferUnit(transfer.id, text, method)));
  }

  if (!reads && lines.length === 0) return null;

  return (
    <section aria-label="Equipos con serie" className="space-y-3 rounded-lg border border-bd-border bg-surface p-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">Equipos con serie</h2>
        <p className="mt-1 text-xs text-muted">
          {draft
            ? "Cada equipo se agrega leyéndolo. Sigue a la venta hasta que se despacha; si se vende antes, el despacho lo dirá."
            : travelling
              ? `En ${transfer.destination_branch_name}, lee cada equipo que llegó. Sólo se recibe con todos verificados.`
              : "Los equipos que viajaron en esta transferencia."}
        </p>
      </div>

      {reads ? (
        <div className="space-y-2">
          <label htmlFor={inputId} className="block text-xs text-muted">
            {draft ? "Escanear equipo (IMEI, serie o código interno)" : "Verificar llegada (IMEI, serie o código interno)"}
          </label>
          <input
            id={inputId} value={code} autoComplete="off" spellCheck={false} disabled={working}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); read(); } }}
            className={`font-mono ${internalInputClass}`}
          />
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} />
            Lo escribí a mano (un equipo con IMEI se identifica por su IMEI)
          </label>
          {problem ? <p role="alert" className="text-sm text-danger">{problem}</p> : null}
        </div>
      ) : null}

      {travelling || transfer.status === "received" ? (
        <p className="text-sm font-semibold text-foreground" role="status">
          {verified} de {total} equipos verificados
        </p>
      ) : null}

      {lines.map((item) => (
        <div key={item.id}>
          <p className="text-xs font-semibold text-foreground">{item.product_name} · {(item.units ?? []).length}</p>
          <ul aria-label={`Equipos de ${item.product_name}`} className="mt-1 space-y-1">
            {(item.units ?? []).map((unit) => (
              <li key={unit.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-mono text-foreground">
                  <span>{unit.identifier}</span>
                  {unit.imei && unit.imei !== unit.identifier ? <span className="text-muted"> · IMEI {unit.imei}</span> : null}
                </span>
                {travelling || transfer.status === "received" ? (
                  <span className={unit.received_at ? "text-success" : "text-warning"}>
                    {unit.received_at ? "Verificado" : "Por verificar"}
                  </span>
                ) : null}
                {draft && mayTransfer ? (
                  <button
                    type="button" disabled={working}
                    className="rounded-lg border border-bd-border px-2 py-1 text-xs font-semibold text-foreground disabled:opacity-40"
                    aria-label={`Quitar ${unit.identifier}`}
                    onClick={() => void act(() => removeTransferUnit(transfer.id, unit.id))}
                  >
                    Quitar
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
