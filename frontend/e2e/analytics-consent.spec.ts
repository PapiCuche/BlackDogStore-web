import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * ANALYTICS-MARKETING — consent and measurement, with a real browser.
 *
 *   · before an answer, and after a refusal, the browser asks nothing of Google,
 *     Meta or TikTok; each category brings only its own providers;
 *   · on a private address — a password reset, a repair's tracking link — no
 *     provider script is requested at all;
 *   · a purchase is measured once, and only when the gateway's signed
 *     notification has made the order paid: opening the success page is not one.
 *
 * NO REQUEST REACHES A PROVIDER. The three script hosts are intercepted and
 * answered with an empty script, so what is observed is what the shop hands to
 * each provider's queue (`dataLayer`, `fbq.queue`, `ttq`).
 *
 * The providers are activated for the run through the same service the console
 * uses, with invented IDs, and removed afterwards: the other specs share this
 * database. The gateway is the contract fake of the payment suites, signing with
 * invented keys.
 */

const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";
const HOSTS = { google: "www.googletagmanager.com", meta: "connect.facebook.net", tiktok: "analytics.tiktok.com" } as const;
const IDS = { google: "G-E2ENOESREAL", meta: "987654321098765", tiktok: "E2ENOESREAL0E2ENOESR" };

/** One backend snippet, run the way the other specs run theirs: against the E2E database. */
const BACKEND = String.raw`
import json, os, urllib.request
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from store.integrations import registry, service
from store.models import Company, IntegrationConfig, Order, OrderItem, PaymentTransaction, Product
from store.payments import izipay
from store.payments.fake_izipay import FakeIzipay

mode, arg = os.environ["E2E_MEASURE_MODE"], os.environ.get("E2E_MEASURE_ARG", "")
KEYS = {"IZIPAY_ENV": "sandbox", "IZIPAY_MERCHANT_CODE": "9000042", "IZIPAY_PUBLIC_KEY": "e2e-publica-NoEsReal",
        "IZIPAY_API_KEY": "e2e-api-key-NoEsReal", "IZIPAY_HASH_KEY": "e2e-hash-key-NoEsReal",
        "IZIPAY_TOKEN_URL": "https://izipay.invalid/token", "IZIPAY_CURRENCY": "PEN"}
PROVIDERS = {
    "google_analytics": ({"measurement_id": "G-E2ENOESREAL"}, {}),
    "meta": ({"mode": "pixel_only", "pixel_id": "987654321098765"}, {}),
    "tiktok": ({"mode": "pixel_only", "pixel_code": "E2ENOESREAL0E2ENOESR"}, {}),
    "izipay_checkout": ({"environment": "sandbox", "merchant_code": KEYS["IZIPAY_MERCHANT_CODE"],
                         "public_key": KEYS["IZIPAY_PUBLIC_KEY"], "token_url": KEYS["IZIPAY_TOKEN_URL"], "currency": "PEN"},
                        {"api_key": KEYS["IZIPAY_API_KEY"], "hash_key": KEYS["IZIPAY_HASH_KEY"]}),
}

def clean():
    # The console is left as it was found. The paid order stays: a sale moved stock,
    # and the Kardex does not let a sale be deleted — which is the application being right.
    IntegrationConfig.objects.filter(provider__in=PROVIDERS).delete()

if mode == "setup":
    clean()
    master = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
    for provider_id, (public, secrets) in PROVIDERS.items():
        provider = registry.get(provider_id)
        draft = service.save_draft(provider, None, actor=master, public=public, secrets=secrets)
        IntegrationConfig.objects.filter(pk=draft.pk).update(
            validated=True, last_test_status="unverified", last_tested_at=timezone.now())
        service.activate(provider, None, actor=master, version=draft.version,
                         payload={"acknowledge": service.INTERRUPT_WORD})
    print(json.dumps({"ok": True}))
elif mode == "order":
    company = Company.objects.get(slug=os.environ.get("E2E_COMPANY_SLUG", "black-dog-store"))
    product = Product.objects.filter(company=company, is_active=True, price__gt=0).order_by("pk").first()
    order = Order.objects.create(
        company=company, total=product.price, subtotal_amount=product.price, currency="PEN",
        status=Order.Status.PENDING_PAYMENT, customer_name="E2E Medicion", customer_email="e2e-medicion@example.invalid")
    OrderItem.objects.create(order=order, product=product, quantity=1, price=product.price)
    reference = izipay.new_transaction_id()
    PaymentTransaction.objects.create(
        order=order, provider=izipay.PROVIDER, transaction_id=reference, order_number=izipay.new_order_number(),
        amount=order.total, currency="PEN", status=PaymentTransaction.Status.PENDING)
    print(json.dumps({"reference": reference, "order": order.pk, "product": str(product.pk)}))
elif mode == "confirm":
    attempt = PaymentTransaction.objects.get(transaction_id=arg)
    fake = FakeIzipay.from_settings(KEYS)
    envelope = fake.envelope(fake.payload(
        transaction_id=attempt.transaction_id, order_number=attempt.order_number, amount=attempt.amount))
    request = urllib.request.Request(
        os.environ["E2E_API_BASE"] + "/payments/izipay/notification/", data=json.dumps(envelope).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        print(json.dumps({"status": response.status}))
elif mode == "cleanup":
    clean()
    print(json.dumps({"ok": True}))
`;

