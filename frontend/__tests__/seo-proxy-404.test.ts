/**
 * @jest-environment node
 */
import { NextRequest } from 'next/server';

/**
 * SEO-AUDIT #4 — un 404 de verdad para `/modelo/<slug>` y `/product/<slug>` que no existen.
 *
 * POR QUÉ EN EL PROXY Y NO EN LA PÁGINA. `app/loading.tsx` está en la RAÍZ: envuelve cada página en un
 * `<Suspense>`. En cuanto la página espera a la API, React pinta el esqueleto y Next empieza a enviar el
 * HTML con un 200; un `notFound()` posterior ya no puede cambiar el estado (sólo añade un
 * `<meta name="robots" content="noindex">`). Lo mismo si el `notFound()` sale de `generateMetadata`: para los
 * navegadores y Googlebot la metadata también va en streaming. Comprobado con `next build` + `next start`
 * (Next 16.3.8) contra una API de prueba, 2026-11-18:
 *
 *   notFound() en la página y en generateMetadata  → 200 con Chrome, Googlebot y Bingbot
 *   lo mismo, sin app/loading.tsx                   → 404
 *   proxy que reescribe a una ruta inexistente       → 404 con app/not-found.tsx
 *
 * Quitar `loading.tsx` de la raíz cambiaría la espera de TODAS las páginas de la V3, así que la decisión se
 * toma antes de pintar nada, en el proxy (lo que recomienda la guía de Next: «run this check in proxy to
 * rewrite missing slugs to a not-found route»). La página sigue llamando a `notFound()` para lo que el proxy
 * no ve (navegación del cliente, o el modelo se despublicó entre las dos lecturas): ahí Next añade el noindex.
 *
 * Sólo «la API dice que no existe» da 404. Una API caída o lenta deja pasar la petición: la página decide.
 * Un «sí existe» se recuerda poco tiempo (una lectura menos por visita); un «no existe» nunca se recuerda,
 * para que un modelo recién publicado no siga dando 404.
 */

const fetchMock = jest.fn();

function answer(body: unknown, status = 200) {
  return { ok: status < 300, status, json: async () => body, body: null } as unknown as Response;
}

