import { test, expect } from "@playwright/test";

const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";

/**
 * C2.1 — cobrar en mostrador y sacar el ticket.
 *
 * QUÉ PRUEBA ESTO QUE NADA MÁS PRUEBA
 * -----------------------------------
 * Comprueba que elegir nota interna al cobrar permite imprimir el documento
 * persistido mediante los botones de la pantalla.
 *
 * La secuencia esperada, y que esta prueba observa en la red:
 *   POST .../pos/sales/      -> 201   (crea venta y nota seleccionada)
 *   GET  .../sales-note/      -> 200   (recupera la nota existente)
 *   GET  .../sales-note/pdf/?formato=ticket80 -> 200 application/pdf
 *
 * Depende de las cuentas de desarrollo y de que el catálogo tenga stock. Si
 * falta cualquiera de las dos, se salta DICIENDO POR QUÉ: un salto silencioso
 * se parece demasiado a un aprobado.
 */

test("una venta de mostrador muestra su desglose y produce un ticket de 80 mm", async ({ page, request }) => {
  test.setTimeout(180_000);

  // QUÉ ARTÍCULO SE VENDE LO DECIDE EL CATÁLOGO, NO ESTE FICHERO.
  //
  // La primera versión buscaba «iPhone» por nombre. Las propias corridas de esta
  // prueba fueron vendiendo unidades hasta dejar ese artículo a cero, y entonces
  // el buscador lo encontraba pero el carrito se quedaba vacío: la prueba fallaba
  // por haberse gastado a sí misma el stock, no por un defecto del código.
  const catalogo = await request.get(`${API}/products/?page_size=50`);
  const cuerpo = await catalogo.json();
  const articulos = Array.isArray(cuerpo) ? cuerpo : (cuerpo.results ?? []);
  const vendible = articulos.find(
    (p: { price: string; inventory: number }) => Number(p.price) > 0 && p.inventory >= 2,
  );
  if (!vendible) {
    test.skip(true, "el catálogo no tiene ningún artículo con stock suficiente");
  }

  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) {
    test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  }
  const row = card.locator("li").filter({ hasText: "dev_admin" });
  const use = row.getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) {
    test.skip(true, "dev_admin no existe o está inactiva: siembra con seed_demo_users");
  }
  await use.click();

  // EL LIMITADOR SON 5 INTENTOS POR MINUTO Y POR IP — H4.1.2A. Es una defensa
  // real, y en la suite completa entran once cuentas seguidas: a alguna le toca
  // esperar. Sin reintento, esa espera se leía como un fallo del cambio que se
  // estuviera probando. Se ESPERA la ventana, como ya hacen `demo-accounts`,
  // `h411-auth-interop`, `fiscal-invoice` y `staff-personnel`.
  for (let intento = 0; intento < 3; intento++) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 15_000 });
      break;
    } catch {
      if (intento === 2) throw new Error("no se pudo iniciar sesión: el limitador no cedió");
      await page.waitForTimeout(62_000);
    }
  }

  await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });

  // «Escanear código» es el primer campo y espera un código de barras; el de
  // búsqueda por nombre es éste. Confundirlos deja el carrito vacío y la prueba
  // pasando por los motivos equivocados.
  const search = page.getByPlaceholder(/por nombre o c/i).first();
  await expect(search, "el punto de venta no cargó").toBeVisible({ timeout: 30_000 });
  await search.fill(vendible.name.slice(0, 12));
  await page.waitForTimeout(3000);

  // EL RESULTADO DE LA BÚSQUEDA, NO LA TARJETA DEL COMBO — H4.1.2A.
  //
  // La pantalla sugiere combos, y sus tarjetas nombran los mismos artículos: un
  // `button` filtrado sólo por texto casaba PRIMERO con el combo «1× AirPods Pro
  // + 1× iPhone 15 Pro». Mientras ese combo estuvo disponible, la prueba pasaba
  // añadiendo un combo de dos artículos en vez del artículo que dice vender —
  // verde por el motivo equivocado. El día que el iPhone se quedó sin stock, el
  // combo apareció deshabilitado, con razón, y la prueba se quedó tres minutos
  // esperando a que se habilitara un botón que nunca debía habilitarse.
  //
  // Se pide lo que la prueba quiere de verdad: una fila del catálogo que se
  // puede pulsar y que declara unidades disponibles.
  const result = page
    .locator("button:not([disabled])")
    .filter({ hasText: vendible.name.slice(0, 12) })
    .filter({ hasText: /\d+\s*disp\./ })
    .first();
  if ((await result.count()) === 0) {
    test.skip(true, "el catálogo no tiene productos con stock en esta sucursal");
  }
  await result.click();
  await page.waitForTimeout(2000);

  // EL DESGLOSE, ANTES DE COBRAR. Si el cliente pregunta cuánto es de IGV, la
  // respuesta ya está en pantalla — y es la que va a salir impresa.
  const beforeCharging = await page.locator("body").innerText();
  expect(/Op\. gravada/.test(beforeCharging), "la previsualización no muestra la base").toBe(true);
  expect(/IGV \(\d+(\.\d+)?%\)/.test(beforeCharging), "la previsualización no muestra el IGV").toBe(true);

  await page.getByRole("checkbox").first().check();
  await page.getByLabel('Tipo de comprobante').selectOption('sales_note');
  const cash = page.locator("input[inputmode='decimal'], input[type='number']").last();
  if (await cash.count()) await cash.fill("99999");

  await page.getByRole("button", { name: /^cobrar$/i }).click();
  await expect(
    page.getByText(/Venta registrada/i),
    "la venta no se completó",
  ).toBeVisible({ timeout: 40_000 });

  // El mismo desglose, ya congelado en la venta.
  const afterCharging = await page.locator("body").innerText();
  expect(/Op\. gravada:/.test(afterCharging), afterCharging.slice(0, 400)).toBe(true);
  expect(/IGV \(\d+(\.\d+)?%\):/.test(afterCharging)).toBe(true);

  // Imprimir recupera la nota creada al cobrar.
  const calls: string[] = [];
  page.on("response", (r) => {
    if (r.url().includes("sales-note") && r.status() !== 307 && r.status() !== 308) {
      calls.push(`${r.request().method()} ${r.status()}`);
    }
  });

  // La respuesta FINAL: el proxy de Next devuelve un 308 quitando la barra
  // final, y esperar esa redirección en vez del PDF hace que la prueba expire
  // con el flujo funcionando perfectamente.
  const pdf = page.waitForResponse(
    (r) => r.url().includes("sales-note/pdf") && r.url().includes("ticket80")
        && r.status() !== 307 && r.status() !== 308,
    { timeout: 60_000 },
  );
  await page.getByRole("button", { name: /imprimir ticket/i }).click();
  const res = await pdf;

  expect(res.status()).toBe(200);
  expect(res.headers()["content-type"]).toContain("application/pdf");
  expect(await res.finished(), "la descarga del PDF no terminó bien").toBeNull();
  expect(Number(res.headers()["content-length"])).toBeGreaterThan(800);
  // EL CONTENIDO SE PIDE OTRA VEZ, con la misma sesión. La página lee esta
  // respuesta como un blob para imprimirla, y desde Playwright 1.63 el cuerpo
  // de una respuesta así llega vacío al arnés aunque el navegador la recibió
  // entera. Pedirla de nuevo comprueba lo que importa: que esa dirección,
  // con esta sesión, devuelve un PDF de verdad. No pasa por `page`, así que no
  // cuenta entre las llamadas que se vigilan más abajo.
  const bytes = await (await page.request.get(res.url())).body();
  expect(bytes.subarray(0, 4).toString(), "lo devuelto no es un PDF").toBe("%PDF");
  expect(bytes.length).toBeGreaterThan(800);

  // La nota seleccionada ya existe: imprimir no debe crear otro documento.
  expect(
    calls.filter((c) => c.startsWith("POST")).length,
    `llamadas a la nota: ${calls.join(", ")}`,
  ).toBe(0);

  // LOS BOTONES TIENEN QUE VOLVER.
  //
  // Esperaban el `onload` de un marco oculto que, con un PDF servido como blob,
  // NO dispara: los dos botones se quedaban en «Preparando…» para siempre y el
  // mostrador sólo salía recargando a media venta. Medido: veinticinco segundos
  // y seguían bloqueados. La espera está acotada ahora, y esto lo vigila.
  await expect(
    page.getByRole("button", { name: /imprimir ticket/i }),
    "el botón de imprimir se quedó bloqueado",
  ).toBeEnabled({ timeout: 20_000 });
  await expect(
    page.getByRole("button", { name: /pdf a4/i }),
    "el botón de A4 se quedó bloqueado",
  ).toBeEnabled({ timeout: 20_000 });
});
