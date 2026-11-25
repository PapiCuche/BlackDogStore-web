import { execFileSync } from "node:child_process";
import { test, expect, type Page } from "@playwright/test";

/**
 * SCAN-TO-STOCK-01 — «Registrar equipo por escaneo», en navegador real y SIN MOCKS DEL BACKEND.
 *
 * Un modelo con paleta, una variante nueva con su código comercial (GTIN) y una variante de seminuevos entran a la
 * empresa del backend local (`E2E_BACKEND_DIR`). El lector se simula como lo que es: un teclado que escribe el código
 * de golpe y pulsa Enter.
 *
 *   · escritorio (1280): el código de la caja elige la variante; IMEI 1, IMEI 2 y serie se leen seguidos; un EID se
 *     rechaza; una lectura repetida se ignora; un Enter doble no hace nada; nada entra hasta «Confirmar ingreso»; el
 *     Kardex dice «Inventario: registro por escaneo»;
 *   · móvil (390): un seminuevo por búsqueda de modelo, batería, confirmar; queda pendiente de inspección; sin desborde.
 *
 * IMEI sintéticos (Luhn válido). Con `E2E_SCREENSHOTS=<carpeta>` deja capturas (fuera del repositorio).
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? "../backend";
const SHOTS = process.env.E2E_SCREENSHOTS ?? "";
const MODELO = `Equipo escaneo ${RUN}`;

function luhn(body: string): string {
  let total = 0;
  for (let index = 0; index < body.length; index += 1) {
    let digit = Number(body[body.length - 1 - index]);
    if (index % 2 === 0) { digit *= 2; if (digit > 9) digit -= 9; }
    total += digit;
  }
  return body + String((10 - (total % 10)) % 10);
}
const imei = (seed: number) => luhn(`35${String(Date.now()).slice(-8)}${String(seed).padStart(4, "0")}`);
function gtin(body: string): string {
  const total = [...body].reverse().reduce((sum, char, index) => sum + Number(char) * (index % 2 === 0 ? 3 : 1), 0);
  return body + String((10 - (total % 10)) % 10);
}

const CAJA = gtin(`19${String(Date.now()).slice(-9)}`);
// The serial carries a digit: a code of letters only is not surely a serial, and the screen asks.
const NUEVO = { imei: imei(11), imei2: imei(12), serial: `QZ7${RUN.toUpperCase().slice(-7)}` };
const USADO = imei(21);
const EID = `8904${"0".repeat(12)}${String(Date.now()).slice(-13).padStart(16, "7")}`;

function python(code: string, env: Record<string, string>, marker: string) {
  const out = execFileSync("python3", ["manage.py", "shell", "-c", code], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ...env },
  }).toString();
  const line = out.split("\n").find((row) => row.startsWith(`${marker} `));
  if (!line) throw new Error(`${marker} no terminó: ${out.slice(-400)}`);
  return JSON.parse(line.slice(marker.length + 1));
}

function sembrar(): void {
  python(`
import json, os
from decimal import Decimal
from django.contrib.auth import get_user_model
from store import device_catalog, model_palette, supply_services
from store.models import Company, Product, ProductBarcode, VariantAttribute, VariantOption

data = json.loads(os.environ['E2E_SEED'])
actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug='black-dog-store')

def attr(role, name, kind='text'):
    found = VariantAttribute.objects.filter(company=company, role=role).first()
    return found or device_catalog.create_attribute(company=company, name=name, role=role, kind=kind)

def opt(attribute, label, swatch=''):
    found = VariantOption.objects.filter(company=company, attribute=attribute, label__iexact=label).first()
    return found or device_catalog.create_option(company=company, attribute_id=attribute.pk, label=label, swatch_hex=swatch)

cap, col = attr('capacity', 'Capacidad'), attr('color', 'Color', 'color')
g128, grafito = opt(cap, '128 GB'), opt(col, 'Grafito', '#3B3B3D')
model = device_catalog.create_model(company=company, name=data['name'])
model_palette.set_palette(model, option_ids=[g128.pk, grafito.pk], actor=actor)
new = Product.objects.create(company=company, name=data['name'] + ' 128 GB Grafito', slug=data['slug'] + '-n',
                             price=Decimal('3299.00'), inventory=0, is_active=True, is_serialized=True,
                             requires_imei=True, is_published_online=False)
device_catalog.set_variant(new, model_id=model.pk, condition_line='new', option_ids=[g128.pk, grafito.pk])
ProductBarcode.objects.create(company=company, product=new, code=data['box'])
used = Product.objects.create(company=company, name=data['name'] + ' 128 GB Grafito seminuevo', slug=data['slug'] + '-u',
                              price=Decimal('2100.00'), inventory=0, is_active=True, is_serialized=True,
                              requires_imei=True, is_published_online=False)
device_catalog.set_variant(used, model_id=model.pk, condition_line='used', option_ids=[g128.pk, grafito.pk])
supply_services.create_supplier(company=company, name='Mayorista escaneo ' + data['slug'], actor=actor)
print('E2E_SEEDED ' + json.dumps({'model': model.pk}))
`, { E2E_SEED: JSON.stringify({ name: MODELO, slug: `equipo-escaneo-${RUN}`, box: CAJA }) }, "E2E_SEEDED");
}

type Unidad = { exists: boolean; status?: string; condition?: string; imei2?: string; serial?: string;
                inspection?: string; published?: boolean; reason?: string; label?: string };
function unidad(value: string): Unidad {
  return python(`
import json, os
from store.models import StockMovement, StockUnit
unit = StockUnit.objects.filter(imei=os.environ['E2E_IMEI']).first()
if unit is None:
    print('E2E_UNIT ' + json.dumps({'exists': False}))
else:
    move = StockMovement.objects.filter(product=unit.product).order_by('-pk').first()
    print('E2E_UNIT ' + json.dumps({'exists': True, 'status': unit.status, 'condition': unit.condition,
        'imei2': unit.imei2 or '', 'serial': unit.serial_number or '', 'inspection': unit.inspection_status,
        'published': unit.published_online, 'reason': move.reason if move else '', 'label': unit.label_code or ''}))
`, { E2E_IMEI: value }, "E2E_UNIT");
}

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

async function abrir(page: Page) {
  await page.goto("/admin/inventory/scan", { waitUntil: "networkidle" });
  await expect(page.getByTestId("scan-intake-input"), "la pantalla no cargó").toBeVisible({ timeout: 30_000 });
}

/** El lector: escribe el código de golpe y pulsa Enter. */
async function leer(page: Page, code: string) {
  const input = page.getByTestId("scan-intake-input");
  await input.focus();
  await page.keyboard.type(code, { delay: 0 });
  await page.keyboard.press("Enter");
  await expect(page.getByText("Consultando…")).toHaveCount(0);
}

