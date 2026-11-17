"use client";

/**
 * P2 · F5a — «Diseño de la tienda»: V3 (actual) o V4 (vista previa).
 *
 * UN CAMBIO DE TIENDA ENTERA, CON SU PROPIO BOTÓN. Elegir V4 hace que la
 * portada y las fichas de modelo lleven a la V4 y que la V4 se indexe; por eso
 * no se guarda con «Guardar portada» (nadie cambia de diseño por editar un
 * titular) y pide una confirmación expresa. Volver a V3 no pide nada: es la
 * salida segura.
 *
 * La V4 está sujeta a la adopción formal del Manual de Marca V4. La decisión
 * es del propietario; esta pantalla sólo la pone a su alcance y le deja ver
 * la V4 en `/v4` antes de elegirla.
 */

import { useState } from "react";
import {
  ContentValidationError,
  updateStorefrontPage,
  type StorefrontPageContent,
} from "../../lib/internal-api";

type Design = StorefrontPageContent["storefront_design"];

const OPTIONS: { value: Design; label: string }[] = [
  { value: "v3", label: "V3 (actual)" },
  { value: "v4", label: "V4 (vista previa)" },
];

export const V4_CONFIRMATION =
  "¿Cambiar la tienda a la V4?\n\n" +
  "La portada y las fichas de modelo llevarán a la V4 y los buscadores podrán indexarla. " +
  "El carrito, el pago, la cuenta y el panel siguen en V3.\n\n" +
  "Hazlo sólo si el Manual de Marca V4 ya se adoptó formalmente y revisaste /v4. " +
  "Para volver atrás, elige V3 y guarda.";

export function StorefrontDesignSwitch({
  value, readOnly, companyId, onSaved,
}: {
  value: Design;
  readOnly: boolean;
  companyId: number | null;
  onSaved: (design: Design) => void;
}) {
  const [draft, setDraft] = useState<Design>(value);
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  // Sin efecto que copie `value` en `draft`: esta sección se monta después de
  // cargar (y se vuelve a montar al cambiar de empresa), y al guardar `value`
  // pasa a ser justo lo que ya está en `draft`.

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (draft === value) return;
    if (draft === "v4" && !window.confirm(V4_CONFIRMATION)) {
      setDraft(value);
      return;
    }
    setSaving(true);
    setErrors([]);
    try {
      const res = await updateStorefrontPage({ storefront_design: draft }, companyId);
      onSaved(res.page.storefront_design === "v4" ? "v4" : "v3");
    } catch (err) {
      if (err instanceof ContentValidationError) {
        setErrors(err.errors.storefront_design ?? err.errors.__all__ ?? [err.message]);
      } else {
        setErrors([err instanceof Error ? err.message : "No se pudo guardar."]);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <fieldset
        role="radiogroup"
        aria-labelledby="storefront-design-legend"
        aria-describedby="storefront-design-help"
        className="space-y-2"
      >
        <legend id="storefront-design-legend" className="text-xs font-semibold uppercase tracking-wider text-muted">
          Diseño de la tienda
        </legend>
        <div className="flex flex-wrap gap-3">
          {OPTIONS.map((option) => (
            <label
              key={option.value}
              className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-full border border-bd-border px-4 text-sm text-foreground has-[:checked]:border-foreground has-[:disabled]:cursor-default"
            >
              <input
                type="radio"
                name="storefront_design"
                value={option.value}
                checked={draft === option.value}
                disabled={readOnly || saving}
                onChange={() => setDraft(option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
      </fieldset>

      <div id="storefront-design-help" className="space-y-2 text-xs leading-5 text-muted">
        <p>
          La V4 está en vista previa: elígela sólo cuando el Manual de Marca V4 se haya adoptado
          formalmente. Antes de cambiar, revisa cómo se ve en{" "}
          <a href="/v4" target="_blank" rel="noopener noreferrer" className="font-semibold text-foreground underline">
            Ver la V4 (/v4)
          </a>
          .
        </p>
        <p>
          Con V4, la portada y las fichas de modelo llevan a la V4 y los buscadores pueden indexarla. El
          carrito, el pago, la cuenta y el panel siguen en V3. Para volver atrás, elige V3 y guarda. El
          cambio llega a la portada y a las fichas en unos 15 segundos; quien ya tenga la tienda abierta
          puede necesitar recargar la página.
        </p>
      </div>

      {errors.length ? (
        <p role="alert" className="text-sm text-danger">{errors.join(" ")}</p>
      ) : null}

      {!readOnly ? (
        <button
          type="submit"
          disabled={saving || draft === value}
          className="min-h-11 rounded-full bg-foreground px-6 text-sm font-semibold text-background transition-colors hover:bg-foreground/85 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          {saving ? "Guardando…" : "Guardar diseño"}
        </button>
      ) : null}
    </form>
  );
}
