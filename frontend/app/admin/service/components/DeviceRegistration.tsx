"use client";

/**
 * DEVICE-IDENTITY — registrar el equipo que está sobre el mostrador.
 *
 * UN EQUIPO, UNA FICHA. Antes de crear nada se pregunta al servidor si ese
 * número de serie o ese IMEI ya estuvieron aquí. Si el equipo es del mismo
 * cliente, se reutiliza —y con él su historial—; si figura a nombre de otro,
 * se avisa: cambió de manos, que es legítimo y conviene saberlo.
 *
 * QUÉ PIDE CADA TIPO lo decide el servidor. Aquí sólo se refleja para no pedir
 * un IMEI a una laptop ni dejar enviar un teléfono sin él: un teléfono lleva
 * IMEI; una tablet o un reloj, a veces; lo demás, no. La validación de verdad
 * —el dígito de control, los duplicados— ocurre al guardar, y su respuesta se
 * muestra junto al campo que falla.
 *
 * LO QUE NO SE PUEDE LEER SE DICE. Un equipo que no enciende y no tiene
 * etiqueta no se registra con un número inventado: se marca que falta y por qué.
 */

import { useId, useRef, useState } from "react";
import {
  ServiceApiError, createServiceDevice, lookupServiceDevice,
  type ServiceDeviceCreated, type ServiceDeviceMatch,
} from "../../../lib/service-console";

type Chosen = { id: number; display_name: string };

type Props = {
  slug: string;
  customerId: number;
  deviceTypes: { value: string; label: string }[];
  onRegistered: (device: Chosen) => void;
  disabled?: boolean;
};

const SERIAL_TYPES = new Set(["phone", "tablet", "laptop", "desktop", "console", "wearable"]);
const IMEI_REQUIRED = new Set(["phone"]);
const IMEI_POSSIBLE = new Set(["phone", "tablet", "wearable", "other"]);

const inputStyle = "mt-1 w-full rounded-lg border border-bd-border bg-surface p-2 text-sm text-foreground aria-[invalid=true]:border-danger-border";

function orders(count: number) {
  return count === 1 ? "1 orden anterior" : `${count} órdenes anteriores`;
}

