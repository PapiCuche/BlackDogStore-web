import { test, expect, type Page } from "@playwright/test";
import zlib from "zlib";

/**
 * M12F.3 — el logotipo se ve, medido sobre PÍXELES REALES.
 *
 * Hasta ahora la cadena de garantías tenía un eslabón de fe: un test comprueba
 * que el ARCHIVO `…-on-dark.png` es claro, y otro que el componente PIDE ese
 * archivo. Entre los dos queda todo lo que puede pasar en el navegador — un
 * `filter`, un `opacity`, un `mix-blend-mode`, un fondo que no era el que se
 * suponía, una versión cacheada — y ninguno de los dos lo vería.
 *
 * Esto recorta el logotipo tal y como se pinta y mide el contraste entre su
 * tinta y su fondo. Si el logotipo desaparece, esta prueba se entera.
 */

/** PNG de 8 bits sin entrelazar → luminancia de cada píxel. */
function luminances(png: Buffer): number[] {
  let pos = 8, width = 0, height = 0, colourType = 0, bitDepth = 0;
  const idat: Buffer[] = [];
  while (pos < png.length) {
    const len = png.readUInt32BE(pos);
    const type = png.toString("ascii", pos + 4, pos + 8);
    const data = png.subarray(pos + 8, pos + 8 + len);
    if (type === "IHDR") {
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      bitDepth = data[8]; colourType = data[9];
    } else if (type === "IDAT") idat.push(data);
    else if (type === "IEND") break;
    pos += 12 + len;
  }
  const channels = ({ 0: 1, 2: 3, 4: 2, 6: 4 } as Record<number, number>)[colourType];
  if (!channels || bitDepth !== 8) throw new Error("PNG no soportado");

  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const out = Buffer.alloc(height * stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? out[y * stride + x - channels] : 0;
      const b = y > 0 ? out[(y - 1) * stride + x] : 0;
      const c = x >= channels && y > 0 ? out[(y - 1) * stride + x - channels] : 0;
      let v = line[x];
      if (filter === 1) v += a;
      else if (filter === 2) v += b;
      else if (filter === 3) v += (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      out[y * stride + x] = v & 0xff;
    }
  }
  const lums: number[] = [];
  for (let i = 0; i < out.length; i += channels) {
    const r = out[i];
    const g = channels >= 3 ? out[i + 1] : r;
    const b = channels >= 3 ? out[i + 2] : r;
    lums.push(0.2126 * r + 0.7152 * g + 0.0722 * b);
  }
  return lums;
}

/** Contraste entre lo más oscuro y lo más claro del recorte. */
function inkContrast(shot: Buffer): number {
  const lums = luminances(shot).sort((a, b) => a - b);
  // Percentiles, no extremos: un píxel suelto de antialias no puede decidir.
  const dark = lums[Math.floor(lums.length * 0.05)];
  const light = lums[Math.floor(lums.length * 0.95)];
  const rel = (v: number) => {
    const x = v / 255;
    return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
  };
  return (rel(light) + 0.05) / (rel(dark) + 0.05);
}

async function withTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((v) => {
    try { window.localStorage.setItem("ui-theme", v); } catch { /* ventana privada */ }
  }, theme);
}

for (const theme of ["light", "dark"] as const) {
  test(`el logotipo de la cabecera se ve en tema ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await withTheme(page, theme);
    await page.goto("/", { waitUntil: "networkidle" });

    const logo = page.locator("header img").first();
    await expect(logo).toBeVisible();

    // Nada entre el archivo y lo que se ve: sin filtros, sin mezcla, opaco.
    const style = await logo.evaluate((el) => {
      const cs = getComputedStyle(el);
      return { filter: cs.filter, blend: cs.mixBlendMode, opacity: +cs.opacity };
    });
    expect(style.filter).toBe("none");
    expect(style.blend).toBe("normal");
    expect(style.opacity).toBeGreaterThan(0.9);

    const contrast = inkContrast(await logo.screenshot());
    expect(
      contrast,
      `la cabecera pinta su logotipo a ${contrast.toFixed(2)}:1 en tema ${theme}`,
    ).toBeGreaterThanOrEqual(3);
  });
}

test("la losa del hero no cambia con el tema", async ({ page }) => {
  // Aquí estuvo el defecto que el propietario vio: el hero era una losa negra
  // deliberada, la traducción de paleta de M12F lo volvió crema en tema claro,
  // y el lockup negro quedó invisible sobre él.
  //
  // NO se mide el contraste de la imagen del hero, y el motivo importa: tras
  // M12F.3 esa imagen es una MARCA DE AGUA al 5 % — textura de marca, no
  // contenido. Exigirle 3:1 sería exigir que la textura deje de ser textura.
  // Lo que sí es invariante es que la losa siga siendo oscura en los dos temas.
  for (const theme of ["light", "dark"] as const) {
    await page.setViewportSize({ width: 1440, height: 900 });
    await withTheme(page, theme);
    await page.goto("/", { waitUntil: "networkidle" });

    const bg = await page
      .locator("section")
      .first()
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    const [r, g, b] = bg.match(/[\d.]+/g)!.slice(0, 3).map(Number);
    const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    expect(
      luminance,
      `el hero dejó de ser una losa oscura en tema ${theme}: ${bg}`,
    ).toBeLessThan(90);

    // Y la marca de agua sigue siendo marca de agua: presente, pero tenue.
    const mark = page.locator("section").first().locator("img").first();
    if (await mark.count()) {
      const opacity = await mark.evaluate((el) => {
        let n: HTMLElement | null = el as HTMLElement, acc = 1;
        while (n) { acc *= +getComputedStyle(n).opacity; n = n.parentElement; }
        return acc;
      });
      expect(opacity).toBeLessThan(0.25);
      expect(opacity).toBeGreaterThan(0);
    }
  }
});
