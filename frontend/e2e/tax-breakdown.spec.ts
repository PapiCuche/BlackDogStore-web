import { test, expect, type Page, type APIRequestContext } from "@playwright/test";

/**
 * C2.1 — el desglose tributario, visto como lo ve un comprador.
 *
 * QUÉ VIGILA ESTO QUE LOS TESTS DE PYTHON NO PUEDEN
 * -------------------------------------------------
 * El backend ya prueba que la aritmética cuadra y que los PDF dicen lo que
 * deben. Lo que no puede probar es que las cifras LLEGUEN a la pantalla, que
 * quepan en un móvil de 320 px y que se lean en los dos temas.
 *
 * POR QUÉ EL CARRITO SE SIEMBRA POR API Y NO HACIENDO CLIC
 * -------------------------------------------------------
 * La primera versión de esta prueba paseaba por la tienda pulsando «Agregar al
 * carrito». Los nueve casos se SALTARON en silencio, porque el selector
 * apuntaba a `/products/` y la ruta real es `/product/`. Un test que se salta
 * no prueba nada, y se parece demasiado a uno que pasa.
 *
 * Sembrar por API quita esa fragilidad y además acota el asunto: aquí se
 * comprueba el DESGLOSE, no el flujo de compra, que tiene su propia prueba. Si
 * el catálogo está de verdad vacío, el `skip` lo dice con el motivo real.
 *
 * LO QUE NO SE COMPRUEBA AQUÍ: que 118 = 100 + 18. Eso es del backend, que es
 * quien lo calcula. Repetir la aritmética en TypeScript crearía la segunda
 * autoridad de cálculo que toda esta fase existe para evitar.
 */

const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";

const VIEWPORTS = [
  { name: "320", width: 320, height: 760 },
  { name: "390", width: 390, height: 844 },
  { name: "768", width: 768, height: 1024 },
  { name: "1440", width: 1440, height: 900 },
];

const THEMES = ["light", "dark"] as const;

/**
 * Clave de carrito única por corrida.
 *
 * Con una fija, cada ejecución sumaba al carrito de la anterior: el mismo test
 * veía 1 unidad la primera vez y 8 la octava. No rompía las aserciones, pero
 * hacía que dos corridas del «mismo» test no probaran lo mismo.
 */
const RUN = `${Date.now().toString(36)}`;

/** Un artículo vendible del catálogo, o null si esta base no tiene ninguno. */
async function sellableProduct(request: APIRequestContext) {
  const res = await request.get(`${API}/products/?page_size=50`);
  if (!res.ok()) return null;
  const body = await res.json();
  const items = Array.isArray(body) ? body : (body.results ?? []);
  // Con holgura: esta prueba no va de stock, y elegir el primero con una
  // unidad la hacía fallar en cuanto otra prueba vendía ese artículo.
  return items.find((p: { price: string; inventory: number }) =>
    Number(p.price) > 0 && p.inventory >= 4) ?? null;
}

/**
 * Deja un carrito servidor listo y hace que el navegador lo reconozca como suyo.
 *
 * La clave de sesión vive en `localStorage`; se fija ANTES de que cargue nada,
 * o la aplicación se inventa una y ve un carrito vacío.
 */
async function seedCart(page: Page, request: APIRequestContext, key: string) {
  const product = await sellableProduct(request);
  if (!product) return false;

  // EL LIMITADOR SE RESPETA, NO SE ESQUIVA.
  //
  // Sembrar el carrito cuesta una petición por combinación de tema y anchura, y
  // la suite completa acaba agotando el cupo por IP: el servidor responde 429 y
  // dice cuántos segundos faltan. Eso es la defensa funcionando. El arnés
  // espera lo que le piden y reintenta, en vez de fallar y dejar creer que el
  // desglose está roto. Desactivar el límite para las pruebas sería probar un
  // servidor que no es el que se despliega.
  let added = await request.post(`${API}/cart/add/`, {
    data: { session_key: key, product: product.id, quantity: 1 },
  });
  for (let intento = 0; added.status() === 429 && intento < 3; intento++) {
    const espera = Number(
      (await added.text()).match(/(\d+)\s*segundo/)?.[1] ?? 5,
    );
    await page.waitForTimeout((espera + 1) * 1000);
    added = await request.post(`${API}/cart/add/`, {
      data: { session_key: key, product: product.id, quantity: 1 },
    });
  }
  expect(
    added.ok(),
    `no se pudo sembrar el carrito (${added.status()}): ${await added.text()}`,
  ).toBe(true);

  await page.addInitScript((value) => {
    try {
      window.localStorage.setItem("blackdog_session_key", value as string);
    } catch {
      // Ventana privada o almacenamiento bloqueado: sin esto no hay carrito, y
      // la aserción de abajo lo dirá con claridad.
    }
  }, key);
  return true;
}

