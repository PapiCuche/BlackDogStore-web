"use client";

/**
 * Ajustes › Documentos — vista previa de los papeles impresos de la empresa.
 *
 * TODO LO QUE SE VE ES UNA MUESTRA. El servidor dibuja el PDF con datos
 * inventados y con «MUESTRA · NO FISCAL» en cada página; la insignia de esta
 * pantalla está SIEMPRE, aunque el PDF falle o todavía no haya llegado.
 *
 * EL BORRADOR (DOC-DESIGN-03). `draft` son los cambios del diseño que la persona
 * está editando y todavía no guardó: se envían con la muestra (el servidor los
 * valida, los dibuja y no los guarda), con una espera de `DRAFT_DEBOUNCE_MS`
 * para no pedir un PDF por tecla. Mientras llega el nuevo, el anterior sigue a
 * la vista. Con `blocked` (un error del borrador que el servidor rechazaría) no
 * se pide nada y se dice por qué. Este componente no guarda nada.
 *
 * EL CUPO (revisión de DOC-DESIGN-03). El servidor da 30 muestras por minuto y
 * una petición cancelada ya se dibujó y cuenta. Si una muestra falla con un PDF
 * a la vista (un 429, o un borrador que el servidor rechaza), ese PDF NO se
 * quita: se avisa de que es la última muestra válida, y tras un 429 se vuelve a
 * pedir cuando el servidor dice (`Retry-After`) o con el próximo cambio.
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

import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import {
  DocumentPreviewError,
  PREVIEW_OPTIONS,
  fetchDocumentPreview,
  type PreviewKind,
} from "../../lib/document-preview-api";
import type { DocumentDesign } from "../../lib/document-design-api";
import { pdfInlineSupport, type PdfInlineSupport } from "../../lib/pdf-inline-support";
import { internalButtonClass } from "../../components/internal-ui";

const CARD = "rounded-xl border border-bd-border bg-surface p-5 sm:p-6";

type Loaded = { url: string; fileName: string };

/** Cuánto vive un PDF ya retirado de la pantalla antes de revocar su objeto-URL. */
export const REVOKE_DELAY_MS = 10_000;

/**
 * La espera tras el último cambio del borrador antes de pedir la muestra. 900 ms y no 450: el cupo del servidor
 * (30 por minuto) se gasta también con las peticiones canceladas.
 */
export const DRAFT_DEBOUNCE_MS = 900;

/** Si un 429 no dice cuánto esperar, se reintenta tras esto. */
export const RATE_LIMIT_FALLBACK_MS = 10_000;

const STALE = "Se muestra la última vista previa válida.";

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

type Props = {
  companyId: number | null;
  /** Los campos del diseño que cambian respecto de lo guardado; vacío o ausente = lo guardado. */
  draft?: Partial<DocumentDesign> | null;
  /** Un error del borrador: mientras exista, no se pide la muestra. */
  blocked?: string | null;
  /** Lo que va arriba en la columna de la izquierda (el formulario del diseño). */
  aside?: ReactNode;
};

