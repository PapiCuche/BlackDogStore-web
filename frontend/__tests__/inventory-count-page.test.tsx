/**
 * MIGRATION-D — la página del recuento: dónde se aprueba y dónde no.
 *
 * Un recuento de MIGRACIÓN no se aprueba desde aquí (es la pantalla de
 * MIGRATION-F): se ve «Pendiente de aprobación» y no hay ningún botón que llame a
 * `…/approve/`. Un recuento ORDINARIO conserva el flujo de hoy, para no dejar a
 * nadie sin poder aprobar mientras F no existe.
 */
import { act, render, screen } from '@testing-library/react';

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
  });

  it('ordinario en el servidor de hoy: conserva «Aprobar y ajustar» con inventory.adjust', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail());
    capabilities = ['inventory.view', 'inventory.adjust'];
    await mount();
    expect(await screen.findByRole('button', { name: 'Aprobar y ajustar' })).toBeInTheDocument();
    expect(inv.approveCount).not.toHaveBeenCalled();
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
