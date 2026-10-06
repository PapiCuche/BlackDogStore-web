/**
 * INTEGRATIONS-CONSOLE — what a platform master configures: the external
 * services of the installation (mail, payment gateway, WhatsApp, Google, SUNAT).
 *
 * A SECRET HAS NO TYPE HERE THAT CAN HOLD ITS VALUE COMING BACK. The server
 * answers `{ configured: true }` for one that is stored, and that is all there
 * is to read. A secret travels in one direction, once, when it is typed.
 *
 * The server refuses every one of these calls to anybody who is not the master
 * (403). Nothing in this file decides that.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type IntegrationField = {
  name: string;
  label: string;
  /** text · int · bool · choice · email · host · url · textarea · file */
  kind: string;
  required: boolean;
  secret: boolean;
  help: string;
  choices?: { value: string; label: string }[];
  default?: string | number | boolean;
  /** Only for `file`: the largest file the server takes, in bytes. */
  max_bytes?: number;
};

/** That a secret is stored, since when and by whom. Nothing of the secret itself. */
export type SecretState = {
  configured: boolean;
  updated_at?: string;
  updated_by?: string;
};

export type IntegrationRow = {
  public: Record<string, unknown>;
  secrets: Record<string, SecretState>;
  enabled: boolean;
  validated: boolean;
  version: number;
  last_tested_at: string | null;
  last_test_status: string;
  last_test_message: string;
  updated_at: string | null;
  updated_by: string;
  /** '', 'test' or 'production'. */
  mode: string;
  /** A word the master has to type before this row can be activated, and why. */
  activation_confirmation: { word: string; message: string } | null;
};

export type IntegrationState =
  | "NOT_CONFIGURED" | "CONFIGURED" | "VALIDATED" | "ACTIVE" | "ERROR" | "DISABLED";

export type IntegrationCompany = { id: number; name: string; slug: string };

export type Integration = {
  id: string;
  label: string;
  category: string;
  scope: "platform" | "company";
  description: string;
  supported_modes: string[];
  state: IntegrationState;
  /** 'panel' (this console), 'env' (the server's environment) or 'none'. */
  source: string;
  company: IntegrationCompany | null;
  active: IntegrationRow | null;
  draft: IntegrationRow | null;
  /** What the environment contributes: public values and the NAMES of the secrets it holds. */
  env: { public: Record<string, unknown>; secrets: string[]; mode: string } | null;
  /**
   * Set when switching this off, revoking it or activating a draft would cut
   * something already in flight (a buyer at the card form). The word is typed.
   */
  interruption: { word: string; message: string } | null;
  /** False when the server has no root key: nothing can be stored or read. */
  store_available: boolean;
  fields?: IntegrationField[];
  test_fields?: IntegrationField[];
};

/** A company-scoped integration in the list: one state per company. */
export type CompanyIntegrationSummary = {
  id: string;
  label: string;
  category: string;
  scope: "company";
  description: string;
  state: null;
  companies: (IntegrationCompany & { state: IntegrationState; source: string })[];
};

export type IntegrationSummary = Integration | CompanyIntegrationSummary;

export type IntegrationTestResult = {
  ok: boolean;
  status: string;
  message: string;
  integration: Integration;
};

export class IntegrationError extends Error {
  readonly status: number;
  /** Per-field messages of a 400. */
  readonly fields: Record<string, string>;

  constructor(message: string, status: number, fields: Record<string, string> = {}) {
    super(message);
    this.name = "IntegrationError";
    this.status = status;
    this.fields = fields;
  }
}

const BASE = `${API_BASE}/admin/integrations/`;
const query = (companyId?: number | null) =>
  companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
const path = (id: string, action = "", companyId?: number | null) =>
  `${BASE}${encodeURIComponent(id)}/${action ? `${action}/` : ""}${query(companyId)}`;

async function read<T>(res: Response, fallback: string): Promise<T> {
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (res.ok && body) return body as T;
  const fields: Record<string, string> = {};
  if (body && typeof body.errors === "object" && body.errors) {
    for (const [name, message] of Object.entries(body.errors as Record<string, unknown>)) {
      fields[name] = String(message);
    }
  }
  throw new IntegrationError(body?.detail ? String(body.detail) : fallback, res.status, fields);
}

export async function fetchIntegrations(): Promise<{ results: IntegrationSummary[]; store_available: boolean }> {
  return read(await fetchWithAuth(BASE), "No se pudieron consultar las integraciones.");
}

