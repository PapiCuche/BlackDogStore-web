import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect, devices, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { BACKEND_DIR, RUN, shell, signIn } from "./inventory-first-support";
import { usedEntry } from "./used-units-support";

/**
 * STOCK-REAL-SIM-2 — registrar el stock real, nuevo y seminuevo, en navegador real y SIN MOCKS (API real de Django
 * sobre una base desechable: la guarda de `inventory-first-guard` se niega a correr contra otra cosa).
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3002 E2E_API_BASE=http://127.0.0.1:8100/api \
 *     npx playwright test e2e/stock-real-new-used.spec.ts
 *
 * En escritorio (1280×800) y en un móvil de 390×844, cada uno con su propio modelo (el inventario inicial sólo se
 * admite mientras el artículo no tenga otros movimientos en la sucursal):
 *
 *   1 · «Stock de equipos nuevos»: se descarga la plantilla, se llena con dos equipos cuyo «Precio de venta» difiere
 *       del de la variante, se previsualiza → «Precios de venta del archivo» propone el cambio; se registra SIN
 *       marcarlo → los equipos entran y la variante conserva su precio;
 *   2 · caja: se escanea el IMEI de uno, se cobra con nota de venta al precio de la variante y se imprime el ticket
 *       de 80 mm;
 *   3 · «Stock de seminuevos»: un equipo sin precio entra «Pendiente de inspección» y aparece en «Seminuevos»
 *       (tarjetas en el móvil, tabla en escritorio);
 *   4 · su ficha: inspección aprobada → foto real subida → confirmada como de este equipo → precio fijado con
 *       confirmación → publicado.
 *
 * Datos inventados: modelos marcados con la corrida, IMEI sintéticos 3500000… con dígito de control válido.
 */

const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "stock-real-e2e-"));
const TAG = `Qsr${RUN}`;
const VARIANT_PRICE = "3000.00";
const FILE_PRICE = 3250;
const USED_PRICE = "1750.50";

type Env = { name: string; code: "D" | "M"; touch: boolean; options: Parameters<Browser["newContext"]>[0] };
const phone = (() => { const { defaultBrowserType: _ignored, ...rest } = devices["iPhone 13"]; void _ignored; return rest; })();
const ENVS: Env[] = [
  { name: "chromium-1280x800", code: "D", touch: false, options: { viewport: { width: 1280, height: 800 } } },
  { name: "chromium-390x844", code: "M", touch: true, options: phone },
];

/** A Luhn-valid IMEI that is obviously synthetic: 3500000 + 7 digits + check digit. */
const STAMP = Number(String(Date.now()).slice(-6));
function syntheticImei(n: number): string {
  const body = `3500000${String((STAMP * 10 + n) % 10_000_000).padStart(7, "0")}`;
  let total = 0;
  for (let index = 0; index < body.length; index += 1) {
    let digit = Number(body[body.length - 1 - index]);
    if (index % 2 === 0) { digit *= 2; if (digit > 9) digit -= 9; }
    total += digit;
  }
  return body + String((10 - (total % 10)) % 10);
}

const modelOf = (env: Env) => `Equipo Carga ${TAG}${env.code}`;
const devicesOf = (env: Env) => {
  const base = env.code === "D" ? 1 : 5;
  return {
    sold: { serial: `SR${RUN}${env.code}N1`.toUpperCase(), imei: syntheticImei(base) },
    kept: { serial: `SR${RUN}${env.code}N2`.toUpperCase(), imei: syntheticImei(base + 1) },
    used: { serial: `SR${RUN}${env.code}U1`.toUpperCase(), imei: syntheticImei(base + 2) },
  };
};

type Seed = { branch: string; models: Record<string, { new: number; used: number; newName: string; usedName: string }> };

