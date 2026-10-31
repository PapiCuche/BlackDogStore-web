import { mkdirSync } from "node:fs";
import {
  chromium, devices, expect, request, test, webkit,
  type Browser, type BrowserContextOptions, type Locator, type Page,
} from "@playwright/test";
import { RUN, shell } from "./inventory-first-support";
import { expectNoOverflow } from "./nav-ux-support";

/**
 * DOC-PREVIEW-03 · el PDF de Ajustes › Documentos SE VE dentro de la página donde el navegador puede, y donde no puede
 * la pantalla lo dice y ofrece «Abrir PDF» / «Descargar PDF». Navegadores reales + Django real sobre una base
 * desechable (`shell()` se niega a correr contra otra cosa).
 *
 *   DATABASE_URL=postgres://…/blackdog_e2e_prev DEBUG=1 E2E_BACKEND_DIR=<backend> \
 *     E2E_BASE_URL=http://localhost:3044 E2E_API_BASE=http://localhost:3044/api \
 *     npx playwright test e2e/document-preview-viewer.spec.ts
 *
 * EL FALLO QUE CUBRE. En Safari el visor era un <iframe> con un `blob:`; el blob hereda la política
 * `frame-ancestors 'none'` de la página y WebKit lo deja en blanco («Refused to load blob:… frame-ancestors») mientras
 * la pantalla decía «Vista previa lista». Chromium no aplica esa herencia, por eso el E2E anterior (sólo Chromium) no
 * lo veía.
 *
 * CÓMO SE SABE QUE EL PDF SE DIBUJÓ (no basta con que el elemento exista):
 *  - Chromium con su visor (`channel: "chromium"`): el <object> tiene un documento `application/pdf` y la captura del
 *    recuadro trae tinta (texto del PDF y la barra del visor), no un rectángulo de un solo color.
 *  - WebKit: el visor de PDF del sistema no tiene documento accesible y la captura de Playwright no recoge el texto
 *    que dibuja PDFKit (tampoco el de un `file://…pdf` abierto directamente). Lo que sí recoge es el fondo gris del
 *    visor y la hoja blanca, y la FORMA de la hoja sale del PDF: A4 (alto/ancho ≈ 1,41) en la nota, larga y estrecha
 *    en el ticket de 80 mm. Con el <iframe> bloqueado no había ni gris ni hoja.
 *  - Sin visor (Chromium sin visor de PDF, perfil de iPhone): no hay recuadro, hay tarjeta con el motivo y la región
 *    viva nunca dice «Vista previa lista».
 */

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3044";
const API = process.env.E2E_API_BASE ?? `${BASE}/api`;
const PASSWORD = "Qae-Doc3-2026!";
const prefix = `qaep_${RUN.toLowerCase()}`;
const MANAGER = `${prefix}_m`;
const SHOTS = process.env.DOC_SHOTS ?? "test-results/doc-preview-03";
const VIEWER = "[data-testid='pdf-viewer']";

test.describe.configure({ mode: "serial" });

let storageState: Awaited<ReturnType<Awaited<ReturnType<typeof request.newContext>>["storageState"]>>;

test.beforeAll(async () => {
  shell(`
from django.contrib.auth import get_user_model
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment
company = Company.objects.get(slug=data['slug'])
user = get_user_model().objects.create_user(username=data['user'], password=data['password'])
role = CompanyRole.objects.create(company=company, name='Rol ' + data['user'], slug=data['user'],
                                  capabilities=['company.view', 'company.manage'])
membership = Membership.objects.create(user=user, company=company, role='admin', is_active=True,
                                       branch_access_mode=Membership.ACCESS_MODE_ALL)
MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)
out({'ok': True})
`, { user: MANAGER, password: PASSWORD });
  // Un solo inicio de sesión para todos los navegadores (el servidor deja cinco por minuto): las cookies valen igual.
  const api = await request.newContext({ baseURL: BASE });
  const login = await api.post(`${API}/auth/login/`, { data: { username: MANAGER, password: PASSWORD } });
  expect(login.status(), "inicio de sesión").toBe(200);
  await api.get(`${API}/auth/csrf/`);
  storageState = await api.storageState();
  await api.dispose();
});

