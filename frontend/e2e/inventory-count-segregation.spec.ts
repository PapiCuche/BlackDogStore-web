import { mkdirSync } from "node:fs";
import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { TAG, apiBase, call, shell } from "./inventory-first-support";
import { expectNoOverflow } from "./nav-ux-support";
import {
  CX_USERS, CX_PASSWORD, sembrarRecuento, recuento, limpiarRecuento, type CxSeed,
} from "./inventory-count-ux-04-support";

/**
 * DEC-INV-06 — quien realiza un recuento no lo aprueba. De punta a punta y SIN MOCKS: navegador real + Django real
 * sobre una base desechable (la guarda de `shell()`). Siembra y limpieza: las de INV-COUNT-UX-04 (marca `TAG`,
 * usuarios `qacx_<corrida>`), más un usuario `qacx_<corrida>_own` con «Aprobar recuentos propios».
 *
 *   DATABASE_URL=postgres://…/blackdog_e2e_seg06 DEBUG=True DEFAULT_STOREFRONT_COMPANY_SLUG=black-dog-store \
 *     E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3086 E2E_API_BASE=http://127.0.0.1:8666/api \
 *     npx playwright test e2e/inventory-count-segregation.spec.ts
 *
 * Escenarios (seriales):
 *   1  approver cuenta el cable (10 → 7) y lo envía a revisión: no ve «Aprobar y ajustar», ve por qué; por la API su
 *      aprobación → 403 `count_self_approval` y nada se aplica.
 *   2  otra persona con permiso de aprobar (own, que no contó) lo aprueba con el botón de siempre: −3 al cable;
 *      el rastro no lo marca como propio.
 *   3  own cuenta su propio recuento en B (protector 5 → 4): ve «Aprobar mi propio recuento», que pide el motivo;
 *      aprobado, la pantalla lo dice con el motivo y la auditoría lo marca (`inventory_count_self_approved`).
 *   4  390 px: la nota de quien no puede aprobar y la historia de una autoaprobación, sin desborde.
 */

const SHOTS = process.env.SEG_SHOTS ?? "test-results/seg-shots";
const DESKTOP = { width: 1280, height: 800 };
const MOBILE = { width: 390, height: 844 };
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3002";
const COUNTS = "/admin/inventory/counts/";
const REASON = `Sólo una persona en la tienda ${TAG}`;
const OWN_USER = `${CX_USERS.approver.replace(/_apr$/, "")}_own`;

type Who = "approver" | "own";
let seed: CxSeed;
let firstId: number;
let ownId: number;
let mobileId: number;
const sessions = new Map<Who, { context: BrowserContext; page: Page }>();
const pageErrors: string[] = [];

