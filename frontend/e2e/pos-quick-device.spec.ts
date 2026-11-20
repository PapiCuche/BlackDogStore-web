import { execFileSync } from "node:child_process";
import { test, expect, type Page } from "@playwright/test";

/**
 * CATALOG-REFERENCE-01 · PR F — venta rápida de equipos en caja, en navegador real.
 *
 * SIN MOCKS DEL BACKEND. Un modelo con su paleta (capacidad, color, SIM, región) y un par imposible (Chip × EE. UU.)
 * entra a la empresa del backend local (`E2E_BACKEND_DIR`) con UNA variante existente. En la caja:
 *
 *   · se busca el modelo y se elige capacidad → color → SIM → región; el par imposible no se puede elegir;
 *   · «Llegó el equipo» registra un equipo de una combinación que aún no es variante (IMEI 1, IMEI 2, precio)
 *     sin abrir el panel; el equipo entra a la cesta identificado y se cobra;
 *   · el pedido guarda ESE equipo, identificado a mano por quien vendió.
 *
 * Con `E2E_SCREENSHOTS=<carpeta>` deja capturas a 1280 y 375 (fuera del repositorio).
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? "../backend";
const SHOTS = process.env.E2E_SCREENSHOTS ?? "";
const MODELO = `Equipo rápido ${RUN}`;

function imei(seed: number): string {
  const body = `35${String(Date.now()).slice(-8)}${String(seed).padStart(4, "0")}`;
  let total = 0;
  for (let index = 0; index < body.length; index += 1) {
    let digit = Number(body[body.length - 1 - index]);
    if (index % 2 === 0) { digit *= 2; if (digit > 9) digit -= 9; }
    total += digit;
  }
  return body + String((10 - (total % 10)) % 10);
}

const NUEVO = { imei: imei(11), imei2: imei(12) };
const MOVIL = imei(21);
const PROVEEDOR = `Mayorista e2e ${RUN}`;

/** El modelo con su paleta, la exclusión y una variante con dos equipos. */
type Pasos = { capacity: string; color: string; sim: string; region: string };

/** Los nombres que la empresa da a cada rol (una base ya sembrada puede tener los suyos). */
let PASO: Pasos = { capacity: "Capacidad", color: "Color", sim: "SIM", region: "Región" };

