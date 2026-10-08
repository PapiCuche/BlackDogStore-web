import { execFileSync } from "node:child_process";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

/**
 * H4.1 — Personal, invitaciones y áreas, en navegador real.
 *
 * SIN MOCKS DEL BACKEND. Las respuestas vienen del servidor de verdad: una
 * prueba que intercepta la API comprueba su propio doble, y el defecto que más
 * caro salió en esta fase —la pantalla girando para siempre— habría pasado
 * inadvertido igual.
 *
 * UNA SOLA SESIÓN PARA TODA LA SUITE. El login está limitado a 5 intentos por
 * minuto y por IP, y eso es una defensa real que no se desactiva para poder
 * probar. Se entra una vez y las cookies se comparten.
 */

let SESSION_COOKIES: Awaited<
  ReturnType<import("@playwright/test").BrowserContext["cookies"]>
> = [];

async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) {
    test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  }
  const use = card.locator("li").filter({ hasText: "dev_admin" })
    .getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) {
    test.skip(true, "dev_admin no existe: siembra con seed_demo_users");
  }
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

test.beforeAll(async ({ browser, baseURL }) => {
  // `signIn` espera 62 s si el límite de entradas (5 por minuto) está agotado.
  // Con el tiempo de hook por defecto, 60 s, esa espera no cabía y la suite
  // entera de Personal caía por una entrada que sólo había que reintentar.
  test.setTimeout(240_000);
  // `baseURL` explícito: un contexto creado a mano no hereda la configuración
  // del proyecto, y sin él la entrada falla saltando la suite entera.
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await signIn(page);
  SESSION_COOKIES = await context.cookies();
  await context.close();
});

test.beforeEach(async ({ context }) => {
  if (SESSION_COOKIES.length) await context.addCookies(SESSION_COOKIES);
});

/** El panel de Personal, ya cargado. */
async function openStaff(page: Page) {
  await page.goto("/admin/staff", { waitUntil: "networkidle" });
  await expect(
    page.getByRole("heading", { name: "Personal", exact: true }),
    "la pantalla de Personal no cargó",
  ).toBeVisible({ timeout: 20_000 });
}

/**
 * Las fichas de PERSONA.
 *
 * Las invitaciones pendientes se pintan más arriba y también son `li` con un
 * correo dentro, así que filtrar por «@» mezclaba las dos listas y el primer
 * resultado podía ser una invitación. Sólo la ficha de una persona lleva la
 * fila «Roles:».
 */
const personCards = (page: Page) =>
  page.locator("ul > li").filter({ hasText: "Roles:" });

/** Elige la primera opción real de un desplegable (la 0 es el marcador). */
async function pickFirst(page: Page, selector: string) {
  const campo = page.locator(selector);
  if ((await campo.count()) === 0) return;
  const opciones = await campo.locator("option").all();
  for (const opcion of opciones.slice(1)) {
    const valor = await opcion.getAttribute("value");
    if (valor) {
      await campo.selectOption(valor);
      return;
    }
  }
}

/**
 * El trabajador desechable de `seed_demo_users --e2e-fixtures` (E2E-02). Es el
 * ÚNICO al que esta suite puede desactivar.
 */
const E2E_WORKER_EMAIL = "dev_e2e_staff@example.invalid";

/** Un correo distinto por ejecución: las invitaciones son únicas por correo. */
const RUN = Date.now().toString(36);
const inviteEmail = (etiqueta: string) => `h41.${etiqueta}.${RUN}@correo.test`;

