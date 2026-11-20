import { test, expect, type Page, type Request } from "@playwright/test";
import { shell } from "./inventory-first-support";

/**
 * CATALOG-REFERENCE-01 · PR G — la compra web completa desde la ficha del modelo, en navegador real.
 *
 * SIN MOCKS DEL BACKEND. Un modelo con dos colores (cada uno con su foto de acabado subida como foto propia) y tres
 * variantes publicadas entra a la empresa del backend local (`E2E_BACKEND_DIR`):
 *
 *   · 128 GB Grafito, S/ 3299, dos equipos en la sucursal que despacha la tienda;
 *   · 128 GB Niebla, S/ 3399 (otro precio sólo por el color) y SIN stock propio ni oferta: «Agotado»;
 *   · 256 GB Grafito, S/ 3799, un equipo.
 *
 * Compra: `/modelo/<slug>?variante=` abre esa variante; el precio, la disponibilidad y la foto siguen a cada elección;
 * «Agregar al carrito» → carrito real → checkout real (`create-checkout-session` del backend, con la pasarela FALSA de
 * las pruebas, `FakeIzipay`, en lugar de la red: nunca claves reales) → la pasarela confirma con su firma → el pedido
 * queda pagado con ESE equipo (`OrderUnit`), la salida en el Kardex y un equipo menos en la sucursal.
 *
 * Rendimiento a 375 px: sin desplazamientos de diseño al cargar ni al cambiar de color, la foto principal es el
 * elemento LCP, y sólo se piden las fotos del acabado elegido.
 *
 * Nombres inventados (AGENTS §2). Con `E2E_SCREENSHOTS=<carpeta>` deja capturas (fuera del repositorio).
 */

const SHOTS = process.env.E2E_SCREENSHOTS ?? "";
const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";
const SDK = "https://sandbox-checkout.izipay.pe/payments/v1/js/index.js";

type Variant = { id: number; slug: string; name: string };
type Seed = {
  slug: string; name: string; branch: number;
  a: Variant; b: Variant; c: Variant; d: Variant; supplier: string;
  images: { grafito: string[]; niebla: string[] };
};
let seed: Seed;

/** The gateway of the tests, configured in the console with invented sandbox keys (never real ones). */
const GATEWAY = String.raw`
from django.contrib.auth import get_user_model
from django.utils import timezone
from store.integrations import registry, service
from store.models import IntegrationConfig
KEYS = {"IZIPAY_ENV": "sandbox", "IZIPAY_MERCHANT_CODE": "9000042", "IZIPAY_PUBLIC_KEY": "e2e-publica-NoEsReal",
        "IZIPAY_API_KEY": "e2e-api-key-NoEsReal", "IZIPAY_HASH_KEY": "e2e-hash-key-NoEsReal",
        "IZIPAY_TOKEN_URL": "https://izipay.invalid/token", "IZIPAY_CURRENCY": "PEN"}
`;

