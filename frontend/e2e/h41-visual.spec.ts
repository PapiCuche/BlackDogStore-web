import { test, expect, type Page } from "@playwright/test";

/**
 * H4.1 — revisión visual. Captura las pantallas a 390, 768 y 1440, en claro y
 * en oscuro, para MIRARLAS. Playwright en verde dice que la lógica funciona; no
 * dice que el texto quepa, que el contraste sirva ni que nada se salga.
 *
 * También mide desbordes horizontales, que es el defecto que no se ve en una
 * captura de escritorio y arruina la pantalla en un teléfono.
 */

const SHOTS = process.env.H41_SHOTS ?? "/tmp/h41shots";
const TAMAÑOS = [
  { nombre: "390", width: 390, height: 844 },
  { nombre: "768", width: 768, height: 1024 },
  { nombre: "1440", width: 1440, height: 900 },
];

let COOKIES: Awaited<ReturnType<import("@playwright/test").BrowserContext["cookies"]>> = [];

test.beforeAll(async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) test.skip(true, "sin accesos de desarrollo");
  await card.locator("li").filter({ hasText: "dev_admin" })
    .getByRole("button", { name: "Usar cuenta" }).click();
  await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
  await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 20_000 });
  COOKIES = await context.cookies();
  await context.close();
});

/** Mide el desborde horizontal real de la página. */
async function desborde(page: Page) {
  return page.evaluate(() => {
    const raiz = document.documentElement;
    const exceso = raiz.scrollWidth - raiz.clientWidth;
    const culpables: string[] = [];
    if (exceso > 1) {
      for (const el of Array.from(document.querySelectorAll<HTMLElement>("*"))) {
        const r = el.getBoundingClientRect();
        if (r.right > raiz.clientWidth + 1 && r.width > 0) {
          culpables.push(
            `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)}`,
          );
          if (culpables.length > 4) break;
        }
      }
    }
    return { exceso, culpables };
  });
}

const PANTALLAS: { id: string; ruta: string; listo: (p: Page) => Promise<void> }[] = [
  {
    id: "personal",
    ruta: "/admin/staff",
    listo: async (p) => {
      await expect(p.getByRole("heading", { name: "Personal", exact: true }))
        .toBeVisible({ timeout: 20_000 });
      await expect(p.getByText(/Cargando personal/i)).toHaveCount(0, { timeout: 20_000 });
    },
  },
  {
    id: "personal-alta",
    ruta: "/admin/staff",
    listo: async (p) => {
      await expect(p.getByRole("heading", { name: "Personal", exact: true }))
        .toBeVisible({ timeout: 20_000 });
      await p.getByRole("button", { name: "Añadir trabajador" }).click();
      await expect(p.locator("#w-email")).toBeVisible();
    },
  },
  {
    id: "areas",
    ruta: "/admin/areas",
    listo: async (p) => {
      await expect(p.getByRole("heading", { name: "Áreas", exact: true }))
        .toBeVisible({ timeout: 20_000 });
      await expect(p.getByText(/Cargando áreas/i)).toHaveCount(0, { timeout: 20_000 });
    },
  },
  {
    id: "invitacion-invalida",
    ruta: "/invitacion?token=noexiste",
    listo: async (p) => {
      await expect(p.getByRole("heading", { name: "Invitación no válida" }))
        .toBeVisible({ timeout: 20_000 });
    },
  },
  {
    id: "invitacion-valida",
    ruta: "/invitacion",
    listo: async (p) => {
      await expect(p.locator("main")).toBeVisible({ timeout: 20_000 });
    },
  },
];

test("captura y mide las pantallas de H4.1", async ({ browser, baseURL }) => {
  test.setTimeout(600_000);
  const problemas: string[] = [];

  // Una invitación de verdad para la pantalla válida.
  let token = "";
  {
    const ctx = await browser.newContext({ baseURL });
    await ctx.addCookies(COOKIES);
    const page = await ctx.newPage();
    await page.goto("/admin/staff", { waitUntil: "networkidle" });
    token = await page.evaluate(async () => {
      const leer = async (r: string) => {
        const res = await fetch(r, { credentials: "include" });
        return res.ok ? (await res.json()).results ?? [] : [];
      };
      const areas = await leer("/api/admin/areas/");
      if (!areas.length) return "";
      const company = areas[0].company;
      const roles = (await leer("/api/admin/roles/"))
        .filter((r: { company: number }) => r.company === company);
      if (!roles.length) return "";
      const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1];
      const res = await fetch("/api/admin/staff/invitations/", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRFToken": decodeURIComponent(csrf) } : {}),
        },
        body: JSON.stringify({
          company, email: `visual.${Date.now().toString(36)}@correo.test`,
          first_name: "Visual", last_name: "De Prueba",
          role: roles[0].id, area: areas[0].id, branch_access_mode: "all",
        }),
      });
      const cuerpo = await res.json().catch(() => ({}));
      return cuerpo.debug_invitation_token ?? "";
    });
    await ctx.close();
  }

  for (const tema of ["light", "dark"] as const) {
    for (const tamaño of TAMAÑOS) {
      const ctx = await browser.newContext({
        baseURL,
        viewport: { width: tamaño.width, height: tamaño.height },
        colorScheme: tema,
      });
      await ctx.addCookies(COOKIES);
      const page = await ctx.newPage();

      for (const pantalla of PANTALLAS) {
        const ruta = pantalla.id === "invitacion-valida" && token
          ? `/invitacion?token=${encodeURIComponent(token)}`
          : pantalla.ruta;
        if (pantalla.id === "invitacion-valida" && !token) continue;

        await page.goto(ruta, { waitUntil: "networkidle" });
        await page.evaluate((t) => {
          document.documentElement.setAttribute("data-theme", t);
          // El indicador de desarrollo de Next flota sobre la página y aparece
          // en la captura como si fuera un elemento nuestro recortado. No lo es.
          document.querySelectorAll("nextjs-portal").forEach((n) => n.remove());
        }, tema);
        await pantalla.listo(page);
        await page.waitForTimeout(400);

        const { exceso, culpables } = await desborde(page);
        if (exceso > 1) {
          problemas.push(
            `${pantalla.id} · ${tema} · ${tamaño.nombre}px → desborde ${exceso}px (${culpables.join(", ")})`,
          );
        }
        await page.screenshot({
          path: `${SHOTS}/${pantalla.id}-${tema}-${tamaño.nombre}.png`,
          fullPage: true,
        });
      }
      await ctx.close();
    }
  }

  console.log(problemas.length ? problemas.join("\n") : "sin desbordes");
  expect(problemas, problemas.join("\n")).toEqual([]);
});
