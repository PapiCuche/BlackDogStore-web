import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, readdirSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { api, signIn } from "./media-helpers";

/**
 * INTEGRATIONS-CONSOLE — Configuración › Integraciones, con navegador de verdad.
 *
 *   · un MASTER configura el correo, lo prueba contra un servidor SMTP y ve
 *     «Correcto»; lo activa y lo revoca;
 *   · quien no es MASTER no llega: ni por el menú, ni escribiendo la dirección,
 *     ni llamando a la API (403);
 *   · Google, Izipay y WhatsApp se pueden configurar con valores inventados, y
 *     ninguno de sus secretos vuelve al navegador.
 *
 * El servidor de correo es el del ensayo de producción (`deploy/rehearsal_smtp_sink.py`):
 * escucha en esta máquina, guarda lo que recibe en una carpeta temporal y no
 * reenvía nada. NINGUNA PRUEBA DE AQUÍ SALE A INTERNET: no se pulsa «Probar
 * conexión» en Google ni en Izipay, que hablarían con sus servidores.
 *
 * Cada prueba deja la consola como la encontró (revoca lo que guardó): las
 * demás pruebas comparten esta base de datos y su correo de desarrollo.
 */

const CONSOLE = "/admin/settings/integrations";
const SINK = path.resolve(process.cwd(), "../deploy/rehearsal_smtp_sink.py");
const NOTICE = "Estas credenciales controlan servicios externos de la empresa. Sólo usuarios MASTER pueden modificarlas.";
// Inventados aquí. No son de ningún servicio.
const MAIL_PASSWORD = `e2e-correo-${Date.now().toString(36)}-NoEsReal`;
const MCW_PASSWORD = "testpassword_E2ENoEsRealNoEsRealNoEsReal";
const WHATSAPP_TOKEN = "e2e-token-de-acceso-NoEsReal";

let sink: ChildProcess | null = null;
let sinkPort = 0;
let sinkFolder = "";

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      probe.close(() => resolve(typeof address === "object" && address ? address.port : 0));
    });
  });
}

test.beforeAll(async () => {
  sinkPort = await freePort();
  sinkFolder = mkdtempSync(path.join(tmpdir(), "e2e-correo-"));
  sink = spawn("python3", [SINK, String(sinkPort), sinkFolder, "e2e", MAIL_PASSWORD], { stdio: "ignore" });
  await new Promise((resolve) => setTimeout(resolve, 1500));
});

test.afterAll(() => {
  sink?.kill();
  if (sinkFolder) rmSync(sinkFolder, { recursive: true, force: true });
});

const card = (page: Page, name: string) => page.getByRole("article", { name, exact: true });

async function openEditor(page: Page, name: string) {
  await page.goto(CONSOLE, { waitUntil: "networkidle" });
  await card(page, name).getByRole("button", { name: "Configurar" }).click();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible({ timeout: 20_000 });
}

/** Deja la integración sin nada guardado, pase lo que pase en la prueba. */
async function revoke(page: Page, id: string, companyId?: number) {
  const query = companyId ? `?company=${companyId}` : "";
  await api(page, "POST", `/api/admin/integrations/${id}/revoke/${query}`, { confirm: "REVOCAR" }).catch(() => null);
}

/** Lo escrito no puede haber quedado ni en la página ni en el almacenamiento del navegador. */
async function expectNotKept(page: Page, secret: string) {
  expect(await page.content(), "el secreto quedó en la página").not.toContain(secret);
  const stored = await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }, document.cookie]));
  expect(stored, "el secreto quedó en el navegador").not.toContain(secret);
}

