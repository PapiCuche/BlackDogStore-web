/**
 * @jest-environment node
 */
import fs from 'fs';
import path from 'path';
import { renderToString } from 'react-dom/server';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-REFERENCE-01 · PR D (ADR §4.3) — the public model page `/modelo/<slug>` (the shop in V3) shows the premium
 * configurator in the identity of the Manual V4, ONLY inside its block: the V3 header, footer and every other page
 * keep their design.
 *
 *  · the configurator is wrapped in `data-design="v4" data-v4-scope="block"` with the manual's font variable, and the
 *    page imports the V4 sheet (every rule of which is scoped to that attribute);
 *  · `?variante=<variant slug>` opens the page with that variant chosen, already in the server's HTML; anything else
 *    is the usual first choice;
 *  · the canonical stays the model's address, without the parameter (D-D-1);
 *  · «Catálogo» is the shop's catalogue, not the V4 preview.
 */

const requestMemos: Map<string, unknown>[] = [];
jest.mock('react', () => {
  const actual = jest.requireActual('react');
  return {
    ...actual,
    cache: <A extends unknown[], R>(fn: (...args: A) => R) => {
      const memo = new Map<string, R>();
      requestMemos.push(memo as Map<string, unknown>);
      return (...args: A) => {
        const key = JSON.stringify(args);
        if (!memo.has(key)) memo.set(key, fn(...args));
        return memo.get(key) as R;
      };
    },
  };
});

jest.mock('next/headers', () => ({
  headers: async () => ({ get: (name: string) => ({ host: 'tienda.example', 'x-forwarded-proto': 'https' } as Record<string, string>)[name.toLowerCase()] ?? null }),
}));

const MANAGED = (n: number) => `/api/storefront/images/${String(n).padStart(32, 'c')}`;
const picture = (url: string, extra: Record<string, unknown> = {}) =>
  ({ url, alt_text: '', is_primary: true, width: 800, height: 800, ...extra });

/** The fixture as a PR C server sends it: 43 with its own panel photo, 42 pending, 41 with only a demo picture. */
function served() {
  const model = { ...modelFixture(), slug: 'equipo-x' };
  model.variants = model.variants.map((variant) => ({
    ...variant,
    gallery: variant.product_id === 43 ? [picture(MANAGED(43))]
      : variant.product_id === 41 ? [picture(MANAGED(41), { is_demo: true })]
        : variant.product_id === 42 ? [] : [picture(MANAGED(variant.product_id))],
  })) as typeof model.variants;
  return model;
}

const fetchMock = jest.fn();
beforeEach(() => {
  for (const memo of requestMemos) memo.clear();
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) =>
    String(url).includes('/catalog/models/') ? reply(served()) : reply({}, 404));
  global.fetch = fetchMock as unknown as typeof fetch;
});

async function metadata(searchParams: Record<string, string> = {}) {
  const { generateMetadata } = await import('@/app/modelo/[slug]/page');
  return generateMetadata({ params: Promise.resolve({ slug: 'equipo-x' }), searchParams: Promise.resolve(searchParams) });
}

function jsonLd(markup: string): Record<string, unknown>[] {
  return [...markup.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)]
    .flatMap((match) => { const block = JSON.parse(match[1]); return Array.isArray(block) ? block : [block]; });
}

async function html(searchParams: Record<string, string> = {}) {
  const { default: Page } = await import('@/app/modelo/[slug]/page');
  const element = await Page({ params: Promise.resolve({ slug: 'equipo-x' }), searchParams: Promise.resolve(searchParams) });
  return renderToString(<StorefrontProvider config={NEUTRAL_CONFIG}>{element}</StorefrontProvider>).replace(/<!-- -->/g, '');
}

/** The radio of `label` in the first HTML is the checked one. */
const checked = (markup: string, label: string) =>
  new RegExp(`role="radio" aria-checked="true"[^>]*>(?:<span[^>]*></span>)?${label}<`).test(markup);

