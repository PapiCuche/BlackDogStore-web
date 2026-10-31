import { test, expect, devices, type Page } from "@playwright/test";
import { shell, signIn, RUN, imei } from "./inventory-first-support";

/**
 * INV-COM-01C — un seminuevo se vende en caja a SU precio, en navegador real y con el servidor de Django real
 * (guarda de base desechable de `inventory-first-guard`). Datos inventados.
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3031 E2E_API_BASE=http://127.0.0.1:8591/api \
 *     npx playwright test e2e/pos-unit-price.spec.ts
 *
 * A · dos equipos de una misma variante con precios distintos: cada línea lleva su precio y su ficha, el total es la
 *     suma, la venta saca DOS equipos reales y conserva los dos precios;
 * B · un equipo sin precio no entra a la venta y dice qué hacer;
 * C · un precio que cambió después de la vista previa no se cobra en silencio: avisa, muestra el total nuevo y se
 *     cobra al volver a pulsar;
 * D · el mismo recorrido en un móvil de 390 px, sin desborde.
 */

const TAG = `Qpu${RUN}`;
const MODEL = `iPhone 15 ${TAG} 128 GB Seminuevo`;
const U1 = { serial: `PU${RUN}1`.toUpperCase(), imei: imei(11), price: "2100.00", cost: "1400.00" };
const U2 = { serial: `PU${RUN}2`.toUpperCase(), imei: imei(12), price: "2450.00", cost: "1600.00" };
const U3 = { serial: `PU${RUN}3`.toUpperCase(), imei: imei(13) };                 // sin precio
const U4 = { serial: `PU${RUN}4`.toUpperCase(), imei: imei(14), price: "1800.00", cost: "1000.00" };
const U5 = { serial: `PU${RUN}5`.toUpperCase(), imei: imei(15), price: "1500.00", cost: "900.00" };

function sembrar() {
  return shell<{ product: number }>(`
from decimal import Decimal
from django.contrib.auth import get_user_model
from store import stock_unit_services
from store.models import Company, Product
actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
product = Product.objects.create(
    company=company, name=data['name'], slug=data['name'].lower().replace(' ', '-'), price=Decimal('0.00'), inventory=0,
    is_active=True, is_serialized=True, requires_imei=True, condition_line=Product.CONDITION_USED, is_published_online=False)
rows = []
for u in data['units']:
    row = {'serial_number': u['serial'], 'imei': u['imei'], 'condition': 'used'}
    if u.get('price'): row['sale_price'] = u['price']
    if u.get('cost'): row['cost'] = u['cost']
    if u['serial'].endswith('1'): row['battery_health'] = 88
    rows.append(row)
stock_unit_services.receive_units(branch=branch, product=product, actor=actor, reason='Prueba de navegador', units=rows)
out({'product': product.pk})
`, { name: MODEL, units: [U1, U2, U3, U4, U5] });
}

function estado(serials: string[]) {
  return shell<{ units: Record<string, { status: string; order: number | null }>; exits: { quantity: number; unit_ids: number[] }[]; items: { price: string; unit: string }[] }>(`
from store.models import OrderItem, StockMovement, StockUnit
units = {u.serial_number: {'status': u.status, 'order': u.order_id} for u in StockUnit.objects.filter(serial_number__in=data['serials'])}
orders = {v['order'] for v in units.values() if v['order']}
exits = [{'quantity': m.quantity, 'unit_ids': m.metadata.get('unit_ids', [])}
         for m in StockMovement.objects.filter(order_id__in=orders, movement_type='sale_exit')]
items = [{'price': str(i.price), 'unit': i.stock_unit.serial_number if i.stock_unit_id else ''} for i in OrderItem.objects.filter(order_id__in=orders)]
out({'units': units, 'exits': exits, 'items': items})
`, { serials });
}

function cambiarPrecio(serial: string, price: string) {
  shell(`
from store.models import StockUnit
StockUnit.objects.filter(serial_number=data['serial']).update(sale_price=data['price'])
out({'ok': True})
`, { serial, price });
}

async function abrirCaja(page: Page) {
  await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });
  await expect(page.getByLabel("Escanear código"), "el punto de venta no cargó").toBeVisible({ timeout: 30_000 });
}

async function escanear(page: Page, code: string) {
  const campo = page.getByLabel("Escanear código");
  await campo.click();
  await campo.pressSequentially(code, { delay: 4 });   // un lector: ráfaga y Enter
  await campo.press("Enter");
}

async function prepararCobro(page: Page) {
  await page.getByRole("checkbox").first().check();
  await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
  await page.getByLabel("Efectivo recibido").fill("99999");
}

const dinero = (text: string) => new RegExp(text.replace(/\./g, "\\.").replace(/(\d)(\d{3})\b/, "$1,?$2"));

async function sinDesborde(page: Page, donde: string) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `desborde horizontal en ${donde}`).toBeLessThanOrEqual(1);
}

