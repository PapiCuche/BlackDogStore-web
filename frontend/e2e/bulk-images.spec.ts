// BULK-IMPORT-SAFETY — imágenes en lote, de punta a punta y sin mocks (navegador real).
//
// Siembra su propia corrida en la empresa de la prueba: dos productos con código (uno con stock), dos productos cuyos
// códigos sólo se distinguen por mayúsculas (un archivo con ese código es ambiguo) y un usuario Administrador con una
// contraseña generada en la corrida, nunca impresa. Entra por la API, como `rich-description.spec.ts`.
//
//   1 · «Añadir»: CÓDIGO.png y CÓDIGO_2.jpg van a su producto, uno desconocido y uno ambiguo se listan y no se
//       cargan; la galería queda en la base y el stock no se mueve.
//   2 · «Reemplazar»: no se aplica hasta escribir REEMPLAZAR, exactamente.
//   3 · La plantilla de relación se descarga desde la pantalla, se rellena y casa las imágenes por código.
//
// Capturas en `BULK_SHOTS` (por defecto `test-results/bulk-images`). La base debe ser local y desechable: `shell()`
// se niega a correr contra otra (`inventory-first-guard.ts`).
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect, type Locator, type Page } from "@playwright/test";
import { BACKEND_DIR, RUN, TAG, shell } from "./inventory-first-support";

const OUT = process.env.BULK_SHOTS ?? "test-results/bulk-images";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3002";
const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8100/api";
const USER = `bulk_img_${TAG.toLowerCase().slice(-6)}`;
const PASSWORD = `Bi-${crypto.randomBytes(9).toString("base64url")}!`;
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "bulk-images-e2e-"));
fs.mkdirSync(OUT, { recursive: true });

const CODE_A = `IMGA${RUN}`;
const CODE_B = `IMGB${RUN}`;
const TWIN_LOWER = `amb${RUN.toLowerCase()}`;
const TWIN_UPPER = `AMB${RUN}`;
const UNKNOWN = `NOEXISTE${RUN}`;

type Seed = {
  a: number; b: number; twins: number[];
  names: { a: string; b: string };
  /** Imágenes distintas (base64), generadas por el backend para que el decodificador las acepte. */
  pictures: Record<"red" | "blue" | "green" | "grey" | "yellow" | "white" | "black", { png: string; jpg: string }>;
};

type Snapshot = Record<string, { images: number; stock: number; movements: number; inventory: number }>;

let seed: Seed;

function snapshot(): Snapshot {
  return shell<Snapshot>(`
from django.db.models import Sum
from store.models import BranchStock, Product, ProductImage, StockMovement
result = {}
for pk in data['ids']:
    result[str(pk)] = {
        'images': ProductImage.objects.filter(product_id=pk).count(),
        'stock': BranchStock.objects.filter(product_id=pk).aggregate(total=Sum('quantity'))['total'] or 0,
        'movements': StockMovement.objects.filter(product_id=pk).count(),
        'inventory': Product.objects.get(pk=pk).inventory,
    }
out(result)
`, { ids: [seed.a, seed.b, ...seed.twins] });
}

const file = (name: string, colour: keyof Seed["pictures"], kind: "png" | "jpg" = "png") => ({
  name, mimeType: kind === "png" ? "image/png" : "image/jpeg", buffer: Buffer.from(seed.pictures[colour][kind], "base64"),
});

async function shot(page: Page, name: string) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
}

async function login(page: Page) {
  const host = new URL(BASE).hostname;
  const api = API.replace("127.0.0.1", host);
  for (let attempt = 0; ; attempt++) {
    const res = await page.context().request.post(`${api}/auth/login/`, { data: { username: USER, password: PASSWORD } });
    if (res.status() === 200) break;
    if (res.status() !== 429 || attempt >= 2) throw new Error(`login ${res.status()}`);
    await page.waitForTimeout(62_000);
  }
  await page.context().request.get(`${api}/auth/csrf/`);
}

