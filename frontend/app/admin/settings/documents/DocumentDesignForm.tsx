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
 * En boletas y facturas (PDF A4 y 80 mm) sólo se aplican el tamaño y la alineación del logo, el
 * mensaje final y el pie, después de los datos tributarios (FISCAL-DESIGN-01).
 *
 * DOC-DESIGN-04 (sólo la nota de venta): título comercial (nunca el nombre de un comprobante),
 * logo en milímetros por formato y su alineación, datos opcionales, mensajes posventa y
 * comercial de varios renglones, presentación de la garantía y del desglose de impuestos,
 * espaciado, énfasis del total y orden de los bloques informativos. Cada cambio se ve en la
 * vista previa ANTES de guardar. La identificación legal del papel no se edita aquí.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  DEFAULT_DESIGN,
  DEFAULT_LIMITS,
  DocumentDesignFieldError,
  LOGO_WIDTH_MM,
  MESSAGE_MAX_LINES,
  MULTILINE_FIELDS,
  OPTIONS,
  designChanges,
  draftProblems,
  updateDocumentDesign,
  changedFields,
  normaliseField,
  type BlockKey,
  type DocumentDesign,
  type DocumentDesignSettings,
  type TextField,
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

type InfoField =
  | "show_seller" | "show_order" | "show_channel" | "show_requested_receipt" | "show_counts"
  | "show_amount_words" | "show_customer_contact" | "show_delivery_address";

const INFO: { field: InfoField; label: string; where: string }[] = [
  { field: "show_seller", label: "Vendedor («Atendido por»)", where: "A4, ticket y térmica" },
  { field: "show_order", label: "Número de pedido", where: "A4, ticket y térmica. El número de la nota se imprime siempre" },
  { field: "show_channel", label: "Canal de venta", where: "A4" },
  { field: "show_requested_receipt", label: "Comprobante solicitado", where: "A4, ticket y térmica" },
  { field: "show_counts", label: "Productos y unidades", where: "A4, ticket y térmica" },
  { field: "show_amount_words", label: "Importe en letras («SON: …»)", where: "A4, ticket y térmica" },
  { field: "show_customer_contact", label: "Contacto del cliente (su teléfono)", where: "A4" },
  { field: "show_delivery_address", label: "Dirección de entrega", where: "A4, ticket y térmica" },
];

