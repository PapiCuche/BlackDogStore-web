/**
 * @jest-environment node
 */
import fs from 'fs';
import path from 'path';

import sharp from 'sharp';

/**
 * FAVICON — the browser tab shows the platform's official isotype, and only that.
 *
 * It used to show two things that were nobody's brand: `app/favicon.ico` was the
 * triangle the framework ships with, and the icon the page named was a green
 * circle drawn for a prototype.
 *
 * What is pinned here:
 *   · every icon file IS the official isotype (compared pixel by pixel with a
 *     fresh reduction of it), so a letter, a badge or a new drawing fails;
 *   · the dark isotype is offered to light tabs and the light one to dark tabs —
 *     it is a one-colour silhouette and would vanish on its own colour;
 *   · the framework's default icon and the prototype's cannot come back.
 */

/* eslint-disable @typescript-eslint/no-require-imports */
const { square } = require('../scripts/generate_favicons.js');

const ROOT = process.cwd();
const BRANDING = path.join(ROOT, 'public/assets/branding');
const ISOTYPE = { 'on-light': 'logo-isotype-on-light.png', 'on-dark': 'logo-isotype-on-dark.png' } as const;
const SIZES = [16, 32, 48, 96];

async function rgba(input: string | Buffer) {
  const { data, info } = await sharp(input).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  return { data, width: info.width, height: info.height };
}

/** Mean absolute difference of the alpha channel, 0–255: how far two silhouettes are from each other. */
function silhouetteDistance(a: Buffer, b: Buffer): number {
  let total = 0;
  for (let i = 3; i < a.length; i += 4) total += Math.abs(a[i] - b[i]);
  return total / (a.length / 4);
}

/** Median luminance of what is actually drawn (alpha > 128). */
function inkLuminance(data: Buffer): number {
  const values: number[] = [];
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] > 128) values.push(0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]);
  }
  values.sort((x, y) => x - y);
  return values[Math.floor(values.length / 2)] ?? -1;
}

describe.each(Object.keys(ISOTYPE) as (keyof typeof ISOTYPE)[])('favicon %s', (variant) => {
  it.each(SIZES)('at %i px is the official isotype and nothing else', async (size) => {
    const file = path.join(BRANDING, `favicon-${variant}-${size}.png`);
    const icon = await rgba(file);
    expect([icon.width, icon.height]).toEqual([size, size]);

    const expected = await rgba(await square(sharp, path.join(BRANDING, ISOTYPE[variant]), size));
    expect(silhouetteDistance(icon.data, expected.data)).toBeLessThan(4);
  });

  it('has the contrast its name promises', async () => {
    const { data } = await rgba(path.join(BRANDING, `favicon-${variant}-32.png`));
    const luminance = inkLuminance(data);
    if (variant === 'on-light') expect(luminance).toBeLessThan(60);       // dark ink, for a light tab
    else expect(luminance).toBeGreaterThan(195);                          // light ink, for a dark tab
  });

  it('has a transparent background: no tile, no badge', async () => {
    const { data, width } = await rgba(path.join(BRANDING, `favicon-${variant}-48.png`));
    for (const [x, y] of [[0, 0], [width - 1, 0], [0, width - 1], [width - 1, width - 1]]) {
      expect(data[(y * width + x) * 4 + 3]).toBe(0);
    }
  });
});

