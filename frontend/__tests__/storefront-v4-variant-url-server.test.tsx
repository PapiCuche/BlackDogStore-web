/**
 * @jest-environment node
 */
import { renderToString } from 'react-dom/server';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import type { CatalogModel } from '@/app/lib/catalog-models';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';
import { modelGroupJsonLd, v4VariantPath } from '@/app/lib/structured-data';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * V4-VARIANT-URL — una dirección por variante: `/v4/modelo/<slug>?variante=<slug de la variante>`.
 *
 * Google pide una URL por variante para sus resultados de variantes, y quien
 * compra quiere compartir una configuración concreta. Aquí, lo que hace el
 * SERVIDOR:
 *
 *  · el primer HTML ya muestra la variante pedida (precio, opción, foto);
 *  · un valor desconocido, de otro modelo o de un seminuevo se ignora: la
 *    elección de siempre, sin error;
 *  · el valor sólo se compara con los slugs de las variantes del modelo: nunca
 *    llega al HTML ni a una petición;
 *  · la canónica sigue siendo la del modelo, sin el parámetro, y el
 *    noindex de la vista previa no cambia;
 *  · en el JSON-LD cada variante nueva apunta a su propia dirección.
 */

jest.mock('react', () => {
  const actual = jest.requireActual('react');
  const memos: Map<string, unknown>[] = [];
  (globalThis as { __variantUrlMemos?: Map<string, unknown>[] }).__variantUrlMemos = memos;
  return {
    ...actual,
    cache: <A extends unknown[], R>(fn: (...args: A) => R) => {
      const memo = new Map<string, R>();
      memos.push(memo as Map<string, unknown>);
      return (...args: A) => {
        const key = JSON.stringify(args);
        if (!memo.has(key)) memo.set(key, fn(...args));
        return memo.get(key) as R;
      };
    },
  };
});

let headerValues: Record<string, string> = {};
jest.mock('next/headers', () => ({
  headers: async () => ({ get: (name: string) => headerValues[name.toLowerCase()] ?? null }),
}));

const ORIGIN = 'https://tienda.example';
const fetchMock = jest.fn();

/** The API of a shop in `design` with `model` at its own slug. */
function shop(design: string, model: Record<string, unknown>) {
  fetchMock.mockImplementation(async (url: string) => {
    const u = String(url);
    if (u.includes('/storefront/config')) return reply({ page: { storefront_design: design } });
    if (u.includes(`/catalog/models/${model.slug}`)) return reply(model);
    return reply({}, 404);
  });
}