/** La sección «Imágenes» del centro de gestión masiva, abierta y lista. */
async function openImages(page: Page): Promise<Locator> {
  await page.goto("/admin/inventory/bulk#imagenes", { waitUntil: "networkidle" });
  const section = page.getByRole("region", { name: "Imágenes", exact: true });
  await expect(section).toBeVisible({ timeout: 60_000 });
  await expect(section.getByRole("note", { name: "Cómo nombrar las imágenes" })).toBeVisible();
  return section;
}

function python(code: string, env: Record<string, string>): string {
  return execFileSync("python3", ["-c", code], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ...env },
  }).toString();
}

test.describe.configure({ mode: "serial" });
test.use({ baseURL: BASE, viewport: { width: 1280, height: 900 } });

test.beforeAll(async () => {
  test.setTimeout(120_000);
  seed = shell<Seed>(`
import base64, io
from decimal import Decimal
from PIL import Image
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from store import inventory_services
from store.models import (
    Company, CompanyRole, Membership, MembershipRoleAssignment, Product, ProductBarcode, StockMovement,
)
company = Company.objects.get(slug=data['slug'])
user = get_user_model().objects.create_user(username=data['user'], password=data['password'], first_name='Imágenes')
membership = Membership.objects.create(user=user, company=company, role='inventory', is_active=True,
                                       branch_access_mode=Membership.ACCESS_MODE_ALL)
MembershipRoleAssignment.objects.create(
    membership=membership, role=CompanyRole.objects.get(company=company, slug='administrador'), is_active=True)

def product(name, *codes):
    item = Product.objects.create(company=company, name=name + ' ' + data['tag'], slug=slugify('bi-' + name + '-' + data['tag']),
                                  price=Decimal('49.90'), inventory=0, is_active=True, is_published_online=False)
    for code in codes:
        ProductBarcode.objects.create(company=company, product=item, code=code, symbology=ProductBarcode.INTERNAL)
    return item

a = product('Funda lote', data['a'])
b = product('Cable lote', data['b'])
twins = [product('Gemelo uno', data['twin_lower']), product('Gemelo dos', data['twin_upper'])]
inventory_services.create_stock_movement(
    branch=company.default_inventory_branch, product_id=a.pk, movement_type=StockMovement.INITIAL_STOCK,
    quantity=4, reason='E2E imágenes en lote ' + data['tag'])

def picture(colour, kind):
    buffer = io.BytesIO()
    Image.new('RGB', (64, 48), colour).save(buffer, 'PNG' if kind == 'png' else 'JPEG')
    return base64.b64encode(buffer.getvalue()).decode()

colours = {'red': (200, 30, 30), 'blue': (30, 30, 200), 'green': (30, 160, 60), 'grey': (120, 120, 120),
           'yellow': (230, 210, 40), 'white': (250, 250, 250), 'black': (10, 10, 10)}
out({
    'a': a.pk, 'b': b.pk, 'twins': [twin.pk for twin in twins],
    'names': {'a': a.name, 'b': b.name},
    'pictures': {name: {'png': picture(rgb, 'png'), 'jpg': picture(rgb, 'jpg')} for name, rgb in colours.items()},
})
`, { user: USER, password: PASSWORD, a: CODE_A, b: CODE_B, twin_lower: TWIN_LOWER, twin_upper: TWIN_UPPER });
});

test.afterAll(async () => {
  if (!seed) return;
  // Quita lo que la corrida colocó, desactiva sus productos y su usuario. El stock sembrado queda con su movimiento.
  shell(`
from django.contrib.auth import get_user_model
from store import product_media
from store.models import Membership, Product, ProductImage
actor = get_user_model().objects.get(username=data['user'])
for image in ProductImage.objects.filter(product_id__in=data['ids']):
    product_media.remove_image(image=image, actor=actor)
Product.objects.filter(pk__in=data['ids']).update(is_active=False)
Membership.objects.filter(user=actor).update(is_active=False)
actor.is_active = False
actor.save(update_fields=['is_active'])
out('ok')
`, { user: USER, ids: [seed.a, seed.b, ...seed.twins] });
});

