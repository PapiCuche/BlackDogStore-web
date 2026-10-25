/**
 * LABEL-03b — la configuración de etiquetas de la empresa (LABEL-02).
 *
 *   GET   /api/admin/label-settings/   `company.view`
 *   PATCH /api/admin/label-settings/   `company.manage` (el servidor dice `can_manage`)
 *
 * La empresa viaja como `?company=`: una selección entre las que quien pide ya
 * alcanza, que el servidor vuelve a comprobar.
 */
import { fetchWithAuth } from "../../../../lib/auth";
import { API_BASE } from "../../../../lib/api";
import { fetchSequences } from "../../../lib/internal-api";

export type DeviceSymbol = "internal" | "imei1";

export type LabelSettings = {
  unit_code_prefix: string;
  device_symbol: DeviceSymbol;
  device_symbol_label: string;
  device_symbols: { value: DeviceSymbol; label: string }[];
  can_manage: boolean;
};

export type LabelSettingsChange = Partial<Pick<LabelSettings, "unit_code_prefix" | "device_symbol">>;

/** Un 400: los mensajes del servidor por campo, tal cual. */
export class LabelSettingsFieldError extends Error {
  constructor(message: string, readonly fields: Record<string, string[]>) {
    super(message);
    this.name = "LabelSettingsFieldError";
  }
}

const query = (companyId: number | null) =>
  companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";

async function detail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return body && typeof body.detail === "string" ? body.detail : fallback;
}

export async function fetchLabelSettings(companyId: number | null): Promise<LabelSettings> {
  const res = await fetchWithAuth(`${API_BASE}/admin/label-settings/${query(companyId)}`);
  if (!res.ok) {
    throw new Error(await detail(res, res.status === 403
      ? "No tienes permiso para ver la configuración de etiquetas."
      : "No se pudo cargar la configuración de etiquetas."));
  }
  return res.json();
}

export async function updateLabelSettings(
  companyId: number | null, change: LabelSettingsChange,
): Promise<LabelSettings> {
  const res = await fetchWithAuth(`${API_BASE}/admin/label-settings/${query(companyId)}`, {
    method: "PATCH",
    body: JSON.stringify(change),
  });
  if (res.ok) return res.json();
  const body = await res.json().catch(() => null);
  if (res.status === 400 && body && typeof body === "object" && !("detail" in body)) {
    const fields = Object.fromEntries(
      Object.entries(body as Record<string, unknown>).map(([key, value]) => [
        key, Array.isArray(value) ? value.map(String) : [String(value)],
      ]),
    );
    throw new LabelSettingsFieldError("Revisa los campos marcados.", fields);
  }
  throw new Error(body && typeof body.detail === "string" ? body.detail : "No se pudo guardar la configuración.");
}

/**
 * El prefijo de los CÓDIGOS DE PRODUCTO, para mostrarlo junto al de los equipos.
 * Es el de la serie `product_code` de la empresa (LABEL-01); sin serie guardada,
 * el servidor usa `P` (`models.stored_product_code_prefix`), y aquí lo mismo.
 */
export const DEFAULT_PRODUCT_CODE_PREFIX = "P";

export async function fetchProductCodePrefix(companyId: number | null): Promise<string> {
  const list = await fetchSequences(companyId);
  const row = list.results.find((r) => r.document_type === "product_code" && r.branch === null);
  return row?.prefix || DEFAULT_PRODUCT_CODE_PREFIX;
}
