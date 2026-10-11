import { execFileSync } from "node:child_process";
import { test, expect, type Page } from "@playwright/test";

/**
 * SERIAL-PICK-01 — el equipo que sale es el que alguien identificó, en navegador real.
 *
 * SIN MOCKS DEL BACKEND. Cuatro equipos de un mismo modelo entran al inventario
 * del backend local (`E2E_BACKEND_DIR`), y se venden como lo hace una persona:
 *
 *   A · en caja con el lector: el IMEI entra a ráfaga y la línea se llena sola;
 *   B · en caja a mano: sin equipo no se cobra, un IMEI con un dígito cambiado
 *       se rechaza, y el correcto se MUESTRA para confirmarlo antes de aceptarlo;
 *   C · un pedido web: no se puede entregar sin decir qué equipo sale, y el que
 *       se entrega puede no ser el que el pago apartó.
 *
 * Lo que el servidor guarda de cada uno —cómo se identificó y quién— se lee al final.
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? "../backend";
const MODELO = `Equipo serie ${RUN}`;

/** Un IMEI válido: 14 dígitos y su dígito de control (Luhn). */
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
const otroDigito = (value: string) => value.slice(0, -1) + String((Number(value.slice(-1)) + 1) % 10);

// En orden de llegada: el pago del pedido web aparta el más antiguo (APARTADO);
// el que se entrega es otro (ENTREGADO).
const APARTADO = { serie: `E2EAP${RUN}`.toUpperCase(), imei: imei(1) };
const ENTREGADO = { serie: `E2EEN${RUN}`.toUpperCase(), imei: imei(2) };
const LECTOR = { serie: `E2ELE${RUN}`.toUpperCase(), imei: imei(3) };
const A_MANO = { serie: `E2EMA${RUN}`.toUpperCase(), imei: imei(4) };

type Siembra = { product: number; order: number; branch: string; company: number };

/** El modelo, sus cuatro equipos y un pedido web pagado de uno de ellos. */
function sembrar(): Siembra {
  const codigo = `
import json, os
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from store import inventory_services, stock_unit_services
from store.models import Company, Order, OrderItem, Product

data = json.loads(os.environ['E2E_SEED'])
actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug='black-dog-store')
branch = company.default_inventory_branch
product = Product.objects.create(
    company=company, name=data['name'], slug=data['slug'], price=Decimal('999.00'), inventory=0,
    is_active=True, is_serialized=True, requires_imei=True,
    # Sólo stock interno: la tienda pública, y las pruebas que eligen de ella, no lo ven.
    is_published_online=False,
)
stock_unit_services.receive_units(
    branch=branch, product=product, actor=actor, reason='Prueba de navegador',
    units=[{'serial_number': u['serie'], 'imei': u['imei']} for u in data['units']],
)
order = Order.objects.create(
    company=company, fulfillment_branch=branch, customer_email='e2e-serie@example.com',
    total=product.price, status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
)
OrderItem.objects.create(order=order, product=product, quantity=1, price=product.price)
inventory_services.record_sale_stock_movements(order)
print('E2E_SEEDED ' + json.dumps({'product': product.pk, 'order': order.pk, 'branch': branch.name, 'company': company.pk}))
`;
  const out = execFileSync("python3", ["manage.py", "shell", "-c", codigo], {
    cwd: BACKEND_DIR,
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      E2E_SEED: JSON.stringify({
        name: MODELO, slug: `equipo-serie-${RUN}`, units: [APARTADO, ENTREGADO, LECTOR, A_MANO],
      }),
    },
  }).toString();
  const line = out.split("\n").find((row) => row.startsWith("E2E_SEEDED "));
  if (!line) throw new Error(`la siembra no terminó: ${out.slice(-400)}`);
  return JSON.parse(line.slice("E2E_SEEDED ".length));
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

