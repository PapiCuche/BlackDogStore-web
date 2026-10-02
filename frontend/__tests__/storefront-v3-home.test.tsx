import { render, screen, waitFor, within } from '@testing-library/react';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';

/**
 * STOREFRONT-V3 · portada.
 *
 * La portada es de la tienda que la publica. Lo que aquí se fija:
 *
 *  · las categorías salen del catálogo real, no de una lista del piloto;
 *  · una tienda que no es la del piloto no ve nada del piloto — ni sus
 *    categorías, ni su copy, ni sus imágenes;
 *  · la campaña de portada es contenido publicado por la tienda: si existe
 *    aporta imagen y texto, y si no, la portada se sostiene sin ella;
 *  · el hero sigue siendo una losa de marca oscura, con sus tokens `slab-*`.
 */

jest.mock('next/navigation', () => ({
  usePathname: () => '/',
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const Home = require('@/app/page').default;
const Hero = require('@/app/components/Hero').default;
const { resetCatalogCategories } = require('@/app/lib/catalog-categories');
/* eslint-enable @typescript-eslint/no-require-imports */

const CATEGORIES = [
  { id: 7, name: 'Laptops', slug: 'laptops' },
  { id: 9, name: 'Audio', slug: 'audio' },
];

const PRODUCTS = [
  { id: 1, slug: 'portatil-a', name: 'Portátil A', price: 1500, inventory: 4, category: CATEGORIES[0] },
  { id: 2, slug: 'auricular-b', name: 'Auricular B', price: 90, inventory: 0, category: CATEGORIES[1] },
];

const OTHER_STORE: StorefrontConfig = {
  ...NEUTRAL_CONFIG,
  company: { ...NEUTRAL_CONFIG.company, name: 'Tienda Norte', slug: 'tienda-norte' },
  contact: { ...NEUTRAL_CONFIG.contact, city: 'Trujillo', whatsapp_link: 'https://wa.me/51900000000' },
  page: { ...NEUTRAL_CONFIG.page, hero_title: 'Tecnología\npara trabajar', hero_subtitle: 'Equipos revisados.' },
};

let products: unknown[] = PRODUCTS;
let categories: unknown[] = CATEGORIES;

beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: () => ({
        matches: false, addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {},
      }),
    });
  }
});

beforeEach(() => {
  resetCatalogCategories();
  products = PRODUCTS;
  categories = CATEGORIES;
  global.fetch = jest.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.includes('/categories') ? categories : url.includes('/products') ? products : [];
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  }) as unknown as typeof fetch;
});

function inStore(node: React.ReactNode, config: StorefrontConfig = OTHER_STORE) {
  return render(
    <ThemeProvider>
      <StorefrontProvider config={config}>{node}</StorefrontProvider>
    </ThemeProvider>,
  );
}

const PILOT = /black dog|iphone|ipad|apple|\bmac\b|watch|arequipa/i;

describe('portada · categorías', () => {
  it('son las del catálogo real de la tienda, con el filtro que el catálogo lee', async () => {
    inStore(<Home />);
    const section = (await screen.findByRole('heading', { name: 'Categorías' })).closest('section') as HTMLElement;
    const links = within(section).getAllByRole('link').filter((a) => a.getAttribute('href')?.includes('category='));
    expect(links.map((a) => [a.textContent?.trim(), a.getAttribute('href')])).toEqual([
      ['Laptops', '/product?category=laptops'],
      ['Audio', '/product?category=audio'],
    ]);
  });

  it('una tienda sin categorías no dibuja el bloque', async () => {
    categories = [];
    inStore(<Home />);
    await screen.findByRole('region', { name: 'Productos destacados' });
    expect(screen.queryByRole('heading', { name: 'Categorías' })).not.toBeInTheDocument();
  });
});

