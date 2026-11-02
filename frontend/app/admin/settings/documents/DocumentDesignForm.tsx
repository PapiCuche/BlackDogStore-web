"use client";

/**
 * Ajustes › Documentos — el diseño de los documentos INTERNOS de la empresa (DOC-DESIGN-03).
 *
 * Lo que se elige aquí: tamaño del logo, qué datos de contacto de la empresa se imprimen
 * (los que ya están en sus ajustes: aquí no se escribe ninguno), un pie de página, un
 * mensaje final y si se ocultan las columnas de descuento cuando están vacías.
 *
 * NADA SE GUARDA AL TECLEAR. Cada cambio es un borrador que la vista previa dibuja; «Guardar»
 * muestra primero la lista de lo que cambia y sólo «Confirmar y guardar» lo envía. El mensaje
 * final empieza vacío para toda empresa: lo escribe quien administra la empresa, si quiere.
 *
 * No se aplica a boletas ni facturas: está pendiente de decisión.
 */

import { useEffect, useId, useRef, useState } from "react";
import {
  DocumentDesignFieldError,
  designChanges,
  draftProblems,
  updateDocumentDesign,
  changedFields,
  normaliseText,
  type DocumentDesign,
  type DocumentDesignSettings,
} from "../../lib/document-design-api";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../components/internal-ui";

const CARD = "rounded-xl border border-bd-border bg-surface p-5 sm:p-6";

const CONTACTS: { field: "show_phone" | "show_email" | "show_address" | "show_website" | "show_email_on_roll";
  label: string; value: keyof DocumentDesignSettings["contact"] }[] = [
  { field: "show_phone", label: "Teléfono", value: "phone" },
  { field: "show_email", label: "Correo (documentos A4)", value: "email" },
  { field: "show_email_on_roll", label: "Correo en el ticket de 80 mm y la impresión térmica", value: "email" },
  { field: "show_address", label: "Dirección", value: "address" },
  { field: "show_website", label: "Web", value: "website" },
];

type Props = {
  companyId: number | null;
  settings: DocumentDesignSettings;
  draft: DocumentDesign;
  onDraft: (next: DocumentDesign) => void;
  onSaved: (next: DocumentDesignSettings) => void;
};