function backend(mode: string, arg = ""): Record<string, unknown> {
  const out = execFileSync("python3", ["manage.py", "shell", "-c", BACKEND], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, E2E_MEASURE_MODE: mode, E2E_MEASURE_ARG: arg, E2E_API_BASE: API },
  }).toString();
  return JSON.parse(out.trim().split("\n").filter((line) => line.startsWith("{")).pop() ?? "{}");
}

/** Every request the page makes to a provider's host, answered with an empty script. */
async function interceptProviders(page: Page): Promise<string[]> {
  const requested: string[] = [];
  const stub = (route: Route) => {
    requested.push(route.request().url());
    return route.fulfill({ status: 200, contentType: "application/javascript", body: "/* e2e */" });
  };
  await page.route(/googletagmanager\.com|google-analytics\.com|doubleclick\.net/, stub);
  await page.route(/facebook\.net|facebook\.com\/tr/, stub);
  await page.route(/tiktok\.com/, stub);
  return requested;
}

const from = (requested: string[], host: string) => requested.filter((url) => url.includes(host));

/** What the shop queued for each provider in THIS document. */
const queued = (page: Page) => page.evaluate(() => {
  const w = window as unknown as { dataLayer?: IArguments[]; fbq?: { queue: unknown[][] }; ttq?: unknown[][] };
  const google = (w.dataLayer ?? []).map((entry) => Array.from(entry)).filter((call) => call[0] === "event")
    .map((call) => ({ name: String(call[1]), params: call[2] as Record<string, unknown> }));
  const meta = (w.fbq?.queue ?? []).filter((call) => call[0] === "track")
    .map((call) => ({ name: String(call[1]), params: call[2] as Record<string, unknown>, options: call[3] as Record<string, string> }));
  const tiktok = (Array.isArray(w.ttq) ? w.ttq : []).filter((call) => Array.isArray(call) && (call[0] === "track" || call[0] === "page"))
    .map((call) => ({ name: call[0] === "page" ? "PageView" : String(call[1]), options: call[3] as Record<string, string> }));
  return { google, meta, tiktok, loaded: { gtag: typeof (window as unknown as { gtag?: unknown }).gtag, fbq: typeof (window as unknown as { fbq?: unknown }).fbq } };
});

const accept = (page: Page) => page.getByRole("button", { name: "Aceptar todas" }).click();

test.describe.configure({ mode: "serial" });

test.beforeAll(() => {
  expect(backend("setup").ok, "no se pudieron activar los proveedores de la prueba").toBe(true);
});

test.afterAll(() => {
  backend("cleanup");
});

