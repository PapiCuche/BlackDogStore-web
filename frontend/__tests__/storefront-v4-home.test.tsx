import { render, screen, within } from '@testing-library/react';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';

/**
 * Portada V4 — huecos de imagen que la tienda llena desde su panel.
 *
 * El diseño pide una imagen junto al texto del hero y una por categoría. Esas
 * imágenes no pueden venir en el código: son de cada tienda, y la del piloto no
 * tiene licencia para repartirse. Lo que se fija aquí:
 *
 *  · el hero tiene dos estilos y lo elige la tienda; el oscuro sigue siendo el
 *    de siempre para quien no eligió;
 *  · una imagen colocada se muestra tal cual, sin caja detrás: un PNG sin fondo
 *    se apoya sobre el color de la sección;
 *  · un hueco vacío no es una imagen rota: la sección se compone sin ella.
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

const IMAGE = '/api/storefront/images/' + 'c'.repeat(32) + '/';

function store(overrides: Partial<StorefrontConfig> = {}): StorefrontConfig {
  return { ...OTHER_STORE, ...overrides };
}

describe('hero · estilo que elige la tienda', () => {
  it('quien no eligió conserva la losa oscura', () => {
    const { container } = inStore(<Hero />);
    const hero = container.querySelector('section') as HTMLElement;

    expect(hero).toHaveAttribute('data-hero-variant', 'dark');
    expect(hero.className).toContain('bg-slab');
  });

  it('el estilo claro no usa la losa y lleva el mismo contenido', () => {
    const { container } = inStore(<Hero />, store({ page: { ...OTHER_STORE.page, hero_variant: 'light' } }));
    const hero = container.querySelector('section') as HTMLElement;

    expect(hero).toHaveAttribute('data-hero-variant', 'light');
    expect(hero.className).not.toContain('bg-slab');
    expect(within(hero).getByRole('heading', { level: 1 })).toHaveTextContent('Tecnología');
    expect(within(hero).getByText('Equipos revisados.')).toBeInTheDocument();
    expect(within(hero).getByRole('link', { name: 'Ver catálogo' })).toHaveAttribute('href', '/product');
  });

  it.each(['dark', 'light'] as const)('en el estilo %s muestra la imagen que la tienda colocó', (variant) => {
    const { container } = inStore(
      <Hero />, store({ page: { ...OTHER_STORE.page, hero_variant: variant, hero_image_url: IMAGE } }),
    );
    const art = container.querySelector('[data-hero-art] img') as HTMLImageElement;

    expect(art).not.toBeNull();
    expect(art.getAttribute('src')).toBe(IMAGE);
    // Sin recorte ni relleno: un PNG sin fondo se ve entero y sin caja.
    expect(art.className).toContain('object-contain');
  });

  it.each(['dark', 'light'] as const)('en el estilo %s, sin imagen no deja un hueco roto', (variant) => {
    const { container } = inStore(<Hero />, store({ page: { ...OTHER_STORE.page, hero_variant: variant } }));

    expect(container.querySelector('[data-hero-art]')).toBeNull();
    expect(container.querySelector('section img[src=""]')).toBeNull();
  });

  it('la imagen de una campaña vigente manda sobre la de la portada', () => {
    const campaign = {
      slot: 'home_hero', badge: '', title: 'Semana del estudiante', subtitle: '', body: '',
      image_url: '/campana.png', cta_label: '', cta_url: '', secondary_cta_label: '',
      secondary_cta_url: '', product: null,
    };
    const { container } = inStore(<Hero />, store({
      page: { ...OTHER_STORE.page, hero_variant: 'light', hero_image_url: IMAGE },
      campaigns: { ...OTHER_STORE.campaigns, home_hero: campaign },
    }));

    expect(container.querySelector('[data-hero-art] img')?.getAttribute('src')).toBe('/campana.png');
  });
});

describe('portada · categorías con imagen', () => {
  it('cada categoría es una tarjeta que lleva a su filtro del catálogo', async () => {
    inStore(<Home />);
    const section = (await screen.findByRole('heading', { name: 'Encuentra lo que necesitas.' }))
      .closest('section') as HTMLElement;
    const links = within(section).getAllByRole('link').filter((a) => a.getAttribute('href')?.includes('category='));

    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/product?category=laptops', '/product?category=audio',
    ]);
    expect(within(links[0]).getByText('Laptops')).toBeInTheDocument();
  });

  it('muestra la imagen de la categoría que la tiene y deja el hueco en la que no', async () => {
    categories = [
      { id: 7, name: 'Laptops', slug: 'laptops', image_url: IMAGE },
      { id: 9, name: 'Audio', slug: 'audio', image_url: '' },
    ];
    inStore(<Home />);
    const section = (await screen.findByRole('heading', { name: 'Encuentra lo que necesitas.' }))
      .closest('section') as HTMLElement;
    const withImage = within(section).getByRole('link', { name: /Laptops/ });
    const without = within(section).getByRole('link', { name: /Audio/ });

    const img = withImage.querySelector('img') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe(IMAGE);
    expect(img.className).toContain('object-contain');
    expect(withImage.querySelector('[data-image-slot="filled"]')).not.toBeNull();

    expect(without.querySelector('img')).toBeNull();
    expect(without.querySelector('[data-image-slot="empty"]')).not.toBeNull();
  });
});

describe('portada · bloques de la tienda', () => {
  it('con la losa oscura no se apila una segunda franja negra', async () => {
    inStore(<Home />);
    await screen.findByRole('region', { name: 'Productos destacados' });
    expect(screen.queryByTestId('brand-statement')).toBeNull();
  });

  it('la franja de marca nombra a la tienda y sólo habla de reparar si repara', async () => {
    const light = { ...OTHER_STORE.page, hero_variant: 'light' as const };
    const { rerender } = inStore(<Home />, store({ page: light }));
    const statement = await screen.findByTestId('brand-statement');
    expect(statement.querySelector('p')).toHaveTextContent('Tienda Norte');
    expect(statement.textContent).not.toMatch(/repara/i);

    rerender(
      <ThemeProvider>
        <StorefrontProvider config={store({
          page: light,
          services: [{ title: 'Cambio de pantalla', description: '', devices_text: '', estimated_time_text: '', highlight: '' }],
        })}>
          <Home />
        </StorefrontProvider>
      </ThemeProvider>,
    );
    expect((await screen.findByTestId('brand-statement')).textContent).toMatch(/repara con respaldo/i);
  });

  it('«Cerca de ti» aparece con la dirección de la tienda y no sin ella', async () => {
    const { unmount } = inStore(<Home />, store({
      contact: { ...OTHER_STORE.contact, address: 'Av. España 123', city: 'Trujillo' },
    }));
    const block = (await screen.findByRole('heading', { name: 'Cerca de ti, antes y después.' })).closest('section') as HTMLElement;
    expect(within(block).getByText(/Av\. España 123/)).toBeInTheDocument();
    unmount();

    inStore(<Home />, store({ contact: { ...OTHER_STORE.contact, address: '', city: '' } }));
    await screen.findByRole('region', { name: 'Productos destacados' });
    expect(screen.queryByRole('heading', { name: 'Cerca de ti, antes y después.' })).not.toBeInTheDocument();
  });
});
