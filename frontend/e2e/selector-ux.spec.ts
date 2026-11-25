import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { test, expect, type Browser, type Locator, type Page } from "@playwright/test";

import { apiBase, RUN, shell } from "./inventory-first-support";

/**
 * SELECTOR-UX-01 — el selector de equipos compartido, de punta a punta con el backend de verdad.
 *
 * QUÉ ES REAL: navegador, frontend, la paleta del modelo (`/admin/catalog/models/<id>/palette/`), la resolución del
 * servidor (`/admin/catalog/resolve/`, con las exclusiones) y la base. Nada se simula con `page.route`.
 *
 * DATOS: una empresa PROPIA de esta corrida (`sel-<corrida>`) con un modelo inventado «Equipo Sel», su paleta (dos
 * capacidades, tres colores, eSIM y Chip, una región), un par EXCLUIDO (Verde · Chip) y una variante existente; y otra
 * empresa con un modelo del mismo nombre que nunca debe verse. La base debe ser local y desechable (guarda de
 * `inventory-first-support`). Al terminar, las empresas y el usuario quedan desactivados.
 *
 * Capturas en `SELECTOR_SHOTS` (por defecto `test-results/selector-ux`): escritorio 1280 y teléfono 390.
 */

const SHOTS = process.env.SELECTOR_SHOTS ?? "test-results/selector-ux";
fs.mkdirSync(SHOTS, { recursive: true });

const PASSWORD = `Sel-${crypto.randomBytes(9).toString("base64url")}!`;
const SLUG = `sel-${RUN.toLowerCase()}`;
const OTHER = `sel-otra-${RUN.toLowerCase()}`;
const USER = `qaesel_${RUN.toLowerCase()}`;
const OTHER_NAME = `OTRA EMPRESA ${RUN}`;

type Seed = { company: number; model: number; supplier: number; existing: string };
let SEED: Seed;
let STATE: Awaited<ReturnType<Awaited<ReturnType<Browser["newContext"]>>["storageState"]>> | null = null;

function sembrar(): Seed {
  return shell<Seed>(`
from django.contrib.auth import get_user_model
from store import device_catalog, model_palette
from store.company_provisioning import provision_company_access_defaults
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment, Product, Supplier
User = get_user_model()
def tenant(slug, variant_name):
    company = Company.objects.create(name='Empresa ' + slug, slug=slug)
    provision_company_access_defaults(company)
    attrs = {
        'capacity': device_catalog.create_attribute(company=company, name='Capacidad', role='capacity', sort_order=0),
        'color': device_catalog.create_attribute(company=company, name='Color', kind='color', role='color', sort_order=1),
        'sim': device_catalog.create_attribute(company=company, name='Tipo de SIM', role='sim', sort_order=2),
        'region': device_catalog.create_attribute(company=company, name='Región', role='region', sort_order=3),
    }
    o = {}
    for role, label, swatch in (('capacity', '128 GB', ''), ('capacity', '256 GB', ''), ('color', 'Negro', '#111111'),
                                ('color', 'Azul', '#1d4ed8'), ('color', 'Verde', '#15803d'), ('color', 'Rosa', '#f9a8d4'),
                                ('sim', 'eSIM', ''), ('sim', 'Chip', ''), ('region', 'J/A', '')):
        o[label] = device_catalog.create_option(company=company, attribute_id=attrs[role].pk, label=label, swatch_hex=swatch)
    model = device_catalog.create_model(company=company, name='Equipo Sel')
    model_palette.set_palette(model, option_ids=[o[l].pk for l in ('128 GB', '256 GB', 'Negro', 'Azul', 'Verde', 'eSIM', 'Chip', 'J/A')])
    model_palette.add_exclusions(model, pairs=[[o['Verde'].pk, o['Chip'].pk]])
    product = Product.objects.create(company=company, name=variant_name, slug=slug + '-v1', price=Decimal('1000.00'),
                                     inventory=0, is_published_online=False)
    device_catalog.set_variant(product, model_id=model.pk, condition_line='new',
                               option_ids=[o[l].pk for l in ('128 GB', 'Negro', 'eSIM', 'J/A')])
    return company, model
company, model = tenant(data['slug_sel'], 'Equipo Sel 128 GB Negro eSIM J/A')
tenant(data['other'], data['other_name'])
supplier = Supplier.objects.create(company=company, name='Proveedor Sel ' + data['tag'])
user = User.objects.create_user(username=data['user'], password=data['password'])
role = CompanyRole.objects.create(company=company, name='Rol ' + data['user'], slug=data['user'].replace('_', '-'),
                                  capabilities=['company.view', 'products.view', 'products.manage', 'costs.view'])
membership = Membership.objects.create(user=user, company=company, role='customer', is_active=True,
                                       branch_access_mode=Membership.ACCESS_MODE_ALL)
MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)
out({'company': company.pk, 'model': model.pk, 'supplier': supplier.pk, 'existing': 'Equipo Sel 128 GB Negro eSIM J/A'})
`, { slug_sel: SLUG, other: OTHER, other_name: OTHER_NAME, user: USER, password: PASSWORD });
}

