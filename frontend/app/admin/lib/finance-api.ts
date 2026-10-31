/**
 * FIN-REMIND-01 — the client of «Finanzas → Pagos fijos y vencimientos».
 *
 *   GET    /api/admin/finance/summary/                      finance.view
 *   GET    /api/admin/finance/obligations/                  finance.view
 *   GET    /api/admin/finance/obligations/<id>/             finance.view
 *   POST   /api/admin/finance/obligations/<id>/payments/    finance.payments.record  (Idempotency-Key)
 *   POST   .../payments/<pid>/reverse/                      finance.payments.reverse
 *   POST   /DELETE .../obligations/<id>/snooze/             finance.view — THIS person's reminders only
 *   GET    /PATCH /api/admin/finance/reminder-policy/       finance.view · finance.automation.manage
 *   GET    /api/admin/finance/rules/ · PATCH .../rules/<id>/ · POST .../rules/<id>/<action>/   finance.manage
 *   GET    /api/admin/finance/categories/
 *
 * The server decides everything that matters: who sees which company and branch, the state of each obligation
 * (`overdue`, `due_today`…), how much is still owed and what «hoy» is for the company. This module builds the
 * query and types the answer. AMOUNTS TRAVEL AS STRINGS AND ARE NEVER RECOMPUTED HERE. Errors surface the
 * server's `detail`.
 *
 * A reminder is a notice. Nothing in this module changes a debt except `recordPayment` and `reversePayment`,
 * which are explicit money operations behind their own capability; `snoozeObligation` only silences the
 * caller's own reminders.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

// ---------------------------------------------------------------------------
// Capabilities (what the interface OFFERS; the server enforces every one)
// ---------------------------------------------------------------------------

export const CAP_VIEW = "finance.view";
export const CAP_MANAGE = "finance.manage";
export const CAP_RECORD = "finance.payments.record";
export const CAP_REVERSE = "finance.payments.reverse";
export const CAP_AUTOMATION = "finance.automation.manage";

type Access = { access: { is_platform_admin: boolean; capabilities: string[] } } | null | undefined;

/** Whether the signed-in person holds `capability` in the selected company (the platform master holds all). */
export function hasFinanceCapability(dashboard: Access, capability: string): boolean {
  if (!dashboard) return false;
  return dashboard.access.is_platform_admin || dashboard.access.capabilities.includes(capability);
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ObligationState =
  | "pending"
  | "due_soon"
  | "due_today"
  | "overdue"
  | "paid"
  | "voided"
  | "paused"
  | "origin_cancelled"
  | "quote_review";

export type Ref = { id: number; name: string } | null;

export type Obligation = {
  id: number;
  reference: string;
  direction: "payable" | "receivable";
  /** `manual`, `rule`… or `repair`: a repair's balance is collected from its order, never here. */
  origin_type: string;
  concept: string;
  branch: Ref;
  category: Ref;
  supplier: Ref;
  payee_name: string;
  owner: { id: number; name: string } | null;
  planned_method: string;
  rule_id: number | null;
  period_index: number | null;
  currency: string;
  amount_original: string | null;
  effective: string;
  paid: string;
  balance: string;
  partially_paid: boolean;
  issue_date: string;
  due_date: string | null;
  state: ObligationState;
  lifecycle: string;
  disputed: boolean;
  dispute_reason: string;
  external_reference: string;
  notes: string;
  /** Until when THE CALLER silenced this obligation's reminders (ISO date), else null. */
  snoozed_until: string | null;
};

export type Payment = {
  id: number;
  amount: string;
  currency: string;
  method: string;
  paid_on: string;
  reference: string;
  notes: string;
  recorded_by: { id: number; name: string } | null;
  recorded_at: string;
  reversed: boolean;
  reversal_reason: string;
  reversed_at: string | null;
};

export type HistoryEntry = {
  action: string;
  actor: { id: number; name: string } | null;
  at: string;
  metadata: Record<string, unknown>;
};

export type ReminderHistory = {
  sent: number;
  mine: { title: string; at: string; read: boolean }[];
};

export type ObligationDetail = Obligation & {
  payments: Payment[];
  adjustments: { id: number; kind: string; amount: string; reason: string; created_at: string }[];
  history: HistoryEntry[];
  reminders: ReminderHistory;
};

export type ObligationPage = { count: number; page: number; page_size: number; results: Obligation[] };

/** Amounts per currency, as strings: `{ PEN: "1250.00" }`. Never mixed. */
export type PerCurrency = Record<string, string>;

export type Summary = {
  open: PerCurrency;
  overdue: PerCurrency;
  due_today: PerCurrency;
  due_soon: PerCurrency;
  counts: { open: number; overdue: number; due_today: number; due_soon: number };
  /** The company's «today» (ISO date). The browser clock is never used for it. */
  today: string;
  notice: string;
};

export type ReminderPolicy = {
  enabled: boolean;
  offsets: number[];
  overdue_every_days: number;
  overdue_max: number;
  email_enabled: boolean;
  email_offsets: number[];
  email_overdue: boolean;
  last_run_at: string | null;
  last_error: string;
  can_manage: boolean;
};

export type PolicyChange = Partial<Omit<ReminderPolicy, "can_manage" | "last_run_at" | "last_error">>;

export type Frequency = "weekly" | "monthly" | "quarterly" | "yearly";

export type Rule = {
  id: number;
  concept: string;
  branch: Ref;
  category: Ref;
  payee_name: string;
  amount: string;
  currency: string;
  frequency: Frequency;
  anchor_date: string;
  due_day: number;
  end_date: string | null;
  planned_method: string;
  reference: string;
  notes: string;
  ended: boolean;
  end_reason: string;
  paused: boolean;
  paused_from: string | null;
  pauses: { paused_from: string; resumed_on: string | null; reason: string }[];
  /** After the first period the calendar and the currency cannot change. */
  calendar_locked: boolean;
};

export type RuleChange = Partial<{
  concept: string;
  amount: string;
  payee_name: string;
  notes: string;
  due_day: number;
}>;

export type Category = { id: number; direction: string; name: string; is_active: boolean };

export type ObligationFilters = {
  state?: string;
  branch?: number | null;
  category?: number | null;
  rule?: number | null;
  disputed?: boolean;
  dueFrom?: string;
  dueTo?: string;
  page?: number;
  pageSize?: number;
};

export type PaymentInput = {
  amount: string;
  method: string;
  paid_on: string;
  reference?: string;
  notes?: string;
};

export class FinanceApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "FinanceApiError";
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

function query(companyId: number | null, extra: Record<string, string | number | boolean | null | undefined> = {}) {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  for (const [key, value] of Object.entries(extra)) {
    if (value === null || value === undefined || value === "" || value === false) continue;
    qs.set(key, String(value));
  }
  const text = qs.toString();
  return text ? `?${text}` : "";
}

type Options = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
};

