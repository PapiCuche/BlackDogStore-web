// Ensayo de producción: la tienda y el panel en un navegador real, a través de Caddy.
// Lo llama deploy/rehearsal.sh, que lo copia junto a frontend/node_modules para
// que encuentre Playwright. Variables: R_DOMAIN, R_PORT, SMOKE_USER,
// SMOKE_PASSWORD, R_STATE (opcional: estado de rehearsal_media.py).
import fs from "node:fs";
import { chromium } from "@playwright/test";

const DOMAIN = process.env.R_DOMAIN ?? "tienda.test";
const PORT = process.env.R_PORT ?? "18443";
const [user, password] = [process.env.SMOKE_USER, process.env.SMOKE_PASSWORD];
const media = process.env.R_STATE && fs.existsSync(process.env.R_STATE)
  ? JSON.parse(fs.readFileSync(process.env.R_STATE, "utf8")) : null;
// Estado de rehearsal_flows.py: el enlace de seguimiento de una orden de ensayo.
const flows = process.env.R_STATE && fs.existsSync(process.env.R_STATE + ".flows")
  ? JSON.parse(fs.readFileSync(process.env.R_STATE + ".flows", "utf8")) : null;

const out = [];
const step = async (name, fn) => {
  try { const r = await fn(); out.push(`OK   ${name}${r ? " — " + r : ""}`); }
  catch (e) { out.push(`FAIL ${name} — ${String(e.message).split("\n")[0].slice(0, 160)}`); }
};
const browser = await chromium.launch({ args: [`--host-resolver-rules=MAP ${DOMAIN}:443 127.0.0.1:${PORT}`] });
const ctx = await browser.newContext({ baseURL: `https://${DOMAIN}`, ignoreHTTPSErrors: true, viewport: { width: 390, height: 844 } });
const page = await ctx.newPage();
const failed = [];
page.on("response", (r) => { if (r.status() >= 500) failed.push(`${r.status()} ${new URL(r.url()).pathname}`); });
// Errores de JavaScript de la página. Un 401 de «¿hay sesión?» en un visitante
// anónimo es una respuesta, no un error de la aplicación: no se cuenta.
const scriptErrors = [];
page.on("pageerror", (e) => scriptErrors.push(String(e.message).slice(0, 120)));
page.on("console", (m) => {
  if (m.type() === "error" && !/Failed to load resource/.test(m.text())) scriptErrors.push(m.text().slice(0, 120));
});
const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
// Imágenes que el navegador terminó de pedir y no pudo pintar.
const broken = () => page.evaluate(() => [...document.images]
  .filter((i) => i.complete && i.naturalWidth === 0 && i.getAttribute("src"))
  .map((i) => i.getAttribute("src").slice(0, 60)));

const productHref = await (async () => {
  await page.goto("/product", { waitUntil: "networkidle" });
  return (await page.$$eval("a[href^='/product/']", (as) => as.map((a) => a.getAttribute("href"))))[0];
})();
for (const width of [390, 1440]) {
  await page.setViewportSize({ width, height: 900 });
  for (const [name, path] of [["portada", "/"], ["catálogo", "/product"], ["ficha", productHref], ["carrito", "/cart"],
    ["checkout", "/checkout"], ["servicios", "/services"], ["nosotros", "/about"], ["contacto", "/contact"], ["acceso", "/auth"]]) {
    await step(`${name} · ${width}px`, async () => {
      scriptErrors.length = 0;
      const r = await page.goto(path, { waitUntil: "networkidle" });
      if (r.status() !== 200) throw new Error("status " + r.status());
      const h = (await page.locator("h1").first().innerText()).replace(/\s+/g, " ").slice(0, 40);
      const o = await overflow();
      if (o > 1) throw new Error("overflow " + o);
      const b = await broken();
      if (b.length) throw new Error("imágenes rotas: " + b.join(", "));
      if (scriptErrors.length) throw new Error("errores de script: " + scriptErrors.join(" | "));
      return `h1 «${h}»`;
    });
  }
}
await page.setViewportSize({ width: 390, height: 844 });

await step("sin «Continuar con Google» mientras no haya ID de cliente", async () => {
  await page.goto("/auth", { waitUntil: "networkidle" });
  if (await page.getByText(/Google/i).count()) throw new Error("la página de acceso ofrece Google sin estar configurado");
  if (!(await page.getByLabel("Usuario").count())) throw new Error("no está el acceso con usuario");
  return "acceso con usuario y contraseña";
});