test("un MASTER configura el correo, lo prueba, lo activa y lo revoca", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page, "dev_master");
  try {
    await page.goto(CONSOLE, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "Integraciones", exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(NOTICE)).toBeVisible();
    for (const name of ["Correo SMTP", "Izipay · SDK web / Checkout", "Izipay · Mi Cuenta Web", "WhatsApp Business",
                        "Inicio de sesión con Google", "SUNAT · Facturación electrónica"]) {
      await expect(card(page, name), `falta la tarjeta de ${name}`).toBeVisible();
    }
    await expect(page.locator(`a[href="${CONSOLE}"]`).first(), "el MASTER no la tiene en su menú").toBeAttached();

    await card(page, "Correo SMTP").getByRole("button", { name: "Configurar" }).click();
    await expect(page.getByRole("heading", { name: "Correo SMTP", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Probar conexión" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Activar", exact: true })).toBeDisabled();

    await page.getByLabel("Servidor SMTP", { exact: true }).fill("127.0.0.1");
    await page.getByLabel("Puerto", { exact: true }).fill(String(sinkPort));
    await page.getByLabel("Seguridad", { exact: true }).selectOption("none");
    await page.getByLabel("Usuario", { exact: true }).fill("e2e");
    await page.getByLabel("Contraseña o contraseña de aplicación", { exact: true }).fill(MAIL_PASSWORD);
    await page.getByLabel("Correo remitente", { exact: true }).fill("tienda-e2e@example.invalid");
    await page.getByRole("button", { name: "Guardar borrador" }).click();
    await expect(page.getByText("Borrador guardado.")).toBeVisible({ timeout: 20_000 });

    // Guardado: la contraseña ya no está en ningún sitio de este navegador.
    await expect(page.getByText("••••••••••")).toBeVisible();
    await expect(page.getByLabel("Contraseña o contraseña de aplicación", { exact: true })).toHaveCount(0);
    await expectNotKept(page, MAIL_PASSWORD);

    await page.getByRole("button", { name: "Probar conexión" }).click();
    await expect(page.getByText("Correcto", { exact: true })).toBeVisible({ timeout: 30_000 });
    expect(readdirSync(sinkFolder), "probar la conexión no envía ningún mensaje").toHaveLength(0);

    await page.getByRole("button", { name: "Activar", exact: true }).click();
    await page.getByRole("button", { name: "Sí", exact: true }).click();
    await expect(page.getByText("Activa", { exact: true })).toBeVisible({ timeout: 20_000 });

    // Recargada, la pantalla sigue sin saber cuál es la contraseña.
    await page.reload({ waitUntil: "networkidle" });
    const detail = await api(page, "GET", "/api/admin/integrations/smtp/");
    expect(detail.status).toBe(200);
    expect(detail.data.source).toBe("panel");
    expect(JSON.stringify(detail.data)).not.toContain(MAIL_PASSWORD);

    await openEditor(page, "Correo SMTP");
    await page.getByLabel("Escribe REVOCAR para borrar esta configuración").fill("REVOCAR");
    await page.getByRole("button", { name: "Revocar", exact: true }).click();
    await expect(page.getByText("Sin configurar", { exact: true })).toBeVisible({ timeout: 20_000 });
  } finally {
    await revoke(page, "smtp");
  }
});

test("quien no es MASTER no llega a la consola por ningún camino", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page, "dev_admin");

  await page.goto("/admin", { waitUntil: "networkidle" });
  await expect(page.locator(`a[href="${CONSOLE}"]`), "aparece en el menú de un administrador").toHaveCount(0);

  const requests: string[] = [];
  page.on("request", (request) => { if (request.url().includes("/admin/integrations")) requests.push(request.url()); });
  await page.goto(CONSOLE, { waitUntil: "networkidle" });
  await expect(page.getByText("Esta sección no está disponible para tu cuenta.")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(NOTICE)).toHaveCount(0);
  expect(requests.filter((url) => url.includes("/api/")), "la página pidió datos de la consola").toEqual([]);

  // Y si lo intenta a mano, el servidor dice que no, en lectura y en escritura.
  expect((await api(page, "GET", "/api/admin/integrations/")).status).toBe(403);
  expect((await api(page, "GET", "/api/admin/integrations/smtp/")).status).toBe(403);
  const write = await api(page, "PUT", "/api/admin/integrations/smtp/draft/", {
    public: { host: "mal.example.invalid" }, secrets: { password: "x" },
  });
  expect(write.status).toBe(403);
  expect((await api(page, "POST", "/api/admin/integrations/smtp/test/", {})).status).toBe(403);
  expect((await api(page, "POST", "/api/admin/integrations/smtp/revoke/", { confirm: "REVOCAR" })).status).toBe(403);
});

