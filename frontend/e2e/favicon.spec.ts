import { expect, test } from "@playwright/test";

/**
 * FAVICON — the tab icon is the platform's isotype, served and declared.
 *
 * What a browser does with it: it reads the `<link rel="icon">` whose `media`
 * matches its colour scheme, and when a page names none it asks for
 * `/favicon.ico` by itself. Both paths have to answer with an image; the old
 * icon was the framework's default and the one the page named was a prototype's.
 */

const PAGES = ["/", "/product", "/admin"];

test("la pestaña declara el isotipo para tema claro y para tema oscuro, y los archivos existen", async ({ page, request }) => {
  for (const path of PAGES) {
    await page.goto(path, { waitUntil: "domcontentloaded" });
    const icons = await page.locator('head link[rel="icon"]').evaluateAll((links) =>
      links.map((link) => ({
        href: link.getAttribute("href") ?? "", media: link.getAttribute("media") ?? "",
        sizes: link.getAttribute("sizes") ?? "", type: link.getAttribute("type") ?? "",
      })));

    expect(icons.length, `${path}: iconos declarados`).toBe(8);
    expect(icons.filter((icon) => !icon.media), `${path}: un icono sin tema ganaría a los demás`).toEqual([]);
    for (const [scheme, variant] of [["light", "on-light"], ["dark", "on-dark"]] as const) {
      const set = icons.filter((icon) => icon.media === `(prefers-color-scheme: ${scheme})`);
      expect(set.map((icon) => icon.sizes).sort(), `${path} · ${scheme}`).toEqual(["16x16", "32x32", "48x48", "96x96"]);
      expect(set.every((icon) => icon.href.includes(`favicon-${variant}-`)), `${path} · ${scheme}: contraste equivocado`).toBe(true);
    }
    expect(icons.some((icon) => /favicon\.svg|\.ico/.test(icon.href)), `${path}: icono antiguo`).toBe(false);

    const apple = await page.locator('head link[rel="apple-touch-icon"]').getAttribute("href");
    expect(apple).toBe("/apple-touch-icon.png");

    if (path === PAGES[0]) {
      for (const href of [...icons.map((icon) => icon.href), apple as string]) {
        const response = await request.get(href);
        expect(response.status(), href).toBe(200);
        expect(response.headers()["content-type"], href).toContain("image/png");
        expect((await response.body()).subarray(1, 4).toString(), href).toBe("PNG");
      }
    }
  }
});

test("lo que el navegador pide por su cuenta responde con el icono", async ({ request }) => {
  const response = await request.get("/favicon.ico");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toMatch(/image\/(x-icon|vnd\.microsoft\.icon)/);
  const body = await response.body();
  expect([body.readUInt16LE(0), body.readUInt16LE(2), body.readUInt16LE(4)]).toEqual([0, 1, 3]);
});