function sembrar(): { model: number; supplier: number; steps: Pasos } {
  const codigo = `
import json, os
from decimal import Decimal
from django.contrib.auth import get_user_model
from store import device_catalog, model_palette, stock_unit_services, supply_services
from store.models import Company, Product, VariantAttribute, VariantOption

data = json.loads(os.environ['E2E_SEED'])
actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug='black-dog-store')
branch = company.default_inventory_branch

def attr(role, name, kind='text'):
    found = VariantAttribute.objects.filter(company=company, role=role).first()
    return found or device_catalog.create_attribute(company=company, name=name, role=role, kind=kind)

def opt(attribute, label, swatch=''):
    found = VariantOption.objects.filter(company=company, attribute=attribute, label__iexact=label).first()
    return found or device_catalog.create_option(company=company, attribute_id=attribute.pk, label=label, swatch_hex=swatch)

cap, col, sim, reg = attr('capacity', 'Capacidad'), attr('color', 'Color', 'color'), attr('sim', 'SIM'), attr('region', 'Región')
g128, g256 = opt(cap, '128 GB'), opt(cap, '256 GB')
grafito, hielo = opt(col, 'Grafito', '#3B3B3D'), opt(col, 'Azul hielo', '#9DB8D2')
esim, chip = opt(sim, 'eSIM'), opt(sim, 'Chip')
intl, usa = opt(reg, 'Internacional'), opt(reg, 'EE. UU.')
model = device_catalog.create_model(company=company, name=data['name'])
model_palette.set_palette(model, option_ids=[o.pk for o in (g128, g256, grafito, hielo, esim, chip, intl, usa)], actor=actor)
model_palette.add_exclusions(model, pairs=[(chip.pk, usa.pk)], actor=actor)
product = Product.objects.create(company=company, name=data['name'] + ' 128 GB Grafito eSIM Internacional',
                                 slug=data['slug'], price=Decimal('3299.00'), inventory=0, is_active=True,
                                 is_serialized=True, requires_imei=True, is_published_online=False)
device_catalog.set_variant(product, model_id=model.pk, condition_line='new',
                           option_ids=[g128.pk, grafito.pk, esim.pk, intl.pk])
stock_unit_services.receive_units(branch=branch, product=product, actor=actor, reason='Prueba de navegador',
                                  units=[{'imei': value} for value in data['units']])
supplier = supply_services.create_supplier(company=company, name=data['supplier'], actor=actor)
print('E2E_SEEDED ' + json.dumps({'model': model.pk, 'supplier': supplier.pk, 'steps': {
    'capacity': cap.name, 'color': col.name, 'sim': sim.name, 'region': reg.name}}))
`;
  const out = execFileSync("python3", ["manage.py", "shell", "-c", codigo], {
    cwd: BACKEND_DIR,
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      E2E_SEED: JSON.stringify({ name: MODELO, slug: `equipo-rapido-${RUN}`, units: [imei(1), imei(2)], supplier: PROVEEDOR }),
    },
  }).toString();
  const line = out.split("\n").find((row) => row.startsWith("E2E_SEEDED "));
  if (!line) throw new Error(`la siembra no terminó: ${out.slice(-400)}`);
  return JSON.parse(line.slice("E2E_SEEDED ".length));
}

/** What the database holds for this run's model: variants, devices, Kardex by type, sold devices, shelf. */
type Cuenta = { variants: number; units: number; available: number; sold: number; entries: number; exits: number;
                orderUnits: number; shelf: number; serial: string | null };
function contar(): Cuenta {
  const codigo = `
import json, os
from django.db.models import Sum
from store.models import BranchStock, OrderUnit, Product, StockMovement, StockUnit
data = json.loads(os.environ['E2E_SEED'])
products = Product.objects.filter(product_model__name=data['name'])
units = StockUnit.objects.filter(product__in=products)
moves = StockMovement.objects.filter(product__in=products)
new = units.filter(imei=data['imei']).first()
print('E2E_COUNT ' + json.dumps({
    'variants': products.count(), 'units': units.count(), 'available': units.filter(status='available').count(),
    'sold': units.filter(status='sold').count(),
    'entries': moves.filter(movement_type=StockMovement.PURCHASE_ENTRY).count(),
    'exits': moves.filter(movement_type=StockMovement.SALE_EXIT).count(),
    'orderUnits': OrderUnit.objects.filter(unit__in=units).count(),
    'shelf': BranchStock.objects.filter(product__in=products).aggregate(n=Sum('quantity'))['n'] or 0,
    'serial': new.serial_number if new else None}))
`;
  const out = execFileSync("python3", ["manage.py", "shell", "-c", codigo], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, E2E_SEED: JSON.stringify({ name: MODELO, imei: NUEVO.imei }) },
  }).toString();
  const line = out.split("\n").find((row) => row.startsWith("E2E_COUNT "));
  if (!line) throw new Error(`el conteo no terminó: ${out.slice(-400)}`);
  return JSON.parse(line.slice("E2E_COUNT ".length));
}

const SERIE = `F2Q${RUN.toUpperCase().slice(-7)}`;

async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  const use = card.locator("li").filter({ hasText: "dev_admin" }).getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) test.skip(true, "dev_admin no existe: siembra con seed_demo_users");
  await use.click();
  for (let intento = 0; intento < 3; intento++) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 12_000 });
      return;
    } catch {
      if (intento === 2) throw new Error("no se pudo iniciar sesión");
      await page.waitForTimeout(62_000);
    }
  }
}

