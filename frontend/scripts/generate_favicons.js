/**
 * The browser-tab icon: the platform's official isotype, and nothing else.
 *
 *     npm run favicons:generate
 *
 * Reads the two official isotypes in `public/assets/branding/` — the dark one
 * (for light backgrounds) and the light one (for dark backgrounds) — and
 * writes every size a browser asks for. Nothing is drawn here: no letter, no
 * badge, no background shape. The isotype is a one-colour silhouette, so a
 * single file cannot be legible on both a light and a dark tab strip; that is
 * why there are two sets, chosen by the browser with `prefers-color-scheme`.
 *
 * The apple-touch icon is the one exception to «no background»: iOS paints a
 * transparent icon on black, which would hide the dark isotype completely.
 */
const fs = require('fs').promises;
const path = require('path');

const BRANDING = path.join(__dirname, '..', 'public', 'assets', 'branding');
const PUBLIC = path.join(__dirname, '..', 'public');
const TRANSPARENT = { r: 0, g: 0, b: 0, alpha: 0 };

/** The isotype on a square canvas, `size` px, with `margin` (0–1) of breathing room. */
async function square(sharp, source, size, { margin = 0, background = TRANSPARENT } = {}) {
  const inner = Math.max(1, Math.round(size * (1 - 2 * margin)));
  // No sharpening, thresholding or alpha gain: tried, and at 16 px it fills the
  // face in and the sunglasses — what makes it THIS dog — disappear. A plain
  // Lanczos reduction keeps them.
  const dog = await sharp(source)
    .resize(inner, inner, { fit: 'contain', background: TRANSPARENT, kernel: 'lanczos3' })
    .png()
    .toBuffer();
  return sharp({ create: { width: size, height: size, channels: 4, background } })
    .composite([{ input: dog, gravity: 'centre' }])
    .png({ compressionLevel: 9 })
    .toBuffer();
}

/** An .ico that carries PNGs, which every current browser reads. */
function ico(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  const entries = [];
  let offset = 6 + 16 * images.length;
  for (const { size, data } of images) {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(size >= 256 ? 0 : size, 0);
    entry.writeUInt8(size >= 256 ? 0 : size, 1);
    entry.writeUInt16LE(1, 4);
    entry.writeUInt16LE(32, 6);
    entry.writeUInt32LE(data.length, 8);
    entry.writeUInt32LE(offset, 12);
    entries.push(entry);
    offset += data.length;
  }
  return Buffer.concat([header, ...entries, ...images.map((image) => image.data)]);
}

async function generate() {
  const sharp = require('sharp');
  const onLight = path.join(BRANDING, 'logo-isotype-on-light.png');
  const onDark = path.join(BRANDING, 'logo-isotype-on-dark.png');

  for (const size of [16, 32, 48, 96]) {
    await fs.writeFile(path.join(BRANDING, `favicon-on-light-${size}.png`), await square(sharp, onLight, size));
    await fs.writeFile(path.join(BRANDING, `favicon-on-dark-${size}.png`), await square(sharp, onDark, size));
  }
  // What a browser fetches by itself when a page names no icon: the canonical, dark isotype.
  const sizes = [16, 32, 48];
  await fs.writeFile(
    path.join(PUBLIC, 'favicon.ico'),
    ico(await Promise.all(sizes.map(async (size) => ({ size, data: await square(sharp, onLight, size) })))),
  );
  await fs.writeFile(
    path.join(PUBLIC, 'apple-touch-icon.png'),
    await square(sharp, onLight, 180, { margin: 0.14, background: { r: 255, g: 255, b: 255, alpha: 1 } }),
  );
}

module.exports = { square, ico, generate };

if (require.main === module) {
  generate().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