/** A shop in V3 (or `design`) with model `equipo-x` and product `funda-roja`. */
function shop(design = 'v3') {
  fetchMock.mockImplementation(async (url: string) => {
    const u = new URL(String(url));
    if (u.pathname.includes('/storefront/config')) return answer({ page: { storefront_design: design } });
    if (u.pathname.endsWith('/catalog/models/equipo-x')) return answer({ slug: 'equipo-x', name: 'Equipo X' });
    if (u.pathname.endsWith('/catalog/models/caido')) return answer({}, 500);
    if (u.pathname.includes('/catalog/models/')) return answer({ detail: 'No encontrado.' }, 404);
    if (u.pathname.endsWith('/products')) {
      const slug = u.searchParams.get('slug');
      if (slug === 'caido') return answer({}, 503);
      return answer(slug === 'funda-roja' || slug === 'a&b' ? [{ slug }] : []);
    }
    return answer({}, 404);
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

async function loadProxy() {
  let mod: typeof import('@/proxy') | undefined;
  await jest.isolateModulesAsync(async () => { mod = await import('@/proxy'); });
  return mod!;
}

function request(path: string, headers: Record<string, string> = {}, method = 'GET') {
  return new NextRequest(`https://tienda.example${path}`, { method, headers: { host: 'tienda.example', ...headers } });
}

const rewrittenTo = (res: Response) => {
  const target = res.headers.get('x-middleware-rewrite');
  return target ? new URL(target).pathname : null;
};

const existenceCalls = () => fetchMock.mock.calls.filter(([url]) => !String(url).includes('/storefront/config'));

describe('proxy: un 404 real cuando la API dice que no existe', () => {
  it.each(['/modelo/no-existe', '/product/no-existe'])('%s → 404 con la página «no encontrada»', async (path) => {
    shop();
    const { proxy } = await loadProxy();
    const res = await proxy(request(path));
    expect(res.status).toBe(404);
    // Una ruta que ninguna página atiende: Next pinta app/not-found.tsx (con su título y noindex).
    const target = rewrittenTo(res)!;
    expect(target).toMatch(/^\/_/);
    expect(target).not.toMatch(/^\/(modelo|product)\b/);
  });

  it.each(['/modelo/equipo-x', '/modelo/equipo-x?line=used', '/product/funda-roja'])('%s existe y sigue como hoy', async (path) => {
    shop();
    const { proxy } = await loadProxy();
    const res = await proxy(request(path));
    expect(res.status).toBe(200);
    expect(rewrittenTo(res)).toBeNull();
    expect(res.headers.get('location')).toBeNull();
  });

  it.each(['/modelo/caido', '/product/caido'])('%s: con la API fallando NO se dice «no existe»', async (path) => {
    shop();
    const { proxy } = await loadProxy();
    const res = await proxy(request(path));
    expect(res.status).toBe(200);
    expect(rewrittenTo(res)).toBeNull();
  });

  it('con la red caída tampoco', async () => {
    fetchMock.mockRejectedValue(new Error('down'));
    const { proxy } = await loadProxy();
    for (const path of ['/modelo/x', '/product/x']) {
      const res = await proxy(request(path));
      expect(res.status).toBe(200);
      expect(rewrittenTo(res)).toBeNull();
    }
  });

  it('pregunta a la API con el slug escapado (nunca añade parámetros)', async () => {
    shop();
    const { proxy } = await loadProxy();
    const res = await proxy(request('/product/a%26b'));
    expect(res.status).toBe(200);
    const url = new URL(String(existenceCalls()[0][0]));
    expect(url.searchParams.getAll('slug')).toEqual(['a&b']);
    const [, init] = existenceCalls()[0];
    expect(init).toMatchObject({ cache: 'no-store' });
    expect(init.signal).toBeDefined();
  });

  it('un slug mal escapado no rompe el proxy', async () => {
    shop();
    const { proxy } = await loadProxy();
    const res = await proxy(request('/product/%E0%A4%A'));
    expect(res.status).toBe(200);
  });

  it('las navegaciones del cliente (RSC) no se consultan: la página decide', async () => {
    shop();
    const { proxy } = await loadProxy();
    const res = await proxy(request('/product/no-existe', { rsc: '1' }));
    expect(res.status).toBe(200);
    expect(existenceCalls()).toHaveLength(0);
  });

  it('sólo GET y HEAD se consultan', async () => {
    shop();
    const { proxy } = await loadProxy();
    expect((await proxy(request('/product/no-existe', {}, 'HEAD'))).status).toBe(404);
    fetchMock.mockClear();
    expect((await proxy(request('/product/no-existe', {}, 'POST'))).status).toBe(200);
    expect(existenceCalls()).toHaveLength(0);
  });

  it('un «sí existe» se recuerda poco tiempo; un «no existe» nunca', async () => {
    shop();
    const { proxy } = await loadProxy();
    await proxy(request('/modelo/equipo-x'));
    await proxy(request('/modelo/equipo-x'));
    expect(existenceCalls()).toHaveLength(1);
    fetchMock.mockClear();
    await proxy(request('/modelo/no-existe'));
    await proxy(request('/modelo/no-existe'));
    expect(existenceCalls()).toHaveLength(2);
  });

  it('con la tienda en V4 la ficha de modelo sigue yendo a /v4 (la V4 decide su propio «no existe»)', async () => {
    shop('v4');
    const { proxy } = await loadProxy();
    const res = await proxy(request('/modelo/no-existe'));
    expect(res.status).toBe(307);
    expect(new URL(res.headers.get('location')!).pathname).toBe('/v4/modelo/no-existe');
  });

  it('el proxy corre también en /product/<slug>, nunca en las precargas', async () => {
    const { config } = await loadProxy();
    const sources = config.matcher.map((entry: { source: string }) => entry.source);
    expect(sources).toEqual(['/', '/modelo/:slug', '/product/:slug']);
    for (const entry of config.matcher as { missing?: { type: string; key: string }[] }[]) {
      expect(entry.missing).toEqual([{ type: 'header', key: 'next-router-prefetch' }]);
    }
  });
});