test("1 · «Añadir»: cada archivo a su producto por código; lo desconocido y lo ambiguo se listan", async ({ page }) => {
  test.setTimeout(240_000);
  const before = snapshot();
  await login(page);
  const section = await openImages(page);

  await section.getByLabel("Elegir imágenes").setInputFiles([
    file(`${CODE_A}.png`, "red"),
    file(`${CODE_A}_2.jpg`, "blue", "jpg"),
    file(`${CODE_B}.png`, "green"),
    file(`${UNKNOWN}.png`, "grey"),
    file(`${TWIN_UPPER}.png`, "white"),
  ]);
  await section.getByRole("button", { name: "Previsualizar" }).click();

  const table = section.getByRole("table");
  await expect(table).toBeVisible({ timeout: 60_000 });
  const rowA = table.getByRole("row").filter({ hasText: seed.names.a });
  await expect(rowA).toContainText(`${CODE_A}.png`);
  await expect(rowA).toContainText(`${CODE_A}_2.jpg`);
  await expect(rowA).toContainText(`Principal: ${CODE_A}.png`);
  await expect(table.getByRole("row").filter({ hasText: seed.names.b })).toContainText(`${CODE_B}.png`);
  const skipped = section.getByRole("region", { name: "Archivos que no se cargarán" });
  await expect(skipped).toContainText("2 archivos no se cargarán");
  await expect(skipped).toContainText("ningún producto");
  await expect(skipped).toContainText("coincide con más de un producto");
  await expect(section).toContainText("3 imágenes para 2 productos");
  await shot(page, "01-anadir-vista-previa");

  // Nada se colocó al previsualizar.
  expect(snapshot()).toEqual(before);

  await section.getByRole("button", { name: "Añadir 3 imágenes" }).click();
  await expect(section.getByRole("status").filter({ hasText: "Se añadieron 3 imágenes a 2 productos." }))
    .toBeVisible({ timeout: 60_000 });
  await shot(page, "02-anadir-aplicado");

  const after = snapshot();
  expect([after[seed.a].images, after[seed.b].images]).toEqual([2, 1]);
  for (const twin of seed.twins) expect(after[twin].images, "un código ambiguo no se asigna a nadie").toBe(0);
  for (const id of [seed.a, seed.b, ...seed.twins]) {
    const { stock, movements, inventory } = after[id];
    expect({ stock, movements, inventory }, "las imágenes no mueven stock").toEqual({
      stock: before[id].stock, movements: before[id].movements, inventory: before[id].inventory,
    });
  }
  expect(after[seed.a].stock).toBe(4);
});

test("2 · «Reemplazar» pide escribir REEMPLAZAR", async ({ page }) => {
  test.setTimeout(240_000);
  const before = snapshot();
  expect(before[seed.a].images).toBe(2);
  await login(page);
  const section = await openImages(page);

  await section.getByRole("radio", { name: /Reemplazar la galería/ }).check();
  await section.getByLabel("Elegir imágenes").setInputFiles([file(`${CODE_A}.png`, "yellow")]);
  await section.getByRole("button", { name: "Previsualizar" }).click();

  const warning = section.getByRole("alert").filter({ hasText: "Se quitarán 2 imágenes de 1 producto" });
  await expect(warning).toBeVisible({ timeout: 60_000 });
  const replace = section.getByRole("button", { name: "Reemplazar galerías" });
  const word = section.getByLabel("Escribe REEMPLAZAR para confirmar");
  await expect(replace).toBeDisabled();
  await word.fill("reemplazar");
  await expect(replace, "la palabra se escribe exactamente").toBeDisabled();
  await shot(page, "03-reemplazar-sin-confirmar");
  expect(snapshot()).toEqual(before);

  await word.fill("REEMPLAZAR");
  await expect(replace).toBeEnabled();
  await replace.click();
  await expect(section.getByRole("status").filter({ hasText: "Se quitaron 2 imágenes y se añadió 1 imagen en 1 producto." }))
    .toBeVisible({ timeout: 60_000 });
  await shot(page, "04-reemplazar-aplicado");

  const after = snapshot();
  expect(after[seed.a].images).toBe(1);
  expect(after[seed.b].images, "sólo los productos de esta carga").toBe(1);
  expect([after[seed.a].stock, after[seed.a].movements]).toEqual([before[seed.a].stock, before[seed.a].movements]);
});

