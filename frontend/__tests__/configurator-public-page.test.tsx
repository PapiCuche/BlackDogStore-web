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

const fetchMock = jest.fn();
beforeEach(() => {
  for (const memo of requestMemos) memo.clear();
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) =>
    String(url).includes('/catalog/models/') ? reply({ ...modelFixture(), slug: 'equipo-x' }) : reply({}, 404));
  global.fetch = fetchMock as unknown as typeof fetch;
});

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

  it('una variante desconocida es la elección de siempre, sin error', async () => {
    const markup = await html({ variante: '<script>x</script>' });
    expect(checked(markup, '128 GB')).toBe(true);
    expect(markup).not.toContain('<script>x');
  });

  it('la canónica es la del modelo, sin la variante', async () => {
    const { generateMetadata } = await import('@/app/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'equipo-x' }) });
    expect(String(meta.alternates?.canonical)).toMatch(/\/modelo\/equipo-x$/);
  });
});
