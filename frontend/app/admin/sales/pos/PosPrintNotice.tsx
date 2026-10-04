"use client";

/**
 * Qué pasó con el ticket de la venta que se acaba de cobrar.
 *
 * EL TICKET LO IMPRIME LA TIENDA. Si el local tiene una impresora propia, el
 * servidor ya dejó el trabajo en su cola al confirmar la venta: quien cobra
 * desde un teléfono no abre ningún diálogo de impresión, sólo lee esto.
 *
 * Sin impresora del local (`job` nulo) no se pinta nada y la caja ofrece
 * imprimir desde el navegador, como siempre.
 */

export type PosPrintJob = {
  id: number; status: string; printer: string;
  /** ¿Hay un agente de este local recogiendo tickets ahora? */
  agent_online?: boolean;
};

export function PosPrintNotice({
  job, onRetry,
}: {
  job: PosPrintJob | null | undefined;
  onRetry?: (jobId: number) => void;
}) {
  if (!job) return null;

  if (job.status === "failed" || job.status === "cancelled") {
    return (
      <div
        role="alert"
        className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-left text-sm text-danger"
      >
        <p>La impresora «{job.printer}» no pudo imprimir el ticket.</p>
        {onRetry ? (
          <button
            type="button"
            onClick={() => onRetry(job.id)}
            className="mt-2 min-h-11 rounded-lg border border-danger-border px-4 text-sm font-semibold"
          >
            Reenviar a la impresora
          </button>
        ) : null}
      </div>
    );
  }

  if (job.status === "pending" && job.agent_online === false) {
    // «En cola» no es «enviado»: si nadie en el local recoge tickets, se dice,
    // y la caja sigue ofreciendo imprimir desde aquí.
    return (
      <p
        role="status"
        className="rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-left text-sm text-warning"
      >
        El ticket está en cola para «{job.printer}», pero el agente de impresión de este local
        no está conectado. Saldrá cuando vuelva; si lo necesitas ya, imprímelo desde este equipo.
      </p>
    );
  }

  return (
    <p
      role="status"
      className="rounded-lg border border-bd-border bg-surface-2 px-4 py-3 text-left text-sm text-foreground"
    >
      {job.status === "printed"
        ? `Ticket impreso en «${job.printer}».`
        : `Ticket enviado a la impresora «${job.printer}».`}{" "}
      <span className="text-muted">No hace falta imprimir desde este equipo.</span>
    </p>
  );
}