/** Per environment: a model with its palette (128 GB · Negro · Azul), a NEW variant at S/ 3000 and a USED one. */
function seed(): Seed {
  return shell<Seed>(`
from django.contrib.auth import get_user_model
from store import device_catalog, model_palette, stock_unit_services
from store.models import Company, Product, VariantAttribute, VariantOption
actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug=data['slug'])
def attr(role, name, kind='text'):
    return VariantAttribute.objects.filter(company=company, role=role).first() \\
        or device_catalog.create_attribute(company=company, name=name, role=role, kind=kind)
def opt(attribute, label, swatch=''):
    return VariantOption.objects.filter(company=company, attribute=attribute, label__iexact=label).first() \\
        or device_catalog.create_option(company=company, attribute_id=attribute.pk, label=label, swatch_hex=swatch)
cap, col = attr('capacity', 'Capacidad'), attr('color', 'Color', 'color')
g128, negro, azul = opt(cap, '128 GB'), opt(col, 'Negro', '#111111'), opt(col, 'Azul', '#1d4ed8')
models = {}
for name in data['models']:
    model = device_catalog.create_model(company=company, name=name)
    model_palette.set_palette(model, option_ids=[g128.pk, negro.pk, azul.pk], actor=actor)
    made = {}
    for key, line, colour, price, suffix in (('new', 'new', negro, data['price'], '128 GB Negro'),
                                             ('used', 'used', azul, '1.00', '128 GB Azul seminuevo')):
        product = Product.objects.create(company=company, name=name + ' ' + suffix,
                                         slug=(name + '-' + key).lower().replace(' ', '-'), price=Decimal(price),
                                         inventory=0, is_active=True)
        stock_unit_services.set_serialized(product, serialized=True, requires_imei=True, actor=actor)
        device_catalog.set_variant(product, model_id=model.pk, condition_line=line,
                                   option_ids=[g128.pk, colour.pk], actor=actor)
        made[key], made[key + 'Name'] = product.pk, product.name
    models[name] = made
out({'branch': company.default_inventory_branch.name, 'models': models})
`, { models: ENVS.map(modelOf), price: VARIANT_PRICE });
}

type Unit = { serial: string; status: string; inspection: string; published: boolean; price: string | null;
              attested: number; product: string };
type State = { units: Record<string, Unit>; prices: Record<string, string>; sold: string[]; picked: string[];
               notes: number };

/** What the database holds for this environment's model: read only. */
function state(env: Env): State {
  return shell<State>(`
from store.models import OrderItem, OrderUnit, Product, SalesNote, StockUnit, UnitPhoto
products = Product.objects.filter(company__slug=data['slug'], product_model__name=data['model'])
units = StockUnit.objects.filter(product__in=products)
orders = set(units.exclude(order_id=None).values_list('order_id', flat=True))
out({
    'units': {u.serial_number: {'serial': u.serial_number, 'status': u.status, 'inspection': u.inspection_status,
                                'published': u.published_online, 'price': u.sale_price, 'product': u.product.name,
                                'attested': UnitPhoto.objects.filter(unit=u).exclude(attested_at=None).count()}
              for u in units.select_related('product')},
    'prices': {p.name: str(p.price) for p in products},
    'sold': [str(i.price) for i in OrderItem.objects.filter(order_id__in=orders)],
    # SERIAL-PICK-01: the device the till identified, whatever line carries it.
    'picked': list(OrderUnit.objects.filter(order_id__in=orders).values_list('unit__serial_number', flat=True)),
    'notes': SalesNote.objects.filter(order_id__in=orders).count(),
})
`, { model: modelOf(env) });
}

function python(code: string, env: Record<string, string>): string {
  return execFileSync("python3", ["-c", code], { cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ...env } }).toString();
}

/** THE downloaded template, with `rows` ({header: value}) from row 3 — row 2 is the template's own example. */
function fill(file: string, rows: Record<string, string | number | null>[]): string {
  const target = path.join(WORK, `lleno-${path.basename(file)}`);
  python(`
import json, os, openpyxl
book = openpyxl.load_workbook(os.environ['E2E_FILE']); sheet = book.worksheets[0]
headers = [cell.value for cell in sheet[1]]
for offset, row in enumerate(json.loads(os.environ['E2E_ROWS'])):
    for header, value in row.items():
        if header not in headers:
            raise SystemExit('la plantilla no tiene la columna ' + header)
        sheet.cell(row=3 + offset, column=headers.index(header) + 1, value=value)
book.save(os.environ['E2E_OUT'])`, { E2E_FILE: file, E2E_ROWS: JSON.stringify(rows), E2E_OUT: target });
  return target;
}

