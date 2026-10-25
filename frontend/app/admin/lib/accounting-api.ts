/**
 * ACCOUNTING-03 — the client of the read-only accounting centre (#174, ACCOUNTING-02).
 *
 *   GET /api/admin/accounting/operations/        view A
 *   GET /api/admin/accounting/fiscal-documents/  view B
 *   GET /api/admin/accounting/pending/           view C
 *   GET /api/admin/accounting/summary/           totals + view C counts
 *
 * The server decides everything that matters: which company and branches the
 * caller may see, whether the buyer's document is masked (`unmasked`), which
 * rules are still provisional (`policy`), and the state of every fiscal
 * document. This module only builds the query and types the answer. Amounts
 * travel as strings with two decimals and are never recomputed here.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

export type PeriodPreset = "day" | "week" | "month" | "year" | "tax_period" | "range";
export type Channel = "online" | "pos" | "service";
export type Kind = "sale" | "repair";
export type DocumentFilter =
  | "factura"
  | "boleta"
  | "nc"
  | "nd"
  | "nota_interna"
  | "sin_documento"
  | "anulado"
  | "pendiente"
  | "rechazado";
export type FiscalEnvironment = "production" | "beta";

export type AccountingFilters = {
  period: PeriodPreset;
  /** Anchor day for day/week/month/year, YYYY-MM-DD. Empty: the company's today. */
  date: string;
  /** YYYYMM, for `tax_period`. */
  taxPeriod: string;
  from: string;
  to: string;
  branch: number | null;
  channel: Channel | "";
  kind: Kind | "";
  documents: DocumentFilter[];
  environment: FiscalEnvironment;
};

export const DEFAULT_FILTERS: AccountingFilters = {
  period: "month",
  date: "",
  taxPeriod: "",
  from: "",
  to: "",
  branch: null,
  channel: "",
  kind: "",
  documents: [],
  environment: "production",
};

export type Policy = {
  status: string;
  pending_owner_decision: string[];
  service_revenue_basis: string;
  sale_period_field: string;
  granted_to_holders_of: string;
  exclude_test_payments: boolean;
};

export type Envelope = {
  company: { id: number; name: string };
  period: { kind: string; from: string; to: string; timezone: string };
  scope: {
    company_wide: boolean;
    branches: { id: number; name: string }[];
    includes_unbranched: boolean;
  };
  policy: Policy;
  notice: string;
  unmasked: boolean;
};

export type Customer = { name: string; document_type: string; document_number: string };
export type BranchRef = { id: number; name: string } | null;

export type Totals = {
  operations: number;
  sales_gross: string;
  sales_taxable: string;
  sales_tax: string;
  late_charges: string;
  gross: string;
  refunds_confirmed: string;
  refunds_pending: string;
  net: string;
  service_collections: string;
  income: string;
  excluded_test: { operations: number; gross: string; refunds_confirmed: string };
  currencies: string[];
};

export type OperationDocument = { kind: string; number: string; state: string; environment: string };

export type OperationRow = {
  key: string;
  kind: Kind;
  order_id?: number;
  repair_order_id?: number;
  repair_number?: string;
  branch: BranchRef;
  channel: Channel;
  seller: string | null;
  customer: Customer;
  currency: string;
  commercial_state: string;
  excluded_from_income: boolean;
  primary_document: { kind: string; number: string; environment?: string } | null;
  flags: string[];
  row_type: "operation" | "adjustment";
  event: string;
  date: string | null;
  amount: string;
  gross?: string;
  taxable?: string | null;
  tax?: string | null;
  tax_rate?: string | null;
  breakdown_source?: string;
  refunds_confirmed?: string;
  kept?: string;
  payments?: number;
  affects_net?: boolean;
  documents: OperationDocument[];
};

export type FiscalRow = {
  id: number;
  key: string;
  order_id: number;
  type: string;
  type_label: string;
  document_id: string;
  issued_at: string;
  environment: string;
  test_environment: boolean;
  customer: Customer;
  currency: string;
  taxable: string;
  tax: string;
  total: string;
  tax_rate: string;
  state: { code: string; label: string; settled: boolean };
  sunat: { code: string; message: string };
  original: string | null;
  granted: { granted_at: string | null; method: string; not_granted: boolean };
  cdr: { present: boolean; response_code: string; hash_ok: boolean | null; authenticity: string };
  summary: { identifier: string; status: string } | null;
};

export type PendingRow = {
  key: string;
  code: string;
  severity: "regularizar" | "revisar" | "info";
  message: string;
  kind: Kind;
  order_id: number | null;
  repair_order_id: number | null;
  branch: BranchRef;
  channel: Channel;
  date: string | null;
  document: string | null;
};

export type Page<T> = { count: number; results: T[]; next_cursor: string | null };

export type OperationsAnswer = Envelope & Page<OperationRow> & { totals: Totals };
export type FiscalAnswer = Envelope &
  Page<FiscalRow> & { environment: FiscalEnvironment; totals: Record<string, { count: number; total: string }> };
export type PendingAnswer = Envelope & Page<PendingRow> & { counts: Record<string, number> };
export type SummaryAnswer = Envelope & { totals: Totals; pending: Record<string, number> };

