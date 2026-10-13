import { render, screen, waitFor } from '@testing-library/react';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

/**
 * INVENTORY-UNIFIED-01, revisión (h) · el contador del carrito en la cabecera.
 *
 * Una línea de un producto que dejó de venderse sigue en el carrito hasta que
 * alguien la quita, pero no es algo que se vaya a comprar: no se cuenta.
 */

jest.mock('@/app/lib/auth', () => ({
  getCurrentUser: () => Promise.resolve(null),
  hasInternalAccess: () => Promise.resolve(false),
  forgetInternalAccess: jest.fn(),
  logout: jest.fn(() => Promise.resolve()),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { Header } = require('@/app/components/Header');

beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {} }),
    });
  }
});

function cartIs(lines: unknown[]) {
  global.fetch = jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(lines) })) as unknown as typeof fetch;
}

function renderHeader() {
  return render(
    <ThemeProvider>
      <StorefrontProvider config={NEUTRAL_CONFIG}>
        <Header />
      </StorefrontProvider>
    </ThemeProvider>,
  );
}

const line = (id: number, available?: boolean) => ({ id, quantity: 1, ...(available === undefined ? {} : { available }), product: { id, name: `P${id}` } });

it('cuenta lo que se puede comprar, no lo que ya no está disponible', async () => {
  cartIs([line(1, true), line(2, false), line(3, true)]);
  renderHeader();
  await waitFor(() => expect(screen.getByRole('link', { name: 'Carrito, 2 artículos' })).toBeInTheDocument());
});

it('si sólo queda lo que ya no se vende, no hay contador', async () => {
  cartIs([line(2, false)]);
  renderHeader();
  await waitFor(() => expect(global.fetch).toHaveBeenCalled());
  // Los dos enlaces al carrito (escritorio y móvil), y ninguno con contador.
  expect((await screen.findAllByRole('link', { name: 'Carrito' })).length).toBe(2);
  expect(screen.queryByRole('link', { name: /Carrito, / })).toBeNull();
});

it('una respuesta de antes, sin la marca, cuenta como siempre', async () => {
  cartIs([line(1), line(2)]);
  renderHeader();
  await waitFor(() => expect(screen.getByRole('link', { name: 'Carrito, 2 artículos' })).toBeInTheDocument());
});
