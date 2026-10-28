import { test, expect, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import {
  TAG, OTHER_USER, OTHER_PASSWORD, imei, sembrar, contar, conciliar, limpiar, signIn, call, apiBase, ficha, adminProducts, ajenos,
  type Seed, type Counts,
} from "./inventory-first-support";

/**
 * INV-QA-E — el recorrido de inventario primero, de punta a punta y SIN MOCKS:
 *
 *   crear/reutilizar catálogo -> recibir mercadería -> etiquetas pendientes -> completar la ficha -> verlo en la tienda
 *
 * Lo que se prueba con la interfaz real (Chromium) y la API real; la base se lee con `manage.py shell` sólo para
 * contar. Datos SINTÉTICOS: IMEI inventados con Luhn válido, nombres con una marca única por corrida (`TAG`), y al final
 * `limpiar()` borra lo marcado. Los escenarios son seriales: cada uno deja lo que el siguiente necesita.
 */

const FIRST = 12;
/** 12 equipos de 3 modelos: 2 modelos nuevos y 1 que ya existe; una variante reutilizada y cuatro por crear. */
const DEVICES = [
  // A · iPhone 13: el modelo y la variante 128 GB Negro YA existen.
  { model: `iPhone 13 ${TAG}`, gb: 128, colour: "negro" },
  { model: `iPhone 13 ${TAG}`, gb: 128, colour: "negro" },
  // A · variante nueva de un modelo existente.
  { model: `iPhone 13 ${TAG}`, gb: 256, colour: "azul" },
  { model: `iPhone 13 ${TAG}`, gb: 256, colour: "azul" },
  // B · modelo nuevo, dos variantes.
  { model: `iPhone 14 ${TAG}`, gb: 128, colour: "negro" },
  { model: `iPhone 14 ${TAG}`, gb: 128, colour: "negro" },
  { model: `iPhone 14 ${TAG}`, gb: 128, colour: "negro" },
  { model: `iPhone 14 ${TAG}`, gb: 256, colour: "azul" },
  { model: `iPhone 14 ${TAG}`, gb: 256, colour: "azul" },
  // C · modelo nuevo con una capacidad y un color que el catálogo no tiene.
  { model: `iPhone 15 ${TAG}`, gb: 512, colour: "rojo" },
  { model: `iPhone 15 ${TAG}`, gb: 512, colour: "rojo" },
  { model: `iPhone 15 ${TAG}`, gb: 512, colour: "rojo" },
].map((device, index) => ({ ...device, serial: `${TAG}S${String(index + 1).padStart(2, "0")}`, imei: imei(index + 1) }));

const PASTE = DEVICES.map((d) =>
  [d.model, `${d.gb} GB`, `Color: ${d.colour}`, `NS: ${d.serial}`, `IMEI 1: ${d.imei}`].join("\n"),
).join("\n\n");

/** Un PNG de 1x1 píxel, generado aquí: la foto de prueba no es una imagen de nadie. */
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

let seed: Seed;
let context: BrowserContext;
let page: Page;
/** Lo que cada escenario deja al siguiente. */
const shared: { jobId?: number; before?: Counts; applied?: { label_error?: boolean; label_batches?: unknown[] }; b128?: { id: number; name: string; slug: string } } = {};

/** La tienda no tiene página 404 real para un producto sin publicar: responde 200 con «Producto no encontrado». */
async function expectHiddenPage(visitor: Page, slug: string) {
  await visitor.goto(`/product/${slug}`, { waitUntil: "networkidle" });
  await expect(visitor.getByText("Producto no encontrado", { exact: true }), `/product/${slug} debía estar oculto`).toBeVisible();
  expect(await visitor.content()).not.toContain(TAG);
}

/** El documento no se ensancha más que la ventana (el desborde de una tabla vive en su propio contenedor). */
async function expectNoOverflow(target: Page, what: string) {
  const size = await target.evaluate(() => {
    const inner = window.innerWidth;
    const scroll = document.scrollingElement!.scrollWidth;
    // Sólo si desborda: quién se pasa de la ventana (hojas de un contenedor que ya desplaza no cuentan).
    const offenders = scroll <= inner ? [] : [...document.body.querySelectorAll("*")]
      .filter((el) => el.getBoundingClientRect().right > inner + 1 && !el.parentElement?.closest("[class*='overflow-x-auto'], [class*='overflow-auto']"))
      .slice(0, 6)
      .map((el) => `${el.tagName.toLowerCase()}.${String((el as HTMLElement).className).slice(0, 60)} -> ${Math.round(el.getBoundingClientRect().right)}px`);
    return { scroll, inner, offenders };
  });
  expect(size.scroll, `${what}: la página mide ${size.scroll}px en una ventana de ${size.inner}px; se pasan: ${size.offenders.join(" | ")}`)
    .toBeLessThanOrEqual(size.inner);
}

/** Tab y Shift+Tab no sacan el foco del diálogo. */
async function expectFocusTrapped(target: Page, dialog: Locator, what: string) {
  for (const key of [..."x".repeat(14).split("").map(() => "Tab"), ..."x".repeat(4).split("").map(() => "Shift+Tab")]) {
    await target.keyboard.press(key);
    const inside = await dialog.evaluate((el) => el.contains(document.activeElement));
    expect(inside, `${what}: el foco salió del diálogo con ${key}`).toBe(true);
  }
}

async function openBulk(target: Page) {
  await target.goto("/admin/inventory/bulk", { waitUntil: "networkidle" });
  await expect(target.getByLabel("Texto para reconocer"), "el centro de carga no cargó").toBeVisible({ timeout: 60_000 });
}

async function openBranchField(target: Page, name: string, reason: string) {
  const field = target.getByLabel("Sucursal del borrador");
  await expect(field).toBeVisible();
  if ((await field.evaluate((el) => el.tagName)) === "SELECT") {
    await expect.poll(async () => (await field.locator("option").allTextContents()).includes(name), { timeout: 15_000 }).toBe(true);
    await field.selectOption({ label: name });
  } else {
    await field.fill(name);
  }
  await target.getByLabel("Motivo del borrador").fill(reason);
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  test.setTimeout(240_000);
  seed = sembrar();
  context = await browser.newContext();
  page = await context.newPage();
  await signIn(page);
});

