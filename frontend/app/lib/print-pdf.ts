/**
 * Poner un PDF delante de quien atiende: imprimirlo, o si no, descargarlo.
 *
 * EL DIÁLOGO DE IMPRESIÓN NO SIEMPRE LLEGA, Y ESO NO PUEDE BLOQUEAR EL
 * MOSTRADOR. Con un PDF servido como blob el `onload` del marco oculto a veces
 * no dispara, así que la espera está ACOTADA: si el marco carga a tiempo se
 * abre el diálogo; si no, el documento se descarga y se puede imprimir desde el
 * visor. Lo que no ocurre nunca es quedarse esperando un evento que no llega.
 *
 * Devuelve qué pasó, para que la pantalla lo diga en vez de fingir que imprimió.
 */

/** Qué acabó pasando con el documento. */
export type PrintOutcome = "printed" | "downloaded";

export async function printPdfResponse(res: Response, fallbackName: string): Promise<PrintOutcome> {
  // EL NOMBRE LO PONE EL SERVIDOR: lo construye con los datos de la empresa
  // dueña del documento, y este código es de todas.
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const served = /filename="([^"]+)"/.exec(disposition)?.[1];
  const url = URL.createObjectURL(await res.blob());

  const frame = document.createElement("iframe");
  frame.style.position = "fixed";
  frame.style.width = "0";
  frame.style.height = "0";
  frame.style.border = "0";
  frame.style.visibility = "hidden";

  // Carrera acotada: gana el `onload`, un fallo, o el reloj. Siempre resuelve.
  const loaded = await new Promise<boolean>((resolve) => {
    let settled = false;
    const finish = (ok: boolean) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };
    frame.onload = () => finish(true);
    frame.onerror = () => finish(false);
    window.setTimeout(() => finish(false), 3000);
    frame.src = url;
    document.body.appendChild(frame);
  });

  if (loaded) {
    try {
      frame.contentWindow?.focus();
      frame.contentWindow?.print();
      // El objeto se libera cuando el diálogo ya no lo necesita. Revocarlo
      // mientras sigue abierto deja al navegador imprimiendo una hoja en blanco.
      window.setTimeout(() => {
        frame.remove();
        URL.revokeObjectURL(url);
      }, 60_000);
      return "printed";
    } catch {
      // El navegador no deja imprimir el marco: cae a la descarga de abajo.
    }
  }

  frame.remove();
  const link = document.createElement("a");
  link.href = url;
  link.download = served ?? fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
  return "downloaded";
}