export async function fetchIntegration(id: string, companyId?: number | null): Promise<Integration> {
  return read(await fetchWithAuth(path(id, "", companyId)), "No se pudo consultar la integración.");
}

export async function saveIntegrationDraft(
  id: string,
  body: { public: Record<string, unknown>; secrets: Record<string, string | null>; version?: number },
  companyId?: number | null,
): Promise<Integration> {
  return read(
    await fetchWithAuth(path(id, "draft", companyId), { method: "PUT", body: JSON.stringify(body) }),
    "No se pudo guardar el borrador.",
  );
}

async function act<T>(
  id: string, action: string, body: Record<string, unknown>, companyId: number | null | undefined, fallback: string,
): Promise<T> {
  return read(
    await fetchWithAuth(path(id, action, companyId), { method: "POST", body: JSON.stringify(body) }),
    fallback,
  );
}

export const testIntegration = (
  id: string, target: "draft" | "active", options: Record<string, string>, companyId?: number | null,
) => act<IntegrationTestResult>(id, "test", { target, ...options }, companyId, "No se pudo hacer la prueba.");

/** The words a master typed: `confirm` for what the act itself asks, `acknowledge` for what it interrupts. */
export type TypedWords = { confirm?: string; acknowledge?: string };

const words = ({ confirm, acknowledge }: TypedWords) => ({
  ...(confirm ? { confirm } : {}),
  ...(acknowledge ? { acknowledge } : {}),
});

export const activateIntegration = (id: string, version: number, typed: TypedWords, companyId?: number | null) =>
  act<Integration>(id, "activate", { version, ...words(typed) }, companyId, "No se pudo activar.");

export const setIntegrationEnabled = (id: string, enabled: boolean, typed: TypedWords, companyId?: number | null) =>
  act<Integration>(id, enabled ? "enable" : "disable", words(typed), companyId, "No se pudo cambiar el estado.");

export const revokeIntegration = (id: string, typed: TypedWords, companyId?: number | null) =>
  act<Integration>(id, "revoke", words(typed), companyId, "No se pudo revocar.");

export const importIntegrationFromEnvironment = (id: string, companyId?: number | null) =>
  act<Integration>(id, "import-env", {}, companyId, "No se pudo copiar la configuración del entorno.");

// -- how things are called on screen -------------------------------------------------

export const STATE_LABELS: Record<IntegrationState, string> = {
  NOT_CONFIGURED: "Sin configurar",
  CONFIGURED: "Borrador sin probar",
  VALIDATED: "Probada, sin activar",
  ACTIVE: "Activa",
  ERROR: "Con error",
  DISABLED: "Desactivada",
};

/**
 * ERROR means «its last test failed», of what runs or of a first draft. Those
 * are different things to read on a screen: one is a service that may be down,
 * the other is nothing running at all.
 */
export function stateLabel(integration: Pick<Integration, "state" | "active">): string {
  if (integration.state !== "ERROR") return STATE_LABELS[integration.state];
  return integration.active ? "Activa, con error" : "Borrador con la prueba fallida";
}

export const STATE_TONES: Record<IntegrationState, "neutral" | "good" | "warn" | "bad"> = {
  NOT_CONFIGURED: "neutral", CONFIGURED: "warn", VALIDATED: "warn", ACTIVE: "good", ERROR: "bad", DISABLED: "neutral",
};

/** The fixed words a test can end with. The server's own sentence is shown beside it. */
const TEST_STATUS_LABELS: Record<string, string> = {
  ok: "Correcto",
  unverified: "Coherente, sin verificar",
  auth_failed: "Credenciales rechazadas",
  tls_invalid: "Certificado TLS no válido",
  timeout: "Sin respuesta a tiempo",
  refused: "Conexión rechazada",
  unreachable: "Servidor no encontrado",
  send_failed: "El mensaje de prueba no se aceptó",
  invalid: "Configuración rechazada",
  unavailable: "Servicio no disponible",
  incomplete: "Faltan datos",
  error: "La prueba no se pudo completar",
};

export const testStatusLabel = (status: string) => TEST_STATUS_LABELS[status] ?? "Resultado desconocido";

export const MODE_LABELS: Record<string, string> = { test: "TEST", production: "PRODUCCIÓN" };

/** A result that passed and proved nothing: shown as a warning, not as a success. */
export const isUnverified = (status: string) => status === "unverified";

export const REVOKE_WORD = "REVOCAR";