export function DocumentPreview({ companyId, draft = null, blocked = null, aside = null }: Props) {
  const [kind, setKind] = useState<PreviewKind>(PREVIEW_OPTIONS[0].kind);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [attempt, setAttempt] = useState(0);
  /** El borrador con el que se dibujó el PDF a la vista ('' = lo guardado). */
  const [shownDraftKey, setShownDraftKey] = useState("");
  /** Una muestra falló pero el PDF anterior sigue a la vista: por qué. */
  const [notice, setNotice] = useState<string | null>(null);
  const retryTimer = useRef<number | null>(null);
  const urlRef = useRef<string | null>(null);
  const draftKey = draft && Object.keys(draft).length ? JSON.stringify(draft) : "";
  const lastDraftKey = useRef(draftKey);
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
    if (blocked) return undefined;
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    const controller = new AbortController();
    // Un cambio del borrador espera a que se deje de teclear; elegir otro documento, no.
    const debounce = draftKey !== lastDraftKey.current;
    lastDraftKey.current = draftKey;
    const body = draftKey ? (JSON.parse(draftKey) as Partial<DocumentDesign>) : undefined;
    const run = () => {
      void (async () => {
        if (debounce || urlRef.current) {
          setLoading(true);
          setError(null);
          setNotice(null);
        }
        try {
          const { blob, fileName } = await fetchDocumentPreview(kind, companyId, controller.signal, body);
          if (controller.signal.aborted) return;
          // El PDF que estaba a la vista se suelta recién ahora, cuando ya hay otro que mostrar.
          release();
          const url = URL.createObjectURL(blob);
          urlRef.current = url;
          setLoaded({ url, fileName });
          setShownDraftKey(draftKey);
          setError(null);
          setNotice(null);
          setLoading(false);
        } catch (err) {
          if (controller.signal.aborted) return;
          const limited = err instanceof DocumentPreviewError && err.status === 429;
          if (urlRef.current) {
            // Hay una muestra válida a la vista: se queda, con el aviso. Nunca desaparece por un 429.
            setNotice(limited
              ? "Demasiadas vistas previas seguidas; espera un momento. Se vuelve a intentar sola."
              : `${err instanceof Error ? err.message : "No se pudo generar la vista previa."}`);
            setLoading(false);
            if (limited) {
              const wait = (err as DocumentPreviewError).retryAfter;
              retryTimer.current = window.setTimeout(() => {
                retryTimer.current = null;
                setAttempt((n) => n + 1);
              }, wait ? wait * 1000 : RATE_LIMIT_FALLBACK_MS);
            }
            return;
          }
          setError(
            err instanceof DocumentPreviewError || err instanceof Error
              ? err.message
              : "No se pudo generar la vista previa.",
          );
          setLoading(false);
        }
      })();
    };
    const timer = debounce ? window.setTimeout(run, DRAFT_DEBOUNCE_MS) : null;
    if (!debounce) run();
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      controller.abort();
    };
  }, [kind, companyId, attempt, draftKey, blocked, release]);

  useEffect(() => release, [release]);
  useEffect(() => () => {
    if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
  }, []);

  /** Cada pedido nuevo (otro documento, o reintentar) empieza limpio: sin PDF ni error del anterior. */
  const request = useCallback((next: PreviewKind) => {
    release();
    setLoaded(null);
    setError(null);
    setNotice(null);
    setLoading(true);
    setKind(next);
    setAttempt((n) => n + 1);
  }, [release]);

  const option = PREVIEW_OPTIONS.find((o) => o.kind === kind)!;
  const drafting = Boolean(draftKey);
  // Entre la última tecla y la respuesta, el PDF a la vista ya no es el del borrador.
  const busy = !blocked && (loading || Boolean(loaded && shownDraftKey !== draftKey && !notice));

  return (
    <div className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
      <div className="min-w-0 space-y-6">
      {aside}
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
      </div>

      <section className={`${CARD} min-w-0 space-y-4 lg:sticky lg:top-4 lg:self-start`} aria-label="Vista previa">
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
            y no se envía nada a SUNAT. El diseño de los documentos internos no se aplica a boletas ni
            facturas.
          </p>
        ) : drafting ? (
          <p className="text-sm text-muted" data-testid="draft-note">
            Muestra con tus cambios sin guardar. Nada se guarda hasta que confirmes.
          </p>
        ) : null}

        {blocked ? (
          <p role="alert" className="rounded-lg border border-danger/40 p-3 text-sm text-danger" data-testid="draft-blocked">
            {blocked}
          </p>
        ) : null}
        {notice && !blocked && loaded ? (
          <p role="alert" className="rounded-lg border border-warning-border bg-warning-surface p-3 text-sm text-foreground" data-testid="preview-notice">
            {notice}
          </p>
        ) : null}

        <div role="status" aria-live="polite" className="text-sm text-muted">
          {loaded && (blocked || (notice && !busy))
            ? STALE
            : busy
            ? loaded ? "Actualizando la vista previa…" : "Generando la vista previa…"
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
            si las boletas y facturas también usarán este diseño, y si el diseño se congela al emitir cada
            documento. Mientras tanto se imprimen como siempre.
          </p>
        </div>
      </section>
    </div>
  );
}
