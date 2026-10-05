import { render, screen, within } from '@testing-library/react';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

/**
 * STOREFRONT-CATEGORIES · qué familias ilustra la portada.
 *
 * Las decide cada tienda desde su panel: cuáles salen en la portada y en qué
 * orden. Aquí no hay una lista escrita a mano ni un orden propio: se dibuja lo
 * que la API devuelve, en el orden en que lo devuelve.
 */

jest.mock('next/navigation', () => ({
  usePathname: () => '/',
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const Home = require('@/app/page').default;
const { resetCatalogCategories } = require('@/app/lib/catalog-categories');
/* eslint-enable @typescript-eslint/no-require-imports */

let categories: unknown[];

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
    const url = String(input);
    const body = url.includes('/categories') ? categories : [];
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  }) as unknown as typeof fetch;
});

function mount() {
  return render(
    <ThemeProvider>
      <StorefrontProvider config={NEUTRAL_CONFIG}><Home /></StorefrontProvider>
    </ThemeProvider>,
  );
}

async function families() {
  const heading = await screen.findByRole('heading', { name: 'Encuentra lo que necesitas.' });
  const section = heading.closest('section') as HTMLElement;
  return within(section).getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
}

test('the home illustrates the families the shop chose, in the shop\'s order', async () => {
  categories = [
    { id: 3, name: 'Teléfonos', slug: 'telefonos', show_on_home: true },
    { id: 1, name: 'Laptops', slug: 'laptops', show_on_home: false },
    { id: 2, name: 'Accesorios', slug: 'accesorios', show_on_home: true },
    { id: 4, name: 'Audio', slug: 'audio', show_on_home: true },
  ];
  mount();

  expect(await families()).toEqual(['Teléfonos', 'Accesorios', 'Audio']);
});

test('a server that does not say is not read as "hidden"', async () => {
  categories = [{ id: 1, name: 'Laptops', slug: 'laptops' }, { id: 2, name: 'Audio', slug: 'audio' }];
  mount();
  expect(await families()).toEqual(['Laptops', 'Audio']);
});

test('with every family off the home, the section is not drawn empty', async () => {
  categories = [{ id: 1, name: 'Laptops', slug: 'laptops', show_on_home: false }];
  mount();

  // La categoría sigue existiendo (el hero la ofrece como acceso): lo que no
  // hay es una sección de familias con cero familias.
  await screen.findAllByText('Laptops');
  expect(screen.queryByRole('heading', { name: 'Encuentra lo que necesitas.' })).toBeNull();
});
