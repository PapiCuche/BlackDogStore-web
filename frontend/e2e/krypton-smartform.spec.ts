import { test, expect, type Page, type APIRequestContext } from "@playwright/test";

/**
 * «Mi Cuenta Web»: el formulario de la pasarela, cargado de verdad.
 *
 * QUÉ VIGILA ESTO QUE JEST NO PUEDE
 * ---------------------------------
 * Jest comprueba que la página pide el guion oficial con los atributos que la
 * documentación indica. No puede comprobar que ese guion CARGUE dentro de esta
 * aplicación: que la política de seguridad de la página no lo bloquee, que React
 * no lo monte dos veces, y que lo que el cliente de la pasarela diga cuando algo
 * va mal llegue a la pantalla en vez de perderse.
 *
 * QUÉ ES REAL Y QUÉ NO
 * --------------------
 * Real: el navegador, la página de pago, y el cliente Krypton, que se descarga
 * de `static.micuentaweb.pe`. Por eso esta prueba necesita red.
 * No real: la sesión de pago. Sin claves de la tienda no hay `formToken` que la
 * pasarela acepte, así que la respuesta de `create-checkout-session` se sirve
 * aquí con un token inventado. El cliente lo rechaza, y eso es justo lo que se
 * mira: cómo se comporta la página cuando el token no vale (uno caducado se ve
 * igual desde el navegador).
 *
 * NO SUSTITUYE AL PAGO DE PRUEBA en el entorno TEST de la pasarela, que sigue
 * pendiente de las claves del propietario.
 */

const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";
const KRYPTON_HOST = "https://static.micuentaweb.pe/";
const RUN = Date.now().toString(36);

// Inventados y con forma de TEST: no son de ninguna tienda.
const SESSION = {
  order_id: 1,
  provider: "micuentaweb",
  environment: "test",
  transaction_id: `E2E-${RUN}`,
  form_token: `e2e-token-${RUN}`,
  public_key: "11111111:testpublickey_NoEsDeNingunaTienda",
};

async function sellableProduct(request: APIRequestContext) {
  const res = await request.get(`${API}/products/?page_size=50`);
  if (!res.ok()) return null;
  const body = await res.json();
  const items = Array.isArray(body) ? body : (body.results ?? []);
  return items.find((p: { price: string; inventory: number }) =>
    Number(p.price) > 0 && p.inventory >= 1) ?? null;
}

async function seedCart(page: Page, request: APIRequestContext, key: string) {
  const product = await sellableProduct(request);
  if (!product) return false;
  let added = await request.post(`${API}/cart/add/`, {
    data: { session_key: key, product: product.id, quantity: 1 },
  });
  // El limitador se respeta: se espera lo que pide y se reintenta.
  for (let attempt = 0; added.status() === 429 && attempt < 3; attempt++) {
    const wait = Number((await added.text()).match(/(\d+)\s*segundo/)?.[1] ?? 5);
    await page.waitForTimeout((wait + 1) * 1000);
    added = await request.post(`${API}/cart/add/`, {
      data: { session_key: key, product: product.id, quantity: 1 },
    });
  }
  expect(added.ok(), `no se pudo sembrar el carrito (${added.status()})`).toBe(true);
  await page.addInitScript((value) => {
    try { window.localStorage.setItem("blackdog_session_key", value as string); } catch { /* sin almacenamiento no hay carrito */ }
  }, key);
  return true;
}

/** Lo que la página de pago necesita para aceptar el formulario. */
async function fillCheckout(page: Page) {
  // El aviso de cookies tapa el final del formulario: se responde, y que no.
  const refuse = page.getByRole("dialog").getByRole("button", { name: /rechazar|solo las necesarias|sólo las necesarias/i });
  if (await refuse.count()) await refuse.first().click();
  await page.locator("#checkout-customer-name").fill("Compradora de Prueba");
  await page.locator("#checkout-customer-email").fill(`e2e-${RUN}@example.test`);
  await page.locator("#checkout-customer-phone").fill("999888777");
  await page.locator("#checkout-document-number").fill("12345678");
  const pickup = page.locator('input[name="delivery_method"][value="pickup"]');
  if (await pickup.count()) await pickup.check({ force: true });
  // Las casillas son de React: hasta que la página está viva, un clic se pierde.
  for (const name of ["accepted_terms", "accepted_warranty_policy"]) {
    const box = page.locator(`input[name="${name}"]`);
    await expect(async () => {
      if (!(await box.isChecked())) await box.click({ force: true });
      expect(await box.isChecked()).toBe(true);
    }).toPass({ timeout: 15_000 });
  }
}

