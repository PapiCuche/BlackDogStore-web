/**
 * SEO-03 — cada página pública declara su URL canónica ABSOLUTA, hecha con el host de la petición (multiempresa:
 * nunca un dominio fijo). Sin origen válido no hay canónica: una relativa sin `metadataBase` acababa en
 * «http://localhost:3000/…» (revisión de SEO-03).
 */
import type { Metadata } from 'next';

let headerValues: Record<string, string> = {};
jest.mock('next/headers', () => ({
  headers: async () => ({ get: (name: string) => headerValues[name.toLowerCase()] ?? null }),
}));
jest.mock('@/app/lib/storefront', () => ({
  ...jest.requireActual('@/app/lib/storefront'),
  fetchStorefrontConfig: async () => ({ contact: { website_url: '' }, company: { name: '' }, branding: {} }),
}));

type Mod = { generateMetadata?: () => Promise<Metadata> };
/* eslint-disable @typescript-eslint/no-require-imports */
const pages: [string, () => Mod, string][] = [
  ['portada', () => require('@/app/page'), '/'],
  ['catálogo', () => require('@/app/product/page'), '/product'],
  ['contacto', () => require('@/app/contact/page'), '/contact'],
  ['nosotros', () => require('@/app/about/page'), '/about'],
  ['servicio técnico', () => require('@/app/services/page'), '/services'],
  ['privacidad', () => require('@/app/privacy/page'), '/privacy'],
  ['cookies', () => require('@/app/cookies/page'), '/cookies'],
  ['privacidad de WhatsApp', () => require('@/app/privacy/whatsapp/page'), '/privacy/whatsapp'],
];
/* eslint-enable @typescript-eslint/no-require-imports */

const canonicalOf = async (mod: Mod) => (await mod.generateMetadata!())?.alternates?.canonical;

describe('SEO-03 · canónicas de las páginas públicas', () => {
  it.each(pages)('%s: canónica absoluta del host de la petición', async (_label, load, path) => {
    headerValues = { host: 'tienda.example', 'x-forwarded-proto': 'https' };
    expect(await canonicalOf(load())).toBe(`https://tienda.example${path === '/' ? '/' : path}`);
  });

  it.each(pages)('%s: sin host válido no hay canónica (nunca localhost)', async (_label, load) => {
    headerValues = { host: 'evil.example/<script>' };
    expect(await canonicalOf(load())).toBeUndefined();
  });

  it('el catálogo tiene título propio (antes salía sólo el nombre de la tienda)', async () => {
    headerValues = { host: 'tienda.example' };
    const mod = pages[1][1]();
    expect((await mod.generateMetadata!()).title).toBe('Productos');
  });
});

describe('SEO-03 · layout raíz', () => {
  it('pone metadataBase del host aunque la empresa no tenga nombre (nada relativo cae en localhost)', async () => {
    headerValues = { host: 'tienda.example', 'x-forwarded-proto': 'https' };
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const layout = require('@/app/layout') as { generateMetadata: () => Promise<Metadata> };
    const meta = await layout.generateMetadata();
    expect(String(meta.metadataBase)).toBe('https://tienda.example/');
  });
});