describe('/modelo/<slug> con el configurador V4 en su bloque', () => {
  it('el configurador va dentro de data-design="v4" data-v4-scope="block", con la fuente del manual', async () => {
    const markup = await html();
    const open = markup.match(/<div[^>]*data-design="v4"[^>]*>/)?.[0] ?? '';
    expect(open).toContain('data-v4-scope="block"');
    expect(open).toMatch(/class="[^"]*\S/);
    expect(markup).toMatch(/<h1[^>]*>Equipo X<\/h1>/);
    expect(markup.indexOf('data-design="v4"')).toBeLessThan(markup.indexOf('<h1'));
  });

  it('la página importa la hoja V4 (acotada a su atributo) y no toca la V3 global', () => {
    const source = fs.readFileSync(path.join(process.cwd(), 'app/modelo/[slug]/page.tsx'), 'utf8');
    expect(source).toMatch(/import\s+["']\.\.\/\.\.\/v4\/v4\.css["']/);
    const globals = fs.readFileSync(path.join(process.cwd(), 'app/globals.css'), 'utf8');
    expect(globals).not.toMatch(/Montserrat|--font-v4|data-v4-scope/);
  });

  it('«Catálogo» lleva al catálogo de la tienda, no a la vista previa', async () => {
    const markup = await html();
    expect(markup).toMatch(/<a [^>]*href="\/product"[^>]*>Catálogo<\/a>/);
    expect(markup).not.toMatch(/href="\/v4/);
  });

  it('?variante= abre esa variante ya en el primer HTML', async () => {
    const markup = await html({ variante: 'equipo-x-43' });
    expect(checked(markup, '256 GB')).toBe(true);
    expect(markup).toContain('S/ 4499.00');
  });

  it('una variante que no es de este modelo es la elección de siempre, y la página no la repite', async () => {
    const markup = await html({ variante: 'equipo-x-99' });
    expect(checked(markup, '128 GB')).toBe(true);
    expect(markup).not.toContain('equipo-x-99');
  });

  it('?variante= vuelve en el JSON-LD: el Product de esa variante con su dirección y su Offer', async () => {
    const markup = await html({ variante: 'equipo-x-43' });
    expect(checked(markup, '256 GB')).toBe(true);
    const group = jsonLd(markup).find((block) => block['@type'] === 'ProductGroup')!;
    const product = (group.hasVariant as Record<string, unknown>[]).find((item) => item.sku === 'equipo-x-43')!;
    const own = 'https://tienda.example/modelo/equipo-x?variante=equipo-x-43';
    expect(product.url).toBe(own);
    expect(product.offers).toMatchObject({ url: own, price: '4499.00' });
    expect(product.image).toEqual([`https://tienda.example${MANAGED(43)}`]);
    expect(group.url).toBe('https://tienda.example/modelo/equipo-x');
  });

  it('con una variante en la dirección, la canónica sigue siendo la del modelo, sin el parámetro', async () => {
    const meta = await metadata({ variante: 'equipo-x-43' });
    expect(String(meta.alternates?.canonical)).toMatch(/\/modelo\/equipo-x$/);
  });
});

describe('vista previa al compartir un enlace con ?variante= (revisión de #373)', () => {
  const images = (meta: Awaited<ReturnType<typeof metadata>>) =>
    ((meta.openGraph?.images ?? []) as { url: string }[]).map((image) => String(image.url));

  it('la foto es la de esa variante (su acabado), no la portada del modelo', async () => {
    expect(images(await metadata({ variante: 'equipo-x-43' }))).toEqual([`https://tienda.example${MANAGED(43)}`]);
  });

  it('una variante con «Imagen pendiente» no lleva foto: nunca la de otro color', async () => {
    expect(images(await metadata({ variante: 'equipo-x-42' }))).toEqual([]);
  });

  it('un recurso demostrativo no se anuncia como la foto', async () => {
    expect(images(await metadata({ variante: 'equipo-x-41' }))).toEqual([]);
  });

  it('sin variante en la dirección, la del modelo, como antes', async () => {
    expect(images(await metadata())).toEqual(['http://localhost/modelo.png']);
  });
});