test.describe("el formulario de Mi Cuenta Web carga dentro de la tienda", () => {
  test("guion oficial, sin bloqueo de la política de seguridad, y un token que no vale se dice en pantalla", async ({ page, request }) => {
    test.setTimeout(180_000);
    if (!(await seedCart(page, request, `krypton-e2e-${RUN}`))) {
      test.skip(true, "la API no devuelve ningún producto con stock");
    }

    const fromGateway: string[] = [];
    const consoleErrors: string[] = [];
    page.on("response", (res) => {
      if (res.url().startsWith(KRYPTON_HOST)) fromGateway.push(`${res.status()} ${res.url()}`);
    });
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await page.addInitScript(() => {
      (window as unknown as { __csp: string[] }).__csp = [];
      document.addEventListener("securitypolicyviolation", (event) => {
        (window as unknown as { __csp: string[] }).__csp.push(`${event.violatedDirective} ${event.blockedURI}`);
      });
    });
    // Cada intento abre su propia sesión, como hace el servidor: otro token.
    let opened = 0;
    await page.route("**/api/payments/create-checkout-session/", (route) => {
      opened += 1;
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ ...SESSION, form_token: `${SESSION.form_token}-${opened}`, transaction_id: `${SESSION.transaction_id}-${opened}` }),
      });
    });

    await page.goto("/checkout", { waitUntil: "networkidle" });
    await fillCheckout(page);
    await page.locator('form button[type="submit"]').click();

    const region = page.getByRole("region", { name: "Pago con tarjeta" });
    await expect(region).toBeVisible({ timeout: 30_000 });

    // El guion oficial, una sola vez, con la clave pública de la sesión.
    await page.waitForFunction(() => Boolean((window as unknown as { KR?: unknown }).KR), null, { timeout: 60_000 });
    const scripts = await page.evaluate(() =>
      Array.from(document.head.querySelectorAll("script"))
        .filter((s) => (s.getAttribute("src") ?? "").includes("krypton-client"))
        .map((s) => ({ src: s.getAttribute("src"), key: s.getAttribute("kr-public-key") })));
    const client = scripts.filter((s) => s.src?.endsWith("kr-payment-form.min.js"));
    expect(client, JSON.stringify(scripts)).toHaveLength(1);
    expect(client[0].key).toBe(SESSION.public_key);
    expect(fromGateway.some((line) => line.startsWith("200 ") && line.includes("kr-payment-form.min.js")),
      fromGateway.join("\n")).toBe(true);

    // Nada de la pasarela quedó bloqueado por la política de la página.
    const blocked = await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp);
    expect(blocked, blocked.join("\n")).toEqual([]);
    expect(consoleErrors.filter((text) => /content security policy/i.test(text))).toEqual([]);

    // Ningún campo de tarjeta es de esta aplicación.
    await expect(page.locator('input[name*="card" i], input[autocomplete^="cc-"]')).toHaveCount(0);

    // El token no vale. El cliente de la pasarela no dibuja nada y sólo se lo
    // dice a quien escucha: la página lo dice en pantalla y devuelve el botón.
    const said = page.getByRole("alert").filter({ hasText: /formulario de pago ya no es válido/i });
    await expect(said).toBeVisible({ timeout: 30_000 });
    await expect(said).toContainText(/no se cobró nada/i);
    await expect(said).not.toContainText(/CLIENT_|PSP_|Invalid/);
    await expect(region).toHaveCount(0);
    const again = page.getByRole("button", { name: /continuar al pago/i });
    await expect(again).toBeEnabled();

    // Segundo intento en la misma pestaña: el cliente ya está, no se carga otra
    // vez, y un segundo token que tampoco vale se vuelve a decir.
    await again.click();
    await expect(said).toBeVisible({ timeout: 30_000 });
    await expect(again).toBeEnabled();
    const loadedTwice = await page.evaluate(() =>
      Array.from(document.head.querySelectorAll("script"))
        .filter((s) => (s.getAttribute("src") ?? "").endsWith("kr-payment-form.min.js")).length);
    expect(loadedTwice).toBe(1);
    expect(opened).toBe(2);
    expect(await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp)).toEqual([]);
  });
});
