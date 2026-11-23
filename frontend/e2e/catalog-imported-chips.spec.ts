import { test, expect, type Page } from "@playwright/test";
import { shell } from "./inventory-first-support";

/**
 * CATALOG-REFERENCE-01 · PR G — modelos importados del catálogo de referencia, tal como se ven (navegador real).
 *
 * SIN MOCKS DEL BACKEND. iPhone 17 Pro se importa del archivo de referencia cargado en la base local
 * (`load_reference_catalog`), con una variante publicada por capacidad × color y un equipo de cada una:
 *
 *   · las capacidades se leen por tamaño (256 GB, 512 GB, 1 TB) en la ficha pública y en la caja;
 *   · un color con foto de acabado propia muestra esa foto en su chip; uno sin foto y con la muestra neutra del
 *     importador no muestra ningún punto (sólo su nombre), y uno con muestra de la empresa la muestra;
 *   · el panel dice «Muestra sin configurar» y deja guardar la muestra de la empresa.
 *
 * Requiere la base local con el catálogo cargado. Las capacidades sólo salen ordenadas si las crea esta importación
 * (una base de antes del arreglo conserva sus opciones: G no reordena lo que existe).
 * Nombres oficiales del catálogo de referencia (AGENTS §2, segunda excepción); los colores de las fotos son inventados.
 */

const SHOTS = process.env.E2E_SCREENSHOTS ?? "";
const KEY = "iphone-17-pro";

type Seed = { id: number; slug: string; name: string; pictured: string[]; unset: string };
let seed: Seed;

async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  await card.locator("li").filter({ hasText: "dev_admin" }).getByRole("button", { name: "Usar cuenta" }).click();
  await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
  await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 30_000 });
}

test.describe.configure({ mode: "serial" });

test.beforeAll(() => {
  test.setTimeout(180_000);
  seed = shell<Seed>(`
import io
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from store import catalog_reference_import as importer, checkout_services as checkout, device_catalog, finish_media
from store import model_palette, stock_unit_services as units
from store.models import Category, Company, Product, ProductModel, ReferenceModel, VariantOption
actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug=data['slug'])
branch = checkout.resolve_fulfillment_branch(company)
if not ReferenceModel.objects.filter(key=data['key']).exists():
    raise SystemExit('el catálogo de referencia no está cargado: load_reference_catalog')
model = ProductModel.objects.filter(company=company, reference_key=data['key']).first()
if model is None:
    importer.apply(company=company, keys=[data['key']], actor=actor)
    model = ProductModel.objects.get(company=company, reference_key=data['key'])
palette = model_palette.palette_of(model)
by_role = {}
for attribute_id, ids in palette.items():
    for option in VariantOption.objects.filter(pk__in=ids).select_related('attribute'):
        by_role.setdefault(option.attribute.role, []).append(option)
for options in by_role.values():
    options.sort(key=lambda o: (o.sort_order, o.label))
excluded = palette.excluded
category = Category.objects.filter(company=company).first() or Category.objects.create(
    company=company, name='Teléfonos', slug='telefonos-e2e')
stamp = str(int(__import__('time').time()))[-6:]

def imei(n):
    body = '35' + stamp + f'{n:06d}'
    total = 0
    for index, char in enumerate(reversed(body)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            digit = digit - 9 if digit > 9 else digit
        total += digit
    return body + str((10 - total % 10) % 10)

n = 0
for cap in by_role['capacity']:
    for colour in by_role['color']:
        if Product.objects.filter(product_model=model, variant_options__option=cap).filter(variant_options__option=colour).exists():
            continue
        pair = next((s, r) for s in by_role['sim'] for r in by_role['region'] if frozenset({s.pk, r.pk}) not in excluded)
        product = device_catalog.create_variant_product(company, model, condition_line='new',
                                                        option_ids=[cap.pk, colour.pk, pair[0].pk, pair[1].pk],
                                                        actor=actor, request=None)
        Product.objects.filter(pk=product.pk).update(price=Decimal('4999.00') + 1000 * by_role['capacity'].index(cap),
                                                     category=category, is_serialized=True, requires_imei=True)
        device_catalog.enable_variant_for_sale(Product.objects.get(pk=product.pk), publish=True, actor=actor, request=None)
        units.receive_units(branch=branch, product=Product.objects.get(pk=product.pk), actor=actor,
                            reason='Prueba de navegador', units=[{'imei': imei(n)}])
        n += 1
ProductModel.objects.filter(pk=model.pk).update(is_published_online=True)
colours = {o.reference_key: o for o in by_role['color']}

def png(rgb):
    buffer = io.BytesIO()
    image = Image.new('RGBA', (800, 800), (0, 0, 0, 0))
    image.paste(Image.new('RGBA', (360, 700), rgb + (255,)), (220, 50))
    image.save(buffer, 'PNG')
    return buffer.getvalue()

pictured = []
# Our own drawings, in the colour of each finish (never a manufacturer's photo): silver and orange; deep blue stays
# without a picture and with the importer's neutral default.
for colour, rgb in ((colours['color:silver'], (205, 205, 210)), (colours['color:cosmic-orange'], (214, 110, 40))):
    if not finish_media.gallery(model, colour).exists():
        finish_media.add_image(model=model, color_option_id=colour.pk, actor=actor,
                               uploaded=SimpleUploadedFile('acabado.png', png(rgb), content_type='image/png'),
                               provenance={'source_kind': 'own_photo'}, alt_text=model.name + ' ' + colour.label)
    # Owner, 2026-11-23: on the shop only once its licence is verified (our own drawings; a rerun verifies them too).
    for image in finish_media.gallery(model, colour):
        finish_media.update_image(image=image, actor=actor, license_verified=True)
    pictured.append(colour.label)
unset = colours['color:deep-blue']
VariantOption.objects.filter(pk=unset.pk).update(swatch_hex=device_catalog.NEUTRAL_SWATCH)
out({'id': model.pk, 'slug': model.slug, 'name': model.name, 'pictured': pictured, 'unset': unset.label})
`, { key: KEY });
});

