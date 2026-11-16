import { render, screen } from '@testing-library/react';
import fs from 'fs';
import path from 'path';

/**
 * P2 · F1 — la ruta de vista previa `/v4`.
 *
 * La V4 se ve sólo si alguien entra a `/v4/...`: no se indexa, no entra en el
 * sitemap y la V3 no cambia. Bajo `/v4` la cabecera y el pie de la V3 no se
 * dibujan (la V4 lleva su propia franja), como ya pasaba en `/admin`.
 */

let pathname = '/';
jest.mock('next/navigation', () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock('@/app/components/Header', () => ({ Header: () => <header data-testid="v3-header" /> }));
jest.mock('@/app/components/Footer', () => ({ Footer: () => <footer data-testid="v3-footer" /> }));

let headerValues: Record<string, string> = {};
jest.mock('next/headers', () => ({
  headers: async () => ({ get: (name: string) => headerValues[name.toLowerCase()] ?? null }),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const { StorefrontContent, StorefrontFooter, StorefrontHeader, SkipLink, WhatsAppButton } = require('@/app/components/StorefrontChrome');
const { StorefrontProvider } = require('@/app/components/StorefrontProvider');
const { NEUTRAL_CONFIG } = require('@/app/lib/storefront');
/* eslint-enable @typescript-eslint/no-require-imports */

const fetchMock = jest.fn();
beforeEach(() => {
  pathname = '/';
  headerValues = { host: 'tienda.example', 'x-forwarded-proto': 'https' };
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

describe('noindex', () => {
  it('el layout de /v4 pide no indexar ni seguir', async () => {
    const { metadata } = await import('@/app/v4/layout');
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });

  it('la ficha V4 también, aunque el modelo exista', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ name: 'Equipo X', description: '' }) });
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'equipo-x' }) } as never);
    expect(meta.robots).toEqual({ index: false, follow: false });
    expect(meta.title).toBe('Equipo X');
  });

  it('la ficha V4 sigue sin indexarse si la API falla', async () => {
    fetchMock.mockRejectedValue(new Error('down'));
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'equipo-x' }) } as never);
    expect(meta.robots).toEqual({ index: false, follow: false });
  });
});

describe('sin canónica', () => {
  // SEO-03 trajo canónicas absolutas a las páginas públicas (`lib/canonical.ts`). La vista previa no es una
  // página pública: no declara canónica, ni propia ni hacia la V3.
  it('ni el layout ni la ficha V4 declaran canónica', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ name: 'Equipo X', description: '' }) });
    const { metadata } = await import('@/app/v4/layout');
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'equipo-x' }) } as never);
    expect(metadata.alternates).toBeUndefined();
    expect(meta.alternates).toBeUndefined();
  });

  it('nada bajo app/v4 usa el ayudante de canónicas', () => {
    const walk = (dir: string): string[] => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
      entry.isDirectory() ? walk(path.join(dir, entry.name)) : [path.join(dir, entry.name)]);
    for (const file of walk(path.join(process.cwd(), 'app/v4'))) {
      expect(`${file}: ${fs.readFileSync(file, 'utf8')}`).not.toMatch(/canonical/);
    }
  });
});

describe('sitemap', () => {
  it('no lista nada bajo /v4', async () => {
    fetchMock.mockImplementation(async (url: string) => ({
      ok: true,
      json: async () => (String(url).includes('/catalog/models')
        ? { results: [{ slug: 'iphone-15' }] }
        : [{ slug: 'funda-roja' }]),
    }));
    const { default: sitemap } = await import('@/app/sitemap');
    const urls = (await sitemap()).map((entry: { url: string }) => entry.url);
    expect(urls).toContain('https://tienda.example/modelo/iphone-15');
    expect(urls.some((url: string) => /\/v4(\/|$)/.test(url))).toBe(false);
  });
});