export function DocumentDesignForm({ companyId, settings, draft, onDraft, onSaved }: Props) {
  const ids = useId();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const confirmHeading = useRef<HTMLHeadingElement>(null);

  const canManage = settings.can_manage;
  const changes = designChanges(settings.design, draft);
  const problems = draftProblems(draft, settings.limits);
  const hasProblems = Object.keys(problems).length > 0;

  useEffect(() => {
    if (confirming) confirmHeading.current?.focus();
  }, [confirming]);

  const set = <K extends keyof DocumentDesign>(field: K, value: DocumentDesign[K]) => {
    setSaved(false);
    setConfirming(false);
    setFieldErrors((errors) => {
      if (!(field in errors)) return errors;
      const rest = { ...errors };
      delete rest[field];
      return rest;
    });
    onDraft({ ...draft, [field]: value });
  };

  const save = async () => {
    setBusy(true);
    setSaveError(null);
    setFieldErrors({});
    try {
      const next = await updateDocumentDesign(companyId, changedFields(settings.design, draft));
      setConfirming(false);
      setSaved(true);
      onSaved(next);
    } catch (err) {
      setConfirming(false);
      if (err instanceof DocumentDesignFieldError) setFieldErrors(err.fields);
      setSaveError(err instanceof Error ? err.message : "No se pudo guardar el diseño.");
    } finally {
      setBusy(false);
    }
  };

  const errorFor = (field: keyof DocumentDesign) => [problems[field], ...(fieldErrors[field] ?? [])].filter(Boolean) as string[];
  const otherErrors = Object.entries(fieldErrors)
    .filter(([key]) => !(key in draft))
    .flatMap(([, messages]) => messages);

  const textField = (field: "footer_text" | "thank_you_message", label: string, help: string, multiline: boolean) => {
    const limit = settings.limits[field];
    const errors = errorFor(field);
    // Lo que cuenta el servidor: el texto normalizado (espacios repetidos y saltos de línea no suman).
    const count = Array.from(normaliseText(draft[field])).length;
    const common = {
      id: `${ids}-${field}`,
      value: draft[field],
      disabled: !canManage,
      "aria-invalid": errors.length > 0,
      "aria-describedby": `${ids}-${field}-help ${ids}-${field}-count ${ids}-${field}-error`,
      className: internalInputClass,
    };
    return (
      <div className="space-y-1">
        <label htmlFor={`${ids}-${field}`} className="block text-sm font-medium text-foreground">{label}</label>
        {multiline ? (
          <textarea {...common} rows={2} onChange={(e) => set(field, e.target.value)} />
        ) : (
          <input {...common} type="text" autoComplete="off" onChange={(e) => set(field, e.target.value)} />
        )}
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
          <span id={`${ids}-${field}-help`}>{help}</span>
          <span id={`${ids}-${field}-count`} className={count > limit ? "text-danger" : ""}>
            {count}/{limit}
          </span>
        </div>
        <div id={`${ids}-${field}-error`} aria-live="polite">
          {errors.map((message) => (
            <p key={message} className="text-xs text-danger">{message}</p>
          ))}
        </div>
      </div>
    );
  };

  return (
    <form
      className={`${CARD} min-w-0 space-y-5`}
      aria-labelledby={`${ids}-title`}
      onSubmit={(event) => {
        event.preventDefault();
        if (canManage && changes.length && !hasProblems) setConfirming(true);
      }}
    >
      <div>
        <h2 id={`${ids}-title`} className="text-sm font-semibold text-foreground">Diseño de los documentos internos</h2>
        <p className="mt-1 text-xs text-muted">
          Nota de venta (A4 y ticket 80 mm), constancia de pedido e impresión térmica. Boletas y facturas no
          cambian. Los montos, el RUC, la razón social, el número y el aviso «sin validez tributaria» no se
          editan aquí.
        </p>
      </div>

      <fieldset className="min-w-0 space-y-2" disabled={!canManage}>
        <legend className="text-sm font-medium text-foreground">Tamaño del logo</legend>
        <div className="flex flex-wrap gap-2">
          {settings.logo_sizes.map((option) => (
            <label
              key={option.value}
              className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border border-bd-border px-3 py-2 text-sm text-foreground has-[:checked]:border-foreground/40 has-[:checked]:bg-foreground/[0.04]"
            >
              <input
                type="radio"
                name={`${ids}-logo`}
                value={option.value}
                checked={draft.logo_size === option.value}
                onChange={() => set("logo_size", option.value)}
                className="h-4 w-4"
              />
              {option.label}
            </label>
          ))}
        </div>
        <p className="text-xs text-muted">El logo es el que la empresa subió en Ajustes; aquí sólo se elige su tamaño.</p>
      </fieldset>

      <fieldset className="min-w-0 space-y-2" disabled={!canManage}>
        <legend className="text-sm font-medium text-foreground">Datos de contacto que se imprimen</legend>
        {CONTACTS.map((contact) => {
          const value = settings.contact[contact.value];
          return (
            <label key={contact.field} className="flex min-h-11 cursor-pointer items-start gap-3 py-1 text-sm text-foreground">
              <input
                type="checkbox"
                checked={draft[contact.field]}
                onChange={(e) => set(contact.field, e.target.checked)}
                className="mt-1 h-4 w-4 shrink-0"
              />
              <span className="min-w-0">
                <span className="block font-medium">{contact.label}</span>
                <span className="block break-words text-xs text-muted">
                  {value || "Sin dato en los ajustes de la empresa: no se imprime nada."}
                </span>
              </span>
            </label>
          );
        })}
        <p className="text-xs text-muted">El nombre, la razón social, el RUC y la sucursal se imprimen siempre.</p>
      </fieldset>

      {textField("footer_text", "Pie de página",
        "Una línea al pie de cada página (A4) y al final del ticket. Vacío: no se imprime.", true)}
      {textField("thank_you_message", "Mensaje final",
        "Se imprime al final, centrado. Vacío: no se imprime ningún mensaje.", false)}

      <fieldset className="min-w-0" disabled={!canManage}>
        <legend className="sr-only">Columnas</legend>
        <label className="flex min-h-11 cursor-pointer items-start gap-3 py-1 text-sm text-foreground">
          <input
            type="checkbox"
            checked={draft.hide_zero_columns}
            onChange={(e) => set("hide_zero_columns", e.target.checked)}
            className="mt-1 h-4 w-4 shrink-0"
          />
          <span>
            <span className="block font-medium">Ocultar «P. lista» y «Dscto.» cuando ninguna línea tiene descuento</span>
            <span className="block text-xs text-muted">Sólo en A4. Una venta con descuento en alguna línea las conserva.</span>
          </span>
        </label>
      </fieldset>

      {otherErrors.map((message) => (
        <p key={message} role="alert" className="text-sm text-danger">{message}</p>
      ))}
      {saveError ? <p role="alert" className="text-sm text-danger">{saveError}</p> : null}
      {saved ? (
        <p role="status" className="text-sm text-success">
          Guardado. Los documentos internos que se impriman desde ahora (también las reimpresiones) usan este diseño.
        </p>
      ) : null}

      {!canManage ? (
        <p className="text-sm text-muted">Sólo quien administra la empresa («company.manage») puede cambiar el diseño.</p>
      ) : confirming ? (
        <section
          aria-labelledby={`${ids}-confirm`}
          className="space-y-3 rounded-lg border border-foreground/20 bg-foreground/[0.03] p-4"
          data-testid="design-confirm"
        >
          <h3 id={`${ids}-confirm`} ref={confirmHeading} tabIndex={-1} className="text-sm font-semibold text-foreground">
            ¿Guardar estos cambios?
          </h3>
          <ul className="space-y-1 text-sm text-foreground">
            {changes.map((change) => (
              <li key={change.field} className="break-words">
                <span className="font-medium">{change.label}:</span> {change.from} → {change.to}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted">Se aplica a los documentos internos de esta empresa. Boletas y facturas no cambian.</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={`${internalPrimaryButtonClass} min-h-11`} disabled={busy} onClick={() => void save()}>
              {busy ? "Guardando…" : "Confirmar y guardar"}
            </button>
            <button type="button" className={`${internalButtonClass} min-h-11`} disabled={busy} onClick={() => setConfirming(false)}>
              Volver
            </button>
          </div>
        </section>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            className={`${internalPrimaryButtonClass} min-h-11`}
            disabled={!changes.length || hasProblems || busy}
          >
            Guardar cambios
          </button>
          <button
            type="button"
            className={`${internalButtonClass} min-h-11`}
            disabled={!changes.length || busy}
            onClick={() => {
              setFieldErrors({});
              setSaveError(null);
              onDraft(settings.design);
            }}
          >
            Descartar cambios
          </button>
          <span className="text-xs text-muted" aria-live="polite">
            {changes.length ? `${changes.length} cambio(s) sin guardar` : "Sin cambios"}
          </span>
        </div>
      )}
    </form>
  );
}
