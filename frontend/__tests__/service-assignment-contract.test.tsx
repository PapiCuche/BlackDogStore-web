import { Suspense } from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import ServiceOrderPage from '@/app/admin/service/orders/[id]/page';
import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { fetchWithAuth } from '@/app/lib/auth';
import { dashboard, user } from './support/fixtures';

/**
 * DRIFT-01. `GET …/service/orders/<pk>/assignment/` answers
 * `{current, candidates}` (backend `V1ServiceOrderAssignmentView`, pinned by
 * the M8 assignment tests); the console read `technicians`, so the picker was
 * always empty. These fixtures use the backend's real shape.
 */

let mockContext: InternalContext;
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: InternalContext) => React.ReactNode }) => children(mockContext),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => children,
}));

const ORDER = {
  id: 1, number: 'SRV-000001', status: 'received', status_label: 'Recibido',
  customer_name: 'Cliente Uno', device_summary: 'Teléfono X', branch_name: 'Centro',
  technician_name: 'Ana Técnica', reported_issue: 'No enciende', physical_condition: '',
  received_accessories: '', internal_notes: '', received_at: '2026-09-28T10:00:00Z',
  received_by_name: 'Recepción', available_transitions: [], customer_notifications: [],
};

// Exactly what the backend serialises: V1ServiceAssignmentSerializer for
// `current`, `{id, name}` per eligible technician for `candidates`.
const ASSIGNMENT = {
  current: { id: 3, technician: 9, technician_name: 'Ana Técnica',
    assigned_at: '2026-09-28T10:05:00Z', unassigned_at: null },
  candidates: [{ id: 9, name: 'Ana Técnica' }, { id: 10, name: 'Luis Taller' }],
};

const calls: { path: string; method: string; body: unknown }[] = [];

function mockApi() {
  calls.length = 0;
  jest.mocked(fetchWithAuth).mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    let body: unknown = { count: 0, results: [] };
    if (path.endsWith('/orders/1/')) body = ORDER;
    else if (path.endsWith('/execution/')) body = { execution: null };
    else if (path.endsWith('/quality/')) body = { quality_check: null };
    else if (path.endsWith('/delivery/')) body = { delivery: null };
    else if (path.endsWith('/assignment/')) body = method === 'POST' ? ORDER : ASSIGNMENT;
    else if (path.endsWith('/payments/')) body = { results: [], summary: { currency: 'PEN', quoted_total: null,
      confirmed_paid: '0.00', outstanding: null, credit: '0.00', payment_status: 'no_quote',
      requires_payment_before_delivery: false } };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
}

async function renderAs(caps: string[]) {
  mockContext = { user: user(), dashboard: dashboard(caps), selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn() };
  mockApi();
  await act(async () => {
    render(<Suspense fallback="Cargando"><ServiceOrderPage params={Promise.resolve({ id: '1' })} /></Suspense>);
  });
  await screen.findByRole('heading', { name: 'SRV-000001' });
}

const MANAGER = ['service.orders.view', 'service.orders.manage'];

it('offers the candidates the server returns', async () => {
  await renderAs(MANAGER);
  const picker = screen.getByRole('combobox', { name: /Asignar a/ });
  const names = within(picker).getAllByRole('option').map((o) => o.textContent);
  expect(names).toEqual(['—', 'Ana Técnica', 'Luis Taller']);
});

it('assigns the chosen candidate by its server id', async () => {
  await renderAs(MANAGER);
  fireEvent.change(screen.getByRole('combobox', { name: /Asignar a/ }), { target: { value: '10' } });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Asignar' }));
  });
  const post = calls.find((c) => c.method === 'POST' && c.path.endsWith('/assignment/'));
  expect(post?.body).toEqual({ technician_id: 10 });
});

it('does not ask for candidates without service.orders.manage', async () => {
  await renderAs(['service.orders.view']);
  expect(calls.some((c) => c.path.endsWith('/assignment/'))).toBe(false);
  expect(screen.queryByRole('combobox', { name: /Asignar a/ })).not.toBeInTheDocument();
});
