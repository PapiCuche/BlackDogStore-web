import { test, type Page } from "@playwright/test";

/** Lo que comparten las pruebas de imágenes: entrar y llamar a la API como la aplicación. */

// 40 × 30, rojo opaco en el centro y transparente en las esquinas.
export const CUTOUT = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAACgAAAAeCAYAAABe3VzdAAAAPklEQVR42u3QsQkAIAxFweggzuH+lXNkEV3BQlDwrg78RyIA/lZ2D0dr8+Rwz9zarq9/UKBAgQIFChQIwE0LKCMEHNqb/bcAAAAASUVORK5CYII=", "base64");
// 1 × 1, opaco.
export const DOT = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC", "base64");

export const SLUG = process.env.E2E_COMPANY_SLUG ?? "black-dog-store";

export async function signIn(page: Page, username = "dev_admin") {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  await card.first().waitFor({ state: "visible", timeout: 15_000 }).catch(() => {});
  test.skip((await card.count()) === 0, "sin tarjeta de accesos de desarrollo: el backend no corre con DEBUG");
  await card.locator("li").filter({ has: page.getByText(username, { exact: true }) })
    .getByRole("button", { name: "Usar cuenta" }).first().click();
  // El limitador son 5 intentos por minuto y por IP. Se espera la ventana.
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((url) => !url.pathname.startsWith("/auth"), { timeout: 12_000 });
      return;
    } catch {
      if (attempt === 2) throw new Error(`no se pudo iniciar sesión como ${username}`);
      await page.waitForTimeout(62_000);
    }
  }
}

/** Una llamada desde la página, como la haría la aplicación: cookies y CSRF. */
export async function api(page: Page, method: string, url: string, body?: unknown) {
  return page.evaluate(
    async ({ method, url, body }) => {
      const read = () => document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1];
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (method !== "GET") {
        if (!read()) await fetch("/api/auth/csrf/", { credentials: "include" });
        const token = read();
        if (token) headers["X-CSRFToken"] = decodeURIComponent(token);
      }
      const response = await fetch(url, {
        method, credentials: "include", headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      let data: any = null; // eslint-disable-line @typescript-eslint/no-explicit-any
      try { data = await response.json(); } catch { /* sin cuerpo */ }
      return { status: response.status, data };
    },
    { method, url, body },
  );
}
