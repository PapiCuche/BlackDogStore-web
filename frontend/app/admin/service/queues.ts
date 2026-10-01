/**
 * SVC-NAV-01 — the work queues of the technical service.
 *
 * A queue is a way of LOOKING at orders: it groups status codes so each stage
 * of the workshop has its own list. The codes are the server's
 * (`RepairStatusCode`), and the grouping follows the lifecycle in
 * `service_services.TRANSITIONS`:
 *
 *   received → diagnosing → waiting_approval → approved → in_repair ⇄
 *   waiting_parts → repaired → quality_control → ready_for_pickup → delivered
 *
 * A queue never decides what an order may do next. Transitions are offered by
 * the server on each order (`available_transitions`) and refused by it.
 */
export type ServiceQueueConfig = {
  title: string;
  description: string;
  /** Status codes this queue lists. `null` is the whole workshop. */
  statuses: readonly string[] | null;
  /** A second, read-mostly set reachable from the same screen. */
  history?: { label: string; statuses: readonly string[] };
  /** `open` shows the intake form at once; `toggle` offers it behind a button. */
  intake: "open" | "toggle" | "none";
  /** Whether the list opens on the caller's own assignments. */
  mine: "technician" | "never";
};

export const SERVICE_QUEUES = {
  intake: {
    title: "Recepción",
    description: "Ingreso de equipos al taller y órdenes recién recibidas.",
    statuses: ["received"],
    intake: "open",
    mine: "never",
  },
  orders: {
    title: "Órdenes de servicio",
    description: "Todas las órdenes del taller que tu cuenta alcanza.",
    statuses: null,
    intake: "toggle",
    mine: "technician",
  },
  diagnostics: {
    title: "Diagnóstico",
    description: "Equipos en diagnóstico y cotizaciones a la espera de respuesta.",
    statuses: ["diagnosing", "waiting_approval"],
    intake: "none",
    mine: "technician",
  },
  repairs: {
    title: "Reparación",
    description: "Trabajos aprobados, en curso o detenidos por un repuesto.",
    statuses: ["approved", "in_repair", "waiting_parts"],
    intake: "none",
    mine: "technician",
  },
  quality: {
    title: "Control de calidad",
    description: "Equipos reparados pendientes de verificación antes de la entrega.",
    statuses: ["repaired", "quality_control"],
    intake: "none",
    mine: "technician",
  },
  delivery: {
    title: "Entrega",
    description: "Equipos listos para que el cliente los recoja.",
    statuses: ["ready_for_pickup"],
    history: { label: "Entregadas", statuses: ["delivered"] },
    intake: "none",
    mine: "never",
  },
} as const satisfies Record<string, ServiceQueueConfig>;

export type ServiceQueueKey = keyof typeof SERVICE_QUEUES;