test.describe("un seminuevo se vende en caja a su precio", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test.beforeAll(() => { sembrar(); });

  test("A · dos equipos de una variante: cada uno con su precio, su ficha y su salida de stock", async ({ page }) => {
    await signIn(page);
    await abrirCaja(page);
    await escanear(page, U1.imei);
    await expect(page.getByText(new RegExp(`IMEI ${U1.imei}`)).first()).toBeVisible({ timeout: 20_000 });
    await escanear(page, U2.imei);
    await expect(page.getByText(new RegExp(`IMEI ${U2.imei}`)).first()).toBeVisible({ timeout: 20_000 });

    // La ficha y el precio de cada equipo, junto a él; ni rastro del costo.
    const fichas = page.locator("[data-unit-ficha]");
    await expect(fichas).toHaveCount(2);
    await expect(fichas.nth(0)).toContainText("batería 88%");
    await expect(fichas.nth(0)).toContainText(dinero("2100.00"));
    await expect(fichas.nth(1)).toContainText(dinero("2450.00"));
    await expect(page.locator("body")).not.toContainText("1400.00");
    await expect(page.locator("body")).not.toContainText("1600.00");
    // El total del servidor es la suma de los dos precios.
    await expect(page.getByText(dinero("4550.00")).first()).toBeVisible({ timeout: 20_000 });

    await prepararCobro(page);
    const boton = page.getByRole("button", { name: /^cobrar$/i });
    await expect(boton).toBeEnabled({ timeout: 15_000 });
    await boton.click();
    await expect(page.getByText(/Venta registrada/i)).toBeVisible({ timeout: 40_000 });

    // El servidor: dos equipos vendidos, UNA salida que los nombra, y los dos precios.
    const seen = estado([U1.serial, U2.serial]);
    expect(Object.values(seen.units).map((u) => u.status)).toEqual(["sold", "sold"]);
    expect(seen.exits).toHaveLength(1);
    expect(seen.exits[0].quantity).toBe(2);
    expect(seen.items.map((i) => [i.unit, i.price]).sort()).toEqual([[U1.serial, "2100.00"], [U2.serial, "2450.00"]]);
  });

  test("B · un equipo sin precio no entra a la venta y dice qué hacer", async ({ page }) => {
    await signIn(page);
    await abrirCaja(page);
    await escanear(page, U3.imei);
    await expect(page.getByText(/todavía no tiene precio de venta\. Solicita que un usuario autorizado lo configure/).first())
      .toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(new RegExp(`IMEI ${U3.imei}`))).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^cobrar$/i })).toBeDisabled();
    expect(estado([U3.serial]).units[U3.serial].status).toBe("available");
  });

  test("C · un precio que cambió después de la vista previa no se cobra en silencio", async ({ page }) => {
    await signIn(page);
    await abrirCaja(page);
    await escanear(page, U4.imei);
    await expect(page.getByText(dinero("1800.00")).first()).toBeVisible({ timeout: 20_000 });
    await prepararCobro(page);
    await expect(page.getByRole("button", { name: /^cobrar$/i })).toBeEnabled({ timeout: 15_000 });

    // La ficha cambia justo entre la vista previa que el cajero leyó y el cobro.
    let cambiado = false;
    await page.route("**/pos/sales/", async (route) => {
      if (!cambiado && route.request().method() === "POST") {
        cambiado = true;
        cambiarPrecio(U4.serial, "1900.00");
      }
      await route.continue();
    });
    await page.getByRole("button", { name: /^cobrar$/i }).click();
    await expect(page.getByText(/El precio cambió/).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(dinero("1900.00")).first()).toBeVisible();
    expect(estado([U4.serial]).units[U4.serial].status).toBe("available"); // nada se cobró
    await page.unroute("**/pos/sales/");

    await page.getByRole("button", { name: /^cobrar$/i }).click();         // ya con el total nuevo a la vista
    await expect(page.getByText(/Venta registrada/i)).toBeVisible({ timeout: 40_000 });
    const seen = estado([U4.serial]);
    expect(seen.items).toEqual([{ price: "1900.00", unit: U4.serial }]);
  });
});

const { defaultBrowserType: _ignored, ...PHONE } = devices["iPhone 13"];
void _ignored;

test.describe("el mismo recorrido en un móvil", () => {
  test.use({ ...PHONE, baseURL: process.env.E2E_BASE_URL });
  test.setTimeout(240_000);

  test("D · 390 px: la línea de un seminuevo cabe y se cobra", async ({ page }) => {
    await signIn(page);
    await abrirCaja(page);
    await escanear(page, U5.imei);
    await expect(page.locator("[data-unit-ficha]")).toContainText(dinero("1500.00"), { timeout: 20_000 });
    await sinDesborde(page, "caja con un seminuevo");
    await prepararCobro(page);
    const boton = page.getByRole("button", { name: /^cobrar$/i });
    await expect(boton).toBeEnabled({ timeout: 15_000 });
    await boton.tap();
    await expect(page.getByText(/Venta registrada/i)).toBeVisible({ timeout: 40_000 });
    expect(estado([U5.serial]).items).toEqual([{ price: "1500.00", unit: U5.serial }]);
  });
});