async function shot(page: Page, name: string) {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

/** Un aprobador más, con «Aprobar recuentos propios»: lo borra `limpiarRecuento()` (prefijo y marca). */
function sembrarDueno() {
  shell(`
from django.contrib.auth import get_user_model
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment
company = Company.objects.get(slug=data['slug'])
user = get_user_model().objects.create_user(username=data['username'], password=data['password'])
role = CompanyRole.objects.create(
    company=company, name='Rol ' + data['username'] + ' ' + data['tag'], slug=(data['username'] + '-' + data['tag']).lower(),
    capabilities=['company.view', 'inventory.view', 'inventory.adjust', 'products.view', 'inventory.counts.approve',
                  'inventory.counts.approve_own'],
)
membership = Membership.objects.create(user=user, company=company, role='inventory', is_active=True,
                                       branch_access_mode=Membership.ACCESS_MODE_ALL)
MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)
out(user.pk)
`, { username: OWN_USER, password: CX_PASSWORD });
}

async function login(browser: Browser, username: string) {
  for (let attempt = 0; ; attempt++) {
    const context = await browser.newContext({ viewport: DESKTOP, baseURL: BASE });
    const res = await context.request.post(`${apiBase()}/auth/login/`, { data: { username, password: CX_PASSWORD } });
    if (res.status() === 200) {
      await context.request.get(`${apiBase()}/auth/csrf/`);
      return context;
    }
    await context.close();
    if (res.status() !== 429 || attempt >= 2) throw new Error(`no pude iniciar sesión como ${username}: ${res.status()}`);
    await new Promise((resolve) => setTimeout(resolve, 62_000));
  }
}

function watch(page: Page, who: string) {
  // confirm de enviar/aprobar: aceptar; prompt de «Aprobar mi propio recuento»: el motivo.
  page.on("dialog", (dialog) => void (dialog.type() === "prompt" ? dialog.accept(REASON) : dialog.accept()));
  page.on("pageerror", (error) => pageErrors.push(`${who}: ${error.message}`));
}

async function as(browser: Browser, who: Who) {
  const found = sessions.get(who);
  if (found) return found;
  const context = await login(browser, who === "own" ? OWN_USER : CX_USERS.approver);
  const page = await context.newPage();
  watch(page, who);
  const made = { context, page };
  sessions.set(who, made);
  return made;
}

async function openCount(page: Page, countId: number) {
  await page.goto(`${COUNTS}${countId}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: `Recuento #${countId}` })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Cargando recuento…")).toHaveCount(0, { timeout: 30_000 });
}

async function openWithList(context: BrowserContext, branch: number): Promise<number> {
  const res = await call<{ id: number }>(context.request, "POST", COUNTS, {
    branch, kind: "regular", include_stocked: true, reason: `Segregación E2E ${TAG}`,
  });
  expect(res.status, JSON.stringify(res.body)).toBe(201);
  return res.body.id;
}

const countedCell = (page: Page, name: string) => page.locator("tr").filter({ hasText: name }).getByTestId("counted-cell");

async function enterTotal(page: Page, name: string, quantity: string) {
  await page.getByRole("button", { name: `Contar ${name}` }).first().click();
  const entry = page.getByRole("form", { name: `Cantidad de ${name}` });
  await expect(entry).toBeVisible({ timeout: 30_000 });
  await entry.getByLabel("Cantidad contada").fill(quantity);
  await entry.getByRole("button", { name: "Guardar total" }).click();
  await expect(countedCell(page, name)).toHaveText(quantity, { timeout: 30_000 });
}

async function submit(page: Page) {
  await page.getByRole("button", { name: "Enviar a revisión" }).click(); // confirm aceptado
  await expect(page.locator("strong").filter({ hasText: "Pendiente de aprobación" })).toBeVisible({ timeout: 30_000 });
}

type Trail = { action: string; actor: string; self: boolean | null; reason: string | null; performers: number[] | null };

function rastro(countId: number): Trail[] {
  return shell<Trail[]>(`
from store.models import AdminAuditLog
rows = AdminAuditLog.objects.filter(target_type='inventory_count', target_id=str(data['id'])).select_related('actor').order_by('id')
out([{'action': r.action, 'actor': r.actor.username if r.actor else '', 'self': r.metadata.get('self_approval'),
      'reason': r.metadata.get('self_approval_reason', r.metadata.get('reason')),
      'performers': r.metadata.get('performers')} for r in rows])
`, { id: countId });
}

function userId(username: string): number {
  return shell<number>(`
from django.contrib.auth import get_user_model
out(get_user_model().objects.get(username=data['username']).pk)
`, { username });
}

test.describe.configure({ mode: "serial" });

test.describe("DEC-INV-06 · quien realiza un recuento no lo aprueba", () => {
  test.setTimeout(240_000);

  test.beforeAll(() => {
    seed = sembrarRecuento();
    sembrarDueno();
  });

  test.afterAll(async () => {
    for (const { context } of sessions.values()) await context.close().catch(() => {});
    if (process.env.E2E_KEEP === "1") {
      console.warn(`DEC-INV-06: E2E_KEEP=1, quedan los datos de ${TAG}`);
      return;
    }
    const result = limpiarRecuento();
    console.log(`DEC-INV-06 limpieza: ${JSON.stringify(result.removed)}`);
    if (result.left.length) throw new Error(`limpieza incompleta: ${result.left.join(" | ")}`);
  });

  test("1 · approver cuenta y envía: no ve «Aprobar», ve por qué, y la API responde 403", async ({ browser }) => {
    const { page, context } = await as(browser, "approver");
    firstId = await openWithList(context, seed.branchA.id);
    await openCount(page, firstId);
    await enterTotal(page, seed.cable.name, "7");
    await submit(page);
    await expect.poll(() => recuento(firstId).status).toBe("review");

    await expect(page.getByRole("button", { name: "Aprobar y ajustar" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Aprobar mi propio recuento" })).toHaveCount(0);
    const note = page.getByRole("note", { name: "No puedes aprobar este recuento" });
    await expect(note).toBeVisible();
    await expect(note).toContainText("Participaste en este recuento");
    await expect(note).toContainText("Aprobar recuentos");
    await expect(page.getByRole("button", { name: "Volver a edición" })).toBeVisible();
    await shot(page, "1-approver-propio-sin-aprobar");

    const detail = await call<{ version: string; self_approval: unknown }>(context.request, "GET", `${COUNTS}${firstId}/`);
    expect(detail.body.self_approval).toEqual({ counted_by_me: true, may_approve_own: false });
    const before = recuento(firstId);
    const res = await call<{ code: string; detail: string }>(context.request, "POST", `${COUNTS}${firstId}/approve/`, {
      version: detail.body.version, self_approval_reason: "lo intento igual",
    });
    expect(res.status, JSON.stringify(res.body)).toBe(403);
    expect(res.body.code).toBe("count_self_approval");
    expect(recuento(firstId)).toEqual(before);
    expect(before.movements).toEqual([]);
  });

  test("2 · otra persona con permiso de aprobar lo aprueba como siempre", async ({ browser }) => {
    const { page } = await as(browser, "own");
    await openCount(page, firstId);
    await expect(page.getByRole("note", { name: "No puedes aprobar este recuento" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Aprobar mi propio recuento" })).toHaveCount(0);
    await page.getByRole("button", { name: "Aprobar y ajustar" }).click(); // confirm aceptado
    await expect.poll(() => recuento(firstId).status, { timeout: 30_000 }).toBe("approved");
    const after = recuento(firstId);
    expect(after.movements).toEqual([
      expect.objectContaining({ product: seed.cable.id, type: "correction_negative", quantity: 3, before: 10, after: 7 }),
    ]);
    await expect(page.getByRole("note", { name: "Aprobado por quien lo contó" })).toHaveCount(0);
    await shot(page, "2-otra-persona-aprobado");
    const approved = rastro(firstId).filter((r) => r.action === "inventory_count_approved");
    expect(approved).toEqual([expect.objectContaining({ actor: OWN_USER, self: false, performers: [userId(CX_USERS.approver)] })]);
    expect(rastro(firstId).some((r) => r.action === "inventory_count_self_approved")).toBe(false);
  });

  test("3 · con «Aprobar recuentos propios»: pide el motivo, aprueba y queda marcado", async ({ browser }) => {
    const { page, context } = await as(browser, "own");
    ownId = await openWithList(context, seed.branchB.id);
    await openCount(page, ownId);
    await enterTotal(page, seed.protector.name, "4");
    await submit(page);

    await expect(page.getByRole("button", { name: "Aprobar y ajustar" })).toHaveCount(0);
    await expect(page.getByRole("note", { name: "Aprobación de tu propio recuento" })).toContainText("auditoría");
    await shot(page, "3-own-antes-de-aprobar");
    await page.getByRole("button", { name: "Aprobar mi propio recuento" }).click(); // prompt: el motivo
    await expect.poll(() => recuento(ownId).status, { timeout: 30_000 }).toBe("approved");
    expect(recuento(ownId).movements).toEqual([
      expect.objectContaining({ product: seed.protector.id, type: "correction_negative", quantity: 1, before: 5, after: 4 }),
    ]);
    const history = page.getByRole("note", { name: "Aprobado por quien lo contó" });
    await expect(history).toBeVisible({ timeout: 30_000 });
    await expect(history).toContainText(OWN_USER);
    await expect(history).toContainText(REASON);
    await shot(page, "3-own-autoaprobado");

    const trail = rastro(ownId);
    expect(trail.filter((r) => r.action === "inventory_count_approved")).toEqual([
      expect.objectContaining({ actor: OWN_USER, self: true, reason: REASON, performers: [userId(OWN_USER)] }),
    ]);
    expect(trail.filter((r) => r.action === "inventory_count_self_approved")).toEqual([
      expect.objectContaining({ actor: OWN_USER, reason: REASON }),
    ]);
  });

  test("4 · 390 px: la nota de quien no puede aprobar y la historia, sin desborde", async ({ browser }) => {
    const { context: approverDesktop } = await as(browser, "approver");
    mobileId = await openWithList(approverDesktop, seed.branchA.id);
    const put = await call(approverDesktop.request, "PUT", `${COUNTS}${mobileId}/items/`, [
      { product: seed.funda.id, physical_quantity: 6 },
    ]);
    expect(put.status, JSON.stringify(put.body)).toBe(200);
    const sent = await call(approverDesktop.request, "POST", `${COUNTS}${mobileId}/submit/`, {});
    expect(sent.status, JSON.stringify(sent.body)).toBe(200);

    for (const [who, countId, name] of [
      ["approver", mobileId, "4-390-approver-propio-sin-aprobar"],
      ["own", ownId, "4-390-own-autoaprobado"],
    ] as const) {
      const { context: desktop } = await as(browser, who);
      const context = await browser.newContext({
        viewport: MOBILE, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
        storageState: await desktop.storageState(), baseURL: BASE,
      });
      try {
        const page = await context.newPage();
        watch(page, `390 ${who}`);
        await openCount(page, countId);
        if (who === "approver") {
          await expect(page.getByRole("note", { name: "No puedes aprobar este recuento" })).toBeVisible();
          await expect(page.getByRole("button", { name: "Aprobar y ajustar" })).toHaveCount(0);
        } else {
          await expect(page.getByRole("note", { name: "Aprobado por quien lo contó" })).toBeVisible();
        }
        await expectNoOverflow(page, `390 · ${who}`);
        await shot(page, name);
      } finally {
        await context.close();
      }
    }
    const cancelled = await call(approverDesktop.request, "POST", `${COUNTS}${mobileId}/cancel/`, { reason: `Fin de la prueba ${TAG}` });
    expect(cancelled.status, JSON.stringify(cancelled.body)).toBe(200);
  });

  test("sin errores de página", () => {
    expect(pageErrors).toEqual([]);
  });
});
