/**
 * INV-QA-E · guarda de seguridad del ayudante de siembra y limpieza.
 *
 * El ayudante escribe y BORRA filas por `manage.py shell`. Nunca debe correr contra una base viva, así que antes de
 * tocar nada se pregunta a Django a qué está conectado y esta función decide. Es pura (sin Playwright ni red) para que
 * `__tests__/inventory-first-guard.test.ts` la pruebe con configuraciones inventadas.
 *
 * REGLA (conservadora): DEBUG encendido Y servidor local (localhost, 127.0.0.1, ::1, o socket sin host) Y un nombre de
 * base que se declare desechable: contiene «qae», «e2e» o «test», o es EXACTAMENTE el que el operador acepta con
 * `E2E_DB_OK=<nombre>` (para la copia de desarrollo que AGENTS.md describe). `blackdog` a secas NO pasa.
 */
export type Target = { debug: boolean; host: string; name: string; engine?: string };

const LOCAL_HOSTS = new Set(["", "localhost", "127.0.0.1", "::1", "[::1]"]);

/** `null` si es seguro; si no, la razón en español. */
export function unsafeTarget(target: Target, accepted = ""): string | null {
  if (target.debug !== true) return "Django corre con DEBUG apagado: la siembra y la limpieza sólo corren en desarrollo.";
  const sqlite = (target.engine ?? "").includes("sqlite");
  const host = String(target.host ?? "").trim().toLowerCase();
  // SQLite es un archivo de esta máquina: no tiene servidor.
  if (!sqlite && !LOCAL_HOSTS.has(host)) return `La base está en «${target.host}», que no es local.`;
  const name = String(target.name ?? "");
  if (!name) return "No se pudo saber el nombre de la base.";
  const disposable = /(qae|e2e|test)/i.test(name);
  if (!disposable && !(accepted && accepted === name)) {
    return `La base «${name}» no se declara desechable (debe contener qae, e2e o test, o ser la que acepta E2E_DB_OK).`;
  }
  return null;
}

export function assertSafeTarget(target: Target, accepted = ""): void {
  const reason = unsafeTarget(target, accepted);
  if (reason) throw new Error(`INV-QA-E se niega a correr: ${reason}`);
}