test.describe("Personal", () => {
  test.describe.configure({ mode: "serial" });

  test("A · carga para un usuario de una sola empresa, sin selector de master", async ({ page }) => {
    test.setTimeout(120_000);
    await openStaff(page);

    // EL DEFECTO QUE ESTO IMPIDE.
    //
    // La pantalla leía `selectedCompanyId`, que es el SELECTOR del master y
    // vale `null` para quien pertenece a una sola empresa — el caso normal. Se
    // quedaba girando para siempre. El smoke anterior pasaba: la ruta
    // renderizaba, no desbordaba y no decía «Membresía #».
    //
    // Por eso aquí se comprueba CONTENIDO, no que la página exista.
    await expect(
      page.getByText(/Cargando personal/i),
      "la pantalla se quedó cargando: volvió el defecto del selector",
    ).toHaveCount(0, { timeout: 20_000 });

    await expect(
      personCards(page).first(),
      "no se listó ninguna persona",
    ).toBeVisible({ timeout: 20_000 });

    const texto = await page.locator("main, body").first().innerText();
    // Nombre, correo, área, roles y sucursales, todos por su nombre.
    expect(texto).toContain("Área:");
    expect(texto).toContain("Roles:");
    expect(texto).toContain("Sucursales:");
    expect(/Membres[íi]a\s*#/.test(texto), "asomó un identificador técnico").toBe(false);
  });

  test("B · la búsqueda encuentra por nombre y por correo", async ({ page }) => {
    test.setTimeout(120_000);
    await openStaff(page);

    const primera = personCards(page).first();
    const correo = (await primera.innerText()).match(/[\w.+-]+@[\w.-]+/)?.[0];
    expect(correo, "no se pudo leer un correo de la lista").toBeTruthy();

    await page.locator("#staff-search").fill(correo!);
    await page.waitForTimeout(1500);
    const resultados = personCards(page);
    await expect(resultados).toHaveCount(1);
    await expect(resultados.first()).toContainText(correo!);
  });

  test("C · los filtros de estado y área responden", async ({ page }) => {
    test.setTimeout(120_000);
    await openStaff(page);

    const activos = await personCards(page).count();

    await page.locator("#staff-status").selectOption("inactive");
    await page.waitForTimeout(1500);
    const inactivos = await personCards(page).count();
    expect(inactivos).toBeLessThanOrEqual(activos);

    await page.locator("#staff-status").selectOption("");
    await page.waitForTimeout(1500);
    const todos = await personCards(page).count();
    expect(todos).toBeGreaterThanOrEqual(activos);

    // El área se elige por NOMBRE, no por identificador.
    const opciones = await page.locator("#staff-area option").allInnerTexts();
    expect(opciones.some((o) => /[A-Za-zÁÉÍÓÚáéíóúñ]{3,}/.test(o))).toBe(true);
  });

  test("D · añadir trabajador sin escribir un solo identificador", async ({ page }) => {
    test.setTimeout(120_000);
    await openStaff(page);
    await page.getByRole("button", { name: "Añadir trabajador" }).click();

    await page.locator("#w-first").fill("Ana");
    await page.locator("#w-last").fill("Torres Pérez");
    await page.locator("#w-email").fill(inviteEmail("alta"));

    // Rol y área se eligen por su NOMBRE en la lista; el test usa el id del
    // campo sólo para localizarlo, porque «Rol» y «Área» etiquetan también a
    // los filtros de arriba.
    await pickFirst(page, "#w-role");
    await pickFirst(page, "#w-area");

    await page.getByRole("button", { name: "Enviar invitación" }).click();
    await expect(
      page.getByRole("heading", { name: /Invitaciones pendientes/i }),
      "la invitación no apareció en pendientes",
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(inviteEmail("alta"))).toBeVisible();
  });

  test("E · invitar dos veces al mismo correo deja UNA invitación", async ({ page }) => {
    test.setTimeout(120_000);
    const correo = inviteEmail("doble");

    for (let vez = 0; vez < 2; vez++) {
      await openStaff(page);
      await page.getByRole("button", { name: "Añadir trabajador" }).click();
      await page.locator("#w-first").fill("Doble");
      await page.locator("#w-email").fill(correo);
      await pickFirst(page, "#w-role");
      await page.getByRole("button", { name: "Enviar invitación" }).click();
      await page.waitForTimeout(2000);
    }

    await openStaff(page);
    const filas = page.locator("li").filter({ hasText: correo });
    await expect(
      filas,
      "un doble envío dejó dos invitaciones vivas para la misma persona",
    ).toHaveCount(1);
  });

  test("F · reenviar y G · revocar una invitación", async ({ page }) => {
    test.setTimeout(120_000);
    const correo = inviteEmail("acciones");

    await openStaff(page);
    await page.getByRole("button", { name: "Añadir trabajador" }).click();
    await page.locator("#w-first").fill("Acciones");
    await page.locator("#w-email").fill(correo);
    await pickFirst(page, "#w-role");
    await page.getByRole("button", { name: "Enviar invitación" }).click();

    const fila = page.locator("li").filter({ hasText: correo });
    await expect(fila).toHaveCount(1, { timeout: 20_000 });

    // EL TOKEN NUNCA LLEGA AL PANEL DE ADMINISTRACIÓN. Mostrarlo daría a quien
    // invita un enlace de acceso a una cuenta ajena.
    const textoFila = await fila.innerText();
    expect(textoFila.length).toBeLessThan(400);
    expect(/token/i.test(textoFila)).toBe(false);

    await fila.getByRole("button", { name: "Reenviar" }).click();
    await page.waitForTimeout(2000);
    await expect(fila, "la invitación desapareció al reenviar").toHaveCount(1);

    await fila.getByRole("button", { name: "Revocar" }).click();
    await page.waitForTimeout(2000);
    await expect(
      page.locator("li").filter({ hasText: correo }),
      "la invitación revocada sigue ofreciéndose",
    ).toHaveCount(0);
  });

  test("I · desactivar y reactivar a alguien", async ({ page }) => {
    test.setTimeout(120_000);
    await openStaff(page);

    // UN TRABAJADOR PROPIO DE ESTA PRUEBA (E2E-02).
    //
    // Desactivar a alguien retira sus roles, y reactivarlo no se los devuelve:
    // así debe ser. Esta prueba elegía «la primera ficha con botón», que era
    // una CUENTA DEMO con la que entran otras suites, y la dejaba sin
    // capacidades para todas las corridas siguientes. Ahora sólo toca a
    // `dev_e2e_staff`, que nadie usa para entrar y ninguna otra prueba lee.
    // Lo crea `seed_demo_users --e2e-fixtures`; volver a sembrar lo restaura.
    await page.locator("#staff-status").selectOption("");
    await page.waitForTimeout(1500);
    const ficha = personCards(page).filter({ hasText: E2E_WORKER_EMAIL });
    if ((await ficha.count()) === 0) {
      test.skip(
        true,
        "falta el trabajador de pruebas: siembra con seed_demo_users --e2e-fixtures",
      );
    }
    await expect(ficha, "hay más de una ficha con ese correo").toHaveCount(1);

    // Una corrida que se cortó a medias pudo dejarlo inactivo. Se parte de
    // activo sin depender de cómo terminó la anterior.
    const reactivar = ficha.getByRole("button", { name: "Reactivar acceso" });
    if ((await reactivar.count()) > 0) {
      await reactivar.click();
      await expect(
        ficha.getByRole("button", { name: "Desactivar acceso" }),
      ).toBeVisible({ timeout: 20_000 });
    }

    // La propia ficha dice por qué no se puede desactivar a uno mismo.
    await expect(page.getByText(/Esta es tu cuenta/i)).toBeVisible();

    await ficha.getByRole("button", { name: "Desactivar acceso" }).click();
    await page.waitForTimeout(2000);

    await page.locator("#staff-status").selectOption("inactive");
    await page.waitForTimeout(1500);
    const inactiva = personCards(page).filter({ hasText: E2E_WORKER_EMAIL });
    await expect(inactiva, "no aparece entre los inactivos").toHaveCount(1);
    await expect(inactiva).toContainText("Inactivo");

    await inactiva.getByRole("button", { name: "Reactivar acceso" }).click();
    await page.waitForTimeout(2000);
    await page.locator("#staff-status").selectOption("active");
    await page.waitForTimeout(1500);
    await expect(
      personCards(page).filter({ hasText: E2E_WORKER_EMAIL }),
    ).toHaveCount(1);
  });
});

test.describe("Aceptación", () => {
  test.describe.configure({ mode: "serial" });

  /**
   * Crea una invitación por API y devuelve su token en claro.
   *
   * El token SÓLO viaja así en desarrollo (`DEBUG`), y por eso este escenario
   * vive en el navegador y no en un arnés aparte: es la única forma de recorrer
   * la página de aceptación sin un servidor de correo.
   */
  async function invitarPorApi(page: Page, correo: string) {
    return page.evaluate(async (email) => {
      const leer = async (ruta: string) => {
        const res = await fetch(ruta, { credentials: "include" });
        return res.ok ? (await res.json()).results ?? [] : [];
      };
      const areas = await leer("/api/admin/areas/");
      if (!areas.length) return { error: "sin áreas" };
      const company = areas[0].company;
      const roles = (await leer("/api/admin/roles/"))
        .filter((r: { company: number }) => r.company === company);
      if (!roles.length) return { error: "sin roles" };

      // La cabecera CSRF va porque la sesión web es de cookie y el servidor
      // exige la prueba en cada mutación. Que este arnés tenga que mandarla es
      // la señal de que la defensa está puesta.
      const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1];
      const res = await fetch("/api/admin/staff/invitations/", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRFToken": decodeURIComponent(csrf) } : {}),
        },
        body: JSON.stringify({
          company, email, first_name: "Invitada", last_name: "De Prueba",
          role: roles[0].id, area: areas[0].id, branch_access_mode: "all",
        }),
      });
      const cuerpo = await res.json().catch(() => ({}));
      return { status: res.status, token: cuerpo.debug_invitation_token, cuerpo };
    }, correo);
  }

  test("H · la página de aceptación muestra la invitación, y el token NO basta para entrar", async ({ page }) => {
    test.setTimeout(120_000);
    await openStaff(page);
    const correo = inviteEmail("acepta");
    const creada = await invitarPorApi(page, correo);
    expect(creada.status, JSON.stringify(creada)).toBe(201);
    const token = creada.token as string | undefined;
    if (!token) {
      test.skip(true, "el backend no corre con DEBUG: no hay enlace en claro");
    }

    await page.goto(`/invitacion?token=${encodeURIComponent(token!)}`, {
      waitUntil: "networkidle",
    });
    await expect(page.getByText(/Comprobando la invitación/i))
      .toHaveCount(0, { timeout: 20_000 });

    // La invitación se lee sin haber iniciado sesión con ese correo: es
    // información de la propia invitación, no acceso.
    await expect(
      page.getByRole("heading", { name: /Te han invitado a/i }),
      "la página no reconoció una invitación válida",
    ).toBeVisible({ timeout: 20_000 });

    // NO SE FILTRA DÓNDE TRABAJA NADIE. La página habla de la empresa que
    // invita y de nada más.
    const texto = await page.locator("main").innerText();
    expect(/ya (pertenece|trabaja) en/i.test(texto)).toBe(false);
    expect(texto).not.toContain(token!);

    // EL PUNTO DEL ESCENARIO: la sesión abierta es de OTRA persona. Tener el
    // enlace no puede vincular esta cuenta. Si esto pasara, quien reenvíe un
    // correo se quedaría con la plaza de otro.
    //
    // Ni siquiera se ofrece el botón: la página dice con qué cuenta estás
    // dentro y para quién es la invitación.
    await expect(
      page.getByRole("button", { name: "Aceptar invitación" }),
      "se ofrece aceptar con una sesión de otro correo",
    ).toHaveCount(0);
    await expect(page.getByText(/Ahora mismo estás dentro como/i)).toBeVisible();
    await expect(page.getByText(correo).first()).toBeVisible();
    // Se ofrece salir de esta sesión aquí mismo. Antes era un enlace a «Iniciar
    // sesión» que, con una sesión abierta, sólo mostraba el perfil de la otra
    // cuenta.
    await expect(
      page.getByRole("button", { name: "Cerrar sesión" }),
    ).toBeVisible();

    // Y la ruta sigue cerrada aunque se llame a mano: la pantalla es cortesía,
    // no la defensa.
    const directo = await page.evaluate(async (t) => {
      const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1];
      const res = await fetch("/api/staff/invitations/accept/", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRFToken": decodeURIComponent(csrf) } : {}),
        },
        body: JSON.stringify({ token: t }),
      });
      return res.status;
    }, token!);
    expect(
      directo,
      "el servidor aceptó vincular una cuenta con otro correo",
    ).not.toBe(200);
    // 401 es la respuesta correcta aquí: lo que falta no es autoridad sino
    // demostrar quién eres. Tener el enlace no es demostrarlo.
    expect([401, 403, 404]).toContain(directo);
  });

  /**
   * STAFF-ONBOARDING-01 — el recorrido completo, como lo hace una persona.
   *
   * En producción alguien abrió su invitación, se le ofreció «Crear cuenta» y
   * acabó ante «ese correo ya está registrado» sin poder entrar con nada. Ninguna
   * prueba recorría el camino entero; estas dos sí, en un navegador sin sesión,
   * que es como llega quien recibe el correo.
   */
  const CLAVE = `Clave-e2e-${RUN}-A1!`;
  const OTRA_CLAVE = `Otra-e2e-${RUN}-B2!`;

  /** Un enlace de recuperación para ese correo, como el que llegaría a su buzón. */
  function enlaceDeRecuperacion(correo: string): string {
    const codigo = [
      "import os",
      "from django.contrib.auth import get_user_model",
      "from store.models import AccountToken",
      'user = get_user_model().objects.get(email__iexact=os.environ["E2E_RESET_EMAIL"])',
      "raw, _ = AccountToken.make(user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)",
      'print("TOKEN=" + raw)',
    ].join("\n");
    const salida = execFileSync("python3", ["manage.py", "shell", "-c", codigo], {
      cwd: process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend"),
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, E2E_RESET_EMAIL: correo },
    }).toString();
    const token = salida.match(/TOKEN=(\S+)/)?.[1];
    if (!token) throw new Error("no se pudo emitir el enlace de recuperación");
    return token;
  }

  async function invitar(page: Page, correo: string): Promise<string> {
    await openStaff(page);
    const creada = await invitarPorApi(page, correo);
    expect(creada.status, JSON.stringify(creada)).toBe(201);
    const token = creada.token as string | undefined;
    if (!token) test.skip(true, "el backend no corre con DEBUG: no hay enlace en claro");
    return token!;
  }

  async function aceptar(persona: Page) {
    await persona.getByRole("button", { name: "Aceptar invitación" }).click();
    await expect(
      persona.getByRole("heading", { name: "Tu acceso ha sido configurado correctamente" }),
    ).toBeVisible({ timeout: 20_000 });
    // No es un rótulo: el servidor abre el panel a esta sesión.
    const panel = await persona.evaluate(async () =>
      (await fetch("/api/me/internal-dashboard/", { credentials: "include" })).status);
    expect(panel, "aceptó, pero el servidor no le abre el panel").toBe(200);
  }

  test("M · persona nueva: crea su cuenta con el correo invitado y su propia contraseña, y acepta", async ({ page, browser, baseURL }) => {
    test.setTimeout(180_000);
    const correo = inviteEmail("nueva");
    const token = await invitar(page, correo);
    const invitacion = `/invitacion?token=${encodeURIComponent(token)}`;

    const contexto = await browser.newContext({ baseURL });
    const persona = await contexto.newPage();
    try {
      await persona.goto(invitacion, { waitUntil: "networkidle" });
      await expect(persona.getByText("Crea tu cuenta para continuar")).toBeVisible({ timeout: 20_000 });
      await expect(persona.getByRole("link", { name: "Iniciar sesión" })).toHaveCount(0);

      await persona.getByRole("link", { name: "Crear mi cuenta" }).click();
      await persona.waitForURL(/\/auth\?mode=register/, { timeout: 20_000 });
      await expect(persona.getByRole("heading", { name: "Crear cuenta" })).toBeVisible({ timeout: 20_000 });
      // El correo es el de la invitación y no se puede cambiar por otro.
      const campo = persona.locator("#auth-page-correo-electronico");
      await expect(campo).toHaveValue(correo, { timeout: 20_000 });
      await expect(campo).toHaveAttribute("readonly", "");

      await persona.locator("#auth-page-usuario").fill(`e2e_nueva_${RUN}`);
      await persona.locator("#auth-page-contrasena").fill(CLAVE);
      await persona.locator("#auth-page-confirmar-contrasena").fill(CLAVE);
      await persona.getByRole("button", { name: "Registrarme" }).click();

      // Sin segundo correo y sin volver a buscar el primero: queda dentro, de
      // vuelta en su invitación.
      await persona.waitForURL(/\/invitacion\?token=/, { timeout: 30_000 });
      await aceptar(persona);
      await expect(persona.locator("main")).not.toContainText(token);
    } finally {
      await contexto.close();
    }
  });

  test("N · con cuenta y sin contraseña conocida: nunca se le ofrece crear otra; recupera, vuelve y acepta", async ({ page, browser, baseURL }) => {
    test.setTimeout(300_000);
    const correo = inviteEmail("recupera");
    const usuario = `e2e_recupera_${RUN}`;
    // Leer una invitación tiene un cupo por dirección (20 por minuto: es lo que
    // frena a quien prueba enlaces), y en desarrollo React pide cada pantalla
    // dos veces. Este recorrido abre cinco; se espera la ventana, como hace
    // `signIn` con el cupo de entradas.
    await page.waitForTimeout(61_000);

    const contexto = await browser.newContext({ baseURL });
    const persona = await contexto.newPage();
    try {
      // La cuenta ya existe: se registró por su cuenta, como cualquier cliente.
      // Donde la verificación de correo es obligatoria queda SIN VERIFICAR, que
      // es exactamente el estado que bloqueaba a la persona en producción.
      await persona.goto("/auth", { waitUntil: "networkidle" });
      const registro = await persona.evaluate(async (datos) => {
        const res = await fetch("/api/auth/register/", {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(datos),
        });
        return res.status;
      }, { username: usuario, email: correo, password: CLAVE, password_confirm: CLAVE });
      expect(registro).toBe(201);

      const token = await invitar(page, correo);
      const invitacion = `/invitacion?token=${encodeURIComponent(token)}`;

      await persona.goto(invitacion, { waitUntil: "networkidle" });
      await expect(persona.getByText("Ya tienes una cuenta con este correo")).toBeVisible({ timeout: 20_000 });
      await expect(persona.getByText(/Crear mi cuenta|Crea tu cuenta/)).toHaveCount(0);

      // EL CAMINO QUE TERMINABA EN «ESE CORREO YA ESTÁ REGISTRADO» ESTÁ CERRADO
      // EN SU ORIGEN: pedir el registro para esa invitación abre el inicio de
      // sesión.
      await persona.goto(`/auth?mode=register&next=${encodeURIComponent(invitacion)}`, { waitUntil: "networkidle" });
      await expect(persona.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible({ timeout: 20_000 });
      await expect(persona.getByText(/Ya tienes una cuenta con este correo/)).toBeVisible();
      await expect(persona.locator("#auth-page-correo-electronico")).toHaveCount(0);

      // Pide el enlace desde la propia invitación.
      await persona.goto(invitacion, { waitUntil: "networkidle" });
      await persona.getByRole("button", { name: /No conozco mi contraseña|Establecer mi contraseña/ }).click();
      await expect(persona.getByText("Te enviamos un enlace")).toBeVisible({ timeout: 20_000 });

      // Abre el enlace de su buzón, que la trae de vuelta a la invitación.
      const recuperacion = enlaceDeRecuperacion(correo);
      await persona.goto(
        `/auth/reset-password?token=${encodeURIComponent(recuperacion)}&next=${encodeURIComponent(invitacion)}`,
        { waitUntil: "networkidle" },
      );
      const claves = persona.locator('input[type="password"]');
      await claves.nth(0).fill(OTRA_CLAVE);
      await claves.nth(1).fill(OTRA_CLAVE);
      await persona.locator('form button[type="submit"]').click();
      await expect(persona.getByText("Contraseña restablecida")).toBeVisible({ timeout: 20_000 });
      // Se le dice con qué usuario se entra: puede no saberlo.
      await expect(persona.getByText(usuario)).toBeVisible();
      // Restablecer cierra todas las sesiones de la cuenta, al segundo.
      await persona.waitForTimeout(1500);

      await persona.getByRole("link", { name: "Iniciar sesión" }).click();
      await persona.waitForURL(/\/auth\?next=/, { timeout: 20_000 });
      await expect(persona.locator("#auth-page-usuario")).toHaveValue(usuario, { timeout: 20_000 });
      await persona.locator("#auth-page-contrasena").fill(OTRA_CLAVE);
      await persona.getByRole("button", { name: /iniciar sesión/i }).first().click();

      await persona.waitForURL(/\/invitacion\?token=/, { timeout: 30_000 });
      await aceptar(persona);
    } finally {
      await contexto.close();
    }
  });

  test("H2 · un token inventado no dice nada de más", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/invitacion?token=noexisteestetoken", {
      waitUntil: "networkidle",
    });
    await expect(
      page.getByRole("heading", { name: "Invitación no válida" }),
    ).toBeVisible({ timeout: 20_000 });

    // UN SOLO MENSAJE para inexistente, caducada y revocada. Distinguirlas
    // convertiría la página en un comprobador de tokens.
    const texto = await page.locator("main").innerText();
    for (const palabra of [/revocad/i, /caducad/i, /expirad/i, /no existe/i]) {
      expect(palabra.test(texto), `el mensaje distingue el motivo: ${palabra}`)
        .toBe(false);
    }
  });
});

