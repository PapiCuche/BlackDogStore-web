"use client";

// INVENTORY-UNIFIED-01 — the company's own rule about publishing in bulk.
//
// The bulk action of the products list and the product load publish a product
// only when it is active, has a price and has a category. A PICTURE is asked
// only by the company that turns this on (owner's decision, 2026-10-13). It
// starts OFF, and it gates nothing else: the product form publishes as it
// always did.
//
// Saved on its own, with only its field. Whether THIS person may change it is
// the server's answer (`canEdit`), and the server checks again on the save.

import { useState } from "react";
import { updateCompanyConfiguration, type CompanyConfiguration } from "../lib/internal-api";
import { internalButtonClass } from "./internal-ui";

type Props = {
  companyId: number | null;
  requiresImage: boolean;
  canEdit: boolean;
  /** Given the configuration the server answered with after a save. */
  onSaved: (config: CompanyConfiguration) => void;
};

export function PublicationSettings({ companyId, requiresImage, canEdit, onSaved }: Props) {
  const [draft, setDraft] = useState(requiresImage);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const updated = await updateCompanyConfiguration(companyId, { publication_requires_image: draft });
      onSaved(updated);
      setDraft(Boolean(updated.settings.publication_requires_image));
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs leading-5 text-muted">
        Publicar en bloque —desde la lista de productos o con la carga de productos— exige que el producto esté
        activo, con precio de venta y con categoría. Lo que no lo cumple se carga igual y queda como «Solo stock
        interno», con el motivo a la vista.
      </p>
      <div>
        <label className="inline-flex cursor-pointer select-none items-start gap-2 text-sm text-foreground/85">
          <input
            type="checkbox"
            className="mt-0.5 accent-warning"
            checked={draft}
            disabled={!canEdit || saving}
            onChange={(e) => { setDraft(e.target.checked); setSaved(false); }}
          />
          Exigir imagen para publicar en bloque
        </label>
        <p className="mt-1.5 text-[11px] text-muted">
          Activado, un producto sin imagen tampoco se publica en bloque. Apagado, la imagen no se exige. La ficha de
          un producto publica como siempre, con o sin esta opción.
        </p>
      </div>
      {canEdit ? (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void save()} disabled={saving} className={internalButtonClass}>
            {saving ? "Guardando…" : "Guardar regla de publicación"}
          </button>
          {saved ? <span role="status" className="text-sm text-muted">Cambios guardados.</span> : null}
        </div>
      ) : null}
      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    </div>
  );
}
