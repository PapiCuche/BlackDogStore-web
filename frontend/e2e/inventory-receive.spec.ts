import { mkdirSync } from "node:fs";
import { test, expect, type Browser, type BrowserContext, type Locator, type Page, type Request } from "@playwright/test";
import {
  TAG, OTHER_USER, OTHER_PASSWORD, imei, sembrar, contar, conciliar, signIn, ficha, type Seed,
} from "./inventory-first-support";
import {
  RX_USERS, RX_PASSWORD, serial, sembrarRecepcion, limpiarRecepcion, setAdjust, deleteProduct, estado, recibos, productos,
  otraEmpresa, sessionFor, jsonCall, type Named, type RxSeed,
} from "./inventory-receive-support";

/**
 * INV-OPS-01 — «Recibir mercadería» y el centro de operaciones del inventario, de punta a punta y SIN MOCKS
 * (interfaz real de Chromium + API real). La base sólo se lee con `manage.py shell` para contar; lo único que se
 * intercepta es la RESPUESTA perdida del escenario 5 (se deja que el servidor procese y luego se corta la red).
 *
 * Mismo molde que `inventory-first.spec.ts`: guarda de base local y desechable, datos sintéticos con la marca `TAG`,
 * limpieza por marca al final (`E2E_KEEP=1` la omite), escenarios seriales que se pasan lo que dejan.
 *
 * ENTORNO: el de `inventory-first` (DEBUG=1, DEFAULT_STOREFRONT_COMPANY_SLUG=black-dog-store, `next dev` con la tarjeta de
 * accesos de desarrollo, base cuyo nombre lleva «e2e»). Los usuarios con permisos exactos (`qaer_*`) los crea la
 * propia prueba; entrar por la API consume el límite de 5 inicios/min, así que se entra una vez y se reutiliza.
 */

const SHOTS = process.env.OPS_SHOTS
  ?? "/private/tmp/claude-501/-Users-cmaucorp-Desktop-BlackDogStore-web/7a0ba6be-01d7-46a0-a74d-d9c68236ac71/scratchpad/ops-shots-real";
const MOBILE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 800 };

let base: Seed;
let rx: RxSeed;
let adminContext: BrowserContext;
let admin: Page;
const sessions = new Map<string, { context: BrowserContext; page: Page }>();
/** Lo que un escenario le deja al siguiente. */
const shared: {
  usedUnit?: { serial: string; imei: string };
  cableBefore?: number;
  otherBefore?: Awaited<ReturnType<typeof otraEmpresa>>;
  keys: string[];
  productsBefore?: { all: number; tagged: number };
  confirmBody?: unknown;
  idemKey?: string;
} = { keys: [] };

// ── Ayudas de página ───────────────────────────────────────────────────────────────────────────────────────────

async function shot(page: Page, name: string) {
  mkdirSync(SHOTS, { recursive: true });
  const prefix = (page.viewportSize()?.width ?? DESKTOP.width) <= 500 ? "m390" : "d1280";
  await page.screenshot({ path: `${SHOTS}/${prefix}-${name}.png`, fullPage: true });
}

/** El documento no se ensancha más que la ventana (copia de `inventory-first.spec.ts`). */
async function expectNoOverflow(target: Page, what: string) {
  const size = await target.evaluate(() => {
    const inner = window.innerWidth;
    const scroll = document.scrollingElement!.scrollWidth;
    const offenders = scroll <= inner ? [] : [...document.body.querySelectorAll("*")]
      .filter((el) => el.getBoundingClientRect().right > inner + 1 && !el.parentElement?.closest("[class*='overflow-x-auto'], [class*='overflow-auto']"))
      .slice(0, 6)
      .map((el) => `${el.tagName.toLowerCase()}.${String((el as HTMLElement).className).slice(0, 60)} -> ${Math.round(el.getBoundingClientRect().right)}px`);
    return { scroll, inner, offenders };
  });
  expect(size.scroll, `${what}: la página mide ${size.scroll}px en una ventana de ${size.inner}px; se pasan: ${size.offenders.join(" | ")}`)
    .toBeLessThanOrEqual(size.inner);
}

/** Texto de la página SIN el <title> (con `next dev` el título viaja en el cuerpo y `getByText` resolvería a dos). */
function pageText(target: Page, matcher: string | RegExp, options?: { exact?: boolean }): Locator {
  return target.getByText(matcher, options).and(target.locator(":not(title)"));
}

async function viewport(page: Page, size: { width: number; height: number }) {
  await page.setViewportSize(size);
}

async function as(browser: Browser, key: keyof typeof RX_USERS | "other") {
  const found = sessions.get(key);
  if (found) return found;
  const username = key === "other" ? OTHER_USER : RX_USERS[key];
  const password = key === "other" ? OTHER_PASSWORD : RX_PASSWORD;
  let made: { context: BrowserContext; page: Page } | null = null;
  for (let attempt = 0; attempt < 3 && !made; attempt++) {
    try { made = await sessionFor(browser, username, password); }
    catch (error) {
      if (attempt === 2 || !/429/.test(String(error))) throw error;
      await new Promise((resolve) => setTimeout(resolve, 62_000)); // límite de 5 inicios por minuto
    }
  }
  made!.page.on("dialog", (dialog) => void dialog.accept());
  sessions.set(key, made!);
  return made!;
}

// ── El asistente ───────────────────────────────────────────────────────────────────────────────────────────────

