import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';

/**
 * Cabecera y pie V4 — lo que faltaba para que se puedan usar sin ratón.
 *
 * El diseño no cambia qué hay en la cabecera ni en el pie: todo sigue saliendo
 * de la configuración de la tienda. Lo que se fija aquí son los defectos reales
 * que tenía el armazón anterior:
 *
 *  · las categorías sólo se abrían al pasar el ratón: con teclado, con lector
 *    de pantalla o con el dedo no existían;
 *  · el carrito del móvil era un icono sin nombre;
 *  · el menú móvil no se cerraba con Escape;
 *  · el pie pintaba la marca dos veces, una encima de la otra.
 */

jest.mock('next/navigation', () => ({
  usePathname: () => '/',
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

jest.mock('@/app/lib/auth', () => ({
  getCurrentUser: () => Promise.resolve(null),
  hasInternalAccess: () => Promise.resolve(false),
  forgetInternalAccess: jest.fn(),
  logout: jest.fn(() => Promise.resolve()),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const { Header } = require('@/app/components/Header');
const { Footer } = require('@/app/components/Footer');
const { resetCatalogCategories } = require('@/app/lib/catalog-categories');
/* eslint-enable @typescript-eslint/no-require-imports */

const CATEGORIES = [
  { id: 7, name: 'Laptops', slug: 'laptops' },
  { id: 9, name: 'Audio', slug: 'audio' },
];

const TENANT: StorefrontConfig = {
  ...NEUTRAL_CONFIG,
  company: { ...NEUTRAL_CONFIG.company, name: 'Tienda Norte', slug: 'tienda-norte' },
  branding: {
    ...NEUTRAL_CONFIG.branding,
    logo_url: '/norte/logo.png',
    logos: {
      primary_on_light: '/norte/v-light.png', primary_on_dark: '/norte/v-dark.png',
      horizontal_on_light: '/norte/h-light.png', horizontal_on_dark: '/norte/h-dark.png',
      isotype_on_light: '/norte/i-light.png', isotype_on_dark: '/norte/i-dark.png',
    },
  },
  contact: {
    ...NEUTRAL_CONFIG.contact,
    whatsapp_link: 'https://wa.me/51900000000',
    facebook_url: 'https://facebook.example/norte',
    instagram_url: 'https://instagram.example/norte',
  },
  services: [{
    title: 'Cambio de pantalla', description: '', devices_text: '', estimated_time_text: '', highlight: '',
  }],
};

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
  global.fetch = jest.fn((input: RequestInfo | URL) => {
    const isCategories = String(input).includes('/categories');
    return Promise.resolve({
      ok: isCategories, status: isCategories ? 200 : 404,
      json: () => Promise.resolve(isCategories ? CATEGORIES : []),
    });
  }) as unknown as typeof fetch;
});

function inStore(node: React.ReactNode, config: StorefrontConfig = TENANT) {
  return render(
    <ThemeProvider>
      <StorefrontProvider config={config}>{node}</StorefrontProvider>
    </ThemeProvider>,
  );
}

/** Monta y deja terminar la carga inicial (sesión, carrito, categorías). */
async function mount(node: React.ReactNode, config: StorefrontConfig = TENANT) {
  const view = inStore(node, config);
  await act(async () => {});
  return view;
}

async function categoriesToggle() {
  // El botón sólo existe cuando la tienda tiene categorías que mostrar.
  return screen.findByRole('button', { name: 'Categorías del catálogo' });
}

describe('cabecera · categorías sin ratón', () => {
  it('el menú de categorías es un botón que dice si está abierto y qué controla', async () => {
    await mount(<Header />);
    const toggle = await categoriesToggle();

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls', 'store-catalog-menu');
    expect(document.getElementById('store-catalog-menu')).toBeNull();
  });

  it('se abre con el teclado y lleva al filtro de cada categoría', async () => {
    const keyboard = userEvent.setup();
    await mount(<Header />);
    const toggle = await categoriesToggle();

    toggle.focus();
    await keyboard.keyboard('{Enter}');

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const menu = document.getElementById('store-catalog-menu') as HTMLElement;
    expect(within(menu).getByRole('link', { name: 'Laptops' })).toHaveAttribute('href', '/product?category=laptops');
    expect(within(menu).getByRole('link', { name: 'Audio' })).toHaveAttribute('href', '/product?category=audio');
    expect(within(menu).getByRole('link', { name: /Ver todo/ })).toHaveAttribute('href', '/product');
  });

  it('Escape lo cierra y devuelve el foco al botón', async () => {
    const keyboard = userEvent.setup();
    await mount(<Header />);
    const toggle = await categoriesToggle();
    await keyboard.click(toggle);
    const first = within(document.getElementById('store-catalog-menu') as HTMLElement)
      .getByRole('link', { name: 'Laptops' });
    first.focus();

    await keyboard.keyboard('{Escape}');

    expect(document.getElementById('store-catalog-menu')).toBeNull();
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
  });

  it('se cierra cuando el foco sale de él', async () => {
    const keyboard = userEvent.setup();
    await mount(<Header />);
    const toggle = await categoriesToggle();
    await keyboard.click(toggle);
    expect(document.getElementById('store-catalog-menu')).not.toBeNull();

    // El siguiente control de la cabecera, fuera del menú.
    act(() => { screen.getAllByRole('link', { name: 'Contacto' })[0].focus(); });

    await waitFor(() => expect(document.getElementById('store-catalog-menu')).toBeNull());
  });

  it('una tienda sin categorías no muestra un botón que no abre nada', async () => {
    global.fetch = jest.fn(() => Promise.resolve({
      ok: true, status: 200, json: () => Promise.resolve([]),
    })) as unknown as typeof fetch;
    await mount(<Header />);

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Categorías del catálogo' })).toBeNull();
    expect(screen.getAllByRole('link', { name: 'Catálogo' })[0]).toHaveAttribute('href', '/product');
  });
});

describe('cabecera · móvil', () => {
  it('el carrito tiene nombre aunque sólo se vea el icono', async () => {
    await mount(<Header />);
    const carts = screen.getAllByRole('link', { name: /Carrito/ });

    // El de escritorio y el del móvil: ninguno es un enlace sin nombre.
    expect(carts).toHaveLength(2);
    for (const cart of carts) expect(cart).toHaveAttribute('href', '/cart');
    expect(screen.getAllByRole('link').filter((link) => !link.textContent?.trim() && !link.getAttribute('aria-label')))
      .toEqual([]);
  });

  it('Escape cierra el menú y devuelve el foco a su botón', async () => {
    const keyboard = userEvent.setup();
    await mount(<Header />);
    await keyboard.click(screen.getByRole('button', { name: 'Abrir menú' }));
    expect(document.getElementById('store-mobile-menu')).not.toBeNull();

    await keyboard.keyboard('{Escape}');

    expect(document.getElementById('store-mobile-menu')).toBeNull();
    expect(screen.getByRole('button', { name: 'Abrir menú' })).toHaveFocus();
  });

  it('los controles del móvil miden lo que un dedo necesita', async () => {
    await mount(<Header />);
    const controls = [
      screen.getByRole('button', { name: 'Abrir menú' }),
      screen.getAllByRole('link', { name: /Carrito/ })[1],
    ];
    for (const control of controls) {
      expect(control.className).toContain('min-h-11');
      expect(control.className).toContain('min-w-11');
    }
  });
});

describe('pie', () => {
  it('pinta la marca una sola vez', async () => {
    const { container } = await mount(<Footer />);
    expect(container.querySelectorAll('footer img')).toHaveLength(1);
  });

  it('agrupa sus enlaces en navegaciones con nombre', async () => {
    await mount(<Footer />);
    const shop = screen.getByRole('navigation', { name: 'Tienda' });
    expect(await within(shop).findByRole('link', { name: 'Laptops' }))
      .toHaveAttribute('href', '/product?category=laptops');
    expect(within(shop).getByRole('link', { name: 'Catálogo' })).toHaveAttribute('href', '/product');

    const services = screen.getByRole('navigation', { name: 'Servicios' });
    expect(within(services).getByRole('link', { name: 'Cambio de pantalla' })).toHaveAttribute('href', '/services');
  });

  it('las redes de la tienda se pueden tocar con el dedo y dicen que abren otra pestaña', async () => {
    await mount(<Footer />);
    for (const name of ['Facebook', 'Instagram']) {
      const link = screen.getByRole('link', { name: new RegExp(name) });
      expect(link.className).toContain('h-11');
      expect(link.className).toContain('w-11');
      expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    }
  });

  it('sin redes ni servicios publicados no deja bloques vacíos', async () => {
    await mount(<Footer />, { ...TENANT, services: [], contact: { ...NEUTRAL_CONFIG.contact } });

    expect(screen.queryByRole('navigation', { name: 'Servicios' })).toBeNull();
    expect(screen.queryByRole('link', { name: /Facebook|Instagram/ })).toBeNull();
    expect(screen.queryByRole('list', { name: 'Redes sociales' })).toBeNull();
  });
});