test.afterAll(async () => {
  await context?.close();
  // E2E_KEEP=1 deja los datos de la corrida para mirarlos a mano.
  if (process.env.E2E_KEEP === "1") { console.warn(`INV-QA-E: datos conservados con la marca ${TAG}`); return; }
  const result = limpiar();
  if (process.env.E2E_VERBOSE === "1") console.warn("INV-QA-E: limpieza ->", JSON.stringify(result));
  if (result.left.length) console.warn("INV-QA-E: lo que la limpieza no pudo borrar ->", result.left.join(" | "));
});

test.describe("inventario primero · de la lista pegada a la tienda", () => {
  test("1 · pegar 12 equipos, proponer lo que falta, aprobar y registrar", async () => {
    test.setTimeout(240_000);
    await openBulk(page);
    await page.getByLabel("Línea declarada").selectOption("new");
    await page.getByLabel("Origen declarado").selectOption("own");
    await page.getByLabel("Texto para reconocer").fill(PASTE);
    await page.getByRole("button", { name: "Analizar localmente" }).click();

    const vista = page.getByLabel("Vista previa de lista");
    await expect(vista.getByRole("status").first()).toContainText(`${FIRST} registros candidatos`);

    // Se busca en el catálogo: la variante que existe se preselecciona; las demás quedan sin variante.
    await page.getByRole("button", { name: "Buscar en el catálogo" }).click();
    const propuesta = page.getByRole("region", { name: "Proponer lo que falta" });
    await expect(propuesta).toContainText("fila(s) no tienen variante", { timeout: 30_000 });
    await expect(propuesta).toContainText("10 fila(s) no tienen variante");

    await propuesta.getByRole("button", { name: "Proponer lo que falta" }).click();
    await expect(propuesta.getByRole("status").first()).toContainText("modelo(s)", { timeout: 30_000 });
    await expect(propuesta.getByRole("status").first()).toContainText("2 modelo(s)");
    await expect(propuesta.getByRole("status").first()).toContainText("4 variante(s) por crear");
    // La variante que ya existía no se propone: sus 2 filas ya llegaron con su variante.

    // El panel NO aprueba nada por su cuenta: todo desmarcado y «Crear lo aprobado» sin efecto posible.
    const marcas = propuesta.getByRole("checkbox");
    const total = await marcas.count();
    expect(total).toBeGreaterThanOrEqual(1 + 2 + 4);
    for (let i = 0; i < total; i++) await expect(marcas.nth(i), `la casilla ${i} nació marcada`).not.toBeChecked();
    const crear = propuesta.getByRole("button", { name: "Crear lo aprobado" });
    await expect(crear).toBeDisabled();
    await expect(propuesta).toContainText("Se crearán 0 modelo(s), 0 opción(es) y 0 variante(s).");

    await propuesta.getByLabel("Seleccionar todo").check();
    await expect(propuesta).toContainText("Se crearán 2 modelo(s)");
    await expect(propuesta).toContainText("4 variante(s).");
    const before = contar();
    await crear.click();
    await expect(propuesta.getByRole("status").last()).toContainText("Creado: 2 modelo(s)", { timeout: 40_000 });
    await expect(propuesta).toContainText("Quedan ocultas de la web");

    // Los nuevos productos son variantes ocultas, sin precio, sin un solo equipo y sin un producto por IMEI.
    const after = contar();
    expect(after.products - before.products).toBe(4);
    expect(after.units).toBe(0);

    // Las 12 filas encuentran su variante solas (coincidencia exacta) tras crear.
    const variantes = page.locator('select[aria-label^="Variante del registro"]');
    await expect.poll(async () => (await variantes.evaluateAll((els) => els.map((el) => (el as HTMLSelectElement).value))).filter(Boolean).length,
      { timeout: 40_000 }).toBe(FIRST);

    await openBranchField(page, seed.branchName, `[E2E] ${TAG} compra de prueba`);
    await page.getByRole("button", { name: "Generar borrador" }).click();

    const recepcion = page.getByRole("region", { name: /Estado de cada fila|Recepción/ }).first();
    await page.getByRole("button", { name: "Revisar y registrar" }).click();
    await expect(page.getByRole("table", { name: /Estado de cada fila/ }).getByText("Listo", { exact: true })).toHaveCount(FIRST, { timeout: 40_000 });
    void recepcion;

    // Registrar exige la confirmación explícita y no antes.
    const registrar = page.getByRole("button", { name: new RegExp(`Registrar ${FIRST} equipos`) });
    await expect(registrar).toBeDisabled();
    await page.getByLabel(new RegExp(`Confirmo registrar ${FIRST} equipos en ${seed.branchName}`)).check();
    const applied = page.waitForResponse((res) => /\/admin\/inventory\/units\/import\/\d+\/apply\/?$/.test(res.url()) && res.request().method() === "POST" && (res.status() < 300 || res.status() >= 400 && res.status() !== 308));
    await registrar.click();
    const reply = await applied;
    expect(reply.status(), reply.url()).toBe(200);
    const body = await reply.json();
    shared.jobId = Number(/import\/(\d+)\/apply/.exec(reply.url())?.[1]);
    shared.applied = body;
    expect(body.label_error).toBe(false);
    expect(Array.isArray(body.label_batches) && body.label_batches.length).toBeGreaterThan(0);

    await expect(page.getByText(`${FIRST} equipos registrados.`)).toBeVisible({ timeout: 40_000 });
    const siguiente = page.getByRole("region", { name: "Siguiente paso" });
    await expect(siguiente.getByText(/Etiquetas listas para imprimir/)).toBeVisible();
    await expect(siguiente.getByRole("link", { name: "Etiquetas", exact: true })).toHaveAttribute("href", "/admin/inventory/labels");

    // La base: 12 equipos con 12 IMEI distintos, ningún producto nuevo por IMEI, etiquetas por equipo.
    const done = contar();
    expect(done.units).toBe(FIRST);
    expect(done.unit_imeis).toBe(FIRST);
    expect(done.products).toBe(after.products);
    expect(done.labels).toBe(FIRST);
    expect(done.batches).toBeGreaterThanOrEqual(1);
    expect(done.movement_types.purchase_entry).toBe(5);
    shared.before = done;
  });

  test("1b · repetir el mismo registro no suma equipos, códigos ni lotes", async () => {
    test.setTimeout(240_000);
    const antes = contar();
    const api = page.context().request;

    // (a) El mismo «aplicar» reenviado (doble clic, reintento de red): el servidor no vuelve a registrar.
    const replay = await call<{ label_batches: { id: number }[] }>(api, "POST", `/admin/inventory/units/import/${shared.jobId}/apply/`, {});
    expect(replay.status, JSON.stringify(replay.body).slice(0, 200)).toBe(200);
    // Responde lo mismo (los mismos lotes), sin registrar nada más.
    const ids = (batches: unknown) => (batches as { id: number }[]).map((batch) => batch.id);
    expect(ids(replay.body.label_batches)).toEqual(ids(shared.applied!.label_batches));
    expect(contar()).toEqual(antes);

    // (b) La misma lista otra vez, por la interfaz: cada fila ya está registrada y ninguna se vuelve a aceptar.
    await openBulk(page);
    await page.getByLabel("Línea declarada").selectOption("new");
    await page.getByLabel("Origen declarado").selectOption("own");
    await page.getByLabel("Texto para reconocer").fill(PASTE);
    await page.getByRole("button", { name: "Analizar localmente" }).click();
    await page.getByRole("button", { name: "Buscar en el catálogo" }).click();
    const variantes = page.locator('select[aria-label^="Variante del registro"]');
    await expect.poll(async () => (await variantes.evaluateAll((els) => els.map((el) => (el as HTMLSelectElement).value))).filter(Boolean).length,
      { timeout: 40_000 }).toBe(FIRST);
    // Nada que proponer: todo el catálogo ya existe.
    await expect(page.getByRole("region", { name: "Proponer lo que falta" })).toHaveCount(0);
    await openBranchField(page, seed.branchName, `[E2E] ${TAG} repetida`);
    await page.getByRole("button", { name: "Generar borrador" }).click();
    await page.getByRole("button", { name: "Revisar y registrar" }).click();
    const tabla = page.getByRole("table", { name: /Estado de cada fila/ });
    await expect(tabla).toBeVisible({ timeout: 40_000 });
    await expect(tabla.getByText("Registrado", { exact: true })).toHaveCount(0);
    await expect(tabla.getByText("Listo", { exact: true })).toHaveCount(0);
    await expect(tabla.getByText("Necesita revisión", { exact: true })).toHaveCount(FIRST);
    await expect(page.getByRole("button", { name: /Registrar \d+ equipos/ })).toBeDisabled();
    expect(contar()).toEqual(antes);
  });
  test("2 · «Recibir mercadería» de un accesorio: +50 deja un movimiento, 50 etiquetas y ningún producto nuevo", async () => {
    test.setTimeout(180_000);
    const api = page.context().request;
    const antes = ficha(seed.cable);
    expect(antes.stock).toBe(100);
    expect(antes.batches).toHaveLength(0);
    const productosAntes = contar().products;

    await page.goto("/admin/products", { waitUntil: "networkidle" });
    await page.getByRole("searchbox", { name: "Buscar productos" }).fill(seed.cableName);
    const enlace = page.getByRole("link", { name: `Recibir mercadería de ${seed.cableName}` });
    await expect(enlace).toBeVisible({ timeout: 30_000 });
    await enlace.click();

    // El formulario de Movimientos abre con el producto elegido y «Entrada por compra».
    await expect(page).toHaveURL(new RegExp(`/admin/inventory/movements\\?receive=1&product=${seed.cable}`));
    await expect(page.getByText("Stock de la empresa: 100 u.")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator("#sm-type")).toHaveValue("purchase_entry");

    await page.locator("#sm-qty").fill("50");
    await page.locator("#sm-reason").fill(`[E2E] ${TAG} compra de 50`);
    const posted = page.waitForResponse((res) => /\/admin\/inventory\/movements\/?$/.test(res.url()) && res.request().method() === "POST" && res.status() !== 308);
    await page.getByRole("button", { name: "Registrar movimiento" }).click();
    const reply = await posted;
    expect(reply.status()).toBe(201);
    const body = await reply.json();
    expect(body).toMatchObject({ movement_type: "purchase_entry", quantity: 50, stock_before: 100, stock_after: 150, label_error: false });
    expect(body.label_batch).toMatchObject({ status: "ready", labels_total: 50 });

    const siguiente = page.getByRole("region", { name: "Siguiente paso" });
    await expect(siguiente.getByText(/Etiquetas listas para imprimir/)).toBeVisible({ timeout: 30_000 });

    // Una sola entrada de 50 en el Kardex, un solo lote de exactamente 50 copias aunque ya había 100.
    const tras = ficha(seed.cable);
    expect(tras.stock).toBe(150);
    expect(tras.inventory).toBe(150);
    expect(tras.movements.filter((m) => m.type === "purchase_entry")).toEqual([{ type: "purchase_entry", quantity: 50, before: 100, after: 150 }]);
    expect(tras.batches).toHaveLength(1);
    expect(tras.batches[0].copies).toBe(50);
    expect(contar().products).toBe(productosAntes);

    // El historial de la pantalla lo muestra.
    await expect(page.getByRole("row").filter({ hasText: seed.cableName }).filter({ hasText: "Entrada por compra" }).first()).toBeVisible({ timeout: 30_000 });

    // Correcciones y entradas manuales NO etiquetan: ni lote nuevo ni aviso de etiquetas.
    for (const [type, qty, label] of [["manual_entry", "3", "Entrada manual"], ["correction_positive", "2", "Corrección positiva"]] as const) {
      await page.locator("#sm-type").selectOption(type);
      await page.locator("#sm-qty").fill(qty);
      await page.locator("#sm-reason").fill(`[E2E] ${TAG} ${label}`);
      const manual = page.waitForResponse((res) => /\/admin\/inventory\/movements\/?$/.test(res.url()) && res.request().method() === "POST" && res.status() !== 308);
      await page.getByRole("button", { name: "Registrar movimiento" }).click();
      const answer = await manual;
      expect(answer.status()).toBe(201);
      const data = await answer.json();
      expect(data).toMatchObject({ movement_type: type, quantity: Number(qty), label_batch: null, label_error: false });
      await expect(page.getByText(/Movimiento registrado/).first()).toBeVisible();
      await expect(siguiente.getByText(/Etiquetas listas para imprimir/)).toHaveCount(0);
    }
    const final = ficha(seed.cable);
    expect(final.stock).toBe(155);
    expect(final.batches).toHaveLength(1);
    expect(final.batches[0].copies).toBe(50);
    expect(final.movements.map((m) => m.type)).toEqual(["initial_stock", "purchase_entry", "manual_entry", "correction_positive"]);
    void api;
  });
  test("3 · lo creado nace oculto y sin precio; se completa la ficha, se publica lo marcado y sólo eso sale a la tienda", async ({ browser, playwright }) => {
    test.setTimeout(300_000);
    const admin = page.context().request;
    const rows = await adminProducts(admin, TAG);
    const nuevas = rows.filter((row) => row.is_serialized && /^iPhone/.test(row.name) && row.price === "0.00");
    expect(nuevas, "las 4 variantes que creó la propuesta y la sembrada, todas sin precio").toHaveLength(5);
    for (const row of nuevas) {
      expect(row.is_published_online, row.name).toBe(false);
      expect(row.publication_problems, row.name).toContain("no_price");
    }
    const porNombre = (re: RegExp) => {
      const found = rows.find((row) => re.test(row.name));
      if (!found) throw new Error(`no hay un producto ${re}`);
      return found;
    };
    const b128 = porNombre(/iPhone 14.*128/);
    shared.b128 = { id: b128.id, name: b128.name, slug: b128.slug };
    const c512 = porNombre(/iPhone 15.*512/);
    const idle = rows.find((row) => row.id === seed.idle)!;
    const funda = rows.find((row) => row.id === seed.ready)!;

    // Nada de eso existe para el público: ni en la API ni en las páginas de la tienda.
    const publico = await playwright.request.newContext({ baseURL: apiBase() });
    const anon = await publico.get(`${apiBase()}/products/`);
    expect(anon.status()).toBe(200);
    expect(await anon.text()).not.toContain(TAG);
    const guest = await browser.newContext();
    const visitor = await guest.newPage();
    for (const row of [b128, c512, idle, funda]) {
      await expectHiddenPage(visitor, row.slug);
    }
    await visitor.goto("/product", { waitUntil: "networkidle" });
    expect(await visitor.content()).not.toContain(TAG);

    // Centro de Publicación: la fila dice por qué no sale.
    await page.goto(`/admin/products/publication?search=${TAG}`, { waitUntil: "networkidle" });
    const tabla = page.getByRole("table", { name: "Productos y variantes" });
    const fila = tabla.getByRole("row").filter({ hasText: b128.name });
    await expect(fila).toContainText("Oculto", { timeout: 40_000 });
    await expect(fila).toContainText("no tiene precio de venta");

    // «Completar ficha»: precio, categoría y una foto de prueba.
    await fila.getByRole("button", { name: `Completar ficha de ${b128.name}` }).click();
    const ficha = page.getByRole("dialog");
    await expect(ficha).toBeVisible();
    await expect(ficha.getByTestId("visibility-banner")).toContainText("Oculto", { timeout: 30_000 });
    await expect(ficha.getByTestId("visibility-banner")).toContainText("el cliente no lo ve");
    await expect(ficha).toContainText("Con existencias: entrega inmediata");
    await expect(ficha.getByRole("button", { name: "Publicar en e-commerce" })).toBeDisabled();

    await ficha.getByLabel("Precio de venta (S/)").fill("2599.90");
    await ficha.getByRole("button", { name: "Guardar precio" }).click();
    await expect(ficha.getByRole("status").filter({ hasText: /precio/i }).first()).toBeVisible({ timeout: 30_000 });
    await ficha.getByLabel("Categoría").selectOption({ label: seed.categoryName });
    await ficha.getByRole("button", { name: "Guardar categoría" }).click();
    await expect(ficha).toContainText("Listo: no le falta nada.", { timeout: 30_000 });
    await ficha.getByLabel("Foto").setInputFiles({ name: "qae.png", mimeType: "image/png", buffer: Buffer.from(PNG, "base64") });
    await ficha.getByRole("button", { name: "Subir foto" }).click();
    await expect(ficha.getByRole("img", { name: b128.name })).toBeVisible({ timeout: 30_000 });
    // Completar la ficha no publica nada: sigue oculto hasta que alguien lo decide.
    await expect(ficha.getByTestId("visibility-banner")).toContainText("Oculto");
    // La vista previa jamás trae IMEI, serie ni costo.
    const texto = await ficha.innerText();
    for (const device of DEVICES) expect(texto).not.toContain(device.imei);
    for (const device of DEVICES) expect(texto).not.toContain(device.serial);
    await page.keyboard.press("Escape");
    await expect(ficha).toHaveCount(0);

    // Sin existencias: la vista previa no lo ofrece como entrega inmediata.
    await tabla.getByRole("row").filter({ hasText: idle.name }).getByRole("button", { name: `Completar ficha de ${idle.name}` }).click();
    const sinStock = page.getByRole("dialog");
    await expect(sinStock.getByTestId("visibility-banner")).toContainText("Oculto", { timeout: 30_000 });
    await expect(sinStock).toContainText("Sin existencias: no se ofrece para entrega inmediata");
    await expect(sinStock).not.toContainText("Con existencias");
    await page.keyboard.press("Escape");
    await expect(sinStock).toHaveCount(0);

    // «Publicar listos»: nada viene marcado; se marca lo que se quiere.
    await page.getByRole("button", { name: "Listos para publicar" }).click();
    await expect(page.getByText(/4 productos ocultos y listos para publicar\./)).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Publicar listos (4)" }).click();
    const lista = page.getByRole("dialog");
    await expect(lista).toContainText("0 marcados de 4");
    const casillas = lista.getByRole("checkbox");
    expect(await casillas.count()).toBe(4);
    for (let i = 0; i < 4; i++) await expect(casillas.nth(i)).not.toBeChecked();
    await expect(lista.getByRole("button", { name: "Publicar 0" })).toBeDisabled();
    await lista.getByLabel(`Publicar ${b128.name}`).check();
    await lista.getByLabel(`Publicar ${idle.name}`).check();
    await expect(lista.getByLabel(`Publicar ${funda.name}`)).not.toBeChecked();
    await expect(lista.getByLabel(`Publicar ${seed.cableName}`)).not.toBeChecked();
    await lista.getByRole("button", { name: "Publicar 2" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 30_000 });

    // Sólo lo marcado salió: el hermano sin marcar y las variantes sin precio siguen ocultos.
    const tras = await adminProducts(admin, TAG);
    const estado = (id: number) => tras.find((row) => row.id === id)!.is_published_online;
    expect(estado(b128.id)).toBe(true);
    expect(estado(idle.id)).toBe(true);
    expect(estado(funda.id)).toBe(false);
    expect(estado(c512.id)).toBe(false);
    expect(estado(seed.cable)).toBe(false);

    const lote = await (await publico.get(`${apiBase()}/products/`)).json();
    const nombres: string[] = lote.map((item: { name: string }) => item.name);
    expect(nombres).toEqual(expect.arrayContaining([b128.name, idle.name]));
    expect(nombres).not.toContain(funda.name);
    expect(nombres).not.toContain(c512.name);
    expect(nombres).not.toContain(seed.cableName);
    const nuestros = lote.filter((item: { name: string }) => item.name.includes(TAG));
    expect(nuestros).toHaveLength(2);
    const serializado = JSON.stringify(nuestros);
    expect(serializado).not.toMatch(/imei|serial|cost|supplier|proveedor/i);
    for (const device of DEVICES) { expect(serializado).not.toContain(device.imei); expect(serializado).not.toContain(device.serial); }
    expect(nuestros.find((item: { name: string }) => item.name === b128.name)).toMatchObject({ price: "2599.90", inventory: 3 });
    expect(nuestros.find((item: { name: string }) => item.name === idle.name)).toMatchObject({ inventory: 0 });

    // La tienda de verdad: con existencias vs. agotado (no se ofrece como entrega inmediata).
    await visitor.goto(`/product/${b128.slug}`, { waitUntil: "networkidle" });
    await expect(visitor.getByTestId("product-availability")).toHaveText("Últimas 3 unidades");
    await expect(visitor.getByTestId("product-price")).toContainText("2599.90");
    await visitor.goto(`/product/${idle.slug}`, { waitUntil: "networkidle" });
    await expect(visitor.getByTestId("product-availability")).toHaveText("Agotado");
    expect(await visitor.content()).not.toMatch(/entrega inmediata/i);
    await expectHiddenPage(visitor, funda.slug);
    await guest.close();
    await publico.dispose();
  });
  test("4 · otra empresa no ve, no previsualiza, no publica, no recibe ni mira las etiquetas de la primera", async ({ playwright }) => {
    test.setTimeout(180_000);
    const b128 = shared.b128!;
    const mine = ajenos(b128.id);
    expect(mine.unit).toBeGreaterThan(0);
    expect(mine.batch).toBeGreaterThan(0);
    const before = ficha(b128.id);
    const published = (await adminProducts(page.context().request, b128.name)).find((row) => row.id === b128.id)!.is_published_online;

    const outsider = await playwright.request.newContext();
    const login = await outsider.post(`${apiBase()}/auth/login/`, { data: { username: OTHER_USER, password: OTHER_PASSWORD } });
    expect(login.status(), await login.text()).toBe(200);
    await call(outsider, "GET", "/auth/csrf/");

    const hidden = (reply: { status: number }, what: string) => expect([403, 404], `${what} -> ${reply.status}`).toContain(reply.status);
    hidden(await call(outsider, "GET", `/admin/products/${b128.id}/`), "detalle");
    hidden(await call(outsider, "GET", `/admin/products/${b128.id}/storefront-preview/`), "vista previa");
    hidden(await call(outsider, "POST", `/admin/inventory/units/${mine.unit}/reserve/`, { reason: "[E2E] ajeno" }), "reservar un equipo");
    const units = await call<{ results: unknown[]; count?: number }>(outsider, "GET", `/admin/inventory/units/?search=${DEVICES[0].serial}`);
    expect(units.status).toBe(200);
    expect(units.body.results).toHaveLength(0);
    hidden(await call(outsider, "GET", `/admin/products/${b128.id}/stock-card/`), "tarjeta de stock");

    // La búsqueda de la otra empresa sólo ve lo suyo.
    const found = await call<{ results: { id: number; name: string }[] }>(outsider, "GET", `/admin/products/?search=${TAG}&page_size=100`);
    expect(found.status).toBe(200);
    expect(found.body.results.map((row) => row.name)).toEqual([seed.other.name]);

    // Publicar: ni por id ni como un «publicar todo». Nada cambia.
    const publish = await call(outsider, "POST", "/admin/products/bulk-visibility/", { action: "publish", ids: [b128.id] });
    expect([400, 403, 404], `publicar -> ${publish.status}`).toContain(publish.status);
    // Recibir stock en un producto ajeno: 404, sin movimiento y sin lote.
    const receive = await call(outsider, "POST", "/admin/inventory/movements/", {
      product_id: b128.id, movement_type: "purchase_entry", quantity: 5, reason: "[E2E] ajeno",
    });
    hidden(receive, "recibir");
    const registerUnit = await call(outsider, "POST", "/admin/inventory/text-preview/units/preview/", {
      branch: seed.other.branch, reason: "[E2E] ajeno",
      rows: [{ key: "1", product_id: b128.id, serial_number: `${TAG}AJENA1`, imei: imei(99), imei2: "", condition: "new" }],
    });
    // Ya sea 4xx o una fila con error, nunca una fila lista para registrar.
    if (registerUnit.status < 300) {
      expect(JSON.stringify(registerUnit.body)).not.toMatch(/"action":\s*"create"/);
    } else {
      expect([400, 403, 404]).toContain(registerUnit.status);
    }
    // Etiquetas: la vista previa de lo ajeno no entrega su título ni su código.
    const labels = await call(outsider, "GET", `/admin/inventory/labels/preview/?branch=${seed.other.branch}&products=${b128.id}:1&units=${mine.unit}`);
    expect(JSON.stringify(labels.body)).not.toContain(b128.name);
    const labelsOwnBranch = await call(outsider, "GET", `/admin/inventory/labels/preview/?branch=${mine.branch}&products=${b128.id}:1&units=`);
    hidden(labelsOwnBranch, "etiquetas de una sucursal ajena");

    // El selector de producto: el código de barras de la otra empresa no aparece en la primera, y al revés.
    expect(seed.other.barcode).not.toBe("");
    const mineSearch = await call<{ results: unknown[] }>(page.context().request, "GET", `/admin/products/?search=${encodeURIComponent(seed.other.barcode)}`);
    expect(mineSearch.body.results).toHaveLength(0);
    const theirs = await call<{ results: unknown[] }>(outsider, "GET", `/admin/products/?search=${encodeURIComponent(seed.cableCode)}`);
    expect(theirs.body.results).toHaveLength(0);
    // Con control positivo: el mismo código sí aparece donde corresponde.
    const control = await call<{ results: { name: string }[] }>(page.context().request, "GET", `/admin/products/?search=${encodeURIComponent(seed.cableCode)}`);
    expect(control.body.results.map((row) => row.name)).toContain(seed.cableName);

    // Lo mismo en la pantalla: el selector no ofrece el producto ajeno.
    await page.goto("/admin/inventory/movements", { waitUntil: "networkidle" });
    const picker = page.locator("#sm-product");
    await picker.fill(seed.other.barcode);
    await expect(page.getByText(/Ningún producto coincide/).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("listbox", { name: /Resultados/ })).toHaveCount(0);
    await picker.fill(seed.cableCode);
    await expect(page.getByRole("listbox", { name: /Resultados/ }).getByRole("option").filter({ hasText: seed.cableName })).toBeVisible({ timeout: 20_000 });

    // Nada cambió en la primera empresa.
    const after = ficha(b128.id);
    expect(after.movements).toEqual(before.movements);
    expect(after.batches).toEqual(before.batches);
    expect(ajenos(b128.id).movements).toBe(mine.movements);
    expect((await adminProducts(page.context().request, b128.name)).find((row) => row.id === b128.id)!.is_published_online).toBe(published);
    // Y la otra empresa conserva lo suyo.
    expect((await call<{ results: { name: string }[] }>(outsider, "GET", `/admin/products/?search=${TAG}`)).body.results).toHaveLength(1);
    await outsider.dispose();
  });
  test("5 · conciliación: la existencia por sucursal es la suma del Kardex y cada equipo tiene su entrada", async () => {
    const result = conciliar();
    expect(result.products).toBe(8);
    expect(result.mismatches, "existencia por sucursal != suma del Kardex").toEqual([]);
    expect(result.inventory_field, "Product.inventory != suma de sus sucursales").toEqual([]);
    expect(result.serialized).toHaveLength(5);
    for (const row of result.serialized) {
      expect(row.units, `${row.product}: equipos`).toBe(row.entries);
      expect(row.available, `${row.product}: equipos disponibles`).toBe(row.units);
    }
    expect(result.serialized.reduce((total, row) => total + row.units, 0)).toBe(FIRST);
    const totals = contar();
    expect(totals.units).toBe(FIRST);
    expect(totals.unit_imeis).toBe(FIRST);
  });

  test("6 · a 390 px no hay desborde y los paneles se operan con el teclado", async () => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 390, height: 844 });
    try {
      // Productos con «Recibir mercadería».
      await page.goto("/admin/products", { waitUntil: "networkidle" });
      await page.getByRole("searchbox", { name: "Buscar productos" }).fill(seed.cableName);
      await expect(page.getByRole("link", { name: `Recibir mercadería de ${seed.cableName}` })).toBeVisible({ timeout: 30_000 });
      await expectNoOverflow(page, "Productos");

      // Movimientos con el producto elegido.
      await page.goto(`/admin/inventory/movements?receive=1&product=${seed.cable}`, { waitUntil: "networkidle" });
      await expect(page.getByText(/Stock de la empresa: \d+ u\./)).toBeVisible({ timeout: 30_000 });
      await expectNoOverflow(page, "Movimientos");

      // Vista previa de texto con la propuesta y la revisión de equipos.
      const lista = [
        [`iPhone 13 ${TAG}`, "128 GB", "Color: negro", `NS: ${TAG}Z01`, `IMEI 1: ${imei(201)}`],
        [`iPhone 16 ${TAG}`, "256 GB", "Color: azul", `NS: ${TAG}Z02`, `IMEI 1: ${imei(202)}`],
        [`iPhone 16 ${TAG}`, "256 GB", "Color: azul", `NS: ${TAG}Z03`, `IMEI 1: ${imei(203)}`],
      ].map((lines) => lines.join("\n")).join("\n\n");
      const before = contar();
      await openBulk(page);
      await page.getByLabel("Línea declarada").selectOption("new");
      await page.getByLabel("Origen declarado").selectOption("own");
      await page.getByLabel("Texto para reconocer").fill(lista);
      await page.getByRole("button", { name: "Analizar localmente" }).click();
      await page.getByRole("button", { name: "Buscar en el catálogo" }).click();
      const propuesta = page.getByRole("region", { name: "Proponer lo que falta" });
      await expect(propuesta).toBeVisible({ timeout: 30_000 });
      await propuesta.getByRole("button", { name: "Proponer lo que falta" }).click();
      await expect(propuesta.getByRole("status").first()).toContainText("por crear", { timeout: 30_000 });
      await expectNoOverflow(page, "Vista previa de texto con la propuesta");
      await propuesta.getByLabel("Seleccionar todo").check();
      await expectNoOverflow(page, "Propuesta aprobada");
      // Sin crear nada: se cancela la propuesta, y el borrador de una fila conocida llega a la revisión.
      await propuesta.getByRole("button", { name: "Cancelar" }).click();
      await openBranchField(page, seed.branchName, `[E2E] ${TAG} móvil`);
      await page.getByRole("button", { name: "Generar borrador" }).click();
      await page.getByRole("button", { name: "Revisar y registrar" }).click();
      await expect(page.getByRole("table", { name: /Estado de cada fila/ })).toBeVisible({ timeout: 40_000 });
      await expectNoOverflow(page, "Revisión de equipos");
      expect(contar()).toEqual(before);

      // Centro de Publicación: tabla, ficha y «Publicar listos».
      await page.goto(`/admin/products/publication?search=${TAG}`, { waitUntil: "networkidle" });
      const tabla = page.getByRole("table", { name: "Productos y variantes" });
      await expect(tabla).toBeVisible({ timeout: 40_000 });
      await expectNoOverflow(page, "Publicación");
      const abrir = tabla.getByRole("button", { name: `Completar ficha de ${shared.b128!.name}` });
      await abrir.focus();
      await abrir.click();
      const ficha = page.getByRole("dialog");
      await expect(ficha.getByTestId("visibility-banner")).toBeVisible({ timeout: 30_000 });
      await expectNoOverflow(page, "Completar ficha");
      await expectFocusTrapped(page, ficha, "Completar ficha");
      await page.keyboard.press("Escape");
      await expect(ficha).toHaveCount(0);
      await expect(abrir, "el foco vuelve al botón que abrió la ficha").toBeFocused();

      await page.getByRole("button", { name: "Listos para publicar" }).click();
      const publicar = page.getByRole("button", { name: /Publicar listos \(\d+\)/ });
      await expect(publicar).toBeVisible({ timeout: 30_000 });
      await publicar.click();
      const listos = page.getByRole("dialog");
      await expect(listos).toContainText("0 marcados");
      await expectNoOverflow(page, "Publicar listos");
      await expectFocusTrapped(page, listos, "Publicar listos");
      await page.keyboard.press("Escape");
      await expect(listos).toHaveCount(0);

      // Etiquetas.
      await page.goto("/admin/inventory/labels", { waitUntil: "networkidle" });
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible({ timeout: 30_000 });
      await expectNoOverflow(page, "Etiquetas");
    } finally {
      await page.setViewportSize({ width: 1280, height: 720 });
    }
  });

  test("7 · el navegador no guarda IMEI, series, costos ni credenciales", async ({ browser }) => {
    const secrets = [...DEVICES.flatMap((d) => [d.imei, d.serial]), TAG];
    const keys = async (target: Page) => target.evaluate(() => ({
      local: { ...window.localStorage }, session: { ...window.sessionStorage }, cookie: document.cookie,
    }));
    await page.goto("/admin/inventory/movements", { waitUntil: "networkidle" });
    const stored = await keys(page);
    const dump = JSON.stringify(stored);
    for (const secret of secrets) expect(dump, `se guardó «${secret}» en el navegador`).not.toContain(secret);
    const names = [...Object.keys(stored.local), ...Object.keys(stored.session)];
    for (const name of names) expect(name, "una clave del almacenamiento suena a dato sensible").not.toMatch(/imei|serial|cost|costo|token|secret|password|clave|supplier|proveedor|jwt/i);
    // Las credenciales viven en cookies HttpOnly: JavaScript sólo ve el CSRF.
    expect(stored.cookie).not.toMatch(/access|refresh|jwt|session/i);
    // El visitante de la tienda tampoco guarda nada de lo anterior.
    const guest = await browser.newContext();
    const visitor = await guest.newPage();
    await visitor.goto(`/product/${shared.b128!.slug}`, { waitUntil: "networkidle" });
    const seen = JSON.stringify(await keys(visitor));
    for (const secret of secrets) expect(seen).not.toContain(secret);
    await guest.close();
    console.log(`INV-QA-E: claves del navegador (panel) -> ${names.sort().join(", ") || "ninguna"}`);
  });
});
