import { fireEvent, render, screen, within } from '@testing-library/react';

import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { dashboard, user } from './support/fixtures';

/**
 * MODULE-CATALOGUE — el tablero separa las herramientas de lo que viene.
 *
 * «Herramientas operativas» son los módulos que existen y a los que se llega.
 * «En desarrollo / Próximamente» va abajo, cerrado, sin enlaces a lo que no
 * existe, y sólo para quien administra la empresa: a un vendedor no le sirve.
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

function mount(capabilities: string[]) {
  (globalThis as { __ctx?: InternalContext }).__ctx = {
    user: user(), dashboard: dashboard(capabilities), reload: jest.fn(), selectCompany: jest.fn(),
  } as unknown as InternalContext;
  render(<AdminHome />);
}

const roadmapHeading = () => screen.queryByRole('heading', { name: 'En desarrollo / Próximamente' });

test('a seller sees the operational tools and no roadmap', () => {
  mount(['sales.pos.use', 'sales.orders.view']);
  expect(screen.getByRole('heading', { name: 'Herramientas operativas' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /Punto de venta/ })).toHaveAttribute('href', '/admin/sales/pos');
  expect(roadmapHeading()).not.toBeInTheDocument();
  expect(screen.queryByText('Cotizaciones')).not.toBeInTheDocument();
  expect(screen.queryByText('Cobertura de módulos')).not.toBeInTheDocument();
});

test('a company administrator has it collapsed, below the tools', () => {
  mount(['company.manage', 'company.view']);
  const heading = roadmapHeading()!;
  expect(heading).toBeInTheDocument();
  const tools = screen.getByRole('heading', { name: 'Herramientas operativas' });
  expect(tools.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const toggle = screen.getByRole('button', { name: 'Ver lista' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByText('Cotizaciones')).not.toBeInTheDocument();
});

test('opened, it lists what is not finished, and none of it is a link', () => {
  mount(['company.manage', 'company.view']);
  fireEvent.click(screen.getByRole('button', { name: 'Ver lista' }));
  const section = screen.getByRole('region', { name: 'En desarrollo / Próximamente' });
  for (const label of ['Cotizaciones', 'Cuentas por pagar', 'Facturación electrónica', 'Garantías']) {
    expect(within(section).getByText(label)).toBeInTheDocument();
  }
  // Only unfinished modules: an existing one such as «Punto de venta» is not here.
  expect(within(section).queryByText('Punto de venta')).not.toBeInTheDocument();
  expect(within(section).queryAllByRole('link')).toEqual([]);
  expect(screen.getByRole('button', { name: 'Ocultar' })).toHaveAttribute('aria-expanded', 'true');
});