test("ficha pública: capacidades por tamaño y chips de color con foto o sólo nombre (375 y 1280, claro y oscuro)", async ({ page }) => {
  test.setTimeout(180_000);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
      await page.goto(`/modelo/${seed.slug}`, { waitUntil: "networkidle" });
      await expect(page.getByRole("heading", { level: 1, name: seed.name })).toBeVisible({ timeout: 60_000 });
      const reject = page.getByRole("button", { name: /Rechazar/ });
      if (await reject.count()) await reject.first().click();
      const capacities = page.getByRole("radiogroup", { name: "Capacidad" }).getByRole("radio");
      await expect(capacities).toHaveText([/^256 GB/, /^512 GB/, /^1 TB/]);
      const colours = page.getByRole("radiogroup", { name: /^Color/ });
      for (const label of seed.pictured) {
        await expect(colours.getByRole("radio", { name: new RegExp(`^${label}`) }).getByTestId("v4-chip-picture")).toHaveCount(1);
      }
      const unset = colours.getByRole("radio", { name: new RegExp(`^${seed.unset}`) });
      await expect(unset).toContainText(seed.unset);
      await expect(unset.locator(".v4-swatch")).toHaveCount(0);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(1);
      await shot(page, `imported-ficha-${width}-${scheme === "light" ? "claro" : "oscuro"}`);
    }
  }
});

test("caja y panel: el mismo orden, la misma foto en el chip y «Muestra sin configurar» con su control", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page);
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
    await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Vender equipo por modelo" }).click();
    await page.getByLabel("Modelo", { exact: true }).fill(seed.name);
    await page.getByRole("button", { name: new RegExp(`^${seed.name}\\b`) }).first().click();
    const capacity = page.getByRole("radiogroup").first();
    await expect(capacity.locator("label")).toHaveText([/^256 GB/, /^512 GB/, /^1 TB/]);
    const colour = page.getByRole("radiogroup").nth(1);
    for (const label of seed.pictured) {
      await expect(colour.locator("label").filter({ hasText: label }).getByTestId("pos-chip-picture")).toHaveCount(1);
    }
    const unset = colour.locator("label").filter({ hasText: seed.unset });
    await expect(unset.getByTestId("pos-chip-picture")).toHaveCount(0);
    await expect(unset.locator('[style*="background"]')).toHaveCount(0);
    await shot(page, `imported-caja-${width}-claro`);
    await page.emulateMedia({ colorScheme: "dark" });
    await shot(page, `imported-caja-${width}-oscuro`);
    await page.emulateMedia({ colorScheme: "light" });
  }

  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
    await page.goto(`/admin/products/models/${seed.id}`, { waitUntil: "networkidle" });
    const finishes = page.getByRole("region", { name: "Imágenes por acabado" });
    const unsetFinish = finishes.getByRole("listitem", { name: seed.unset });
    await expect(unsetFinish.getByText("Muestra sin configurar")).toBeVisible({ timeout: 30_000 });
    await expect(unsetFinish.getByRole("button", { name: "Guardar muestra" })).toBeDisabled();
    await expect(page.getByText("Muestra sin configurar").first()).toBeVisible();
    await unsetFinish.scrollIntoViewIfNeeded();
    await shot(page, `imported-panel-${width}-claro`);
    await page.emulateMedia({ colorScheme: "dark" });
    await shot(page, `imported-panel-${width}-oscuro`);
    await page.emulateMedia({ colorScheme: "light" });
  }
});
