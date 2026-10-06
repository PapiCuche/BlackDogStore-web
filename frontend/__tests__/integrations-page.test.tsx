import { render, screen } from '@testing-library/react';

import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INTEGRATIONS-CONSOLE · quién llega a la pantalla.
 *
 * Sólo el master. A quien escribe la dirección a mano con cualquier otra cuenta
 * —el administrador de una empresa con todas sus capacidades incluido— la
 * página le dice que no existe para él y NO PIDE NADA al servidor: no hay ni un
 * intento que el servidor tenga que rechazar. (Si lo hubiera, respondería 403.)
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: InternalContext) => React.ReactNode }) =>
    children((globalThis as { __ctx?: InternalContext }).__ctx as InternalContext),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const IntegrationsPage = require('@/app/admin/settings/integrations/page').default;

const send = jest.mocked(fetchWithAuth);

function context(access: Record<string, unknown> | null): InternalContext {
  return {
    user: { id: 1, username: 'alguien' },
    dashboard: access ? { company: { id: 3, slug: 'taller', name: 'Taller' }, access } : null,
    selectedCompanyId: 3, selectCompany: () => undefined, reload: () => undefined,
  } as unknown as InternalContext;
}

beforeEach(() => {
  send.mockReset();
  send.mockResolvedValue({ ok: true, status: 200, json: async () => ({ results: [], store_available: true }) } as Response);
});

const EVERYTHING = ['company.view', 'company.manage', 'settings.view', 'settings.manage', 'memberships.manage'];

test.each([
  ['el administrador de una empresa, con todas sus capacidades', context({ is_platform_admin: false, capabilities: EVERYTHING })],
  ['el operador antiguo, sin empresa', context(null)],
])('%s no ve la consola y no se pide nada al servidor', (_who, ctx) => {
  (globalThis as { __ctx?: InternalContext }).__ctx = ctx;
  render(<IntegrationsPage />);

  expect(screen.getByText('Esta sección no está disponible para tu cuenta.')).toBeInTheDocument();
  expect(screen.queryByText(/MASTER pueden modificarlas/)).toBeNull();
  expect(send).not.toHaveBeenCalled();
});

test('el master ve la consola', async () => {
  (globalThis as { __ctx?: InternalContext }).__ctx = context({ is_platform_admin: true, capabilities: [] });
  render(<IntegrationsPage />);

  expect(await screen.findByText(/Sólo usuarios MASTER pueden modificarlas/)).toBeInTheDocument();
  expect(send).toHaveBeenCalledTimes(1);
  expect(String(send.mock.calls[0][0])).toMatch(/\/admin\/integrations\/$/);
});