test.afterAll(() => {
  const result = shell<{ left: string[] }>(`
from django.contrib.auth import get_user_model
from store.models import CompanyRole, Membership, MembershipRoleAssignment
users = get_user_model().objects.filter(username__startswith=data['prefix'])
left = []
for label, qs in [
    ('assignments', MembershipRoleAssignment.objects.filter(membership__user__in=users)),
    ('memberships', Membership.objects.filter(user__in=users)),
    ('roles', CompanyRole.objects.filter(slug__startswith=data['prefix'])),
    ('users', users)]:
    try:
        qs.delete()
    except Exception as exc:
        left.append(label + ': ' + str(exc)[:120])
out({'left': left})
`, { prefix });
  expect(result.left, `DOC-PREVIEW-03: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

type Ink = { width: number; height: number; ink: number; gray: number; sheet: { w: number; h: number } | null };

/**
 * Lee la captura del recuadro en el propio navegador (canvas): fracción de píxeles con tinta (luminancia < 200),
 * fracción del gris del visor de WebKit y el rectángulo que ocupa la hoja blanca.
 */
async function inkOf(page: Page, viewer: Locator): Promise<Ink> {
  const png = (await viewer.screenshot()).toString("base64");
  return page.evaluate(async (b64) => {
    const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${b64}`)).blob());
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(bitmap, 0, 0);
    const { data, width, height } = ctx.getImageData(0, 0, bitmap.width, bitmap.height);
    let ink = 0, gray = 0, minX = width, minY = height, maxX = -1, maxY = -1;
    // El borde del recuadro (2 px) no cuenta.
    for (let y = 2; y < height - 2; y += 1) {
      for (let x = 2; x < width - 2; x += 1) {
        const i = (y * width + x) * 4;
        const [r, g, b] = [data[i], data[i + 1], data[i + 2]];
        const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        if (lum < 200) ink += 1;
        if (Math.abs(r - 128) < 12 && Math.abs(g - 128) < 12 && Math.abs(b - 128) < 12) gray += 1;
        if (r > 245 && g > 245 && b > 245) {
          if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y;
        }
      }
    }
    const area = (width - 4) * (height - 4);
    return {
      width, height, ink: ink / area, gray: gray / area,
      sheet: maxX < 0 ? null : { w: maxX - minX + 1, h: maxY - minY + 1 },
    };
  }, png);
}

