import { act, render, screen, within } from '@testing-library/react';
import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { INTERNAL_MODULES } from '@/app/admin/lib/internal-modules';
import { fetchWithAuth } from '@/app/lib/auth';
import { dashboard, user } from './support/fixtures';

/**
 * SVC-NAV-01. The six technical-service modules all pointed at `/admin/service`,
 * so they opened the same screen and the sidebar lit all of them at once. Each
 * now has its own route and its own queue; `/admin/service` stays as a redirect.
 *
 * Navigation is not authorisation: the capability each module needs is the same
 * as before, and the server still decides every request.
 */

let mockPathname = '/admin';
const mockRedirect = jest.fn();
jest.mock('next/navigation', () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  redirect: (...args: unknown[]) => mockRedirect(...args),
}));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: InternalContext) => React.ReactNode }) =>
    children((globalThis as { __ctx?: InternalContext }).__ctx as InternalContext),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
jest.mock('@/app/components/BrandLogo', () => ({ BrandLogo: () => null }));

/* eslint-disable @typescript-eslint/no-require-imports */
const { InternalSidebarContent } = require('@/app/admin/components/InternalSidebar');
/* eslint-enable @typescript-eslint/no-require-imports */

const EXPECTED: Record<string, { href: string; capability: string }> = {
  'service.intake': { href: '/admin/service/intake', capability: 'service.orders.create' },
  'service.orders': { href: '/admin/service/orders', capability: 'service.orders.view' },
  'service.diagnostic': { href: '/admin/service/diagnostics', capability: 'service.diagnostic.manage' },
  'service.repair': { href: '/admin/service/repairs', capability: 'service.repair.manage' },
  'service.quality': { href: '/admin/service/quality', capability: 'service.quality.manage' },
  'service.delivery': { href: '/admin/service/delivery', capability: 'service.delivery.manage' },
};

const SERVICE_CAPS = Object.values(EXPECTED).map((m) => m.capability);

describe('registro de módulos', () => {
  it('cada módulo de servicio tiene su propia ruta', () => {
    for (const [id, expected] of Object.entries(EXPECTED)) {
      const found = INTERNAL_MODULES.find((m) => m.id === id);
      expect(found?.href).toBe(expected.href);
    }
    const hrefs = Object.keys(EXPECTED).map((id) => INTERNAL_MODULES.find((m) => m.id === id)?.href);
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });

  it('la capacidad que exige cada módulo no cambia', () => {
    for (const [id, expected] of Object.entries(EXPECTED)) {
      const found = INTERNAL_MODULES.find((m) => m.id === id);
      expect(found?.requiredCapabilities).toEqual([expected.capability]);
    }
  });
});

describe('barra lateral', () => {
  const access = {
    capabilities: SERVICE_CAPS, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
  };

  function activeLinks() {
    const nav = screen.getByRole('navigation', { name: 'Módulos del control interno' });
    return within(nav).getAllByRole('link').filter((a) => a.getAttribute('aria-current') === 'page');
  }

  it.each([
    ['/admin/service/repairs', 'Reparación'],
    ['/admin/service/intake', 'Recepción'],
    ['/admin/service/quality', 'Control de calidad'],
    // El detalle de una orden pertenece a «Órdenes», y sólo a ella.
    ['/admin/service/orders/12', 'Órdenes'],
  ])('en %s sólo «%s» está activa', (pathname, label) => {
    mockPathname = pathname;
    render(<InternalSidebarContent access={access} companyName="Taller" />);
    expect(activeLinks().map((a) => a.textContent)).toEqual([label]);
  });

  it('sin la capacidad el módulo no se ofrece', () => {
    mockPathname = '/admin/service/orders';
    render(
      <InternalSidebarContent
        access={{ ...access, capabilities: ['service.orders.view'] }}
        companyName="Taller"
      />,
    );
    expect(screen.getByRole('link', { name: 'Órdenes' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Reparación' })).not.toBeInTheDocument();
  });
});

describe('rutas', () => {
  const requested: string[] = [];

  beforeEach(() => {
    requested.length = 0;
    mockRedirect.mockClear();
    (globalThis as { __ctx?: InternalContext }).__ctx = {
      user: user(),
      dashboard: dashboard(['service.orders.view', 'service.orders.create',
        'service.customers.view', 'service.devices.view', ...SERVICE_CAPS]),
      selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn(),
    };
    jest.mocked(fetchWithAuth).mockImplementation(async (input) => {
      const path = String(input);
      requested.push(path);
      const body = path.includes('/service/context')
        ? { statuses: [
            { code: 'received', label: 'Recibido' }, { code: 'diagnosing', label: 'En diagnóstico' },
            { code: 'waiting_approval', label: 'Esperando aprobación' }, { code: 'approved', label: 'Aprobado' },
            { code: 'in_repair', label: 'En reparación' }, { code: 'waiting_parts', label: 'Esperando repuestos' },
            { code: 'repaired', label: 'Reparado' }, { code: 'quality_control', label: 'En control de calidad' },
            { code: 'ready_for_pickup', label: 'Listo para recoger' }, { code: 'delivered', label: 'Entregado' },
          ], available_branches: [{ id: 11, name: 'Centro' }], device_types: [] }
        : { count: 0, results: [] };
      return { ok: true, status: 200, json: async () => body } as Response;
    });
  });

  const ordersRequest = () => requested.find((p) => p.includes('/service/orders/'));

  it('/admin/service redirige a /admin/service/orders', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const Root = require('@/app/admin/service/page').default;
    try { Root(); } catch { /* el redirect real lanza; el doble no */ }
    expect(mockRedirect).toHaveBeenCalledWith('/admin/service/orders');
  });

  it.each([
    ['intake', 'Recepción', 'status=received'],
    ['orders', 'Órdenes de servicio', null],
    ['diagnostics', 'Diagnóstico', 'status=diagnosing%2Cwaiting_approval'],
    ['repairs', 'Reparación', 'status=approved%2Cin_repair%2Cwaiting_parts'],
    ['quality', 'Control de calidad', 'status=repaired%2Cquality_control'],
    ['delivery', 'Entrega', 'status=ready_for_pickup'],
  ])('/admin/service/%s muestra «%s» y pide su cola', async (route, title, statusQuery) => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const Page = require(`@/app/admin/service/${route}/page`).default;
    await act(async () => { render(<Page />); });
    expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument();
    const url = ordersRequest();
    expect(url).toBeDefined();
    if (statusQuery) expect(url).toContain(statusQuery);
    else expect(url).not.toContain('status=');
  });
});
