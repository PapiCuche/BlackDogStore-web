/**
 * @jest-environment node
 */
import { renderToString } from 'react-dom/server';

/**
 * SEO-AUDIT #4 y #18 — la ficha de producto `/product/<slug>`.
 *
 *  · «no existe» (la API responde y la lista viene vacía) es `notFound()` en la
 *    página y en la metadata. El 404 REAL lo da el proxy: ver
 *    `seo-proxy-404.test.ts` y su nota sobre `app/loading.tsx`;
 *  · «falló la lectura» (500, red caída) NO es «no existe»: la página muestra
 *    un aviso con «Reintentar» y la metadata dice noindex;
 *  · el slug va escapado en la consulta a la API (`?slug=a&b` añadía un
 *    parámetro a la llamada);
 *  · `generateMetadata` y la página leen el producto UNA vez por petición.
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

// `jest.setup.ts` turns `notFound()` into a no-op for the whole suite; these pages need Next's own, which throws.
jest.mock('next/navigation', () => ({
  ...jest.requireActual<object>('next/navigation'),
  notFound: jest.requireActual<typeof import('next/dist/client/components/not-found')>('next/dist/client/components/not-found').notFound,
}));

let headerValues: Record<string, string> = {};
jest.mock('next/headers', () => ({
  headers: async () => ({ get: (name: string) => headerValues[name.toLowerCase()] ?? null }),
}));

const NOT_FOUND = { digest: 'NEXT_HTTP_ERROR_FALLBACK;404' };
const PRODUCT = { id: 1, slug: 'funda-x', name: 'Funda X', price: '89.00', inventory: 3, description: '', image_url: '', images: [] };

function answer(body: unknown, status = 200) {
  return { ok: status < 300, status, json: async () => body } as unknown as Response;
}

const fetchMock = jest.fn();
beforeEach(() => {
  for (const memo of requestMemos) memo.clear();
  headerValues = { host: 'tienda.example', 'x-forwarded-proto': 'https' };
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

function serve(body: unknown, status = 200) {
  fetchMock.mockImplementation(async (url: string) => (String(url).includes('/products') ? answer(body, status) : answer({}, 404)));
}

async function page(slug: string) {
  const { default: Page } = await import('@/app/product/[slug]/page');
  return Page({ params: Promise.resolve({ slug }) });
}

async function metadata(slug: string) {
  const { generateMetadata } = await import('@/app/product/[slug]/page');
  return generateMetadata({ params: Promise.resolve({ slug }) });
}

const productCalls = () => fetchMock.mock.calls.filter(([url]) => String(url).includes('/products'));

describe('un producto que no existe', () => {
  it('la página llama a notFound()', async () => {
    serve([]);
    await expect(page('no-existe')).rejects.toMatchObject(NOT_FOUND);
  });

  it('la metadata también', async () => {
    serve([]);
    await expect(metadata('no-existe')).rejects.toMatchObject(NOT_FOUND);
  });
});

describe('si la lectura falla, no se dice «no encontrado»', () => {
  it.each([
    ['la API responde 500', () => serve({}, 500)],
    ['la red falla', () => fetchMock.mockRejectedValue(new Error('down'))],
    ['la API responde algo que no es una lista', () => serve({ detail: 'raro' })],
  ])('%s: aviso con «Reintentar» a la misma dirección', async (_label, arrange) => {
    arrange();
    const html = renderToString(await page('funda x'));
    expect(html).not.toMatch(/no encontrado/i);
    expect(html).toContain('role="alert"');
    expect(html).toMatch(/<a[^>]*href="\/product\/funda%20x"[^>]*>Reintentar<\/a>/);
  });

  it.each([
    ['la API responde 500', () => serve({}, 500)],
    ['la red falla', () => fetchMock.mockRejectedValue(new Error('down'))],
  ])('%s: la metadata pide noindex y no dice «no encontrado»', async (_label, arrange) => {
    arrange();
    const meta = await metadata('funda-x');
    expect(String(meta.title)).not.toMatch(/no encontrado/i);
    expect(meta.robots).toMatchObject({ index: false });
    expect(meta.alternates).toBeUndefined();
  });
});

describe('la consulta a la API (#18)', () => {
  it('el slug va escapado: no puede añadir parámetros a la llamada', async () => {
    serve([]);
    await expect(page('a&b=c')).rejects.toMatchObject(NOT_FOUND);
    const url = new URL(String(productCalls()[0][0]));
    expect(url.searchParams.getAll('slug')).toEqual(['a&b=c']);
    expect(url.searchParams.has('b')).toBe(false);
  });

  it('sin caché compartida entre hosts', async () => {
    serve([PRODUCT]);
    await metadata('funda-x');
    expect(productCalls()[0][1]).toMatchObject({ cache: 'no-store' });
  });

  it('generateMetadata y la página comparten UNA lectura', async () => {
    serve([PRODUCT]);
    const meta = await metadata('funda-x');
    await page('funda-x');
    expect(meta.title).toBe('Funda X');
    expect(productCalls()).toHaveLength(1);
  });
});