async function open(browser: Browser, options: BrowserContextOptions) {
  const context = await browser.newContext({ ...options, baseURL: BASE, acceptDownloads: true, storageState });
  const page = await context.newPage();
  const errors: string[] = [];
  // Igual que document-preview.spec.ts: el aviso de hidratación del layout raíz de `next dev` es ajeno a esta
  // pantalla; cualquier otro error (en especial «Refused to load blob:… frame-ancestors») hace fallar la prueba.
  const rootHydration = /Hydration failed|hydrat/i;
  page.on("console", (msg) => { if (msg.type() === "error" && !rootHydration.test(msg.text())) errors.push(msg.text()); });
  page.on("pageerror", (err) => { if (!rootHydration.test(String(err))) errors.push(String(err)); });
  await page.goto("/admin/settings/documents", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { level: 1, name: "Documentos" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("link", { name: "Abrir PDF" })).toBeVisible({ timeout: 60_000 });
  return { context, page, errors };
}

async function choose(page: Page, label: RegExp) {
  const href = await page.getByRole("link", { name: "Abrir PDF" }).getAttribute("href");
  await page.getByRole("radio", { name: label }).check();
  // El blob anterior se quitó y llegó uno nuevo.
  await expect(page.getByRole("link", { name: "Abrir PDF" })).not.toHaveAttribute("href", href ?? "", { timeout: 60_000 });
  await expect(page.getByRole("link", { name: "Abrir PDF" })).toBeVisible();
}

/** «Abrir PDF» abre una pestaña con el mismo PDF; «Descargar PDF» trae el nombre del servidor. */
async function expectExits(page: Page) {
  const [popup] = await Promise.all([page.waitForEvent("popup"), page.getByRole("link", { name: "Abrir PDF" }).click()]);
  await popup.waitForLoadState();
  expect(popup.url()).toMatch(/^blob:http:\/\/localhost:\d+\//);
  await expect.poll(() => popup.evaluate(() => document.contentType), { timeout: 15_000 }).toBe("application/pdf");
  await popup.close();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "Descargar PDF" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^MUESTRA-.*\.pdf$/);
}

async function expectFallback(page: Page, reason: RegExp) {
  await expect(page.getByTestId("pdf-fallback")).toBeVisible();
  await expect(page.getByTestId("pdf-fallback")).toContainText(reason);
  await expect(page.locator("section[aria-label='Vista previa'] :is(object, iframe, embed)")).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText("El PDF de muestra está listo para abrir o descargar.");
  await expect(page.getByText("Vista previa lista.")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Descargar PDF" })).toBeVisible();
}

function shot(page: Page, name: string) {
  mkdirSync(SHOTS, { recursive: true });
  return page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

test.describe("WebKit (motor de Safari)", () => {
  let browser: Browser;
  test.beforeAll(async () => { browser = await webkit.launch(); });
  test.afterAll(async () => { await browser.close(); });

  test("escritorio 1280: el visor de PDF dibuja la hoja de cada documento y no hay bloqueo de frame-ancestors", async () => {
    test.setTimeout(180_000);
    // Alto: la hoja A4 entera cabe en el recuadro (70vh) y su proporción se puede medir.
    const { context, page, errors } = await open(browser, { viewport: { width: 1280, height: 1600 } });
    try {
      expect(await page.evaluate(() => navigator.pdfViewerEnabled)).toBe(true);
      await expect(page.getByRole("status")).toHaveText("Vista previa lista.");
      await expect(page.locator("section[aria-label='Vista previa'] iframe")).toHaveCount(0);
      const viewer = page.locator(VIEWER);
      await expect(viewer).toBeVisible();
      expect(await viewer.evaluate((el) => (el as HTMLObjectElement).type)).toBe("application/pdf");

      // A4: el visor está (gris) y la hoja tiene la proporción de un A4.
      await expect.poll(async () => (await inkOf(page, viewer)).gray, { timeout: 15_000 }).toBeGreaterThan(0.05);
      const a4 = await inkOf(page, viewer);
      expect(a4.sheet, "hoja A4 dibujada").not.toBeNull();
      expect(a4.sheet!.h / a4.sheet!.w).toBeGreaterThan(1.3);
      expect(a4.sheet!.h / a4.sheet!.w).toBeLessThan(1.5);
      await viewer.screenshot({ path: `${SHOTS}/webkit-1280-viewer-a4.png` });

      // Ticket de 80 mm: otra forma de hoja, leída del PDF nuevo (no la del anterior).
      await choose(page, /Ticket de nota de venta/);
      await expect.poll(async () => {
        const t = await inkOf(page, viewer);
        return t.sheet ? t.sheet.w / t.sheet.h : 99;
      }, { timeout: 20_000 }).toBeLessThan(0.6);
      await viewer.screenshot({ path: `${SHOTS}/webkit-1280-viewer-ticket.png` });
      await expect(page.getByRole("status")).toHaveText("Vista previa lista.");

      await expectNoOverflow(page, "webkit 1280");
      await shot(page, "webkit-1280");
      await expectExits(page);
      expect(errors, "errores de consola (p. ej. «Refused to load blob:… frame-ancestors»)").toEqual([]);
    } finally {
      await context.close();
    }
  });

  test("iPhone 13 (390 px): sin recuadro, con el motivo, y abrir/descargar funcionan", async () => {
    test.setTimeout(120_000);
    const { context, page, errors } = await open(browser, { ...devices["iPhone 13"] });
    try {
      await expectFallback(page, /iPhone o iPad/);
      await choose(page, /Factura electrónica/);
      await expectFallback(page, /iPhone o iPad/);
      await expectNoOverflow(page, "webkit iPhone 13");
      await shot(page, "webkit-iphone13");
      await expectExits(page);
      expect(errors, "errores de consola").toEqual([]);
    } finally {
      await context.close();
    }
  });
});

test.describe("Chromium", () => {
  test("con visor de PDF, 1280 y 390: el PDF se carga en el <object> y se ve", async () => {
    test.setTimeout(180_000);
    // `channel: "chromium"` es el Chromium completo, con su visor de PDF (el de por defecto de Playwright no lo trae).
    const browser = await chromium.launch({ channel: "chromium" });
    try {
      for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
        const { context, page, errors } = await open(browser, { viewport });
        try {
          expect(await page.evaluate(() => navigator.pdfViewerEnabled)).toBe(true);
          await expect(page.getByRole("status")).toHaveText("Vista previa lista.");
          const viewer = page.locator(VIEWER);
          await expect(viewer).toBeVisible();
          await expect.poll(() => viewer.evaluate((el) => (el as HTMLObjectElement).contentDocument?.contentType ?? ""),
            { timeout: 15_000 }).toBe("application/pdf");
          // Tinta (texto y barra del visor) Y una hoja blanca de buen tamaño: un visor vacío es sólo gris oscuro.
          await expect.poll(async () => {
            const t = await inkOf(page, viewer);
            return t.ink > 0.02 && (t.sheet?.w ?? 0) > 0.4 * t.width && (t.sheet?.h ?? 0) > 0.4 * t.height;
          }, { timeout: 15_000 }).toBe(true);
          await viewer.screenshot({ path: `${SHOTS}/chromium-${viewport.width}-viewer.png` });
          await choose(page, /Boleta de venta electrónica/);
          await expect.poll(() => viewer.evaluate((el) => (el as HTMLObjectElement).contentDocument?.contentType ?? ""),
            { timeout: 15_000 }).toBe("application/pdf");
          await expectNoOverflow(page, `chromium ${viewport.width}`);
          await shot(page, `chromium-${viewport.width}`);
          await expectExits(page);
          expect(errors, "errores de consola").toEqual([]);
        } finally {
          await context.close();
        }
      }
    } finally {
      await browser.close();
    }
  });

  test("sin visor de PDF (1280 y 390): tarjeta con el motivo, nunca «Vista previa lista»", async () => {
    test.setTimeout(120_000);
    const browser = await chromium.launch();
    try {
      for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
        const { context, page, errors } = await open(browser, { viewport });
        try {
          expect(await page.evaluate(() => navigator.pdfViewerEnabled)).toBe(false);
          await expectFallback(page, /no muestra PDF dentro de la página/);
          await expectNoOverflow(page, `chromium sin visor ${viewport.width}`);
          await shot(page, `chromium-sin-visor-${viewport.width}`);
          const [download] = await Promise.all([
            page.waitForEvent("download"),
            page.getByRole("link", { name: "Descargar PDF" }).click(),
          ]);
          expect(download.suggestedFilename()).toMatch(/^MUESTRA-.*\.pdf$/);
          expect(errors, "errores de consola").toEqual([]);
        } finally {
          await context.close();
        }
      }
    } finally {
      await browser.close();
    }
  });
});
