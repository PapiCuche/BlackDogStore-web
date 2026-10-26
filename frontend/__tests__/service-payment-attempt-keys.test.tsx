import { Suspense } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ServiceOrderPage from '@/app/admin/service/orders/[id]/page';
import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { fetchWithAuth } from '@/app/lib/auth';
import { NoSecureKeyError, attemptAmount, createAttemptKeys, makeIdempotencyKey } from '@/app/lib/service-console';
import { dashboard, user } from './support/fixtures';

/**
 * One key per ATTEMPT, not per shape of the form.
 *
 * The bug this pins: the panel keyed `orderId:amount:method:reference` and
 * kept the key for the whole page session. Two genuine, identical payments
 * (two S/ 100 in cash, no reference) sent the same key; the server answered
 * the second as a replay of the first, the counter saw success, and the second
 * payment was never recorded. A retry of the SAME attempt must still reuse its
 * key; a new attempt after a success must not.
 */

let mockContext: InternalContext;
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: InternalContext) => React.ReactNode }) => children(mockContext),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => children,
}));

type Sent = { amount: string; idempotency_key: string };

function serve(sent: Sent[], { failFirst = false } = {}) {
  let failed = false;
  jest.mocked(fetchWithAuth).mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (method === 'POST' && path.endsWith('/payments/')) {
      const body = JSON.parse(String(init?.body)) as Sent;
      sent.push(body);
      if (failFirst && !failed) {
        failed = true;
        throw new TypeError('Failed to fetch');
      }
      return { ok: true, status: 201, json: async () => ({ id: sent.length, amount: body.amount }) } as Response;
    }
    let body: unknown = { count: 0, results: [] };
    if (path.endsWith('/orders/1/')) body = { id: 1, number: 'SRV-000001', status: 'repairing', status_label: 'En reparación',
      customer_name: 'Cliente Uno', device_summary: 'Teléfono X', branch_name: 'Centro', technician_name: '',
      reported_issue: 'No enciende', physical_condition: '', received_accessories: '', internal_notes: '',
      received_at: '2026-09-28T10:00:00Z', received_by_name: 'Recepción', available_transitions: [],
      customer_notifications: [] };
    else if (path.endsWith('/history/') && !path.includes('/quality/')) body = { results: [] };
    else if (path.endsWith('/execution/')) body = { execution: null };
    else if (path.endsWith('/quality/')) body = { quality_check: null };
    else if (path.endsWith('/delivery/')) body = { delivery: null };
    else if (path.endsWith('/assignment/')) body = { current: null, candidates: [] };
    else if (path.endsWith('/payments/')) body = { results: [], summary: { currency: 'PEN', quoted_total: '500.00',
      confirmed_paid: '0.00', outstanding: '500.00', credit: '0.00', payment_status: 'unpaid',
      requires_payment_before_delivery: false } };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
}

async function openPage() {
  mockContext = {
    user: user(),
    dashboard: dashboard(['service.orders.view', 'service.payments.collect']),
    selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn(),
  };
  await act(async () => {
    render(<Suspense fallback="Cargando"><ServiceOrderPage params={Promise.resolve({ id: '1' })} /></Suspense>);
  });
  await screen.findByRole('heading', { name: 'SRV-000001' });
}

async function pay(amount: string) {
  const field = screen.getByLabelText('Importe (PEN)');
  await userEvent.clear(field);
  await userEvent.type(field, amount);
  await userEvent.click(screen.getByRole('button', { name: 'Registrar pago' }));
  await userEvent.click(screen.getByRole('button', { name: 'Sí' }));
}

afterEach(() => jest.mocked(fetchWithAuth).mockReset());

it('records two identical payments as two, with two keys', async () => {
  const sent: Sent[] = [];
  serve(sent);
  await openPage();
  await pay('100.00');
  await waitFor(() => expect(sent).toHaveLength(1));
  // Confirmed: the form is cleared, and the counter takes the next S/ 100.
  await waitFor(() => expect(screen.getByLabelText('Importe (PEN)')).toHaveValue(''));
  await pay('100.00');
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[0].amount).toBe(sent[1].amount);
  expect(sent[0].idempotency_key).not.toEqual(sent[1].idempotency_key);
});

it('resends the same key when the same attempt is retried after a failure', async () => {
  const sent: Sent[] = [];
  serve(sent, { failFirst: true });
  await openPage();
  await pay('100.00');
  await waitFor(() => expect(sent).toHaveLength(1));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Registrar pago' })).toBeEnabled());
  // The amount is still in the field: the counter presses again.
  await userEvent.click(screen.getByRole('button', { name: 'Registrar pago' }));
  await userEvent.click(screen.getByRole('button', { name: 'Sí' }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1].idempotency_key).toEqual(sent[0].idempotency_key);
});

describe('createAttemptKeys', () => {
  it('keeps a key until the attempt settles, then mints a new one', () => {
    const keys = createAttemptKeys();
    const first = keys.keyFor('1:100.00:cash:');
    expect(keys.keyFor('1:100.00:cash:')).toBe(first);
    keys.settle('1:100.00:cash:');
    expect(keys.keyFor('1:100.00:cash:')).not.toBe(first);
  });

  it('mints keys that fit the server column and do not repeat', () => {
    const seen = new Set(Array.from({ length: 200 }, () => createAttemptKeys().keyFor('x'.repeat(400))));
    expect(seen.size).toBe(200);
    for (const key of seen) expect(key.length).toBeLessThanOrEqual(64);
  });
});

