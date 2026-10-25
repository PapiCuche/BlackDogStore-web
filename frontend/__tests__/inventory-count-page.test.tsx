/**
 * MIGRATION-D — la página del recuento: dónde se aprueba y dónde no.
 *
 * Un recuento de MIGRACIÓN no se aprueba desde aquí (es la pantalla de
 * MIGRATION-F): se ve «Pendiente de aprobación» y no hay ningún botón que llame a
 * `…/approve/`. Un recuento ORDINARIO conserva el flujo de hoy, para no dejar a
 * nadie sin poder aprobar mientras F no existe.
 */
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import CountDetailPage from '@/app/admin/inventory/counts/[id]/page';
import * as api from '@/app/admin/lib/counts-api';
import * as inventory from '@/app/lib/inventory';
import type { CountDetail } from '@/app/admin/lib/counts-api';

jest.mock('next/navigation', () => ({ useParams: () => ({ id: '9' }) }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ fetchAdminProducts: jest.fn() }));
jest.mock('@/app/lib/inventory', () => ({
  ...jest.requireActual('@/app/lib/inventory'),
  approveCount: jest.fn(),
  cancelCount: jest.fn(),
}));
jest.mock('@/app/admin/inventory/counts/_components/ordinary-approval', () => ({
  approveOrdinaryCount: jest.fn(),
}));
jest.mock('@/app/admin/lib/counts-api', () => ({
  ...jest.requireActual('@/app/admin/lib/counts-api'),
  fetchCountDetail: jest.fn(),
  scanCount: jest.fn(),
  setCountLines: jest.fn(),
  recountLines: jest.fn(),
  submitCountForReview: jest.fn(),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

let capabilities: string[] = [];
jest.mock('@/app/admin/components/AccessGuard', () => ({
  AccessGuard: ({ children }: { children: (a: unknown) => React.ReactNode }) => (
    <>
      {children({
        user: { username: 'ana', role: 'customer' },
        dashboard: null,
        hasCompanyContext: true,
        isPlatformAdmin: false,
        isLegacyBridge: false,
        can: (cap: string) => capabilities.includes(cap),
      })}
    </>
  ),
}));

const mocked = api as jest.Mocked<typeof api>;
const inv = inventory as jest.Mocked<typeof inventory>;
const approval = jest.requireMock('@/app/admin/inventory/counts/_components/ordinary-approval') as {
  approveOrdinaryCount: jest.Mock;
};
const { CountApiError } = jest.requireActual('@/app/admin/lib/counts-api') as typeof api;

function detail(over: Partial<CountDetail> = {}): CountDetail {
  return {
    id: 9, company: 7, branch: 11, branch_name: 'Sucursal Centro',
    status: 'counting', status_label: 'En conteo', reason: '',
    items: [{
      id: 101, product: 5, product_name: 'Cargador USB-C 20W', product_slug: 'c',
      theoretical_at_start: 4, physical_quantity: 5, theoretical_at_approval: null,
      difference: null, is_counted: true, note: '', updated_at: '2026-10-23T10:00:00Z',
    }],
    counted_items: 1, created_by: 1, created_by_username: 'ana',
    created_at: '2026-10-23T09:00:00Z', approved_at: null, cancelled_at: null,
    updated_at: '2026-10-23T09:00:00Z',
    ...over,
  };
}

async function mount() {
  await act(async () => { render(<CountDetailPage />); });
}

beforeEach(() => {
  jest.clearAllMocks();
  capabilities = ['inventory.view', 'inventory.adjust', 'inventory.counts.approve'];
});

describe('página del recuento', () => {
  it('migración: sin botón de aprobar, «Pendiente de aprobación» en revisión, nunca approve', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({ kind: 'migration', status: 'review', status_label: 'En revisión' }));
    await mount();
    expect(await screen.findByRole('heading', { name: 'Recuento #9' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Aprobar/ })).not.toBeInTheDocument();
    expect(screen.getAllByText('Pendiente de aprobación').length).toBeGreaterThan(0);
    expect(inv.approveCount).not.toHaveBeenCalled();
    expect(approval.approveOrdinaryCount).not.toHaveBeenCalled();
  });

  it('ordinario en el servidor de hoy: conserva «Aprobar y ajustar» con inventory.adjust', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail());
    capabilities = ['inventory.view', 'inventory.adjust'];
    await mount();
    expect(await screen.findByRole('button', { name: 'Aprobar y ajustar' })).toBeInTheDocument();
    expect(inv.approveCount).not.toHaveBeenCalled();
    expect(approval.approveOrdinaryCount).not.toHaveBeenCalled();
  });

  it('ordinario con el contrato de C: aprobar exige inventory.counts.approve', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({ kind: 'regular' }));
    capabilities = ['inventory.view', 'inventory.adjust'];
    await mount();
    await screen.findByRole('heading', { name: 'Recuento #9' });
    expect(screen.queryByRole('button', { name: 'Aprobar y ajustar' })).not.toBeInTheDocument();
  });

  it('sin costs.view la página no pide ver costos al área de conteo', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({
      kind: 'migration',
      items: [{ ...detail().items[0], difference_value: '9.90' } as CountDetail['items'][number]],
    }));
    await mount();
    await screen.findByRole('heading', { name: 'Recuento #9' });
    expect(screen.queryByText(/9\.90/)).not.toBeInTheDocument();
  });
});


describe('aprobación de un recuento ordinario (P2-3)', () => {
  const ordinary = (over: Partial<CountDetail> = {}) => detail({ kind: 'regular', ...over });

  it('una aprobación negada enumera el motivo de cada línea y relee el recuento', async () => {
    jest.spyOn(window, 'confirm').mockReturnValue(true);
    mocked.fetchCountDetail.mockResolvedValue(ordinary());
    approval.approveOrdinaryCount.mockRejectedValueOnce(new CountApiError(
      'El recuento no puede aprobarse todavía: revisa las líneas indicadas.', 400, 'approval_blocked',
      { problems: [{ product: 5, product_name: 'Cargador USB-C 20W', reason: 'overlapping', detail: 'Ajuste #12 después de contarlo.' }] },
    ));
    await mount();
    await userEvent.click(await screen.findByRole('button', { name: 'Aprobar y ajustar' }));
    const box = await screen.findByRole('region', { name: /No se pudo aprobar/ });
    expect(box).toHaveTextContent('Cargador USB-C 20W');
    expect(box).toHaveTextContent('Ajuste #12 después de contarlo.');
    expect(mocked.fetchCountDetail).toHaveBeenCalledTimes(2);
  });

  it('con líneas ambiguas o solapadas a la vista, aprobar está desactivado', async () => {
    mocked.fetchCountDetail.mockResolvedValue(ordinary({
      items: [{ ...detail().items[0], review_state: 'ambiguous' } as CountDetail['items'][number]],
    }));
    await mount();
    expect(await screen.findByRole('button', { name: 'Aprobar y ajustar' })).toBeDisabled();
    expect(screen.getByText(/Recuenta las líneas marcadas/)).toBeInTheDocument();
  });

  it('quien sólo aprueba (sin inventory.adjust) ve aprobar, no anular', async () => {
    capabilities = ['inventory.view', 'inventory.counts.approve'];
    mocked.fetchCountDetail.mockResolvedValue(ordinary());
    await mount();
    expect(await screen.findByRole('button', { name: 'Aprobar y ajustar' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Anular' })).not.toBeInTheDocument();
  });
});