function seedShop(): Seed {
  return shell<Seed>(`
import io
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from store import checkout_services as checkout, device_catalog, finish_media, model_palette
from store import stock_unit_services as units
from store.models import Company, Product, VariantAttribute, VariantOption
${GATEWAY}
actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug=data['slug'])
branch = checkout.resolve_fulfillment_branch(company)

def attr(role, name, kind='text'):
    found = VariantAttribute.objects.filter(company=company, role=role).first()
    return found or device_catalog.create_attribute(company=company, name=name, role=role, kind=kind)

def opt(attribute, label, swatch=''):
    found = VariantOption.objects.filter(company=company, attribute=attribute, label__iexact=label).first()
    return found or device_catalog.create_option(company=company, attribute_id=attribute.pk, label=label, swatch_hex=swatch)

def png(rgb):
    buffer = io.BytesIO()
    image = Image.new('RGBA', (800, 800), (0, 0, 0, 0))
    image.paste(Image.new('RGBA', (440, 640), rgb + (255,)), (180, 80))
    image.save(buffer, 'PNG')
    return buffer.getvalue()

cap, col = attr('capacity', 'Capacidad'), attr('color', 'Color', 'color')
g128, g256 = opt(cap, '128 GB'), opt(cap, '256 GB')
grafito, niebla = opt(col, 'Grafito', '#3B3B3D'), opt(col, 'Niebla', '#C9CED6')
name = 'Equipo web ' + data['tag']
model = device_catalog.create_model(company=company, name=name, is_published_online=True,
                                    description='Equipo de prueba para la compra web.')
model_palette.set_palette(model, option_ids=[g128.pk, g256.pk, grafito.pk, niebla.pk], actor=actor)

def variant(suffix, price, options, devices):
    product = Product.objects.create(company=company, name=name + ' ' + suffix, slug=(model.slug + '-' + suffix.replace(' ', '-')).lower(),
                                     price=Decimal(price), inventory=0, is_active=True, is_serialized=True,
                                     requires_imei=True, is_published_online=True)
    product = device_catalog.set_variant(product, model_id=model.pk, condition_line='new', option_ids=[o.pk for o in options])
    if devices:
        units.receive_units(branch=branch, product=product, actor=actor, reason='Prueba de navegador',
                            units=[{'imei': value} for value in devices])
    return {'id': product.pk, 'slug': product.slug, 'name': product.name}

a = variant('128 GB Grafito', '3299.00', [g128, grafito], data['imeis'][:2])
b = variant('128 GB Niebla', '3399.00', [g128, niebla], [])
c = variant('256 GB Grafito', '3799.00', [g256, grafito], data['imeis'][2:3])
# 256 GB Niebla: no own device, a supplier offer of 37 units. The shop may sell it «bajo pedido»; the supplier's
# quantity, cost and name are never the shop's stock and never reach the page.
d = variant('256 GB Niebla', '3899.00', [g256, niebla], [])
from datetime import timedelta
from django.utils import timezone
from store import supply_services as supply
Product.objects.filter(pk=d['id']).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
supplier = supply.create_supplier(company=company, name='Mayorista Oculto ' + data['tag'], actor=actor)
supply.create_offer(company=company, supplier_id=supplier.pk, product_id=d['id'], actor=actor, may_view_costs=True,
                    agreed_cost='2811.00', commercial_price='3899.00', confirmed_quantity=37, lead_time_days=5,
                    valid_from=timezone.localdate() - timedelta(days=1), valid_until=timezone.localdate() + timedelta(days=7))

images = {}
for key, colour, rgb in (('grafito', grafito, (59, 59, 61)), ('niebla', niebla, (201, 206, 214))):
    placed = finish_media.add_image(model=model, color_option_id=colour.pk, actor=actor,
                                    uploaded=SimpleUploadedFile(key + '.png', png(rgb), content_type='image/png'),
                                    provenance={'source_kind': 'own_photo'}, alt_text=name + ' ' + colour.label)
    images[key] = [placed.image_url.rstrip('/').rsplit('/', 1)[-1]]
out({'slug': model.slug, 'name': name, 'branch': branch.pk, 'a': a, 'b': b, 'c': c, 'd': d,
     'supplier': supplier.name, 'images': images})
`, { imeis: [imei(1), imei(2), imei(3)] });
}

function imei(n: number): string {
  const body = `35${String(Date.now()).slice(-8)}${String(n + 70).padStart(4, "0")}`;
  let total = 0;
  for (let index = 0; index < body.length; index += 1) {
    let digit = Number(body[body.length - 1 - index]);
    if (index % 2 === 0) { digit *= 2; if (digit > 9) digit -= 9; }
    total += digit;
  }
  return body + String((10 - (total % 10)) % 10);
}

/** The pictures of the stage, the price and the availability row of the configurator. */
const stage = (page: Page) => page.getByTestId("v4-stage");
const price = (page: Page) => page.getByTestId("v4-price");
const availability = (page: Page) =>
  page.getByTestId("v4-summary").locator("dt", { hasText: "Disponibilidad" }).locator("xpath=following-sibling::dd[1]");
const radio = (page: Page, group: RegExp | string, name: RegExp) =>
  page.getByRole("radiogroup", { name: group }).getByRole("radio", { name });

async function declineConsent(page: Page) {
  const reject = page.getByRole("button", { name: /Rechazar/ });
  if (await reject.count()) await reject.first().click();
}

async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

