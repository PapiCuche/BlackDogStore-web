import { mkdirSync } from "node:fs";
import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { TAG, apiBase, call, shell } from "./inventory-first-support";
import { expectNoOverflow } from "./nav-ux-support";
import {
  CX_USERS, CX_PASSWORD, sembrarRecuento, recuento, limpiarRecuento, type CxSeed,
} from "./inventory-count-ux-04-support";

/**
 * COUNT-REVIEW-FREEZE (hallazgo C-M1, decisión B del propietario) — un recuento enviado a revisión no se modifica.
 * De punta a punta y SIN MOCKS: navegador real + Django real sobre una base desechable (la guarda de `shell()`).
 * Siembra y limpieza: las de INV-COUNT-UX-04 (marca `TAG`, usuarios `qacx_<corrida>`).
 *
 *   DATABASE_URL=postgres://…/blackdog_e2e_fixb DEBUG=True DEFAULT_STOREFRONT_COMPANY_SLUG=black-dog-store \
 *     E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3062 E2E_API_BASE=http://127.0.0.1:8622/api \
 *     npx playwright test e2e/inventory-count-review.spec.ts
 *
 * Escenarios (seriales):
 *   1  counter cuenta (cable 10 = sistema, funda 6 = sistema) y envía a revisión: su pantalla queda cerrada con la
 *      explicación; por la API un PUT y una anulación → 409 `count_in_review`; nada cambia.
 *   2  approver ve «Volver a edición» y «Aprobar y ajustar»; vuelve a edición con un motivo (auditado: quién, motivo,
 *      revisión → en conteo). Su pantalla queda como estaba: la usará luego, ya desactualizada.
 *   3  counter ve quién lo devolvió y por qué, corrige el cable a 9 (auditado con valor anterior y nuevo) y reenvía.
 *   4  approver pulsa «Aprobar» en la pantalla vieja (cable 10): 409 `count_stale`, nada se aplica; relee (cable 9)
 *      y aprueba: −1 al cable, nada a la funda. Rastro: enviado, devuelto, enviado, aprobado.
 *   5  390 px: la pantalla cerrada de quien cuenta y los botones de quien aprueba, sin desborde.
 */

const SHOTS = process.env.REVIEW_SHOTS ?? "test-results/review-shots";
const DESKTOP = { width: 1280, height: 800 };
const MOBILE = { width: 390, height: 844 };
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3002";
const COUNTS = "/admin/inventory/counts/";
const REASON = `Faltó contar la vitrina 2 ${TAG}`;

type Who = "approver" | "counter";
let seed: CxSeed;
let id: number;
let mobileId: number;
const sessions = new Map<Who, { context: BrowserContext; page: Page }>();
const pageErrors: string[] = [];

async function shot(page: Page, name: string) {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
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
  // confirm de enviar/aprobar: aceptar; prompt de «Volver a edición»: el motivo.
  page.on("dialog", (dialog) => void (dialog.type() === "prompt" ? dialog.accept(REASON) : dialog.accept()));
  page.on("pageerror", (error) => pageErrors.push(`${who}: ${error.message}`));
}

async function as(browser: Browser, who: Who) {
  const found = sessions.get(who);
  if (found) return found;
  const context = await login(browser, CX_USERS[who]);
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

/** Abre un recuento con la lista de la sucursal, por la API (como la pantalla), y devuelve su número. */
async function openWithList(context: BrowserContext, branch: number): Promise<number> {
  const res = await call<{ id: number }>(context.request, "POST", COUNTS, {
    branch, kind: "regular", include_stocked: true, reason: `Revisión E2E ${TAG}`,
  });
  expect(res.status, JSON.stringify(res.body)).toBe(201);
  return res.body.id;
}

const scanField = (page: Page) => page.getByLabel("Código de barras o búsqueda");
const countedCell = (page: Page, name: string) => page.locator("tr").filter({ hasText: name }).getByTestId("counted-cell");

async function enterTotal(page: Page, button: string, name: string, quantity: string) {
  await page.getByRole("button", { name: button }).first().click();
  const entry = page.getByRole("form", { name: `Cantidad de ${name}` });
  await expect(entry).toBeVisible({ timeout: 30_000 });
  await entry.getByLabel("Cantidad contada").fill(quantity);
  await entry.getByRole("button", { name: "Guardar total" }).click();
  await expect(countedCell(page, name)).toHaveText(quantity, { timeout: 30_000 });
}

type Trail = { action: string; actor: string; from: string | null; to: string | null; reason: string | null;
  previous: number | null; physical: number | null };

/** El rastro del recuento en el registro de auditoría, en orden: sólo lectura. */
function rastro(countId: number): Trail[] {
  return shell<Trail[]>(`
from store.models import AdminAuditLog
rows = AdminAuditLog.objects.filter(target_type='inventory_count', target_id=str(data['id'])).select_related('actor').order_by('id')
out([{'action': r.action, 'actor': r.actor.username if r.actor else '', 'from': r.metadata.get('from_status'),
      'to': r.metadata.get('to_status'), 'reason': r.metadata.get('reason'),
      'previous': r.metadata.get('previous'), 'physical': r.metadata.get('physical_quantity')} for r in rows])
`, { id: countId });
}

async function submit(page: Page) {
  await page.getByRole("button", { name: "Enviar a revisión" }).click(); // confirm aceptado
  await expect(page.locator("strong").filter({ hasText: "Pendiente de aprobación" })).toBeVisible({ timeout: 30_000 });
}

async function expectLocked(page: Page) {
  await expect(scanField(page)).toBeDisabled();
  await expect(page.getByText(/nadie puede cambiar cantidades/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Corregir / })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Contar / })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Enviar a revisión" })).toHaveCount(0);
}

