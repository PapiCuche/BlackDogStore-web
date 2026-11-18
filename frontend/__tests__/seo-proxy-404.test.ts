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

/**
 * Review of SEO-AUDIT #4 — the proxy's API reads do NOT carry the visitor's host: Django sees the internal address
 * and resolves the deployment's fallback tenant (`DEFAULT_STOREFRONT_COMPANY_SLUG`, or the only active company). It
 * resolves a tenant by host only for a tenant subdomain (`store/tenancy.py · resolve_company_from_host`: three labels
 * or more, first label not www/api/admin/app). On such a host the answer would be about ANOTHER company, so the proxy
 * does not ask, and the request goes on as before (the page's own notFound(): 200 + noindex).
 */
describe('proxy: sólo pregunta cuando la API resuelve la misma empresa que el host', () => {
  const visit = (host: string, path = '/product/no-existe') =>
    new NextRequest(`https://${host}${path}`, { headers: { host } });

  it.each(['tienda-b.plataforma.example', 'tienda-b.plataforma.example:443', 'TIENDA-B.Plataforma.example'])(
    '%s (subdominio de empresa): no se consulta y la petición sigue', async (host) => {
      shop();
      const { proxy } = await loadProxy();
      for (const path of ['/product/no-existe', '/modelo/no-existe']) {
        const res = await proxy(visit(host, path));
        expect(res.status).toBe(200);
        expect(rewrittenTo(res)).toBeNull();
      }
      expect(existenceCalls()).toHaveLength(0);
    },
  );

  it.each(['tienda.example', 'www.tienda.example', 'localhost:3002', 'api.tienda.example'])(
    '%s (sin subdominio de empresa): 404 real', async (host) => {
      shop();
      const { proxy } = await loadProxy();
      expect((await proxy(visit(host))).status).toBe(404);
    },
  );
});

describe('proxy: con la API colgada, la ficha de modelo no espera dos veces', () => {
  it('la lectura del diseño y la de existencia salen a la vez', async () => {
    const pending: { url: string; resolve: (value: Response) => void }[] = [];
    fetchMock.mockImplementation((url: string) => new Promise<Response>((resolve) => { pending.push({ url: String(url), resolve }); }));
    const { proxy } = await loadProxy();
    const answered = proxy(request('/modelo/no-existe'));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(pending.map((call) => new URL(call.url).pathname)).toEqual(
      expect.arrayContaining([expect.stringMatching(/\/storefront\/config/), expect.stringMatching(/\/catalog\/models\/no-existe/)]),
    );
    for (const call of pending) {
      call.resolve(call.url.includes('/storefront/config') ? answer({ page: { storefront_design: 'v3' } }) : answer({}, 404));
    }
    expect((await answered).status).toBe(404);
  });

  it('las dos lecturas llevan su tope de tiempo', async () => {
    shop();
    const { proxy } = await loadProxy();
    await proxy(request('/modelo/equipo-x'));
    expect(fetchMock.mock.calls).toHaveLength(2);
    for (const [, init] of fetchMock.mock.calls) expect(init.signal).toBeDefined();
  });
});

/**
 * `e2e/device-commerce.spec.ts` serves a made-up model to the BROWSER (`page.route`). The server cannot see those
 * mocks, so for the slugs named by `E2E_MOCKED_MODEL_PREFIX` — and only outside a production build — «missing» is
 * treated as «not read»: no 404, the browser reads (and gets the mock).
 */
describe('E2E_MOCKED_MODEL_PREFIX (sólo fuera de producción)', () => {
  const env = process.env as Record<string, string | undefined>;
  const nodeEnv = env.NODE_ENV;
  afterEach(() => {
    env.NODE_ENV = nodeEnv;
    delete env.E2E_MOCKED_MODEL_PREFIX;
  });

  it('en desarrollo, un modelo con ese prefijo no da 404 ni se consulta', async () => {
    env.NODE_ENV = 'development';
    env.E2E_MOCKED_MODEL_PREFIX = 'equipo-e2e-';
    shop();
    const { proxy } = await loadProxy();
    expect((await proxy(request('/modelo/equipo-e2e-abc'))).status).toBe(200);
    expect(existenceCalls()).toHaveLength(0);
    // Sólo modelos y sólo ese prefijo.
    expect((await proxy(request('/modelo/no-existe'))).status).toBe(404);
    expect((await proxy(request('/product/equipo-e2e-abc'))).status).toBe(404);
  });

  it('en un build de producción no se puede encender', async () => {
    env.NODE_ENV = 'production';
    env.E2E_MOCKED_MODEL_PREFIX = 'equipo-e2e-';
    shop();
    const { proxy } = await loadProxy();
    expect((await proxy(request('/modelo/equipo-e2e-abc'))).status).toBe(404);
  });

  it('un prefijo que no nombra e2e no vale (nunca «todos los modelos»)', async () => {
    env.NODE_ENV = 'development';
    shop();
    const { proxy } = await loadProxy();
    for (const prefix of ['', 'equipo-', 'no-']) {
      env.E2E_MOCKED_MODEL_PREFIX = prefix;
      expect((await proxy(request('/modelo/no-existe'))).status).toBe(404);
    }
  });
});
