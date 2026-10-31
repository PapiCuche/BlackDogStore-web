import { test, expect, devices, type Page } from "@playwright/test";
import { shell, signIn, RUN, imei } from "./inventory-first-support";

/**
 * SALES-UX-02 — el recorrido de la caja en navegador real, con el Django real (guarda de base desechable).
 * Productos → Cliente → Forma de pago → Comprobante → Revisar → Cobrar. Datos inventados.
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3033 E2E_API_BASE=http://127.0.0.1:8593/api \
 *     npx playwright test e2e/sales-ux-02.spec.ts
 *
 * A · escritorio 1280: la barra de pasos, el orden de las secciones, la revisión con el equipo (IMEI enmascarado, su
 *     precio, su batería), el accesorio, el documento interno rotulado «no fiscal», «parte de pago» deshabilitado y la
 *     venta se cobra con lo que dijo la revisión;
 * B · «Nuevo cliente» sin salir de la caja: la cesta sigue, el cliente queda elegido, un documento repetido ofrece al existente;
 * C · boleta y factura aparecen «no operativas» mientras la emisión electrónica está apagada, y la nota interna no se presenta como fiscal;
 * D · tableta 768 y móvil 390: sin desborde, los pasos se alcanzan y se cobra.
 */

const TAG = `Qsx${RUN}`;
const DNI = String(Date.now()).slice(-8);
const PHONE = `iPhone 14 ${TAG} 128 GB Seminuevo`;
const CASE = `Funda ${TAG}`;
const U1 = { serial: `SX${RUN}1`.toUpperCase(), imei: imei(21), price: "1950.00", cost: "1200.00" };
const U2 = { serial: `SX${RUN}2`.toUpperCase(), imei: imei(22), price: "2050.00", cost: "1300.00" };
const U3 = { serial: `SX${RUN}3`.toUpperCase(), imei: imei(23), price: "1700.00", cost: "1000.00" };
const U4 = { serial: `SX${RUN}4`.toUpperCase(), imei: imei(24), price: "1600.00", cost: "900.00" };
const U5 = { serial: `SX${RUN}5`.toUpperCase(), imei: imei(25), price: "1650.00", cost: "950.00" };

function sembrar() {
  return shell<{ product: number }>(`
from decimal import Decimal
from django.contrib.auth import get_user_model
from store import stock_unit_services, inventory_services
from store.models import Company, Product, StockMovement
actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
phone = Product.objects.create(
    company=company, name=data['phone'], slug=data['phone'].lower().replace(' ', '-'), price=Decimal('0.00'), inventory=0,
    is_active=True, is_serialized=True, requires_imei=True, condition_line=Product.CONDITION_USED, is_published_online=False)
rows = []
for u in data['units']:
    row = {'serial_number': u['serial'], 'imei': u['imei'], 'condition': 'used', 'sale_price': u['price'], 'cost': u['cost']}
    if u['serial'].endswith('1'): row['battery_health'] = 91
    rows.append(row)
stock_unit_services.receive_units(branch=branch, product=phone, actor=actor, reason='Prueba de navegador', units=rows)
case = Product.objects.create(company=company, name=data['case'], slug=data['case'].lower().replace(' ', '-'),
                              price=Decimal('49.90'), inventory=0, is_active=True, is_published_online=False)
inventory_services.create_stock_movement(branch=branch, product_id=case.pk, movement_type=StockMovement.INITIAL_STOCK,
                                         quantity=50, reason='Prueba de navegador', actor=actor)
out({'product': phone.pk})
`, { phone: PHONE, case: CASE, units: [U1, U2, U3, U4, U5], slug: "black-dog-store" });
}

function estado(serials: string[]) {
  return shell<{ units: Record<string, string>; customers: string[] }>(`
from store.models import Customer, StockUnit
units = {u.serial_number: u.status for u in StockUnit.objects.filter(serial_number__in=data['serials'])}
out({'units': units, 'customers': [c.display_name for c in Customer.objects.filter(last_name__startswith=data['tag'])]})
`, { serials, tag: TAG });
}

async function abrirCaja(page: Page) {
  await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });
  await expect(page.getByLabel("Escanear código"), "el punto de venta no cargó").toBeVisible({ timeout: 30_000 });
}

async function escanear(page: Page, code: string) {
  const campo = page.getByLabel("Escanear código");
  await campo.click();
  await campo.pressSequentially(code, { delay: 4 });
  await campo.press("Enter");
}

