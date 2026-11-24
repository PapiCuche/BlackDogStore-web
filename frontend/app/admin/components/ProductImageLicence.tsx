"use client";

/**
 * IMG-GATE-01 — the licence of ONE picture of a product's gallery, in the panel.
 *
 * Owner, 2026-11-23: «Las nuevas fotografías requieren procedencia y autorización comprobables antes de publicarse.»
 * A device variant's own pictures are shown on the shop only once their licence is verified, as a finish's are; until
 * then the card says «No visible en la tienda hasta verificar la licencia». The check is the finish images' flow, on
 * the same file (`PATCH …/images/<id>/` with the provenance and `license_verified`): a known origin first («Registrar
 * procedencia»), then «Verificar licencia», which records who and when. Changing the provenance clears the check. A
 * product without a model shows its pictures as before (the server says `requires_license: false`), and its licence
 * may still be recorded. An address that is not an upload of the shop (another host) has no licence to verify.
 *
 * The server is the authority: it says whether the shop shows it (`public`) and refuses what does not apply.
 */

import { useRef, useState } from "react";

import type { AdminProductImage, ProductImagePatch } from "../../lib/admin";
import { isManagedStorefrontImage } from "../../lib/storefront-media";
import { provenanceErrors, type Provenance, type SourceKind } from "../lib/catalog-presentation-api";
import { ProvenanceFields, SOURCE_KIND_LABELS } from "./ProvenanceFields";
import { internalButtonClass, internalPrimaryButtonClass } from "./internal-ui";

export const NOT_PUBLIC_NOTICE = "No visible en la tienda hasta verificar la licencia";

const KINDS = (Object.entries(SOURCE_KIND_LABELS) as [SourceKind, string][]).map(([value, label]) => ({ value, label }));
const smallButton = `${internalButtonClass} min-h-9 px-3 py-1.5 text-xs`;

const formatDate = (iso: string | null | undefined) => {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("es-PE", { day: "2-digit", month: "2-digit", year: "numeric" });
};

export function ProductImageLicence({
  image, name, readOnly, busy, onPatch,
}: {
  image: AdminProductImage;
  /** «imagen 2»: names the picture in the controls' accessible names. */
  name: string;
  readOnly: boolean;
  busy: boolean;
  /** Saves the change; true when the server accepted it. */
  onPatch: (data: ProductImagePatch) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [provenance, setProvenance] = useState<Provenance>({ source_kind: "", license: "", source_url: "", attribution: "" });
  const [errors, setErrors] = useState<Partial<Record<keyof Provenance, string>>>({});
  const origin = useRef<HTMLSelectElement>(null);
  const source = image.source;
  if (!source) return null;            // an older server: nothing to say

  const managed = isManagedStorefrontImage(image.url);
  const verified = source.license_verified;
  const unknown = source.kind === "unknown";
  const hidden = image.requires_license === true && image.public === false;

  function startEditing() {
    if (!source) return;
    setProvenance({
      source_kind: unknown ? "" : source.kind, license: source.license, source_url: source.url,
      attribution: source.attribution,
    });
    setErrors({});
    setEditing(true);
    // After the form renders: its first field.
    setTimeout(() => origin.current?.focus(), 0);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const found = provenanceErrors(provenance);
    setErrors(found);
    if (Object.keys(found).length) return;
    const ok = await onPatch({
      source_kind: provenance.source_kind, license: provenance.license, source_url: provenance.source_url,
      attribution: provenance.attribution,
    });
    if (ok) setEditing(false);
  }

  return (
    <div className="mt-3 space-y-2 text-xs" data-testid="product-image-licence">
      <dl className="space-y-1">
        <div className="flex flex-wrap gap-x-1"><dt className="text-muted">Origen:</dt><dd className="text-foreground">{managed ? source.kind_label : "Dirección externa"}</dd></div>
        {source.license ? (
          <div className="flex flex-wrap gap-x-1"><dt className="text-muted">Licencia:</dt><dd className="break-words text-foreground">{source.license}</dd></div>
        ) : null}
        <div className="flex flex-wrap gap-x-1">
          <dt className="text-muted">Licencia verificada:</dt>
          <dd className={verified ? "text-success" : "text-warning"}>
            {verified
              ? `Sí${source.license_verified_by ? `, por ${source.license_verified_by}` : ""}${source.license_verified_at ? ` el ${formatDate(source.license_verified_at)}` : ""}`
              : "Sin verificar"}
          </dd>
        </div>
      </dl>
      {hidden ? (
        <p data-testid="product-image-not-public" className="font-semibold text-warning">{NOT_PUBLIC_NOTICE}</p>
      ) : null}
      {!managed ? (
        <p className="text-muted">
          Es una dirección de otro sitio: su licencia no se puede verificar. Sustitúyela por una foto subida desde el panel.
        </p>
      ) : null}

      {!readOnly && managed ? (
        editing ? (
          <form onSubmit={save} noValidate aria-label={`Procedencia de la ${name}`} className="space-y-3 rounded-lg border border-bd-border p-3">
            <ProvenanceFields value={provenance} onChange={setProvenance} errors={errors} kinds={KINDS} disabled={busy} firstFieldRef={origin} />
            <p className="text-muted">Cambiar la procedencia quita la verificación de la licencia.</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className={smallButton} onClick={() => setEditing(false)} disabled={busy}>Cancelar</button>
              <button type="submit" className={`min-h-9 ${internalPrimaryButtonClass} text-xs`} disabled={busy}>Guardar procedencia</button>
            </div>
          </form>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            {unknown ? (
              <p className="w-full text-muted">Registra de dónde sale la imagen para poder verificar su licencia.</p>
            ) : verified ? (
              <button type="button" className={smallButton} disabled={busy} aria-label={`Quitar la verificación de la licencia: ${name}`}
                onClick={() => void onPatch({ license_verified: false })}>
                Quitar verificación
              </button>
            ) : (
              <button type="button" className={smallButton} disabled={busy} aria-label={`Verificar licencia: ${name}`}
                onClick={() => void onPatch({ license_verified: true })}>
                Verificar licencia
              </button>
            )}
            <button type="button" className={smallButton} disabled={busy} aria-label={`${unknown ? "Registrar" : "Editar"} procedencia: ${name}`} onClick={startEditing}>
              {unknown ? "Registrar procedencia" : "Editar procedencia"}
            </button>
          </div>
        )
      ) : null}
    </div>
  );
}
