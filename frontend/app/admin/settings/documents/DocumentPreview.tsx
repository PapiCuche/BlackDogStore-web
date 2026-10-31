"use client";

/**
 * Ajustes › Documentos — vista previa de los papeles impresos de la empresa.
 *
 * TODO LO QUE SE VE ES UNA MUESTRA. El servidor dibuja el PDF con datos
 * inventados y con «MUESTRA · NO FISCAL» en cada página; la insignia de esta
 * pantalla está SIEMPRE, aunque el PDF falle o todavía no haya llegado.
 *
 * LO QUE ESTA PANTALLA NO HACE: no guarda nada. No hay controles de diseño
 * (tamaño del logo, cabecera, pie, bloque de garantía): están pendientes de la
 * decisión del propietario y un control que no guarda sería un engaño.
 *
 * CARRERAS. Cada elección cancela la petición anterior (AbortController) y el
 * objeto-URL del PDF anterior se revoca al cambiar y al desmontar, de modo que
 * una respuesta lenta nunca pisa a una más reciente.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  DocumentPreviewError,
  PREVIEW_OPTIONS,
  fetchDocumentPreview,
  type PreviewKind,
} from "../../lib/document-preview-api";
import { internalButtonClass } from "../../components/internal-ui";

const CARD = "rounded-xl border border-bd-border bg-surface p-5 sm:p-6";

type Loaded = { url: string; fileName: string };

export function DocumentPreview({ companyId }: { companyId: number | null }) {
  const [kind, setKind] = useState<PreviewKind>(PREVIEW_OPTIONS[0].kind);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [attempt, setAttempt] = useState(0);
  const urlRef = useRef<string | null>(null);
  const legendId = useId();

  const release = useCallback(() => {
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    release();
    void (async () => {
      try {
        const { blob, fileName } = await fetchDocumentPreview(kind, companyId, controller.signal);
        if (controller.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        setLoaded({ url, fileName });
        setLoading(false);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(
          err instanceof DocumentPreviewError || err instanceof Error
            ? err.message
            : "No se pudo generar la vista previa.",
        );
        setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [kind, companyId, attempt, release]);

  useEffect(() => release, [release]);

  /** Cada pedido nuevo (otro documento, o reintentar) empieza limpio: sin PDF ni error del anterior. */
  const request = useCallback((next: PreviewKind) => {
    setLoaded(null);
    setError(null);
    setLoading(true);
    setKind(next);
    setAttempt((n) => n + 1);
  }, []);

  const option = PREVIEW_OPTIONS.find((o) => o.kind === kind)!;

  return (
    <div className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
      <fieldset className={`${CARD} min-w-0`}>
        <legend id={legendId} className="px-1 text-sm font-semibold text-foreground">
          Documento
        </legend>
        <div role="radiogroup" aria-labelledby={legendId} className="mt-2 space-y-1">
          {PREVIEW_OPTIONS.map((o) => (
            <label
              key={o.kind}
              className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-transparent px-2 py-2 text-sm text-foreground hover:bg-foreground/[0.03] has-[:checked]:border-bd-border has-[:checked]:bg-foreground/[0.04]"
            >
              <input
                type="radio"
                name="document-preview-kind"
                value={o.kind}
                checked={kind === o.kind}
                onChange={() => request(o.kind)}
                className="mt-1 h-4 w-4 shrink-0"
              />
              <span className="min-w-0">
                <span className="block font-medium">{o.label}</span>
                <span className="block text-xs text-muted">{o.paper}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <section className={`${CARD} min-w-0 space-y-4`} aria-label="Vista previa">
        <div className="flex flex-wrap items-center gap-2">
          <span
            data-testid="sample-badge"
            className="inline-flex items-center rounded-md border border-danger px-2.5 py-1 text-xs font-bold uppercase tracking-wider text-danger"
          >
            MUESTRA · NO FISCAL
          </span>
          <span className="text-xs text-muted">{option.label} · {option.paper}</span>
        </div>
        {option.fiscal ? (
          <p className="text-sm text-muted" data-testid="fiscal-note">
            Es una muestra sin validez tributaria: el emisor, los montos y el código QR son inventados
            y no se envía nada a SUNAT.
          </p>
        ) : null}

        <div role="status" aria-live="polite" className="text-sm text-muted">
          {loading ? "Generando la vista previa…" : loaded ? "Vista previa lista." : ""}
        </div>

        {error ? (
          <div role="alert" className="space-y-3 rounded-lg border border-danger/40 p-4 text-sm text-danger">
            <p>{error}</p>
            <button type="button" className={internalButtonClass} onClick={() => request(kind)}>
              Reintentar
            </button>
          </div>
        ) : null}

        {loaded ? (
          <>
            <iframe
              title={`Vista previa: ${option.label}`}
              src={loaded.url}
              className="hidden h-[70vh] min-h-[28rem] w-full rounded-lg border border-bd-border bg-background md:block"
            />
            <p className="text-sm text-muted md:hidden">
              En el teléfono el PDF se ve mejor en su propia pestaña.
            </p>
            <div className="flex flex-wrap gap-2">
              <a
                href={loaded.url}
                target="_blank"
                rel="noopener noreferrer"
                className={`${internalButtonClass} inline-flex min-h-11 items-center`}
              >
                Abrir PDF
              </a>
              <a
                href={loaded.url}
                download={loaded.fileName}
                className={`${internalButtonClass} inline-flex min-h-11 items-center`}
              >
                Descargar PDF
              </a>
            </div>
          </>
        ) : null}

        <div className="space-y-2 border-t border-bd-border pt-4 text-sm text-muted">
          <p>
            <span className="font-semibold text-foreground">Lo que no se edita aquí:</span> los montos,
            los nombres fiscales, la serie y el correlativo, el código QR, la firma, la constancia
            de SUNAT (CDR) y el texto legal. Los dibuja el sistema y no dependen de esta pantalla.
          </p>
          <p>
            <span className="font-semibold text-foreground">Pendiente de decisión del propietario:</span>{" "}
            las opciones de diseño por empresa (tamaño del logo, cabecera, pie y bloque de garantía).
            Todavía no hay nada que guardar, por eso esta pantalla no tiene controles de edición.
          </p>
        </div>
      </section>
    </div>
  );
}