async function sinDesborde(page: Page, donde: string) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `desborde horizontal en ${donde}`).toBeLessThanOrEqual(1);
}

async function agregarAccesorio(page: Page) {
  await page.getByLabel("Buscar producto").fill(CASE);
  await page.getByRole("button", { name: new RegExp(CASE) }).first().click();
}

const dinero = (text: string) => new RegExp(text.replace(/\./g, "\\.").replace(/(\d)(\d{3})\b/, "$1,?$2"));

test.describe("el recorrido de la caja", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);
  test.beforeAll(() => { sembrar(); });

  test("A · pasos, revisión y cobro en escritorio", async ({ page }) => {
    await signIn(page);
    await abrirCaja(page);
    await escanear(page, U1.imei);
    await expect(page.getByText(dinero("1950.00")).first()).toBeVisible({ timeout: 20_000 });
    await agregarAccesorio(page);

    // La barra de pasos y su orden.
    const pasos = page.getByRole("list", { name: "Pasos de la venta" });
    await expect(pasos).toBeVisible();
    await expect(pasos.getByRole("listitem")).toHaveText([/Productos/, /Cliente/, /Forma de pago/, /Comprobante/, /Revisar/]);

    // Las secciones aparecen en el orden de la venta.
    const titulos = await page.locator("h3[id^='pos-step-']").allTextContents();
    expect(titulos.map((t) => t.replace(/\d|\(.*\)/g, "").trim())).toEqual(["Cliente", "Forma de pago", "Comprobante", "Revisar y cobrar"]);

    // La parte de pago se anuncia pero no se puede usar.
    const parte = page.locator("[data-trade-in-placeholder]");
    await expect(parte).toContainText("Aún no disponible");
    await expect(parte.getByRole("button")).toHaveCount(0);

    // Comprobante interno: rotulado, y la revisión lo dice.
    await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
    await expect(page.getByRole("note")).toContainText("Interno · no fiscal");
    await page.getByLabel("Efectivo recibido").fill("99999");
    const revision = page.getByRole("region", { name: "Revisar la venta" });
    await expect(revision).toContainText(PHONE);
    await expect(revision).toContainText(`IMEI ••••${U1.imei.slice(-4)}`);
    await expect(revision).not.toContainText(U1.imei);
    await expect(revision).toContainText("Batería 91 %");
    await expect(revision).toContainText(dinero("1950.00"));
    await expect(revision).toContainText(CASE);
    await expect(revision).toContainText("Venta al público");
    await expect(revision).toContainText("Interno · no fiscal");

    await page.getByRole("checkbox").first().check();
    const cobrar = page.getByRole("button", { name: /^cobrar$/i });
    await expect(cobrar).toBeEnabled({ timeout: 15_000 });
    await cobrar.click();
    await expect(page.getByText(/Venta registrada/i)).toBeVisible({ timeout: 40_000 });
    expect(estado([U1.serial]).units[U1.serial]).toBe("sold");
  });

  test("B · Nuevo cliente sin salir de la caja", async ({ page }) => {
    await signIn(page);
    await abrirCaja(page);
    await escanear(page, U2.imei);
    await expect(page.getByText(dinero("2050.00")).first()).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "Buscar cliente (opcional)" }).click();
    await page.getByRole("button", { name: "Nuevo cliente" }).click();
    const dialogo = page.getByRole("dialog", { name: "Nuevo cliente" });
    await expect(dialogo).toBeVisible();
    await dialogo.getByLabel(/^Nombres?/i).fill("Rosa");
    await dialogo.getByLabel(/^Apellidos?/i).fill(`${TAG}Quispe`);
    await dialogo.getByLabel("Tipo de documento").selectOption("dni");
    await dialogo.getByLabel("Número de documento").fill(DNI);
    await dialogo.getByRole("button", { name: "Crear cliente" }).click();
    await expect(dialogo).toBeHidden({ timeout: 20_000 });

    // El cliente quedó elegido y la cesta sigue intacta.
    await expect(page.getByText(`Rosa ${TAG}Quispe`).first()).toBeVisible();
    await expect(page.getByText(dinero("2050.00")).first()).toBeVisible();
    expect(estado([U2.serial]).units[U2.serial]).toBe("available");
    expect(estado([U2.serial]).customers.length).toBe(1);

    // Esc cierra el diálogo sin crear nada.
    await page.getByRole("button", { name: "Quitar", exact: true }).first().click();
    await page.getByRole("button", { name: "Buscar cliente (opcional)" }).click();
    await page.getByRole("button", { name: "Nuevo cliente" }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Nuevo cliente" })).toBeHidden();
    expect(estado([U2.serial]).customers.length).toBe(1);

    // Un documento que ya identifica a alguien ofrece usar a esa persona, sin duplicarla.
    await page.getByRole("button", { name: "Nuevo cliente" }).click();
    const otra = page.getByRole("dialog", { name: "Nuevo cliente" });
    await otra.getByLabel(/^Nombres?/i).fill("Otra");
    await otra.getByLabel(/^Apellidos?/i).fill(`${TAG}Repetida`);
    await otra.getByLabel("Tipo de documento").selectOption("dni");
    await otra.getByLabel("Número de documento").fill(DNI);
    await otra.getByRole("button", { name: "Crear cliente" }).click();
    await otra.getByRole("button", { name: new RegExp(`^Usar a Rosa ${TAG}Quispe`) }).click();
    await expect(otra).toBeHidden();
    await expect(page.getByText(`Rosa ${TAG}Quispe`).first()).toBeVisible();
    expect(estado([U2.serial]).customers.length).toBe(1);
  });

  test("C · lo fiscal se dice no operativo y lo interno no se presenta como fiscal", async ({ page }) => {
    await signIn(page);
    await abrirCaja(page);
    await escanear(page, U3.imei);
    await expect(page.getByText(dinero("1700.00")).first()).toBeVisible({ timeout: 20_000 });
    const nota = page.getByRole("note");
    await expect(nota).toContainText("Nota de venta interna");
    await expect(nota).toContainText(/Boleta y factura electrónicas/);
    await expect(page.getByLabel("Tipo de comprobante").locator("option", { hasText: /Nota de venta interna/ })).toBeEnabled();
    await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
    await expect(page.getByRole("note")).not.toContainText("Comprobante electrónico");
    await expect(page.getByRole("note")).toContainText("No es un comprobante de pago ante SUNAT");
  });
});

