import type { CatalogModel } from '@/app/lib/catalog-models';
import { V3_MODEL_PATHS, modelGroupJsonLd, v3VariantPath } from '@/app/lib/structured-data';

import { modelFixture } from './support/device-commerce-fixtures';

/**
 * CATALOG-REFERENCE-01 · PR D — the model page's schema.org data, by the PR C picture rule and D-D-1.
 *
 *  - each variant's `image` is what the page shows for it: its own pictures, else its finish's (model + colour),
 *    else none — never another colour's photo nor the model's cover;
 *  - the public page `/modelo/<slug>` now has one address per new variant (`?variante=`), while the canonical (the
 *    group's `url`) stays the model's.
 */

const ORIGIN = 'https://tienda.example';
const FINISH = (n: number) => `/api/storefront/images/${String(n).padStart(32, 'f')}`;

function model(): CatalogModel {
  const data = modelFixture() as unknown as CatalogModel;
  data.image_url = '/media/portada.png';
  // `finish_images` is keyed by the option of the attribute whose role is `color` (PR C).
  data.attributes = data.attributes.map((attribute) => ({ ...attribute, role: attribute.kind === 'color' ? 'color' : 'capacity' }));
  // Azul (21) has finish pictures; Blanco (22) and Rojo (23) have none. The server resolves each variant's gallery.
  const azul = [{ url: FINISH(1), alt_text: 'Equipo X Azul', is_primary: true, width: 800, height: 800 }];
  data.finish_images = { '21': azul };
  data.variants = data.variants.map((variant) => {
    const gallery = variant.options['4'] === 21 ? azul : [];
    return { ...variant, image_url: '', images: [], gallery, image_pending: gallery.length === 0 };
  });
  return data;
}

const variants = (data: CatalogModel, paths = V3_MODEL_PATHS) =>
  modelGroupJsonLd(data, ORIGIN, '', paths).hasVariant as Record<string, unknown>[];

describe('la foto de cada variante en el JSON-LD', () => {
  it('sin fotos propias, la de su acabado (la galería del servidor)', () => {
    const [azul128] = variants(model());
    expect(azul128.image).toEqual([`${ORIGIN}${FINISH(1)}`]);
  });

  it('la galería que resolvió el servidor manda', () => {
    const data = model();
    data.variants[0].gallery = [{ url: FINISH(9), alt_text: '', is_primary: true, width: 1, height: 1 }];
    expect(variants(data)[0].image).toEqual([`${ORIGIN}${FINISH(9)}`]);
  });

  it('sin foto propia ni de su acabado no lleva imagen: nunca la portada ni la de otro color', () => {
    const [, blanco128] = variants(model());
    expect(blanco128).not.toHaveProperty('image');
    expect(JSON.stringify(blanco128)).not.toContain('portada');
    expect(JSON.stringify(blanco128)).not.toContain(FINISH(1));
  });
});

describe('sólo las fotos de la tienda que no son demostrativas (revisión de #373)', () => {
  const image = (url: string, extra: Record<string, unknown> = {}) =>
    ({ url, alt_text: '', is_primary: true, width: 1, height: 1, ...extra });

  it('un recurso demostrativo («Imagen referencial») no se publica como la foto del producto', () => {
    const data = model();
    data.variants[0].gallery = [image(FINISH(7), { is_demo: true }), image(FINISH(8))];
    expect(variants(data)[0].image).toEqual([`${ORIGIN}${FINISH(8)}`]);
  });

  it('una foto que no subió la tienda (otro host o una ruta propia) tampoco', () => {
    const data = model();
    data.variants[0].gallery = [image('https://cdn.proveedor.example/x.jpg'), image('/media/azul.png')];
    expect(variants(data)[0]).not.toHaveProperty('image');
  });

  it('con un servidor sin `gallery`, la misma regla sobre sus fotos propias', () => {
    const data = model();
    data.variants[0] = { ...data.variants[0], gallery: undefined, image_url: 'https://cdn.proveedor.example/y.jpg', images: [image(FINISH(9))] };
    expect(variants(data)[0].image).toEqual([`${ORIGIN}${FINISH(9)}`]);
  });
});

describe('direcciones de la ficha pública (D-D-1)', () => {
  it('cada variante nueva tiene su dirección /modelo/<slug>?variante=<variante>', () => {
    const [first] = variants(model());
    const url = `${ORIGIN}/modelo/equipo-x?variante=equipo-x-41`;
    expect(first.url).toBe(url);
    expect((first.offers as Record<string, unknown>).url).toBe(url);
  });

  it('el grupo (la canónica) sigue siendo la dirección del modelo, sin parámetros', () => {
    expect(modelGroupJsonLd(model(), ORIGIN, '', V3_MODEL_PATHS).url).toBe(`${ORIGIN}/modelo/equipo-x`);
  });

  it('v3VariantPath escapa los dos slugs', () => {
    expect(v3VariantPath('a b', 'c&d')).toBe('/modelo/a%20b?variante=c%26d');
  });
});
