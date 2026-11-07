// CATALOG-RICH-DESC-01 — descripción con formato en «Modelos y variantes» y en la tienda (navegador real).
//
// Entorno aislado de QA: siembra su propio modelo de prueba y un usuario Administrador de prueba (contraseña
// generada en la corrida, nunca impresa). RD_MODE=before sólo fotografía cómo se veía la descripción guardada
// por el campo de texto anterior (el mismo texto, sin editor); RD_MODE=after (por defecto) recorre el editor.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";
import { TAG, shell } from "./inventory-first-support";

const MODE = process.env.RD_MODE === "before" ? "before" : "after";
const OUT = process.env.RD_SHOTS ?? "test-results/rich-description";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3002";
const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";
const USER = `rd_admin_${TAG.toLowerCase().slice(-6)}`;
const PASSWORD = `Rd-${crypto.randomBytes(9).toString("base64url")}!`;
fs.mkdirSync(OUT, { recursive: true });

const SAMPLE = `**Apple iPhone 13 Pro Max**

El iPhone 13 Pro Max combina potencia, elegancia y fotografía profesional.

## Características principales

- Pantalla Super Retina XDR OLED.
- Tecnología ProMotion de 120 Hz.
- Procesador A15 Bionic.
- Triple cámara de 12 MP.
- Face ID.
- MagSafe.

## Capacidades

1. 128 GB
2. 256 GB
3. 512 GB
4. 1 TB`;

// Lo que da el portapapeles al seleccionar y copiar la respuesta ya formateada de ChatGPT.
const SAMPLE_HTML = `<p><strong>Apple iPhone 13 Pro Max</strong></p><p>El iPhone 13 Pro Max combina potencia, elegancia y fotografía profesional.</p>
<h2>Características principales</h2><ul><li><p>Pantalla Super Retina XDR OLED.</p></li><li><p>Tecnología ProMotion de 120 Hz.</p></li>
<li><p>Procesador A15 Bionic.</p></li><li><p>Triple cámara de 12 MP.</p></li><li><p>Face ID.</p></li><li><p>MagSafe.</p></li></ul>
<h2>Capacidades</h2><ol><li><p>128 GB</p></li><li><p>256 GB</p></li><li><p>512 GB</p></li><li><p>1 TB</p></li></ol>`;

const HOSTILE = `## Prueba de seguridad

<script>window.__pwned = 1</script>
<img src=x onerror="window.__pwned = 2">

[enlace malicioso](javascript:window.__pwned=3) y [enlace válido](https://example.com)`;

let model: { id: number; slug: string; name: string };

async function shot(page: Page, name: string, full = true) {
  await page.waitForTimeout(500);
  if (full) {
    const width = page.viewportSize()!.width;
    const height = await page.evaluate(() => Math.ceil(document.documentElement.scrollHeight));
    await page.setViewportSize({ width, height: Math.min(height, 6000) });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, `${MODE}-${name}.png`) });
    await page.setViewportSize({ width, height: width < 500 ? 844 : 900 });
  } else {
    await page.screenshot({ path: path.join(OUT, `${MODE}-${name}.png`) });
  }
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

const stored = () => shell<string>(`from store.models import ProductModel\nout(ProductModel.objects.get(pk=data['id']).description)`, { id: model.id });

test.describe.configure({ mode: "serial" });
test.use({ baseURL: BASE, viewport: { width: 1280, height: 900 } });

test.beforeAll(async () => {
  test.setTimeout(120_000);
  model = shell<typeof model>(`
from decimal import Decimal
from django.contrib.auth import get_user_model
from store import device_catalog
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment, Product
company = Company.objects.get(slug=data['slug'])
u = get_user_model().objects.create_user(username=data['user'], password=data['password'], first_name='Catálogo')
m = Membership.objects.create(user=u, company=company, role='inventory', is_active=True, branch_access_mode=Membership.ACCESS_MODE_ALL)
MembershipRoleAssignment.objects.create(membership=m, role=CompanyRole.objects.get(company=company, slug='administrador'), is_active=True)
name = 'iPhone 13 Pro Max ' + data['tag']
model = device_catalog.create_model(company=company, name=name, is_published_online=True,
    image_url='https://images.example.com/catalogo/' + 'carpeta-muy-larga/' * 12 + 'iphone-13-pro-max.png')
variant = Product.objects.create(company=company, name=name + ' 128 GB', slug=('rd-' + data['tag'] + '-128').lower(),
    price=Decimal('3999.00'), inventory=0, is_active=True, is_published_online=True)
device_catalog.set_variant(variant, model_id=model.pk, condition_line='new', option_ids=[])
if data['mode'] == 'before':
    # Lo que guardaba el campo de texto anterior al pegar el Markdown de ChatGPT: el mismo texto.
    device_catalog.update_model(model, description=data['sample'])
out({'id': model.pk, 'slug': model.slug, 'name': model.name})
`, { slug: "black-dog-store", user: USER, password: PASSWORD, tag: TAG, mode: MODE, sample: SAMPLE });
});

