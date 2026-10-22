"use client";

/**
 * INV-TEXT-01: reconocimiento inicial EN EL NAVEGADOR.
 * Sin fetch, sin proveedor IA, sin almacenamiento y sin escritura de inventario.
 * El modo minucioso sigue siendo los formularios y las importaciones existentes.
 */
import { useState } from "react";
import Link from "next/link";
import {
  MAX_TEXT_CHARS, previewSupplierText,
  type InventoryLine, type InventoryOrigin, type TextPreview,
} from "../lib/inventory-text-preview";

function masked(value: string): string {
  if (!value) return "—";
  return "••••" + value.slice(-4);
}

export function InventoryTextPreview({ canSeeCosts }: { canSeeCosts: boolean }) {
  const [raw, setRaw] = useState("");
  const [line, setLine] = useState<InventoryLine>("used");
  const [origin, setOrigin] = useState<InventoryOrigin>("supplier");
  const [preview, setPreview] = useState<TextPreview | null>(null);
  const [error, setError] = useState("");
  function analyze() {
    try {
      setPreview(previewSupplierText(raw, { line, origin }));
      setError("");
    } catch (cause) {
      setPreview(null);
      setError(cause instanceof Error ? cause.message : "No se pudo interpretar el texto.");
    }
  }
  function changeRaw(value: string) {
    setRaw(value);
    setPreview(null);
    setError("");
  }
  return (
    <div className="space-y-4 rounded-xl border border-bd-border bg-surface p-4 sm:p-5">
      <div>
        <h3 className="text-sm font-semibold">Reconocer lista de WhatsApp · Vista previa local</h3>
        <p className="mt-1 text-xs text-muted">
          Elige el origen y la línea, pega varios mensajes y revisa el resultado.
          No se envía texto al servidor ni a ninguna IA. No crea ni modifica productos, costos o stock.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span className="block font-semibold">Estos productos son</span>
          <select aria-label="Línea declarada" className="w-full rounded-lg border border-bd-border bg-background p-2"
            value={line} onChange={event => { setLine(event.target.value as InventoryLine); setPreview(null); }}>
            <option value="used">Seminuevos</option>
            <option value="new">Nuevos</option>
            <option value="accessory">Accesorios</option>
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span className="block font-semibold">Origen de la lista</span>
          <select aria-label="Origen declarado" className="w-full rounded-lg border border-bd-border bg-background p-2"
            value={origin} onChange={event => { setOrigin(event.target.value as InventoryOrigin); setPreview(null); }}>
            <option value="supplier">Proveedor (NO stock propio)</option>
            <option value="own">Recepción física propia (solo preparación)</option>
          </select>
        </label>
      </div>
      <label className="block space-y-1 text-sm">
        <span className="block font-semibold">Texto de WhatsApp</span>
        <textarea aria-label="Texto para reconocer" rows={7} maxLength={MAX_TEXT_CHARS}
          className="w-full resize-y rounded-lg border border-bd-border bg-background p-3 font-mono text-xs"
          placeholder={"iPhone 13\n128 GB\nBatería 93%\nNS: EJEMPLO123\nVersión: Chip\nColor: azul"}
          value={raw} onChange={event => changeRaw(event.target.value)} />
        <span className="block text-xs text-muted">{raw.length} / {MAX_TEXT_CHARS} caracteres. Los datos solo viven en esta pestaña.</span>
      </label>
      <button type="button" onClick={analyze} disabled={!raw.trim()}
        className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-background disabled:opacity-40">
        Analizar localmente
      </button>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {preview ? (
        <div className="space-y-3" aria-label="Vista previa de lista">
          <p role="status" className="text-sm font-medium">
            {preview.candidates.length} registros candidatos · {preview.discardedLines} líneas sin producto
            {preview.truncated ? " · Límite de 300 candidatos: lista incompleta, no aplicar" : ""}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-bd-border">
                  <th scope="col" className="p-2">#</th>
                  <th scope="col" className="p-2">Modelo sugerido</th>
                  <th scope="col" className="p-2">Capacidad</th>
                  <th scope="col" className="p-2">Batería</th>
                  <th scope="col" className="p-2">Serie / IMEI</th>
                  {canSeeCosts ? <th scope="col" className="p-2">Costo sugerido</th> : null}
                  <th scope="col" className="p-2">Revisión</th>
                </tr>
              </thead>
              <tbody>
                {preview.candidates.map(candidate => (
                  <tr key={candidate.id} className="border-b border-bd-border align-top">
                    <td className="p-2">{candidate.id}</td>
                    <td className="p-2">{candidate.model}</td>
                    <td className="p-2">{candidate.capacityGb !== null ? String(candidate.capacityGb) + " GB" : "—"}</td>
                    <td className="p-2">{candidate.batteryPercent !== null ? String(candidate.batteryPercent) + "%" : "—"}</td>
                    <td className="p-2">{masked(candidate.serial)} / {masked(candidate.imei)}
                      {candidate.imei2 ? " / " + masked(candidate.imei2) : ""}</td>
                    {canSeeCosts ? <td className="p-2">{candidate.supplierCost || "Pendiente"}</td> : null}
                    <td className="p-2">
                      {candidate.warnings.length
                        ? <ul className="space-y-1">{candidate.warnings.map((warning, index) =>
                            <li key={index} className="text-warning">{warning}</li>)}</ul>
                        : <span className="text-success">Sin avisos de formato</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted">
            Ningún candidato está confirmado. Las cantidades no se deducen de filas repetidas;
            una lista del proveedor no constituye recepción física. Luhn no verifica la existencia real de un equipo.
          </p>
          <p className="text-xs text-muted">
            Para validar y registrar individualmente, utiliza las herramientas existentes:
            {" "}<Link className="underline" href="/admin/inventory/units/import">Carga de equipos</Link>
            {" · "}<Link className="underline" href="/admin/products/suppliers/offers">Ofertas de proveedores</Link>.
          </p>
        </div>
      ) : null}
    </div>
  );
}
