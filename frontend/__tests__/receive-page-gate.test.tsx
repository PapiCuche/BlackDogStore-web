/**
 * INV-OPS-01 (P2-I) — la pantalla «Recibir mercadería» pide poder ver productos además de ajustar
 * inventario: sin esa capacidad el buscador respondería 403, así que se explica en lugar de abrir un asistente roto.
 */
import { act, render, screen } from '@testing-library/react';

import ReceiveInventoryPage from '@/app/admin/inventory/receive/page';
import { buildInternalAccess } from '@/app/admin/lib/internal-access';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
jest.mock('@/app/admin/components/ReceivingWizard', () => ({
  ReceivingWizard: (props: Record<string, unknown>) => <div data-testid="wizard" data-props={JSON.stringify(props)} />,
}));
jest.mock('@/app/admin/lib/use-branch-scope', () => ({
  useBranchScope: () => ({ access: { results: [{ id: 1, name: 'Miraflores' }], default_branch: { id: 1 } }, loading: false, error: null, ready: true }),
}));

let capabilities: string[] = [];
jest.mock('@/app/admin/components/AccessGuard', () => ({
  AccessGuard: ({ children }: { children: (access: unknown) => React.ReactNode }) => (
    <>{children(buildInternalAccess(
      { id: 12, username: 'ana', role: 'customer' } as never,
      { company: { id: 7, name: 'Tienda' }, access: { capabilities, is_platform_admin: false } } as never,
    ))}</>
  ),
}));

describe('página de recibir', () => {
  it('sin products.view explica qué falta y no monta el asistente', async () => {
    capabilities = ['inventory.view', 'inventory.adjust'];
    await act(async () => { render(<ReceiveInventoryPage />); });
    expect(screen.getByText('Para recibir necesitas poder ver productos.')).toBeInTheDocument();
    expect(screen.queryByTestId('wizard')).not.toBeInTheDocument();
  });

  it('con ambos permisos monta el asistente atado a esta persona y empresa, con costos y etiquetas según su permiso', async () => {
    capabilities = ['inventory.view', 'inventory.adjust', 'products.view', 'inventory.labels.print'];
    await act(async () => { render(<ReceiveInventoryPage />); });
    const props = JSON.parse(screen.getByTestId('wizard').dataset.props!);
    expect(props.scope).toEqual({ user: 12, company: 7 });
    expect(props.access).toEqual({ costs: false, manageProducts: false, viewProducts: true, labels: true });
  });
});