function catalogo() {
  return shell<{ products: number; offers: number }>(`
from store.models import Product, SupplierOffer
out({'products': Product.objects.filter(company__slug=data['s']).count(),
     'offers': SupplierOffer.objects.filter(supplier__company__slug=data['s']).count()})
`, { s: SLUG });
}

function desactivar() {
  return shell(`
from django.contrib.auth import get_user_model
from store.models import Company
Company.objects.filter(slug__in=data['slugs']).update(is_active=False)
get_user_model().objects.filter(username=data['user']).update(is_active=False)
out({'ok': True})
`, { slugs: [SLUG, OTHER], user: USER });
}

async function login(browser: Browser) {
  const context = await browser.newContext();
  for (let attempt = 0; ; attempt++) {
    const answer = await context.request.post(`${apiBase()}/auth/login/`, { data: { username: USER, password: PASSWORD } });
    if (answer.status() === 200) break;
    if (answer.status() !== 429 || attempt === 2) throw new Error(`SELECTOR-UX-01: no pude iniciar sesión: ${answer.status()}`);
    await new Promise((resolve) => setTimeout(resolve, 62_000));
  }
  await context.request.get(`${apiBase()}/auth/csrf/`);
  STATE = await context.storageState();
  await context.close();
}

async function session(browser: Browser, viewport = { width: 1280, height: 900 }) {
  const context = await browser.newContext({ storageState: STATE!, viewport });
  return { context, page: await context.newPage() };
}

const LIST = [
  "equipo sel 128gb esim j/a negro, azul 2500",
  "equipo sel 128gb chip j/a 2700",
  "equipo sel 128gb esim j/a negro 2500 azul 2600",
].join("\n");

const lineOf = (page: Page, n: number) => page.getByRole("article", { name: `Línea ${n}`, exact: true });
const selectorOf = (page: Page, n: number) => page.getByRole("region", { name: `Selector visual · línea ${n}`, exact: true });
const colour = (scope: Locator, name: string) => scope.getByRole("group", { name: "Color", exact: true }).getByRole("checkbox", { name: new RegExp(name) });
/** A chip is its label (the input inside is visually hidden): a person clicks the chip. */
const chip = (scope: Locator, name: string) => scope.getByRole("group", { name: "Color", exact: true }).locator("label").filter({ hasText: name });

async function openAndRead(page: Page, text = LIST) {
  await page.goto("/admin/products/suppliers/offers/update-list", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Actualizar lista de proveedor" })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Texto de la lista del proveedor").fill(text);
  const resolved = page.waitForResponse((res) => /\/admin\/catalog\/resolve\/?$/.test(new URL(res.url()).pathname)
    && res.request().method() === "POST" && res.status() !== 308);
  await page.getByRole("button", { name: "Leer la lista" }).click();
  expect((await resolved).status()).toBe(200);
  await expect(page.getByRole("status", { name: "Resumen de la lectura" })).toBeVisible();
}

/** Opens a line's selector. A model's palette is read once: the next line of the same model reuses it. */
async function openSelector(page: Page, n: number) {
  await selectorOf(page, n).getByRole("button", { name: /Elegir en el selector visual/ }).click();
  await expect(selectorOf(page, n).getByRole("group", { name: "Color", exact: true })).toBeVisible({ timeout: 30_000 });
}

async function useSelection(page: Page, n: number) {
  const resolved = page.waitForResponse((res) => /\/admin\/catalog\/resolve\/?$/.test(new URL(res.url()).pathname)
    && res.request().method() === "POST" && res.status() !== 308);
  await selectorOf(page, n).getByRole("button", { name: /Usar esta selección/ }).click();
  const answer = await resolved;
  expect(answer.status()).toBe(200);
  return JSON.parse(answer.request().postData() ?? "{}") as { rows: Record<string, string>[] };
}

async function expectNoOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  test.setTimeout(240_000);
  SEED = sembrar();
  await login(browser);
});

test.afterAll(() => { desactivar(); });

