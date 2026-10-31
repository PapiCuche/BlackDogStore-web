import { mkdirSync } from "node:fs";
import { inflateSync } from "node:zlib";
import {
  chromium, expect, request, test, webkit,
  type Browser, type BrowserContextOptions, type Page,
} from "@playwright/test";
import { RUN, shell } from "./inventory-first-support";
import { expectNoOverflow } from "./nav-ux-support";

/**
 * DOC-DESIGN-03 · Ajustes › Documentos: el diseño de los documentos internos, de punta a punta y SIN MOCKS
 * (navegador real + Django real sobre una base desechable; `shell()` se niega a correr contra otra cosa).
 *
 *   DATABASE_URL=postgres://…/blackdog_e2e_design DEBUG=1 E2E_BACKEND_DIR=<backend> E2E_DB_OK=blackdog_e2e_design \
 *     E2E_BASE_URL=http://localhost:3045 E2E_API_BASE=http://localhost:3045/api \
 *     npx playwright test e2e/document-design.spec.ts
 *
 * Lo que se comprueba:
 *  - Chromium 1280 y 390: el formulario carga con el mensaje final VACÍO; teclear pide UNA muestra con el borrador
 *    (y el PDF que devuelve el servidor para ese borrador dice el texto); nada se guarda al teclear (ni fila ni
 *    auditoría); un texto con «<» bloquea muestra y guardado con su motivo; «Guardar cambios» lista lo que cambia y
 *    sólo «Confirmar y guardar» escribe (fila + `document_design_updated`); al recargar está lo guardado; los fiscales
 *    dicen que el diseño no se les aplica; sin desborde horizontal ni errores de consola.
 *  - WebKit 1280: la muestra con el borrador se dibuja en el visor (DOC-PREVIEW-03) y dice «Vista previa lista.».
 *
 * Textos inventados («Mensaje final de prueba …»): nunca la frase de una tienda real. Al terminar se borra la fila de
 * diseño de la empresa de la prueba, así la siguiente corrida empieza en los valores por defecto.
 */

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3045";
const API = process.env.E2E_API_BASE ?? `${BASE}/api`;
const PASSWORD = "Qae-Design-2026!";
const prefix = `qaedd_${RUN.toLowerCase()}`;
const MANAGER = `${prefix}_m`;
const SHOTS = process.env.DOC_SHOTS ?? "test-results/doc-design-03";

test.describe.configure({ mode: "serial" });

let storageState: Awaited<ReturnType<Awaited<ReturnType<typeof request.newContext>>["storageState"]>>;

function designRow() {
  return shell<{ row: Record<string, unknown> | null; audits: number }>(`
from store.models import AdminAuditLog, Company, CompanyDocumentDesign
company = Company.objects.get(slug=data['slug'])
row = CompanyDocumentDesign.objects.filter(company=company).values('logo_size', 'thank_you_message', 'footer_text', 'show_phone').first()
out({'row': row, 'audits': AdminAuditLog.objects.filter(company=company, action='document_design_updated').count()})
`);
}

function resetDesign() {
  shell(`
from store.models import Company, CompanyDocumentDesign
CompanyDocumentDesign.objects.filter(company__slug=data['slug']).delete()
out({'ok': True})
`);
}

test.beforeAll(async () => {
  resetDesign();
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
  const api = await request.newContext({ baseURL: BASE });
  const login = await api.post(`${API}/auth/login/`, { data: { username: MANAGER, password: PASSWORD } });
  expect(login.status(), "inicio de sesión").toBe(200);
  await api.get(`${API}/auth/csrf/`);
  storageState = await api.storageState();
  await api.dispose();
});

