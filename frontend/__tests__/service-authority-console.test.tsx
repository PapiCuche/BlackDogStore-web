import { Suspense } from 'react';
import { act, render, screen, within } from '@testing-library/react';
import ServiceOrderPage from '@/app/admin/service/orders/[id]/page';
import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { fetchWithAuth } from '@/app/lib/auth';
import { dashboard, user } from './support/fixtures';

/**
 * SVC-FUNC-01 on the order screen: an act that used to need one wide capability
 * can now be done with a narrower one, and the wide one still implies it.
 *
 *  · SVC-ASSIGN-01 — `service.orders.assign` is enough to choose the technician.
 *  · SVC-PAY-01 — recording a payment and reversing one are different
 *    authorities: `service.payments.collect` shows «Registrar pago»,
 *    only `service.payments.manage` shows «Reversar».
 *
 * The buttons are a courtesy. Every one of these is refused by the server too.
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
  id: 1, number: 'SRV-000001', status: 'repaired', status_label: 'Reparado',
  customer_name: 'Cliente Uno', device_summary: 'Teléfono X', branch_name: 'Centro',
  technician_name: '', reported_issue: 'No enciende', physical_condition: '',
  received_accessories: '', internal_notes: '', received_at: '2026-09-28T10:00:00Z',
  received_by_name: 'Recepción', available_transitions: [], customer_notifications: [],
};

const PAYMENT = {
  id: 5, amount: '100.00', currency: 'PEN', method: 'cash', reference: '', notes: '',
  received_at: '2026-09-29T10:00:00Z', received_by_name: 'Técnico', is_reversed: false,
  reversed_at: null, reversed_by_name: '', reversal_reason: '',
};

const SUMMARY = {
  currency: 'PEN', quoted_total: '450.00', confirmed_paid: '100.00', outstanding: '350.00',
  credit: '0.00', payment_status: 'partial', requires_payment_before_delivery: false,
};

const calls: { path: string; method: string; body: unknown }[] = [];

function mockApi(overrides: (path: string, method: string) => unknown | undefined = () => undefined) {
  calls.length = 0;
  jest.mocked(fetchWithAuth).mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    let body: unknown = overrides(path, method);
    if (body === undefined) {
      body = { count: 0, results: [] };
      if (path.endsWith('/orders/1/')) body = ORDER;
      else if (path.endsWith('/execution/')) body = { execution: null };
      else if (path.endsWith('/quality/')) body = { quality_check: null };
      else if (path.endsWith('/delivery/')) body = { delivery: null };
      else if (path.endsWith('/assignment/')) body = { current: null, candidates: [{ id: 9, name: 'Ana Técnica' }] };
      else if (path.endsWith('/payments/')) body = { count: 1, results: [PAYMENT], summary: SUMMARY };
    }
    return { ok: true, status: 200, json: async () => body } as Response;
  });
}

async function openOrderAs(caps: string[], order: Partial<typeof ORDER> = {}) {
  mockContext = { user: user(), dashboard: dashboard(caps), selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn() };
  mockApi((path) => (path.endsWith('/orders/1/') ? { ...ORDER, ...order } : undefined));
  await act(async () => {
    render(<Suspense fallback="Cargando"><ServiceOrderPage params={Promise.resolve({ id: '1' })} /></Suspense>);
  });
  await screen.findByRole('heading', { name: 'SRV-000001' });
}

describe('asignación de técnico', () => {
  it('basta service.orders.assign para elegir técnico', async () => {
    await openOrderAs(['service.orders.view', 'service.orders.assign']);
    const picker = screen.getByRole('combobox', { name: /Asignar a/ });
    expect(within(picker).getByRole('option', { name: 'Ana Técnica' })).toBeInTheDocument();
    // …y no por eso puede mover la orden por el taller.
    expect(screen.queryByText('Mover la orden')).not.toBeInTheDocument();
  });

  // SVC-ASSIGN-UNASSIGN: dejar una orden sin responsable es de quien la gestiona.
  it('con assign se puede corregir el técnico, no dejar la orden sin él', async () => {
    await openOrderAs(['service.orders.view', 'service.orders.assign'], { technician_name: 'Ana Técnica' });
    expect(screen.getByRole('button', { name: 'Asignar' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Quitar' })).not.toBeInTheDocument();
  });

  it('con manage se ofrece además quitarlo', async () => {
    await openOrderAs(['service.orders.view', 'service.orders.manage'], { technician_name: 'Ana Técnica' });
    expect(screen.getByRole('button', { name: 'Asignar' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Quitar' })).toBeInTheDocument();
  });

  it('sin assign ni manage no se piden candidatos', async () => {
    await openOrderAs(['service.orders.view']);
    expect(calls.some((c) => c.path.endsWith('/assignment/'))).toBe(false);
  });
});

describe('cobro del servicio', () => {
  it('con collect se puede registrar un pago, no reversarlo', async () => {
    await openOrderAs(['service.orders.view', 'service.payments.collect']);
    expect(screen.getByRole('button', { name: 'Registrar pago' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reversar' })).not.toBeInTheDocument();
  });

  it('con manage se puede registrar y reversar', async () => {
    await openOrderAs(['service.orders.view', 'service.payments.manage']);
    expect(screen.getByRole('button', { name: 'Registrar pago' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reversar' })).toBeInTheDocument();
  });

  it('sin ninguna de las dos no se ofrece ninguna acción', async () => {
    await openOrderAs(['service.orders.view']);
    expect(screen.queryByRole('button', { name: 'Registrar pago' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reversar' })).not.toBeInTheDocument();
  });
});
