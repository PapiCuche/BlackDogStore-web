/**
 * @jest-environment node
 */
import { NextRequest } from 'next/server';

/**
 * P2 · F5a — el interruptor de diseño por empresa (`page.storefront_design`).
 *
 * Con `v3` (lo de siempre) NADA cambia: la portada y la ficha V3 se sirven
 * como hoy, `/v4` no se indexa, no tiene canónica y no entra en el sitemap.
 *
 * Con `v4`: `/` y `/modelo/<slug>` llevan a la V4 con una redirección
 * TEMPORAL (307) hecha en el proxy, antes de pintar nada; las páginas V4 se
 * indexan con canónica absoluta y el sitemap nombra las direcciones V4.
 * Carrito, pago, cuenta y panel siguen en V3 en los dos modos.
 */

let headerValues: Record<string, string> = {};
jest.mock('next/headers', () => ({
  headers: async () => ({ get: (name: string) => headerValues[name.toLowerCase()] ?? null }),
}));

const fetchMock = jest.fn();
const INDEX = { index: true, follow: true };
const NOINDEX = { index: false, follow: false };

function answer(body: unknown, status = 200) {
  return { ok: status < 300, status, json: async () => body };
}

/** The API of a shop in `design`, with one model and one product. */
function shop(design: unknown, model: Record<string, unknown> = { name: 'Equipo X', description: '**Rápido** y fiable' }) {
  fetchMock.mockImplementation(async (url: string) => {
    const u = String(url);
    if (u.includes('/storefront/config')) return answer({ page: { storefront_design: design }, contact: { website_url: '' } });
    if (u.includes('/catalog/models/equipo-x')) return answer(model);
    if (u.includes('/catalog/models/no-existe')) return answer({}, 404);
    if (u.includes('/catalog/models')) return answer({ results: [{ slug: 'iphone-15' }] });
    if (u.includes('/products')) return answer([{ slug: 'funda-roja' }]);
    return answer({}, 404);
  });
}