async function openReceive(page: Page) {
  await page.goto("/admin/inventory/receive", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" }), "el asistente no cargó").toBeVisible({ timeout: 60_000 });
}

async function setOrigin(page: Page, origin: { branch?: string; supplier?: string; doc?: string; notes?: string }) {
  if (origin.branch) await page.locator("#receive-branch").selectOption({ label: origin.branch });
  if (origin.supplier) await page.locator("#receive-supplier").selectOption({ label: origin.supplier });
  if (origin.doc !== undefined) await page.locator("#receive-doc").fill(origin.doc);
  if (origin.notes !== undefined) await page.locator("#receive-notes").fill(origin.notes);
}

const button = (page: Page, name: string | RegExp) => page.getByRole("button", { name });

async function toItems(page: Page) {
  await button(page, "Siguiente: artículos").click();
  await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
}

async function addProduct(page: Page, name: string): Promise<Locator> {
  await page.locator("#receive-product").fill(name);
  await page.getByRole("option").filter({ hasText: name }).first().click({ timeout: 30_000 });
  const card = page.getByRole("article", { name, exact: true });
  await expect(card).toBeVisible();
  return card;
}

type Dev = { serial: string; imei?: string; imei2?: string; condition?: string };

/** Llena el equipo número `i` (0-based) de una tarjeta; el primero ya existe, los demás se agregan. */
async function fillUnit(card: Locator, i: number, dev: Dev) {
  if ((await card.getByLabel("Número de serie *").count()) <= i) await card.getByRole("button", { name: "Agregar otro equipo" }).click();
  const at = (label: string) => card.getByLabel(label, { exact: true }).nth(i);
  await at("Número de serie *").fill(dev.serial);
  if (dev.imei !== undefined) await card.locator('[data-field="imei"]').nth(i).fill(dev.imei);
  if (dev.imei2 !== undefined) await card.locator('[data-field="imei2"]').nth(i).fill(dev.imei2);
  if (dev.condition) await card.getByLabel("Estado del equipo").nth(i).selectOption({ label: dev.condition });
}

async function toReview(page: Page) {
  await button(page, "Siguiente: revisar").click();
  await expect(page.getByRole("heading", { name: "Revisa antes de registrar" })).toBeVisible();
}

const summary = (page: Page) => page.getByRole("status").filter({ hasText: "Se prepararán" });

async function accept(page: Page) {
  await page.getByLabel(/Revisé los datos y quiero registrar/).check();
}

const confirmButton = (page: Page) => button(page, /Confirmar recepción|Registrando/);

/** Escucha los POST a «confirm» (la clave de idempotencia y el cuerpo) mientras dura el escenario. */
function watchConfirm(page: Page) {
  const seen: { key: string; body: unknown }[] = [];
  const onRequest = (req: Request) => {
    if (req.method() === "POST" && /receipts\/confirm/.test(req.url()) && req.redirectedFrom() === null) {
      seen.push({ key: req.headers()["idempotency-key"] ?? "", body: req.postDataJSON() });
    }
  };
  page.on("request", onRequest);
  return { seen, stop: () => page.off("request", onRequest) };
}

const waitConfirmResponse = (page: Page) =>
  page.waitForResponse((res) => /receipts\/confirm/.test(res.url()) && res.request().method() === "POST" && res.status() !== 308);

// ── Preparación ────────────────────────────────────────────────────────────────────────────────────────────────

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  test.setTimeout(300_000);
  base = sembrar();
  rx = sembrarRecepcion();
  adminContext = await browser.newContext({ viewport: DESKTOP });
  admin = await adminContext.newPage();
  // «¿Salir de la página?»: dejar un asistente a medias es normal en una prueba; aceptar para no colgar la navegación.
  admin.on("dialog", (dialog) => void dialog.accept());
  await signIn(admin);
  shared.otherBefore = otraEmpresa();
  shared.productsBefore = productos();
});

test.afterAll(async () => {
  for (const { context } of sessions.values()) await context.close().catch(() => undefined);
  await adminContext?.close();
  if (process.env.E2E_KEEP === "1") { console.warn(`INV-OPS-01: datos conservados con la marca ${TAG}`); return; }
  const result = limpiarRecepcion();
  if (process.env.E2E_VERBOSE === "1") console.warn("INV-OPS-01: limpieza ->", JSON.stringify(result));
  if (result.left.length) console.warn("INV-OPS-01: lo que la limpieza no pudo borrar ->", result.left.join(" | "));
});