const CHOICE_CLASS =
  "flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border border-bd-border px-3 py-2 text-sm text-foreground has-[:checked]:border-foreground/40 has-[:checked]:bg-foreground/[0.04]";

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
  const widths = settings.logo_width_mm ?? LOGO_WIDTH_MM;
  const maxLines = settings.max_lines ?? MESSAGE_MAX_LINES;
  const value = <K extends keyof DocumentDesign>(field: K): DocumentDesign[K] => draft[field] ?? DEFAULT_DESIGN[field];
  const changes = designChanges(settings.design, draft);
  const problems = draftProblems(draft, settings.limits, widths);
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

  const textField = (field: TextField, label: string, help: string, multiline: boolean) => {
    const limit = settings.limits[field] ?? DEFAULT_LIMITS[field];
    const errors = errorFor(field);
    const keepsLines = MULTILINE_FIELDS.includes(field);
    // Lo que cuenta el servidor: el texto normalizado (espacios repetidos no suman; en un mensaje
    // de varios renglones, cada salto de línea cuenta uno).
    const count = Array.from(normaliseField(field, value(field) as string)).length;
    const common = {
      id: `${ids}-${field}`,
      value: value(field) as string,
      disabled: !canManage,
      "aria-invalid": errors.length > 0,
      "aria-describedby": `${ids}-${field}-help ${ids}-${field}-count ${ids}-${field}-error`,
      className: internalInputClass,
    };
    return (
      <div className="space-y-1">
        <label htmlFor={`${ids}-${field}`} className="block text-sm font-medium text-foreground">{label}</label>
        {multiline ? (
          <textarea {...common} rows={keepsLines ? 3 : 2} onChange={(e) => set(field, e.target.value)} />
        ) : (
          <input {...common} type="text" autoComplete="off" onChange={(e) => set(field, e.target.value)} />
        )}
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
          <span id={`${ids}-${field}-help`}>
            {help}
            {keepsLines ? ` Hasta ${maxLines} renglones; sin emoji.` : ""}
          </span>
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

  /** Los mensajes del servidor para un campo sin caja de texto (opciones, interruptores, orden). */
  const fieldErrorList = (field: keyof DocumentDesign): ReactNode => {
    const errors = fieldErrors[field] ?? [];
    return errors.length ? (
      <div aria-live="polite">
        {errors.map((message) => <p key={message} className="text-xs text-danger">{message}</p>)}
      </div>
    ) : null;
  };

  const radios = <K extends keyof DocumentDesign>(
    field: K, legend: string, options: { value: string; label: string }[], help?: string,
  ) => (
    <fieldset className="min-w-0 space-y-2" disabled={!canManage}>
      <legend className="text-sm font-medium text-foreground">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <label key={option.value} className={CHOICE_CLASS}>
            <input
              type="radio"
              name={`${ids}-${String(field)}`}
              value={option.value}
              checked={value(field) === option.value}
              onChange={() => set(field, option.value as DocumentDesign[K])}
              className="h-4 w-4"
            />
            {option.label}
          </label>
        ))}
      </div>
      {help ? <p className="text-xs text-muted">{help}</p> : null}
      {fieldErrorList(field)}
    </fieldset>
  );

  const checkbox = (field: keyof DocumentDesign, label: string, help: string) => (
    <label key={field} className="flex min-h-11 cursor-pointer items-start gap-3 py-1 text-sm text-foreground">
      <input
        type="checkbox"
        checked={Boolean(value(field))}
        onChange={(e) => set(field, e.target.checked as DocumentDesign[typeof field])}
        className="mt-1 h-4 w-4 shrink-0"
      />
      <span className="min-w-0">
        <span className="block font-medium">{label}</span>
        <span className="block text-xs text-muted">{help}</span>
        {fieldErrorList(field)}
      </span>
    </label>
  );

  const widthField = (field: "logo_a4_width_mm" | "logo_ticket_width_mm", label: string, range: [number, number]) => (
    <WidthInput
      id={`${ids}-${field}`}
      label={label}
      range={range}
      value={value(field)}
      disabled={!canManage}
      errors={errorFor(field)}
      onChange={(next) => set(field, next)}
    />
  );

  const order = value("block_order");
  const blockLabels = Object.fromEntries((settings.blocks ?? OPTIONS.blocks).map((o) => [o.value, o.label]));
  const move = (index: number, step: -1 | 1) => {
    const next = [...order];
    const target = index + step;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    set("block_order", next as BlockKey[]);
  };
  const warrantyUrl = settings.contact.warranty_url ?? "";

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
          Nota de venta (A4 y ticket 80 mm), constancia de pedido e impresión térmica. En boletas y facturas
          sólo cambian el logo, el mensaje final y el pie. Los montos, el RUC, la razón social y el número no
          se editan aquí.
        </p>
      </div>

      {textField("document_title", "Título de la nota de venta",
        "Se imprime en mayúsculas como encabezado. No puede llamarse factura, boleta ni comprobante.", false)}

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

      <div className="grid min-w-0 gap-4 sm:grid-cols-2">
        {widthField("logo_a4_width_mm", "Ancho del logo en A4 (mm)", widths.a4)}
        {widthField("logo_ticket_width_mm", "Ancho del logo en el ticket de 80 mm (mm)", widths.ticket)}
      </div>
      {radios("logo_a4_align", "Alineación del logo en A4", settings.logo_aligns ?? OPTIONS.logo_aligns)}
      {radios("logo_ticket_align", "Alineación del logo en el ticket de 80 mm", settings.logo_aligns ?? OPTIONS.logo_aligns,
        "La impresión térmica (ESC/POS) usa el mismo tamaño y la misma alineación, con un máximo de 24 mm de alto.")}

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

      <fieldset className="min-w-0" disabled={!canManage}>
        <legend className="text-sm font-medium text-foreground">Datos opcionales de la nota de venta</legend>
        {INFO.map((info) => checkbox(info.field, info.label, info.where))}
        <p className="text-xs text-muted">El cliente, los productos, los importes y el número de la nota se imprimen siempre.</p>
      </fieldset>

      {textField("footer_text", "Pie de página",
        "Una línea al pie de cada página (A4) y al final del ticket. Vacío: no se imprime.", true)}
      {textField("thank_you_message", "Mensaje final",
        "Se imprime después de los totales, centrado y en negrita. Vacío: no se imprime ningún mensaje.", true)}
      {textField("post_sale_message", "Mensaje posventa",
        "Indicaciones para después de la compra (cambios, soporte). Debajo del mensaje final, en la nota de venta.", true)}
      {textField("commercial_message", "Mensaje comercial",
        "Una promoción, tus redes o tu web, en texto. Debajo del mensaje posventa, en la nota de venta.", true)}

      {radios("warranty_mode", "Garantía en la nota de venta", settings.warranty_modes ?? OPTIONS.warranty_modes,
        "Texto completo: la política de Ajustes › Empresa. Resumen y enlace: tu resumen y la dirección de la política completa.")}
      {value("warranty_mode") === "summary" ? (
        <div className="space-y-2">
          {textField("warranty_summary", "Resumen de la garantía",
            "Una línea. Sin resumen ni enlace se imprime el texto completo.", false)}
          <p className="text-xs text-muted">
            {warrantyUrl
              ? `Enlace que se imprime: ${warrantyUrl}`
              : "La empresa no tiene enlace de la política de garantía en Ajustes › Empresa: sólo se imprime el resumen."}
          </p>
        </div>
      ) : null}

      {radios("tax_breakdown_mode", "Desglose referencial de impuestos",
        settings.tax_breakdown_modes ?? OPTIONS.tax_breakdown_modes,
        "Detallado: base imponible e IGV. Resumido: una línea «Incluye IGV». Oculto: sólo subtotal, descuento y total. Los importes son los de la venta; no se recalculan.")}

      {radios("density", "Espaciado de la nota de venta", settings.densities ?? OPTIONS.densities,
        "Cambia el aire entre bloques en A4 y ticket; en la térmica, «Amplio» añade un renglón junto a cada separador.")}
      <fieldset className="min-w-0" disabled={!canManage}>
        <legend className="sr-only">Total</legend>
        {checkbox("emphasize_total", "Destacar el total",
          "Banda sombreada en A4 y letra grande en el ticket y la térmica. Sin destacar sigue en negrita.")}
      </fieldset>

      <fieldset className="min-w-0 space-y-2" disabled={!canManage}>
        <legend className="text-sm font-medium text-foreground">Orden de los bloques informativos</legend>
        <ol className="space-y-1" aria-describedby={`${ids}-order-help`}>
          {order.map((key, index) => (
            <li key={key} className="flex min-h-11 items-center justify-between gap-2 rounded-lg border border-bd-border px-3 py-1 text-sm text-foreground">
              <span>{index + 1}. {blockLabels[key] ?? key}</span>
              <span className="flex gap-1">
                <button
                  type="button"
                  className={`${internalButtonClass} min-h-11 min-w-11`}
                  aria-label={`Subir «${blockLabels[key] ?? key}»`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className={`${internalButtonClass} min-h-11 min-w-11`}
                  aria-label={`Bajar «${blockLabels[key] ?? key}»`}
                  disabled={index === order.length - 1}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </button>
              </span>
            </li>
          ))}
        </ol>
        <p id={`${ids}-order-help`} className="text-xs text-muted">
          Los bloques de después de los totales siguen este orden en A4, ticket y térmica. «Cliente» y «Datos de
          la venta» cambian de columna en A4; en el ticket y la térmica los datos de la venta siguen bajo el
          número. La identificación legal del papel no es un bloque: no se mueve ni se edita aquí.
        </p>
        {fieldErrorList("block_order")}
      </fieldset>

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
          <p className="text-xs text-muted">Se aplica a los documentos de esta empresa; en boletas y facturas, sólo el logo, el mensaje final y el pie.</p>
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

/**
 * El ancho del logo en mm. Mientras se escribe, la caja puede quedar vacía (no salta a 0); al salir
 * de ella vacía, vale 0 («según el tamaño»).
 */
function WidthInput({ id, label, range, value, disabled, errors, onChange }: {
  id: string; label: string; range: [number, number]; value: number; disabled: boolean; errors: string[];
  onChange: (next: number) => void;
}) {
  const [text, setText] = useState(String(value));
  const [seen, setSeen] = useState(value);
  if (value !== seen) {
    // Un cambio que no viene de esta caja (descartar, guardar) se refleja; uno propio ya está escrito.
    setSeen(value);
    if (text === "" || Number(text) !== value) setText(String(value));
  }

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-foreground">{label}</label>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        min={0}
        max={range[1]}
        step={1}
        value={text}
        disabled={disabled}
        aria-invalid={errors.length > 0}
        aria-describedby={`${id}-help ${id}-error`}
        className={`${internalInputClass} max-w-[10rem]`}
        onChange={(e) => {
          setText(e.target.value);
          if (e.target.value !== "") onChange(Number(e.target.value));
        }}
        onBlur={() => {
          if (text === "") {
            setText("0");
            onChange(0);
          }
        }}
      />
      <p id={`${id}-help`} className="text-xs text-muted">
        0 = según el tamaño elegido arriba. Si no, de {range[0]} a {range[1]} mm; el alto tiene un tope.
      </p>
      <div id={`${id}-error`} aria-live="polite">
        {errors.map((message) => (
          <p key={message} className="text-xs text-danger">{message}</p>
        ))}
      </div>
    </div>
  );
}