beforeEach(() => {
  headerValues = { host: 'tienda.example', 'x-forwarded-proto': 'https' };
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

// ---------------------------------------------------------------------------
// El valor
// ---------------------------------------------------------------------------

describe('designOf', () => {
  it('sólo «v4» es V4; todo lo demás, incluida la ausencia, es V3', async () => {
    const { designOf } = await import('@/app/lib/storefront-design');
    expect(designOf({ storefront_design: 'v4' })).toBe('v4');
    for (const value of ['v3', 'V4', 'v4 ', '', null, undefined, 4, {}]) {
      expect(designOf({ storefront_design: value })).toBe('v3');
    }
    expect(designOf(undefined)).toBe('v3');
    expect(designOf(null)).toBe('v3');
  });

  it('la configuración neutral (API caída) está en V3', async () => {
    const { NEUTRAL_CONFIG, EMPTY_PAGE } = await import('@/app/lib/storefront');
    expect(EMPTY_PAGE.storefront_design).toBe('v3');
    expect(NEUTRAL_CONFIG.page.storefront_design).toBe('v3');
  });
});

describe('v4Path', () => {
  it('sólo la portada y la ficha de modelo tienen equivalente V4', async () => {
    const { v4Path } = await import('@/app/lib/storefront-design');
    expect(v4Path('/')).toBe('/v4');
    expect(v4Path('/modelo/iphone-15')).toBe('/v4/modelo/iphone-15');
    for (const route of ['/cart', '/checkout', '/account', '/admin', '/product', '/product/funda', '/modelo',
      '/modelo/a/b', '/v4', '/v4/modelo/x', '/services', '/orders', '/auth']) {
      expect(v4Path(route)).toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// El proxy: la redirección
// ---------------------------------------------------------------------------

async function loadProxy() {
  let mod: typeof import('@/proxy') | undefined;
  await jest.isolateModulesAsync(async () => { mod = await import('@/proxy'); });
  return mod!;
}

function request(path: string) {
  return new NextRequest(`https://tienda.example${path}`, { headers: { host: 'tienda.example' } });
}

describe('proxy con V3', () => {
  it.each(['/', '/modelo/equipo-x', '/modelo/equipo-x?line=used'])('%s se sirve como hoy', async (path) => {
    shop('v3');
    const { proxy } = await loadProxy();
    const res = await proxy(request(path));
    expect(res.headers.get('location')).toBeNull();
    expect(res.status).toBe(200);
  });

  it('si la configuración no se puede leer, la tienda sigue en V3', async () => {
    fetchMock.mockRejectedValue(new Error('down'));
    const { proxy } = await loadProxy();
    expect((await proxy(request('/'))).headers.get('location')).toBeNull();
    fetchMock.mockResolvedValue(answer({}, 500));
    expect((await proxy(request('/modelo/equipo-x'))).headers.get('location')).toBeNull();
  });
});

describe('proxy con V4', () => {
  it('la portada lleva a /v4 con un 307 que nadie guarda', async () => {
    shop('v4');
    const { proxy } = await loadProxy();
    const res = await proxy(request('/'));
    expect(res.status).toBe(307);
    expect(new URL(res.headers.get('location')!).pathname).toBe('/v4');
    expect(res.headers.get('cache-control')).toBe('no-store');
  });

  it('la ficha lleva a /v4/modelo/<slug> y conserva la consulta', async () => {
    shop('v4');
    const { proxy } = await loadProxy();
    const res = await proxy(request('/modelo/equipo-x?line=used&utm_source=ig'));
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get('location')!);
    expect(location.pathname).toBe('/v4/modelo/equipo-x');
    expect(location.searchParams.get('line')).toBe('used');
    expect(location.searchParams.get('utm_source')).toBe('ig');
    expect(location.host).toBe('tienda.example');
  });

  it.each(['/cart', '/checkout', '/account', '/admin', '/product/funda-roja', '/v4', '/v4/modelo/equipo-x'])(
    '%s no se redirige', async (path) => {
      shop('v4');
      const { proxy } = await loadProxy();
      expect((await proxy(request(path))).headers.get('location')).toBeNull();
    },
  );

  it('el proxy sólo corre en la portada y la ficha, y nunca en las precargas', async () => {
    const { config } = await loadProxy();
    const sources = config.matcher.map((entry: { source: string }) => entry.source);
    expect(sources).toEqual(['/', '/modelo/:slug']);
    for (const entry of config.matcher as { missing?: { type: string; key: string }[] }[]) {
      expect(entry.missing).toEqual([{ type: 'header', key: 'next-router-prefetch' }]);
    }
  });
});

describe('volver a V3', () => {
  it('se lee de nuevo pasada la caché corta del proxy', async () => {
    const { DESIGN_CACHE_TTL_MS, createDesignReader } = await import('@/app/lib/storefront-design');
    let now = 1_000;
    let design: 'v3' | 'v4' | null = 'v4';
    const read = jest.fn(async () => design);
    const reader = createDesignReader({ read, now: () => now });

    expect(await reader('tienda.example')).toBe('v4');
    design = 'v3';
    now += DESIGN_CACHE_TTL_MS - 1;
    expect(await reader('tienda.example')).toBe('v4');
    now += 1;
    expect(await reader('tienda.example')).toBe('v3');
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('la caché es por host y una lectura fallida no se guarda', async () => {
    const { createDesignReader } = await import('@/app/lib/storefront-design');
    const answers: ('v3' | 'v4' | null)[] = [null, 'v4', 'v3'];
    let call = 0;
    const read = jest.fn(async () => answers[call++]);
    const reader = createDesignReader({ read, now: () => 0 });
    expect(await reader('a.example')).toBe('v3');
    expect(await reader('a.example')).toBe('v4');
    expect(await reader('b.example')).toBe('v3');
    expect(read).toHaveBeenCalledTimes(3);
  });

  it('la caché dura como mucho 15 segundos', async () => {
    const { DESIGN_CACHE_TTL_MS } = await import('@/app/lib/storefront-design');
    expect(DESIGN_CACHE_TTL_MS).toBeLessThanOrEqual(15_000);
  });
});

// ---------------------------------------------------------------------------
// Indexación y canónicas de la V4
// ---------------------------------------------------------------------------

describe('/v4 con V3: sin cambios', () => {
  it('el layout sigue sin indexar', async () => {
    const { metadata } = await import('@/app/v4/layout');
    expect(metadata.robots).toEqual(NOINDEX);
    expect(metadata.alternates).toBeUndefined();
  });

  it('la portada V4 sigue siendo una vista previa sin indexar ni canónica', async () => {
    shop('v3');
    const { generateMetadata } = await import('@/app/v4/page');
    const meta = await generateMetadata();
    expect(meta).toEqual({ title: 'Vista previa', robots: NOINDEX });
  });

  it('la ficha V4 sigue sin indexar ni canónica', async () => {
    shop('v3');
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'equipo-x' }) } as never);
    expect(meta).toEqual({ title: 'Equipo X', robots: NOINDEX });
  });
});

describe('/v4 con V4: se indexa con canónica absoluta', () => {
  it('la portada V4', async () => {
    shop('v4');
    const { generateMetadata } = await import('@/app/v4/page');
    const meta = await generateMetadata();
    expect(meta.robots).toEqual(INDEX);
    expect(meta.alternates).toEqual({ canonical: 'https://tienda.example/v4' });
    expect(meta.title).toBeUndefined();
  });

  it('la ficha V4', async () => {
    shop('v4');
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'equipo-x' }) } as never);
    expect(meta.robots).toEqual(INDEX);
    expect(meta.alternates).toEqual({ canonical: 'https://tienda.example/v4/modelo/equipo-x' });
    expect(meta.title).toBe('Equipo X');
    // El texto, nunca las marcas de Markdown (como la ficha V3).
    expect(meta.description).toBe('Rápido y fiable');
  });

  it('un modelo que no existe no se indexa', async () => {
    shop('v4');
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'no-existe' }) } as never);
    expect(meta.robots).toEqual({ index: false, follow: true });
    expect(meta.alternates).toBeUndefined();
  });

  it('sin un host válido no hay canónica (nunca una relativa)', async () => {
    shop('v4');
    headerValues = { host: 'evil.example/<x>' };
    const { generateMetadata } = await import('@/app/v4/page');
    const meta = await generateMetadata();
    expect(meta.alternates).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Sitemap
// ---------------------------------------------------------------------------

describe('sitemap', () => {
  async function urls() {
    const { default: sitemap } = await import('@/app/sitemap');
    return (await sitemap()).map((entry: { url: string }) => entry.url);
  }

  it('con V3 no nombra nada bajo /v4', async () => {
    shop('v3');
    const list = await urls();
    expect(list).toContain('https://tienda.example');
    expect(list).toContain('https://tienda.example/modelo/iphone-15');
    expect(list.some((url) => /\/v4(\/|$)/.test(url))).toBe(false);
  });

  it('con V4 nombra la portada y las fichas V4 en lugar de las que redirigen', async () => {
    shop('v4');
    const list = await urls();
    expect(list).toContain('https://tienda.example/v4');
    expect(list).toContain('https://tienda.example/v4/modelo/iphone-15');
    expect(list).not.toContain('https://tienda.example');
    expect(list).not.toContain('https://tienda.example/modelo/iphone-15');
    // Lo demás sigue en V3, como siempre.
    expect(list).toContain('https://tienda.example/product');
    expect(list).toContain('https://tienda.example/product/funda-roja');
    expect(list).toContain('https://tienda.example/services');
  });
});
