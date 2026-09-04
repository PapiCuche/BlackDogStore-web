import { defineConfig } from "@playwright/test";

/**
 * M12F.1 — aceptación funcional y responsive con navegador de verdad.
 *
 * NO ES REGRESIÓN VISUAL POR PÍXELES. Eso vendría después de estabilizar el
 * diseño, y hoy sólo produciría capturas gigantes que fallan cada vez que
 * alguien mueve un margen. Lo que se comprueba aquí son hechos binarios: si la
 * página desborda a lo ancho, si la consola escupe errores, si el logotipo que
 * se pinta es el del contraste correcto.
 *
 * El navegador NO se versiona: `@playwright/test` es dependencia de desarrollo
 * y el binario vive en la caché del sistema.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    // El servidor de desarrollo compila la primera vez que se pide una ruta.
    navigationTimeout: 60_000,
  },
});