async function captura(page: Page, nombre: string) {
  if (!SHOTS) return;
  await page.screenshot({ path: `${SHOTS}/${nombre}.png`, fullPage: true });
}

async function sinDesborde(page: Page) {
  const ancho = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(ancho, "la página desborda a lo ancho").toBeLessThanOrEqual(0);
}

test.describe("registrar equipo por escaneo", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test.beforeAll(() => sembrar());

  test("escritorio: caja → IMEI 1 → IMEI 2 → serie, con rechazos, y sólo el botón confirma", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await signIn(page);
    await abrir(page);

    await leer(page, CAJA);
    await expect(page.getByTestId("scan-intake-variant")).toContainText(`${MODELO} 128 GB Grafito`, { timeout: 20_000 });
    await expect(page.getByTestId("scan-intake-expected")).toContainText("IMEI 1");

    await leer(page, EID);
    await expect(page.getByTestId("scan-intake-log")).toContainText("EID");
    await expect(page.getByTestId("scan-intake-value-imei")).toHaveText("—");

    await leer(page, NUEVO.imei);
    await expect(page.getByTestId("scan-intake-value-imei")).toHaveText(NUEVO.imei);
    await expect(page.getByTestId("scan-intake-expected")).toContainText("IMEI 2");
    await leer(page, NUEVO.imei);
    await expect(page.getByTestId("scan-intake-log")).toContainText("Lectura repetida");
    await leer(page, NUEVO.imei2);
    await expect(page.getByTestId("scan-intake-value-imei2")).toHaveText(NUEVO.imei2);
    await leer(page, NUEVO.serial);
    await expect(page.getByTestId("scan-intake-value-serial")).toHaveText(NUEVO.serial);

    // Un Enter doble con el campo vacío: nada, y el equipo sigue sin registrar.
    await page.getByTestId("scan-intake-input").press("Enter");
    await page.getByTestId("scan-intake-input").press("Enter");
    await expect(page.getByTestId("scan-intake-input")).toBeFocused();
    expect(unidad(NUEVO.imei).exists).toBe(false);

    // Review F3: with «Confirmar ingreso» focused, a scanner's read goes to the scanner field and its Enter never confirms.
    await page.getByTestId("scan-intake-confirm").focus();
    await page.keyboard.type(EID, { delay: 0 });
    await page.keyboard.press("Enter");
    await expect(page.getByText("Consultando…")).toHaveCount(0);
    await expect(page.getByTestId("scan-intake-log").getByRole("listitem").first()).toContainText("EID");
    expect(unidad(NUEVO.imei).exists).toBe(false);
    await captura(page, "scan-to-stock-desktop-review");

    await page.getByTestId("scan-intake-confirm").click();
    await expect(page.getByTestId("scan-intake-done")).toBeVisible({ timeout: 20_000 });
    const nuevo = unidad(NUEVO.imei);
    expect(nuevo).toMatchObject({ exists: true, status: "available", condition: "new", imei2: NUEVO.imei2,
      serial: NUEVO.serial, reason: "Inventario: registro por escaneo" });
    await expect(page.getByText(nuevo.label as string)).toBeVisible();
    await captura(page, "scan-to-stock-desktop-done");

    // Otro de este modelo: la variante se queda, las casillas se vacían, y el IMEI ya registrado se rechaza.
    await page.getByTestId("scan-intake-again").click();
    await expect(page.getByTestId("scan-intake-value-imei")).toHaveText("—");
    await leer(page, NUEVO.imei);
    await expect(page.getByTestId("scan-intake-log")).toContainText("ya está registrado");
    await expect(page.getByTestId("scan-intake-value-imei")).toHaveText("—");
    await sinDesborde(page);
  });

  test("móvil 390: un seminuevo queda pendiente de inspección, sin desborde", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await signIn(page);
    await abrir(page);
    await sinDesborde(page);
    await page.getByRole("button", { name: "Seminuevo" }).click();
    await page.getByTestId("scan-intake-model").fill(MODELO);
    await page.getByRole("button", { name: MODELO, exact: true }).click();
    await page.getByLabel(new RegExp(`${MODELO} 128 GB Grafito seminuevo`)).check();
    await leer(page, USADO);
    await expect(page.getByTestId("scan-intake-value-imei")).toHaveText(USADO);
    await page.getByTestId("scan-intake-battery").fill("88");
    await sinDesborde(page);
    await captura(page, "scan-to-stock-390-review");
    await page.getByTestId("scan-intake-confirm").click();
    await expect(page.getByTestId("scan-intake-done")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/pendiente de inspección/)).toBeVisible();
    expect(unidad(USADO)).toMatchObject({ exists: true, condition: "used", inspection: "pending", published: false });
    await sinDesborde(page);
    await captura(page, "scan-to-stock-390-done");
  });
});