/** Lo que el panel sabe de un pedido, preguntado con la sesión de quien lo mira. */
async function pedido(page: Page, id: number) {
  return page.evaluate(async (raw) => {
    // La API en el mismo nombre de máquina que la página: es donde vive la sesión.
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

/** Cobra lo que hay en la cesta, en efectivo y con nota interna. Devuelve el número de pedido. */
async function cobrar(page: Page): Promise<number> {
  await page.getByRole("checkbox").first().check();
  await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
  await page.getByLabel("Efectivo recibido").fill("99999");
  const boton = page.getByRole("button", { name: /^cobrar$/i });
  await expect(boton).toBeEnabled({ timeout: 15_000 });
  await boton.click();
  await expect(page.getByText(/Venta registrada/i), "la venta no se completó").toBeVisible({ timeout: 40_000 });
  const texto = await page.locator("body").innerText();
  return Number(/Pedido #(\d+)/.exec(texto)?.[1]);
}

test.describe("el equipo que sale es el que alguien identificó", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  let siembra: Siembra;
  test.beforeAll(() => { siembra = sembrar(); });

  test("A · caja, con el lector: el IMEI entra a ráfaga y la venta lleva ese equipo", async ({ page }) => {
    await signIn(page);
    await abrirCaja(page);

    // Un lector es un teclado que escribe sin pausas, y termina con Enter.
    const campo = page.getByLabel("Escanear código");
    await campo.click();
    await campo.pressSequentially(LECTOR.imei, { delay: 4 });
    await campo.press("Enter");

    // Entra solo, sin pedir confirmación, y la línea dice cuál es y cómo llegó.
    await expect(page.getByText(new RegExp(`IMEI ${LECTOR.imei}`)).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("group", { name: "Confirma el equipo" })).toHaveCount(0);
    await expect(page.getByText("Lector", { exact: true })).toBeVisible();
    await expect(page.getByText(/Falta identificar/)).toHaveCount(0);

    // El mismo equipo otra vez no entra dos veces.
    await campo.click();
    await campo.pressSequentially(LECTOR.imei, { delay: 4 });
    await campo.press("Enter");
    await expect(page.getByText(/ya está en esta venta/)).toBeVisible({ timeout: 20_000 });

    const orden = await cobrar(page);
    await expect(page.getByText(new RegExp(`IMEI ${LECTOR.imei}`)).first()).toBeVisible();

    const { body } = await pedido(page, orden);
    const equipos = body.items.flatMap((item: { units: unknown[] }) => item.units);
    expect(equipos).toHaveLength(1);
    expect(equipos[0]).toMatchObject({ imei: LECTOR.imei, serial_number: LECTOR.serie, method: "scanner", identified_by: "dev_admin" });
    expect(body.items[0].price).toBe("999.00");                       // el precio del producto
  });

  test("B · caja, a mano: sin equipo no se cobra; se valida, se muestra y se confirma", async ({ page }) => {
    await signIn(page);
    await abrirCaja(page);

    // El modelo, buscado por nombre: entra a la cesta pidiendo su equipo.
    await page.getByLabel("Buscar producto").fill(MODELO);
    const fila = page.locator("button:not([disabled])").filter({ hasText: MODELO }).filter({ hasText: /\d+\s*disp\./ }).first();
    await expect(fila).toBeVisible({ timeout: 20_000 });
    await fila.click();
    await expect(page.getByText(/Falta identificar 1 equipo/).first()).toBeVisible();

    // Con todo lo demás listo, no se cobra.
    await page.getByRole("checkbox").first().check();
    await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
    await page.getByLabel("Efectivo recibido").fill("99999");
    await expect(page.getByRole("button", { name: /^cobrar$/i })).toBeDisabled();

    // «El lector no lee»: un IMEI con un dígito cambiado no llega ni a preguntarse.
    await page.getByRole("button", { name: "El lector no lee: escribirlo a mano" }).click();
    const campo = page.getByLabel("IMEI o serie del equipo");
    await campo.fill(otroDigito(A_MANO.imei));
    await campo.press("Enter");
    await expect(page.getByRole("alert").filter({ hasText: /no es válido/ })).toBeVisible();
    await expect(page.getByRole("group", { name: "Confirma el equipo" })).toHaveCount(0);

    // A mano, un equipo con IMEI no se identifica por su serie: la línea lo dice sin preguntar…
    await campo.fill(A_MANO.serie);
    await campo.press("Enter");
    await expect(page.getByRole("alert").filter({ hasText: /15 dígitos/ })).toBeVisible();
    // …y el servidor dice lo mismo a quien la pega en el campo de escaneo.
    const escaner = page.getByLabel("Escanear código");
    await escaner.fill(A_MANO.serie);
    await escaner.press("Enter");
    await expect(page.getByText(/se identifica por su IMEI/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("group", { name: "Confirma el equipo" })).toHaveCount(0);

    // El correcto se muestra: modelo, estado, IMEI y serie. «No es este» no añade nada.
    await campo.fill(A_MANO.imei);
    await campo.press("Enter");
    const confirma = page.getByRole("group", { name: "Confirma el equipo" });
    await expect(confirma).toBeVisible({ timeout: 20_000 });
    await expect(confirma).toContainText(MODELO);
    await expect(confirma).toContainText(A_MANO.imei);
    await expect(confirma).toContainText(A_MANO.serie);
    await confirma.getByRole("button", { name: "No es este" }).click();
    await expect(page.getByRole("button", { name: /^cobrar$/i })).toBeDisabled();

    await campo.fill(A_MANO.imei);
    await campo.press("Enter");
    await page.getByRole("group", { name: "Confirma el equipo" }).getByRole("button", { name: "Confirmar este equipo" }).click();
    await expect(page.getByText("A mano", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/Falta identificar/)).toHaveCount(0);

    const orden = await cobrar(page);
    const { body } = await pedido(page, orden);
    const equipos = body.items.flatMap((item: { units: unknown[] }) => item.units);
    expect(equipos).toHaveLength(1);
    // Queda escrito que se ingresó a mano, y quién lo hizo.
    expect(equipos[0]).toMatchObject({ imei: A_MANO.imei, method: "manual", identified_by: "dev_admin" });
  });

  test("C · pedido web: no se entrega sin decir qué equipo sale", async ({ page }) => {
    await signIn(page);
    await page.goto(`/admin/orders/${siembra.order}`, { waitUntil: "networkidle" });

    await expect(page.getByRole("heading", { name: "Equipos que se entregan" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/Falta identificar 1 equipo/)).toBeVisible();
    // El pago apartó un equipo, pero nadie lo ha identificado: no se muestra ninguno.
    await expect(page.locator("body")).not.toContainText(APARTADO.imei);

    // Marcarlo entregado así se rechaza, con el motivo.
    await page.getByLabel("Estado de despacho").selectOption("delivered");
    await page.getByRole("button", { name: "Guardar" }).click();
    await expect(page.getByText(/identific/i).last()).toBeVisible({ timeout: 20_000 });
    expect((await pedido(page, siembra.order)).body.fulfillment_status).not.toBe("delivered");

    // Se entrega OTRO equipo del mismo modelo, escrito a mano: se muestra y se confirma.
    await page.getByRole("button", { name: "El lector no lee: escribirlo a mano" }).click();
    const campo = page.getByLabel("IMEI o serie del equipo");
    await campo.fill(ENTREGADO.imei);
    await campo.press("Enter");
    const confirma = page.getByRole("group", { name: "Confirma el equipo" });
    await expect(confirma).toContainText(MODELO, { timeout: 20_000 });
    await expect(confirma).toContainText(ENTREGADO.imei);
    await confirma.getByRole("button", { name: "Confirmar este equipo" }).click();

    await expect(page.getByText(/Todos los equipos de este pedido están identificados/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(new RegExp(`IMEI ${ENTREGADO.imei}`))).toBeVisible();
    await expect(page.getByText(/Ingreso manual/)).toBeVisible();

    let { body } = await pedido(page, siembra.order);
    expect(body.units_pending).toBe(0);
    expect(body.items[0].units[0]).toMatchObject({ imei: ENTREGADO.imei, method: "manual", identified_by: "dev_admin" });

    // Ahora sí se entrega, y lo entregado ya no se cambia.
    await page.getByLabel("Estado de despacho").selectOption("delivered");
    await page.getByRole("button", { name: "Guardar" }).click();
    await expect.poll(async () => (await pedido(page, siembra.order)).body.fulfillment_status, { timeout: 20_000 }).toBe("delivered");
    await expect(page.getByRole("button", { name: /Quitar el equipo/ })).toHaveCount(0);
    ({ body } = await pedido(page, siembra.order));
    expect(body.units_can_change).toBe(false);

    // Devolver el pedido a «en preparación» no reabre el equipo que salió.
    await page.getByLabel("Estado de despacho").selectOption("preparing");
    await page.getByRole("button", { name: "Guardar" }).click();
    await expect.poll(async () => (await pedido(page, siembra.order)).body.fulfillment_status, { timeout: 20_000 }).toBe("preparing");
    await expect(page.getByText(new RegExp(`IMEI ${ENTREGADO.imei}`))).toBeVisible();
    await expect(page.getByRole("button", { name: /Quitar el equipo/ })).toHaveCount(0);
    ({ body } = await pedido(page, siembra.order));
    expect(body.items[0].units[0].can_remove).toBe(false);

    // El equipo que el pago había apartado volvió al estante: la caja lo puede vender.
    // Pegado en el campo de escaneo no hay teclas que medir: cuenta como escrito
    // a mano, y se muestra para confirmar en vez de entrar solo.
    await abrirCaja(page);
    const escaner = page.getByLabel("Escanear código");
    await escaner.fill(APARTADO.imei);
    await escaner.press("Enter");
    const enCaja = page.getByRole("group", { name: "Confirma el equipo" });
    await expect(enCaja).toContainText(APARTADO.serie, { timeout: 20_000 });
    await enCaja.getByRole("button", { name: "No es este" }).click();
    await expect(page.getByText(new RegExp(`IMEI ${APARTADO.imei}`))).toHaveCount(0);

    // Tecleado al ritmo de una persona, lo mismo; confirmado, queda como «a mano».
    await escaner.click();
    await escaner.pressSequentially(APARTADO.imei, { delay: 130 });
    await escaner.press("Enter");
    await expect(enCaja).toContainText(MODELO, { timeout: 20_000 });
    await enCaja.getByRole("button", { name: "Confirmar este equipo" }).click();
    await expect(page.getByText(new RegExp(`IMEI ${APARTADO.imei}`)).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("A mano", { exact: true })).toBeVisible();
    // Y el entregado ya no.
    await escaner.click();
    await escaner.pressSequentially(ENTREGADO.imei, { delay: 4 });
    await escaner.press("Enter");
    await expect(page.getByText(/no se puede vender/)).toBeVisible({ timeout: 20_000 });
  });
});