test("Google, Izipay y WhatsApp se configuran con valores inventados y sus secretos no vuelven", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page, "dev_master");
  let companyId = 0;
  try {
    // -- Google: un ID de cliente, y ningún secreto que pedir.
    await openEditor(page, "Inicio de sesión con Google");
    await expect(page.locator('input[type="password"]'), "Google no tiene secreto de cliente").toHaveCount(0);
    await page.getByLabel("ID de cliente de OAuth", { exact: true }).fill("esto-no-es-un-id");
    await page.getByRole("button", { name: "Guardar borrador" }).click();
    await expect(page.getByText(/No es un ID de cliente de Google/)).toBeVisible({ timeout: 20_000 });
    await page.getByLabel("ID de cliente de OAuth", { exact: true }).fill("123456789012-e2enoesrealnoesreal.apps.googleusercontent.com");
    await page.getByRole("button", { name: "Guardar borrador" }).click();
    await expect(page.getByText("Borrador guardado.")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Activar", exact: true }), "guardar no es activar").toBeDisabled();

    // -- Izipay · Mi Cuenta Web: claves de TEST inventadas.
    await openEditor(page, "Izipay · Mi Cuenta Web");
    await page.getByLabel("Usuario (identificador de la tienda)", { exact: true }).fill("90000001");
    await page.getByLabel("Clave pública", { exact: true }).fill("90000001:testpublickey_E2ENoEsRealNoEsReal");
    await page.getByLabel("Contraseña", { exact: true }).fill("prodpassword_E2ENoEsRealNoEsReal");
    await page.getByRole("button", { name: "Guardar borrador" }).click();
    await expect(page.getByText(/entornos distintos/), "mezclar TEST y PRODUCCIÓN no se rechaza").toBeVisible({ timeout: 20_000 });
    await page.getByLabel("Contraseña", { exact: true }).fill(MCW_PASSWORD);
    await page.getByRole("button", { name: "Guardar borrador" }).click();
    await expect(page.getByText("Borrador guardado.")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("TEST", { exact: true })).toBeVisible();
    await expectNotKept(page, MCW_PASSWORD);
    const checkout = await api(page, "GET", "/api/admin/integrations/izipay_checkout/");
    expect(checkout.data.draft, "guardar un producto no toca el otro").toBeNull();

    // -- WhatsApp: de una empresa, con el proveedor simulado de este entorno.
    await page.goto(CONSOLE, { waitUntil: "networkidle" });
    const whatsapp = card(page, "WhatsApp Business");
    await expect(whatsapp.getByRole("button", { name: "Configurar" })).toBeDisabled();
    const option = whatsapp.getByLabel("Empresa").locator("option").nth(1);
    companyId = Number(await option.getAttribute("value"));
    await whatsapp.getByLabel("Empresa").selectOption(String(companyId));
    await whatsapp.getByRole("button", { name: "Configurar" }).click();
    await expect(page.getByRole("heading", { name: /^WhatsApp Business · / })).toBeVisible({ timeout: 20_000 });
    await page.getByLabel("Identificador del número", { exact: true }).fill("2077700000");
    await page.getByLabel("Token de acceso", { exact: true }).fill(WHATSAPP_TOKEN);
    await page.getByLabel("Secreto de la aplicación", { exact: true }).fill("e2e-secreto-NoEsReal");
    await page.getByLabel("Token de verificación del webhook", { exact: true }).fill("e2e-verificacion-NoEsReal");
    await page.getByRole("button", { name: "Guardar borrador" }).click();
    await expect(page.getByText("Borrador guardado.")).toBeVisible({ timeout: 20_000 });
    await expectNotKept(page, WHATSAPP_TOKEN);
    await page.getByRole("button", { name: "Probar conexión" }).click();
    await expect(page.getByText("Correcto", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/no se llamó a Meta/)).toBeVisible();

    const listing = await api(page, "GET", "/api/admin/integrations/");
    const text = JSON.stringify(listing.data);
    for (const secret of [MCW_PASSWORD, WHATSAPP_TOKEN, "e2e-secreto-NoEsReal", "e2e-verificacion-NoEsReal"]) {
      expect(text).not.toContain(secret);
    }
  } finally {
    await revoke(page, "google");
    await revoke(page, "izipay_micuentaweb");
    if (companyId) await revoke(page, "whatsapp_cloud", companyId);
  }
  const after = await api(page, "GET", "/api/admin/integrations/google/");
  expect(after.data.draft, "la prueba dejó un borrador").toBeNull();
});
