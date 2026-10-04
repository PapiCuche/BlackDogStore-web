import fs from 'node:fs';
import path from 'node:path';

import { isManagedStorefrontImage, storefrontMediaStyle } from '@/app/lib/storefront-media';

/**
 * La profundidad de un recorte: UNA regla, y que se vea.
 *
 * La sombra sigue la silueta de la imagen (`drop-shadow`), no dibuja una caja.
 * Pero una sombra negra sobre una superficie oscura no se ve, y la portada
 * tiene dos superficies oscuras: la losa del hero y el tema oscuro entero. Por
 * eso el valor vive en dos variables de CSS y el componente sólo dice sobre qué
 * superficie está.
 */

const MANAGED = '/api/storefront/images/' + 'a'.repeat(32);
const CSS = fs.readFileSync(path.join(__dirname, '..', 'app', 'globals.css'), 'utf8');

describe('storefrontMediaStyle', () => {
  it('reconoce sólo las imágenes de la tubería propia', () => {
    expect(isManagedStorefrontImage(MANAGED)).toBe(true);
    expect(isManagedStorefrontImage(MANAGED + '/')).toBe(true);
    expect(isManagedStorefrontImage('https://cdn.example/foto.png')).toBe(false);
    expect(isManagedStorefrontImage('/assets/logo.svg')).toBe(false);
    expect(isManagedStorefrontImage('')).toBe(false);
  });

  it('no toca una imagen externa', () => {
    expect(storefrontMediaStyle('https://cdn.example/foto.png')).toBeUndefined();
    expect(storefrontMediaStyle('https://cdn.example/foto.png', 'slab')).toBeUndefined();
  });

  it('sobre la página usa la sombra del tema', () => {
    expect(storefrontMediaStyle(MANAGED)).toEqual({ filter: 'var(--cutout-shadow)' });
  });

  it('sobre la losa oscura usa el halo', () => {
    expect(storefrontMediaStyle(MANAGED, 'slab')).toEqual({ filter: 'var(--cutout-shadow-on-slab)' });
  });
});

describe('las variables de la sombra', () => {
  const declarations = (name: string) =>
    Array.from(CSS.matchAll(new RegExp(`${name}:\\s*([^;]+);`, 'g'))).map((m) => m[1].trim());

  it('la sombra de página es oscura en el tema claro y clara en el oscuro', () => {
    const values = declarations('--cutout-shadow');
    expect(values).toHaveLength(2);
    expect(values[0]).toMatch(/drop-shadow\(.*rgb\(0 0 0/);
    expect(values[1]).toBe('var(--cutout-shadow-on-slab)');

    const dark = CSS.slice(CSS.indexOf(':root[data-theme="dark"] {'));
    expect(dark.slice(0, dark.indexOf('\n}'))).toContain('--cutout-shadow: var(--cutout-shadow-on-slab);');
  });

  it('el halo de la losa se define una vez y es claro', () => {
    const values = declarations('--cutout-shadow-on-slab');
    expect(values).toHaveLength(1);
    expect(values[0]).toMatch(/drop-shadow\(.*rgb\(255 255 255/);
  });

  it('no queda ninguna regla de sombra paralela', () => {
    expect(CSS).not.toMatch(/\.v3-cutout/);
  });
});