beforeEach(() => {
  for (const memo of (globalThis as { __variantUrlMemos?: Map<string, unknown>[] }).__variantUrlMemos ?? []) memo.clear();
  headerValues = { host: 'tienda.example', 'x-forwarded-proto': 'https' };
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

async function serverHtml(slug: string, search: Record<string, string | string[]> = {}) {
  const { default: Page } = await import('@/app/v4/modelo/[slug]/page');
  const element = await Page({ params: Promise.resolve({ slug }), searchParams: Promise.resolve(search) });
  return renderToString(<StorefrontProvider config={NEUTRAL_CONFIG}>{element}</StorefrontProvider>)
    .replace(/<!-- -->/g, '');
}

const priceOf = (html: string) => html.match(/data-testid="v4-price"[^>]*>([^<]*)</)?.[1];
const radioChecked = (html: string, label: string) =>
  new RegExp(`role="radio" aria-checked="true"[^>]*>(?:<span[^>]*></span>)?${label}<`).test(html);

describe('el primer HTML con ?variante=', () => {
  it('muestra la variante pedida: su precio, sus opciones y su foto', async () => {
    shop('v3', { ...modelFixture(), slug: 'var-a' });
    const html = await serverHtml('var-a', { variante: 'equipo-x-45' });
    expect(priceOf(html)).toBe('S/ 5499.00');
    expect(html).toContain('Equipo X 45');
    expect(html).toContain('http://localhost/rojo-512.png');
    expect(radioChecked(html, '512 GB')).toBe(true);
    expect(radioChecked(html, 'Rojo')).toBe(true);
    expect(html).not.toContain('Cargando modelo');
  });

  it('una variante agotada también se puede preseleccionar: dice «Agotado»', async () => {
    shop('v3', { ...modelFixture(), slug: 'var-b' });
    const html = await serverHtml('var-b', { variante: 'equipo-x-44' });
    expect(priceOf(html)).toBe('S/ 4499.00');
    expect(html).toContain('Equipo X 44');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*class="v4-cta[^"]*"[^>]*>Agotado</);
  });

  it('sin ?variante, la elección de siempre', async () => {
    shop('v3', { ...modelFixture(), slug: 'var-c' });
    const html = await serverHtml('var-c');
    expect(priceOf(html)).toBe('S/ 3999.00');
    expect(html).toContain('Equipo X 41');
  });

  it.each([
    ['desconocido', 'no-existe'],
    ['de otro modelo', 'equipo-y-45'],
    ['un equipo seminuevo (su id)', '0b4c7a52-1111-4a6e-9b1c-000000000001'],
    ['un equipo seminuevo (su producto)', '52'],
    ['vacío', ''],
    ['en otra caja', 'EQUIPO-X-45'],
  ])('un valor %s se ignora: la elección de siempre, sin error', async (_what, value) => {
    shop('v3', { ...modelFixture(), slug: 'var-d' });
    const html = await serverHtml('var-d', { variante: value });
    expect(priceOf(html)).toBe('S/ 3999.00');
    expect(html).toContain('Equipo X 41');
    expect(html).not.toMatch(/role="alert"/);
  });

  it('repetido (?variante=a&variante=b) se ignora', async () => {
    shop('v3', { ...modelFixture(), slug: 'var-e' });
    const html = await serverHtml('var-e', { variante: ['equipo-x-45', 'equipo-x-43'] });
    expect(priceOf(html)).toBe('S/ 3999.00');
  });

  it('con ?line=used manda la línea pedida; la variante espera en la línea de nuevos', async () => {
    shop('v3', { ...modelFixture(), slug: 'var-f' });
    const html = await serverHtml('var-f', { line: 'used', variante: 'equipo-x-45' });
    expect(priceOf(html)).toBe('S/ 2599.00');
  });
});

describe('seguridad del parámetro', () => {
  it('nunca se refleja en el HTML, ni siquiera escapado, y nunca se usa en una petición', async () => {
    shop('v4', { ...modelFixture(), slug: 'var-x' });
    const value = '"><script>alert(1)</script>';
    const html = await serverHtml('var-x', { variante: value });
    expect(html).not.toContain('alert(1)');
    expect(html).not.toContain('script>alert');
    expect(html).not.toContain(encodeURIComponent(value));
    expect(priceOf(html)).toBe('S/ 3999.00');
    for (const [url] of fetchMock.mock.calls) {
      expect(String(url)).not.toContain('alert');
      expect(String(url)).not.toContain('variante');
    }
  });

  it('un valor válido tampoco se usa en una petición: sólo se compara', async () => {
    shop('v4', { ...modelFixture(), slug: 'var-y' });
    await serverHtml('var-y', { variante: 'equipo-x-45' });
    for (const [url] of fetchMock.mock.calls) expect(String(url)).not.toContain('equipo-x-45');
  });
});

describe('canónica e indexación', () => {
  it('con V4, la canónica de ?variante= es la del modelo, sin el parámetro', async () => {
    shop('v4', { ...modelFixture(), slug: 'canon-v4' });
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({
      params: Promise.resolve({ slug: 'canon-v4' }),
      searchParams: Promise.resolve({ variante: 'equipo-x-45' }),
    } as never);
    expect(meta.alternates).toEqual({ canonical: `${ORIGIN}/v4/modelo/canon-v4` });
    expect(meta.robots).toEqual({ index: true, follow: true });
    expect(meta.openGraph).toMatchObject({ url: `${ORIGIN}/v4/modelo/canon-v4` });
    expect(JSON.stringify(meta)).not.toContain('variante');
  });

  it('con V3, ?variante= sigue sin indexar y sin canónica', async () => {
    shop('v3', { ...modelFixture(), slug: 'canon-v3' });
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({
      params: Promise.resolve({ slug: 'canon-v3' }),
      searchParams: Promise.resolve({ variante: 'equipo-x-45' }),
    } as never);
    expect(meta.robots).toEqual({ index: false, follow: false });
    expect(meta.alternates).toBeUndefined();
  });
});

describe('JSON-LD: una dirección por variante', () => {
  type Json = Record<string, unknown>;
  const jsonLd = (html: string) => [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)]
    .flatMap((m) => [JSON.parse(m[1])].flat()) as Json[];

  it('v4VariantPath: la dirección del modelo con ?variante= escapado', () => {
    expect(v4VariantPath('equipo-x', 'equipo-x-45')).toBe('/v4/modelo/equipo-x?variante=equipo-x-45');
    expect(v4VariantPath('a b', 'ñ&x=1')).toBe('/v4/modelo/a%20b?variante=%C3%B1%26x%3D1');
  });

  it('el Product y la Offer de cada variante nueva apuntan a su dirección absoluta; el grupo, al modelo', () => {
    const group = modelGroupJsonLd(modelFixture() as unknown as CatalogModel, ORIGIN);
    expect(group.url).toBe(`${ORIGIN}/v4/modelo/equipo-x`);
    const variants = group.hasVariant as Json[];
    expect(variants).toHaveLength(5);
    for (const variant of variants) {
      const expected = `${ORIGIN}/v4/modelo/equipo-x?variante=${variant.sku}`;
      expect(variant.url).toBe(expected);
      expect((variant.offers as Json).url).toBe(expected);
    }
  });

  it('sin origen, ninguna dirección relativa', () => {
    const group = modelGroupJsonLd(modelFixture() as unknown as CatalogModel, null);
    expect(JSON.stringify(group)).not.toMatch(/"url"/);
  });

  it('en la página con V4, desde el host de la petición', async () => {
    headerValues = { host: 'otra.example', 'x-forwarded-proto': 'https' };
    shop('v4', { ...modelFixture(), slug: 'ld-var' });
    const group = jsonLd(await serverHtml('ld-var', { variante: 'equipo-x-45' }))
      .find((block) => block['@type'] === 'ProductGroup')!;
    expect(group.url).toBe('https://otra.example/v4/modelo/ld-var');
    const urls = (group.hasVariant as Json[]).map((variant) => variant.url);
    expect(urls).toEqual([41, 42, 43, 44, 45].map((id) => `https://otra.example/v4/modelo/ld-var?variante=equipo-x-${id}`));
  });
});
