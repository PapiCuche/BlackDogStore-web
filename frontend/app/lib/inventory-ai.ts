// INV-SWITCH-01 — the company's inventory AI switch (Configuración › Inventario).
// The switch is not the control: the server decides again on every import.

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type InventoryAIState = "disabled" | "enabled" | "not_configured" | "unavailable" | "quota_exhausted";

export type InventoryAISettingsPayload = {
  enabled: boolean;
  global_enabled: boolean;
  state: InventoryAIState;
  state_label: string;
  can_manage: boolean;
};

async function answer(res: Response, fallback: string): Promise<InventoryAISettingsPayload> {
  const body = await res.json().catch(() => null);
  if (res.ok) return body as InventoryAISettingsPayload;
  const detail = body && typeof body.detail === "string" ? body.detail : "";
  throw new Error(detail || (res.status === 403 ? "No tienes permisos para esto." : fallback));
}

export async function fetchInventoryAISettings(): Promise<InventoryAISettingsPayload> {
  return answer(await fetchWithAuth(`${API_BASE}/admin/inventory/ai-settings/`), "No se pudo cargar la configuración de IA.");
}

export async function updateInventoryAISettings(enabled: boolean): Promise<InventoryAISettingsPayload> {
  return answer(
    await fetchWithAuth(`${API_BASE}/admin/inventory/ai-settings/`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
    "No se pudo guardar la configuración de IA.",
  );
}