describe('portada · sin datos del piloto', () => {
  it('otra tienda no ve categorías, copy ni imágenes del piloto', async () => {
    const { container } = inStore(<Home />);
    await screen.findByRole('region', { name: 'Productos destacados' });
    await screen.findByRole('link', { name: 'Laptops' });

    expect(container.textContent ?? '').not.toMatch(PILOT);
    const sources = Array.from(container.querySelectorAll('img')).map((img) => img.getAttribute('src') ?? '');
    expect(sources.filter((src) => /editorial|logo-icon|products\//.test(src))).toEqual([]);
  });

  it('se sostiene sin campaña, sin servicios y sin catálogo', async () => {
    products = [];
    categories = [];
    const { container } = inStore(<Home />, { ...NEUTRAL_CONFIG, company: { ...NEUTRAL_CONFIG.company, name: 'Vacía' } });
    expect(await screen.findByText('Catálogo en preparación')).toBeInTheDocument();
    expect(container.textContent ?? '').not.toMatch(PILOT);
    expect(container.querySelector('img[src*="logo-icon"]')).toBeNull();
    // Sin servicios publicados no se anuncia un taller.
    expect(screen.queryByText('Cómo trabajamos')).not.toBeInTheDocument();
  });
});

describe('portada · catálogo y preguntas', () => {
  it('los productos reales van en un carrusel que conserva sus enlaces', async () => {
    inStore(<Home />);
    const carousel = await screen.findByRole('region', { name: 'Productos destacados' });
    expect(within(carousel).getByRole('link', { name: /Portátil A/ })).toHaveAttribute('href', '/product/portatil-a');
    expect(within(carousel).getByRole('link', { name: /Auricular B/ })).toHaveAttribute('href', '/product/auricular-b');
  });

  it('las preguntas frecuentes son las que la tienda publicó', async () => {
    inStore(<Home />, {
      ...OTHER_STORE,
      faqs: [{ question: '¿Hacen envíos?', answer: 'Sí, a todo el país.' }],
    });
    expect(await screen.findByText('¿Hacen envíos?')).toBeInTheDocument();
    expect(screen.getByText('Sí, a todo el país.')).toBeInTheDocument();
  });

  it('sin preguntas publicadas no hay bloque de preguntas', async () => {
    inStore(<Home />);
    await screen.findByRole('region', { name: 'Productos destacados' });
    expect(screen.queryByRole('heading', { name: 'Preguntas frecuentes' })).not.toBeInTheDocument();
  });
});

describe('hero', () => {
  it('es una losa de marca oscura, no una superficie que sigue al tema', () => {
    const { container } = inStore(<Hero />);
    const slab = container.querySelector('section') as HTMLElement;
    expect(slab.className).toContain('bg-slab');
    expect(slab.className).toContain('text-slab-foreground');
    expect(slab.className).not.toContain('bg-background');
  });

  it('sin campaña usa el texto de la tienda y no pinta ninguna imagen de producto', async () => {
    const { container } = inStore(<Hero />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Tecnologíapara trabajar');
    expect(screen.getByText('Equipos revisados.')).toBeInTheDocument();
    expect(container.querySelector('[data-hero-art]')).toBeNull();
    await waitFor(() => expect(screen.getByRole('link', { name: 'Ver catálogo' })).toHaveAttribute('href', '/product'));
  });

  it('una campaña de portada publicada aporta imagen, texto y destinos', () => {
    const { container } = inStore(<Hero />, {
      ...OTHER_STORE,
      campaigns: {
        home_hero: {
          title: 'Vuelta a clases', body: 'Portátiles con entrega inmediata.',
          image_url: 'https://cdn.norte.example/campana.png',
          cta_label: 'Ver portátiles', cta_url: '/product?category=laptops',
          secondary_cta_label: 'Cómo comprar', secondary_cta_url: 'https://norte.example/ayuda',
          product: null,
        } as never,
      },
    });
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Vuelta a clases');
    expect(screen.getByText('Portátiles con entrega inmediata.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ver portátiles' })).toHaveAttribute('href', '/product?category=laptops');
    const outside = screen.getByRole('link', { name: 'Cómo comprar' });
    expect(outside).toHaveAttribute('href', 'https://norte.example/ayuda');
    expect(outside).toHaveAttribute('rel', 'noopener noreferrer');

    const art = container.querySelector('[data-hero-art] img') as HTMLImageElement;
    expect(decodeURIComponent(art.getAttribute('src') ?? '')).toContain('cdn.norte.example/campana.png');
  });

  it('el destino secundario configurado por la tienda se respeta', () => {
    inStore(<Hero />, {
      ...OTHER_STORE,
      page: { ...OTHER_STORE.page, hero_secondary_cta_label: 'Agenda una visita', hero_secondary_cta_url: '/contact' },
    });
    expect(screen.getByRole('link', { name: 'Agenda una visita' })).toHaveAttribute('href', '/contact');
  });

  it('no escribe rótulos comerciales que la tienda no publicó', () => {
    const { container } = inStore(<Hero />);
    expect(container.textContent ?? '').not.toMatch(/Equipos\s*·?\s*Accesorios|Soporte técnico/);
    expect(container.textContent ?? '').not.toMatch(PILOT);
  });
});
