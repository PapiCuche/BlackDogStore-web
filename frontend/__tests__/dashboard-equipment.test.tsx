import { render, screen } from '@testing-library/react';

import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { dashboard, user } from './support/fixtures';

/**
 * SERIALIZED-STOCK · «Equipos disponibles» en el tablero.
 *
 * La cifra es del servidor y es un SUBCONJUNTO de las unidades en stock: un
 * equipo disponible ya está contado en ellas. La tarjeta lo dice, para que
 * nadie sume las dos.
 */

jest.mock('next/navigation', () => ({
  usePathname: () => '/admin',
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: InternalContext) => React.ReactNode }) =>
    children((globalThis as { __ctx?: InternalContext }).__ctx as InternalContext),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  ...jest.requireActual('@/app/admin/components/AdminShell'),
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
jest.mock('@/app/components/BrandLogo', () => ({ BrandLogo: () => null }));

/* eslint-disable @typescript-eslint/no-require-imports */
const AdminHome = require('@/app/admin/page').default;
/* eslint-enable @typescript-eslint/no-require-imports */

function mount(equipment: { available: number; reserved: number }) {
  (globalThis as { __ctx?: InternalContext }).__ctx = {
    user: user(),
    dashboard: dashboard(['inventory.view'], {
      inventory: {
        has_branch_access: true, branches: [{ id: 11, name: 'Sucursal Centro' }],
        total_units: 40, out_of_stock_count: 1, low_stock_count: 2, stocked_count: 9,
        inventory_value: '1000.00', value_basis: 'sale_price', transfers_in_transit: 0,
        pending_counts: 0, equipment_available: equipment.available,
        equipment_reserved: equipment.reserved, stock_by_branch: [], low_stock_by_branch: [],
      },
    }),
    reload: jest.fn(), selectCompany: jest.fn(),
  } as unknown as InternalContext;
  render(<AdminHome />);
}

test('the dashboard shows the available devices the server counted, as part of the units', () => {
  mount({ available: 12, reserved: 0 });

  const card = screen.getByText('Equipos disponibles').closest('div') as HTMLElement;
  expect(card.parentElement?.textContent).toContain('12');
  expect(screen.getByText('Con serie, incluidos en las unidades')).toBeInTheDocument();
});

test('devices set aside are mentioned and not added', () => {
  mount({ available: 12, reserved: 3 });
  expect(screen.getByText(/incluidos en las unidades · 3 apartados/)).toBeInTheDocument();
});
