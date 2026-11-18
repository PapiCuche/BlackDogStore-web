import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fs from 'fs';
import path from 'path';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { EMPTY_PAGE, NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';
import type { CatalogModelCard, CatalogModelList } from '@/app/lib/catalog-models';

/**
 * P2 · F2 — la portada de la vista previa V4 (`/v4`) y su cabecera.
 *
 * TODO SALE DE LA TIENDA. Los textos del hero, su imagen y las campañas son de
 * la configuración de la empresa; los modelos, de su catálogo público. Lo que
 * se fija aquí:
 *
 *  · sin textos configurados no hay hero: ni un lema ni un antetítulo de
 *    relleno;
 *  · cada modelo enlaza a su ficha V4, con su precio real o «Agotado»;
 *  · nada de precios, existencias, reseñas, horarios ni direcciones
 *    inventados, y ningún muro de compra «La Manada»;
 *  · no se indexa y no declara canónica;
 *  · la cabecera: el logotipo horizontal en escritorio, el isotipo en el
 *    móvil, «Servicio» sólo si la tienda publicó servicios.
 */

let pathname = '/v4';
jest.mock('next/navigation', () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const { HomeV4 } = require('@/app/components/v4/HomeV4');
const { TopBarV4 } = require('@/app/components/v4/TopBarV4');
/* eslint-enable @typescript-eslint/no-require-imports */

const MANAGED = (n: number) => `/api/storefront/images/${String(n).padStart(32, 'a')}`;

function card(extra: Partial<CatalogModelCard> = {}): CatalogModelCard {
  return {
    slug: 'equipo-x',
    name: 'Equipo X',
    image_url: 'https://cdn.example.com/equipo-x.png',
    category: { slug: 'telefonos', name: 'Teléfonos' },
    from_price: '3999.00',
    has_new: true,
    has_used: false,
    sold_out: false,
    ...extra,
  };
}

function list(results: CatalogModelCard[]): CatalogModelList {
  return {
    lines: { new: { label: 'De caja', count: results.length }, used: { label: 'Reacondicionados', count: 0 } },
    results,
  };
}

function config(extra: Partial<StorefrontConfig> = {}): StorefrontConfig {
  return {
    ...NEUTRAL_CONFIG,
    company: { ...NEUTRAL_CONFIG.company, name: 'Tienda Norte', slug: 'tienda-norte' },
    ...extra,
  };
}

function renderHome(models: CatalogModelList | null, cfg: StorefrontConfig = config()) {
  return render(
    <StorefrontProvider config={cfg}>
      <HomeV4 models={models} />
    </StorefrontProvider>,
  );
}

const HERO_PAGE = {
  ...EMPTY_PAGE,
  hero_eyebrow: 'Temporada de invierno',
  hero_title: 'Tu próximo equipo',
  hero_subtitle: 'Elige capacidad y color.',
  hero_image_url: MANAGED(1),
};

describe('el hero sólo dice lo que la tienda escribió', () => {
  it('sin textos configurados no hay hero', () => {
    const { container } = renderHome(list([card()]), config({ page: { ...EMPTY_PAGE, hero_image_url: MANAGED(1) } }));
    expect(screen.queryByTestId('v4-hero')).toBeNull();
    // El encabezado de la página es el de los modelos, no un lema inventado.
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Modelos');
    expect(container.querySelectorAll('h1')).toHaveLength(1);
  });

  it('una configuración vacía (la neutra) no escribe ningún texto de hero', () => {
    const { container } = renderHome(list([card()]), NEUTRAL_CONFIG);
    expect(screen.queryByTestId('v4-hero')).toBeNull();
    for (const text of ['Temporada', 'Tu próximo', 'Compra con', 'Descubre', 'Bienvenid']) {
      expect(container.textContent).not.toContain(text);
    }
  });

  it('con textos configurados, los muestra tal cual con un solo botón principal', () => {
    renderHome(list([card()]), config({ page: HERO_PAGE }));
    const hero = screen.getByTestId('v4-hero');
    expect(within(hero).getByText('Temporada de invierno')).toBeInTheDocument();
    expect(within(hero).getByRole('heading', { level: 1 })).toHaveTextContent('Tu próximo equipo');
    expect(within(hero).getByText('Elige capacidad y color.')).toBeInTheDocument();
    const ctas = within(hero).getAllByRole('link');
    expect(ctas).toHaveLength(1);
    expect(ctas[0]).toHaveAttribute('href', '#modelos');
    // La imagen subida desde el panel, decorativa (el titular ya lo dice) y con prioridad: es el LCP.
    const img = hero.querySelector('img');
    expect(img).toHaveAttribute('src', MANAGED(1));
    expect(img).toHaveAttribute('alt', '');
    expect(img).toHaveAttribute('fetchpriority', 'high');
    // Con hero, «Modelos» deja de ser el h1.
    expect(screen.getByRole('heading', { level: 2, name: 'Modelos' })).toBeInTheDocument();
  });

  it('la campaña vigente del hero manda sobre los textos de la portada', () => {
    const campaign = {
      slot: 'home_hero', badge: 'Campaña', title: 'Campaña de verano', subtitle: '', body: 'Precios de temporada.',
      image_url: MANAGED(2), cta_label: '', cta_url: '', secondary_cta_label: '', secondary_cta_url: '', product: null,
    };
    renderHome(list([card()]), config({ page: HERO_PAGE, campaigns: { home_hero: campaign } }));
    const hero = screen.getByTestId('v4-hero');
    expect(within(hero).getByRole('heading', { level: 1 })).toHaveTextContent('Campaña de verano');
    expect(within(hero).getByText('Precios de temporada.')).toBeInTheDocument();
    expect(hero.querySelector('img')).toHaveAttribute('alt', 'Campaña de verano');
  });

  it('sin modelos, el hero no lleva un botón que no lleva a nada', () => {
    renderHome(list([]), config({ page: HERO_PAGE }));
    expect(within(screen.getByTestId('v4-hero')).queryByRole('link')).toBeNull();
  });
});

describe('la rejilla de modelos', () => {
  it('cada modelo enlaza a su ficha V4 con su precio real', () => {
    renderHome(list([card(), card({ slug: 'equipo-y', name: 'Equipo Y', from_price: '2499.50', category: null })]));
    const grid = screen.getByRole('list', { name: 'Modelos' });
    const links = within(grid).getAllByRole('link');
    expect(links.map((l) => l.getAttribute('href'))).toEqual(['/v4/modelo/equipo-x', '/v4/modelo/equipo-y']);
    expect(links[0]).toHaveTextContent('Equipo X');
    expect(links[0]).toHaveTextContent('Desde S/ 3999.00');
    expect(links[0]).toHaveTextContent('Teléfonos');
    expect(links[1]).toHaveTextContent('Desde S/ 2499.50');
  });

  it('un modelo sin stock dice «Agotado» y no muestra precio', () => {
    renderHome(list([card({ sold_out: true })]));
    const link = within(screen.getByRole('list', { name: 'Modelos' })).getByRole('link');
    expect(link).toHaveTextContent('Agotado');
    expect(link).not.toHaveTextContent('S/');
  });

  it('sin precio publicado no escribe ninguno', () => {
    renderHome(list([card({ from_price: null })]));
    const link = within(screen.getByRole('list', { name: 'Modelos' })).getByRole('link');
    expect(link).not.toHaveTextContent('S/');
    expect(link).not.toHaveTextContent('Agotado');
  });

  it('las líneas con los nombres de la empresa', () => {
    renderHome(list([card({ has_new: true, has_used: true })]));
    const link = within(screen.getByRole('list', { name: 'Modelos' })).getByRole('link');
    expect(link).toHaveTextContent('De caja');
    expect(link).toHaveTextContent('Reacondicionados');
  });

  it('una foto que no subió la tienda dice «Imagen referencial»', () => {
    renderHome(list([card(), card({ slug: 'propia', name: 'Propia', image_url: MANAGED(3) })]));
    const links = within(screen.getByRole('list', { name: 'Modelos' })).getAllByRole('link');
    expect(links[0]).toHaveTextContent('Imagen referencial');
    expect(links[1]).not.toHaveTextContent('Imagen referencial');
  });

  it('el slug se codifica en el enlace', () => {
    renderHome(list([card({ slug: 'a b' })]));
    expect(within(screen.getByRole('list', { name: 'Modelos' })).getByRole('link'))
      .toHaveAttribute('href', '/v4/modelo/a%20b');
  });

  it('sin modelos publicados, lo dice', () => {
    renderHome(list([]));
    expect(screen.getByText('Aún no hay modelos publicados.')).toBeInTheDocument();
  });

  it('si no se pudo leer el catálogo, lo dice', () => {
    renderHome(null);
    expect(screen.getByRole('alert')).toHaveTextContent('No se pudieron cargar los modelos.');
  });
});

describe('campañas', () => {
  const promo = (slot: string, extra: Record<string, unknown> = {}) => ({
    slot, badge: '', title: `Promo ${slot}`, subtitle: '', body: 'Detalle.', image_url: '', cta_label: 'Ver',
    cta_url: '/v4/modelo/equipo-x', secondary_cta_label: '', secondary_cta_url: '', product: null, ...extra,
  });

  it('las campañas vigentes de la portada se muestran con sus textos', () => {
    renderHome(list([card()]), config({ campaigns: { home_featured: promo('home_featured'), home_bottom_promo: promo('home_bottom_promo') } }));
    const section = screen.getByRole('region', { name: 'Campañas' });
    expect(within(section).getByRole('heading', { name: 'Promo home_featured' })).toBeInTheDocument();
    expect(within(section).getByRole('heading', { name: 'Promo home_bottom_promo' })).toBeInTheDocument();
    expect(within(section).getAllByRole('link', { name: 'Ver' })[0]).toHaveAttribute('href', '/v4/modelo/equipo-x');
  });

  it('sin campañas no hay sección', () => {
    renderHome(list([card()]));
    expect(screen.queryByRole('region', { name: 'Campañas' })).toBeNull();
  });

  it('una campaña sin título no se muestra', () => {
    renderHome(list([card()]), config({ campaigns: { home_promo: promo('home_promo', { title: '' }) } }));
    expect(screen.queryByRole('region', { name: 'Campañas' })).toBeNull();
  });
});

describe('nada inventado', () => {
  const VETOED = [/oficial/i, /100\s*%/, /impecable/i, /garantizad/i];

  it('ni reseñas, ni horarios, ni direcciones, ni «La Manada»', () => {
    const { container } = renderHome(
      list([card(), card({ slug: 'b', sold_out: true })]),
      config({ page: HERO_PAGE, contact: { ...NEUTRAL_CONFIG.contact, address: 'Av. Siempre Viva 123', city: 'Lima' } }),
    );
    const text = container.textContent ?? '';
    for (const word of [/reseña/i, /estrellas/i, /horario/i, /\b\d{1,2}:\d{2}\b/, /Av\. Siempre Viva/, /Manada/i, /unidades/i]) {
      expect(text).not.toMatch(word);
    }
    for (const word of VETOED) expect(text).not.toMatch(word);
    expect(text).not.toMatch(/garant/i);
  });
});

describe('/v4 no se indexa y no tiene canónica', () => {
  // P2 · F5a — mientras la tienda esté en V3 (lo de siempre). Con V4 elegido en el panel, la portada V4 se
  // indexa con canónica: `storefront-design-switch.test.ts`.
  it('la metadata de la portada, con la tienda en V3', async () => {
    const realFetch = global.fetch;
    global.fetch = jest.fn(async () => ({ ok: true, status: 200, json: async () => config() })) as unknown as typeof fetch;
    try {
      const { generateMetadata } = await import('@/app/v4/page');
      const metadata = await generateMetadata();
      expect(metadata.robots).toEqual({ index: false, follow: false });
      expect(metadata.alternates).toBeUndefined();
    } finally {
      global.fetch = realFetch;
    }
  });
});

// ---------------------------------------------------------------------------
// Cabecera
// ---------------------------------------------------------------------------

const LOGOS = {
  primary_on_light: '/norte/v-light.png', primary_on_dark: '/norte/v-dark.png',
  horizontal_on_light: '/norte/h-light.png', horizontal_on_dark: '/norte/h-dark.png',
  isotype_on_light: '/norte/i-light.png', isotype_on_dark: '/norte/i-dark.png',
};

function renderHeader(extra: Partial<StorefrontConfig> = {}) {
  return render(
    <StorefrontProvider config={config(extra)}>
      <TopBarV4 />
    </StorefrontProvider>,
  );
}

function withLogos(logos: Partial<typeof LOGOS>, logo_url = ''): Partial<StorefrontConfig> {
  return {
    branding: {
      ...NEUTRAL_CONFIG.branding,
      logo_url,
      logos: { ...NEUTRAL_CONFIG.branding.logos, ...logos },
    },
  };
}

describe('cabecera V4: el logotipo', () => {
  beforeEach(() => { pathname = '/v4'; });

  it('horizontal claro en escritorio (≥ 220 px) e isotipo claro en el móvil', () => {
    renderHeader(withLogos(LOGOS));
    const home = screen.getByRole('link', { name: 'Tienda Norte, inicio' });
    expect(home).toHaveAttribute('href', '/v4');
    const desktop = home.querySelector('[data-logo="desktop"]') as HTMLImageElement;
    const mobile = home.querySelector('[data-logo="mobile"]') as HTMLImageElement;
    expect(desktop).toHaveAttribute('src', '/norte/h-light.png');
    // Which logo shows is said by a wrapper: on the <img> itself, the unlayered `img { display: block }` of
    // globals.css wins over `hidden` and both logos were drawn (jsdom has no cascade; the e2e spec
    // storefront-v4-configurator measures it).
    expect(desktop.parentElement).toHaveAttribute('data-logo-slot', 'desktop');
    expect(desktop.parentElement?.className).toMatch(/\bhidden\b/);
    expect(desktop.parentElement?.className).toMatch(/\blg:block\b/);
    expect(desktop.className).not.toMatch(/(^|\s)([a-z0-9]+:)?(hidden|block)(\s|$)/);
    const width = Number(desktop.className.match(/\bw-\[(\d+)px\]/)?.[1] ?? 0);
    expect(width).toBeGreaterThanOrEqual(220);
    expect(mobile).toHaveAttribute('src', '/norte/i-light.png');
    expect(mobile.parentElement).toHaveAttribute('data-logo-slot', 'mobile');
    expect(mobile.parentElement?.className).toMatch(/\blg:hidden\b/);
    expect(mobile.className).not.toMatch(/(^|\s)([a-z0-9]+:)?(hidden|block)(\s|$)/);
    // La V4 es clara siempre: nunca las variantes para fondo oscuro.
    expect(home.innerHTML).not.toMatch(/-dark\.png/);
    // El nombre lo dice el enlace; las imágenes son decorativas.
    expect(desktop).toHaveAttribute('alt', '');
    expect(mobile).toHaveAttribute('alt', '');
  });

  it('sin isotipo, el móvil usa el horizontal sin deformarlo', () => {
    renderHeader(withLogos({ horizontal_on_light: '/norte/h-light.png' }));
    const mobile = screen.getByRole('link', { name: 'Tienda Norte, inicio' }).querySelector('[data-logo="mobile"]');
    expect(mobile).toHaveAttribute('src', '/norte/h-light.png');
    expect(mobile?.className).toMatch(/\bw-auto\b/);
  });

  it('sin horizontal, el escritorio usa el principal', () => {
    renderHeader(withLogos({ primary_on_light: '/norte/v-light.png' }));
    const desktop = screen.getByRole('link', { name: 'Tienda Norte, inicio' }).querySelector('[data-logo="desktop"]');
    expect(desktop).toHaveAttribute('src', '/norte/v-light.png');
  });

  it('sin logotipos, el nombre de la tienda', () => {
    renderHeader();
    const home = screen.getByRole('link', { name: 'Tienda Norte, inicio' });
    expect(home.querySelector('img')).toBeNull();
    expect(home).toHaveTextContent('Tienda Norte');
  });

  it('sólo con variantes oscuras, el nombre (nunca un logo ilegible)', () => {
    renderHeader(withLogos({ horizontal_on_dark: '/norte/h-dark.png', isotype_on_dark: '/norte/i-dark.png' }));
    const home = screen.getByRole('link', { name: 'Tienda Norte, inicio' });
    expect(home.querySelector('img')).toBeNull();
    expect(home).toHaveTextContent('Tienda Norte');
  });
});

describe('cabecera V4: los enlaces', () => {
  beforeEach(() => { pathname = '/v4'; });

  it('Catálogo y Carrito; Servicio sólo si la tienda publicó servicios', () => {
    const { unmount } = renderHeader();
    const nav = screen.getByRole('navigation', { name: 'Tienda' });
    expect(within(nav).getByRole('link', { name: 'Catálogo' })).toHaveAttribute('href', '/v4');
    expect(within(nav).getByRole('link', { name: 'Carrito' })).toHaveAttribute('href', '/cart');
    expect(within(nav).queryByRole('link', { name: 'Servicio' })).toBeNull();
    unmount();
    renderHeader({ services: [{ title: 'Cambio de pantalla' } as StorefrontConfig['services'][number]] });
    expect(within(screen.getByRole('navigation', { name: 'Tienda' })).getByRole('link', { name: 'Servicio' }))
      .toHaveAttribute('href', '/services');
  });

  it('marca la página actual', () => {
    const { unmount } = renderHeader();
    expect(screen.getByRole('link', { name: 'Catálogo' })).toHaveAttribute('aria-current', 'page');
    unmount();
    pathname = '/v4/modelo/equipo-x';
    renderHeader();
    expect(screen.getByRole('link', { name: 'Catálogo' })).not.toHaveAttribute('aria-current');
  });

  it('se recorre con el teclado en orden: inicio, Catálogo, Servicio, Carrito', async () => {
    const user = userEvent.setup();
    renderHeader({ ...withLogos(LOGOS), services: [{ title: 'X' } as StorefrontConfig['services'][number]] });
    await user.tab();
    expect(screen.getByRole('link', { name: 'Tienda Norte, inicio' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('link', { name: 'Catálogo' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('link', { name: 'Servicio' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('link', { name: 'Carrito' })).toHaveFocus();
  });

  it('el foco visible es el negro de la V4 (≥ 3:1, medido en contrast.test.ts) y el movimiento respeta reduced-motion', () => {
    const css = fs.readFileSync(path.join(process.cwd(), 'app/v4/v4.css'), 'utf8');
    expect(css).toMatch(/\[data-design="v4"\] :is\(a, button[^)]*\):focus-visible\s*\{\s*outline: 2px solid var\(--v4-focus\)/);
    const source = fs.readFileSync(path.join(process.cwd(), 'app/components/v4/TopBarV4.tsx'), 'utf8');
    // Nada de transiciones fuera de las clases V4 que ya se apagan con reduced-motion.
    expect(source).not.toMatch(/\btransition\b|\banimate-|duration-/);
  });
});