test.describe.configure({ mode: "serial" });

test.describe("COUNT-REVIEW-FREEZE · un recuento en revisión no se modifica", () => {
  test.setTimeout(240_000);

  test.beforeAll(() => {
    seed = sembrarRecuento();
  });

  test.afterAll(async () => {
    for (const { context } of sessions.values()) await context.close().catch(() => {});
    if (process.env.E2E_KEEP === "1") {
      console.warn(`COUNT-REVIEW-FREEZE: E2E_KEEP=1, quedan los datos de ${TAG}`);
      return;
    }
    const result = limpiarRecuento();
    console.log(`COUNT-REVIEW-FREEZE limpieza: ${JSON.stringify(result.removed)}`);
    if (result.left.length) throw new Error(`limpieza incompleta: ${result.left.join(" | ")}`);
  });

  test("1 · counter envía a revisión: su pantalla se cierra y la API responde 409", async ({ browser }) => {
    const { page, context } = await as(browser, "counter");
    id = await openWithList(context, seed.branchA.id);
    await openCount(page, id);
    await enterTotal(page, `Contar ${seed.cable.name}`, seed.cable.name, "10");
    await enterTotal(page, `Contar ${seed.funda.name}`, seed.funda.name, "6");
    await submit(page);
    await expect.poll(() => recuento(id).status).toBe("review");
    await expectLocked(page);
    await expect(page.getByRole("button", { name: "Anular recuento" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Volver a edición" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Aprobar y ajustar" })).toHaveCount(0);
    await shot(page, "1-counter-en-revision-cerrado");

    const before = recuento(id);
    const put = await call<{ code: string; detail: string }>(context.request, "PUT", `${COUNTS}${id}/items/`, [
      { product: seed.cable.id, physical_quantity: 2 },
    ]);
    expect(put.status, JSON.stringify(put.body)).toBe(409);
    expect(put.body.code).toBe("count_in_review");
    expect(put.body.detail).toMatch(/en revisión/);
    const scan = await call<{ code: string }>(context.request, "POST", `${COUNTS}${id}/scan/`, {
      code: seed.cable.code, mode: "increment", expected: 10,
    });
    expect(scan.status, JSON.stringify(scan.body)).toBe(409);
    expect(scan.body.code).toBe("count_in_review");
    const cancel = await call<{ code: string }>(context.request, "POST", `${COUNTS}${id}/cancel/`, {});
    expect(cancel.status, JSON.stringify(cancel.body)).toBe(409);
    const reopen = await call(context.request, "POST", `${COUNTS}${id}/reopen/`, { reason: "yo mismo" });
    expect(reopen.status, JSON.stringify(reopen.body)).toBe(403);
    expect(recuento(id)).toEqual(before); // nada cambió
  });

  test("2 · approver vuelve a edición con un motivo, auditado", async ({ browser }) => {
    const { page } = await as(browser, "approver");
    await openCount(page, id);
    await expect(page.getByRole("button", { name: "Aprobar y ajustar" })).toBeEnabled();
    const back = page.getByRole("button", { name: "Volver a edición" });
    await expect(back).toBeEnabled();
    await expect(scanField(page)).toBeDisabled(); // tampoco quien aprueba anota en revisión
    await shot(page, "2-approver-en-revision");

    await back.click(); // prompt → REASON
    await expect.poll(() => recuento(id).status, { timeout: 30_000 }).toBe("counting");
    await expect(page.getByRole("note", { name: /Devuelto a edición/ })).toContainText(REASON, { timeout: 30_000 });
    const reopened = rastro(id).filter((r) => r.action === "inventory_count_reopened");
    expect(reopened).toEqual([{
      action: "inventory_count_reopened", actor: CX_USERS.approver, from: "review", to: "counting", reason: REASON,
      previous: null, physical: null,
    }]);
    await shot(page, "2-approver-devuelto");
  });

  test("3 · counter ve el motivo, corrige el cable y reenvía", async ({ browser }) => {
    const { page } = await as(browser, "counter");
    await openCount(page, id);
    const note = page.getByRole("note", { name: /Devuelto a edición/ });
    await expect(note).toContainText(CX_USERS.approver);
    await expect(note).toContainText(REASON);
    await expect(scanField(page)).toBeEnabled();
    await shot(page, "3-counter-devuelto");
    await enterTotal(page, `Corregir ${seed.cable.name}`, seed.cable.name, "9");
    const changed = rastro(id).filter((r) => r.action === "inventory_count_line_changed");
    expect(changed).toEqual([expect.objectContaining({ actor: CX_USERS.counter, previous: 10, physical: 9 })]);
    await submit(page);
    await expect.poll(() => recuento(id).status).toBe("review");
    await expectLocked(page);
  });

  test("4 · la pantalla vieja de quien aprueba no aprueba nada; la nueva aplica lo que muestra", async ({ browser }) => {
    const { page } = await as(browser, "approver");
    // Esta pantalla todavía muestra el cable en 10 (antes de la corrección): aprobarla sería aprobar a ciegas.
    await expect(countedCell(page, seed.cable.name)).toHaveText("10");
    await page.getByRole("button", { name: "Aprobar y ajustar" }).click(); // confirm aceptado
    await expect(page.getByText(/cambió desde que lo abriste/)).toBeVisible({ timeout: 30_000 });
    expect(recuento(id).movements).toEqual([]);
    expect(recuento(id).status).toBe("review");
    await expect(countedCell(page, seed.cable.name)).toHaveText("9", { timeout: 30_000 }); // releído
    await shot(page, "4-approver-version-vieja-rechazada");

    await page.getByRole("button", { name: "Aprobar y ajustar" }).click();
    await expect.poll(() => recuento(id).status, { timeout: 30_000 }).toBe("approved");
    const state = recuento(id);
    expect(state.movements).toEqual([
      { product: seed.cable.id, type: "correction_negative", quantity: 1, before: 10, after: 9 },
    ]);
    await shot(page, "4-approver-aprobado");

    const transitions = rastro(id)
      .filter((r) => ["inventory_count_submitted", "inventory_count_reopened", "inventory_count_approved"].includes(r.action))
      .map((r) => [r.action, r.actor, r.from, r.to]);
    expect(transitions).toEqual([
      ["inventory_count_submitted", CX_USERS.counter, "counting", "review"],
      ["inventory_count_reopened", CX_USERS.approver, "review", "counting"],
      ["inventory_count_submitted", CX_USERS.counter, "counting", "review"],
      ["inventory_count_approved", CX_USERS.approver, "review", "approved"],
    ]);
  });

  test("5 · 390 px: cerrado para quien cuenta, botones de quien aprueba, sin desborde", async ({ browser }) => {
    const { context: counterDesktop } = await as(browser, "counter");
    mobileId = await openWithList(counterDesktop, seed.branchB.id);
    const put = await call(counterDesktop.request, "PUT", `${COUNTS}${mobileId}/items/`, [
      { product: seed.protector.id, physical_quantity: 5 },
    ]);
    expect(put.status, JSON.stringify(put.body)).toBe(200);
    const sent = await call(counterDesktop.request, "POST", `${COUNTS}${mobileId}/submit/`, {});
    expect(sent.status, JSON.stringify(sent.body)).toBe(200);

    for (const who of ["counter", "approver"] as const) {
      const { context: desktop } = await as(browser, who);
      const context = await browser.newContext({
        viewport: MOBILE, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
        storageState: await desktop.storageState(), baseURL: BASE,
      });
      try {
        const page = await context.newPage();
        watch(page, `390 ${who}`);
        await openCount(page, mobileId);
        if (who === "counter") {
          await expectLocked(page);
        } else {
          await expect(page.getByRole("button", { name: "Volver a edición" })).toBeVisible();
          await expect(page.getByRole("button", { name: "Aprobar y ajustar" })).toBeVisible();
        }
        await expectNoOverflow(page, `390 · ${who} en revisión`);
        await shot(page, `5-390-${who}-en-revision`);
      } finally {
        await context.close();
      }
    }
    // Lo deja anulado (por quien aprueba): no queda nada abierto de la corrida.
    const { context: approver } = await as(browser, "approver");
    const cancelled = await call(approver.request, "POST", `${COUNTS}${mobileId}/cancel/`, {});
    expect(cancelled.status, JSON.stringify(cancelled.body)).toBe(200);
  });

  test("sin errores de página", () => {
    expect(pageErrors).toEqual([]);
  });
});
