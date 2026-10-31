"use client";

import type { Step, StepId, StepStatus } from "../../lib/pos-steps";

const SPOKEN: Record<StepStatus, string> = {
  done: "listo",
  current: "paso actual",
  optional: "opcional",
  blocked: "requiere atención",
  todo: "pendiente",
};

const DOT: Record<StepStatus, string> = {
  done: "bg-success-surface text-success border-success-border",
  current: "bg-foreground text-background border-foreground",
  optional: "bg-background text-muted border-bd-border",
  blocked: "bg-warning-surface text-warning border-warning-border",
  todo: "bg-background text-muted border-bd-border",
};

/** The five steps of a sale as a compact bar. A click takes the operator to that part of the till. */
export function PosSaleSteps({ steps, onGo }: { steps: Step[]; onGo: (id: StepId) => void }) {
  // What the step that needs the operator says, as visible text: touch users never see a `title`.
  const attention = steps.find((s) => (s.status === "blocked" || s.status === "current") && s.note);
  return (
    <div className="space-y-1.5">
    <ol aria-label="Pasos de la venta" className="flex gap-1 overflow-x-auto rounded-xl border border-bd-border bg-surface p-1.5 text-xs">
      {steps.map((step, index) => (
        <li key={step.id} className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => onGo(step.id)}
            aria-current={step.status === "current" ? "step" : undefined}
            aria-label={`${index + 1} ${step.label}, ${SPOKEN[step.status]}${step.note ? `. ${step.note}` : ""}`}
            title={step.note || undefined}
            className="flex w-full min-w-[4.5rem] flex-col items-center gap-1 rounded-lg px-1.5 py-1.5 text-center transition hover:bg-foreground/[0.05]"
          >
            <span className={`flex h-6 w-6 items-center justify-center rounded-full border text-[11px] font-semibold ${DOT[step.status]}`}>
              {step.status === "done" ? "✓" : index + 1}
            </span>
            <span className={`truncate text-[11px] ${step.status === "current" ? "font-semibold text-foreground" : "text-muted"}`}>
              {step.label}
            </span>
          </button>
        </li>
      ))}
    </ol>
    <p role="status" aria-live="polite" className={`px-1 text-xs ${attention?.status === "blocked" ? "text-warning" : "text-muted"}`}>
      {attention ? `${attention.label}: ${attention.note}` : ""}
    </p>
    </div>
  );
}