async function pedido(page: Page, id: number) {
  return page.evaluate(async (raw) => {
    const url = new URL(raw, window.location.origin);
    url.hostname = window.location.hostname;
    const res = await fetch(url.toString(), { credentials: "include" });
    return { status: res.status, body: await res.json().catch(() => null) };
  }, `${process.env.E2E_API_BASE ?? "/api"}/admin/orders/${id}/`);
}

async function abrirCaja(page: Page) {
  await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });
  await expect(page.getByLabel("Escanear código"), "el punto de venta no cargó").toBeVisible({ timeout: 30_000 });
}

/** Elige una opción de un paso por su texto (la opción es un radio dentro de su etiqueta). */
async function elegir(page: Page, paso: string, opcion: string) {
  await page.getByRole("radiogroup", { name: paso }).locator("label").filter({ hasText: opcion }).first().click();
}

async function abrirModelo(page: Page) {
  await page.getByRole("button", { name: "Vender equipo por modelo" }).click();
  await page.getByLabel("Modelo", { exact: true }).fill(MODELO);
  await page.getByRole("button", { name: new RegExp(MODELO) }).click();
  await expect(page.getByRole("radiogroup", { name: PASO.capacity })).toBeVisible({ timeout: 20_000 });
}

async function captura(page: Page, nombre: string) {
  if (!SHOTS) return;
  await page.screenshot({ path: `${SHOTS}/${nombre}.png`, fullPage: true });
}

