import { render, screen } from '@testing-library/react';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';

/**
 * STOREFRONT-V3 · Nosotros y Contacto.
 *
 * Dos páginas que sólo dicen lo que la tienda publicó: su nombre, dónde está,
 * cómo escribirle, qué políticas tiene. Nada de esto se redacta aquí — una
 * frase escrita en el código es la frase de UNA tienda puesta en todas.
 */

/* eslint-disable @typescript-eslint/no-require-imports */
const { StoreInformation } = require('@/app/components/StoreInformation');
/* eslint-enable @typescript-eslint/no-require-imports */

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

const STORE: StorefrontConfig = {
  ...NEUTRAL_CONFIG,
  company: { ...NEUTRAL_CONFIG.company, name: 'Tienda Norte', slug: 'tienda-norte' },
  contact: {
    ...NEUTRAL_CONFIG.contact,
    address: 'Av. Larco 100', city: 'Trujillo', phone: '+51 900 000 000',
    email: 'hola@norte.example', whatsapp_link: 'https://wa.me/51900000000',
    instagram_url: 'https://instagram.com/tiendanorte',
  },
  policies: {
    warranty_text: 'Seis meses en equipos revisados.', warranty_url: 'https://norte.example/garantia',
    terms_url: '', privacy_url: '',
  },
  services: [{ title: 'Cambio de pantalla', description: 'Con diagnóstico previo.', devices_text: '', estimated_time_text: '', highlight: '' }],
};

function inStore(node: React.ReactNode, config: StorefrontConfig) {
  return render(
    <ThemeProvider>
      <StorefrontProvider config={config}>{node}</StorefrontProvider>
    </ThemeProvider>,
  );
}

const PILOT = /black dog|iphone|ipad|apple|\bmac\b|arequipa/i;

describe.each([
  ['Nosotros', false],
  ['Contacto', true],
])('%s', (_name, contactPage) => {
  it('muestra los datos que la tienda publicó', () => {
    inStore(<StoreInformation contactPage={contactPage} />, STORE);
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
    expect(screen.getByText('Av. Larco 100, Trujillo')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '+51 900 000 000' })).toHaveAttribute('href', 'tel:+51900000000');
    expect(screen.getByRole('link', { name: 'hola@norte.example' })).toHaveAttribute('href', 'mailto:hola@norte.example');
    expect(screen.getByRole('link', { name: /WhatsApp/ })).toHaveAttribute('href', 'https://wa.me/51900000000');
    const map = screen.getByRole('link', { name: /Cómo llegar/ });
    expect(map.getAttribute('href')).toContain(encodeURIComponent('Av. Larco 100, Trujillo'));
    expect(map).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('con una tienda sin datos lo dice, y no inventa ninguno', () => {
    const { container } = inStore(
      <StoreInformation contactPage={contactPage} />,
      { ...NEUTRAL_CONFIG, company: { ...NEUTRAL_CONFIG.company, name: 'Vacía' } },
    );
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
    expect(screen.getByText('La tienda todavía no ha publicado sus datos de contacto.')).toBeInTheDocument();
    expect(container.querySelectorAll('a[href^="tel:"], a[href^="mailto:"], a[href*="wa.me"], a[href*="maps"]')).toHaveLength(0);
    expect(screen.queryByText(/garantía/i)).not.toBeInTheDocument();
  });

  it('no escribe nada del piloto en otra tienda', () => {
    const { container } = inStore(<StoreInformation contactPage={contactPage} />, STORE);
    expect(container.textContent ?? '').not.toMatch(PILOT);
  });
});

describe('diferencias entre las dos páginas', () => {
  it('Nosotros enseña los servicios publicados; Contacto no', () => {
    const about = inStore(<StoreInformation />, STORE);
    expect(screen.getByText('Cambio de pantalla')).toBeInTheDocument();
    about.unmount();
    inStore(<StoreInformation contactPage />, STORE);
    expect(screen.queryByText('Cambio de pantalla')).not.toBeInTheDocument();
  });

  it('la garantía se muestra con el texto de la tienda, nunca con uno por defecto', () => {
    inStore(<StoreInformation />, STORE);
    expect(screen.getByText('Seis meses en equipos revisados.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Garantía/ })).toHaveAttribute('href', 'https://norte.example/garantia');
  });
});
