/**
 * SEO-01 — robots.txt, sitemap.xml y el origen del sitio salen del host de la petición (multiempresa: nunca un
 * dominio fijo), y las rutas privadas nunca se indexan.
 */
import robots from '@/app/robots';
import sitemap from '@/app/sitemap';
import { PRIVATE_ROUTES, siteOrigin } from '@/app/lib/site-origin';

let headerValues: Record<string, string> = {};
jest.mock('next/headers', () => ({
  headers: async () => ({ get: (name: string) => headerValues[name.toLowerCase()] ?? null }),
}));
jest.mock('@/app/lib/storefront', () => ({
  fetchStorefrontConfig: async () => ({ contact: { website_url: '' }, company: { name: 'Tienda' } }),
}));

const fetchMock = jest.fn();
beforeEach(() => {
  headerValues = { host: 'tienda.example', 'x-forwarded-proto': 'https' };
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

describe('siteOrigin', () => {
  it('usa el host de la petición', async () => {
    expect(await siteOrigin()).toBe('https://tienda.example');
  });

  it('rechaza un host que no es un nombre limpio y cae en website_url', async () => {
    headerValues = { host: 'evil.example/<script>' };
    expect(await siteOrigin('https://blackdog.example/x')).toBe('https://blackdog.example');
    expect(await siteOrigin()).toBeNull();
  });

  it('localhost va por http salvo que el proxy diga https', async () => {
    headerValues = { host: 'localhost:3000' };
    expect(await siteOrigin()).toBe('http://localhost:3000');
  });
});

describe('robots.txt', () => {
  it('abre la tienda, cierra las rutas privadas y nombra el sitemap del host', async () => {
    const result = await robots();
    const rule = Array.isArray(result.rules) ? result.rules[0] : result.rules;
    expect(rule.allow).toBe('/');
    for (const route of PRIVATE_ROUTES) expect(rule.disallow).toContain(route);
    expect(result.sitemap).toBe('https://tienda.example/sitemap.xml');
  });
});

describe('sitemap.xml', () => {
  it('lista páginas públicas, modelos y productos publicados del host, y nada privado', async () => {
    fetchMock.mockImplementation(async (url: string) => ({
      ok: true,
      json: async () => (url.includes('/catalog/models')
        ? { results: [{ slug: 'iphone-15' }, { slug: '../malo' }] }
        : [{ slug: 'funda-roja' }, { slug: null }]),
    }));
    const urls = (await sitemap()).map((entry) => entry.url);
    expect(urls).toContain('https://tienda.example');
    expect(urls).toContain('https://tienda.example/privacy');
    expect(urls).toContain('https://tienda.example/modelo/iphone-15');
    expect(urls).toContain('https://tienda.example/product/funda-roja');
    expect(urls.some((url) => /malo|admin|cart|checkout/.test(url))).toBe(false);
  });

  it('si la API falla, el sitemap sigue con las páginas públicas', async () => {
    fetchMock.mockRejectedValue(new Error('down'));
    const urls = (await sitemap()).map((entry) => entry.url);
    expect(urls).toContain('https://tienda.example/product');
    expect(urls.some((url) => url.includes('/product/'))).toBe(false);
  });
});