/** With `E2E_SCREENSHOTS`: the same page at 1280 and 375, light and dark, then back to 1280 light. */
async function shotMatrix(page: Page, name: string) {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
      // A long variant name (a reference model's «… 256 GB Azul profundo Dual nano-SIM …») never widens the page.
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${name}: desborde horizontal a ${width} px`).toBeLessThanOrEqual(1);
      await shot(page, `${name}-${width}-${scheme === "light" ? "claro" : "oscuro"}`);
    }
  }
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 900 });
}

test.describe.configure({ mode: "serial" });

/**
 * The gateway rows this local database held before the test (Django's own serialization, ciphertext as stored:
 * never decrypted, never printed). Review of #377: the test puts them back exactly as it found them.
 */
let gatewaySnapshot = "[]";

test.beforeAll(() => {
  test.setTimeout(120_000);
  gatewaySnapshot = shell<{ rows: string }>(`${GATEWAY}
from django.core import serializers
out({'rows': serializers.serialize('json', IntegrationConfig.objects.filter(provider='izipay_checkout'))})
`).rows;
  shell(`${GATEWAY}
from store.integrations import registry, service
# Only for the test's run: the rows above come back in afterAll.
IntegrationConfig.objects.filter(provider='izipay_checkout').delete()
master = get_user_model().objects.filter(is_superuser=True).order_by('pk').first()
provider = registry.get('izipay_checkout')
draft = service.save_draft(provider, None, actor=master,
    public={'environment': 'sandbox', 'merchant_code': KEYS['IZIPAY_MERCHANT_CODE'], 'public_key': KEYS['IZIPAY_PUBLIC_KEY'],
            'token_url': KEYS['IZIPAY_TOKEN_URL'], 'currency': 'PEN'},
    secrets={'api_key': KEYS['IZIPAY_API_KEY'], 'hash_key': KEYS['IZIPAY_HASH_KEY']})
IntegrationConfig.objects.filter(pk=draft.pk).update(validated=True, last_test_status='unverified', last_tested_at=timezone.now())
service.activate(provider, None, actor=master, version=draft.version, payload={'acknowledge': service.INTERRUPT_WORD})
out({'ok': True})
`);
  seed = seedShop();
});

test.afterAll(() => {
  // The gateway rows are put back exactly as they were before the test (its own row goes). The paid order stays:
  // a sale moved stock and the Kardex keeps it.
  shell(`${GATEWAY}
from django.core import serializers
IntegrationConfig.objects.filter(provider='izipay_checkout').delete()
restored = 0
for row in serializers.deserialize('json', data['rows']):
    row.save()
    restored += 1
out({'restored': restored})
`, { rows: gatewaySnapshot });
});

test("compra web: ficha con ?variante= → precio y foto por variante → carrito → checkout → pago firmado → pedido con ese equipo", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  // The gateway's own form: a stub that closes at once, as when the buyer finishes paying. What it says is NOT
  // a confirmation (the success page asks the backend), so this changes nothing the test asserts.
  await page.route(SDK, (route) => route.fulfill({
    contentType: "application/javascript",
    body: "window.Izipay = class { constructor() {} LoadForm(args) { setTimeout(() => args.callbackResponse({ code: '00' }), 50); } };",
  }));

  // 1. The address opens the variant it names, already in the first HTML.
  await page.goto(`/modelo/${seed.slug}?variante=${seed.c.slug}`, { waitUntil: "networkidle" });
  await declineConsent(page);
  await expect(page.getByRole("heading", { level: 1, name: seed.name })).toBeVisible({ timeout: 60_000 });
  await expect(radio(page, "Capacidad", /^256 GB/)).toHaveAttribute("aria-checked", "true");
  await expect(price(page)).toHaveText("S/ 3799.00");
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", new RegExp(`/modelo/${seed.slug}$`));

  // 2. The price follows the variant: capacity alone, then colour alone.
  await radio(page, "Capacidad", /^128 GB/).click();
  await expect(price(page)).toHaveText("S/ 3299.00");
  await expect(availability(page)).toHaveText("En stock");
  await expect(page).toHaveURL(new RegExp(`variante=${seed.a.slug}$`));
  await expect(stage(page).getByRole("img", { name: `${seed.name} Grafito` })).toBeVisible();
  // G: each colour chip shows its own finish picture (never the importer's grey dot), with its name.
  for (const [colour, key] of [["Grafito", "grafito"], ["Niebla", "niebla"]] as const) {
    const chipPicture = radio(page, /^Color/, new RegExp(`^${colour}`)).getByTestId("v4-chip-picture").locator("img");
    await expect(chipPicture).toHaveAttribute("srcset", new RegExp(seed.images[key][0]));
  }

  // 3. A colour with no own stock and no offer: «Agotado», nothing to buy, its own photo.
  await radio(page, /^Color/, /^Niebla/).click();
  await expect(price(page)).toHaveText("S/ 3399.00");
  await expect(availability(page)).toHaveText("Agotado");
  await expect(page.getByRole("button", { name: "Agotado" })).toBeDisabled();
  await expect(stage(page).getByRole("img", { name: `${seed.name} Niebla` })).toBeVisible();
  await expect(page.getByText(/en stock/i)).toHaveCount(0);
  await shot(page, "web-01-agotado-1280");

  // 3b. The same colour in 256 GB: no own stock but a purchasable offer — «Bajo pedido», the offer's price and
  //     lead time; never «En stock», never the supplier's 37 units, cost or name (page and public API).
  await radio(page, "Capacidad", /^256 GB/).click();
  await expect(price(page)).toHaveText("S/ 3899.00");
  await expect(availability(page)).toHaveText("Bajo pedido — entrega en 5 días");
  await expect(page.getByRole("button", { name: "Pedir bajo pedido" })).toBeEnabled();
  await expect(page.getByText(/en stock/i)).toHaveCount(0);
  const text = await page.locator('[data-v4-scope="block"]').innerText();
  for (const leak of ["37", "2811", seed.supplier]) expect(text, `la página dice «${leak}»`).not.toContain(leak);
  const publicModel = await (await page.request.get(`${API}/catalog/models/${seed.slug}/`)).text();
  // `"availability":"supplier"` is the documented kind of availability; a supplier KEY, quantity or cost is a leak.
  for (const leak of ["2811", seed.supplier, "confirmed_quantity", "committed_quantity", "agreed_cost", "\"supplier\":", "quantity"]) {
    expect(publicModel, `la API pública dice «${leak}»`).not.toContain(leak);
  }
  const onOrder = (JSON.parse(publicModel).variants as { product_id: number; on_order: Record<string, unknown> | null }[])
    .find((row) => row.product_id === seed.d.id)!.on_order;
  expect(onOrder, "bajo pedido: sólo plazo y precio").toEqual({ lead_time_days: 5, price: "3899.00" });
  await shot(page, "web-01b-bajo-pedido-1280");
  // Back to 128 GB: Niebla is sold out there, so the colour (read after the capacity) moves to the nearest one
  // that can be bought and the page says so (D-PRD-3).
  await radio(page, "Capacidad", /^128 GB/).click();
  await expect(page.getByTestId("v4-choice-adjusted")).toContainText("Grafito");
  await expect(availability(page)).toHaveText("En stock");

  // 4. Back to Grafito and into the real cart.
  await radio(page, /^Color/, /^Grafito/).click();
  await expect(price(page)).toHaveText("S/ 3299.00");
  await page.getByRole("button", { name: "Agregar al carrito" }).click();
  await expect(page.getByText("Agregado al carrito.")).toBeVisible({ timeout: 20_000 });
  await shot(page, "web-02-agregado-1280");

  await page.goto("/cart", { waitUntil: "networkidle" });
  await expect(page.getByText(seed.a.name).first()).toBeVisible({ timeout: 30_000 });
  // G: the line shows its colour's finish (the model page's rule), not the name's initial; a NEW device says no
  // battery, only its IMEI ending.
  const cartPicture = page.getByTestId("cart-thumb").first().locator("img");
  await expect(cartPicture).toHaveAttribute("src", new RegExp(seed.images.grafito[0]));
  const device = page.getByRole("list", { name: `Equipos de ${seed.a.name}` }).getByRole("listitem").first();
  await expect(device).toContainText("IMEI termina en");
  await expect(device).not.toContainText("Batería");
  await shotMatrix(page, "web-03-carrito");
  await page.getByRole("link", { name: "Continuar al checkout" }).click();
  await page.waitForURL(/\/checkout$/);
  await expect(page.getByTestId("cart-thumb").first().locator("img")).toHaveAttribute("src", new RegExp(seed.images.grafito[0]));

  // 5. Checkout: the backend's own view creates the order; the gateway is the tests' fake, never the network.
  let created: { status: number; body: Record<string, unknown> } | null = null;
  await page.route("**/api/payments/create-checkout-session/", async (route) => {
    created = shell<{ status: number; body: Record<string, unknown> }>(`${GATEWAY}
import json
from django.test import Client, override_settings
from store.payments.fake_izipay import FakeIzipay
# TEST keys open a payment for a guest only where the installation says so (PAYMENTS_PUBLIC_TEST, a developer's
# machine): said here, for this one request of the test, not for the server.
with override_settings(PAYMENTS_PUBLIC_TEST=True), FakeIzipay.from_settings(KEYS).online():
    res = Client().post('/api/payments/create-checkout-session/', data=json.dumps(data['body']),
                        content_type='application/json', HTTP_HOST='localhost')
out({'status': res.status_code, 'body': res.json()})
`, { body: route.request().postDataJSON() });
    await route.fulfill({ status: created.status, contentType: "application/json", body: JSON.stringify(created.body) });
  });
  await page.locator("#checkout-customer-name").fill("Compradora De Prueba");
  await page.locator("#checkout-customer-email").fill("compradora@example.invalid");
  await page.locator("#checkout-customer-phone").fill("987654321");
  await page.locator("#checkout-document-number").fill("44556677");
  await shotMatrix(page, "web-04b-checkout");
  for (const box of await page.locator('form input[type="checkbox"]').all()) await box.check();
  await shot(page, "web-04-checkout-1280");
  await page.getByRole("button", { name: /Continuar al pago/ }).click();
  await expect.poll(() => created, { message: "el checkout no llegó al backend" }).not.toBeNull();
  expect(created!.status, JSON.stringify(created!.body)).toBe(200);
  const session = created!.body as { order_id: number; transaction_id: string };

  // 6. The form closed: the success page asks the backend, which has not seen a signed payment yet.
  await page.waitForURL(/\/checkout\/success$/, { timeout: 30_000 });
  await expect(page.getByText(/verificando|pendiente|procesando/i).first()).toBeVisible({ timeout: 30_000 });
  const pending = shell<{ status: string; units: number }>(`
from store.models import Order, OrderUnit
order = Order.objects.get(pk=data['order'])
out({'status': order.status, 'units': OrderUnit.objects.filter(order=order).count()})
`, { order: session.order_id });
  expect(pending.status).toBe("pending_payment");

  // 7. The gateway confirms with its signature.
  const confirmed = shell<{ status: number }>(`${GATEWAY}
import json, os, urllib.request
from store.models import PaymentTransaction
from store.payments.fake_izipay import FakeIzipay
attempt = PaymentTransaction.objects.get(transaction_id=data['reference'])
fake = FakeIzipay.from_settings(KEYS)
envelope = fake.envelope(fake.payload(transaction_id=attempt.transaction_id, order_number=attempt.order_number, amount=attempt.amount))
request = urllib.request.Request(data['api'] + '/payments/izipay/notification/', data=json.dumps(envelope).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(request, timeout=20) as response:
    out({'status': response.status})
`, { reference: session.transaction_id, api: API });
  expect(confirmed.status).toBe(200);
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.getByText(/registrada como pagada/)).toBeVisible({ timeout: 30_000 });
  await shot(page, "web-05-pagado-1280");

  // 8. The order holds THAT device of THAT variant; the Kardex tells the sale; one device less at the branch.
  const after = shell<{
    status: string; items: number[]; units: { product: number; method: string; status: string }[];
    exits: number; here: number; available: number;
  }>(`
from store import inventory_services as inventory
from store.models import Branch, Order, OrderUnit, Product, StockMovement, StockUnit
order = Order.objects.get(pk=data['order'])
product = Product.objects.get(pk=data['a'])
branch = Branch.objects.get(pk=data['branch'])
records = list(OrderUnit.objects.filter(order=order).select_related('unit'))
out({'status': order.status, 'items': list(order.items.values_list('product_id', flat=True)),
     'units': [{'product': r.unit.product_id, 'method': r.method, 'status': r.unit.status} for r in records],
     'exits': StockMovement.objects.filter(product=product, movement_type=StockMovement.SALE_EXIT, order=order).count(),
     'here': inventory.branch_quantity(branch, product),
     'available': StockUnit.objects.filter(product=product, branch=branch, status='available').count()})
`, { order: session.order_id, a: seed.a.id, branch: seed.branch });
  expect(after.status).toBe("paid");
  expect(after.items).toEqual([seed.a.id]);
  expect(after.units).toEqual([{ product: seed.a.id, method: "buyer", status: "sold" }]);
  expect(after.exits).toBe(1);
  expect(after.here).toBe(1);
  expect(after.available).toBe(1);
});

test("rendimiento a 375 px: sin saltos al cargar ni al cambiar de color, la foto es el LCP y sólo se piden las del acabado", async ({ browser }) => {
  test.setTimeout(120_000);
  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  await page.addInitScript(() => {
    const scope = window as unknown as { __cls: number; __lcp: { tag: string; alt: string; inStage: boolean } | null };
    scope.__cls = 0;
    scope.__lcp = null;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as (PerformanceEntry & { value: number; hadRecentInput: boolean })[]) {
        if (!entry.hadRecentInput) scope.__cls += entry.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
    new PerformanceObserver((list) => {
      const entries = list.getEntries() as (PerformanceEntry & { element: Element | null })[];
      const last = entries.at(-1);
      const element = last?.element ?? null;
      scope.__lcp = element ? {
        tag: element.tagName, alt: element.getAttribute("alt") ?? "",
        inStage: Boolean(element.closest('[data-testid="v4-stage"]')),
      } : null;
    }).observe({ type: "largest-contentful-paint", buffered: true });
  });
  const pictures: { url: string; bytes: number }[] = [];
  page.on("requestfinished", async (request: Request) => {
    if (request.resourceType() !== "image") return;
    const sizes = await request.sizes().catch(() => null);
    pictures.push({ url: decodeURIComponent(request.url()), bytes: sizes?.responseBodySize ?? 0 });
  });

  await page.goto(`/modelo/${seed.slug}?variante=${seed.a.slug}`, { waitUntil: "networkidle" });
  await expect(stage(page).getByRole("img", { name: `${seed.name} Grafito` })).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(1500);

  const load = await page.evaluate(() => {
    const scope = window as unknown as { __cls: number; __lcp: { tag: string; alt: string; inStage: boolean } | null };
    return { cls: scope.__cls, lcp: scope.__lcp,
             overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth };
  });
  expect(load.overflow, "desborde horizontal a 375 px").toBeLessThanOrEqual(1);
  expect(load.cls, "CLS al cargar").toBeLessThan(0.1);
  expect(load.lcp, "el LCP es la foto principal del acabado elegido").toEqual({ tag: "IMG", alt: `${seed.name} Grafito`, inStage: true });
  const finish = (key: "grafito" | "niebla") => pictures.filter((p) => seed.images[key].some((id) => p.url.includes(id)));
  expect(finish("grafito").length, "la foto del acabado elegido").toBeGreaterThan(0);
  // G (owner's visual order 4): another colour's finish is requested only as its chip's picture, at chip size.
  const width = (url: string) => Number(/[?&]w=(\d+)/.exec(url)?.[1] ?? "100000");
  expect(finish("niebla").filter((p) => width(p.url) > 96), "una foto grande de otro color pedida sin elegirlo").toEqual([]);
  const bytes = finish("grafito").reduce((sum, p) => sum + p.bytes, 0);
  expect(bytes, "bytes de imagen del acabado elegido (optimizador AVIF/WebP)").toBeLessThan(250_000);
  await declineConsent(page);
  await shot(page, "web-06-ficha-375");

  // Choosing another colour moves nothing on the page.
  await page.evaluate(() => { (window as unknown as { __cls: number }).__cls = 0; });
  await radio(page, /^Color/, /^Niebla/).click();
  await expect(stage(page).getByRole("img", { name: `${seed.name} Niebla` })).toBeVisible();
  await page.waitForTimeout(1500);
  const switched = await page.evaluate(() => (window as unknown as { __cls: number }).__cls);
  expect(switched, "desplazamiento al cambiar de color").toBeLessThan(0.01);
  expect(finish("niebla").length).toBeGreaterThan(0);
  await shot(page, "web-07-niebla-375");
  await context.close();
});