export class AccountingError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** The query string every view shares. Only what the person chose travels. */
export function buildAccountingQuery(
  companyId: number | null,
  filters: AccountingFilters,
  extra: { cursor?: string | null; pageSize?: number; environment?: boolean } = {},
): string {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  if (filters.period === "tax_period") {
    if (filters.taxPeriod) qs.set("tax_period", filters.taxPeriod);
  } else if (filters.period === "range") {
    qs.set("period", "range");
    if (filters.from) qs.set("from", filters.from);
    if (filters.to) qs.set("to", filters.to);
  } else {
    qs.set("period", filters.period);
    if (filters.date) qs.set("date", filters.date);
  }
  if (filters.branch) qs.set("branch", String(filters.branch));
  if (filters.channel) qs.set("channel", filters.channel);
  if (filters.kind) qs.set("kind", filters.kind);
  if (filters.documents.length) qs.set("doc", [...filters.documents].sort().join(","));
  if (extra.environment) qs.set("environment", filters.environment);
  if (extra.cursor) qs.set("cursor", extra.cursor);
  if (extra.pageSize) qs.set("page_size", String(extra.pageSize));
  return qs.toString();
}

async function read<T>(path: string, query: string, fallback: string): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}/admin/accounting/${path}/?${query}`);
  if (!res.ok) {
    let detail = fallback;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      // A body that is not JSON keeps the fallback sentence.
    }
    if (res.status === 403) detail = "No tienes permiso para ver el centro contable.";
    if (res.status === 404) detail = "No se encontró la empresa o no tienes acceso a ella.";
    throw new AccountingError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export function fetchOperations(companyId: number | null, filters: AccountingFilters, cursor?: string | null) {
  return read<OperationsAnswer>(
    "operations", buildAccountingQuery(companyId, filters, { cursor }), "No se pudieron cargar las operaciones.");
}

export function fetchFiscalDocuments(companyId: number | null, filters: AccountingFilters, cursor?: string | null) {
  return read<FiscalAnswer>(
    "fiscal-documents",
    buildAccountingQuery(companyId, filters, { cursor, environment: true }),
    "No se pudieron cargar los comprobantes.",
  );
}

export function fetchPending(companyId: number | null, filters: AccountingFilters, cursor?: string | null) {
  return read<PendingAnswer>(
    "pending", buildAccountingQuery(companyId, filters, { cursor }), "No se pudieron cargar los pendientes.");
}

export function fetchSummary(companyId: number | null, filters: AccountingFilters) {
  return read<SummaryAnswer>("summary", buildAccountingQuery(companyId, filters), "No se pudo cargar el resumen.");
}

// ---------------------------------------------------------------------------
// Words. What the server's codes mean to a person; nothing here decides.
// ---------------------------------------------------------------------------

export const CHANNEL_LABELS: Record<Channel, string> = {
  online: "Tienda online",
  pos: "Punto de venta",
  service: "Servicio técnico",
};

export const KIND_LABELS: Record<Kind, string> = { sale: "Venta", repair: "Servicio técnico" };

export const DOCUMENT_LABELS: Record<DocumentFilter, string> = {
  factura: "Factura",
  boleta: "Boleta",
  nc: "Nota de crédito",
  nd: "Nota de débito",
  nota_interna: "Nota de venta interna",
  sin_documento: "Sin documento",
  anulado: "Anulado",
  pendiente: "Pendiente",
  rechazado: "Rechazado",
};

export const COMMERCIAL_STATE_LABELS: Record<string, string> = {
  pagada: "Pagada",
  anulada: "Anulada",
  devuelta: "Devuelta",
  reembolsada: "Reembolsada",
  servicio: "Servicio",
};

export const EVENT_LABELS: Record<string, string> = {
  sale: "Venta",
  service_collection: "Cobro de servicio",
  refund: "Reembolso confirmado",
  refund_pending: "Reembolso pendiente",
  late_charge: "Cobro tardío",
};

export const SEVERITY_LABELS: Record<PendingRow["severity"], string> = {
  regularizar: "Regularizar",
  revisar: "Revisar",
  info: "Informativo",
};

/** The provisional rules as a person reads them (P1–P4 of the ADR). */
export function policyLines(policy: Policy): string[] {
  const lines: string[] = [];
  if (policy.service_revenue_basis === "collection") {
    lines.push("P1 · El servicio técnico cuenta como ingreso cuando se cobra (pagos no reversados).");
  }
  if (policy.sale_period_field === "paid_at") {
    lines.push("P2 · Una venta pertenece al período en que se pagó; lo no pagado no es ingreso.");
  }
  if (policy.granted_to_holders_of) {
    lines.push(`P3 · Acceso concedido a quienes administran la empresa (${policy.granted_to_holders_of}).`);
  }
  if (policy.exclude_test_payments) {
    lines.push("P4 · Los pagos hechos con credenciales de prueba quedan fuera del ingreso y se muestran aparte.");
  }
  return lines;
}

export function money(value: string | null | undefined, currency = "PEN"): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString("es-PE", { style: "currency", currency });
}
