/**
 * DOC-PREVIEW-03 · ¿puede este navegador mostrar un PDF DENTRO de la página?
 *
 * Decide si la vista previa de Ajustes › Documentos dibuja el visor en línea o
 * sólo ofrece «Abrir PDF» / «Descargar PDF» con una explicación. No hay una
 * tercera opción a propósito: un recuadro vacío sin explicación es el fallo que
 * esta comprobación existe para evitar.
 *
 *   inline       `navigator.pdfViewerEnabled === true` (Chrome/Edge 94+,
 *                Safari 16.4+, Firefox 99+ con su visor activo) y no es un iPhone
 *                ni un iPad.
 *   apple-touch  iPhone, iPad o iPod (también el iPad que se presenta como Mac:
 *                «Macintosh» + pantalla táctil). Safari de iOS dice tener visor,
 *                pero dentro de una página suele dibujar el PDF como una imagen
 *                fija de la primera hoja, sin desplazarse ni ampliar; en su propia
 *                pestaña sí se lee entero. NO comprobado en un iPhone real: el
 *                perfil «iPhone 13» de Playwright es WebKit de escritorio con
 *                agente de iPhone, y sólo prueba esta regla.
 *   no-viewer    el navegador no tiene visor de PDF (Chrome de Android, Chromium
 *                sin visor, navegadores antiguos que no declaran la propiedad).
 */

export type PdfInlineSupport = "inline" | "apple-touch" | "no-viewer";

type NavigatorLike = Pick<Navigator, "userAgent" | "maxTouchPoints"> & { pdfViewerEnabled?: boolean };

export function isAppleTouchDevice(nav: NavigatorLike): boolean {
  const ua = nav.userAgent ?? "";
  if (/\b(iPhone|iPad|iPod)\b/.test(ua)) return true;
  // iPadOS 13+ pide por defecto la versión de escritorio y se presenta como un Mac.
  return /\bMacintosh\b/.test(ua) && (nav.maxTouchPoints ?? 0) > 1;
}

export function pdfInlineSupport(nav: NavigatorLike | undefined): PdfInlineSupport {
  if (!nav || nav.pdfViewerEnabled !== true) return "no-viewer";
  if (isAppleTouchDevice(nav)) return "apple-touch";
  return "inline";
}
