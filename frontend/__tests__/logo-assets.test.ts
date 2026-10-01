import fs from 'fs';
import path from 'path';
import zlib from 'zlib';

/**
 * M12F.2 — que el archivo sea lo que su nombre dice.
 *
 * `logo-horizontal-on-dark.png` es una promesa: «esto es claro, va sobre fondo
 * oscuro». Todo el sistema de contraste descansa en ella —`pickLogo` elige por
 * nombre, no mira los píxeles— así que si un día alguien reemplaza un archivo
 * por su versión contraria, no falla nada: simplemente el logotipo desaparece
 * sobre su fondo, que es exactamente el defecto que M12E vino a cerrar.
 *
 * Esto abre el PNG y mide. Sin dependencias: un PNG de 8 bits sin entrelazar
 * se descomprime con `zlib` y se recorre a mano.
 */

const DIR = path.join(process.cwd(), 'public/assets/branding');

type Png = { width: number; height: number; pixels: Buffer; channels: number };

function readPng(file: string): Png {
  const buf = fs.readFileSync(file);
  if (buf.readUInt32BE(0) !== 0x89504e47) throw new Error(`${file} no es PNG`);

  let pos = 8;
  let width = 0, height = 0, bitDepth = 0, colourType = 0, interlace = 0;
  const idat: Buffer[] = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString('ascii', pos + 4, pos + 8);
    const data = buf.subarray(pos + 8, pos + 8 + len);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colourType = data[9];
      interlace = data[12];
    } else if (type === 'IDAT') {
      idat.push(data);
    } else if (type === 'IEND') break;
    pos += 12 + len;
  }
  if (bitDepth !== 8 || interlace !== 0) {
    throw new Error(`${file}: sólo se leen PNG de 8 bits sin entrelazar`);
  }
  // 6 = RGBA, 2 = RGB, 4 = gris+alfa, 0 = gris
  const channels = { 0: 1, 2: 3, 4: 2, 6: 4 }[colourType];
  if (!channels) throw new Error(`${file}: tipo de color ${colourType} no soportado`);

  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const out = Buffer.alloc(height * stride);

  // Deshacer los filtros por línea, que es lo que hace que un PNG comprima.
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
  return { width, height, pixels: out, channels };
}

/** Luminancia mediana de los píxeles VISIBLES. El fondo transparente no cuenta. */
function medianVisibleLuminance(png: Png): { median: number; visible: number } {
  const { pixels, channels } = png;
  const lums: number[] = [];
  for (let i = 0; i < pixels.length; i += channels) {
    let r: number, g: number, b: number, a: number;
    if (channels === 4) { [r, g, b, a] = [pixels[i], pixels[i+1], pixels[i+2], pixels[i+3]]; }
    else if (channels === 3) { [r, g, b, a] = [pixels[i], pixels[i+1], pixels[i+2], 255]; }
    else if (channels === 2) { r = g = b = pixels[i]; a = pixels[i+1]; }
    else { r = g = b = pixels[i]; a = 255; }
    if (a <= 24) continue;                       // prácticamente transparente
    lums.push(0.2126 * r + 0.7152 * g + 0.0722 * b);
  }
  lums.sort((x, y) => x - y);
  return { median: lums.length ? lums[Math.floor(lums.length / 2)] : NaN, visible: lums.length };
}

const VARIANTS = [
  'logo-horizontal-on-dark',
  'logo-horizontal-on-light',
  'logo-vertical-on-dark',
  'logo-vertical-on-light',
  'logo-isotype-on-dark',
  'logo-isotype-on-light',
];

describe('el archivo es lo que su nombre promete', () => {
  it.each(VARIANTS)('%s existe y tiene contenido visible', (name) => {
    const png = readPng(path.join(DIR, `${name}.png`));
    const { visible } = medianVisibleLuminance(png);
    // Un PNG entero transparente pasaría cualquier comprobación de nombre y no
    // dibujaría nada.
    expect(visible).toBeGreaterThan(1000);
  });

  it.each(VARIANTS.filter((v) => v.includes('on-dark')))(
    '%s es CLARO, como exige ir sobre fondo oscuro',
    (name) => {
      const { median } = medianVisibleLuminance(readPng(path.join(DIR, `${name}.png`)));
      expect(median).toBeGreaterThan(128);
    },
  );

  it.each(VARIANTS.filter((v) => v.includes('on-light')))(
    '%s es OSCURO, como exige ir sobre fondo claro',
    (name) => {
      const { median } = medianVisibleLuminance(readPng(path.join(DIR, `${name}.png`)));
      expect(median).toBeLessThan(128);
    },
  );

  it('cada pareja son versiones cromáticas de la MISMA pieza', () => {
    // Mismo número de píxeles visibles: si alguien sustituyera una variante por
    // otro dibujo, la geometría dejaría de coincidir aunque el tono acertara.
    for (const base of ['logo-horizontal', 'logo-vertical', 'logo-isotype']) {
      const dark = medianVisibleLuminance(readPng(path.join(DIR, `${base}-on-dark.png`)));
      const light = medianVisibleLuminance(readPng(path.join(DIR, `${base}-on-light.png`)));
      expect(Math.abs(dark.visible - light.visible)).toBeLessThan(dark.visible * 0.02);
    }
  });

  it('el isotipo llega al mínimo digital del manual', () => {
    // 48 px. Un archivo más pequeño no se puede usar donde el manual lo manda.
    for (const name of ['logo-isotype-on-dark', 'logo-isotype-on-light']) {
      const png = readPng(path.join(DIR, `${name}.png`));
      expect(Math.min(png.width, png.height)).toBeGreaterThanOrEqual(48);
    }
  });
});
