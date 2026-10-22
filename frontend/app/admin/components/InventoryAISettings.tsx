"use client";

// INV-SWITCH-01 — «Inteligencia artificial» in Configuración › Inventario.
//
// The company's switch, and what it means today (off, on, on but not
// configured, service down, quota spent). Whether this person may change it is
// the server's answer (`can_manage`); the server also asks again on every
// import, so this switch is a preference, not the control.

import { useEffect, useId, useState } from "react";
import {
  fetchInventoryAISettings, updateInventoryAISettings, type InventoryAISettingsPayload,
} from "../../lib/inventory-ai";

export function InventoryAISettings() {
  const id = useId();
  const [data, setData] = useState<InventoryAISettingsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await fetchInventoryAISettings();
        if (!cancelled) setData(loaded);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "No se pudo cargar la configuración de IA.");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function toggle(enabled: boolean) {
    setBusy(true);
    setError(null);
    try {
      setData(await updateInventoryAISettings(enabled));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar la configuración de IA.");
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return error ? <p className="text-sm text-danger">{error}</p> : <p className="text-sm text-muted">Cargando…</p>;
  }

  return (
    <div className="space-y-3">
      <label htmlFor={id} className="flex items-center gap-3 text-sm font-medium text-foreground">
        <input
          id={id}
          type="checkbox"
          checked={data.enabled}
          disabled={!data.can_manage || busy}
          onChange={(event) => void toggle(event.target.checked)}
          className="h-4 w-4"
        />
        Usar reconocimiento con IA
      </label>
      <p className="text-sm text-muted">
        Estado: <span className="font-medium text-foreground">{data.state_label}</span>
      </p>
      <p className="text-xs text-muted">
        Aun activada, cada importación pregunta si usar IA. Mientras no esté configurada, todo se lee sin IA:
        la carga manual y la lectura local siguen funcionando igual y no se envía nada a ningún servicio externo.
      </p>
      {!data.can_manage && (
        <p className="text-xs text-muted">Sólo un administrador de toda la empresa puede cambiarla.</p>
      )}
      {error && <p className="text-sm text-danger" role="alert">{error}</p>}
    </div>
  );
}
