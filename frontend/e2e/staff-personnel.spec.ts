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

    // Alguien que NO sea la propia cuenta. La propia ficha no trae botón —
    // nadie puede quitarse el acceso a sí mismo— y eso es justo lo que la
    // distingue aquí, sin tener que adivinar el nombre de la cuenta conectada.
    const otra = personCards(page)
      .filter({ has: page.getByRole("button", { name: "Desactivar acceso" }) })
      .first();
    await expect(
      otra,
      "no hay nadie más que la propia cuenta en esta empresa",
    ).toBeVisible({ timeout: 20_000 });
    const correo = (await otra.innerText()).match(/[\w.+-]+@[\w.-]+/)?.[0] ?? "";
    expect(correo, "la ficha no muestra el correo").toBeTruthy();

    // Y la propia ficha dice por qué no se puede.
    await expect(page.getByText(/Esta es tu cuenta/i)).toBeVisible();

    await otra.getByRole("button", { name: "Desactivar acceso" }).click();
    await page.waitForTimeout(2000);

    await page.locator("#staff-status").selectOption("inactive");
    await page.waitForTimeout(1500);
    const inactiva = personCards(page).filter({ hasText: correo });
    await expect(inactiva, "no aparece entre los inactivos").toHaveCount(1);
    await expect(inactiva).toContainText("Inactivo");

    await inactiva.getByRole("button", { name: "Reactivar acceso" }).click();
    await page.waitForTimeout(2000);
    await page.locator("#staff-status").selectOption("active");
    await page.waitForTimeout(1500);
    await expect(personCards(page).filter({ hasText: correo })).toHaveCount(1);
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
    await expect(
      page.getByRole("link", { name: "Iniciar sesión" }),
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
