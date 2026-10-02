import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';

import { user } from './support/fixtures';

/**
 * STOREFRONT-V3 · navegación pública.
 *
 * Tres cosas que una tienda cualquiera —no sólo la del piloto— necesita:
 *
 *  · que los enlaces de categoría del pie lleguen al filtro que el catálogo lee
 *    de verdad (`?category=`; el pie escribía `?cat=` y no filtraba nada);
 *  · que el menú móvil diga a un lector de pantalla si está abierto y qué
 *    controla, y que lleve a Contacto y Nosotros;
 *  · que el armazón de la tienda NO se pinte dentro de `/admin`.
 *
 * Las categorías salen del catálogo real de la empresa, no de una lista escrita
 * en el código.
 */

let mockPathname = '/';
jest.mock('next/navigation', () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

const mockGetCurrentUser = jest.fn();
const mockHasInternalAccess = jest.fn();
jest.mock('@/app/lib/auth', () => ({
  getCurrentUser: () => mockGetCurrentUser(),
  hasInternalAccess: () => mockHasInternalAccess(),
  forgetInternalAccess: jest.fn(),
  logout: jest.fn(() => Promise.resolve()),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const { Header } = require('@/app/components/Header');
const { Footer } = require('@/app/components/Footer');
const chrome = require('@/app/components/StorefrontChrome');
const { resetCatalogCategories } = require('@/app/lib/catalog-categories');
/* eslint-enable @typescript-eslint/no-require-imports */

const CATEGORIES = [
  { id: 7, name: 'Laptops', slug: 'laptops' },
  { id: 9, name: 'Audio', slug: 'audio' },
];

const TENANT: StorefrontConfig = {
  ...NEUTRAL_CONFIG,
  company: { ...NEUTRAL_CONFIG.company, name: 'Tienda Norte', slug: 'tienda-norte' },
  contact: { ...NEUTRAL_CONFIG.contact, whatsapp_link: 'https://wa.me/51900000000' },
  policies: {
    warranty_text: '', warranty_url: 'https://norte.example/garantia',
    terms_url: 'https://norte.example/terminos', privacy_url: '',
  },
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
  mockPathname = '/';
  mockGetCurrentUser.mockReset().mockResolvedValue(null);
  mockHasInternalAccess.mockReset().mockResolvedValue(false);
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

describe('pie', () => {
  it('enlaza las categorías reales con el parámetro que lee el catálogo', async () => {
    inStore(<Footer />);
    const laptops = await screen.findByRole('link', { name: 'Laptops' });
    expect(laptops).toHaveAttribute('href', '/product?category=laptops');
    expect(screen.getByRole('link', { name: 'Audio' })).toHaveAttribute('href', '/product?category=audio');

    const hrefs = screen.getAllByRole('link').map((a) => a.getAttribute('href') ?? '');
    expect(hrefs.filter((href) => href.includes('?cat='))).toEqual([]);
  });

  it('no inventa categorías de otra tienda mientras el catálogo no responde', () => {
    inStore(<Footer />);
    for (const pilot of ['iPhones', 'Accesorios', 'Repuestos']) {
      expect(screen.queryByRole('link', { name: pilot })).not.toBeInTheDocument();
    }
  });

  it('lleva a Nosotros y Contacto, y a las políticas que la tienda publicó', async () => {
    inStore(<Footer />);
    expect(screen.getByRole('link', { name: 'Nosotros' })).toHaveAttribute('href', '/about');
    expect(screen.getByRole('link', { name: 'Contacto' })).toHaveAttribute('href', '/contact');

    const legal = screen.getByRole('navigation', { name: 'Información legal' });
    expect(within(legal).getByRole('link', { name: 'Garantía' }))
      .toHaveAttribute('href', 'https://norte.example/garantia');
    expect(within(legal).getByRole('link', { name: 'Términos y condiciones' })).toBeInTheDocument();
    // Lo que la tienda no publicó no se enlaza.
    expect(within(legal).queryByRole('link', { name: 'Privacidad' })).not.toBeInTheDocument();
  });

  it('conserva una acción principal visible', () => {
    inStore(<Footer />);
    expect(screen.getByRole('link', { name: /Escribir al WhatsApp/ }))
      .toHaveAttribute('href', 'https://wa.me/51900000000');
  });
});

describe('cabecera · menú móvil', () => {
  it('dice si está abierto y qué controla', async () => {
    inStore(<Header />);
    const toggle = screen.getByRole('button', { name: 'Abrir menú' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls', 'store-mobile-menu');

    await act(async () => { fireEvent.click(toggle); });

    const open = screen.getByRole('button', { name: 'Cerrar menú' });
    expect(open).toHaveAttribute('aria-expanded', 'true');
    expect(document.getElementById('store-mobile-menu')).toBeInTheDocument();
  });

  it('lleva a Contacto y Nosotros', async () => {
    inStore(<Header />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Abrir menú' })); });
    const menu = document.getElementById('store-mobile-menu') as HTMLElement;
    expect(within(menu).getByRole('link', { name: 'Contacto' })).toHaveAttribute('href', '/contact');
    expect(within(menu).getByRole('link', { name: 'Nosotros' })).toHaveAttribute('href', '/about');
  });

  it('el acceso interno sigue dependiendo del servidor, también en el menú', async () => {
    mockGetCurrentUser.mockResolvedValue(user({ role: 'customer', is_staff: false }));
    mockHasInternalAccess.mockResolvedValue(true);
    inStore(<Header />);
    await waitFor(() => expect(mockHasInternalAccess).toHaveBeenCalled());
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Abrir menú' })); });
    const menu = document.getElementById('store-mobile-menu') as HTMLElement;
    expect(await within(menu).findByRole('link', { name: 'Control interno' })).toHaveAttribute('href', '/admin');
  });
});

describe('armazón de la tienda', () => {
  function Chrome() {
    return (
      <>
        <chrome.StorefrontHeader />
        <chrome.StorefrontContent><p>contenido</p></chrome.StorefrontContent>
        <chrome.StorefrontFooter />
        <chrome.WhatsAppButton />
      </>
    );
  }

  it.each(['/admin', '/admin/sales/pos', '/admin/service/orders'])(
    'no se pinta dentro de %s', (pathname) => {
      mockPathname = pathname;
      inStore(<Chrome />);
      expect(screen.queryByRole('banner')).not.toBeInTheDocument();
      expect(screen.queryByRole('contentinfo')).not.toBeInTheDocument();
      expect(screen.queryByRole('link', { name: 'Contactar por WhatsApp' })).not.toBeInTheDocument();
      // El contenido sigue ahí, marcado como superficie interna.
      expect(screen.getByText('contenido').parentElement).toHaveClass('internal-surface');
      expect(screen.getByText('contenido').parentElement).not.toHaveClass('shop-surface');
    },
  );

  it('se pinta en la tienda y marca su contenido como tal', () => {
    mockPathname = '/product';
    inStore(<Chrome />);
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Contactar por WhatsApp' })).toBeInTheDocument();
    expect(screen.getByText('contenido').parentElement).toHaveClass('shop-surface');
  });
});