if (flows?.tracking_path) {
  await step("seguimiento de una reparación, sin sesión", async () => {
    scriptErrors.length = 0;
    const r = await page.goto(flows.tracking_path, { waitUntil: "networkidle" });
    if (r.status() !== 200) throw new Error("status " + r.status());
    if (r.headers()["referrer-policy"] !== "no-referrer") throw new Error("referrer-policy " + r.headers()["referrer-policy"]);
    const robots = await page.locator('meta[name="robots"]').getAttribute("content");
    if (!/noindex/.test(robots ?? "")) throw new Error("la página se puede indexar");
    const text = await page.locator("main").innerText();
    if (text.includes(flows.tracking_imei)) throw new Error("el IMEI se muestra entero");
    if (!text.includes(flows.tracking_imei.slice(-4))) throw new Error("no se muestra la orden");
    const o = await overflow();
    if (o > 1) throw new Error("overflow " + o);
    if (scriptErrors.length) throw new Error("errores de script: " + scriptErrors.join(" | "));
    return "orden visible, IMEI enmascarado, sin indexar";
  });
}

if (media) {
  // Las imágenes que la tienda subió, vistas como las ve un comprador.
  for (const theme of ["light", "dark"]) {
    for (const width of [320, 390, 768, 1440]) {
      await step(`imágenes de la tienda · ${theme} · ${width}px`, async () => {
        const p = await ctx.newPage();
        await p.addInitScript((t) => { try { localStorage.setItem("ui-theme", t); } catch {} }, theme);
        await p.setViewportSize({ width, height: 900 });
        await p.goto("/", { waitUntil: "networkidle" });
        const found = await p.evaluate(async (urls) => {
          const seen = {};
          for (const [slot, url] of Object.entries(urls)) {
            const img = [...document.images].find((i) => i.getAttribute("src") === url);
            if (!img) { seen[slot] = "ausente"; continue; }
            img.scrollIntoView();
            if (!img.complete) await new Promise((res) => { img.onload = img.onerror = res; });
            const box = img.getBoundingClientRect();
            const filter = getComputedStyle(img).filter;
            let tile = false;
            for (let n = img.parentElement; n && !n.matches("section, a, article"); n = n.parentElement) {
              if (getComputedStyle(n).backgroundColor !== "rgba(0, 0, 0, 0)") tile = true;
            }
            seen[slot] = img.naturalWidth === 0 ? "rota"
              : !/drop-shadow/.test(filter) ? "sin sombra"
              : box.left < -0.5 || box.right > innerWidth + 0.5 ? "fuera de pantalla"
              : getComputedStyle(img).objectFit !== "contain" ? "recortada"
              : (slot === "servicio" || slot === "ubicación" || slot === "hero") && tile ? "con fondo detrás"
              : "ok";
          }
          return { seen, overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth };
        }, media.urls);
        await p.close();
        const bad = Object.entries(found.seen).filter(([, v]) => v !== "ok").map(([k, v]) => `${k}: ${v}`);
        if (bad.length) throw new Error(bad.join(", "));
        if (found.overflow > 1) throw new Error("overflow " + found.overflow);
        return `${Object.keys(found.seen).length} imágenes, sombra por silueta, sin desbordes`;
      });
    }
  }
}