describe('what a browser fetches by itself', () => {
  it('/favicon.ico carries the isotype at 16, 32 and 48 px', async () => {
    const ico = fs.readFileSync(path.join(ROOT, 'public/favicon.ico'));
    expect([ico.readUInt16LE(0), ico.readUInt16LE(2)]).toEqual([0, 1]);          // an icon file
    const count = ico.readUInt16LE(4);
    const entries = Array.from({ length: count }, (_, index) => {
      const at = 6 + index * 16;
      return { size: ico.readUInt8(at), length: ico.readUInt32LE(at + 8), offset: ico.readUInt32LE(at + 12) };
    });
    expect(entries.map((entry) => entry.size)).toEqual([16, 32, 48]);
    for (const entry of entries) {
      const image = await rgba(ico.subarray(entry.offset, entry.offset + entry.length));
      const expected = await rgba(await square(sharp, path.join(BRANDING, ISOTYPE['on-light']), entry.size));
      expect(silhouetteDistance(image.data, expected.data)).toBeLessThan(4);
    }
  });

  it('the apple-touch icon is the isotype on an opaque background (iOS paints transparency black)', async () => {
    const icon = await rgba(path.join(ROOT, 'public/apple-touch-icon.png'));
    expect([icon.width, icon.height]).toEqual([180, 180]);
    let transparent = 0;
    for (let i = 3; i < icon.data.length; i += 4) if (icon.data[i] < 255) transparent += 1;
    expect(transparent).toBe(0);
    expect(Array.from(icon.data.subarray(0, 3))).toEqual([255, 255, 255]);
    // What is drawn on that white is the dark isotype: there is ink, and it is dark.
    const ink: number[] = [];
    for (let i = 0; i < icon.data.length; i += 4) {
      const luminance = 0.2126 * icon.data[i] + 0.7152 * icon.data[i + 1] + 0.0722 * icon.data[i + 2];
      if (luminance < 128) ink.push(luminance);
    }
    expect(ink.length).toBeGreaterThan(180 * 180 * 0.15);
    expect(Math.max(...ink.slice(0, 2000))).toBeLessThan(128);
  });
});

describe('what cannot come back', () => {
  it("the framework's default icon is not in the app directory", () => {
    // A file named `app/favicon.ico` takes precedence over what the page declares.
    expect(fs.existsSync(path.join(ROOT, 'app/favicon.ico'))).toBe(false);
    expect(fs.existsSync(path.join(ROOT, 'app/icon.png'))).toBe(false);
  });

  it("the prototype's circle is gone, and nothing names it", () => {
    expect(fs.existsSync(path.join(BRANDING, 'favicon.svg'))).toBe(false);
    expect(fs.readFileSync(path.join(ROOT, 'app/layout.tsx'), 'utf8')).not.toContain('favicon.svg');
  });
});

describe('what the page declares', () => {
  it('one icon per colour scheme, each the isotype that shows on it, and the apple-touch icon', async () => {
    const { PLATFORM_ICONS } = await import('@/app/lib/platform-icons');
    const icons = PLATFORM_ICONS.icon as { url: string; media: string; sizes: string; type: string }[];

    const light = icons.filter((icon) => icon.media === '(prefers-color-scheme: light)');
    const dark = icons.filter((icon) => icon.media === '(prefers-color-scheme: dark)');
    expect(light.map((icon) => icon.sizes).sort()).toEqual(['16x16', '32x32', '48x48', '96x96']);
    expect(dark.map((icon) => icon.sizes).sort()).toEqual(['16x16', '32x32', '48x48', '96x96']);
    expect(light.every((icon) => icon.url.includes('favicon-on-light-'))).toBe(true);
    expect(dark.every((icon) => icon.url.includes('favicon-on-dark-'))).toBe(true);
    expect(icons).toHaveLength(8);                                          // and no third, unconditional one

    for (const icon of icons) {
      expect(icon.type).toBe('image/png');
      expect(fs.existsSync(path.join(ROOT, 'public', icon.url))).toBe(true);
    }
    expect(PLATFORM_ICONS.apple).toEqual([{ url: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }]);
  });

  it('the root layout uses exactly that', () => {
    const layout = fs.readFileSync(path.join(ROOT, 'app/layout.tsx'), 'utf8');
    expect(layout).toMatch(/icons:\s*PLATFORM_ICONS/);
    expect(layout.match(/icons:/g)).toHaveLength(2);       // with a company name, and without one
  });
});
