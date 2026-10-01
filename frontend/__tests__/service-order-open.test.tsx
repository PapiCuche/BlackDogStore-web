import { Suspense } from 'react';
import { act, render, screen } from '@testing-library/react';
import ServiceOrderPage from '@/app/admin/service/orders/[id]/page';
import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { fetchWithAuth } from '@/app/lib/auth';
import { dashboard, user } from './support/fixtures';

let mockContext: InternalContext;
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: InternalContext) => React.ReactNode }) => children(mockContext),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => children,
}));

it.each(['sales', 'technician', 'admin'])('opens a real-shaped order detail for %s', async (role) => {
  const caps = ['service.orders.view', 'service.orders.create'];
  if (role !== 'sales') caps.push('service.orders.manage');
  mockContext = { user: user(), dashboard: dashboard(caps), selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn() };
  const history = [{ id: 1, from_status: '', to_status: 'received', to_status_label: 'Ingreso al taller',
    origin: 'internal', comment: '', actor_name: 'Recepción', is_customer_visible: true, created_at: '2026-09-28T10:00:00Z' }];
  jest.mocked(fetchWithAuth).mockImplementation(async (input) => {
    const path = String(input);
    let body: unknown = { count: 0, results: [] };
    if (path.endsWith('/orders/1/')) body = { id: 1, number: 'SRV-000001', status: 'received', status_label: 'Recibido',
      customer_name: 'Cliente Uno', device_summary: 'Teléfono X', branch_name: 'Centro', technician_name: '',
      reported_issue: 'No enciende', physical_condition: '', received_accessories: '', internal_notes: '',
      received_at: '2026-09-28T10:00:00Z', received_by_name: 'Recepción', available_transitions: [{ code: 'diagnosing', label: 'Diagnosticar' }],
      customer_notifications: [] };
    else if (path.endsWith('/history/') && !path.includes('/quality/')) body = { results: history };
    else if (path.endsWith('/execution/')) body = { execution: null };
    else if (path.endsWith('/quality/')) body = { quality_check: null };
    else if (path.endsWith('/delivery/')) body = { delivery: null };
    else if (path.endsWith('/assignment/')) body = { current: null, candidates: [] };
    else if (path.endsWith('/payments/')) body = { results: [], summary: { currency: 'PEN', quoted_total: null,
      confirmed_paid: '0.00', outstanding: null, credit: '0.00', payment_status: 'no_quote', requires_payment_before_delivery: false } };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  await act(async () => {
    render(<Suspense fallback="Cargando"><ServiceOrderPage params={Promise.resolve({ id: '1' })} /></Suspense>);
  });
  expect(await screen.findByRole('heading', { name: 'SRV-000001' })).toBeInTheDocument();
  expect(screen.getByText('Ingreso al taller')).toBeInTheDocument();
  if (role === 'sales') expect(screen.queryByText('Mover la orden')).not.toBeInTheDocument();
  else expect(screen.getByText('Mover la orden')).toBeInTheDocument();
});
