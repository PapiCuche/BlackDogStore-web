import { test, expect, type Page } from "@playwright/test";

/**
 * Los seis accesos de desarrollo, probados de extremo a extremo.
 *
 * EL DEFECTO QUE ESTA PRUEBA EXISTE PARA IMPEDIR.
 * La auditoría anterior capturó `/auth` 108 veces y nunca pulsó «Usar cuenta».
 * Verificó el MECANISMO de login —con un usuario que ella misma creaba— y dio
 * por buena la PROMESA de la pantalla sin comprobarla. Las seis cuentas que
 * anunciaba no existían: el backend respondía «No active account found with
 * the given credentials» y nadie se enteró.
 *
 * Esta prueba pasa por el mismo camino que una persona: pulsa el botón de la
 * tarjeta, envía el formulario, y comprueba adónde llega y qué autoridad tiene.
 *
 * SOBRE EL LIMITADOR. El login está limitado a 5 intentos por minuto y por IP,
 * y eso está bien: es una defensa real. Seis inicios de sesión seguidos lo
 * activan, así que la prueba espera cuando toca en lugar de desactivarlo — un
 * test que apaga una defensa para pasar deja de probar el sistema.
 */

const PASSWORD = "Demo123!";

type Expectation = {
  username: string;
  /** Debe poder abrir el control interno. */
  internal: boolean;
  /** Rutas del panel que su autoridad SÍ debe permitir. */
  allowed: string[];
  /** Superusuario de plataforma. */
  master?: boolean;
};

const ACCOUNTS: Expectation[] = [
  { username: "dev_customer", internal: false, allowed: [] },
  { username: "dev_sales", internal: true, allowed: ["/admin/orders"] },
  { username: "dev_inventory", internal: true, allowed: ["/admin/inventory"] },
  { username: "dev_technician", internal: true, allowed: ["/admin/service"] },
  { username: "dev_admin", internal: true, allowed: ["/admin/products", "/admin/settings"] },
  { username: "dev_master", internal: true, allowed: ["/admin"], master: true },
];

/** Entra pulsando el botón de la tarjeta, como haría una persona. */
async function signInThroughCard(page: Page, username: string) {
  await page.goto("/auth", { waitUntil: "networkidle" });

  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  await expect(
    card,
    "la tarjeta de accesos de desarrollo no se muestra",
  ).toBeVisible();

  const row = card.locator("li").filter({ hasText: username });
  await expect(
    row,
    `la tarjeta no ofrece ${username}`,
  ).toHaveCount(1);
  // Si la cuenta no sirve, la tarjeta no pinta botón — y eso es lo correcto,
  // pero significa que el entorno no está sembrado.
  await expect(
    row.getByRole("button", { name: "Usar cuenta" }),
    `${username} aparece sin botón: la cuenta no existe o está inactiva`,
  ).toHaveCount(1);

  await row.getByRole("button", { name: "Usar cuenta" }).click();

  // El botón sólo RELLENA. El inicio de sesión sigue siendo el real.
  await expect(page.getByLabel(/usuario/i).first()).toHaveValue(username);
  await expect(page.getByLabel(/contraseña/i).first()).toHaveValue(PASSWORD);

  // EL LIMITADOR SON 5 INTENTOS POR MINUTO Y POR IP, y eso está bien: es una
  // defensa real contra fuerza bruta. Seis inicios de sesión seguidos lo
  // activan, así que la prueba ESPERA la ventana en vez de desactivarlo — un
  // test que apaga una defensa para pasar deja de probar lo que se envía.
  //
  // No se busca un mensaje concreto: el texto puede cambiar. Se observa el
  // hecho —seguimos en /auth— y se reintenta pasada la ventana.
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 12_000 });
      return;
    } catch {
      if (attempt === 2) throw new Error(`${username} no pudo iniciar sesión`);
      await page.waitForTimeout(62_000);
    }
  }
}

test.describe("los seis accesos de desarrollo funcionan", () => {
  // Secuencial y con margen: comparten el limitador por IP.
  test.describe.configure({ mode: "serial" });

  for (const account of ACCOUNTS) {
    test(`${account.username} entra y llega a donde debe`, async ({ page }) => {
      test.setTimeout(180_000);
      await signInThroughCard(page, account.username);

      // LA SESIÓN SE COMPRUEBA COMO LA VE UNA PERSONA, no replicando el
      // protocolo. Un `fetch` crudo a `/auth/me/` devuelve 401: la aplicación
      // usa `fetchWithAuth`, que ante un 401 refresca el token y reintenta.
      // Copiar ese protocolo a medias probaría mi copia, no la aplicación.
      //
      // Que la cabecera ofrezca «Salir» en vez de «Ingresar» es la señal de
      // que el backend reconoció la sesión: sale de `getCurrentUser()`.
      await expect(
        page.getByRole("button", { name: /^salir$/i }).first(),
        `${account.username} no quedó con sesión iniciada`,
      ).toBeVisible({ timeout: 15_000 });

      await page.goto("/admin", { waitUntil: "networkidle" });
      const text = await page.locator("body").innerText();

      if (account.internal) {
        // El panel imprime QUIÉN es y con qué autoridad. Es la aplicación
        // diciendo lo que el backend le contestó.
        expect(
          text.includes(account.username),
          `el panel no reconoce a ${account.username}`,
        ).toBe(true);
        if (account.master) {
          expect(
            /MASTER|Superadministrador/i.test(text),
            "dev_master no aparece como autoridad de plataforma",
          ).toBe(true);
        }
      }

      if (!account.internal) {
        // Un cliente NO entra al control interno. Lo que vea da igual — una
        // puerta, un aviso, la tienda — mientras no sea el panel.
        const insidePanel =
          (await page.locator("nav[aria-label='Módulos del control interno']").count()) > 0;
        expect(
          insidePanel,
          "dev_customer alcanzó la navegación del control interno",
        ).toBe(false);
      } else {
        expect(
          /No tienes acceso|sin acceso/i.test(text),
          `${account.username} fue rechazado del control interno`,
        ).toBe(false);
      }

      for (const route of account.allowed) {
        await page.goto(route, { waitUntil: "networkidle" });
        const body2 = await page.locator("body").innerText();
        expect(
          /No tienes permisos/i.test(body2),
          `${account.username} no puede abrir ${route}, y debería`,
        ).toBe(false);
      }
    });
  }
});

test.describe("la tarjeta no promete lo que no existe", () => {
  test("cada cuenta ofrecida tiene botón, y las que no, no", async ({ page }) => {
    // El defecto original: seis botones para seis cuentas inexistentes. La
    // tarjeta ahora pregunta al backend qué hay de verdad.
    await page.goto("/auth", { waitUntil: "networkidle" });
    const res = await page.request.get("http://127.0.0.1:8000/api/dev/demo-accounts/");
    expect(res.ok()).toBe(true);
    const data = await res.json();

    const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
    for (const account of data.accounts as Array<{ username: string; usable: boolean }>) {
      const row = card.locator("li").filter({ hasText: account.username });
      await expect(row).toHaveCount(1);
      await expect(
        row.getByRole("button", { name: "Usar cuenta" }),
        `${account.username}: usable=${account.usable} pero el botón no coincide`,
      ).toHaveCount(account.usable ? 1 : 0);
    }
  });
});