/** The sentence the server wrote (`detail`), else the fallback. A body that is not JSON keeps the fallback. */
async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    // Not JSON: the fallback sentence stands.
  }
  return fallback;
}

async function call<T>(path: string, fallback: string, options: Options = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetchWithAuth(`${API_BASE}/admin/finance/${path}`, {
      method: options.method ?? "GET",
      headers: options.headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new FinanceApiError("No se pudo conectar con el servidor. Revisa tu conexión e inténtalo de nuevo.", 0);
  }
  if (!res.ok) {
    let detail = await detailOf(res, fallback);
    if (res.status === 403 && detail === fallback) detail = "No tienes permiso para esta operación en esta empresa.";
    throw new FinanceApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export function fetchSummary(companyId: number | null): Promise<Summary> {
  return call<Summary>(`summary/${query(companyId, { direction: "payable" })}`, "No se pudo cargar el resumen.");
}

export function buildObligationsQuery(companyId: number | null, filters: ObligationFilters = {}): string {
  return query(companyId, {
    direction: "payable",
    state: filters.state,
    branch: filters.branch,
    category: filters.category,
    rule: filters.rule,
    disputed: filters.disputed,
    due_from: filters.dueFrom,
    due_to: filters.dueTo,
    page: filters.page,
    page_size: filters.pageSize,
  });
}

export function fetchObligations(companyId: number | null, filters: ObligationFilters = {}): Promise<ObligationPage> {
  return call<ObligationPage>(
    `obligations/${buildObligationsQuery(companyId, filters)}`, "No se pudieron cargar los pagos.");
}

/** Pages the server hands out are 200 rows at most; the screen follows them up to this many. */
export const MAX_PAGES = 5;
export const PAGE_SIZE = 200;

export type LoadedObligations = {
  results: Obligation[];
  /** What the server says exists. When it exceeds `results.length` the screen says the list is partial. */
  count: number;
  truncated: boolean;
};

/** Follows the server's pages (a bounded number of them) so filters and the calendar work over what is loaded. */
export async function fetchAllObligations(
  companyId: number | null,
  filters: ObligationFilters = {},
  maxPages: number = MAX_PAGES,
): Promise<LoadedObligations> {
  const results: Obligation[] = [];
  let count = 0;
  for (let page = 1; page <= maxPages; page += 1) {
    const answer = await fetchObligations(companyId, { ...filters, page, pageSize: PAGE_SIZE });
    results.push(...answer.results);
    count = answer.count;
    if (results.length >= answer.count || answer.results.length === 0) break;
  }
  return { results, count, truncated: results.length < count };
}

export function fetchObligation(companyId: number | null, id: number): Promise<ObligationDetail> {
  return call<ObligationDetail>(`obligations/${id}/${query(companyId)}`, "No se pudo cargar la obligación.");
}

export function fetchCategories(companyId: number | null): Promise<Category[]> {
  return call<{ results: Category[] }>(
    `categories/${query(companyId, { direction: "payable" })}`, "No se pudieron cargar las categorías.",
  ).then((answer) => answer.results);
}

export function fetchRules(companyId: number | null): Promise<Rule[]> {
  return call<{ results: Rule[] }>(`rules/${query(companyId)}`, "No se pudieron cargar las reglas.")
    .then((answer) => answer.results);
}

export function fetchPolicy(companyId: number | null): Promise<ReminderPolicy> {
  return call<ReminderPolicy>(`reminder-policy/${query(companyId)}`, "No se pudo cargar la política de avisos.");
}

// ---------------------------------------------------------------------------
// Writes
// ---------------------------------------------------------------------------

/** A fresh key. One per dialog OPEN: a retry of the same payment reuses it, so the server records it once. */
export function newIdempotencyKey(): string {
  const c = typeof globalThis !== "undefined" ? (globalThis as { crypto?: Crypto }).crypto : undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  const random = Math.random().toString(36).slice(2);
  return `fin-${Date.now().toString(36)}-${random}`;
}

export type PaymentResult = { payment: Payment; obligation: Obligation };

export function recordPayment(
  companyId: number | null, obligationId: number, input: PaymentInput, idempotencyKey: string,
): Promise<PaymentResult> {
  return call<PaymentResult>(
    `obligations/${obligationId}/payments/${query(companyId)}`, "No se pudo registrar el pago.",
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: {
        amount: input.amount,
        method: input.method,
        paid_on: input.paid_on,
        reference: input.reference ?? "",
        notes: input.notes ?? "",
      },
    },
  );
}

export function reversePayment(
  companyId: number | null, obligationId: number, paymentId: number, reason: string,
): Promise<PaymentResult> {
  return call<PaymentResult>(
    `obligations/${obligationId}/payments/${paymentId}/reverse/${query(companyId)}`, "No se pudo revertir el pago.",
    { method: "POST", body: { reason } },
  );
}

/** «Posponer aviso»: silences the caller's own reminders for 1–30 days. The debt does not change. */
export function snoozeObligation(
  companyId: number | null, obligationId: number, days: number,
): Promise<{ snoozed_until: string }> {
  return call<{ snoozed_until: string }>(
    `obligations/${obligationId}/snooze/${query(companyId)}`, "No se pudo posponer el aviso.",
    { method: "POST", body: { days } },
  );
}

export function unsnoozeObligation(
  companyId: number | null, obligationId: number,
): Promise<{ snoozed_until: null }> {
  return call<{ snoozed_until: null }>(
    `obligations/${obligationId}/snooze/${query(companyId)}`, "No se pudo quitar el aplazamiento.",
    { method: "DELETE" },
  );
}

export function updatePolicy(companyId: number | null, change: PolicyChange): Promise<ReminderPolicy> {
  return call<ReminderPolicy>(
    `reminder-policy/${query(companyId)}`, "No se pudo guardar la política de avisos.", { method: "PATCH", body: change },
  );
}

export function updateRule(companyId: number | null, ruleId: number, change: RuleChange): Promise<Rule> {
  return call<Rule>(`rules/${ruleId}/${query(companyId)}`, "No se pudo guardar la regla.", {
    method: "PATCH", body: change,
  });
}

export type RuleAction = "pause" | "resume" | "end";

export function actOnRule(
  companyId: number | null, ruleId: number, action: RuleAction, body: { reason?: string; end_date?: string } = {},
): Promise<Rule> {
  return call<Rule>(`rules/${ruleId}/${action}/${query(companyId)}`, "No se pudo aplicar la acción a la regla.", {
    method: "POST", body,
  });
}

// ---------------------------------------------------------------------------
// Words. What the server's codes mean to a person; nothing here decides.
// ---------------------------------------------------------------------------

export const STATE_LABELS: Record<string, string> = {
  overdue: "Vencido",
  due_today: "Vence hoy",
  due_soon: "Por vencer",
  pending: "Pendiente",
  paid: "Pagado",
  voided: "Anulado",
  paused: "En pausa",
  origin_cancelled: "Origen cancelado",
  quote_review: "Presupuesto en revisión",
};

export const ADJUSTMENT_LABELS: Record<string, string> = {
  increase: "Cargo adicional",
  decrease: "Descuento",
  write_off: "Castigo",
  amount_change: "Cambio de importe",
};

/** The board manages payables that are not a repair's balance; the server refuses anything else. */
export function isManagedHere(item: { direction: string; origin_type: string }): boolean {
  return item.direction === "payable" && item.origin_type !== "repair";
}

export const METHOD_LABELS: Record<string, string> = {
  cash: "Efectivo",
  card: "Tarjeta",
  transfer: "Transferencia",
  other: "Otro",
};

export const FREQUENCY_LABELS: Record<Frequency, string> = {
  weekly: "Semanal",
  monthly: "Mensual",
  quarterly: "Trimestral",
  yearly: "Anual",
};

export const WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"];

/** «Día 15 de cada mes», «Cada lunes»: the due day as the rule's frequency reads it. */
export function dueDayLabel(rule: Pick<Rule, "frequency" | "due_day">): string {
  if (rule.frequency === "weekly") return `Cada ${WEEKDAYS[rule.due_day] ?? `día ${rule.due_day}`}`;
  const every = { monthly: "de cada mes", quarterly: "de cada trimestre", yearly: "de cada año" }[rule.frequency];
  return `Día ${rule.due_day} ${every}`;
}

/** The human label of an audit action in the history list. */
export const HISTORY_LABELS: Record<string, string> = {
  finance_obligation_created: "Obligación creada",
  finance_payment_recorded: "Pago registrado",
  finance_payment_reversed: "Pago revertido",
  finance_obligation_adjusted: "Importe ajustado",
  finance_obligation_voided: "Obligación anulada",
  finance_obligation_disputed: "Marcada en disputa",
  finance_obligation_dispute_resolved: "Disputa resuelta",
  finance_period_generated: "Período generado",
  finance_period_included: "Período incluido",
};

export function money(value: string | null | undefined, currency = "PEN"): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  try {
    return n.toLocaleString("es-PE", { style: "currency", currency });
  } catch {
    return `${currency} ${value}`;
  }
}