test.afterAll(() => {
  resetDesign();
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
  expect(result.left, `DOC-DESIGN-03: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

/** ASCII85 (`… ~>`), como lo escribe reportlab alrededor de cada stream comprimido. */
function a85(text: string): Buffer {
  const body = text.replace(/\s/g, "").replace(/~>$/, "").replace(/^<~/, "");
  const out: number[] = [];
  let group: number[] = [];
  for (const char of body) {
    if (char === "z" && group.length === 0) { out.push(0, 0, 0, 0); continue; }
    group.push(char.charCodeAt(0) - 33);
    if (group.length === 5) {
      const value = group.reduce((acc, d) => acc * 85 + d, 0);
      out.push((value >>> 24) & 255, (value >>> 16) & 255, (value >>> 8) & 255, value & 255);
      group = [];
    }
  }
  if (group.length) {
    const n = group.length;
    while (group.length < 5) group.push(84);
    const value = group.reduce((acc, d) => acc * 85 + d, 0);
    out.push(...[(value >>> 24) & 255, (value >>> 16) & 255, (value >>> 8) & 255, value & 255].slice(0, n - 1));
  }
  return Buffer.from(out);
}

/** El texto que dibuja un PDF de reportlab: sus streams (ASCII85 + Flate), descomprimidos, y las cadenas `( … )`. */
function pdfText(pdf: Buffer): string {
  const out: string[] = [];
  const raw = pdf.toString("latin1");
  const re = /stream\r?\n([\s\S]*?)endstream/g;
  for (let m = re.exec(raw); m; m = re.exec(raw)) {
    const chunk = m[1].replace(/\r?\n$/, "");
    let body = chunk.trimEnd().endsWith("~>") ? a85(chunk) : Buffer.from(chunk, "latin1");
    try { body = inflateSync(body); } catch { /* no comprimido */ }
    const text = body.toString("latin1");
    for (const piece of text.match(/\((?:\\.|[^\\()])*\)/g) ?? []) out.push(piece.slice(1, -1));
  }
  return out.join(" ");
}

async function open(browser: Browser, options: BrowserContextOptions) {
  const context = await browser.newContext({ ...options, baseURL: BASE, storageState });
  const page = await context.newPage();
  const errors: string[] = [];
  // Igual que document-preview.spec.ts: el aviso de hidratación del layout raíz de `next dev` es ajeno a esta pantalla.
  const rootHydration = /Hydration failed|hydrat|Encountered a script tag while rendering React component/i;
  page.on("console", (msg) => { if (msg.type() === "error" && !rootHydration.test(msg.text())) errors.push(msg.text()); });
  page.on("pageerror", (err) => { if (!rootHydration.test(String(err))) errors.push(String(err)); });
  const previews: string[] = [];
  page.on("request", (req) => {
    // The Next proxy answers 308 and the browser repeats the POST: that repetition is the same request, not another.
    if (/\/admin\/document-preview\/?(\?|$)/.test(req.url()) && req.method() === "POST" && !req.redirectedFrom()) {
      previews.push(req.postData() ?? "");
    }
  });
  await page.goto("/admin/settings/documents", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { level: 1, name: "Documentos" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "Diseño de los documentos internos" })).toBeVisible({ timeout: 60_000 });
  return { context, page, errors, previews };
}

function shot(page: Page, name: string) {
  mkdirSync(SHOTS, { recursive: true });
  return page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

const ENVS = [
  { name: "1280x800", viewport: { width: 1280, height: 800 }, touch: false },
  { name: "390x844", viewport: { width: 390, height: 844 }, touch: true },
];

test.describe("Chromium", () => {
  let browser: Browser;
  test.beforeAll(async () => { browser = await chromium.launch(); });
  test.afterAll(async () => { await browser.close(); });

  for (const env of ENVS) {
    test(`borrador, bloqueo, confirmación y guardado en ${env.name}`, async () => {
      test.setTimeout(240_000);
      resetDesign();
      // The audit rows of earlier runs stay (an audit is never deleted): only this run's are counted.
      const auditsBefore = designRow().audits;
      const message = `Mensaje final de prueba ${env.name}.`;
      const { context, page, errors, previews } = await open(browser, {
        viewport: env.viewport, hasTouch: env.touch, isMobile: env.touch,
      });
      try {
        const thanks = page.getByLabel("Mensaje final");
        await expect(thanks).toHaveValue("");
        await expect.poll(() => previews.length, { timeout: 60_000 }).toBeGreaterThanOrEqual(1);
        expect(previews.every((body) => !body.includes('"design"')), "sin borrador al cargar").toBe(true);
        await expectNoOverflow(page, `${env.name} al cargar`);
        await shot(page, `design-${env.name}-inicial`);

        // Teclear: UNA petición con el borrador, tras la espera, y el servidor lo dibuja.
        const before = previews.length;
        const drafted = page.waitForResponse((r) => /document-preview/.test(r.url()) && r.request().method() === "POST"
          && r.status() !== 308 && (r.request().postData() ?? "").includes(message), { timeout: 60_000 });
        await thanks.pressSequentially(message, { delay: 15 });
        const response = await drafted;
        expect(response.status()).toBe(200);
        expect(response.headers()["content-type"]).toContain("application/pdf");
        expect(previews.length - before, "una sola muestra para todo lo tecleado").toBe(1);
        await expect(page.getByTestId("draft-note")).toBeVisible();
        await expect(page.getByRole("link", { name: "Abrir PDF" })).toBeVisible({ timeout: 60_000 });
        // El PDF que el servidor dibuja para ese borrador dice el mensaje y sigue marcado como muestra.
        const direct = await context.request.post(`${API}/admin/document-preview/`, {
          data: { document: "sales_note", design: { thank_you_message: message } },
          headers: { "X-CSRFToken": (await context.cookies()).find((c) => c.name === "csrftoken")?.value ?? "" },
        });
        expect(direct.status()).toBe(200);
        const text = pdfText(await direct.body());
        expect(text).toContain(message);
        expect(text).toContain("MUESTRA");
        // Nada se guardó al teclear.
        expect(designRow()).toEqual({ row: null, audits: auditsBefore });

        // Un «<» bloquea la muestra y el guardado, con su motivo.
        const footer = page.getByLabel("Pie de página");
        await footer.fill("Pie <b>");
        await expect(page.getByTestId("draft-blocked")).toBeVisible();
        await expect(page.getByText("Sólo texto plano: sin los signos < ni >.")).toBeVisible();
        await expect(page.getByRole("button", { name: "Guardar cambios" })).toBeDisabled();
        await footer.fill("Pie de página de prueba");

        // Guardar: primero la lista, después la escritura.
        await page.getByRole("radio", { name: "Grande" }).check();
        await page.getByRole("button", { name: "Guardar cambios" }).click();
        const confirm = page.getByTestId("design-confirm");
        await expect(confirm).toContainText("Tamaño del logo: Mediano → Grande");
        await expect(confirm).toContainText(`Mensaje final: (vacío) → «${message}»`);
        await expect(confirm).toContainText("Pie de página: (vacío) → «Pie de página de prueba»");
        expect(designRow().row, "nada guardado antes de confirmar").toBeNull();
        await expectNoOverflow(page, `${env.name} confirmación`);
        await shot(page, `design-${env.name}-confirmacion`);
        await confirm.getByRole("button", { name: "Confirmar y guardar" }).click();
        await expect(page.getByText(/^Guardado\./)).toBeVisible({ timeout: 30_000 });
        const stored = designRow();
        expect(stored.row).toEqual({ logo_size: "large", thank_you_message: message, footer_text: "Pie de página de prueba", show_phone: true });
        expect(stored.audits).toBe(auditsBefore + 1);

        // Recargar: lo guardado es lo que se ve, sin borrador.
        await page.reload({ waitUntil: "networkidle" });
        await expect(page.getByLabel("Mensaje final")).toHaveValue(message, { timeout: 60_000 });
        await expect(page.getByRole("radio", { name: "Grande" })).toBeChecked();
        await expect(page.getByText("Sin cambios")).toBeVisible();

        // Un fiscal dice que el diseño no se le aplica.
        await page.getByRole("radio", { name: /Factura electrónica/ }).check();
        await expect(page.getByTestId("fiscal-note")).toContainText("no se aplica a boletas ni facturas");
        await expectNoOverflow(page, `${env.name} fiscal`);
        await shot(page, `design-${env.name}-guardado`);
        expect(errors, "errores de consola").toEqual([]);
      } finally {
        await context.close();
      }
    });
  }
});

test.describe("WebKit (motor de Safari)", () => {
  let browser: Browser;
  test.beforeAll(async () => { browser = await webkit.launch(); });
  test.afterAll(async () => { await browser.close(); });

  test("escritorio 1280: la muestra con el borrador se dibuja en el visor", async () => {
    test.setTimeout(180_000);
    resetDesign();
    const { context, page, errors } = await open(browser, { viewport: { width: 1280, height: 1400 } });
    try {
      expect(await page.evaluate(() => navigator.pdfViewerEnabled)).toBe(true);
      await expect(page.getByRole("status")).toHaveText("Vista previa lista.", { timeout: 60_000 });
      const first = await page.getByRole("link", { name: "Abrir PDF" }).getAttribute("href");

      await page.getByLabel("Mensaje final").fill("Mensaje final de prueba en WebKit.");
      await page.getByRole("radio", { name: "Pequeño" }).check();
      await expect(page.getByRole("link", { name: "Abrir PDF" })).not.toHaveAttribute("href", first ?? "", { timeout: 60_000 });
      await expect(page.getByRole("status")).toHaveText("Vista previa lista.", { timeout: 60_000 });
      await expect(page.getByTestId("draft-note")).toBeVisible();
      const viewer = page.locator("[data-testid='pdf-viewer']");
      await expect(viewer).toBeVisible();
      expect(await viewer.evaluate((el) => (el as HTMLObjectElement).type)).toBe("application/pdf");
      await expect(page.locator("section[aria-label='Vista previa'] iframe")).toHaveCount(0);
      await page.waitForTimeout(1500);   // que el visor del sistema termine de pintar la hoja para la captura
      await shot(page, "design-webkit-1280-borrador");
      expect(designRow().row, "el borrador no se guarda").toBeNull();
      expect(errors, "errores de consola").toEqual([]);
    } finally {
      await context.close();
    }
  });
});
