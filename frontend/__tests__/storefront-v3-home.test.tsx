import { render, screen, waitFor, within } from '@testing-library/react';
import { renderToString } from 'react-dom/server';

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
    // El bloque se titula «Encuentra lo que necesitas.» y cada categoría es una
    // tarjeta con su nombre y «Explorar». Lo que se fija no cambia: son las del
    // catálogo, en su orden, con el filtro que el catálogo lee.
    const section = (await screen.findByRole('heading', { name: 'Encuentra lo que necesitas.' })).closest('section') as HTMLElement;
    const links = within(section).getAllByRole('link').filter((a) => a.getAttribute('href')?.includes('category='));
    expect(links.map((a) => [a.querySelector('h3')?.textContent, a.getAttribute('href')])).toEqual([
      ['Laptops', '/product?category=laptops'],
      ['Audio', '/product?category=audio'],
    ]);
  });

  it('una tienda sin categorías no dibuja el bloque', async () => {
    categories = [];
    inStore(<Home />);
    await screen.findByRole('region', { name: 'Productos destacados' });
    expect(screen.queryByRole('heading', { name: 'Encuentra lo que necesitas.' })).not.toBeInTheDocument();
  });
});

describe('portada · sin datos del piloto', () => {
  it('otra tienda no ve categorías, copy ni imágenes del piloto', async () => {
    const { container } = inStore(<Home />);
    await screen.findByRole('region', { name: 'Productos destacados' });
    await screen.findByRole('heading', { name: 'Encuentra lo que necesitas.' });

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
    expect(screen.queryByText('Servicio técnico')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Ver servicios' })).not.toBeInTheDocument();
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
  it('se apoya sobre el fondo del tema, no sobre una losa oscura fija', () => {
    const { container } = inStore(<Hero />);
    const hero = container.querySelector('section') as HTMLElement;
    expect(hero.className).toContain('bg-background');
    expect(hero.className).not.toContain('bg-slab');
  });

  it('los rótulos bajo los botones son las categorías del catálogo de la tienda', async () => {
    const { container } = inStore(<Hero />);
    await waitFor(() => expect(
      Array.from(container.querySelectorAll('ul li')).map((li) => li.textContent),
    ).toEqual(['Laptops', 'Audio']));
  });

  it('sin servicios publicados no ofrece un botón de taller', () => {
    inStore(<Hero />);
    expect(screen.queryByRole('link', { name: 'Servicio técnico' })).not.toBeInTheDocument();
  });

  it('con servicios publicados el segundo botón lleva al taller', () => {
    inStore(<Hero />, {
      ...OTHER_STORE,
      services: [{ title: 'Cambio de pantalla', description: '', devices_text: '', estimated_time_text: '', highlight: '' }],
    });
    expect(screen.getByRole('link', { name: 'Servicio técnico' })).toHaveAttribute('href', '/services');
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

  describe('imagen del hero (PERF-LCP)', () => {
    const MANAGED = '/api/storefront/images/' + 'f'.repeat(32);
    const withHeroImage = { ...OTHER_STORE, page: { ...OTHER_STORE.page, hero_image_url: MANAGED } };
    // La columna de la imagen: toda la pantalla hasta `lg`, la mitad después, y
    // nunca más de la mitad de `.v3-container` (1248 px) menos el hueco.
    const HERO_SIZES = '(min-width: 1280px) 604px, (min-width: 1024px) 50vw, 100vw';

    it('la imagen subida desde el panel sale optimizada, con srcset, sizes y prioridad alta', () => {
      const { container } = inStore(<Hero />, withHeroImage);
      const art = container.querySelector('[data-hero-art] img') as HTMLImageElement;
      expect(art.getAttribute('srcset')).toContain(`/_next/image?url=${encodeURIComponent(MANAGED)}&w=`);
      expect(art).toHaveAttribute('sizes', HERO_SIZES);
      expect(art).toHaveAttribute('fetchpriority', 'high');
      expect(art).not.toHaveAttribute('loading', 'lazy');
      // Lo que se ve no cambia: recorte entero, sin caja, con la sombra por silueta.
      expect(art.className).toContain('object-contain');
      expect(art.style.filter).toBe('var(--cutout-shadow)');
      expect(art).toHaveAttribute('alt', '');
    });

    it('en el primer HTML la anuncia una sola vez, con su srcset y prioridad alta', () => {
      const html = renderToString(
        <ThemeProvider><StorefrontProvider config={withHeroImage}><Hero /></StorefrontProvider></ThemeProvider>,
      );
      const preloads = html.match(/<link rel="preload" as="image"[^>]*>/g) ?? [];
      expect(preloads).toHaveLength(1);
      expect(preloads[0]).toMatch(/fetchPriority="high"/);
      expect(preloads[0]).toContain(`imageSrcSet="/_next/image?url=${encodeURIComponent(MANAGED)}`);
      expect(preloads[0]).toContain(`imageSizes="${HERO_SIZES}"`);
    });

    it('una imagen externa de campaña se muestra tal cual, pero con prioridad alta', () => {
      const { container } = inStore(<Hero />, {
        ...OTHER_STORE,
        campaigns: {
          home_hero: { title: 'Vuelta a clases', image_url: 'https://cdn.norte.example/campana.png', product: null } as never,
        },
      });
      const art = container.querySelector('[data-hero-art] img') as HTMLImageElement;
      expect(art).toHaveAttribute('src', 'https://cdn.norte.example/campana.png');
      expect(art).toHaveAttribute('fetchpriority', 'high');
      expect(art).toHaveAttribute('loading', 'eager');
      expect(art.className).toContain('object-contain');
      expect(art.style.filter).toBe('');
    });
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

describe('portada · imágenes bajo el hero (PERF-HOME-IMAGES)', () => {
  // Un ensayo con datos reales: el hero ya salía optimizado (30 KB), pero tres
  // imágenes del panel bajo él se pedían como PNG de 1,3–1,46 MB y competían
  // con él por el ancho de banda en el móvil.
  const id = (c: string) => '/api/storefront/images/' + c.repeat(32);
  const CATEGORY = id('a');
  const SERVICES = id('b');
  const LOCATION = id('c');
  const PROMO = id('d');
  const FULL: StorefrontConfig = {
    ...OTHER_STORE,
    services: [{ title: 'Cambio de pantalla', description: '', devices_text: '', estimated_time_text: '', highlight: '' }],
    contact: { ...OTHER_STORE.contact, address: 'Av. Principal 123' },
    page: { ...OTHER_STORE.page, services_image_url: SERVICES, location_image_url: LOCATION },
    campaigns: {
      home_bottom_promo: {
        slot: 'home_bottom_promo', badge: '', title: 'Campaña', subtitle: '', body: '', image_url: PROMO,
        cta_label: '', cta_url: '', secondary_cta_label: '', secondary_cta_url: '', product: null,
      },
    },
  };

  async function renderFull() {
    categories = [{ id: 7, name: 'Laptops', slug: 'laptops', image_url: CATEGORY, show_on_home: true }];
    const view = inStore(<Home />, FULL);
    await screen.findByRole('heading', { name: 'Encuentra lo que necesitas.' });
    await screen.findByRole('region', { name: 'Productos destacados' });
    return view;
  }

  function optimised(container: HTMLElement, src: string) {
    const encoded = encodeURIComponent(src);
    const img = Array.from(container.querySelectorAll('img'))
      .find((el) => (el.getAttribute('srcset') ?? '').includes(`/_next/image?url=${encoded}&`));
    expect(img).toBeDefined();
    return img as HTMLImageElement;
  }

  it('ninguna imagen del panel se pide en bruto', async () => {
    const { container } = await renderFull();
    const raw = Array.from(container.querySelectorAll('img'))
      .map((img) => img.getAttribute('src') ?? '')
      .filter((src) => src.startsWith('/api/storefront/images/'));
    expect(raw).toEqual([]);
  });

  it.each([
    ['categoría', CATEGORY, '(min-width: 1280px) 256px, (min-width: 1024px) 25vw, (min-width: 640px) 50vw, 100vw', 'var(--cutout-shadow)'],
    ['servicios', SERVICES, '(min-width: 1280px) 536px, (min-width: 1024px) 50vw, 100vw', 'var(--cutout-shadow)'],
    ['ubicación', LOCATION, '(min-width: 1280px) 360px, (min-width: 1024px) 33vw, 100vw', 'var(--cutout-shadow)'],
    ['campaña', PROMO, '(min-width: 1280px) 608px, (min-width: 768px) 50vw, 100vw', 'var(--cutout-shadow-on-slab)'],
  ])('la imagen de %s sale optimizada, perezosa, entera y con su sombra', async (_name, src, sizes, shadow) => {
    const { container } = await renderFull();
    const img = optimised(container, src);
    expect(img).toHaveAttribute('sizes', sizes);
    expect(img).toHaveAttribute('loading', 'lazy');
    expect(img).not.toHaveAttribute('fetchpriority');
    expect(img.className).toContain('object-contain');
    expect(img.style.filter).toBe(shadow);
    // Llena una caja con su tamaño ya puesto: no hay salto al cargar.
    expect(img.style.position).toBe('absolute');
    expect(img.parentElement?.className).toMatch(/\brelative\b/);
    expect(img.parentElement?.className).toMatch(/\baspect-(square|video)\b|\bh-(72|80)\b/);
  });

  it('la tarjeta de categoría conserva el zoom al pasar el cursor', async () => {
    const { container } = await renderFull();
    expect(optimised(container, CATEGORY).className).toContain('group-hover:scale-105');
  });

  it('la campaña conserva su texto alternativo', async () => {
    const { container } = await renderFull();
    expect(optimised(container, PROMO)).toHaveAttribute('alt', 'Campaña');
  });
});

describe('portada · logotipos bajo el hero (PERF-LCP)', () => {
  it('la marca de agua de la firma de marca se pide perezosa: no compite con el hero', async () => {
    const withLogos: StorefrontConfig = {
      ...OTHER_STORE,
      branding: {
        ...OTHER_STORE.branding,
        logos: { isotype_on_light: '/i-negra.png', isotype_on_dark: '/i-blanca.png' } as never,
      },
    };
    inStore(<Home />, withLogos);
    const watermark = screen.getByTestId('brand-statement').querySelector('img') as HTMLImageElement;
    expect(watermark).not.toBeNull();
    expect(watermark).toHaveAttribute('loading', 'lazy');
    await waitFor(() => expect(screen.getByRole('link', { name: 'Ver catálogo' })).toBeInTheDocument());
  });
});
