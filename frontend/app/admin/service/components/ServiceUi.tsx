"use client";

import { useState } from "react";
import {
  Button,
  ErrorNote,
  Field,
  Panel as BasePanel,
  Pill,
} from "../../components/internal-ui";

export { Button, ErrorNote, Field, Pill };

export function Panel({
  title,
  subtitle,
  children,
  actions,
}: {
  title?: string;
  subtitle?: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <BasePanel title={title} description={subtitle} action={actions}>
      {children}
    </BasePanel>
  );
}

/**
 * A destructive or physical action, behind one deliberate click.
 *
 * Presentation is shared with the rest of internal control; the confirmation
 * behavior remains specific to this service-console helper.
 */
export function Confirm({
  label,
  question,
  onConfirm,
  disabled,
  tone = "default",
}: {
  label: string;
  question: string;
  onConfirm: () => void;
  disabled?: boolean;
  tone?: "default" | "primary" | "danger";
}) {
  const [asking, setAsking] = useState(false);

  if (!asking) {
    return (
      <Button onClick={() => setAsking(true)} disabled={disabled} tone={tone}>
        {label}
      </Button>
    );
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-2 rounded-lg border border-bd-border bg-background px-2 py-1.5">
      <span className="text-[11px] text-muted-foreground">{question}</span>
      <Button
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
        tone={tone}
      >
        Sí
      </Button>
      <Button onClick={() => setAsking(false)}>No</Button>
    </span>
  );
}

export function dateTime(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("es-PE", { dateStyle: "medium", timeStyle: "short" });
}