test.describe("venta rápida de equipos en caja", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test.beforeAll(() => { PASO = sembrar().steps; });

  test("el par imposible no se elige; un equipo nuevo se registra, entra a la venta y se cobra", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await signIn(page);
    await abrirCaja(page);
    await abrirModelo(page);

    // La variante existente dice su stock propio aquí.
    await elegir(page, PASO.capacity, "128 GB");
    await elegir(page, PASO.color, "Grafito");
    await elegir(page, PASO.sim, "eSIM");
    await elegir(page, PASO.region, "Internacional");
    await expect(page.getByRole("region", { name: "Equipo elegido" }).getByText("2 en esta sucursal")).toBeVisible();
    await captura(page, "1280-variante-existente");

    // Chip × EE. UU. no existe.
    await elegir(page, PASO.sim, "Chip");
    await expect(page.getByRole("radiogroup", { name: PASO.region }).getByRole("radio", { name: /^EE\. UU\./ })).toBeDisabled();

    // Una combinación que aún no es variante: llega el equipo.
    await elegir(page, PASO.capacity, "256 GB");
    await elegir(page, PASO.color, "Azul hielo");
    await elegir(page, PASO.sim, "eSIM");
    await elegir(page, PASO.region, "Internacional");
    await page.getByRole("button", { name: "Llegó el equipo" }).click();
    await page.getByLabel("IMEI 1").fill(NUEVO.imei.slice(0, -1) + String((Number(NUEVO.imei.slice(-1)) + 1) % 10));
    await page.getByLabel("IMEI 2 (si lo tiene)").fill(NUEVO.imei2);
    await page.getByLabel("Precio de venta").fill("3999.00");
    await page.getByRole("button", { name: "Registrar y agregar a la venta" }).click();
    await expect(page.getByText(/no es válido/).first()).toBeVisible();
    await captura(page, "1280-llego-el-equipo-error");
    await page.getByLabel("IMEI 1").fill(NUEVO.imei);
    await page.getByLabel("Serie (si la tiene)").fill(SERIE);
    const antes = contar();
    await page.getByRole("button", { name: "Registrar y agregar a la venta" }).click();
    await expect(page.getByText(/equipo registrado y agregado a la venta/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(new RegExp(`IMEI ${NUEVO.imei}`)).first()).toBeVisible();
    await expect(page.getByText(/Falta identificar/)).toHaveCount(0);
    // La combinación ya es una variante, con ese equipo en el estante hasta que se cobre.
    await expect(page.getByRole("region", { name: "Equipo elegido" }).getByText("1 en esta sucursal")).toBeVisible({ timeout: 20_000 });
    await captura(page, "1280-en-la-venta");

    await page.getByRole("checkbox").first().check();
    await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
    await page.getByLabel("Efectivo recibido").fill("99999");
    const boton = page.getByRole("button", { name: /^cobrar$/i });
    await expect(boton).toBeEnabled({ timeout: 15_000 });
    await boton.click();
    await expect(page.getByText(/Venta registrada/i), "la venta no se completó").toBeVisible({ timeout: 40_000 });
    const orden = Number(/Pedido #(\d+)/.exec(await page.locator("body").innerText())?.[1]);

    const { body } = await pedido(page, orden);
    const equipos = body.items.flatMap((item: { units: unknown[] }) => item.units);
    expect(equipos).toHaveLength(1);
    expect(equipos[0]).toMatchObject({ imei: NUEVO.imei, imei2: NUEVO.imei2, method: "manual", identified_by: "dev_admin" });
    expect(body.items[0].price).toBe("3999.00");

    // G (owner's priority 5): what the till wrote, counted in the database before «Llegó el equipo» and after
    // the sale — one new variant, one device with its IMEI and serial, one purchase entry and one sale exit in the
    // Kardex, one device assigned to the order; the shelf is back where it was (in and out).
    const despues = contar();
    console.log(`POS e2e · antes ${JSON.stringify(antes)} · después ${JSON.stringify(despues)}`);
    expect(despues.variants - antes.variants).toBe(1);
    expect(despues.units - antes.units).toBe(1);
    expect(despues.entries - antes.entries).toBe(1);
    expect(despues.exits - antes.exits).toBe(1);
    expect(despues.sold - antes.sold).toBe(1);
    expect(despues.orderUnits - antes.orderUnits).toBe(1);
    expect(despues.available).toBe(antes.available);
    expect(despues.shelf).toBe(antes.shelf);
    expect(despues.serial).toBe(SERIE);
  });

  test("en un teléfono el selector y el formulario caben sin desborde", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await signIn(page);
    await abrirCaja(page);
    await abrirModelo(page);
    await elegir(page, PASO.capacity, "256 GB");
    await elegir(page, PASO.color, "Grafito");
    await elegir(page, PASO.sim, "eSIM");
    await elegir(page, PASO.region, "Internacional");
    await page.getByRole("button", { name: "Llegó el equipo" }).click();
    await expect(page.getByLabel("IMEI 1")).toBeFocused();
    const ancho = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(ancho).toBeLessThanOrEqual(0);

    // D-F-10: quien recibe elige el proveedor en el mismo formulario.
    await page.getByLabel("IMEI 1").fill(MOVIL);
    await page.getByLabel("Proveedor (opcional)").selectOption({ label: PROVEEDOR });
    await page.getByLabel("Precio de venta").fill("3899.00");
    await page.getByRole("region", { name: "Equipo elegido" }).scrollIntoViewIfNeeded();
    await captura(page, "375-llego-el-equipo");
    await page.getByRole("button", { name: "Registrar y agregar a la venta" }).click();
    await expect(page.getByText(/equipo registrado y agregado a la venta/)).toBeVisible({ timeout: 30_000 });
    const equipos = await page.evaluate(async (raw) => {
      const url = new URL(raw, window.location.origin);
      url.hostname = window.location.hostname;
      const res = await fetch(url.toString(), { credentials: "include" });
      return (await res.json()).results;
    }, `${process.env.E2E_API_BASE ?? "/api"}/admin/inventory/units/?search=${MOVIL}`);
    expect(equipos).toHaveLength(1);
    expect(equipos[0].supplier).toMatchObject({ name: PROVEEDOR });
    await captura(page, "375-en-la-venta");
  });
});