test.describe("recibir mercadería · de la caja a las etiquetas", () => {
  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("1 · el centro de operaciones: cinco tarjetas, motivos claros y modo avanzado", async ({ browser }) => {
    test.setTimeout(240_000);
    const tasks = ["Recibir mercadería", "Registrar un equipo rápidamente", "Recuento físico", "Etiquetas pendientes", "Consultar stock"];

    await admin.goto("/admin/inventory/operations", { waitUntil: "networkidle" });
    const list = admin.getByRole("list", { name: "Tareas del inventario" });
    await expect(list).toBeVisible({ timeout: 60_000 });
    await expect(list.getByRole("listitem")).toHaveCount(5);
    for (const title of tasks) await expect(list.getByRole("link", { name: new RegExp(title) }), `«${title}» debía ser un enlace`).toBeVisible();
    await expect(list.locator('[aria-disabled="true"]')).toHaveCount(0);
    await expect(list.getByRole("link", { name: /Recibir mercadería/ })).toHaveAttribute("href", "/admin/inventory/receive");

    const advanced = admin.getByRole("region", { name: "Modo avanzado" });
    await expect(advanced).toBeVisible();
    const hrefs = await advanced.getByRole("link").evaluateAll((els) => els.map((el) => el.getAttribute("href") ?? ""));
    expect(hrefs.length, `modo avanzado: ${hrefs.join(", ")}`).toBeGreaterThanOrEqual(8);
    for (const href of hrefs) expect(href).toMatch(/^\/admin\//);
    for (const known of ["/admin/inventory/stock", "/admin/inventory/counts", "/admin/inventory/bulk"]) expect(hrefs, `falta ${known}`).toContain(known);
    await shot(admin, "hub-admin");

    // Una persona que sólo VE el inventario: las tareas que mueven stock se ven, desactivadas y con el motivo.
    const viewer = await as(browser, "viewer");
    await viewer.page.goto("/admin/inventory/operations", { waitUntil: "networkidle" });
    const vlist = viewer.page.getByRole("list", { name: "Tareas del inventario" });
    await expect(vlist).toBeVisible({ timeout: 60_000 });
    const disabled = vlist.locator('[aria-disabled="true"]');
    for (const title of ["Recibir mercadería", "Registrar un equipo rápidamente", "Recuento físico"]) {
      const card = disabled.filter({ hasText: title });
      await expect(card, `«${title}» debía estar desactivada`).toHaveCount(1);
      await expect(card).toContainText("Tu usuario no puede");
      await expect(card).toContainText("Pídelo a quien administra el inventario.");
    }
    await expect(vlist.getByRole("link", { name: /Consultar stock/ })).toBeVisible();
    await expect(vlist.getByRole("link", { name: /Recibir mercadería/ })).toHaveCount(0);
    await expect(viewer.page.getByRole("region", { name: "Modo avanzado" })).toBeVisible();
    await shot(viewer.page, "hub-viewer");
    // La puerta del asistente también lo dice con palabras llanas.
    await viewer.page.goto("/admin/inventory/receive", { waitUntil: "networkidle" });
    await expect(pageText(viewer.page, /pide el permiso de ajustar inventario/i)).toBeVisible({ timeout: 30_000 });
    await shot(viewer.page, "receive-denied");

    // 390 px: sin desborde horizontal.
    await viewport(admin, MOBILE);
    await admin.goto("/admin/inventory/operations", { waitUntil: "networkidle" });
    await expect(admin.getByRole("list", { name: "Tareas del inventario" })).toBeVisible({ timeout: 60_000 });
    await expectNoOverflow(admin, "centro de operaciones a 390 px");
    await shot(admin, "hub-admin");
    await viewport(viewer.page, MOBILE);
    await viewer.page.goto("/admin/inventory/operations", { waitUntil: "networkidle" });
    await expect(viewer.page.getByRole("list", { name: "Tareas del inventario" })).toBeVisible({ timeout: 60_000 });
    await expectNoOverflow(viewer.page, "centro de operaciones (sólo lectura) a 390 px");
    await shot(viewer.page, "hub-viewer");
    await viewport(admin, DESKTOP);
    await viewport(viewer.page, DESKTOP);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("2 · un equipo seminuevo: serie, IMEI, procedencia, revisión y confirmación", async () => {
    test.setTimeout(240_000);
    const dev = { serial: serial("USD"), imei: imei(21) };
    const doc = `FAC-${TAG}-001`;
    const before = estado(rx.used.id);
    const receiptsBefore = recibos();
    expect(before.units).toHaveLength(0);
    const watch = watchConfirm(admin);

    await openReceive(admin);
    await setOrigin(admin, { branch: base.branchName, supplier: rx.supplier.name, doc, notes: `[E2E] ${TAG} seminuevo` });
    await shot(admin, "s2-step1-origen");
    await toItems(admin);
    await shot(admin, "s2-step2-vacio");
    const card = await addProduct(admin, rx.used.name);
    // Un producto "usado" nace con el estado usado; se fija también a mano para no depender de ello.
    await fillUnit(card, 0, { ...dev, condition: "Usado" });
    await shot(admin, "s2-step2-lleno");
    await toReview(admin);

    // La revisión dice qué pasará ANTES de guardar: 1 equipo, 1 etiqueta, y todavía no hay nada en la base.
    await expect(summary(admin)).toContainText("1 equipo identificado");
    await expect(summary(admin)).toContainText("1 etiqueta");
    await expect(admin.getByRole("definition").filter({ hasText: rx.supplier.name })).toBeVisible();
    await expect(admin.getByRole("definition").filter({ hasText: doc })).toBeVisible();
    expect(estado(rx.used.id).units).toHaveLength(0);
    await expect(confirmButton(admin)).toBeDisabled();
    await shot(admin, "s2-step3-revisar");
    await accept(admin);
    await expect(confirmButton(admin)).toBeEnabled();
    const answered = waitConfirmResponse(admin);
    await confirmButton(admin).click();
    const reply = await answered;
    expect(reply.status()).toBe(201);
    expect((await reply.json()).replayed).toBeUndefined();

    await expect(admin.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    const next = admin.getByRole("region", { name: "Siguiente paso" });
    await expect(next.getByText(/Etiquetas listas para imprimir/)).toBeVisible();
    await expect(admin.getByRole("link", { name: "Imprimir o ver etiquetas" })).toBeVisible();
    await shot(admin, "s2-step4-resultado");
    watch.stop();
    expect(watch.seen).toHaveLength(1);
    expect(watch.seen[0].key).toMatch(/^rcv-/);

    const after = estado(rx.used.id);
    expect(after.units).toHaveLength(1);
    expect(after.units[0]).toMatchObject({ serial: dev.serial, imei: dev.imei, condition: "used", status: "available", branch: base.branch });
    const entries = after.movements.filter((m) => m.type === "purchase_entry");
    expect(after.movements).toHaveLength(1);
    expect(entries).toHaveLength(1);
    expect(entries[0].reason).toContain(doc);
    expect(entries[0].reason).toContain(rx.supplier.name);
    expect(entries[0]).toMatchObject({ quantity: 1, before: 0, after: 1, branch: base.branch });
    expect(after.stock[String(base.branch)]).toBe(1);
    expect(after.batches).toHaveLength(1);
    expect(after.batches[0].copies).toBe(1);
    expect(recibos()).toBe(receiptsBefore + 1);
    shared.usedUnit = dev;
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("3 · equipos nuevos: identidades distintas, repetidos marcados en su fila, sin filtrar otra empresa", async () => {
    test.setTimeout(300_000);
    const used = shared.usedUnit!;
    const a1 = { serial: serial("NA", 1), imei: imei(31) };
    const a2 = { serial: serial("NA", 2), imei: imei(32) };
    // Este IMEI sólo lo tiene la OTRA empresa: para esta no es un repetido y su respuesta no debe nombrar nada ajeno.
    const b1 = { serial: serial("NB", 1), imei: rx.foreign.imei };

    await openReceive(admin);
    await setOrigin(admin, { branch: base.branchName, doc: `FAC-${TAG}-002` });
    await toItems(admin);
    const cardA = await addProduct(admin, rx.newA.name);
    await fillUnit(cardA, 0, { ...a1, condition: "Nuevo" });
    await fillUnit(cardA, 1, { serial: a2.serial, imei: a1.imei, condition: "Nuevo" });

    // (a) El mismo IMEI dos veces en la lista: la pantalla lo marca en AMBAS filas antes de pedirle nada al servidor.
    await button(admin, "Siguiente: revisar").click();
    await expect(admin.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
    const imeis = cardA.locator('[data-field="imei"]');
    await expect(imeis.nth(0)).toHaveAttribute("aria-invalid", "true");
    await expect(imeis.nth(1)).toHaveAttribute("aria-invalid", "true");
    await expect(cardA.getByText("Este IMEI está repetido en esta recepción.")).toHaveCount(2);
    await expect(cardA.locator('[data-field="serial_number"]').nth(1)).not.toHaveAttribute("aria-invalid", "true");
    await shot(admin, "s3-step2-repetido-en-pantalla");

    // (b) El mismo IMEI escrito de otra forma (con espacios) pasa la pantalla y lo ve el SERVIDOR: lo clava en la fila 2.
    const spaced = `${a1.imei.slice(0, 5)} ${a1.imei.slice(5, 10)} ${a1.imei.slice(10)}`;
    await imeis.nth(1).fill(spaced);
    const cardB = await addProduct(admin, rx.newB.name);
    await fillUnit(cardB, 0, { ...b1, condition: "Nuevo" });
    await toReview(admin);
    const lineA = admin.getByRole("list", { name: "Detalle por artículo" }).getByRole("listitem").filter({ hasText: rx.newA.name });
    await expect(lineA).toContainText("Equipo 2: este IMEI está repetido en la misma recepción.");
    await expect(lineA).not.toContainText("Equipo 1:");
    await expect(admin.getByRole("alert").filter({ hasText: "Hay artículos con problemas" })).toBeVisible();
    await expect(confirmButton(admin)).toBeDisabled();
    await expect(admin.getByLabel(/Revisé los datos y quiero registrar/)).toHaveCount(0); // no se puede ni marcar
    // El otro artículo sí está bien (el IMEI de la otra empresa no cuenta como repetido).
    const lineB = admin.getByRole("list", { name: "Detalle por artículo" }).getByRole("listitem").filter({ hasText: rx.newB.name });
    await expect(lineB).not.toContainText(/Repetidos en esta recepción|Ya registrados en el sistema/);
    await shot(admin, "s3-step3-repetido-servidor");
    // «Corregir» lleva el cursor al campo exacto del equipo 2.
    await lineA.getByRole("button", { name: /Corregir/ }).click();
    await expect(admin.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
    await expect(imeis.nth(1)).toHaveAttribute("aria-invalid", "true");
    await expect(imeis.nth(1)).toBeFocused();
    await expect(cardA.getByText(/Equipo 2: este IMEI está repetido en la misma recepción\./)).toBeVisible();
    await expect(imeis.nth(0)).not.toHaveAttribute("aria-invalid", "true");

    // (c) Un equipo que YA está en el stock de esta empresa: serie e IMEI del seminuevo del escenario 2.
    await imeis.nth(1).fill(a2.imei);
    const cardU = await addProduct(admin, rx.used.name);
    await fillUnit(cardU, 0, { serial: used.serial, imei: imei(33) });
    await toReview(admin);
    const lineU = admin.getByRole("list", { name: "Detalle por artículo" }).getByRole("listitem").filter({ hasText: rx.used.name });
    await expect(lineU).toContainText("Equipo 1: ese número de serie ya está registrado para este producto.");
    await expect(confirmButton(admin)).toBeDisabled();
    // Ni el nombre ni ningún dato de la otra empresa aparece en lo que ve esta persona.
    const visible = await admin.locator("main").innerText();
    expect(visible).not.toContain(rx.foreign.name);
    expect(visible).not.toContain("AJENO");
    expect(visible).not.toContain("QAE otra");
    await shot(admin, "s3-step3-ya-en-stock");

    // (d) Se quita el seminuevo repetido y se recibe lo bueno: 3 equipos, 3 etiquetas, 3 identidades distintas.
    await lineU.getByRole("button", { name: /Corregir/ }).click();
    await expect(admin.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
    await admin.getByRole("button", { name: `Quitar ${rx.used.name} de la lista` }).click();
    await toReview(admin);
    await expect(summary(admin)).toContainText("3 equipos identificados");
    await expect(summary(admin)).toContainText("3 etiquetas");
    await expect(lineA).not.toContainText(/Repetidos en esta recepción|Ya registrados en el sistema/);
    await accept(admin);
    // El cuerpo de la vista previa de la API tampoco nombra nada ajeno (misma petición, vista como API).
    const preview = await jsonCall<Record<string, unknown>>(adminContext.request, "/admin/inventory/receipts/preview/", {
      branch: base.branch, lines: [
        { kind: "units", product: rx.newA.id, units: [{ serial: a1.serial, imei: a1.imei }, { serial: a2.serial, imei: a2.imei }] },
        { kind: "units", product: rx.newB.id, units: [{ serial: b1.serial, imei: b1.imei }] },
      ],
    });
    expect(preview.status).toBe(200);
    const text = JSON.stringify(preview.body);
    expect(text).not.toContain(rx.foreign.name);
    expect(text).not.toContain("AJENO");
    expect(JSON.stringify((preview.body as { ok: boolean }).ok)).toBe("true");
    expect(estado(rx.newA.id).units).toHaveLength(0);

    const answered = waitConfirmResponse(admin);
    await confirmButton(admin).click();
    expect((await answered).status()).toBe(201);
    await expect(admin.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    await expect(admin.getByRole("region", { name: "Siguiente paso" }).getByText(/Etiquetas listas para imprimir/)).toBeVisible();

    const stateA = estado(rx.newA.id);
    const stateB = estado(rx.newB.id);
    const all = [...stateA.units, ...stateB.units];
    expect(all).toHaveLength(3);
    expect(new Set(all.map((u) => u.serial)).size).toBe(3);
    expect(new Set(all.map((u) => u.imei)).size).toBe(3);
    expect(all.every((u) => u.condition === "new" && u.status === "available")).toBe(true);
    expect(stateA.movements.map((m) => [m.type, m.quantity, m.before, m.after])).toEqual([["purchase_entry", 2, 0, 2]]);
    expect(stateB.movements.map((m) => [m.type, m.quantity, m.before, m.after])).toEqual([["purchase_entry", 1, 0, 1]]);
    expect(stateA.batches.map((b) => b.copies)).toEqual([2]);
    expect(stateB.batches.map((b) => b.copies)).toEqual([1]);
    expect(stateA.stock[String(base.branch)]).toBe(2);
    expect(stateB.stock[String(base.branch)]).toBe(1);
    // Esta empresa pudo recibir el IMEI que otra empresa tiene (la unicidad es por empresa) y la otra no se enteró.
    expect(otraEmpresa()).toEqual(shared.otherBefore);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("4 · accesorios: se suma al stock existente, no se crea ningún producto y el costo exige permiso", async ({ browser }) => {
    test.setTimeout(300_000);
    const before = ficha(base.cable);
    expect(before.stock).toBe(100);
    const productsBefore = productos();
    const adjust = await as(browser, "adjust");
    const page = adjust.page;
    const doc = `GR-${TAG}-050`;

    await openReceive(page);
    await setOrigin(page, { branch: base.branchName, supplier: rx.supplier.name, doc });
    await toItems(page);
    const card = await addProduct(page, base.cableName);
    await expect(card.getByText("Por cantidad")).toBeVisible();
    await card.getByLabel("Cantidad", { exact: true }).fill("50");
    // Sin «Ver costos y márgenes» el costo ni se pide.
    await expect(page.getByLabel(/Costo por unidad/)).toHaveCount(0);
    await shot(page, "s4-accesorio-sin-costos");
    await toReview(page);
    await expect(summary(page)).toContainText("50 unidades por cantidad");
    await expect(summary(page)).toContainText("50 etiquetas");
    await accept(page);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    expect((await answered).status()).toBe(201);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("region", { name: "Siguiente paso" }).getByText(/Etiquetas listas para imprimir/)).toBeVisible();

    const after = ficha(base.cable);
    expect(after.stock).toBe(150);
    expect(after.inventory).toBe(150);
    expect(after.movements.at(-1)).toMatchObject({ type: "purchase_entry", quantity: 50, before: 100, after: 150 });
    expect(after.batches.filter((b) => b.copies === 50)).toHaveLength(1);
    expect(after.batches).toHaveLength(before.batches.length + 1);
    expect(productos()).toEqual(productsBefore);
    expect(estado(base.cable).movements.at(-1)!.reason).toContain(doc);

    // El servidor lo exige también: un costo de quien no puede verlos es un 400 llano, no un 500 ni un silencio.
    const refused = await jsonCall<{ code: string; detail: string }>(page.context().request, "/admin/inventory/receipts/preview/", {
      branch: base.branch, lines: [{ kind: "quantity", product: base.ready, quantity: 1, unit_cost: "12.50" }],
    });
    expect(refused.status).toBe(400);
    expect(refused.body.code).toBe("costs_forbidden");

    // Con «Ver costos y márgenes» el costo aparece, se valida y se guarda.
    const costs = await as(browser, "costs");
    await openReceive(costs.page);
    await setOrigin(costs.page, { branch: base.branchName });
    await toItems(costs.page);
    const cardC = await addProduct(costs.page, base.readyName);
    await cardC.getByLabel("Cantidad", { exact: true }).fill("3");
    const cost = cardC.getByLabel(/Costo por unidad/);
    await expect(cost).toBeVisible();
    await cost.fill("12,5");
    await button(costs.page, "Siguiente: revisar").click();
    await expect(cardC.getByText(/Escribe el costo con hasta 2 decimales/)).toBeVisible();
    await expect(costs.page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
    await shot(costs.page, "s4-costo-invalido");
    await cost.fill("12.50");
    await toReview(costs.page);
    await accept(costs.page);
    const answered2 = waitConfirmResponse(costs.page);
    await confirmButton(costs.page).click();
    expect((await answered2).status()).toBe(201);
    await expect(costs.page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    const ready = estado(base.ready);
    expect(ready.movements.at(-1)).toMatchObject({ type: "purchase_entry", quantity: 3, before: 5, after: 8 });
    expect(Number(ready.movements.at(-1)!.unit_cost)).toBeCloseTo(12.5, 4);
    expect(ready.stock[String(base.branch)]).toBe(8);
    await shot(costs.page, "s4-resultado-con-costos");
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("5 · una respuesta perdida no duplica nada: el reintento reutiliza la clave; cambiar el contenido la cambia", async () => {
    test.setTimeout(300_000);

    // (a) El servidor PROCESA la recepción y la respuesta se pierde en el camino.
    const dev = { serial: serial("ID"), imei: imei(51) };
    const receiptsBefore = recibos();
    const watch = watchConfirm(admin);
    let lost: { receipt_id: string } | null = null;
    let armed = true;
    await admin.route(/receipts\/confirm/, async (route) => {
      if (armed && route.request().method() === "POST" && route.request().redirectedFrom() === null) {
        armed = false;
        const reached = await route.fetch(); // llega al servidor y se guarda
        lost = await reached.json();
        await route.abort("failed"); // ...pero el navegador nunca ve la respuesta
      } else {
        await route.continue();
      }
    });
    await openReceive(admin);
    await setOrigin(admin, { branch: base.branchName, doc: `FAC-${TAG}-005` });
    await toItems(admin);
    const card = await addProduct(admin, rx.idem.name);
    await fillUnit(card, 0, { ...dev, condition: "Nuevo" });
    await toReview(admin);
    await accept(admin);
    await confirmButton(admin).click();
    await expect(admin.getByText(/No llegó la respuesta del servidor, así que no sabemos si la recepción se guardó/)).toBeVisible({ timeout: 30_000 });
    await expect(admin.getByRole("heading", { name: "Revisa antes de registrar" })).toBeVisible(); // sigue en la revisión
    await shot(admin, "s5-respuesta-perdida");
    expect(lost, "el servidor no llegó a procesarla").not.toBeNull();
    expect(estado(rx.idem.id).units).toHaveLength(1); // ya está guardada, aunque la pantalla no lo sepa
    expect(recibos()).toBe(receiptsBefore + 1);

    // Reintento desde la interfaz: MISMA clave, y la respuesta es la guardada (replayed), no una segunda recepción.
    await expect(confirmButton(admin)).toBeEnabled();
    const answered = waitConfirmResponse(admin);
    await confirmButton(admin).click();
    const reply = await answered;
    expect(reply.status()).toBe(200);
    const replayed = await reply.json();
    expect(replayed.replayed).toBe(true);
    expect(replayed.receipt_id).toBe(lost!.receipt_id);
    await expect(admin.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    await expect(admin.getByText(/ya estaba registrada: no se repitió/)).toBeVisible();
    await shot(admin, "s5-reintento-repetido");
    watch.stop();
    expect(watch.seen).toHaveLength(2);
    expect(watch.seen[0].key).toMatch(/^rcv-/);
    expect(watch.seen[1].key).toBe(watch.seen[0].key);
    expect(watch.seen[1].body).toEqual(watch.seen[0].body);
    shared.idemKey = watch.seen[0].key;
    shared.confirmBody = watch.seen[0].body;

    const state = estado(rx.idem.id);
    expect(state.units).toHaveLength(1);
    expect(state.movements).toHaveLength(1);
    expect(state.batches.map((b) => b.copies)).toEqual([1]);
    expect(state.stock[String(base.branch)]).toBe(1);
    expect(recibos()).toBe(receiptsBefore + 1);

    // La misma clave con el MISMO cuerpo, por la API: la misma respuesta; con OTRO cuerpo: 409 llano y nada nuevo.
    const api = adminContext.request;
    const again = await jsonCall<{ replayed?: boolean; receipt_id: string }>(api, "/admin/inventory/receipts/confirm/", shared.confirmBody, { "Idempotency-Key": shared.idemKey });
    expect(again.status).toBe(200);
    expect(again.body.replayed).toBe(true);
    expect(again.body.receipt_id).toBe(lost!.receipt_id);
    const other = JSON.parse(JSON.stringify(shared.confirmBody)) as { lines: { units: { serial: string }[] }[] };
    other.lines[0].units[0].serial = serial("ID", 9);
    const conflict = await jsonCall<{ code: string }>(api, "/admin/inventory/receipts/confirm/", other, { "Idempotency-Key": shared.idemKey });
    expect(conflict.status).toBe(409);
    expect(conflict.body.code).toBe("idempotency_conflict");
    expect(estado(rx.idem.id).units).toHaveLength(1);
    expect(recibos()).toBe(receiptsBefore + 1);
    await admin.unroute(/receipts\/confirm/);

    // (b) La petición muere ANTES de llegar al servidor y la persona cambia lo escrito: es otro intento, con otra clave.
    const dev2 = { serial: serial("CL", 1), imei: imei(52) };
    const dev3 = { serial: serial("CL", 2), imei: dev2.imei };
    const receipts2 = recibos();
    const watch2 = watchConfirm(admin);
    let cut = true;
    await admin.route(/receipts\/confirm/, async (route) => {
      if (cut && route.request().method() === "POST" && route.request().redirectedFrom() === null) { cut = false; await route.abort("failed"); }
      else await route.continue();
    });
    await openReceive(admin);
    await setOrigin(admin, { branch: base.branchName });
    await toItems(admin);
    const card2 = await addProduct(admin, rx.idem2.name);
    await fillUnit(card2, 0, { ...dev2, condition: "Nuevo" });
    await toReview(admin);
    await accept(admin);
    await confirmButton(admin).click();
    await expect(admin.getByText(/No llegó la respuesta del servidor/)).toBeVisible({ timeout: 30_000 });
    expect(estado(rx.idem2.id).units).toHaveLength(0); // nada llegó
    await button(admin, "Atrás").click();
    await fillUnit(admin.getByRole("article", { name: rx.idem2.name, exact: true }), 0, dev3);
    await toReview(admin);
    await expect(admin.getByLabel(/Revisé los datos y quiero registrar/)).not.toBeChecked(); // lo revisado ya no vale
    await accept(admin);
    const answered2 = waitConfirmResponse(admin);
    await confirmButton(admin).click();
    expect((await answered2).status()).toBe(201);
    await expect(admin.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    watch2.stop();
    expect(watch2.seen).toHaveLength(2);
    expect(watch2.seen[1].key).toMatch(/^rcv-/);
    expect(watch2.seen[1].key).not.toBe(watch2.seen[0].key);
    expect(watch2.seen[1].body).not.toEqual(watch2.seen[0].body);
    const state2 = estado(rx.idem2.id);
    expect(state2.units.map((u) => u.serial)).toEqual([dev3.serial]);
    expect(state2.movements).toHaveLength(1);
    expect(recibos()).toBe(receipts2 + 1);
    await admin.unroute(/receipts\/confirm/);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("6 · errores y permisos: 403 y 404 en español llano, y una recepción inválida no recibe nada", async ({ browser }) => {
    test.setTimeout(360_000);
    const adjust = await as(browser, "adjust");
    const page = adjust.page;
    const receiptsBefore = recibos();

    // (a) Se le quita «ajustar inventario» con el asistente ya abierto: el servidor dice que no, y se dice sin jerga.
    await openReceive(page);
    await setOrigin(page, { branch: base.branchName });
    await toItems(page);
    const card = await addProduct(page, rx.dup.name);
    await fillUnit(card, 0, { serial: serial("PM"), imei: imei(61), condition: "Nuevo" });
    await toReview(page);
    await accept(page);
    setAdjust(rx.roleAdjust, false);
    try {
      await confirmButton(page).click();
      const denied = page.getByText("No tienes permisos sobre el inventario.");
      await expect(denied).toBeVisible({ timeout: 30_000 });
      const visible = await page.locator("main").innerText();
      expect(visible).not.toMatch(/\b403\b|Forbidden|undefined|\[object/i);
      await shot(page, "s6-403-confirmar");
      expect(estado(rx.dup.id).units).toHaveLength(0);
      // Y la revisión también lo dice (con «Reintentar»).
      await button(page, "Atrás").click();
      await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
      await button(page, "Siguiente: revisar").click();
      await expect(page.getByText("No tienes permisos sobre el inventario.")).toBeVisible({ timeout: 30_000 });
      await expect(button(page, "Reintentar")).toBeVisible();
      await shot(page, "s6-403-revisar");
    } finally {
      setAdjust(rx.roleAdjust, true);
    }
    await button(page, "Reintentar").click();
    await expect(page.getByRole("heading", { name: "Revisa antes de registrar" })).toBeVisible();
    await expect(summary(page)).toContainText("1 equipo identificado", { timeout: 30_000 }); // con el permiso de vuelta, funciona

    // (b) Un producto que no existe para esta empresa: 404 manejado, con salida a «ese artículo».
    await openReceive(admin);
    await toItems(admin);
    const eph = await addProduct(admin, rx.ephemeral.name);
    await expect(eph).toBeVisible();
    deleteProduct(rx.ephemeral.id);
    await toReview(admin);
    await expect(admin.getByText("Producto no encontrado.")).toBeVisible({ timeout: 30_000 });
    await expect(button(admin, "Ir a ese artículo")).toBeVisible();
    await shot(admin, "s6-404-producto");
    await button(admin, "Ir a ese artículo").click();
    await expect(admin.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
    // El enlace con un producto de OTRA empresa: no se carga, se explica y la lista queda vacía.
    await admin.goto(`/admin/inventory/receive?product=${rx.foreign.product}`, { waitUntil: "networkidle" });
    await expect(admin.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible({ timeout: 60_000 });
    await expect(admin.getByText(/No se pudo cargar el artículo|no encontrado|No encontrado/i).first()).toBeVisible({ timeout: 30_000 });
    await expect(admin.getByRole("article")).toHaveCount(0);
    expect(await admin.locator("main").innerText()).not.toContain(rx.foreign.name);
    await shot(admin, "s6-404-enlace-ajeno");

    // (c) Una sucursal fuera de su alcance es un 404 (como una que no existe); la de su alcance, un 200.
    const scoped = await as(browser, "scoped");
    await openReceive(scoped.page);
    const options = await scoped.page.locator("#receive-branch option").allTextContents();
    expect(options).toContain(base.branchName);
    expect(options.join("|")).not.toContain(rx.branch2.name);
    const one = { kind: "quantity", product: base.cable, quantity: 1 };
    const outside = await jsonCall<{ detail: string; code: string }>(scoped.context.request, "/admin/inventory/receipts/preview/", { branch: rx.branch2.id, lines: [one] });
    expect(outside.status, JSON.stringify(outside.body)).toBe(404);
    expect(outside.body.detail).toBe("Sucursal no encontrada o sin acceso.");
    const confirmOutside = await jsonCall(scoped.context.request, "/admin/inventory/receipts/confirm/", { branch: rx.branch2.id, lines: [one] }, { "Idempotency-Key": `rcv-${TAG}-fuera` });
    expect(confirmOutside.status).toBe(404);
    const inside = await jsonCall<{ ok: boolean }>(scoped.context.request, "/admin/inventory/receipts/preview/", { branch: base.branch, lines: [one] });
    expect(inside.status).toBe(200);
    expect(inside.body.ok).toBe(true);
    const control = await jsonCall<{ ok: boolean }>(adminContext.request, "/admin/inventory/receipts/preview/", { branch: rx.branch2.id, lines: [one] });
    expect(control.status).toBe(200); // quien SÍ tiene todas las sucursales la ve
    expect(control.body.ok).toBe(true);

    // (d) `invalid_lines`: la revisión estaba bien, pero otra persona recibió ese equipo un instante antes.
    const racer = { serial: serial("RC"), imei: imei(63) };
    await openReceive(admin);
    await setOrigin(admin, { branch: base.branchName });
    await toItems(admin);
    const cardR = await addProduct(admin, rx.race.name);
    await fillUnit(cardR, 0, { ...racer, condition: "Nuevo" });
    await toReview(admin);
    await accept(admin);
    const sneaked = await jsonCall(adminContext.request, "/admin/inventory/receipts/confirm/", {
      branch: base.branch, lines: [{ kind: "units", product: rx.race.id, units: [{ serial: racer.serial, imei: racer.imei, condition: "new" }] }],
    }, { "Idempotency-Key": `rcv-${TAG}-carrera` });
    expect(sneaked.status).toBe(201);
    const answered = waitConfirmResponse(admin);
    await confirmButton(admin).click();
    const reply = await answered;
    expect(reply.status()).toBe(400);
    expect((await reply.json()).code).toBe("invalid_lines");
    await expect(admin.getByText(/No se registró nada: hay artículos con problemas/)).toBeVisible({ timeout: 30_000 });
    const lineR = admin.getByRole("list", { name: "Detalle por artículo" }).getByRole("listitem").filter({ hasText: rx.race.name });
    await expect(lineR).toContainText("ese número de serie ya está registrado para este producto.");
    await expect(confirmButton(admin)).toBeDisabled();
    await shot(admin, "s6-invalid-lines");
    const raced = estado(rx.race.id);
    expect(raced.units).toHaveLength(1); // sólo el de la otra persona
    expect(raced.movements).toHaveLength(1);
    expect(recibos()).toBe(receiptsBefore + 1);
    // Y quien no tiene un cuerpo válido tampoco recibe nada: una lista vacía es un 400 llano.
    const empty = await jsonCall<{ detail: string }>(adminContext.request, "/admin/inventory/receipts/preview/", { branch: base.branch, lines: [] });
    expect(empty.status).toBe(400);
    expect(empty.body.detail).toBe("Indica al menos una línea.");
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("7 · aislamiento entre empresas: lo de A no se ve, no se toca y no se repite desde B", async ({ browser }) => {
    test.setTimeout(240_000);
    const outsider = await as(browser, "other");
    const api = outsider.context.request;
    const cableBefore = estado(base.cable);
    const unitsBefore = estado(rx.used.id);
    const receiptsBefore = recibos();
    const mine = { branch: base.other.branch, product: base.other.product };
    const theirs = { branch: base.branch, product: base.cable, supplier: rx.supplier.id };
    const q = (product: number) => ({ kind: "quantity", product, quantity: 1 });
    const leaks = (reply: { body: unknown }, what: string) => {
      const text = JSON.stringify(reply.body ?? "");
      for (const secret of [base.cableName, base.branchName, rx.supplier.name, rx.used.name, TAG + "S", String(theirs.product)]) {
        if (secret === String(theirs.product)) continue; // un número suelto puede coincidir con cualquier otro dato
        expect(text, `${what}: el cuerpo trae «${secret}»`).not.toContain(secret);
      }
    };
    const idem = (n: string) => ({ "Idempotency-Key": `rcv-${TAG}-${n}` });

    // Control positivo: la persona de B recibe (vista previa) lo SUYO sin problema.
    const own = await jsonCall<{ ok: boolean }>(api, "/admin/inventory/receipts/preview/", { branch: mine.branch, lines: [q(mine.product)] });
    expect(own.status).toBe(200);
    expect(own.body.ok).toBe(true);
    // Y la de A, lo de A.
    const legit = await jsonCall<{ ok: boolean }>(adminContext.request, "/admin/inventory/receipts/preview/", { branch: theirs.branch, lines: [q(theirs.product)] });
    expect(legit.status).toBe(200);
    expect(legit.body.ok).toBe(true);

    const attempts: [string, Record<string, unknown>][] = [
      ["sucursal de A", { branch: theirs.branch, lines: [q(mine.product)] }],
      ["producto de A", { branch: mine.branch, lines: [q(theirs.product)] }],
      ["producto de A en la sucursal por omisión", { lines: [q(theirs.product)] }],
      ["proveedor de A", { branch: mine.branch, supplier: theirs.supplier, lines: [q(mine.product)] }],
      ["equipo de A", { branch: mine.branch, lines: [{ kind: "units", product: rx.used.id, units: [{ serial: "X1", imei: imei(71) }] }] }],
    ];
    for (const [what, body] of attempts) {
      const preview = await jsonCall(api, "/admin/inventory/receipts/preview/", body);
      expect(preview.status, `vista previa: ${what}`).toBe(404);
      leaks(preview, `vista previa: ${what}`);
      const confirmed = await jsonCall(api, "/admin/inventory/receipts/confirm/", body, idem(what.replace(/\W+/g, "-")));
      expect(confirmed.status, `confirmar: ${what}`).toBe(404);
      leaks(confirmed, `confirmar: ${what}`);
    }
    // Repetir desde B la clave y el cuerpo de una recepción de A no devuelve esa recepción: es un 404 como los demás.
    const replay = await jsonCall(api, "/admin/inventory/receipts/confirm/", shared.confirmBody, { "Idempotency-Key": shared.idemKey! });
    expect(replay.status).toBe(404);
    expect(JSON.stringify(replay.body)).not.toContain("receipt_id");
    leaks(replay, "repetir una recepción de A");

    // Nada cambió: ni el stock, ni los equipos, ni el Kardex de A; ni las recepciones registradas.
    expect(estado(base.cable)).toEqual(cableBefore);
    expect(estado(rx.used.id)).toEqual(unitsBefore);
    expect(recibos()).toBe(receiptsBefore);
    expect(otraEmpresa()).toEqual(shared.otherBefore); // y B quedó exactamente como estaba: lo de A nunca la tocó
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("8 · conciliación: la existencia por sucursal es la suma del Kardex y los equipos son las entradas", async () => {
    const result = conciliar();
    expect(result.products).toBeGreaterThanOrEqual(10);
    expect(result.mismatches, JSON.stringify(result.mismatches)).toEqual([]);
    expect(result.inventory_field, JSON.stringify(result.inventory_field)).toEqual([]);
    expect(result.serialized.length).toBeGreaterThanOrEqual(5);
    for (const row of result.serialized) {
      expect(row.units, `${row.product}: equipos vs entradas`).toBe(row.entries);
      expect(row.available, `${row.product}: disponibles`).toBe(row.units);
    }
    const counts = contar();
    expect(counts.units).toBeGreaterThanOrEqual(7); // 1 seminuevo + 3 nuevos + 1 de reintento + 1 de clave nueva + 1 de la carrera
    expect(counts.unit_imeis).toBe(counts.units);
    // Ninguna recepción creó productos: sólo los que sembró la prueba (más el de la otra empresa, que no cuenta aquí).
    expect(productos().all - shared.productsBefore!.all).toBe(0 - 1); // el «efímero» se borró a propósito
    expect(otraEmpresa()).toEqual(shared.otherBefore);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("9 · el asistente a 390 px y a 1280 px: cuatro pasos sin desborde, teclado y foco, sin datos en el navegador", async () => {
    test.setTimeout(420_000);
    const used = shared.usedUnit!;
    const notes: string[] = [];

    /** Una descripción corta de dónde está el foco. */
    const focusOf = (page: Page) => page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return "BODY";
      const label = el.getAttribute("aria-label") ?? (el as HTMLInputElement).labels?.[0]?.textContent ?? el.textContent ?? "";
      return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}[${label.trim().slice(0, 40)}]`;
    });

    async function flow(page: Page, product: Named, size: { width: number; height: number }, where: string) {
      await viewport(page, size);
      const a = { serial: serial(`U${size.width}`, 1), imei: imei(size.width + 1) };
      const b = { serial: serial(`U${size.width}`, 2), imei: imei(size.width + 2) };

      // Paso 1 · Procedencia, con el teclado: sucursal → proveedor → documento → observaciones → «Siguiente».
      await openReceive(page);
      await expectNoOverflow(page, `${where}: paso 1`);
      await shot(page, "paso1-procedencia");
      await page.locator("#receive-branch").focus();
      for (const id of ["#receive-supplier", "#receive-doc", "#receive-notes"]) {
        await page.keyboard.press("Tab");
        await expect(page.locator(id), `${where}: el Tab no llegó a ${id}`).toBeFocused();
      }
      await page.locator("#receive-doc").fill(`FAC-${TAG}-${size.width}`);
      await page.locator("#receive-notes").focus();
      await page.keyboard.press("Tab");
      await expect(button(page, "Siguiente: artículos")).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
      notes.push(`${where}: foco tras pasar al paso 2 → ${await focusOf(page)}`);
      await expectNoOverflow(page, `${where}: paso 2 vacío`);
      await shot(page, "paso2-articulos-vacio");

      // Paso 2 · el buscador con el teclado: escribir, flecha abajo, Enter.
      await page.locator("#receive-product").focus();
      await page.keyboard.type(product.name);
      await expect(page.getByRole("option").filter({ hasText: product.name }).first()).toBeVisible({ timeout: 30_000 });
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      const card = page.getByRole("article", { name: product.name, exact: true });
      await expect(card).toBeVisible();
      const serials = card.locator('[data-field="serial_number"]');
      const imeis = card.locator('[data-field="imei"]');
      await expect(serials.first(), `${where}: el foco debía caer en el primer número de serie`).toBeFocused();
      // Escáner: serie, Enter, IMEI, Enter → aparece el equipo siguiente con el foco en su serie.
      await page.keyboard.type(a.serial);
      await page.keyboard.press("Enter");
      await expect(imeis.first()).toBeFocused();
      await page.keyboard.type(a.imei);
      await page.keyboard.press("Enter");
      await expect(serials.nth(1)).toBeFocused();
      await page.keyboard.type(b.serial);
      await page.keyboard.press("Enter");
      await expect(imeis.nth(1)).toBeFocused();
      await page.keyboard.type(b.imei);
      await page.keyboard.press("Enter");
      await expect(serials.nth(2)).toBeFocused(); // una fila vacía lista para el siguiente equipo (se ignora si queda vacía)
      // Orden de Tab dentro de un equipo: serie → IMEI → IMEI 2 → estado → costo → «Quitar este equipo».
      await serials.first().focus();
      for (const expected of [/IMEI \*/, /IMEI 2/, /Estado del equipo/, /Costo del equipo/, /Quitar este equipo|Quitar el equipo 1/]) {
        await page.keyboard.press("Tab");
        expect(await focusOf(page), `${where}: orden de Tab`).toMatch(expected);
      }
      await expectNoOverflow(page, `${where}: paso 2 lleno`);
      await shot(page, "paso2-articulos-lleno");

      // Error de pantalla: a la segunda fila le falta la serie.
      await serials.nth(1).fill("");
      await button(page, "Siguiente: revisar").click();
      await expect(card.getByText("Falta el número de serie.")).toBeVisible();
      await expect(page.getByRole("alert").filter({ hasText: "Revisa los campos marcados en rojo" })).toBeVisible();
      await expectNoOverflow(page, `${where}: paso 2 con error`);
      await shot(page, "paso2-error-de-pantalla");
      await serials.nth(1).fill(b.serial);

      // Error del servidor: el IMEI de la segunda fila es el del seminuevo que ya está en el stock.
      await imeis.nth(1).fill(used.imei);
      await toReview(page);
      const line = page.getByRole("list", { name: "Detalle por artículo" }).getByRole("listitem").filter({ hasText: product.name });
      await expect(line).toContainText("Equipo 2: ese IMEI ya está registrado en otro equipo de la empresa.");
      await expect(confirmButton(page)).toBeDisabled();
      await expectNoOverflow(page, `${where}: paso 3 con error`);
      await shot(page, "paso3-error-del-servidor");
      await line.getByRole("button", { name: /Corregir/ }).click();
      await expect(imeis.nth(1)).toBeFocused();
      await expect(imeis.nth(1)).toHaveAttribute("aria-invalid", "true");
      await imeis.nth(1).fill(b.imei);

      // Paso 3 · revisar y confirmar con el teclado: casilla (Espacio) → Atrás → Confirmar (Enter).
      await toReview(page);
      notes.push(`${where}: foco tras pasar al paso 3 → ${await focusOf(page)}`);
      await expect(summary(page)).toContainText("2 equipos identificados");
      await expect(summary(page)).toContainText("2 etiquetas");
      await expectNoOverflow(page, `${where}: paso 3`);
      await shot(page, "paso3-revisar");
      await page.getByLabel(/Revisé los datos y quiero registrar/).focus();
      await page.keyboard.press("Space");
      await expect(page.getByLabel(/Revisé los datos y quiero registrar/)).toBeChecked();
      await page.keyboard.press("Tab");
      await expect(button(page, "Atrás")).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(confirmButton(page)).toBeFocused();
      const answered = waitConfirmResponse(page);
      await page.keyboard.press("Enter");
      expect((await answered).status()).toBe(201);

      // Paso 4 · resultado.
      await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
      await expect(page.getByRole("region", { name: "Siguiente paso" }).getByText(/Etiquetas listas para imprimir/)).toBeVisible();
      notes.push(`${where}: foco tras pasar al paso 4 → ${await focusOf(page)}`);
      await expectNoOverflow(page, `${where}: paso 4`);
      await shot(page, "paso4-resultado");
      const done = estado(product.id);
      expect(done.units.map((u) => u.serial)).toEqual([a.serial, b.serial]);
      expect(done.batches.map((x) => x.copies)).toEqual([2]);
      expect(done.stock[String(base.branch)]).toBe(2);

      // Nada sensible en el navegador: ni series, ni IMEI, ni la marca, ni la clave de idempotencia.
      const stored = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }));
      const dump = JSON.stringify(stored);
      for (const secret of [a.serial, b.serial, a.imei, b.imei, used.imei, TAG, "rcv-", "Idempotency"]) {
        expect(dump, `${where}: el almacenamiento del navegador trae «${secret}»`).not.toContain(secret);
      }
      notes.push(`${where}: claves en localStorage=${Object.keys(stored.local).join(",") || "—"}; sessionStorage=${Object.keys(stored.session).join(",") || "—"}`);
    }

    await flow(admin, rx.ux1, MOBILE, "390 px");
    await flow(admin, rx.ux2, DESKTOP, "1280 px");
    test.info().annotations.push({ type: "foco", description: notes.join(" || ") });
    console.warn(`INV-OPS-01 · notas de teclado y foco:\n  ${notes.join("\n  ")}`);
  });
});