/** A real PNG (the server decodes it: its name decides nothing). */
function photo(): string {
  const target = path.join(WORK, "equipo.png");
  if (!fs.existsSync(target)) {
    python(`
import os
from PIL import Image
Image.new('RGB', (320, 320), (40, 90, 200)).save(os.environ['E2E_OUT'], format='PNG')`, { E2E_OUT: target });
  }
  return target;
}

const press = async (target: Locator, touch: boolean) => {
  await target.scrollIntoViewIfNeeded({ timeout: 30_000 });
  if (touch) await target.tap({ timeout: 30_000 }); else await target.click({ timeout: 30_000 });
};

async function noOverflow(page: Page, where: string) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `desborde horizontal en ${where}`).toBeLessThanOrEqual(1);
}

async function download(page: Page, link: string, env: Env): Promise<string> {
  const [file] = await Promise.all([page.waitForEvent("download"), press(page.getByRole("link", { name: link }), env.touch)]);
  const target = path.join(WORK, `${env.code}-${Date.now()}-${file.suggestedFilename()}`);
  await file.saveAs(target);
  return target;
}

async function previewAndRegister(page: Page, env: Env, file: string, line: "Nuevos" | "Seminuevos", count: number) {
  await page.getByLabel("Archivo de equipos (.xlsx)").setInputFiles(file);
  await press(page.getByRole("radio", { name: line, exact: true }), env.touch);
  await press(page.getByRole("radio", { name: /^Inventario inicial/ }), env.touch);
  await page.getByLabel("Motivo para las filas sin motivo").fill(`Inventario inicial E2E ${TAG}`);
  await press(page.getByRole("button", { name: "Previsualizar" }), env.touch);
  const register = page.getByRole("button", { name: `Registrar ${count} ${count === 1 ? "equipo" : "equipos"}` });
  await expect(register, "la vista previa no permite registrar").toBeVisible({ timeout: 60_000 });
  return register;
}

let seeded: Seed;
let session: Awaited<ReturnType<BrowserContext["storageState"]>> | null = null;