await step("ficha de producto y carrito", async () => {
  await page.goto("/product", { waitUntil: "networkidle" });
  const href = (await page.$$eval("a[href^='/product/']", (as) => as.map((a) => a.getAttribute("href"))))[0];
  await page.goto(href, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Agregar al carrito" }).click();
  await page.getByText("Producto agregado al carrito.").waitFor({ timeout: 10000 });
  await page.goto("/cart", { waitUntil: "networkidle" });
  const lines = await page.getByRole("spinbutton").count();
  if (lines < 1) throw new Error("el carrito no muestra la línea");
  return `${href}, ${lines} línea(s) en el carrito`;
});
await step("checkout sin pagar", async () => {
  const r = await page.goto("/checkout", { waitUntil: "networkidle" });
  if (r.status() !== 200) throw new Error("status " + r.status());
  return /Total|Resumen|pedido/i.test(await page.locator("body").innerText()) ? "formulario y resumen visibles; no se envía" : "página cargada";
});
await step("sin accesos de demostración", async () => {
  await page.goto("/auth", { waitUntil: "networkidle" });
  if (await page.getByText("Accesos de desarrollo").count()) throw new Error("la tarjeta de demostración se pinta");
});
await step("categorías de la cabecera con teclado", async () => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "networkidle" });
  const toggle = page.getByRole("button", { name: "Categorías del catálogo" });
  await toggle.focus();
  await page.keyboard.press("Enter");
  const links = await page.locator("#store-catalog-menu a").count();
  await page.keyboard.press("Escape");
  if (links < 2 || await page.locator("#store-catalog-menu").count()) throw new Error("el menú no abre o no cierra");
  await page.setViewportSize({ width: 390, height: 844 });
  return `${links} enlaces; Escape lo cierra`;
});
await step("inicio de sesión", async () => {
  await page.goto("/auth", { waitUntil: "networkidle" });
  await page.getByLabel("Usuario").fill(user);
  await page.getByLabel("Contraseña", { exact: true }).fill(password);
  await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
  await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 20000 });
  const session = (await ctx.cookies()).filter((c) => c.name.startsWith("blackdog_"));
  if (!session.length || !session.every((c) => c.secure && c.httpOnly)) throw new Error("cookies de sesión sin Secure/HttpOnly");
  return session.map((c) => `${c.name} Secure HttpOnly SameSite=${c.sameSite}`).join("; ");
});
await step("panel", async () => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto("/admin", { waitUntil: "networkidle" });
  const pick = page.getByRole("button", { name: /Selecciona una empresa/ });
  if (await pick.count()) { await pick.first().click(); await page.getByRole("option").first().click(); await page.waitForLoadState("networkidle"); }
  await page.locator("#admin-main-content").waitFor({ timeout: 20000 });
  return (await page.locator("h1").first().innerText()).replace(/\s+/g, " ").slice(0, 40);
});
for (const [name, path, expect] of [
  ["inventario", "/admin/inventory", /Inventario/], ["caja (sin vender)", "/admin/sales/pos", /Punto de venta|Caja|Productos/],
  ["servicio técnico", "/admin/service/orders", /rdenes|Taller/], ["productos", "/admin/products", /Productos/],
  ["portada (editor)", "/admin/settings/storefront", /Portada|Estilo del hero|Imagen/],
  ["equipos con serie", "/admin/inventory/units", /Equipos/], ["carga de equipos", "/admin/inventory/units/import", /Excel|plantilla/i],
  ["carga masiva de productos", "/admin/products/import", /plantilla|Carga/i], ["categorías", "/admin/products/categories", /Categorías/],
  ["impresoras", "/admin/settings/printing", /Impresoras|Impresión/], ["mensajería (WhatsApp)", "/admin/settings/messaging", /WhatsApp/],
  ["pedidos", "/admin/orders", /Órdenes|Pedidos/],
]) {
  await step(name, async () => {
    await page.goto(path, { waitUntil: "networkidle" });
    await page.locator("#admin-main-content").waitFor({ timeout: 20000 });
    const text = await page.locator("#admin-main-content").innerText();
    if (/no tiene permiso|No se pudo comprobar|Backend no disponible/i.test(text)) throw new Error("acceso denegado o error: " + text.slice(0, 80));
    if (!expect.test(text)) throw new Error("contenido inesperado: " + text.slice(0, 80).replace(/\s+/g, " "));
    return text.split("\n").filter(Boolean).slice(0, 2).join(" · ").slice(0, 60);
  });
}
await step("mensajería: sin credenciales, la pantalla dice qué falta y no deja activar", async () => {
  await page.goto("/admin/settings/messaging", { waitUntil: "networkidle" });
  const text = await page.locator("#admin-main-content").innerText();
  const missing = text.split("\n").find((line) => line.startsWith("Falta:"));
  if (!missing) throw new Error("no se dice qué falta");
  return missing.slice(0, 90);
});
await step("cerrar sesión", async () => {
  const res = await page.evaluate(async () => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    return (await fetch("/api/auth/logout/", { method: "POST", credentials: "include", headers: { "X-CSRFToken": csrf } })).status;
  });
  if (res !== 200) throw new Error("status " + res);
  return "200 con CSRF";
});
out.push(failed.length ? `FAIL respuestas 5xx: ${[...new Set(failed)].join(", ")}` : "OK   ninguna respuesta 5xx durante la navegación");
console.log(out.join("\n"));
await browser.close();
process.exit(out.some((l) => l.startsWith("FAIL")) ? 1 : 0);
