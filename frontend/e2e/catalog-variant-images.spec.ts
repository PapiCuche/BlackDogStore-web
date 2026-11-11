// CATALOG-VARIANT-IMAGES — imágenes por variante y portada del modelo desde «Modelos y variantes» (navegador real).
//
// Entorno aislado de QA: siembra su propio modelo con dos variantes (un atributo propio con dos opciones) y un
// usuario Administrador de prueba (contraseña generada en la corrida, nunca impresa). Sube por la interfaz, con
// vista previa, un PNG con transparencia a la variante A y un JPEG a la variante B; elige la principal y la portada;
// comprueba la base y la tienda (/modelo/<slug>) a 1280 y 390 px.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { test, expect, type Locator, type Page } from "@playwright/test";
import { TAG, shell } from "./inventory-first-support";

const OUT = process.env.CVI_SHOTS ?? "test-results/catalog-variant-images";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3002";
const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";
const USER = `cvi_admin_${TAG.toLowerCase().slice(-6)}`;
const PASSWORD = `Cvi-${crypto.randomBytes(9).toString("base64url")}!`;
fs.mkdirSync(OUT, { recursive: true });

// ---------------------------------------------------------------------------
// Archivos generados en la corrida (sin imágenes con licencia en el repositorio).
// ---------------------------------------------------------------------------

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(bytes: Buffer): number {
  let c = 0xffffffff;
  for (const byte of bytes) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

/** Un recorte RGBA: opaco en el centro (color `rgb`), alfa 0 en las esquinas y el borde. */
function cutoutPng(width: number, height: number, rgb: [number, number, number]): Buffer {
  const rows: Buffer[] = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * 4); // filtro 0 + RGBA
    for (let x = 0; x < width; x++) {
      const inside = x >= width / 4 && x < (3 * width) / 4 && y >= height / 4 && y < (3 * height) / 4;
      row.set(inside ? [...rgb, 255] : [0, 0, 0, 0], 1 + x * 4);
    }
    rows.push(row);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 6, 0, 0, 0], 8); // 8 bits, RGBA, sin entrelazado
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// 32×24, generado con Pillow para esta prueba (fondo azul, rectángulo amarillo).
const PHOTO_JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/" +
  "2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAAYACADASIAAhEBAxEB/8QAHwAA" +
  "AQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRol" +
  "JicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW" +
  "19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdh" +
  "cRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJma" +
  "oqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDgq6X/AIQbxH/0Dv8AyPH/APFVzVfStetx" +
  "lxLisi9j9WjF8/NfmTe3La1mu55GTZbSx/P7Vtcttrdb+T7Hh/8Awg3iP/oHf+R4/wD4quar6Vr5qo4N4lxWe+2+sxiuTltyprfmve7fYM5y2lgOT2Tb5r726W8l" +
  "3Cul/wCE58R/9BH/AMgR/wDxNFFfWYrAYXGW+s0ozttzRTtfe10eTSxFWjf2UnG/ZtB/wnPiP/oI/wDkCP8A+JrmqKKMLgMLg7/VqUYX35YpXtteyCriKta3tZOV" +
  "u7bP/9k=",
  "base64",
);

type Seed = { id: number; slug: string; name: string; a: { id: number; name: string }; b: { id: number; name: string } };
let seed: Seed;

async function shot(page: Page, name: string) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
}

async function login(page: Page) {
  const host = new URL(BASE).hostname;
  const api = API.replace("127.0.0.1", host);
  for (let i = 0; ; i++) {
    const res = await page.context().request.post(`${api}/auth/login/`, { data: { username: USER, password: PASSWORD } });
    if (res.status() === 200) break;
    if (res.status() !== 429 || i >= 2) throw new Error(`login ${res.status()}`);
    await page.waitForTimeout(62_000);
  }
  await page.context().request.get(`${api}/auth/csrf/`);
}