it('records two identical part usages as two, with two keys', async () => {
  const sent: { quantity: number; idempotency_key: string }[] = [];
  jest.mocked(fetchWithAuth).mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (method === 'POST' && path.endsWith('/parts/')) {
      const body = JSON.parse(String(init?.body));
      sent.push(body);
      return { ok: true, status: 201, json: async () => ({ id: sent.length }) } as Response;
    }
    let body: unknown = { count: 0, results: [] };
    if (path.endsWith('/orders/1/')) body = { id: 1, number: 'SRV-000001', status: 'repairing', status_label: 'En reparación',
      customer_name: 'Cliente Uno', device_summary: 'Teléfono X', branch_name: 'Centro', technician_name: '',
      reported_issue: 'No enciende', physical_condition: '', received_accessories: '', internal_notes: '',
      received_at: '2026-09-28T10:00:00Z', received_by_name: 'Recepción', available_transitions: [],
      customer_notifications: [] };
    else if (path.endsWith('/history/') && !path.includes('/quality/')) body = { results: [] };
    else if (path.endsWith('/execution/')) body = { execution: { id: 3, started_at: '2026-09-28T11:00:00Z',
      completed_at: null, is_completed: false, work_performed: '', result: '', result_label: '', internal_notes: '',
      started_by_name: 'Técnico', completed_by_name: '', parts: [] } };
    else if (path.endsWith('/parts/candidates/')) body = { results: [{ quote_item_id: 9, product_id: 4,
      description: 'Batería', approved_quantity: 2, used_quantity: 0, outstanding_quantity: 2, available_in_branch: 5 }] };
    else if (path.endsWith('/quality/')) body = { quality_check: null };
    else if (path.endsWith('/delivery/')) body = { delivery: null };
    else if (path.endsWith('/assignment/')) body = { current: null, candidates: [] };
    else if (path.endsWith('/payments/')) body = { results: [], summary: { currency: 'PEN', quoted_total: '500.00',
      confirmed_paid: '0.00', outstanding: '500.00', credit: '0.00', payment_status: 'unpaid',
      requires_payment_before_delivery: false } };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  mockContext = {
    user: user(),
    dashboard: dashboard(['service.orders.view', 'service.repair.manage']),
    selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn(),
  };
  await act(async () => {
    render(<Suspense fallback="Cargando"><ServiceOrderPage params={Promise.resolve({ id: '1' })} /></Suspense>);
  });
  for (let i = 0; i < 2; i += 1) {
    await userEvent.click(await screen.findByRole('button', { name: 'Registrar consumo' }));
    await userEvent.click(screen.getByRole('button', { name: 'Sí' }));
    await waitFor(() => expect(sent).toHaveLength(i + 1));
  }
  expect(sent[0].quantity).toBe(sent[1].quantity);
  expect(sent[0].idempotency_key).not.toEqual(sent[1].idempotency_key);
});


describe('only a secure source mints a key (#186 review)', () => {
  const realCrypto = global.crypto;
  afterEach(() => Object.defineProperty(global, 'crypto', { value: realCrypto, configurable: true }));
  const withoutCrypto = () => Object.defineProperty(global, 'crypto', { value: undefined, configurable: true });

  it('refuses to mint without crypto, and never falls back to Math.random', () => {
    const random = jest.spyOn(Math, 'random');
    withoutCrypto();
    expect(() => makeIdempotencyKey('7:100.00:cash:')).toThrow(NoSecureKeyError);
    expect(() => createAttemptKeys().keyFor('7:100.00:cash:')).toThrow(NoSecureKeyError);
    expect(random).not.toHaveBeenCalled();
    random.mockRestore();
  });

  it('uses randomUUID or getRandomValues', () => {
    Object.defineProperty(global, 'crypto', { value: { randomUUID: () => 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee' },
      configurable: true });
    expect(makeIdempotencyKey('x')).toMatch(/^aaaaaaaabbbb4ccc8dddeeeeeeeeeeee-/);
    Object.defineProperty(global, 'crypto', { value: { getRandomValues: (b: Uint8Array) => b.fill(0xab) },
      configurable: true });
    expect(makeIdempotencyKey('x')).toMatch(/^(ab){16}-/);
  });

  it('the panel refuses to record a payment without a secure key, and says why', async () => {
    const sent: Sent[] = [];
    serve(sent);
    await openPage();
    withoutCrypto();
    await pay('100.00');
    expect(await screen.findByText(/clave segura/)).toBeInTheDocument();
    expect(sent).toHaveLength(0);
  });
});

describe('the attempt shape normalises the amount (#186 review)', () => {
  it('reads 100, 100.0 and 100.00 as one amount', () => {
    expect(attemptAmount('100')).toBe('100.00');
    expect(attemptAmount(' 100.0 ')).toBe('100.00');
    expect(attemptAmount('0100.5')).toBe('100.50');
    expect(attemptAmount('0.5')).toBe('0.50');
    expect(attemptAmount('abc')).toBe('abc');
  });

  it('a retry typed as 100 after 100.00 failed resends the same key', async () => {
    const sent: Sent[] = [];
    serve(sent, { failFirst: true });
    await openPage();
    await pay('100.00');
    await waitFor(() => expect(sent).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Registrar pago' })).toBeEnabled());
    await pay('100');
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1].idempotency_key).toEqual(sent[0].idempotency_key);
  });
});