test.describe("Áreas", () => {
  test.describe.configure({ mode: "serial" });

  test("J · listar, crear y desactivar con aviso de impacto", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/admin/areas", { waitUntil: "networkidle" });
    await expect(
      page.getByRole("heading", { name: "Áreas", exact: true }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/Cargando áreas/i)).toHaveCount(0, { timeout: 20_000 });

    // Las áreas del aprovisionamiento están ahí.
    await expect(page.getByText("Servicio Técnico").first()).toBeVisible();

    const nombre = `Área ${RUN}`;
    await page.locator("#area-name").fill(nombre);
    await page.getByRole("button", { name: "Crear área" }).click();
    const nueva = page.locator("ul > li").filter({ hasText: nombre });
    await expect(nueva, "el área nueva no apareció").toHaveCount(1, { timeout: 20_000 });

    // Un área SIN personal se desactiva sin aviso: no hay impacto que explicar.
    await nueva.getByRole("button", { name: "Desactivar" }).click();
    await page.waitForTimeout(2000);
    await expect(nueva).toContainText("Inactiva");

    // Una CON personal sí avisa, y el aviso dice lo que NO pasa.
    const conGente = page.locator("ul > li")
      .filter({ hasText: "Servicio Técnico" }).first();
    const cuenta = (await conGente.innerText()).match(/(\d+)\s+persona/)?.[1];
    if (cuenta && Number(cuenta) > 0) {
      await conGente.getByRole("button", { name: "Desactivar" }).click();
      const aviso = page.getByText(/asignaci[óo]n(es)? activa/i);
      await expect(aviso, "desactivar un área con gente no avisó").toBeVisible();
      await expect(page.getByText(/no quita roles ni permisos/i)).toBeVisible();
      await page.getByRole("button", { name: "Cancelar" }).click();
      await expect(conGente).toContainText("Activa");
    }
  });
});