async function overflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/** Opens the gallery of one variant and stages + uploads `files`, checking the preview first. */
async function upload(page: Page, variantName: string, files: { name: string; mimeType: string; buffer: Buffer }[], shotName: string) {
  await page.getByRole("button", { name: `Imágenes de ${variantName}`, exact: true }).click();
  const panel = page.getByRole("region", { name: `Imágenes de ${variantName}`, exact: true });
  await expect(panel).toBeVisible();
  await panel.getByLabel(/Elegir imágenes/).setInputFiles(files);
  for (const file of files) {
    await expect(panel.getByRole("img", { name: `Vista previa de ${file.name}` })).toBeVisible();
  }
  await shot(page, shotName);
  const label = files.length === 1 ? "Subir 1 imagen" : `Subir ${files.length} imágenes`;
  await panel.getByRole("button", { name: label }).click();
  await expect(panel.getByRole("list", { name: "Imágenes por subir" })).toHaveCount(0, { timeout: 30_000 });
  await expect(panel.getByRole("button", { name: /^Quitar imagen \d$/ })).toHaveCount(files.length);
  return panel;
}

const publicIdOf = (url: string) => url.replace(/\/+$/, "").split("/").pop() ?? "";

async function mainImageSrc(page: Page): Promise<string> {
  const img: Locator = page.getByTestId("model-main-image").getByRole("img");
  return decodeURIComponent((await img.getAttribute("src")) ?? "");
}

test.describe.configure({ mode: "serial" });
test.use({ baseURL: BASE, viewport: { width: 1280, height: 900 } });

test.beforeAll(async () => {
  test.setTimeout(120_000);
  seed = shell<Seed>(`
from decimal import Decimal
from django.contrib.auth import get_user_model
from store import device_catalog
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment, Product
company = Company.objects.get(slug=data['slug'])
u = get_user_model().objects.create_user(username=data['user'], password=data['password'], first_name='Catálogo')
m = Membership.objects.create(user=u, company=company, role='inventory', is_active=True, branch_access_mode=Membership.ACCESS_MODE_ALL)
MembershipRoleAssignment.objects.create(membership=m, role=CompanyRole.objects.get(company=company, slug='administrador'), is_active=True)
attribute = device_catalog.create_attribute(company=company, name='Acabado ' + data['tag'])
light = device_catalog.create_option(company=company, attribute_id=attribute.pk, label='Claro')
dark = device_catalog.create_option(company=company, attribute_id=attribute.pk, label='Oscuro')
name = 'Modelo CVI ' + data['tag']
model = device_catalog.create_model(company=company, name=name, is_published_online=True)
rows = {}
for key, option, price in (('a', light, '1999.00'), ('b', dark, '2199.00')):
    product = Product.objects.create(company=company, name=name + ' ' + option.label, slug=('cvi-' + data['tag'] + '-' + key).lower(),
        price=Decimal(price), inventory=0, is_active=True, is_published_online=True)
    device_catalog.set_variant(product, model_id=model.pk, condition_line='new', option_ids=[option.pk])
    rows[key] = {'id': product.pk, 'name': product.name}
out({'id': model.pk, 'slug': model.slug, 'name': model.name, **rows})
`, { user: USER, password: PASSWORD });
});