const { defaultBrowserType: _tablet, ...TABLET } = devices["iPad (gen 7)"];
const { defaultBrowserType: _phone, ...PHONE_DEVICE } = devices["iPhone 13"];
void _tablet;
void _phone;

for (const [name, device, unit] of [["tableta 768", TABLET, U4], ["móvil 390", PHONE_DEVICE, U5]] as const) {
  test.describe(`D · ${name}`, () => {
    test.use({ ...device, baseURL: process.env.E2E_BASE_URL });
    test.setTimeout(240_000);

    test(`sin desborde, los pasos se alcanzan y se cobra (${name})`, async ({ page }) => {
      await signIn(page);
      await abrirCaja(page);
      await escanear(page, unit.imei);
      await expect(page.getByText(dinero(unit.price)).first()).toBeVisible({ timeout: 20_000 });
      await sinDesborde(page, `${name}: caja con un equipo`);
      await expect(page.getByRole("list", { name: "Pasos de la venta" })).toBeVisible();
      // Bajo 1024 px (tableta y móvil) la revisión queda lejos del escáner: la barra fija dice cuánto es y lleva a ella.
      const barra = page.locator("[data-pos-mobile-bar]");
      await expect(barra).toBeVisible();
      await expect(barra).toContainText(dinero(unit.price));
      await barra.getByRole("button", { name: "Revisar y cobrar" }).click();
      await expect(page.locator("#pos-step-review")).toBeInViewport({ timeout: 10_000 });
      await page.getByRole("button", { name: /Comprobante/ }).first().click();
      await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
      await page.getByLabel("Efectivo recibido").fill("99999");
      await page.getByRole("checkbox").first().check();
      await sinDesborde(page, `${name}: revisión`);
      await expect(page.getByRole("region", { name: "Revisar la venta" })).toContainText(`IMEI ••••${unit.imei.slice(-4)}`);
      const cobrar = page.getByRole("button", { name: /^cobrar$/i });
      await expect(cobrar).toBeEnabled({ timeout: 15_000 });
      await cobrar.tap();
      await expect(page.getByText(/Venta registrada/i)).toBeVisible({ timeout: 40_000 });
      expect(estado([unit.serial]).units[unit.serial]).toBe("sold");
    });
  });
}