test.describe("Autorización", () => {
  test("K · el backend rechaza a quien no tiene la capacidad", async ({ page }) => {
    test.setTimeout(120_000);
    // NO se comprueba «el botón está oculto»: ocultar un botón nunca impidió
    // que alguien llame a la ruta. Se llama a la ruta.
    await page.goto("/admin/staff", { waitUntil: "networkidle" });

    const sinPermiso = await page.evaluate(async () => {
      const res = await fetch("/api/admin/staff/?company=999999", {
        credentials: "include",
      });
      return res.status;
    });
    // Una empresa que quien llama no alcanza responde como inexistente: un 403
    // confirmaría que ese identificador existe.
    expect([403, 404]).toContain(sinPermiso);
  });

  test("L · el personal de otra empresa no se alcanza", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/admin/staff", { waitUntil: "networkidle" });

    const ajena = await page.evaluate(async () => {
      // Se prueban varios identificadores: si alguno respondiera 200 con datos,
      // la frontera estaría rota.
      const codigos: number[] = [];
      for (const id of [9001, 9002, 9003]) {
        const res = await fetch(`/api/admin/staff/?company=${id}`, {
          credentials: "include",
        });
        codigos.push(res.status);
      }
      return codigos;
    });
    for (const codigo of ajena) expect([403, 404]).toContain(codigo);
  });
});