test("imágenes por variante, principal y portada; la tienda cambia de imagen con la variante", async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await page.goto(`/admin/products/models/${seed.id}`, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: seed.name }).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Sin imágenes", { exact: true })).toHaveCount(2);
  await shot(page, "01-variantes-sin-imagenes-1280");

  // Variante A: dos PNG con transparencia (vista previa antes de subir).
  const panelA = await upload(page, seed.a.name, [
    { name: "recorte.png", mimeType: "image/png", buffer: cutoutPng(96, 64, [200, 30, 30]) },
    { name: "recorte-2.png", mimeType: "image/png", buffer: cutoutPng(80, 80, [30, 160, 60]) },
  ], "02-vista-previa-variante-a-1280");
  // La segunda pasa a ser la principal.
  await panelA.getByRole("button", { name: "Marcar como principal: imagen 2" }).click();
  await expect(panelA.getByRole("listitem").nth(1).getByText("Principal")).toBeVisible();
  // La primera, portada del modelo.
  await panelA.getByRole("button", { name: "Usar como portada del modelo: imagen 1" }).click();
  await expect(page.getByText("Portada del modelo actualizada.")).toBeVisible({ timeout: 30_000 });
  await expect(panelA.getByText("Portada del modelo", { exact: true })).toBeVisible();
  await expect(page.getByText("2 imágenes", { exact: true })).toBeVisible();
  await shot(page, "03-variante-a-principal-y-portada-1280");
  await panelA.getByRole("button", { name: `Cerrar imágenes de ${seed.a.name}` }).click();

  // Variante B: un JPEG.
  await upload(page, seed.b.name, [{ name: "foto.jpg", mimeType: "image/jpeg", buffer: PHOTO_JPEG }], "04-vista-previa-variante-b-1280");
  await expect(page.getByText("1 imagen", { exact: true })).toBeVisible();
  await shot(page, "05-variante-b-subida-1280");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  expect(await overflow(page), "desborde horizontal del panel a 390 px").toBeLessThanOrEqual(1);
  await shot(page, "06-panel-390");
  await page.setViewportSize({ width: 1280, height: 900 });

  // La base: cada imagen en su variante, el PNG conserva el alfa, la portada es la elegida.
  const db = shell<{
    a: { url: string; primary: boolean; alpha: boolean; company: boolean }[];
    b: { url: string; primary: boolean; alpha: boolean; company: boolean }[];
    cover: string; a_url: string; b_url: string;
  }>(`
from store.models import Product, ProductImage, ProductModel, StorefrontImage
def rows(pid):
    product = Product.objects.get(pk=pid)
    result = []
    for image in ProductImage.objects.filter(product=product).order_by('sort_order', 'id'):
        stored = StorefrontImage.objects.get(public_id=image.image_url.rstrip('/').rsplit('/', 1)[-1])
        result.append({'url': image.image_url, 'primary': image.is_primary, 'alpha': stored.has_alpha,
                       'company': image.company_id == product.company_id == stored.company_id})
    return result
model = ProductModel.objects.get(pk=data['id'])
out({'a': rows(data['a']), 'b': rows(data['b']), 'cover': model.image_url,
     'a_url': Product.objects.get(pk=data['a']).image_url, 'b_url': Product.objects.get(pk=data['b']).image_url})
`, { id: seed.id, a: seed.a.id, b: seed.b.id });
  expect(db.a).toHaveLength(2);
  expect(db.b).toHaveLength(1);
  expect(db.a.map((row) => row.primary)).toEqual([false, true]);
  expect(db.a.every((row) => row.alpha), "el PNG conserva la transparencia").toBe(true);
  expect(db.b[0].alpha).toBe(false);
  expect([...db.a, ...db.b].every((row) => row.company)).toBe(true);
  expect(db.cover).toBe(db.a[0].url);
  expect(db.a_url).toBe(db.a[1].url);
  expect(db.b_url).toBe(db.b[0].url);

  // La tienda: la imagen sigue a la variante y empieza por la principal.
  const idA = publicIdOf(db.a[1].url);
  const idB = publicIdOf(db.b[0].url);
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: width < 500 ? 844 : 900 });
    await page.goto(`/modelo/${seed.slug}`, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { level: 1, name: seed.name })).toBeVisible({ timeout: 60_000 });
    const group = page.getByRole("radiogroup", { name: new RegExp(`^Acabado ${TAG}`) });
    await group.getByRole("radio", { name: /^Claro/ }).click();
    await expect.poll(() => mainImageSrc(page)).toContain(idA);
    await shot(page, `07-tienda-claro-${width}`);
    await group.getByRole("radio", { name: /^Oscuro/ }).click();
    await expect.poll(() => mainImageSrc(page)).toContain(idB);
    await shot(page, `08-tienda-oscuro-${width}`);
    expect(await overflow(page), `desborde horizontal de la tienda a ${width} px`).toBeLessThanOrEqual(1);
  }
});