test.describe("stock real: nuevos y seminuevos, de la plantilla a la venta y a la web", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeAll(() => { seeded = seed(); });
  // The filled templates and the photo live only for the run.
  test.afterAll(() => { fs.rmSync(WORK, { recursive: true, force: true }); });

  for (const env of ENVS) {
    test(`${env.name}: plantilla de nuevos con precio distinto, caja con ticket, seminuevo hasta publicarlo`, async ({ browser }) => {
      test.setTimeout(420_000);
      // ONE sign-in for the run (the login admits 5 per minute): the phone reuses the desktop's session.
      if (!session) {
        const first = await browser.newContext({ viewport: { width: 1280, height: 800 } });
        await signIn(await first.newPage());
        session = await first.storageState();
        await first.close();
      }
      const context = await browser.newContext({ ...env.options, storageState: session });
      const page = await context.newPage();
      page.on("dialog", (dialog) => void dialog.accept());
      const model = modelOf(env);
      const ids = seeded.models[model];
      const units = devicesOf(env);
      try {
        // 1 · «Stock de equipos nuevos» with a price that is NOT the variant's: a proposal, never applied alone.
        await page.goto("/admin/inventory/units/import", { waitUntil: "networkidle" });
        await expect(page.getByLabel("Archivo de equipos (.xlsx)"), "la carga de stock no cargó").toBeVisible({ timeout: 90_000 });
        const newTemplate = await download(page, "Stock de equipos nuevos", env);
        const row = (unit: { serial: string; imei: string }) => ({
          Sucursal: seeded.branch, Modelo: model, Capacidad: "128 GB", Color: "Negro", "IMEI 1": unit.imei,
          Serie: unit.serial, Costo: 2500, "Precio de venta": FILE_PRICE,
        });
        const register = await previewAndRegister(page, env, fill(newTemplate, [row(units.sold), row(units.kept)]), "Nuevos", 2);
        const prices = page.getByRole("region", { name: "Precios de venta del archivo" });
        await expect(prices).toBeVisible();
        const proposal = prices.getByRole("row").filter({ hasText: ids.newName });
        await expect(proposal).toContainText(/3,?000\.00/);
        await expect(proposal).toContainText(/3,?250\.00/);
        const update = proposal.getByRole("checkbox", { name: /Actualizar el precio de la variante a S\/\s?3,?250\.00/ });
        await expect(update).not.toBeChecked();
        await noOverflow(page, `${env.name} vista previa de nuevos`);
        await press(register, env.touch);                          // registered WITHOUT ticking the proposal
        await expect(page.getByRole("status").filter({ hasText: "Se registraron 2 equipos" })).toBeVisible({ timeout: 60_000 });
        await expect(page.getByRole("note", { name: "Precios actualizados" })).toHaveCount(0);
        let seen = state(env);
        expect(seen.prices[ids.newName]).toBe(VARIANT_PRICE);
        expect([seen.units[units.sold.serial]?.status, seen.units[units.kept.serial]?.status]).toEqual(["available", "available"]);
        expect(seen.units[units.sold.serial]?.price).toBeNull();

        // 2 · The till: scan the IMEI, charge with the sales note at the VARIANT's price, print the 80 mm ticket.
        await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });
        const scan = page.getByLabel("Escanear código");
        await expect(scan, "el punto de venta no cargó").toBeVisible({ timeout: 60_000 });
        await press(scan, env.touch);
        await scan.pressSequentially(units.sold.imei, { delay: 4 });       // a scanner: a burst and Enter
        await scan.press("Enter");
        await expect(page.getByText(new RegExp(`IMEI ${units.sold.imei}`)).first()).toBeVisible({ timeout: 30_000 });
        await noOverflow(page, `${env.name} caja`);
        await page.getByRole("checkbox").first().check();
        await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
        await page.getByLabel("Efectivo recibido").fill("99999");
        const charge = page.getByRole("button", { name: /^cobrar$/i });
        await expect(charge).toBeEnabled({ timeout: 20_000 });
        await press(charge, env.touch);
        await expect(page.getByText(/Venta registrada/i), "la venta no se completó").toBeVisible({ timeout: 60_000 });
        const ticket = page.waitForResponse((r) => r.url().includes("sales-note/pdf") && r.url().includes("ticket80")
          && r.status() !== 307 && r.status() !== 308, { timeout: 60_000 });
        await press(page.getByRole("button", { name: /imprimir ticket/i }), env.touch);
        const pdf = await ticket;
        expect(pdf.status()).toBe(200);
        expect(pdf.headers()["content-type"]).toContain("application/pdf");
        const bytes = await (await page.request.get(pdf.url())).body();
        expect(bytes.subarray(0, 4).toString(), "el ticket no es un PDF").toBe("%PDF");
        seen = state(env);
        expect(seen.units[units.sold.serial].status).toBe("sold");
        expect(seen.units[units.kept.serial].status).toBe("available");
        expect(seen.sold).toEqual([VARIANT_PRICE]);
        expect(seen.picked).toEqual([units.sold.serial]);
        expect(seen.notes).toBe(1);

        // 3 · «Stock de seminuevos»: a device without a price enters pending, and «Seminuevos» lists it.
        await page.goto("/admin/inventory/units/import", { waitUntil: "networkidle" });
        await expect(page.getByLabel("Archivo de equipos (.xlsx)")).toBeVisible({ timeout: 90_000 });
        const usedTemplate = await download(page, "Stock de seminuevos", env);
        const usedFile = fill(usedTemplate, [{
          Sucursal: seeded.branch, Modelo: model, Capacidad: "128 GB", Color: "Azul", "IMEI 1": units.used.imei,
          Serie: units.used.serial, Costo: 1200, "Salud de batería (%)": 88,
        }]);
        await press(await previewAndRegister(page, env, usedFile, "Seminuevos", 1), env.touch);
        await expect(page.getByRole("status").filter({ hasText: "Se registró 1 equipo" })
          .or(page.getByRole("status").filter({ hasText: "Se registraron 1 equipo" }))).toBeVisible({ timeout: 60_000 });
        await expect(page.getByRole("note", { name: "Después de cargar seminuevos" })).toBeVisible();
        expect(state(env).units[units.used.serial]).toMatchObject({ status: "available", inspection: "pending", published: false, price: null });

        await page.goto("/admin/inventory/used", { waitUntil: "networkidle" });
        await expect(page.getByRole("heading", { name: "Seminuevos", exact: true })).toBeVisible({ timeout: 60_000 });
        await page.getByLabel("Serie o IMEI").fill(units.used.serial);
        await press(page.getByRole("button", { name: "Filtrar" }), env.touch);
        const entry = usedEntry(page, units.used.serial);
        await expect(entry).toHaveCount(1, { timeout: 30_000 });
        await expect(entry).toContainText("Pendiente de inspección");
        await expect(entry).toContainText("Sin precio (pendiente)");
        await expect(page.getByTestId(env.touch ? "used-units-cards" : "used-units-table")).toBeVisible();
        await expect(page.getByTestId(env.touch ? "used-units-table" : "used-units-cards")).toBeHidden();
        await noOverflow(page, `${env.name} seminuevos`);

        // 4 · Its ficha: inspection → a real photo → attested → price (confirmed) → published.
        const edit = `Editar ${units.used.serial}`;
        await press(page.getByRole("button", { name: edit }), env.touch);
        let dialog = page.getByRole("dialog");
        await press(dialog.getByRole("button", { name: "Aprobar inspección" }), env.touch);
        await expect(dialog.getByText("Inspección aprobada.")).toBeVisible({ timeout: 30_000 });
        await dialog.getByLabel("Nueva foto").setInputFiles(photo());
        await press(dialog.getByRole("button", { name: "Subir foto" }), env.touch);
        const attest = dialog.getByRole("checkbox", { name: /Confirmo que la foto 1 .* es de este equipo exacto/ });
        await expect(attest).toBeVisible({ timeout: 30_000 });
        await press(attest, env.touch);
        await expect(attest).toBeChecked({ timeout: 30_000 });
        await expect(dialog.getByTestId("unit-photo-state").first()).toHaveText("Se muestra en la tienda", { timeout: 30_000 });
        await dialog.getByLabel(/Precio de venta \(S\/\)/).fill(USED_PRICE);
        await press(dialog.getByRole("button", { name: "Guardar" }), env.touch);
        const confirm = dialog.getByRole("alertdialog", { name: "Confirmar el precio de venta" });
        await expect(confirm).toContainText(/S\/\s?1,?750\.50/);
        await press(confirm.getByRole("button", { name: "Sí, guardar este precio" }), env.touch);
        await expect(page.getByRole("status").filter({ hasText: "Equipo guardado." })).toBeVisible({ timeout: 30_000 });
        seen = state(env);
        expect(seen.units[units.used.serial]).toMatchObject({ inspection: "approved", attested: 1, price: USED_PRICE, published: false });

        await press(page.getByRole("button", { name: edit }), env.touch);
        dialog = page.getByRole("dialog");
        await expect(dialog.getByTestId("publication-blockers")).toHaveCount(0);
        await press(dialog.getByRole("button", { name: "Publicar este equipo" }), env.touch);
        await expect(dialog.getByText("Equipo publicado en la tienda en línea.")).toBeVisible({ timeout: 30_000 });
        await expect(dialog.getByTestId("unit-publication")).toHaveText("Publicado en línea");
        await noOverflow(page, `${env.name} ficha del seminuevo`);
        expect(state(env).units[units.used.serial]).toMatchObject({ inspection: "approved", published: true, price: USED_PRICE, status: "available" });
        // The variant of the new devices still sells at its own price: the file's never applied itself.
        expect(state(env).prices[ids.newName]).toBe(VARIANT_PRICE);
      } finally {
        await context.close().catch(() => undefined);
      }
    });
  }
});