test("descripción del modelo: panel y tienda", async ({ page }) => {
  test.setTimeout(300_000);
  const dialogs: string[] = [];
  page.on("dialog", (d) => { dialogs.push(d.message()); void d.dismiss(); });
  await login(page);
  await page.goto(`/admin/products/models/${model.id}`, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: model.name }).first()).toBeVisible({ timeout: 60_000 });

  if (MODE === "after") {
    await page.getByRole("button", { name: "Editar modelo" }).click();
    const field = page.getByLabel("Descripción", { exact: true });
    await field.fill("");
    // Pegado real: un ClipboardEvent con el HTML y el texto de una respuesta de ChatGPT.
    await field.focus();
    await page.evaluate(([html, text]) => {
      const area = document.activeElement as HTMLTextAreaElement;
      const data = new DataTransfer();
      data.setData("text/html", html);
      data.setData("text/plain", text);
      area.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    }, [SAMPLE_HTML, SAMPLE]);
    await expect(field).toHaveValue(SAMPLE);
    await shot(page, "01-editor-desktop");
    await page.getByRole("tab", { name: "Vista previa" }).click();
    const preview = page.getByRole("tabpanel", { name: "Vista previa de la descripción" });
    await expect(preview.getByRole("heading", { level: 2 })).toHaveCount(2);
    await shot(page, "02-vista-previa-desktop");
    await page.getByRole("tab", { name: "Escribir" }).click();
    await page.getByRole("button", { name: "Guardar cambios" }).click();
    await expect(page.getByText("Modelo guardado.")).toBeVisible({ timeout: 30_000 });
    expect(stored()).toBe(SAMPLE);
    // Persistencia: se recarga y se vuelve a abrir el editor con el mismo Markdown.
    await page.reload({ waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Editar descripción" }).click();
    await expect(page.getByLabel("Descripción", { exact: true })).toHaveValue(SAMPLE);
    await page.getByRole("button", { name: "Cancelar" }).click();
  }

  // Detalle administrativo.
  const adminBox = MODE === "after" ? page.getByTestId("model-description-preview") : page.locator("dd.whitespace-pre-line");
  await expect(adminBox).toContainText("Características principales");
  if (MODE === "after") {
    await expect(adminBox.getByRole("heading", { level: 2 })).toHaveCount(2);
    await expect(adminBox.locator("li")).toHaveCount(10);
    await expect(adminBox).not.toContainText("**");
    await expect(page.getByRole("img", { name: `Imagen de ${model.name}` })).toBeVisible();
  }
  await shot(page, "03-detalle-panel-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  await shot(page, "04-detalle-panel-390");
  await page.setViewportSize({ width: 1280, height: 900 });

  // Tienda pública.
  await page.goto(`/modelo/${model.slug}`, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { level: 1, name: model.name })).toBeVisible({ timeout: 60_000 });
  const pub = MODE === "after" ? page.getByTestId("model-description") : page.locator("header p").filter({ hasText: "Características" });
  await expect(pub).toContainText("Capacidades");
  if (MODE === "after") {
    await expect(pub.getByRole("heading", { level: 2 })).toHaveCount(2);
    await expect(pub.locator("ul > li")).toHaveCount(6);
    await expect(pub.locator("ol > li")).toHaveCount(4);
    await expect(pub).not.toContainText("**");
    await expect(pub).not.toContainText("##");
    expect(await page.getByRole("heading", { level: 1 }).count()).toBe(1);
  } else {
    expect(await pub.innerText()).toContain("**Apple iPhone 13 Pro Max**");
  }
  await shot(page, "05-tienda-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: "networkidle" });
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, "desborde horizontal a 390 px").toBeLessThanOrEqual(1);
  await shot(page, "06-tienda-390");
  await page.setViewportSize({ width: 1280, height: 900 });

  if (MODE === "after") {
    // Contenido malicioso: se guarda como texto y la tienda no ejecuta nada.
    await page.goto(`/admin/products/models/${model.id}`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Editar descripción" }).click();
    await page.getByLabel("Descripción", { exact: true }).fill(HOSTILE);
    await page.getByRole("button", { name: "Guardar cambios" }).click();
    await expect(page.getByText("Modelo guardado.")).toBeVisible({ timeout: 30_000 });
    expect(stored()).toBe(HOSTILE);
    await page.goto(`/modelo/${model.slug}`, { waitUntil: "networkidle" });
    const box = page.getByTestId("model-description");
    await expect(box).toContainText("<script>window.__pwned = 1</script>");
    expect(await box.locator("script, img, iframe").count()).toBe(0);
    expect(await box.locator('a[href^="javascript"]').count()).toBe(0);
    await expect(box.getByRole("link", { name: "enlace válido" })).toHaveAttribute("href", "https://example.com");
    await page.waitForTimeout(800);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned ?? 0)).toBe(0);
    expect(dialogs).toEqual([]);
    await shot(page, "07-tienda-contenido-malicioso", false);
    // Se deja el ejemplo para las capturas finales.
    shell(`from store import device_catalog\nfrom store.models import ProductModel\ndevice_catalog.update_model(ProductModel.objects.get(pk=data['id']), description=data['sample'])\nout(1)`, { id: model.id, sample: SAMPLE });
  }
});