test("asistente de listas: selector múltiple sobre la paleta real, exclusiones y dos ofertas en una línea (1280)", async ({ browser }) => {
  test.setTimeout(180_000);
  const before = catalogo();
  const { context, page } = await session(browser);
  await openAndRead(page);

  // Line 1: two colours read; the selector marks them, fixes the capacity the text says, and never offers Rosa.
  await expect(lineOf(page, 1).getByRole("article")).toHaveCount(2);
  await openSelector(page, 1);
  const one = selectorOf(page, 1);
  await expect(colour(one, "Negro")).toBeChecked();
  await expect(colour(one, "Azul")).toBeChecked();
  await expect(colour(one, "Verde")).not.toBeChecked();
  await expect(one.getByRole("checkbox", { name: /Rosa/ })).toHaveCount(0);
  await expect(one.getByRole("radiogroup", { name: "Capacidad" }).getByRole("radio", { name: /256 GB/ })).toBeDisabled();
  await chip(one, "Azul").click();
  await chip(one, "Verde").click();
  await expect(colour(one, "Azul")).not.toBeChecked();
  await expect(colour(one, "Verde")).toBeChecked();
  await expect(one.getByRole("group", { name: "Resultado del selector · línea 1" })).toContainText("Variante existente");
  await page.screenshot({ path: path.join(SHOTS, "desktop-1280-selector-multiple.png"), fullPage: false });
  await one.screenshot({ path: path.join(SHOTS, "desktop-1280-selector-linea.png") });
  const sent = await useSelection(page, 1);
  expect(sent.rows.map((row) => row.colour_text)).toEqual(["Negro", "Verde"]);
  expect(JSON.stringify(sent)).not.toContain("2500");
  const cards = lineOf(page, 1).getByRole("article");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toContainText("Con variante");
  await expect(cards.nth(0)).toContainText(SEED.existing);
  await expect(cards.nth(1)).toContainText("Sin variante");
  await expect(cards.nth(1)).toContainText("Elegido a mano (la lista no lo dice): Verde");

  // Line 2: Chip (said by the text) with Verde is a pair the model excludes — the server says it does not exist.
  await openSelector(page, 2);
  const two = selectorOf(page, 2);
  await chip(two, "Verde").click();
  await expect(two).toContainText("No existe en este modelo");
  await useSelection(page, 2);
  const excluded = lineOf(page, 2).getByRole("article");
  await expect(excluded).toContainText("No existe en este modelo");
  await expect(excluded.getByRole("button", { name: /Crear variante/ })).toHaveCount(0);

  // Line 3: two offers in one line — never saved as it is; split only when a person asks.
  const offers = page.getByRole("group", { name: "Varias ofertas en la línea 3" });
  await expect(offers).toContainText("equipo sel 128gb esim j/a azul 2600");
  await expect(lineOf(page, 3).getByRole("region", { name: /Selector visual/ })).toHaveCount(0);
  await lineOf(page, 3).screenshot({ path: path.join(SHOTS, "desktop-1280-dos-ofertas.png") });
  await offers.getByRole("button", { name: /Separar en 2 líneas/ }).click();
  await expect(page.getByLabel("Texto de la lista del proveedor")).toHaveValue(
    LIST.replace("equipo sel 128gb esim j/a negro 2500 azul 2600", "equipo sel 128gb esim j/a negro 2500\nequipo sel 128gb esim j/a azul 2600"));
  await expect(lineOf(page, 4)).toContainText("2600");

  // Another company's model of the same name is never read, and nothing was written.
  await expect(page.getByText(OTHER_NAME)).toHaveCount(0);
  await expectNoOverflow(page);
  expect(catalogo()).toEqual(before);
  await context.close();
});

test("teléfono 390: el selector cabe, se usa con el teclado y no desborda", async ({ browser }) => {
  test.setTimeout(120_000);
  const { context, page } = await session(browser, { width: 390, height: 844 });
  await openAndRead(page, "equipo sel 128gb esim j/a negro 2500");
  const toggle = selectorOf(page, 1).getByRole("button", { name: /Elegir en el selector visual/ });
  const palette = page.waitForResponse((res) => /\/palette\/?$/.test(new URL(res.url()).pathname) && res.status() !== 308);
  await toggle.focus();
  await page.keyboard.press("Enter");
  await palette;
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const azul = colour(selectorOf(page, 1), "Azul");
  await azul.focus();
  await page.keyboard.press("Space");
  await expect(azul).toBeChecked();
  await expectNoOverflow(page);
  await selectorOf(page, 1).screenshot({ path: path.join(SHOTS, "mobile-390-selector.png") });
  await context.close();
});