test("sin respuesta no se pide nada a nadie; rechazar tampoco; cada categoría trae sólo lo suyo", async ({ page }) => {
  const requested = await interceptProviders(page);
  await page.goto("/", { waitUntil: "networkidle" });

  const notice = page.getByRole("dialog", { name: "Cookies en esta tienda" });
  await expect(notice).toBeVisible();
  for (const name of ["Aceptar todas", "Rechazar opcionales", "Configurar"]) {
    await expect(notice.getByRole("button", { name })).toBeVisible();
  }
  expect(requested, "se pidió algo a un proveedor antes de responder").toEqual([]);
  expect((await queued(page)).loaded).toEqual({ gtag: "undefined", fbq: "undefined" });

  await notice.getByRole("button", { name: "Rechazar opcionales" }).click();
  await expect(notice).toBeHidden();
  await page.goto("/product", { waitUntil: "networkidle" });
  expect(requested, "se pidió algo a un proveedor tras rechazar").toEqual([]);
  await expect(page.getByRole("dialog", { name: "Cookies en esta tienda" }), "volvió a preguntar").toHaveCount(0);

  // Las preferencias se reabren desde el pie, y cambiar una vale al momento.
  await page.getByRole("button", { name: "Preferencias de cookies" }).click();
  const preferences = page.getByRole("dialog", { name: "Preferencias de cookies" });
  await expect(preferences.getByRole("switch", { name: "Necesarias" })).toBeDisabled();
  await expect(preferences.getByRole("switch", { name: "Analítica" })).not.toBeChecked();
  await preferences.getByRole("switch", { name: "Analítica" }).check();
  await preferences.getByRole("button", { name: "Guardar preferencias" }).click();

  await expect.poll(() => from(requested, HOSTS.google).length, { message: "Google no se cargó tras aceptar analítica" }).toBe(1);
  expect(from(requested, HOSTS.google)[0]).toContain(`id=${IDS.google}`);
  expect(from(requested, HOSTS.meta).concat(from(requested, HOSTS.tiktok)), "marketing se cargó sin aceptarlo").toEqual([]);
  expect((await queued(page)).google.map((event) => event.name)).toEqual(["page_view"]);

  // Y retirarlo detiene el siguiente evento.
  await page.getByRole("button", { name: "Preferencias de cookies" }).click();
  await page.getByRole("dialog", { name: "Preferencias de cookies" }).getByRole("button", { name: "Rechazar opcionales" }).click();
  await page.getByRole("contentinfo").getByRole("link", { name: "Nosotros" }).click();
  await page.waitForURL(/\/about$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect((await queued(page)).google.map((event) => event.name), "siguió midiendo tras retirar el permiso").toEqual(["page_view"]);
});

test("aceptar todo carga los tres, una vez; en una dirección privada no se carga ninguno", async ({ page }) => {
  const requested = await interceptProviders(page);
  await page.goto("/", { waitUntil: "networkidle" });
  await accept(page);
  await expect.poll(() => requested.length).toBe(3);
  expect(from(requested, HOSTS.google)).toEqual([`https://www.googletagmanager.com/gtag/js?id=${IDS.google}`]);
  expect(from(requested, HOSTS.meta)).toEqual(["https://connect.facebook.net/en_US/fbevents.js"]);
  expect(from(requested, HOSTS.tiktok)).toEqual([`https://analytics.tiktok.com/i18n/pixel/events.js?sdkid=${IDS.tiktok}&lib=ttq`]);

  let events = await queued(page);
  expect(events.google.map((event) => event.name)).toEqual(["page_view"]);
  expect(events.meta.map((event) => event.name)).toEqual(["PageView"]);
  expect(events.tiktok.map((event) => event.name)).toEqual(["PageView"]);

  // Otra ruta, en el mismo documento: otra vista, y ningún script repetido.
  await page.goto("/product?category=iphone&utm_source=anuncio", { waitUntil: "networkidle" });
  events = await queued(page);
  expect(events.google.filter((event) => event.name === "page_view").map((event) => event.params.page_path)).toEqual(["/product"]);
  expect(String(events.google[0].params.page_location), "la dirección que se dice lleva parámetros").not.toContain("?");

  // Direcciones que son, o llevan, una credencial: con el permiso dado y todo.
  for (const address of [
    "/auth/reset-password?token=AbCdEf0123456789AbCdEf0123456789AbCd",
    "/auth/verify-email?token=AbCdEf0123456789AbCdEf0123456789AbCd",
    "/seguimiento/AbCdEf0123456789AbCdEf0123456789AbCd",
    "/invitacion?token=AbCdEf0123456789AbCdEf0123456789AbCd",
    "/admin",
  ]) {
    requested.length = 0;
    await page.goto(address, { waitUntil: "networkidle" });
    expect(requested, `${address}: se cargó un script de medición`).toEqual([]);
    expect((await queued(page)).loaded, address).toEqual({ gtag: "undefined", fbq: "undefined" });
  }
});

test("con los scripts ya cargados, ir a una dirección privada abre un documento nuevo, sin ellos", async ({ page }) => {
  // REVISIÓN (P1). Un script ya cargado lee la dirección por su cuenta: no enviar
  // NUESTROS eventos no protege nada. La aplicación no cambia a una dirección
  // privada dentro del documento donde vive un script de medición.
  const requested = await interceptProviders(page);
  await page.goto("/", { waitUntil: "networkidle" });
  await accept(page);
  await expect.poll(() => requested.length).toBe(3);

  // Una navegación de la propia aplicación: lo que hace un <Link> o un router.push.
  await page.evaluate(() => { (window as unknown as { __mismoDocumento?: boolean }).__mismoDocumento = true; });
  requested.length = 0;
  await page.evaluate(() => window.history.pushState({}, "", "/orders"));
  await page.waitForURL(/\/(orders|auth)/);
  await page.waitForLoadState("networkidle");
  expect(await page.evaluate(() => (window as unknown as { __mismoDocumento?: boolean }).__mismoDocumento), "siguió en el mismo documento").toBeUndefined();
  expect(requested, "el documento nuevo cargó un script de medición").toEqual(
    page.url().includes("/auth") ? expect.arrayContaining([expect.stringContaining(HOSTS.google)]) : []);
  expect((await queued(page)).loaded.fbq, "Meta está en una página privada o con formulario").toBe("undefined");

  // El botón Atrás. Se llega al seguimiento con carga completa (sin scripts), se pasa a
  // la tienda dentro del mismo documento —ahí se cargan— y se vuelve atrás: la dirección
  // con el token no puede quedar en un documento que ya los tiene.
  const tracking = "/seguimiento/AbCdEf0123456789AbCdEf0123456789AbCd";
  await page.goto(tracking, { waitUntil: "networkidle" });
  expect((await queued(page)).loaded).toEqual({ gtag: "undefined", fbq: "undefined" });
  requested.length = 0;
  await page.evaluate(() => window.history.pushState({}, "", "/about"));
  await expect.poll(() => requested.length, { message: "la tienda no cargó sus scripts al salir del seguimiento" }).toBe(3);
  await page.evaluate(() => { (window as unknown as { __mismoDocumento?: boolean }).__mismoDocumento = true; });
  requested.length = 0;
  await page.goBack({ waitUntil: "commit" }).catch(() => null);
  // La recarga la pide la página al oír el «Atrás»: se espera a que el documento sea otro.
  await expect.poll(
    () => page.evaluate(() => (window as unknown as { __mismoDocumento?: boolean }).__mismoDocumento ?? "nuevo").catch(() => "cargando"),
    { message: "Atrás dejó el token en el documento con scripts", timeout: 20_000 },
  ).toBe("nuevo");
  await page.waitForLoadState("networkidle");
  expect(page.url()).toContain(tracking);
  expect(requested).toEqual([]);
  expect((await queued(page)).loaded, "un script de medición está en la página de seguimiento").toEqual({ gtag: "undefined", fbq: "undefined" });
});

test("en el checkout, que pide datos personales, no están los píxeles de marketing", async ({ page }) => {
  const requested = await interceptProviders(page);
  await page.goto("/", { waitUntil: "networkidle" });
  await accept(page);
  await expect.poll(() => requested.length).toBe(3);

  for (const address of ["/checkout", "/auth"]) {
    requested.length = 0;
    await page.goto(address, { waitUntil: "networkidle" });
    expect(from(requested, HOSTS.meta).concat(from(requested, HOSTS.tiktok)), `${address}: se cargó un píxel de marketing`).toEqual([]);
    expect(from(requested, HOSTS.google), `${address}: Google sí puede estar`).toHaveLength(1);
    expect((await queued(page)).loaded.fbq).toBe("undefined");
  }
});

test("producto → carrito → checkout → pago confirmado: una compra, una vez, y sólo cuando el pago es firme", async ({ page, request }) => {
  test.setTimeout(240_000);
  const requested = await interceptProviders(page);
  const order = backend("order") as { reference: string; order: number; product: string };

  // El permiso se da en la portada: lo que ocurre ANTES de darlo no se mide después.
  await page.goto("/", { waitUntil: "networkidle" });
  await accept(page);
  await expect.poll(() => requested.length).toBe(3);
  await page.getByRole("link", { name: /cat[aá]logo/i }).first().click();
  await page.waitForURL(/\/product(\?.*)?$/);
  await expect.poll(async () => (await queued(page)).google.map((event) => event.name)).toContain("view_item_list");

  // Catálogo → ficha: se elige un producto y se ve, una vez.
  await page.locator('a[href^="/product/"]').first().click();
  await page.waitForURL(/\/product\/[^/]+$/);
  await expect.poll(async () => (await queued(page)).google.map((event) => event.name)).toContain("view_item");
  let events = await queued(page);
  expect(events.google.map((event) => event.name)).toEqual(expect.arrayContaining(["view_item_list", "select_item", "view_item"]));
  expect(events.google.filter((event) => event.name === "view_item"), "la ficha se contó más de una vez").toHaveLength(1);
  expect(events.meta.map((event) => event.name)).toContain("ViewContent");

  // Al carrito, por la interfaz.
  const add = page.getByRole("button", { name: "Agregar al carrito" });
  test.skip((await add.count()) === 0 || !(await add.isEnabled()), "el primer producto del catálogo no tiene stock");
  await add.click();
  await expect(page.getByText("Producto agregado al carrito.")).toBeVisible({ timeout: 20_000 });
  events = await queued(page);
  expect(events.google.filter((event) => event.name === "add_to_cart")).toHaveLength(1);
  expect(events.meta.filter((event) => event.name === "AddToCart")).toHaveLength(1);
  expect(events.tiktok.filter((event) => event.name === "AddToCart")).toHaveLength(1);
  // El mismo evento lleva el mismo id en Meta y en TikTok.
  expect(events.meta.find((event) => event.name === "AddToCart")!.options.eventID)
    .toBe(events.tiktok.find((event) => event.name === "AddToCart")!.options.event_id);

  await page.getByRole("link", { name: /carrito/i }).first().click();
  await page.waitForURL(/\/cart$/);
  // EL LIMITADOR SE RESPETA, NO SE ESQUIVA. Leer el carrito está limitado a 60 por
  // minuto y por IP, y esta suite lo lee en cada página que abre: si responde 429 se
  // espera la ventana y se vuelve a pedir, en vez de apagar la defensa para la prueba.
  for (let intento = 0; intento < 4; intento += 1) {
    await page.waitForLoadState("networkidle");
    if ((await page.getByRole("link", { name: "Continuar al checkout" }).count()) > 0) break;
    await page.waitForTimeout(20_000);
    await page.reload({ waitUntil: "networkidle" });
  }
  await expect.poll(async () => (await queued(page)).google.filter((event) => event.name === "view_cart").length).toBe(1);

  // «Continuar al checkout»: el último sitio donde Meta y TikTok pueden oírlo, porque
  // en el checkout —que pide datos personales— no están.
  const sentToProviders: string[] = [];
  await page.exposeFunction("__e2eBegun", (name: string) => { sentToProviders.push(name); });
  await page.evaluate(() => {
    const w = window as unknown as { fbq: { queue: unknown[][] }; __e2eBegun: (name: string) => void };
    const push = w.fbq.queue.push.bind(w.fbq.queue);
    w.fbq.queue.push = (...calls: unknown[][]) => { calls.forEach((call) => { if (call[0] === "track") w.__e2eBegun(String(call[1])); }); return push(...calls); };
  });
  await page.getByRole("link", { name: "Continuar al checkout" }).click();
  await page.waitForURL(/\/checkout$/);
  expect(sentToProviders, "Meta no supo que empezaba el checkout").toContain("InitiateCheckout");

  // Checkout: lo que el comprador aceptó viaja con el pedido.
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/payments/create-checkout-session/", async (route) => {
    sent = route.request().postDataJSON();
    // La pasarela de la prueba no existe: la sesión de pago se sustituye abajo por un pedido sembrado.
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Pasarela no disponible en la prueba." }) });
  });
  await page.waitForLoadState("networkidle");
  // Documento nuevo: Google está, los píxeles no, y el inicio del checkout no se repite.
  expect((await queued(page)).loaded.fbq, "un píxel de marketing está en el checkout").toBe("undefined");
  expect((await queued(page)).google.filter((event) => event.name === "begin_checkout"), "el inicio del checkout se contó dos veces").toEqual([]);
  await page.locator("#checkout-customer-name").fill("Compradora De Prueba");
  await page.locator("#checkout-customer-email").fill("compradora@example.invalid");
  await page.locator("#checkout-customer-phone").fill("987654321");
  await page.locator("#checkout-document-number").fill("44556677");
  for (const box of await page.locator('form input[type="checkbox"]').all()) await box.check();
  await page.getByRole("button", { name: /Continuar al pago/ }).click();
  await expect.poll(() => sent, { message: "el checkout no llegó a enviarse" }).not.toBeNull();
  const measurement = (sent as unknown as { measurement: { consent: Record<string, boolean> } & Record<string, unknown> }).measurement;
  expect(measurement.consent).toEqual({ analytics: true, marketing: true });
  expect(Object.keys(measurement).every((key) => ["consent", "ga_client_id", "ga_session_id", "fbp", "fbc", "ttp", "ttclid"].includes(key))).toBe(true);
  // Nada de la persona viajó a ningún proveedor por rellenar el formulario.
  expect(JSON.stringify(await queued(page))).not.toMatch(/Compradora|compradora@|987654321|44556677/);

  // La página de éxito, con el pago todavía sin confirmar: abrirla no es comprar.
  await page.evaluate((reference) => window.sessionStorage.setItem("bd.checkout.reference", reference), order.reference);
  await page.goto("/checkout/success", { waitUntil: "networkidle" });
  expect(page.url(), "la referencia del pago está en la dirección").not.toContain("reference");
  await expect(page.getByText(/verificando|pendiente|No pudimos|procesando/i).first()).toBeVisible({ timeout: 30_000 });
  events = await queued(page);
  expect(events.google.concat(events.meta as never).filter((event) => /purchase/i.test(event.name)), "compra medida sin pago").toEqual([]);

  // La pasarela confirma, con su firma: ahora sí.
  expect(backend("confirm", order.reference).status).toBe(200);
  const status = await request.get(`${API}/payments/status/?reference=${order.reference}`);
  expect((await status.json()).measurement.event_id).toBe(`purchase.${order.order}`);

  await page.goto("/checkout/success", { waitUntil: "networkidle" });
  await expect(page.getByText(/registrada como pagada/)).toBeVisible({ timeout: 30_000 });
  events = await queued(page);
  const bought = {
    google: events.google.filter((event) => event.name === "purchase"),
    meta: events.meta.filter((event) => event.name === "Purchase"),
    tiktok: events.tiktok.filter((event) => event.name === "track" || event.options?.event_id === `purchase.${order.order}`),
  };
  expect(bought.google, "Google no recibió la compra una vez").toHaveLength(1);
  expect(bought.meta, "Meta no recibió la compra una vez").toHaveLength(1);
  expect(bought.google[0].params.transaction_id).toBe(String(order.order));
  expect(bought.meta[0].options.eventID).toBe(`purchase.${order.order}`);
  expect(bought.tiktok.map((event) => event.options.event_id)).toEqual([`purchase.${order.order}`]);
  expect(JSON.stringify(events), "la referencia del pago llegó a un proveedor").not.toContain(order.reference);

  // Recargar la página de éxito no es una segunda compra.
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.getByText(/registrada como pagada/)).toBeVisible({ timeout: 30_000 });
  events = await queued(page);
  expect(events.google.filter((event) => event.name === "purchase"), "la compra se midió dos veces").toEqual([]);
  expect(events.meta.filter((event) => event.name === "Purchase")).toEqual([]);
});
