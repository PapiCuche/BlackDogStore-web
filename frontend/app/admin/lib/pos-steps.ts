/**
 * SALES-UX-02 — the path of a sale as five steps: Productos → Cliente → Forma de pago → Comprobante → Revisar,
 * and then «Cobrar». This only READS the state of the till and says what is missing. It never prices, totals or
 * authorises anything: the server decides all of that when the sale is previewed and charged.
 */
export type StepId = "products" | "customer" | "payment" | "receipt" | "review";
export type StepStatus = "done" | "current" | "optional" | "blocked" | "todo";

export type Step = { id: StepId; label: string; status: StepStatus; note: string };

export type StepInput = {
  hasLines: boolean;
  /** Devices of the basket not identified yet. */
  unitsMissing: number;
  /** Devices charged by their own price that have none. */
  unpriced: number;
  customerChosen: boolean;
  /** The chosen customer carries a RUC. */
  customerIsRuc: boolean;
  /** `sales_note`, `boleta`, `factura` or '' (none chosen). */
  receiptType: string;
  paymentChosen: boolean;
  isCash: boolean;
  /** The cash typed, as typed. */
  received: string;
  total: number;
  /** The server's total belongs to THIS basket. */
  previewReady: boolean;
  /** The seller confirmed the sale conditions. */
  terms: boolean;
};

const LABELS: Record<StepId, string> = {
  products: "Productos",
  customer: "Cliente",
  payment: "Forma de pago",
  receipt: "Comprobante",
  review: "Revisar",
};

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

export function saleSteps(input: StepInput): Step[] {
  const steps: Step[] = [];
  const add = (id: StepId, status: StepStatus, note = "") => steps.push({ id, label: LABELS[id], status, note });

  if (!input.hasLines) add("products", "todo", "Escanea o busca un producto.");
  else if (input.unitsMissing > 0) {
    add("products", "blocked", `Identifica ${plural(input.unitsMissing, "equipo", "equipos")}: escanea su IMEI o serie.`);
  } else if (input.unpriced > 0) {
    add("products", "blocked", `${plural(input.unpriced, "seminuevo", "seminuevos")} sin precio de venta.`);
  } else add("products", "done");

  if (input.receiptType === "factura") {
    if (input.customerChosen && input.customerIsRuc) add("customer", "done");
    else add("customer", "blocked", "La factura necesita un cliente con RUC.");
  } else if (input.customerChosen) add("customer", "done");
  else add("customer", "optional", "Opcional.");

  if (!input.paymentChosen) add("payment", "todo", "Elige cómo paga el cliente.");
  else if (input.isCash && input.received === "") add("payment", "todo", "Indica cuánto entrega el cliente.");
  else if (input.isCash && Number(input.received) < input.total) add("payment", "blocked", "El efectivo no alcanza para el total.");
  else add("payment", "done");

  if (input.receiptType === "") add("receipt", "todo", "Elige el documento que se entrega.");
  else add("receipt", "done");

  const before = steps.filter((s) => s.id !== "customer").every((s) => s.status === "done")
    && !steps.some((s) => s.status === "blocked");
  if (!before) add("review", "todo", "Completa los pasos anteriores.");
  else if (!input.previewReady) add("review", "todo", "Calculando el total…");
  else if (!input.terms) add("review", "todo", "Confirma las condiciones de venta.");
  else add("review", "done", "Listo para cobrar.");

  // The first step that still needs the operator is «current»; «optional» never is.
  const pending = steps.find((s) => s.status === "todo" || s.status === "blocked");
  if (pending && pending.status === "todo") pending.status = "current";
  return steps;
}
