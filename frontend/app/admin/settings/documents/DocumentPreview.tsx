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
 * una respuesta lenta nunca pisa a una más reciente. El que está en pantalla no
 * se revoca mientras se muestra: sólo cuando se pide otro (y el visor ya se
 * quitó) o al salir, y aun entonces con unos segundos de margen (`release`).
 *
 * EL VISOR ES UN <object>, NO UN <iframe> (DOC-PREVIEW-03). Un documento `blob:`
 * hereda la política de seguridad de la página que lo creó, y la de esta es
 * `frame-ancestors 'none'` (next.config.ts). Safari la aplica al blob dentro de
 * un marco y lo deja en blanco («Refused to load blob:… because it does not
 * appear in the frame-ancestors directive»), mientras la pantalla decía «Vista
 * previa lista». Chrome no la aplica, por eso sólo fallaba en Safari. Un
 * <object type="application/pdf"> lo dibuja el visor de PDF del navegador sin
 * abrir un marco, en Safari y en Chrome, y la política sigue intacta.
 *
 * SIN VISOR EN LA PÁGINA (`pdf-inline-support.ts`): navegadores sin visor de PDF
 * e iPhone/iPad no dibujan ningún recuadro: una tarjeta explica por qué y quedan
 * «Abrir PDF» y «Descargar PDF». Entonces la pantalla NO dice «Vista previa
 * lista», porque no hay nada que mirar aquí.
 */

import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import {
  DocumentPreviewError,
  PREVIEW_OPTIONS,
  fetchDocumentPreview,
  type PreviewKind,
} from "../../lib/document-preview-api";
import { pdfInlineSupport, type PdfInlineSupport } from "../../lib/pdf-inline-support";
import { internalButtonClass } from "../../components/internal-ui";

const CARD = "rounded-xl border border-bd-border bg-surface p-5 sm:p-6";

type Loaded = { url: string; fileName: string };

/** Cuánto vive un PDF ya retirado de la pantalla antes de revocar su objeto-URL. */
export const REVOKE_DELAY_MS = 10_000;

/** La capacidad del navegador no cambia mientras la página está abierta: no hay nada a lo que suscribirse. */
const noSubscription = () => () => undefined;
const readSupport = (): PdfInlineSupport => pdfInlineSupport(window.navigator);
/** En el servidor no hay navegador: no se promete visor. */
const serverSupport = (): PdfInlineSupport => "no-viewer";

const FALLBACK_REASON: Record<Exclude<PdfInlineSupport, "inline">, string> = {
  "apple-touch":
    "En iPhone o iPad el PDF se lee entero en su propia pestaña: dentro de esta página el sistema sólo "
    + "mostraría una imagen fija de la primera hoja.",
  "no-viewer":
    "Este navegador no muestra PDF dentro de la página. Ábrelo en una pestaña nueva o descárgalo; "
    + "el archivo es el mismo.",
};

export function DocumentPreview({ companyId }: { companyId: number | null }) {
  const [kind, setKind] = useState<PreviewKind>(PREVIEW_OPTIONS[0].kind);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [attempt, setAttempt] = useState(0);
  const urlRef = useRef<string | null>(null);
  const legendId = useId();
  const support = useSyncExternalStore(noSubscription, readSupport, serverSupport);
  const inline = support === "inline";

  /**
   * Suelta el PDF anterior. La revocación espera un poco: el visor de WebKit sigue leyendo el blob un instante
   * después de quitar el <object>, y revocarlo en el acto deja «WebKitBlobResource error 3» en la consola (visto en
   * WebKit al recorrer los siete documentos seguidos). El temporizador sobrevive al desmontaje, así que salir de la
   * pantalla también lo libera.
   */
  const release = useCallback(() => {
    const url = urlRef.current;
    if (!url) return;
    urlRef.current = null;
    window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
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
          {loading
            ? "Generando la vista previa…"
            : loaded
              ? inline
                ? "Vista previa lista."
                : "El PDF de muestra está listo para abrir o descargar."
              : ""}
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
            {support === "inline" ? (
              <>
                <object
                  key={loaded.url}
                  type="application/pdf"
                  data={loaded.url}
                  title={`Vista previa: ${option.label}`}
                  data-testid="pdf-viewer"
                  className="block h-[70vh] min-h-[28rem] w-full rounded-lg border border-bd-border bg-background"
                >
                  {/* Sólo se ve si el navegador no pudo dibujar el PDF. */}
                  <span className="flex h-full items-center justify-center p-6 text-center text-sm text-muted">
                    Este navegador no pudo mostrar el PDF aquí. Usa «Abrir PDF» o «Descargar PDF».
                  </span>
                </object>
                <p className="text-xs text-muted" data-testid="pdf-viewer-hint">
                  ¿El recuadro sigue vacío? Usa «Abrir PDF» o «Descargar PDF»: es el mismo archivo.
                </p>
              </>
            ) : (
              <div
                data-testid="pdf-fallback"
                className="space-y-1 rounded-lg border border-dashed border-bd-border p-4 text-sm"
              >
                <p className="font-semibold text-foreground">La vista previa no se muestra en esta página</p>
                <p className="text-muted">{FALLBACK_REASON[support]}</p>
              </div>
            )}
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
