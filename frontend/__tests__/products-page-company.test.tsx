/**
 * INVENTORY-UNIFIED-01, revisión N5 · la pantalla de productos actúa sobre la
 * empresa para la que el servidor decidió su acceso, no sobre la que el
 * navegador tenga guardada al enviar cada petición.
 */
import { act, render, screen } from '@testing-library/react';

import AdminProductsPage from '@/app/admin/products/page';
import * as admin from '@/app/lib/admin';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
  fetchAdminCategories: jest.fn(),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

let access: { user: unknown; dashboard: unknown; can: jest.Mock };
jest.mock('@/app/admin/components/AccessGuard', () => ({
  AccessGuard: ({ children }: { children: (value: unknown) => React.ReactNode }) => <>{children(access)}</>,
}));

const api = admin as jest.Mocked<typeof admin>;

beforeEach(() => {
  api.fetchAdminProducts.mockReset().mockResolvedValue({ count: 0, page: 1, page_size: 20, results: [] });
  api.fetchAdminCategories.mockReset().mockResolvedValue([]);
});

it('pide la lista para la empresa que resolvió el servidor, aunque el navegador guarde otra', async () => {
  window.localStorage.setItem('internal-selected-company', '9');
  access = { user: { username: 'ana' }, dashboard: { company: { id: 7, name: 'Tienda A' } }, can: jest.fn(() => true) };

  await act(async () => { render(<AdminProductsPage />); });
  await screen.findByText('No hay productos que coincidan.');

  expect(api.fetchAdminProducts.mock.calls[0][0]).toMatchObject({ company: 7 });
  expect(access.can).toHaveBeenCalledWith('products.manage', ['admin', 'superadmin']);
  window.localStorage.removeItem('internal-selected-company');
});

it('un operador sin empresa resuelta no manda ninguna', async () => {
  access = { user: { username: 'ana' }, dashboard: null, can: jest.fn(() => true) };
  await act(async () => { render(<AdminProductsPage />); });
  await screen.findByText('No hay productos que coincidan.');
  expect(api.fetchAdminProducts.mock.calls[0][0]?.company).toBeUndefined();
});
