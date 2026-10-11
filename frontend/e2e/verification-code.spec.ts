import { execFileSync } from "node:child_process";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

/**
 * MAIL-TEMPLATE-01, fase 3A — verificar el correo con el código de 6 dígitos,
 * en navegador real y contra el servidor de verdad.
 *
 * Necesita el backend con la verificación de correo activa
 * (`REQUIRE_EMAIL_VERIFICATION=1`), que es como corre producción. Sin ella el
 * registro no pide nada y no hay nada que recorrer: la suite se omite y lo dice.
 *
 * EL CÓDIGO NO SE PUEDE LEER DEL SERVIDOR: sólo guarda su HMAC. Lo que la prueba
 * hace es lo que haría la persona: pedir otro —pasado el minuto de espera, que
 * aquí se adelanta en la base— y usar ese. De paso recorre «un código nuevo
 * anula el anterior».
 */

const RUN = Date.now().toString(36);
const CLAVE = `Clave-e2e-${RUN}-A1!`;
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");

/** Un código nuevo para ese correo, como el que llegaría a su buzón. */
function codigoNuevo(correo: string): string {
  const codigo = [
    "import os",
    "from datetime import timedelta",
    "from django.contrib.auth import get_user_model",
    "from django.utils import timezone",
    "from store import verification_codes",
    "from store.models import AccountToken",
    'user = get_user_model().objects.get(email__iexact=os.environ["E2E_VERIFY_EMAIL"])',
    "AccountToken.objects.filter(user=user).update(created_at=timezone.now() - timedelta(minutes=2))",
    "issued = verification_codes.issue(user)",
    'print("CODE=" + issued.code)',
  ].join("\n");
  const salida = execFileSync("python3", ["manage.py", "shell", "-c", codigo], {
    cwd: BACKEND_DIR,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, E2E_VERIFY_EMAIL: correo },
  }).toString();
  const code = salida.match(/CODE=(\d{6})/)?.[1];
  if (!code) throw new Error("no se pudo emitir un código de verificación");
  return code;
}

async function registrarse(page: Page, usuario: string, correo: string) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Crear una ahora" }).click();
  await page.getByLabel("Usuario").fill(usuario);
  await page.getByLabel("Correo electrónico").fill(correo);
  await page.getByLabel("Contraseña", { exact: true }).fill(CLAVE);
  await page.getByLabel("Confirmar contraseña").fill(CLAVE);
  // El servidor de desarrollo de Next redirige primero la barra final (308): se
  // espera la respuesta de verdad.
  const enviar = async () => {
    const respuesta = page.waitForResponse((r) =>
      r.url().includes("/auth/register") && r.request().method() === "POST" && !(r.status() >= 300 && r.status() < 400));
    await page.getByRole("button", { name: "Registrarme" }).click();
    return respuesta;
  };
  let recibida = await enviar();
  if (recibida.status() === 429) {
    // El registro admite 5 por minuto y por dirección, y eso no se apaga para
    // probar: se espera a que pase el minuto y se envía otra vez.
    await page.waitForTimeout(61_000);
    recibida = await enviar();
  }
  const cuerpo = await recibida.json().catch(() => ({}));
  expect(recibida.status(), JSON.stringify(cuerpo)).toBe(201);
  if (!cuerpo.requires_verification) {
    test.skip(true, "el backend no exige verificar el correo: arráncalo con REQUIRE_EMAIL_VERIFICATION=1");
  }
}

test.describe("verificar el correo con el código", () => {
  // El registro admite 5 por minuto y por dirección: dos pruebas, dos registros.
  test.describe.configure({ mode: "serial" });
  test.setTimeout(150_000);

  test("A · registrarse, pegar el código tal como viene en el correo, y entrar", async ({ page }) => {
    const usuario = `codigo_a_${RUN}`;
    const correo = `${usuario}@e2e.test`;
    await registrarse(page, usuario, correo);

    await expect(page.getByRole("heading", { name: "Verifica tu correo" })).toBeVisible();
    await expect(page.getByText(correo)).toBeVisible();
    const campo = page.getByLabel("Código de verificación");
    await expect(campo).toHaveAttribute("autocomplete", "one-time-code");
    await expect(campo).toHaveAttribute("inputmode", "numeric");
    // Recién enviado el correo, pedir otro tiene su espera a la vista.
    await expect(page.getByRole("button", { name: /Enviar otro código en \d+ s/ })).toBeDisabled();

    const code = codigoNuevo(correo);
    // Pegado con un espacio en medio, como lo copia mucha gente. Sin pulsar nada más.
    await campo.fill(`${code.slice(0, 3)} ${code.slice(3)}`);

    await expect(page.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Correo verificado. Ya puedes iniciar sesión.")).toBeVisible();

    // No es un rótulo: la cuenta entra con lo que la persona eligió.
    await page.getByLabel("Usuario").fill(usuario);
    await page.getByLabel("Contraseña", { exact: true }).fill(CLAVE);
    await page.getByRole("button", { name: "Iniciar sesión" }).click();
    await expect.poll(async () =>
      page.evaluate(async () => (await fetch("/api/auth/me/", { credentials: "include" })).status),
    { timeout: 15_000 }).toBe(200);
  });

  test("B · un código equivocado se dice con amabilidad, se borra, y el bueno sigue sirviendo", async ({ page }) => {
    const usuario = `codigo_b_${RUN}`;
    const correo = `${usuario}@e2e.test`;
    await registrarse(page, usuario, correo);
    const campo = page.getByLabel("Código de verificación");
    await expect(campo).toBeVisible();

    const code = codigoNuevo(correo);
    const equivocado = String((Number(code) + 1) % 1_000_000).padStart(6, "0");

    await campo.fill(equivocado);
    const aviso = page.getByRole("alert").filter({ hasText: "código" });
    await expect(aviso).toHaveText("El código no es válido o ya venció. Pide uno nuevo.");
    await expect(campo).toHaveValue("");
    await expect(campo).toBeEnabled();
    // Sigue en la pantalla del código: no entró, ni volvió al registro.
    await expect(page.getByRole("heading", { name: "Verifica tu correo" })).toBeVisible();

    await campo.fill(`${code.slice(0, 3)}-${code.slice(3)}`);
    await expect(page.getByText("Correo verificado. Ya puedes iniciar sesión.")).toBeVisible({ timeout: 15_000 });
  });

  test("C · la página de verificación sin enlace ofrece escribir el código, y no dice si una cuenta existe", async ({ page }) => {
    await page.goto("/auth/verify-email", { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "Verifica tu correo" })).toBeVisible();

    await page.getByLabel("Correo electrónico").fill(`nadie_${RUN}@e2e.test`);
    await page.getByLabel("Código de verificación").fill("123456");
    const aviso = page.getByRole("alert").filter({ hasText: "código" });
    await expect(aviso).toHaveText("El código no es válido o ya venció. Pide uno nuevo.");
  });
});
