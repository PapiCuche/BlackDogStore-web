/**
 * @jest-environment node
 */
import { renderToString } from 'react-dom/server';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

import { accessoryFixture, modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * P2 · F2 — la ficha V4 llega pintada desde el servidor (LCP).
 *
 * En F1 la ficha se pintaba en el navegador: el primer HTML era un esqueleto y
 * el nombre, el precio y la foto llegaban después de una segunda lectura. Aquí
 * se fija que:
 *
 *  · el HTML del servidor ya trae el nombre, el precio y la foto principal,
 *    cargada con prioridad y con `sizes` del ancho real de la galería;
 *  · `generateMetadata` y la página leen el modelo UNA vez por petición
 *    (`cache()` de React, que en el servidor vive lo que dura la petición);
 *  · «no existe» y «falló la lectura» siguen sin indexarse.
 *
 * Entorno `node`: es lo que hace el servidor. Sin efectos, sin `fetch` del
 * navegador: lo que se ve aquí es exactamente el primer HTML.
 */

// Fuera del servidor de React, `cache()` no memoriza (devuelve la función tal
// cual). Aquí se sustituye por una que sí lo hace, como la del servidor durante
// una petición: así la prueba ve si la metadata y la página comparten lectura.
jest.mock('react', () => {
  const actual = jest.requireActual('react');
  return {
    ...actual,
    cache: <A extends unknown[], R>(fn: (...args: A) => R) => {
      const memo = new Map<string, R>();
      return (...args: A) => {
        const key = JSON.stringify(args);
        if (!memo.has(key)) memo.set(key, fn(...args));
        return memo.get(key) as R;
      };
    },
  };
});

// Antes de cargar `ProductImage` (lo carga la página, con `import()`): este host se optimiza con `next/image`.
process.env.NEXT_PUBLIC_IMAGE_HOSTS = 'img.example.com';

const fetchMock = jest.fn();
beforeEach(() => {
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

const NOINDEX = { index: false, follow: false };

async function serverHtml(slug: string, line?: string) {
  const { default: Page } = await import('@/app/v4/modelo/[slug]/page');
  const element = await Page({
    params: Promise.resolve({ slug }),
    searchParams: Promise.resolve(line ? { line } : {}),
  });
  // `<!-- -->` separa dos textos seguidos («S/ » y el importe); el navegador lo lee como uno.
  return renderToString(<StorefrontProvider config={NEUTRAL_CONFIG}>{element}</StorefrontProvider>)
    .replace(/<!-- -->/g, '');
}

function modelCalls(slug: string) {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes(`/catalog/models/${slug}`));
}

describe('el primer HTML de /v4/modelo/<slug>', () => {
  it('trae el nombre, el precio y la foto principal, sin esqueleto', async () => {
    fetchMock.mockResolvedValue(reply({ ...modelFixture(), slug: 'html-a' }));
    const html = await serverHtml('html-a');
    expect(html).toContain('Equipo X');
    expect(html).toContain('S/ 3999.00');
    expect(html).toContain('http://localhost/azul-128.png');
    expect(html).not.toContain('Cargando modelo');
  });

  it('la foto principal se pide con prioridad y se anuncia en la cabecera del documento', async () => {
    fetchMock.mockResolvedValue(reply({ ...modelFixture(), slug: 'html-b' }));
    const html = await serverHtml('html-b');
    const img = html.match(/<img[^>]*azul-128\.png[^>]*>/)?.[0] ?? '';
    expect(img).toMatch(/fetchPriority="high"/);
    expect(img).toMatch(/loading="eager"/);
    expect(html).toMatch(/<link rel="preload"[^>]*href="http:\/\/localhost\/azul-128\.png"[^>]*>/);
    // Sólo la foto principal: las miniaturas no compiten con ella.
    expect(html.match(/<img[^>]*fetchPriority="high"/g)).toHaveLength(1);
  });

  it('una foto optimizable lleva el ancho real de la galería en `sizes`', async () => {
    const fixture = { ...modelFixture(), slug: 'html-e' };
    fixture.variants[0].image_url = 'https://img.example.com/azul-128.png';
    fetchMock.mockResolvedValue(reply(fixture));
    const html = await serverHtml('html-e');
    const img = html.match(/<img[^>]*azul-128\.png[^>]*>/)?.[0] ?? '';
    expect(img).toMatch(/fetchPriority="high"/);
    // La galería ocupa toda la pantalla en el móvil y menos de la mitad en escritorio.
    expect(img).toContain('sizes="(min-width: 1280px) 610px, (min-width: 1024px) 47vw, 100vw"');
  });

  it('con ?line=used, el precio del equipo elegido', async () => {
    fetchMock.mockResolvedValue(reply({ ...modelFixture(), slug: 'html-c' }));
    const html = await serverHtml('html-c', 'used');
    expect(html).toContain('S/ 2599.00');
  });

  it('pide el modelo a la API pública, sin caché compartida entre hosts', async () => {
    fetchMock.mockResolvedValue(reply({ ...modelFixture(), slug: 'html-d' }));
    await serverHtml('html-d');
    const [url, init] = modelCalls('html-d')[0];
    expect(String(url)).toMatch(/\/api\/catalog\/models\/html-d$/);
    expect(init).toMatchObject({ cache: 'no-store' });
  });

  it('P2 · F3: los accesorios compatibles ya vienen en el primer HTML', async () => {
    const fixture = { ...modelFixture(), slug: 'html-acc', accessories: [accessoryFixture()] };
    fetchMock.mockResolvedValue(reply(fixture));
    const html = await serverHtml('html-acc');
    expect(html).toContain('data-testid="v4-accessories"');
    expect(html).toContain('Compatible con Equipo X');
    expect(html).toContain('Funda X');
    expect(html).toContain('S/ 89.00');
    expect(html).toContain('href="/product/funda-x"');
    // La foto del accesorio no compite con la principal del modelo.
    expect(html.match(/<img[^>]*fetchPriority="high"/g)).toHaveLength(1);
  });

  it('P2 · F3: sin accesorios, el primer HTML no trae el bloque', async () => {
    fetchMock.mockResolvedValue(reply({ ...modelFixture(), slug: 'html-noacc', accessories: [] }));
    const html = await serverHtml('html-noacc');
    expect(html).not.toContain('v4-accessories');
    expect(html).not.toContain('Compatible con');
  });

  it('un modelo inexistente se dice en el primer HTML', async () => {
    fetchMock.mockResolvedValue(reply({}, 404));
    const html = await serverHtml('html-missing');
    expect(html).toContain('Modelo no encontrado');
  });

  it('si la API falla, la página no se rompe: el navegador lo vuelve a intentar', async () => {
    fetchMock.mockRejectedValue(new Error('down'));
    const html = await serverHtml('html-down');
    expect(html).toContain('Cargando modelo');
  });
});

describe('una sola lectura por petición', () => {
  it('generateMetadata y la página comparten la lectura del modelo', async () => {
    fetchMock.mockResolvedValue(reply({ ...modelFixture(), slug: 'once-a' }));
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'once-a' }) } as never);
    expect(meta.title).toBe('Equipo X');
    await serverHtml('once-a');
    expect(modelCalls('once-a')).toHaveLength(1);
  });

  it('la lectura está envuelta en cache() de React', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const fs = require('fs') as typeof import('fs');
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const path = require('path') as typeof import('path');
    const page = fs.readFileSync(path.join(process.cwd(), 'app/v4/modelo/[slug]/page.tsx'), 'utf8');
    // La página no hace su propio fetch: usa el lector compartido.
    expect(page).not.toMatch(/\bfetch\(/);
    const reader = fs.readFileSync(path.join(process.cwd(), 'app/lib/catalog-models-server.ts'), 'utf8');
    expect(reader).toMatch(/import\s*\{[^}]*\bcache\b[^}]*\}\s*from\s*["']react["']/);
    expect(reader).toMatch(/=\s*cache\(/);
  });
});

describe('noindex en todos los casos', () => {
  it.each([
    ['existe', () => reply({ ...modelFixture(), slug: 'meta-ok' }), 'meta-ok', 'Equipo X'],
    ['no existe', () => reply({}, 404), 'meta-404', 'Modelo no encontrado'],
    ['la API responde con error', () => reply({}, 500), 'meta-500', 'Modelo'],
  ])('%s', async (_name, answer, slug, title) => {
    fetchMock.mockImplementation(async () => answer());
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug }) } as never);
    expect(meta.robots).toEqual(NOINDEX);
    expect(meta.title).toBe(title);
    expect(meta.alternates).toBeUndefined();
  });

  it('la API caída', async () => {
    fetchMock.mockRejectedValue(new Error('down'));
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'meta-down' }) } as never);
    expect(meta.robots).toEqual(NOINDEX);
  });
});