describe('el armazón de la V3 bajo /v4', () => {
  function renderChrome() {
    return render(
      <>
        <SkipLink />
        <StorefrontHeader />
        <StorefrontContent><p>contenido</p></StorefrontContent>
        <StorefrontFooter />
      </>,
    );
  }

  it('en la tienda V3 hay cabecera y pie', () => {
    pathname = '/modelo/equipo-x';
    renderChrome();
    expect(screen.getByTestId('v3-header')).toBeInTheDocument();
    expect(screen.getByTestId('v3-footer')).toBeInTheDocument();
  });

  it.each(['/v4', '/v4/', '/v4/modelo/equipo-x'])('en %s no hay cabecera ni pie de la V3', (route) => {
    pathname = route;
    renderChrome();
    expect(screen.queryByTestId('v3-header')).toBeNull();
    expect(screen.queryByTestId('v3-footer')).toBeNull();
    // Sigue siendo escaparate: el enlace de salto apunta a su contenido.
    expect(screen.getByRole('link', { name: 'Saltar al contenido' })).toHaveAttribute('href', '#contenido');
    expect(document.getElementById('contenido')).toHaveTextContent('contenido');
  });

  it('el botón flotante de WhatsApp de la V3 tampoco: la franja de soporte V4 ya lo ofrece', () => {
    const config = { ...NEUTRAL_CONFIG, contact: { ...NEUTRAL_CONFIG.contact, whatsapp_link: 'https://wa.me/51900000000' } };
    pathname = '/v4/modelo/equipo-x';
    const { unmount } = render(<StorefrontProvider config={config}><WhatsAppButton /></StorefrontProvider>);
    expect(screen.queryByRole('link', { name: 'Contactar por WhatsApp' })).toBeNull();
    unmount();
    pathname = '/modelo/equipo-x';
    render(<StorefrontProvider config={config}><WhatsAppButton /></StorefrontProvider>);
    expect(screen.getByRole('link', { name: 'Contactar por WhatsApp' })).toBeInTheDocument();
  });

  it('una ruta que sólo empieza por «v4» no es la vista previa', () => {
    pathname = '/v40';
    renderChrome();
    expect(screen.getByTestId('v3-header')).toBeInTheDocument();
  });

  it('/admin sigue sin el armazón de la tienda', () => {
    pathname = '/admin/products';
    renderChrome();
    expect(screen.queryByTestId('v3-header')).toBeNull();
    expect(document.querySelector('.internal-surface')).not.toBeNull();
  });
});

describe('el layout de /v4', () => {
  it('envuelve la página en data-design="v4" e importa sus tokens', async () => {
    const { default: V4Layout } = await import('@/app/v4/layout');
    const { container } = render(<V4Layout><p>ficha</p></V4Layout>);
    const root = container.querySelector('[data-design="v4"]');
    expect(root).not.toBeNull();
    expect(root).toHaveTextContent('ficha');
    const source = fs.readFileSync(path.join(process.cwd(), 'app/v4/layout.tsx'), 'utf8');
    expect(source).toMatch(/import\s+["']\.\/v4\.css["']/);
    expect(source).toMatch(/Montserrat/);
  });

  it('Montserrat sólo se carga en la V4', () => {
    const root = fs.readFileSync(path.join(process.cwd(), 'app/layout.tsx'), 'utf8');
    expect(root).not.toMatch(/Montserrat/);
    const css = fs.readFileSync(path.join(process.cwd(), 'app/globals.css'), 'utf8');
    expect(css).not.toMatch(/Montserrat|--font-v4/);
  });

  it('los tokens V4 están acotados a [data-design="v4"] y respetan reduced-motion', () => {
    const css = fs.readFileSync(path.join(process.cwd(), 'app/v4/v4.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
    // Ninguna regla fuera del ámbito V4: cada selector empieza por [data-design="v4"].
    const selectors = css
      .replace(/@media[^{]+\{/g, '')
      .split('}')
      .map((block) => block.split('{')[0].trim())
      .filter(Boolean);
    expect(selectors.length).toBeGreaterThan(0);
    // Commas inside `:is(...)` / `:where(...)` do not start a new selector.
    const topLevel = (group: string) => {
      const parts: string[] = [];
      let depth = 0;
      let current = '';
      for (const ch of group) {
        if (ch === '(') depth += 1;
        if (ch === ')') depth -= 1;
        if (ch === ',' && depth === 0) { parts.push(current); current = ''; } else current += ch;
      }
      return [...parts, current];
    };
    for (const group of selectors) {
      for (const selector of topLevel(group)) {
        expect(selector.trim()).toMatch(/^\[data-design="v4"\]/);
      }
    }
  });
});