async function useTheme(page: Page, theme: (typeof THEMES)[number]) {
  await page.emulateMedia({ colorScheme: theme });
  await page.addInitScript((value) => {
    try { window.localStorage.setItem("theme", value as string); } catch { /* ver arriba */ }
  }, theme);
}

test.describe("el comprador ve cuánto es de impuesto antes de pagar", () => {
  test.describe.configure({ mode: "serial" });

  for (const theme of THEMES) {
    for (const viewport of VIEWPORTS) {
      test(`checkout · ${theme} · ${viewport.name}px`, async ({ page, request }) => {
        test.setTimeout(120_000);
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await useTheme(page, theme);

        const key = `c21-e2e-${RUN}-${theme}-${viewport.name}`;
        if (!(await seedCart(page, request, key))) {
          test.skip(true, "la API no devuelve ningún producto con stock");
        }

        // La cotización viaja en su propia petición, así que se anota su
        // respuesta: cuando el desglose no aparece, la causa está ahí y no en
        // el maquetado. Sin esto el fallo decía «no se ve» y había que
        // adivinar si el servidor había contestado mal o no había contestado.
        const cotizaciones: string[] = [];
        page.on("response", (res) => {
          if (res.url().includes("/checkout/quote")) {
            cotizaciones.push(`${res.status()} ${res.url()}`);
          }
        });

        await page.goto("/checkout", { waitUntil: "networkidle" });

        // La cotización es una llamada aparte; hay que darle su turno.
        await expect(
          page.getByText(/Op\. (gravada|exonerada|inafecta)/).first(),
          `el resumen no muestra el desglose tributario · cotización: ${
            cotizaciones.join(" | ") || "no se pidió"
          }`,
        ).toBeVisible({ timeout: 20_000 });

        const summary = page.locator("aside").first();
        const text = await summary.innerText();

        expect(/IGV \(\d+(\.\d+)?%\)/.test(text), text).toBe(true);

        // «Se emite por separado», nunca «emitido»: aquí no ha habido
        // aceptación de SUNAT y el comprador no debe salir creyendo lo
        // contrario.
        expect(/no es un comprobante/i.test(text), text).toBe(true);
        expect(/\bemitid[ao]\b/i.test(text), text).toBe(false);

        // NADA se sale del ancho de la pantalla. `min-width: auto` en un hijo
        // de grid o flex es el mecanismo que ya rompió esta página tres veces.
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - window.innerWidth,
        );
        expect(overflow, `la página se sale ${overflow}px a lo ancho`).toBeLessThanOrEqual(1);
      });
    }
  }
});

test.describe("el desglose no se inventa en el navegador", () => {
  test("sin respuesta del servidor no se muestra ningún impuesto", async ({ page, request }) => {
    test.setTimeout(120_000);
    // SI LA COTIZACIÓN FALLA, LA PANTALLA CALLA.
    //
    // Es la regla que impide que el escaparate divida el total por su cuenta:
    // mejor no decir nada del impuesto que enseñar una cifra que esta pantalla
    // se haya inventado y que no vaya a coincidir con el papel.
    if (!(await seedCart(page, request, `c21-e2e-${RUN}-sin-cotizacion`))) {
      test.skip(true, "la API no devuelve ningún producto con stock");
    }
    await page.route("**/api/checkout/quote/", (route) => route.abort());

    await page.goto("/checkout", { waitUntil: "networkidle" });
    // El resumen SÍ carga —el carrito viene de otra llamada— y muestra el
    // total, que es lo que se va a cobrar.
    await expect(page.getByText("Subtotal").first()).toBeVisible({ timeout: 20_000 });

    const text = await page.locator("aside").first().innerText();
    expect(/IGV/i.test(text), "se mostró un IGV sin que el servidor lo diera").toBe(false);
    expect(/Op\. gravada/i.test(text), text).toBe(false);
  });
});