test("3 · la plantilla de relación se descarga, se rellena y casa por código", async ({ page }) => {
  test.setTimeout(240_000);
  await login(page);
  const section = await openImages(page);

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    section.getByRole("link", { name: "Descargar plantilla de relación" }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("relacion-imagenes.xlsx");
  const template = path.join(WORK, "relacion.xlsx");
  await download.saveAs(template);

  const shape = JSON.parse(python(`
import json, os, openpyxl
book = openpyxl.load_workbook(os.environ['E2E_FILE']); sheet = book.worksheets[0]
rows = [[c for c in row] for row in sheet.iter_rows(min_row=2, values_only=True)]
print(json.dumps({'sheets': book.sheetnames, 'headers': [c.value for c in sheet[1]],
                  'notes': sum(1 for c in sheet[1] if c.comment), 'codes': [row[0] for row in rows],
                  'empty': all(row[1] is None and row[2] is None for row in rows)}))
`, { E2E_FILE: template })) as { sheets: string[]; headers: string[]; notes: number; codes: string[]; empty: boolean };
  expect(shape.sheets).toEqual(["Imágenes"]);
  expect(shape.headers).toEqual(["Código", "Imagen principal", "Imágenes", "Producto (referencia)"]);
  expect(shape.notes).toBe(4);
  expect(shape.empty).toBe(true);
  expect(shape.codes).toEqual(expect.arrayContaining([CODE_A, CODE_B]));
  // Un código que la carga llamaría ambiguo no se ofrece.
  expect(shape.codes).not.toContain(TWIN_LOWER);
  expect(shape.codes).not.toContain(TWIN_UPPER);

  // Se rellena ESE archivo: la fila de B con dos nombres que no son códigos; las demás filas se borran.
  const filled = path.join(WORK, "relacion-llena.xlsx");
  python(`
import os, openpyxl
book = openpyxl.load_workbook(os.environ['E2E_FILE']); sheet = book.worksheets[0]
for index in range(sheet.max_row, 1, -1):
    if sheet.cell(index, 1).value == os.environ['E2E_CODE']:
        sheet.cell(index, 2).value = 'frente-relacion.png'
        sheet.cell(index, 3).value = 'frente-relacion.png|lado-relacion.jpg'
    else:
        sheet.delete_rows(index)
book.save(os.environ['E2E_OUT'])
`, { E2E_FILE: template, E2E_CODE: CODE_B, E2E_OUT: filled });

  await section.getByLabel("Elegir imágenes").setInputFiles([
    file("frente-relacion.png", "black"), file("lado-relacion.jpg", "white", "jpg"),
  ]);
  await section.getByLabel("Libro «Imágenes» (opcional)").setInputFiles(filled);
  await section.getByRole("button", { name: "Previsualizar" }).click();

  const rowB = section.getByRole("table").getByRole("row").filter({ hasText: seed.names.b });
  await expect(rowB).toContainText("frente-relacion.png", { timeout: 60_000 });
  await expect(rowB).toContainText("lado-relacion.jpg");
  await expect(section.getByRole("alert").filter({ hasText: "con error" })).toHaveCount(0);
  await expect(section.getByRole("button", { name: "Añadir 2 imágenes" })).toBeEnabled();
  await shot(page, "05-plantilla-de-relacion");
});