export function DeviceRegistration({ slug, customerId, deviceTypes, onRegistered, disabled = false }: Props) {
  const ids = useId();
  const [type, setType] = useState("");
  const [brand, setBrand] = useState("");
  const [model, setModel] = useState("");
  const [serial, setSerial] = useState("");
  const [imei, setImei] = useState("");
  const [imei2, setImei2] = useState("");
  const [unreadable, setUnreadable] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [existing, setExisting] = useState<Chosen | null>(null);
  const [known, setKnown] = useState<ServiceDeviceMatch[]>([]);
  const [changedHands, setChangedHands] = useState<ServiceDeviceMatch[]>([]);
  const lastLookup = useRef("");

  const needsSerial = SERIAL_TYPES.has(type) && !unreadable;
  const showImei = IMEI_POSSIBLE.has(type);
  const needsImei = IMEI_REQUIRED.has(type) && !unreadable;

  function field(name: string) {
    const messages = fieldErrors[name];
    return {
      "aria-invalid": messages ? true : undefined,
      "aria-describedby": messages ? `${ids}-${name}-error` : undefined,
    } as const;
  }

  function errorOf(name: string) {
    const messages = fieldErrors[name];
    return messages ? (
      <span id={`${ids}-${name}-error`} className="mt-1 block text-xs text-danger">{messages.join(" ")}</span>
    ) : null;
  }

  /** Sólo con un identificador completo: un número a medio escribir no se busca. */
  async function lookup() {
    const query = {
      serial_number: serial.trim().length >= 4 ? serial.trim() : "",
      imei: imei.replace(/\D/g, "").length === 15 ? imei.replace(/\D/g, "") : "",
      imei2: imei2.replace(/\D/g, "").length === 15 ? imei2.replace(/\D/g, "") : "",
    };
    const key = JSON.stringify(query);
    if (!query.serial_number && !query.imei && !query.imei2) {
      setKnown([]);
      return;
    }
    if (key === lastLookup.current) return;
    lastLookup.current = key;
    try {
      setKnown((await lookupServiceDevice(slug, query)).results);
    } catch {
      // La búsqueda es una ayuda: si falla, guardar sigue avisando de duplicados.
      setKnown([]);
    }
  }

  async function save() {
    if (busy) return;
    setBusy(true);
    setFieldErrors({});
    setFailure(null);
    setExisting(null);
    setChangedHands([]);
    try {
      const created: ServiceDeviceCreated = await createServiceDevice(slug, {
        customer_id: customerId, device_type: type, brand: brand.trim(), model: model.trim(),
        serial_number: serial.trim(), imei: showImei ? imei.trim() : "",
        imei2: showImei ? imei2.trim() : "",
        identifiers_pending_reason: unreadable ? reason.trim() : "",
      });
      setChangedHands((created.possible_duplicates ?? []).filter((row) => row.customer !== customerId));
      setKnown([]);
      onRegistered({ id: created.id, display_name: created.display_name });
    } catch (err) {
      const data = err instanceof ServiceApiError ? err.data : null;
      if (err instanceof ServiceApiError && err.status === 409 && data && typeof data === "object" && "existing_device" in data) {
        const device = (data as { existing_device: Chosen }).existing_device;
        setExisting({ id: device.id, display_name: device.display_name });
        setFailure(err.message);
      } else if (err instanceof ServiceApiError && err.status === 400 && data && typeof data === "object" && !("detail" in data)) {
        setFieldErrors(data as Record<string, string[]>);
      } else {
        setFailure(err instanceof Error ? err.message : "No se pudo registrar el equipo.");
      }
    } finally {
      setBusy(false);
    }
  }

  const mine = known.filter((row) => row.customer === customerId);
  const others = known.filter((row) => row.customer !== customerId);
  const complete = Boolean(type && brand.trim() && model.trim())
    && (!needsSerial || serial.trim()) && (!needsImei || imei.trim())
    && (!unreadable || reason.trim());

  return (
    <div className="mt-3 space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-muted">Tipo de equipo
          <select className={inputStyle} value={type} disabled={disabled || busy} onChange={(event) => setType(event.target.value)}>
            <option value="">Selecciona tipo</option>
            {deviceTypes.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted">Marca
          <input className={inputStyle} value={brand} disabled={disabled || busy} onChange={(event) => setBrand(event.target.value)} {...field("brand")} />
          {errorOf("brand")}
        </label>
        <label className="text-xs text-muted">Modelo
          <input className={inputStyle} value={model} disabled={disabled || busy} onChange={(event) => setModel(event.target.value)} {...field("model")} />
          {errorOf("model")}
        </label>
        <label className="text-xs text-muted">Número de serie{needsSerial ? "" : " (opcional)"}
          <input
            className={inputStyle} value={serial} required={needsSerial} disabled={disabled || busy}
            autoCapitalize="characters" autoComplete="off" spellCheck={false}
            onChange={(event) => setSerial(event.target.value)} onBlur={() => void lookup()}
            {...field("serial_number")}
          />
          {errorOf("serial_number")}
        </label>
        {showImei ? (
          <>
            <label className="text-xs text-muted">IMEI{IMEI_REQUIRED.has(type) ? "" : " (si es celular)"}
              <input
                className={inputStyle} value={imei} required={needsImei} disabled={disabled || busy}
                inputMode="numeric" autoComplete="off" maxLength={20}
                onChange={(event) => setImei(event.target.value)} onBlur={() => void lookup()}
                aria-label={IMEI_REQUIRED.has(type) ? "IMEI" : "IMEI (si es celular)"}
                {...field("imei")}
              />
              {errorOf("imei")}
            </label>
            <label className="text-xs text-muted">Segundo IMEI (doble SIM o eSIM, opcional)
              <input
                className={inputStyle} value={imei2} disabled={disabled || busy}
                inputMode="numeric" autoComplete="off" maxLength={20}
                onChange={(event) => setImei2(event.target.value)} onBlur={() => void lookup()}
                {...field("imei2")}
              />
              {errorOf("imei2")}
            </label>
          </>
        ) : null}
      </div>

      {SERIAL_TYPES.has(type) ? (
        <div className="space-y-2">
          <label className="flex items-start gap-2 text-xs text-foreground">
            <input type="checkbox" className="mt-0.5" checked={unreadable} disabled={disabled || busy} onChange={(event) => setUnreadable(event.target.checked)} />
            No se puede leer la serie o el IMEI de este equipo
          </label>
          {unreadable ? (
            <label className="block text-xs text-muted">Motivo por el que falta
              <input
                className={inputStyle} value={reason} maxLength={200} disabled={disabled || busy}
                placeholder="Ej.: no enciende y no tiene bandeja SIM"
                onChange={(event) => setReason(event.target.value)}
                {...field("identifiers_pending_reason")}
              />
              {errorOf("identifiers_pending_reason")}
            </label>
          ) : null}
        </div>
      ) : null}

      {mine.length || others.length || changedHands.length ? (
        <div role="status" className="space-y-2 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning">
          {mine.map((row) => (
            <div key={`m-${row.id}`} className="space-y-1">
              <p className="font-semibold">Este equipo ya estuvo aquí: {row.display_name}</p>
              <p>
                {orders(row.repair_orders_count)}
                {row.last_repair_order ? ` · la última, ${row.last_repair_order.number}` : ""}
              </p>
              <button type="button" onClick={() => onRegistered({ id: row.id, display_name: row.display_name })} className="min-h-9 rounded-lg border border-warning-border bg-background px-3 font-semibold text-foreground">
                Usar {row.display_name}
              </button>
            </div>
          ))}
          {[...others, ...changedHands].map((row) => (
            <div key={`o-${row.id}`} className="space-y-1">
              <p className="font-semibold">Este equipo ya estuvo aquí, a nombre de {row.customer_name}</p>
              <p>
                {orders(row.repair_orders_count)}
                {row.last_repair_order ? ` · la última, ${row.last_repair_order.number}` : ""}
                . Si cambió de dueño, regístralo para este cliente.
              </p>
            </div>
          ))}
        </div>
      ) : null}

      {failure ? (
        <div role="alert" className="space-y-2 rounded-lg border border-danger-border bg-danger-surface px-3 py-2 text-xs text-danger">
          <p>{failure}</p>
          {existing ? (
            <button type="button" onClick={() => onRegistered(existing)} className="min-h-9 rounded-lg border border-bd-border bg-background px-3 font-semibold text-foreground">
              Usar {existing.display_name}
            </button>
          ) : null}
        </div>
      ) : null}

      <button
        type="button" disabled={disabled || busy || !complete} onClick={() => void save()}
        className="min-h-10 rounded-lg bg-foreground px-4 text-sm font-semibold text-background transition-opacity hover:opacity-90 disabled:opacity-40"
      >
        {busy ? "Guardando…" : "Guardar equipo"}
      </button>
    </div>
  );
}
