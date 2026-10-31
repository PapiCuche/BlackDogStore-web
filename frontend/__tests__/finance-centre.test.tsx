import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

/**
 * FIN-REMIND-01 — la pantalla «Pagos fijos y vencimientos», contra el contrato del backend simulado.
 *
 * Lo que se fija:
 *  - los importes salen POR MONEDA y tal como los dio el servidor (nunca se mezclan soles y dólares);
 *  - cada pestaña lista lo que le toca, y los filtros (estado, texto) actúan sobre lo cargado;
 *  - sucursal y categoría se piden al servidor;
 *  - las acciones sólo se ofrecen con su capacidad (pagar, revertir, administrar reglas);
 *  - «Registrar pago»: importe por defecto = saldo, UNA clave por apertura del diálogo, doble clic seguro;
 *  - «Posponer aviso» dice que sólo silencia los avisos de quien lo hace y NO cambia el pago;
 *  - la política de avisos es de lectura salvo `can_manage`;
 *  - `?obligation=<id>` abre ese detalle, y un 404 o 403 se explican;
 *  - «hoy» es el del servidor, no el del navegador; el calendario filtra por día.
 */
let mockSearch = '';
jest.mock('next/navigation', () => ({
  usePathname: () => '/admin/finance',
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), refresh: jest.fn(), back: jest.fn(), forward: jest.fn(), prefetch: jest.fn() }),
  useSearchParams: () => new URLSearchParams(mockSearch),
  useParams: () => ({}),
}));

const mockSummary = jest.fn();
const mockAll = jest.fn();
const mockOne = jest.fn();
const mockRules = jest.fn();
const mockPolicy = jest.fn();
const mockCategories = jest.fn();
const mockPay = jest.fn();
const mockReverse = jest.fn();
const mockSnooze = jest.fn();
const mockUnsnooze = jest.fn();
const mockUpdatePolicy = jest.fn();
const mockUpdateRule = jest.fn();
const mockAct = jest.fn();
jest.mock('@/app/admin/lib/finance-api', () => {
  const actual = jest.requireActual('@/app/admin/lib/finance-api');
  return {
    ...actual,
    fetchSummary: (...a: unknown[]) => mockSummary(...a),
    fetchAllObligations: (...a: unknown[]) => mockAll(...a),
    fetchObligation: (...a: unknown[]) => mockOne(...a),
    fetchRules: (...a: unknown[]) => mockRules(...a),
    fetchPolicy: (...a: unknown[]) => mockPolicy(...a),
    fetchCategories: (...a: unknown[]) => mockCategories(...a),
    recordPayment: (...a: unknown[]) => mockPay(...a),
    reversePayment: (...a: unknown[]) => mockReverse(...a),
    snoozeObligation: (...a: unknown[]) => mockSnooze(...a),
    unsnoozeObligation: (...a: unknown[]) => mockUnsnooze(...a),
    updatePolicy: (...a: unknown[]) => mockUpdatePolicy(...a),
    updateRule: (...a: unknown[]) => mockUpdateRule(...a),
    actOnRule: (...a: unknown[]) => mockAct(...a),
  };
});

/* eslint-disable @typescript-eslint/no-require-imports */
const { FinanceCentre, segmentOf, matchesSearch } = require('@/app/admin/finance/_components/FinanceCentre');
const { FinanceApiError } = require('@/app/admin/lib/finance-api');
/* eslint-enable @typescript-eslint/no-require-imports */

const TODAY = '2026-10-31';

function ob(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id, reference: `OB-${id}`, direction: 'payable', origin_type: 'manual', concept: `Pago ${id}`, branch: { id: 10, name: 'Centro' },
    category: { id: 1, name: 'Alquiler' }, supplier: null, payee_name: `Proveedor ${id}`, owner: null, planned_method: '',
    rule_id: null, period_index: null, currency: 'PEN', amount_original: '100.00', effective: '100.00', paid: '0.00',
    balance: '100.00', partially_paid: false, issue_date: '2026-10-01', due_date: '2026-11-10', state: 'due_soon',
    lifecycle: 'open', disputed: false, dispute_reason: '', external_reference: '', notes: '', snoozed_until: null,
    ...overrides,
  };
}

const OVERDUE = ob(1, { concept: 'Alquiler del local', payee_name: 'Inmobiliaria Sol', due_date: '2026-10-20', state: 'overdue', balance: '2500.00', effective: '2500.00', rule_id: 5 });
const TODAYS = ob(2, { concept: 'Luz Centro', payee_name: 'Electro', due_date: TODAY, state: 'due_today', balance: '300.00', effective: '300.00' });
const SOON = ob(3, { concept: 'Internet', payee_name: 'Fibra SA', due_date: '2026-11-03', state: 'due_soon', balance: '120.00', effective: '120.00' });
const FAR = ob(4, { concept: 'Seguro anual', payee_name: 'Aseguradora', due_date: '2026-12-15', state: 'pending', balance: '900.00', effective: '900.00', branch: { id: 11, name: 'Norte' } });
const PARTIAL = ob(5, { concept: 'Hosting', payee_name: 'Nube', due_date: '2026-11-03', state: 'due_soon', balance: '40.00', effective: '100.00', paid: '60.00', partially_paid: true });
const USD = ob(6, { concept: 'Licencia software', payee_name: 'Vendor', currency: 'USD', due_date: '2026-10-25', state: 'overdue', balance: '50.00', effective: '50.00' });
const PAID = ob(7, { concept: 'Agua septiembre', due_date: '2026-10-10', state: 'paid', lifecycle: 'settled', balance: '0.00', paid: '80.00', effective: '80.00' });

const OPEN_ROWS = [OVERDUE, USD, TODAYS, SOON, PARTIAL, FAR];

const SUMMARY = {
  today: TODAY, notice: 'Control interno',
  open: { PEN: '3860.00', USD: '50.00' }, overdue: { PEN: '2500.00', USD: '50.00' }, due_today: { PEN: '300.00' },
  due_soon: { PEN: '160.00' }, counts: { open: 6, overdue: 2, due_today: 1, due_soon: 2 },
};

const POLICY = {
  enabled: true, offsets: [7, 3, 1, 0], overdue_every_days: 7, overdue_max: 4, email_enabled: false, email_offsets: [1, 0],
  email_overdue: true, last_run_at: '2026-10-31T08:00:00-05:00', last_error: '', can_manage: false,
};

const RULE = {
  id: 5, concept: 'Alquiler del local', branch: { id: 10, name: 'Centro' }, category: { id: 1, name: 'Alquiler' }, payee_name: 'Inmobiliaria Sol',
  amount: '2500.00', currency: 'PEN', frequency: 'monthly', anchor_date: '2026-01-01', due_day: 20, end_date: null, planned_method: '',
  reference: '', notes: '', ended: false, end_reason: '', paused: false, paused_from: null, pauses: [], calendar_locked: true,
};

const ALL_CAPS = ['finance.view', 'finance.manage', 'finance.payments.record', 'finance.payments.reverse', 'finance.automation.manage'];

function dashboard(caps: string[] = ['finance.view'], master = false) {
  return {
    company: { id: 1, name: 'Tienda', slug: 'tienda', is_active: true },
    access: { is_platform_admin: master, capabilities: caps },
    branch_scope: { mode: 'all', default_branch: null, branches: [{ id: 10, name: 'Centro' }, { id: 11, name: 'Norte' }] },
  } as never;
}

function detailOf(item: ReturnType<typeof ob>, extra: Record<string, unknown> = {}) {
  return {
    ...item, payments: [], adjustments: [], history: [{ action: 'finance_obligation_created', actor: { id: 1, name: 'Ana' }, at: '2026-10-01T10:00:00-05:00', metadata: {} }],
    reminders: { sent: 2, mine: [{ title: 'Pago vencido: Alquiler del local', at: '2026-10-21T08:00:00-05:00', read: false }] },
    ...extra,
  };
}

beforeEach(() => {
  mockSearch = '';
  window.history.replaceState(null, '', '/admin/finance');
  mockSummary.mockReset().mockResolvedValue(SUMMARY);
  mockAll.mockReset().mockImplementation(async (_company: unknown, filters: { state?: string }) => {
    const rows = filters.state === 'paid' ? [PAID] : OPEN_ROWS;
    return { results: rows, count: rows.length, truncated: false };
  });
  mockOne.mockReset().mockImplementation(async (_c: unknown, id: number) => {
    const found = [...OPEN_ROWS, PAID].find((r) => r.id === id);
    if (!found) throw new FinanceApiError('No encontrado.', 404);
    return detailOf(found);
  });
  mockRules.mockReset().mockResolvedValue([RULE]);
  mockPolicy.mockReset().mockResolvedValue(POLICY);
  mockCategories.mockReset().mockResolvedValue([{ id: 1, direction: 'payable', name: 'Alquiler', is_active: true }, { id: 2, direction: 'payable', name: 'Servicios', is_active: true }]);
  mockPay.mockReset();
  mockReverse.mockReset();
  mockSnooze.mockReset();
  mockUnsnooze.mockReset();
  mockUpdatePolicy.mockReset();
  mockUpdateRule.mockReset();
  mockAct.mockReset();
});

afterEach(async () => { await settle(); });

async function renderCentre(caps: string[] = ['finance.view']) {
  render(<FinanceCentre companyId={1} dashboard={dashboard(caps)} />);
  await screen.findByTestId('finance-list');
  await settle();
}

/** Lets the secondary loads (rules, policy, categories) land inside act. */
async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

function listText() {
  return screen.getByTestId('finance-list').textContent ?? '';
}
function tab(name: RegExp) {
  return screen.getByRole('tab', { name });
}
function row(concept: string) {
  // A row of the list (the open panel repeats the concept in its heading, which is not an <li>).
  const found = screen.getAllByText(concept).map((n) => n.closest('li')).find(Boolean);
  if (!found) throw new Error(`no row for ${concept}`);
  return found as HTMLElement;
}

describe('summary', () => {
  it('shows each state with its amounts PER CURRENCY, never added together', async () => {
    await renderCentre();
    const overdue = document.querySelector('[data-summary="overdue"]') as HTMLElement;
    expect(overdue.querySelector('[data-currency="PEN"]')).toHaveTextContent(/2[.,\s]?500/);
    expect(overdue.querySelector('[data-currency="USD"]')).toHaveTextContent(/50/);
    expect(overdue).toHaveTextContent('2 pagos');
    const today = document.querySelector('[data-summary="today"]') as HTMLElement;
    expect(today).toHaveTextContent('1 pago');
    expect(today.querySelector('[data-currency="USD"]')).toBeNull();
    const total = document.querySelector('[data-summary="total"]') as HTMLElement;
    expect(total.querySelectorAll('[data-currency]')).toHaveLength(2);
    expect(total).toHaveTextContent('6 pagos');
  });

  it('uses the company today from the server, not the browser clock', async () => {
    jest.useFakeTimers({ doNotFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'setImmediate', 'queueMicrotask', 'nextTick'] });
    jest.setSystemTime(new Date('2031-03-15T12:00:00Z'));
    try {
      await renderCentre();
      expect(screen.getByText(/Hoy es sábado 31 de octubre de 2026/)).toBeInTheDocument();
      expect(screen.getByText(/Calendario · octubre de 2026/)).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it('opens the list a card summarises', async () => {
    await renderCentre();
    fireEvent.click(document.querySelector('[data-summary="today"]') as HTMLElement);
    expect(tab(/^Hoy/)).toHaveAttribute('aria-selected', 'true');
    expect(listText()).toContain('Luz Centro');
    expect(listText()).not.toContain('Alquiler del local');
  });
});

describe('tabs', () => {
  it('opens on the most urgent list and counts each tab', async () => {
    await renderCentre();
    expect(tab(/^Vencidos/)).toHaveAttribute('aria-selected', 'true');
    expect(tab(/^Vencidos \(2\)/)).toBeInTheDocument();
    expect(tab(/^Hoy \(1\)/)).toBeInTheDocument();
    expect(tab(/^Próximos \(3\)/)).toBeInTheDocument();
    expect(tab(/^Parciales \(1\)/)).toBeInTheDocument();
  });

  it('lists in each tab only what belongs to it', async () => {
    await renderCentre();
    expect(listText()).toContain('Alquiler del local');
    expect(listText()).toContain('Licencia software');
    expect(listText()).not.toContain('Internet');

    fireEvent.click(tab(/^Próximos/));
    expect(listText()).toContain('Internet');
    expect(listText()).toContain('Seguro anual');
    expect(listText()).not.toContain('Luz Centro');

    fireEvent.click(tab(/^Parciales/));
    expect(listText()).toContain('Hosting');
    expect(listText()).toContain('Pagado');
    expect(listText()).not.toContain('Internet');
  });

  it('loads the completed ones only when asked, from the last 90 days', async () => {
    await renderCentre();
    expect(mockAll.mock.calls.every(([, f]) => f.state === 'open')).toBe(true);
    fireEvent.click(tab(/^Completados/));
    expect(await screen.findByText('Agua septiembre')).toBeInTheDocument();
    const paidCall = mockAll.mock.calls.find(([, f]) => f.state === 'paid');
    expect(paidCall?.[1]).toMatchObject({ state: 'paid', dueFrom: '2026-08-02' });
  });

  it('moves between tabs with the arrow keys, Home and End', async () => {
    await renderCentre();
    const current = tab(/^Vencidos/);
    current.focus();
    fireEvent.keyDown(current, { key: 'ArrowRight' });
    expect(tab(/^Parciales/)).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(tab(/^Parciales/));
    fireEvent.keyDown(tab(/^Parciales/), { key: 'End' });
    expect(tab(/^Avisos/)).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(tab(/^Avisos/), { key: 'Home' });
    expect(tab(/^Próximos/)).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(tab(/^Próximos/), { key: 'ArrowLeft' });
    expect(tab(/^Avisos/)).toHaveAttribute('aria-selected', 'true');
  });

  it('says there is nothing, instead of drawing an empty box', async () => {
    mockAll.mockResolvedValue({ results: [FAR], count: 1, truncated: false });
    await renderCentre();
    fireEvent.click(tab(/^Vencidos/));
    expect(screen.getByTestId('finance-empty')).toHaveTextContent('Nada vencido');
  });
});

describe('filters', () => {
  it('asks the server for a branch and a category', async () => {
    await renderCentre();
    mockAll.mockClear();
    fireEvent.change(screen.getByLabelText('Sucursal'), { target: { value: '11' } });
    await waitFor(() => expect(mockAll).toHaveBeenCalled());
    expect(mockAll.mock.calls[0][1]).toMatchObject({ state: 'open', branch: 11, category: null });
    mockAll.mockClear();
    fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: '2' } });
    await waitFor(() => expect(mockAll).toHaveBeenCalled());
    expect(mockAll.mock.calls[0][1]).toMatchObject({ branch: 11, category: 2 });
  });

  it('offers only the branches the server says the person reaches', async () => {
    await renderCentre();
    const options = within(screen.getByLabelText('Sucursal')).getAllByRole('option').map((o) => o.textContent);
    expect(options).toEqual(['Todas las sucursales', 'Centro', 'Norte']);
  });

  it('filters the loaded payments by state and by text, and says so', async () => {
    await renderCentre();
    expect(screen.getByText(/filtran los pagos ya cargados/)).toBeInTheDocument();
    fireEvent.click(tab(/^Próximos/));
    fireEvent.change(screen.getByLabelText('Estado'), { target: { value: 'pending' } });
    expect(listText()).toContain('Seguro anual');
    expect(listText()).not.toContain('Internet');
    fireEvent.change(screen.getByLabelText('Estado'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Buscar'), { target: { value: 'fibra' } });
    expect(listText()).toContain('Internet');
    expect(listText()).not.toContain('Seguro anual');
    fireEvent.change(screen.getByLabelText('Buscar'), { target: { value: 'nada de esto' } });
    expect(screen.getByTestId('finance-empty')).toHaveTextContent('coincide');
  });

  it('segments and searches as pure functions', () => {
    const rows = OPEN_ROWS as never[];
    expect(segmentOf('overdue', rows)).toHaveLength(2);
    expect(segmentOf('upcoming', rows)).toHaveLength(3);
    expect(segmentOf('partial', rows)).toHaveLength(1);
    expect(matchesSearch(SOON as never, '  FIBRA ')).toBe(true);
    expect(matchesSearch(SOON as never, 'luz')).toBe(false);
  });

  it('warns when the server holds more than was loaded', async () => {
    mockAll.mockResolvedValue({ results: OPEN_ROWS, count: 4000, truncated: true });
    await renderCentre();
    expect(screen.getByRole('note')).toHaveTextContent(/Hay 4000 pagos abiertos y se cargaron 6/);
  });
});

describe('actions follow the capabilities', () => {
  it('a reader sees no payment and no rule editing, but can view and snooze', async () => {
    await renderCentre(['finance.view']);
    const r = row('Alquiler del local');
    expect(within(r).getByRole('button', { name: /Ver obligación/ })).toBeInTheDocument();
    expect(within(r).getByRole('button', { name: /Posponer aviso/ })).toBeInTheDocument();
    expect(within(r).getByRole('button', { name: /Ver historial/ })).toBeInTheDocument();
    expect(within(r).queryByRole('button', { name: /Registrar pago/ })).toBeNull();
    expect(within(r).queryByRole('button', { name: /Editar regla/ })).toBeNull();
  });

  it('shows «Registrar pago» with finance.payments.record and «Editar regla» only on rule rows with finance.manage', async () => {
    await renderCentre(['finance.view', 'finance.payments.record', 'finance.manage']);
    expect(within(row('Alquiler del local')).getByRole('button', { name: /Registrar pago/ })).toBeInTheDocument();
    expect(within(row('Alquiler del local')).getByRole('button', { name: /Editar regla/ })).toBeInTheDocument();
    expect(within(row('Licencia software')).getByRole('button', { name: /Registrar pago/ })).toBeInTheDocument();
    expect(within(row('Licencia software')).queryByRole('button', { name: /Editar regla/ })).toBeNull();
  });

  it('offers neither payment nor snooze on what is already paid', async () => {
    await renderCentre(ALL_CAPS);
    fireEvent.click(tab(/^Completados/));
    const r = row(await screen.findByText('Agua septiembre').then((n) => n.textContent as string));
    expect(within(r).queryByRole('button', { name: /Registrar pago/ })).toBeNull();
    expect(within(r).queryByRole('button', { name: /Posponer aviso/ })).toBeNull();
  });

  it('hides rule actions on the Reglas tab without finance.manage', async () => {
    await renderCentre(['finance.view']);
    fireEvent.click(tab(/^Reglas/));
    expect(await screen.findByText('Día 20 de cada mes', { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Pausar regla/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Terminar regla/ })).toBeNull();
  });
});

describe('«Registrar pago»', () => {
  async function openDialog() {
    await renderCentre(['finance.view', 'finance.payments.record']);
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Registrar pago/ }));
    return screen.getByRole('dialog', { name: 'Registrar pago' });
  }

  it('defaults to the whole balance, today and a bank transfer, and says it moves the balance', async () => {
    const dialog = await openDialog();
    expect(within(dialog).getByLabelText(/Importe/)).toHaveValue('2500.00');
    expect(within(dialog).getByLabelText('Fecha del pago')).toHaveValue(TODAY);
    expect(within(dialog).getByLabelText('Fecha del pago')).toHaveAttribute('max', TODAY);
    expect(within(dialog).getByLabelText('Forma de pago')).toHaveValue('transfer');
    expect(dialog).toHaveTextContent(/sí registra un pago/);
  });

  it('puts focus inside the dialog and returns it to the button on close', async () => {
    const button = (await (async () => { await renderCentre(['finance.view', 'finance.payments.record']); return within(row('Alquiler del local')).getByRole('button', { name: /Registrar pago/ }); })());
    button.focus();
    fireEvent.click(button);
    const dialog = screen.getByRole('dialog', { name: 'Registrar pago' });
    expect(dialog.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it('sends the payment ONCE on a double click, with one idempotency key', async () => {
    let resolve: (v: unknown) => void = () => {};
    mockPay.mockReturnValue(new Promise((r) => { resolve = r; }));
    const dialog = await openDialog();
    const submit = within(dialog).getByRole('button', { name: 'Registrar pago' });
    fireEvent.click(submit);
    fireEvent.click(submit);
    fireEvent.submit(dialog.querySelector('form')!);
    expect(mockPay).toHaveBeenCalledTimes(1);
    const [company, id, input, key] = mockPay.mock.calls[0];
    expect(company).toBe(1);
    expect(id).toBe(1);
    expect(input).toMatchObject({ amount: '2500.00', method: 'transfer', paid_on: TODAY });
    expect(typeof key).toBe('string');
    expect(key.length).toBeGreaterThan(8);
    await act(async () => { resolve({ payment: { id: 9 }, obligation: { ...OVERDUE, balance: '0.00', state: 'paid', lifecycle: 'settled' } }); });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByRole('status')).toHaveTextContent(/Pago registrado/);
  });

  it('keeps the SAME key on a retry after a server refusal, and a NEW one on the next open', async () => {
    mockPay.mockRejectedValueOnce(new FinanceApiError('El saldo pendiente es 2500.00 PEN.', 400));
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar pago' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('saldo pendiente');
    mockPay.mockRejectedValueOnce(new FinanceApiError('El saldo pendiente es 2500.00 PEN.', 400));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar pago' }));
    await waitFor(() => expect(mockPay).toHaveBeenCalledTimes(2));
    expect(mockPay.mock.calls[1][3]).toBe(mockPay.mock.calls[0][3]);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancelar' }));
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Registrar pago/ }));
    mockPay.mockRejectedValueOnce(new FinanceApiError('x', 400));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Registrar pago' }));
    await waitFor(() => expect(mockPay).toHaveBeenCalledTimes(3));
    expect(mockPay.mock.calls[2][3]).not.toBe(mockPay.mock.calls[0][3]);
  });

  it('after a LOST answer: reads everything again, says to verify, freezes the form and retries byte-identical with the same key', async () => {
    mockPay.mockRejectedValueOnce(new FinanceApiError('No se pudo conectar con el servidor.', 0));
    const dialog = await openDialog();
    const callsBefore = mockAll.mock.calls.length;
    const summaryBefore = mockSummary.mock.calls.length;
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar pago' }));
    expect(await within(dialog).findByText('Verifica en el detalle si el pago ya se registró.')).toBeInTheDocument();
    await waitFor(() => expect(mockAll.mock.calls.length).toBeGreaterThan(callsBefore));
    expect(mockSummary.mock.calls.length).toBeGreaterThan(summaryBefore);
    expect(within(dialog).getByLabelText(/Importe/)).toBeDisabled();
    expect(within(dialog).getByLabelText('Fecha del pago')).toBeDisabled();
    expect(within(dialog).getByLabelText('Forma de pago')).toBeDisabled();
    expect(within(dialog).getByLabelText(/Referencia/)).toBeDisabled();

    mockPay.mockResolvedValueOnce({ payment: { id: 9 }, obligation: { ...OVERDUE, balance: '0.00', state: 'paid', lifecycle: 'settled' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reintentar con los mismos datos' }));
    await waitFor(() => expect(mockPay).toHaveBeenCalledTimes(2));
    expect(mockPay.mock.calls[1][3]).toBe(mockPay.mock.calls[0][3]);
    expect(mockPay.mock.calls[1][2]).toEqual(mockPay.mock.calls[0][2]);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('after a 409 (key already used for other data): says to verify, reads again and offers no second send', async () => {
    mockPay.mockRejectedValueOnce(new FinanceApiError('Esa clave ya se usó para un pago diferente.', 409));
    const dialog = await openDialog();
    const callsBefore = mockAll.mock.calls.length;
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar pago' }));
    expect(await within(dialog).findByText('Verifica en el detalle si el pago ya se registró.')).toBeInTheDocument();
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Esa clave ya se usó');
    await waitFor(() => expect(mockAll.mock.calls.length).toBeGreaterThan(callsBefore));
    expect(within(dialog).getByRole('button', { name: 'Registrar pago' })).toBeDisabled();
    // «Ver detalle» closes the dialog and opens the payment's detail.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ver detalle' }));
    expect(await screen.findByRole('dialog', { name: 'Alquiler del local' })).toBeInTheDocument();
    expect(mockOne).toHaveBeenCalledWith(1, 1);
  });

  it('a plain refusal (400) does NOT freeze the form nor claim the payment may exist', async () => {
    mockPay.mockRejectedValueOnce(new FinanceApiError('El saldo pendiente es 2500.00 PEN.', 400));
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar pago' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('saldo pendiente');
    expect(within(dialog).queryByText(/Verifica en el detalle/)).toBeNull();
    expect(within(dialog).getByLabelText(/Importe/)).not.toBeDisabled();
  });

  it.each(['1,250.00', 'S/ 300', '1 250', '12.345', 'abc'])(
    'explains an amount like «%s» next to the field (aria-describedby) instead of silently disabling the button', async (typed) => {
      const dialog = await openDialog();
      const input = within(dialog).getByLabelText(/Importe/);
      fireEvent.change(input, { target: { value: typed } });
      const hint = within(dialog).getByText(/sólo con números y punto decimal/);
      expect(input.getAttribute('aria-describedby')).toContain(hint.id);
      expect(input).toHaveAttribute('aria-invalid', 'true');
      const submit = within(dialog).getByRole('button', { name: 'Registrar pago' });
      expect(submit).not.toBeDisabled();
      fireEvent.click(submit);
      expect(await within(dialog).findByRole('alert')).toHaveTextContent('sólo con números y punto decimal');
      expect(mockPay).not.toHaveBeenCalled();
    });

  it('refuses a non-positive amount and a future date without calling the server', async () => {
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/Importe/), { target: { value: '0' } });
    expect(within(dialog).getByText('El importe debe ser mayor que cero.')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar pago' }));
    expect(mockPay).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText(/Importe/), { target: { value: '10' } });
    fireEvent.change(within(dialog).getByLabelText('Fecha del pago'), { target: { value: '2026-11-15' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar pago' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('no puede ser futura');
    expect(mockPay).not.toHaveBeenCalled();
  });

  it('sends a partial amount as typed', async () => {
    mockPay.mockResolvedValue({ payment: { id: 9 }, obligation: { ...OVERDUE, balance: '1500.00', paid: '1000.00', partially_paid: true } });
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText(/Importe/), { target: { value: '1000.00' } });
    fireEvent.change(within(dialog).getByLabelText('Forma de pago'), { target: { value: 'cash' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar pago' }));
    await waitFor(() => expect(mockPay).toHaveBeenCalled());
    expect(mockPay.mock.calls[0][2]).toMatchObject({ amount: '1000.00', method: 'cash' });
  });
});

describe('«Posponer aviso»', () => {
  async function openDialog(snoozedUntil: string | null = null) {
    mockAll.mockImplementation(async () => ({
      results: OPEN_ROWS.map((r) => (r.id === 1 ? { ...r, snoozed_until: snoozedUntil } : r)), count: 6, truncated: false,
    }));
    await renderCentre();
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Posponer aviso/ }));
    return screen.getByRole('dialog', { name: 'Posponer aviso' });
  }

  it('says it only silences MY reminders and does not change the payment', async () => {
    const dialog = await openDialog();
    expect(dialog).toHaveTextContent(/sólo silencia tus avisos/);
    expect(dialog).toHaveTextContent(/No cambia el pago/);
    expect(dialog).toHaveTextContent(/los demás siguen recibiendo sus avisos/);
    for (const label of ['1 día', '3 días', '7 días', '14 días']) {
      expect(within(dialog).getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(within(dialog).queryByRole('button', { name: 'Quitar pospuesto' })).toBeNull();
  });

  it('snoozes for the chosen days and leaves the debt and the state untouched', async () => {
    mockSnooze.mockResolvedValue({ snoozed_until: '2026-11-07' });
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: '7 días' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mockSnooze).toHaveBeenCalledWith(1, 1, 7);
    expect(mockPay).not.toHaveBeenCalled();
    const r = row('Alquiler del local');
    expect(r).toHaveTextContent('Mis avisos pospuestos hasta 7 nov 2026');
    expect(r).toHaveTextContent('Vencido');
    expect(r).toHaveTextContent(/2[.,\s]?500/);
    expect(screen.getByRole('status')).toHaveTextContent(/El pago no cambió/);
  });

  it('lets the person remove a snooze', async () => {
    mockUnsnooze.mockResolvedValue({ snoozed_until: null });
    const dialog = await openDialog('2026-11-05');
    expect(dialog).toHaveTextContent('5 nov 2026');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Quitar pospuesto' }));
    await waitFor(() => expect(mockUnsnooze).toHaveBeenCalledWith(1, 1));
    await waitFor(() => expect(row('Alquiler del local')).not.toHaveTextContent('pospuestos'));
  });

  it('shows the server error and keeps the dialog open', async () => {
    mockSnooze.mockRejectedValue(new FinanceApiError('Pospón el aviso entre 1 y 30 días.', 400));
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: '3 días' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('entre 1 y 30');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});

describe('reminder policy', () => {
  async function openNotices(policy = POLICY, caps: string[] = ['finance.view']) {
    mockPolicy.mockResolvedValue(policy);
    await renderCentre(caps);
    fireEvent.click(tab(/^Avisos/));
    return screen.findByRole('form', { name: 'Política de avisos' });
  }

  it('explains in plain Spanish that reminders are internal notices, email is optional and there is no WhatsApp', async () => {
    const form = await openNotices();
    expect(form).toHaveTextContent('notificación interna');
    expect(form).toHaveTextContent('correo es opcional');
    expect(form).toHaveTextContent('No se envía nada por WhatsApp');
    expect(form).toHaveTextContent(/nunca registra un pago/);
  });

  it('is read-only for someone who cannot manage it', async () => {
    const form = await openNotices();
    expect(within(form).getByLabelText('Días antes del vencimiento')).toBeDisabled();
    expect(within(form).getByLabelText('Días antes del vencimiento')).toHaveValue('7, 3, 1, 0');
    expect(within(form).getByLabelText(/Enviar avisos internos/)).toBeDisabled();
    expect(within(form).queryByRole('button', { name: /Guardar política/ })).toBeNull();
    expect(form).toHaveTextContent('modo lectura');
    expect(form).toHaveTextContent('Última revisión automática');
    expect(form).toHaveTextContent('Ninguno');
  });

  it('shows the last error the server recorded', async () => {
    const form = await openNotices({ ...POLICY, last_error: '1 aviso(s) fallaron; revisa el registro del servidor.' });
    expect(form).toHaveTextContent('1 aviso(s) fallaron');
  });

  it('is editable when the server says can_manage, and saves what was parsed', async () => {
    mockUpdatePolicy.mockImplementation(async (_c: unknown, change: Record<string, unknown>) => ({ ...POLICY, ...change, can_manage: true }));
    const form = await openNotices({ ...POLICY, can_manage: true });
    expect(form).not.toHaveTextContent('modo lectura');
    fireEvent.change(within(form).getByLabelText('Días antes del vencimiento'), { target: { value: '10, 2 ,2, 0' } });
    fireEvent.change(within(form).getByLabelText(/Si ya venció/), { target: { value: '5' } });
    fireEvent.change(within(form).getByLabelText(/Máximo de avisos/), { target: { value: '3' } });
    fireEvent.click(within(form).getByLabelText(/Enviar también por correo/));
    fireEvent.click(within(form).getByRole('button', { name: /Guardar política/ }));
    await waitFor(() => expect(mockUpdatePolicy).toHaveBeenCalledTimes(1));
    expect(mockUpdatePolicy.mock.calls[0][0]).toBe(1);
    // ONLY what changed: `enabled` and `email_overdue` stay as they were and are not sent.
    expect(mockUpdatePolicy.mock.calls[0][1]).toEqual({
      offsets: [10, 2, 0], overdue_every_days: 5, overdue_max: 3, email_enabled: true, email_offsets: [0],
    });
    expect(await screen.findByText('Política de avisos guardada.', { selector: 'p' })).toBeInTheDocument();
  });

  it('does not call the server with offsets it can see are wrong', async () => {
    const form = await openNotices({ ...POLICY, can_manage: true });
    fireEvent.change(within(form).getByLabelText('Días antes del vencimiento'), { target: { value: '7, tres' } });
    fireEvent.click(within(form).getByRole('button', { name: /Guardar política/ }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('«tres» no es un número de días');
    fireEvent.change(within(form).getByLabelText('Días antes del vencimiento'), { target: { value: '99' } });
    fireEvent.click(within(form).getByRole('button', { name: /Guardar política/ }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('0 a 60');
    expect(mockUpdatePolicy).not.toHaveBeenCalled();
  });

  it('shows the server refusal', async () => {
    mockUpdatePolicy.mockRejectedValue(new FinanceApiError('Los avisos de vencido son de 0 a 12.', 400));
    const form = await openNotices({ ...POLICY, can_manage: true });
    fireEvent.change(within(form).getByLabelText(/Máximo de avisos/), { target: { value: '9' } });
    fireEvent.click(within(form).getByRole('button', { name: /Guardar política/ }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('de 0 a 12');
  });

  it('rejects an EMPTY «Máximo de avisos de vencido» instead of sending it as 0', async () => {
    const form = await openNotices({ ...POLICY, can_manage: true });
    fireEvent.change(within(form).getByLabelText(/Máximo de avisos/), { target: { value: '' } });
    fireEvent.click(within(form).getByRole('button', { name: /Guardar política/ }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('Escribe el máximo');
    fireEvent.change(within(form).getByLabelText(/Máximo de avisos/), { target: { value: '  ' } });
    fireEvent.click(within(form).getByRole('button', { name: /Guardar política/ }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('Escribe el máximo');
    fireEvent.change(within(form).getByLabelText(/Si ya venció/), { target: { value: '' } });
    fireEvent.click(within(form).getByRole('button', { name: /Guardar política/ }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('cada 1 a 60 días');
    expect(mockUpdatePolicy).not.toHaveBeenCalled();
  });

  it('says so, and sends nothing, when nothing changed', async () => {
    const form = await openNotices({ ...POLICY, can_manage: true });
    fireEvent.click(within(form).getByRole('button', { name: /Guardar política/ }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('No cambiaste nada');
    expect(mockUpdatePolicy).not.toHaveBeenCalled();
  });
});

describe('detail and deep link', () => {
  it('opens ?obligation=<id> straight onto that payment with its payments, reminders and history', async () => {
    mockSearch = 'obligation=1';
    mockOne.mockResolvedValue(detailOf(OVERDUE, {
      payments: [{ id: 3, amount: '500.00', currency: 'PEN', method: 'transfer', paid_on: '2026-10-22', reference: 'OP-77', notes: '', recorded_by: { id: 1, name: 'Ana' }, recorded_at: '2026-10-22T10:00:00-05:00', reversed: false, reversal_reason: '', reversed_at: null }],
    }));
    render(<FinanceCentre companyId={1} dashboard={dashboard(ALL_CAPS)} />);
    const drawer = await screen.findByRole('dialog', { name: 'Alquiler del local' });
    await within(drawer).findByText('Ref. OP-77');
    expect(mockOne).toHaveBeenCalledWith(1, 1);
    expect(drawer).toHaveTextContent('Se enviaron 2 aviso(s)');
    expect(drawer).toHaveTextContent('Pago vencido: Alquiler del local');
    expect(drawer).toHaveTextContent('Obligación creada');
    expect(drawer).toHaveTextContent('venció hace 11 días');
    expect(within(drawer).getByRole('button', { name: 'Registrar pago' })).toBeInTheDocument();
    expect(within(drawer).getByRole('button', { name: 'Editar regla' })).toBeInTheDocument();
    expect(within(drawer).getByRole('button', { name: 'Revertir este pago' })).toBeInTheDocument();
  });

  it('explains a payment that is not found (another company, another branch, gone)', async () => {
    mockSearch = 'obligation=999';
    render(<FinanceCentre companyId={1} dashboard={dashboard()} />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('No encontramos este pago');
    expect(alert).toHaveTextContent('otra empresa');
    fireEvent.click(within(alert).getByRole('button', { name: 'Volver a la lista' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('explains a refusal', async () => {
    mockSearch = 'obligation=1';
    mockOne.mockRejectedValue(new FinanceApiError('No tienes permiso para esta operación en esta empresa.', 403));
    render(<FinanceCentre companyId={1} dashboard={dashboard()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permiso para verlo');
  });

  it('ignores an id that is not a number', async () => {
    mockSearch = 'obligation=abc';
    await renderCentre();
    expect(mockOne).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens from a row and puts the id in the address, and clears it on close', async () => {
    await renderCentre();
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Ver obligación/ }));
    await screen.findByRole('dialog', { name: 'Alquiler del local' });
    expect(window.location.search).toBe('?obligation=1');
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar' }));
    expect(window.location.search).toBe('');
  });

  it('reverses a payment only with a reason', async () => {
    mockSearch = 'obligation=1';
    mockOne.mockResolvedValue(detailOf(OVERDUE, {
      payments: [{ id: 3, amount: '500.00', currency: 'PEN', method: 'cash', paid_on: '2026-10-22', reference: '', notes: '', recorded_by: null, recorded_at: '2026-10-22T10:00:00-05:00', reversed: false, reversal_reason: '', reversed_at: null }],
    }));
    mockReverse.mockResolvedValue({ payment: {}, obligation: {} });
    render(<FinanceCentre companyId={1} dashboard={dashboard(ALL_CAPS)} />);
    const drawer = await screen.findByRole('dialog', { name: 'Alquiler del local' });
    fireEvent.click(await within(drawer).findByRole('button', { name: 'Revertir este pago' }));
    fireEvent.click(within(drawer).getByRole('button', { name: 'Confirmar reversión' }));
    expect(await within(drawer).findByRole('alert')).toHaveTextContent('motivo');
    expect(mockReverse).not.toHaveBeenCalled();
    fireEvent.change(within(drawer).getByLabelText('Motivo de la reversión'), { target: { value: 'Se registró dos veces' } });
    fireEvent.click(within(drawer).getByRole('button', { name: 'Confirmar reversión' }));
    await waitFor(() => expect(mockReverse).toHaveBeenCalledWith(1, 1, 3, 'Se registró dos veces'));
  });

  it('does not offer to reverse without finance.payments.reverse', async () => {
    mockSearch = 'obligation=1';
    mockOne.mockResolvedValue(detailOf(OVERDUE, {
      payments: [{ id: 3, amount: '500.00', currency: 'PEN', method: 'cash', paid_on: '2026-10-22', reference: '', notes: '', recorded_by: null, recorded_at: '2026-10-22T10:00:00-05:00', reversed: false, reversal_reason: '', reversed_at: null }],
    }));
    render(<FinanceCentre companyId={1} dashboard={dashboard(['finance.view', 'finance.payments.record'])} />);
    const drawer = await screen.findByRole('dialog', { name: 'Alquiler del local' });
    await within(drawer).findByText('Pagos registrados');
    expect(within(drawer).queryByRole('button', { name: 'Revertir este pago' })).toBeNull();
  });
});

describe('calendar', () => {
  it('draws the due dates of the month with their states and filters the list by day', async () => {
    await renderCentre();
    const grid = screen.getByTestId('finance-calendar-grid');
    // The grid starts on the company's month (octubre), not on the next one.
    expect(within(grid).queryByRole('button', { name: /3 de noviembre/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Mes siguiente' }));
    const nov3 = within(screen.getByTestId('finance-calendar-grid')).getByRole('button', { name: /martes 3 de noviembre de 2026: 2 por vencer/ });
    fireEvent.click(nov3);
    expect(screen.getByText(/Vencen el martes 3 de noviembre de 2026/)).toBeInTheDocument();
    expect(listText()).toContain('Internet');
    expect(listText()).toContain('Hosting');
    expect(listText()).not.toContain('Alquiler del local');
    fireEvent.click(screen.getByRole('button', { name: 'Quitar filtro de día' }));
    expect(listText()).toContain('Alquiler del local');
  });

  it('has a compact agenda for narrow screens next to the grid', async () => {
    await renderCentre();
    const agenda = screen.getByTestId('finance-calendar-agenda');
    expect(agenda.className).toContain('sm:hidden');
    expect(screen.getByTestId('finance-calendar-grid').className).toContain('hidden sm:block');
    // October: the overdue ones (20th, 25th) and today (31st).
    expect(within(agenda).getAllByRole('button')).toHaveLength(3);
    expect(within(agenda).getByText(/31 oct · hoy/)).toBeInTheDocument();
  });

  it('marks today from the server date', async () => {
    await renderCentre();
    expect(within(screen.getByTestId('finance-calendar-grid')).getByText(/31 · hoy/)).toBeInTheDocument();
  });
});

describe('states', () => {
  it('shows an error instead of an empty list when the payments cannot be read', async () => {
    mockAll.mockRejectedValue(new FinanceApiError('No se pudieron cargar los pagos.', 500));
    render(<FinanceCentre companyId={1} dashboard={dashboard()} />);
    expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent('No se pudieron cargar los pagos.');
    expect(screen.queryByTestId('finance-empty')).toBeNull();
  });

  it('shows a loading state before the first answer', async () => {
    mockSummary.mockReturnValue(new Promise(() => {}));
    mockAll.mockReturnValue(new Promise(() => {}));
    render(<FinanceCentre companyId={1} dashboard={dashboard()} />);
    expect(screen.getByText('Cargando el resumen…')).toBeInTheDocument();
    expect(screen.getByText('Cargando los pagos…')).toBeInTheDocument();
    await settle();
  });

  it('shows the summary error', async () => {
    mockSummary.mockRejectedValue(new FinanceApiError('No tienes permiso para esta operación en esta empresa.', 403));
    render(<FinanceCentre companyId={1} dashboard={dashboard()} />);
    expect(await screen.findByText(/No tienes permiso/)).toBeInTheDocument();
  });
});

describe('rules', () => {
  it('lists the rules and lets a manager pause one with a reason', async () => {
    mockAct.mockResolvedValue({ ...RULE, paused: true, paused_from: TODAY });
    await renderCentre(['finance.view', 'finance.manage']);
    fireEvent.click(tab(/^Reglas/));
    const rule = (await screen.findByText('Día 20 de cada mes', { exact: false })).closest('li') as HTMLElement;
    expect(rule).toHaveTextContent('Mensual');
    expect(rule).toHaveTextContent('Activa');
    fireEvent.click(within(rule).getByRole('button', { name: /Pausar regla/ }));
    const dialog = screen.getByRole('dialog', { name: 'Pausar regla' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Pausar' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('motivo');
    expect(mockAct).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText('Motivo'), { target: { value: 'Local cerrado' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Pausar' }));
    await waitFor(() => expect(mockAct).toHaveBeenCalledWith(1, 5, 'pause', { reason: 'Local cerrado' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByText('Pausada')).toBeInTheDocument();
  });

  it('edits only the fields that changed and locks the calendar once periods exist', async () => {
    mockUpdateRule.mockResolvedValue({ ...RULE, amount: '2600.00' });
    await renderCentre(['finance.view', 'finance.manage']);
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Editar regla/ }));
    const dialog = screen.getByRole('dialog', { name: 'Editar regla' });
    expect(within(dialog).getByLabelText('Día del mes')).toBeDisabled();
    expect(dialog).toHaveTextContent('termina esta regla y crea otra');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar regla' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('No cambiaste nada');
    fireEvent.change(within(dialog).getByLabelText(/Importe/), { target: { value: '2600.00' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar regla' }));
    await waitFor(() => expect(mockUpdateRule).toHaveBeenCalledWith(1, 5, { amount: '2600.00' }));
  });

  it('ends a rule with a date and a reason', async () => {
    mockAct.mockResolvedValue({ ...RULE, ended: true, end_date: TODAY });
    await renderCentre(['finance.view', 'finance.manage']);
    fireEvent.click(tab(/^Reglas/));
    fireEvent.click(await screen.findByRole('button', { name: /Terminar regla/ }));
    const dialog = screen.getByRole('dialog', { name: 'Terminar regla' });
    expect(within(dialog).getByLabelText('Fecha de fin')).toHaveValue(TODAY);
    fireEvent.change(within(dialog).getByLabelText('Motivo'), { target: { value: 'Contrato terminado' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Terminar regla' }));
    await waitFor(() => expect(mockAct).toHaveBeenCalledWith(1, 5, 'end', { reason: 'Contrato terminado', end_date: TODAY }));
  });
});

describe('dialog focus', () => {
  it('keeps Tab inside the dialog', async () => {
    await renderCentre(['finance.view', 'finance.payments.record']);
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Registrar pago/ }));
    const dialog = screen.getByRole('dialog', { name: 'Registrar pago' });
    const stops = [...dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])')];
    const last = stops[stops.length - 1];
    last.focus();
    fireEvent.keyDown(last, { key: 'Tab' });
    expect(document.activeElement).toBe(stops[0]);
    fireEvent.keyDown(stops[0], { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(last);
  });
});

describe('«Completados»: honest about what the 90 days measure', () => {
  it('says the window is the DUE date, and that rows without one are not listed', async () => {
    await renderCentre();
    fireEvent.click(tab(/^Completados/));
    await screen.findByText('Agua septiembre');
    const panel = screen.getByRole('tabpanel');
    expect(panel).toHaveTextContent('cuyo vencimiento cae en los últimos 90 días');
    expect(panel).toHaveTextContent('no la del pago');
    expect(panel).toHaveTextContent('no tienen fecha de vencimiento no se listan');
    expect(panel).not.toHaveTextContent('saldados en los últimos 90 días');
  });

  it('the empty state says «con vencimiento en los últimos 90 días»', async () => {
    mockAll.mockImplementation(async (_c: unknown, f: { state?: string }) => (
      f.state === 'paid' ? { results: [], count: 0, truncated: false } : { results: OPEN_ROWS, count: 6, truncated: false }));
    await renderCentre();
    fireEvent.click(tab(/^Completados/));
    expect(await screen.findByTestId('finance-empty')).toHaveTextContent('con vencimiento en los últimos 90 días');
  });
});

describe('a refresh that fails does not pass for a good answer', () => {
  it('after a payment, if the re-read fails the stale money gets a role=alert banner with a retry', async () => {
    mockPay.mockResolvedValue({ payment: { id: 9 }, obligation: { ...OVERDUE, balance: '1500.00', paid: '1000.00', partially_paid: true, snoozed_until: null } });
    await renderCentre(['finance.view', 'finance.payments.record']);
    expect(screen.queryByTestId('finance-stale')).toBeNull();
    mockAll.mockRejectedValue(new FinanceApiError('No se pudieron cargar los pagos.', 500));
    mockSummary.mockRejectedValue(new FinanceApiError('No se pudo cargar el resumen.', 500));
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Registrar pago/ }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Registrar pago' }));
    const banner = await screen.findByTestId('finance-stale');
    expect(banner).toHaveAttribute('role', 'alert');
    expect(banner).toHaveTextContent('No se pudo actualizar; lo que ves puede estar desactualizado.');
    expect(banner).toHaveTextContent('No se pudieron cargar los pagos.');
    expect(banner).toHaveTextContent('No se pudo cargar el resumen.');
    // The data stays on screen (it is all there is), now flagged.
    expect(screen.getByTestId('finance-list')).toBeInTheDocument();
    expect(document.querySelector('[data-summary="overdue"]')).not.toBeNull();

    // Retry: the server answers again and the banner goes away.
    mockAll.mockImplementation(async () => ({ results: OPEN_ROWS, count: 6, truncated: false }));
    mockSummary.mockResolvedValue(SUMMARY);
    fireEvent.click(within(banner).getByRole('button', { name: 'Reintentar' }));
    await waitFor(() => expect(screen.queryByTestId('finance-stale')).toBeNull());
  });

  it('a failed completed-list refresh with data on screen is flagged too', async () => {
    mockOne.mockResolvedValue(detailOf(PAID, { payments: [{ id: 3, amount: '80.00', currency: 'PEN', method: 'cash', paid_on: '2026-10-10', reference: '', notes: '', recorded_by: null, recorded_at: '2026-10-10T10:00:00-05:00', reversed: false, reversal_reason: '', reversed_at: null }] }));
    mockReverse.mockResolvedValue({ payment: {}, obligation: {} });
    render(<FinanceCentre companyId={1} dashboard={dashboard(ALL_CAPS)} />);
    await screen.findByTestId('finance-list');
    fireEvent.click(tab(/^Completados/));
    await screen.findByText('Agua septiembre');
    mockAll.mockRejectedValue(new FinanceApiError('No se pudieron cargar los pagos completados.', 500));
    fireEvent.click(within(row('Agua septiembre')).getByRole('button', { name: /Ver obligación/ }));
    const drawer = await screen.findByRole('dialog', { name: 'Agua septiembre' });
    fireEvent.click(await within(drawer).findByRole('button', { name: 'Revertir este pago' }));
    fireEvent.change(within(drawer).getByLabelText('Motivo de la reversión'), { target: { value: 'Error' } });
    fireEvent.click(within(drawer).getByRole('button', { name: 'Confirmar reversión' }));
    const banner = await screen.findByTestId('finance-stale');
    expect(banner).toHaveTextContent('No se pudieron cargar los pagos completados.');
    expect(row('Agua septiembre')).toBeInTheDocument();
  });

  it('when the categories cannot be loaded the person is told, not left with an empty filter', async () => {
    mockCategories.mockRejectedValue(new FinanceApiError('No se pudieron cargar las categorías.', 500));
    await renderCentre();
    const banner = await screen.findByTestId('finance-stale');
    expect(banner).toHaveTextContent('No se pudieron cargar las categorías.');
    mockCategories.mockResolvedValue([{ id: 1, direction: 'payable', name: 'Alquiler', is_active: true }]);
    fireEvent.click(within(banner).getByRole('button', { name: 'Reintentar' }));
    await waitFor(() => expect(screen.queryByTestId('finance-stale')).toBeNull());
    expect(within(screen.getByLabelText('Categoría')).getByRole('option', { name: 'Alquiler' })).toBeInTheDocument();
  });

  it('with NOTHING on screen the plain error stands alone (no stale banner)', async () => {
    mockAll.mockRejectedValue(new FinanceApiError('No se pudieron cargar los pagos.', 500));
    render(<FinanceCentre companyId={1} dashboard={dashboard()} />);
    expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent('No se pudieron cargar los pagos.');
    expect(screen.queryByTestId('finance-stale')).toBeNull();
  });
});

describe('a payment answer never erases MY snooze', () => {
  it('keeps «Mis avisos pospuestos» after a partial payment whose response says snoozed_until null', async () => {
    mockAll.mockImplementation(async () => ({
      results: OPEN_ROWS.map((r) => (r.id === 1 ? { ...r, snoozed_until: '2026-11-07' } : r)), count: 6, truncated: false,
    }));
    mockPay.mockResolvedValue({ payment: { id: 9 }, obligation: { ...OVERDUE, balance: '1500.00', paid: '1000.00', partially_paid: true, snoozed_until: null } });
    await renderCentre(['finance.view', 'finance.payments.record']);
    expect(row('Alquiler del local')).toHaveTextContent('Mis avisos pospuestos hasta 7 nov 2026');
    // The re-read never answers, so what the row shows is only what the local merge produced.
    mockAll.mockReturnValue(new Promise(() => {}));
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Registrar pago/ }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Registrar pago' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(row('Alquiler del local')).toHaveTextContent('Parcial');
    expect(row('Alquiler del local')).toHaveTextContent('Mis avisos pospuestos hasta 7 nov 2026');
  });
});

describe('what the person sees after a reversal', () => {
  const paymentRow = { id: 3, amount: '500.00', currency: 'PEN', method: 'cash', paid_on: '2026-10-22', reference: '', notes: '', recorded_by: null, recorded_at: '2026-10-22T10:00:00-05:00', reversed: false, reversal_reason: '', reversed_at: null };

  it('the panel shows the restored balance and state, and the lists behind it are read again', async () => {
    mockSearch = 'obligation=1';
    const before = detailOf({ ...OVERDUE, balance: '2000.00', paid: '500.00', partially_paid: true } as never, { payments: [paymentRow] });
    const after = detailOf(OVERDUE, { payments: [{ ...paymentRow, reversed: true, reversal_reason: 'Se registró dos veces' }] });
    mockOne.mockResolvedValueOnce(before).mockResolvedValue(after);
    mockReverse.mockResolvedValue({ payment: {}, obligation: {} });
    render(<FinanceCentre companyId={1} dashboard={dashboard(ALL_CAPS)} />);
    const drawer = await screen.findByRole('dialog', { name: 'Alquiler del local' });
    await within(drawer).findByText('Revertir este pago');
    expect(drawer).toHaveTextContent(/Saldo pendiente\s*S\/\s*2,000\.00/);
    const listCalls = mockAll.mock.calls.length;
    fireEvent.click(within(drawer).getByRole('button', { name: 'Revertir este pago' }));
    fireEvent.change(within(drawer).getByLabelText('Motivo de la reversión'), { target: { value: 'Se registró dos veces' } });
    fireEvent.click(within(drawer).getByRole('button', { name: 'Confirmar reversión' }));
    await within(drawer).findByText(/Revertido: Se registró dos veces/);
    expect(drawer).toHaveTextContent(/Saldo pendiente\s*S\/\s*2,500\.00/);
    expect(drawer).toHaveTextContent('Vencido');
    expect(within(drawer).queryByRole('button', { name: 'Revertir este pago' })).toBeNull();
    await waitFor(() => expect(mockAll.mock.calls.length).toBeGreaterThan(listCalls));
  });

  it('if the re-read after the reversal fails the form is not left on «Revirtiendo…» and the last detail stays with a warning', async () => {
    mockSearch = 'obligation=1';
    mockOne.mockResolvedValueOnce(detailOf(OVERDUE, { payments: [paymentRow] }))
      .mockRejectedValue(new FinanceApiError('No se pudo conectar con el servidor.', 0));
    mockReverse.mockResolvedValue({ payment: {}, obligation: {} });
    render(<FinanceCentre companyId={1} dashboard={dashboard(ALL_CAPS)} />);
    const drawer = await screen.findByRole('dialog', { name: 'Alquiler del local' });
    fireEvent.click(await within(drawer).findByRole('button', { name: 'Revertir este pago' }));
    fireEvent.change(within(drawer).getByLabelText('Motivo de la reversión'), { target: { value: 'Error' } });
    fireEvent.click(within(drawer).getByRole('button', { name: 'Confirmar reversión' }));
    const warning = await within(drawer).findByText(/lo que ves puede estar desactualizado/);
    expect(warning).toBeInTheDocument();
    expect(within(drawer).queryByText('Revirtiendo…')).toBeNull();
    expect(within(drawer).getByText('Pagos registrados')).toBeInTheDocument();
    expect(mockReverse).toHaveBeenCalledTimes(1);
    // The stale detail still lists the payment as not reversed: its form opens fresh, not stuck on «Revirtiendo…».
    fireEvent.click(within(drawer).getByRole('button', { name: 'Revertir este pago' }));
    expect(within(drawer).getByRole('button', { name: 'Confirmar reversión' })).not.toBeDisabled();
    expect(within(drawer).queryByText('Revirtiendo…')).toBeNull();
    fireEvent.click(within(drawer).getByRole('button', { name: 'Cancelar' }));
    // «Reintentar» reads again.
    mockOne.mockResolvedValue(detailOf(OVERDUE, { payments: [{ ...paymentRow, reversed: true, reversal_reason: 'Error' }] }));
    fireEvent.click(within(drawer).getByRole('button', { name: 'Reintentar' }));
    await within(drawer).findByText(/Revertido: Error/);
  });

  it('names adjustment kinds in Spanish', async () => {
    mockSearch = 'obligation=1';
    mockOne.mockResolvedValue(detailOf(OVERDUE, { adjustments: [
      { id: 1, kind: 'increase', amount: '10.00', reason: 'Mora', created_at: '2026-10-22T10:00:00-05:00' },
      { id: 2, kind: 'write_off', amount: '5.00', reason: 'Castigo', created_at: '2026-10-23T10:00:00-05:00' },
      { id: 3, kind: 'amount_change', amount: '0.00', reason: 'Nuevo contrato', created_at: '2026-10-24T10:00:00-05:00' },
      { id: 4, kind: 'decrease', amount: '2.00', reason: 'Descuento', created_at: '2026-10-25T10:00:00-05:00' },
    ] }));
    render(<FinanceCentre companyId={1} dashboard={dashboard()} />);
    const drawer = await screen.findByRole('dialog', { name: 'Alquiler del local' });
    const section = (await within(drawer).findByText('Ajustes')).closest('section') as HTMLElement;
    for (const label of ['Cargo adicional', 'Castigo', 'Cambio de importe', 'Descuento']) expect(section).toHaveTextContent(label);
    for (const raw of ['increase', 'write_off', 'amount_change', 'decrease']) expect(section).not.toHaveTextContent(raw);
  });
});

describe('what this board does not manage', () => {
  it.each([
    ['a receivable', { direction: 'receivable', origin_type: 'manual' }, /cuenta por cobrar/],
    ['a repair\'s balance', { direction: 'payable', origin_type: 'repair' }, /saldo de una reparación/],
  ])('%s opened by deep link offers neither payment nor snooze and says why', async (_name, extra, words) => {
    mockSearch = 'obligation=1';
    mockOne.mockResolvedValue(detailOf({ ...OVERDUE, ...extra } as never));
    render(<FinanceCentre companyId={1} dashboard={dashboard(ALL_CAPS)} />);
    const drawer = await screen.findByRole('dialog', { name: 'Alquiler del local' });
    expect(await within(drawer).findByRole('note')).toHaveTextContent(words);
    expect(drawer).toHaveTextContent('Aquí sólo puedes consultarla');
    expect(within(drawer).queryByRole('button', { name: 'Registrar pago' })).toBeNull();
    expect(within(drawer).queryByRole('button', { name: 'Posponer aviso' })).toBeNull();
  });

  it('a normal payable still offers both', async () => {
    mockSearch = 'obligation=1';
    mockOne.mockResolvedValue(detailOf({ ...OVERDUE, origin_type: 'manual' } as never));
    render(<FinanceCentre companyId={1} dashboard={dashboard(ALL_CAPS)} />);
    const drawer = await screen.findByRole('dialog', { name: 'Alquiler del local' });
    expect(await within(drawer).findByRole('button', { name: 'Registrar pago' })).toBeInTheDocument();
    expect(within(drawer).getByRole('button', { name: 'Posponer aviso' })).toBeInTheDocument();
    expect(within(drawer).queryByRole('note')).toBeNull();
  });
});

describe('when /summary/ fails', () => {
  it('a deep link still opens the detail (without inventing «hoy») and the page says the summary failed', async () => {
    mockSearch = 'obligation=1';
    mockSummary.mockRejectedValue(new FinanceApiError('No se pudo cargar el resumen.', 500));
    render(<FinanceCentre companyId={1} dashboard={dashboard(ALL_CAPS)} />);
    const drawer = await screen.findByRole('dialog', { name: 'Alquiler del local' });
    await within(drawer).findByText('Pagos registrados');
    expect(drawer).toHaveTextContent('20 oct 2026');
    expect(drawer).not.toHaveTextContent(/vence hoy|venció hace|vence en/);
    // Without the company's today a payment cannot be offered honestly.
    expect(within(drawer).queryByRole('button', { name: 'Registrar pago' })).toBeNull();
    expect(screen.getAllByRole('alert').some((a) => a.textContent?.includes('No se pudo cargar el resumen.'))).toBe(true);
  });

  it('the rows say nothing about «cuándo» and offer no payment', async () => {
    mockSummary.mockRejectedValue(new FinanceApiError('No se pudo cargar el resumen.', 500));
    render(<FinanceCentre companyId={1} dashboard={dashboard(ALL_CAPS)} />);
    await screen.findByTestId('finance-list');
    expect(listText()).not.toMatch(/vence hoy|venció hace|vence en/);
    expect(within(row('Alquiler del local')).queryByRole('button', { name: /Registrar pago/ })).toBeNull();
    expect(within(row('Alquiler del local')).getByRole('button', { name: /Posponer aviso/ })).toBeInTheDocument();
  });
});

describe('the page notice', () => {
  it('goes away by itself after a while', async () => {
    jest.useFakeTimers();
    try {
      mockSnooze.mockResolvedValue({ snoozed_until: '2026-11-07' });
      await act(async () => { render(<FinanceCentre companyId={1} dashboard={dashboard()} />); await jest.advanceTimersByTimeAsync(10); });
      fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Posponer aviso/ }));
      await act(async () => { fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '7 días' })); await jest.advanceTimersByTimeAsync(10); });
      expect(screen.getByRole('status')).toHaveTextContent(/El pago no cambió/);
      await act(async () => { await jest.advanceTimersByTimeAsync(9_000); });
      expect(screen.getByRole('status')).toHaveTextContent(/El pago no cambió/);
      await act(async () => { await jest.advanceTimersByTimeAsync(2_000); });
      expect(screen.getByRole('status')).not.toHaveTextContent(/El pago no cambió/);
    } finally {
      jest.useRealTimers();
    }
  });

  it('is cleared by the next thing the person does', async () => {
    mockSnooze.mockResolvedValue({ snoozed_until: '2026-11-07' });
    await renderCentre();
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Posponer aviso/ }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '7 días' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/El pago no cambió/));
    fireEvent.click(tab(/^Hoy/));
    expect(screen.getByRole('status')).not.toHaveTextContent(/El pago no cambió/);
  });
});

describe('the calendar does not rely on colour alone', () => {
  it('draws a letter and a count next to each state dot, and explains the letters in the legend', async () => {
    await renderCentre();
    const legend = screen.getByRole('list', { name: 'Leyenda del calendario' });
    for (const text of ['V Vencido', 'H Vence hoy', 'P Por vencer', 'L Pendiente']) {
      expect(legend.textContent).toContain(text);
    }
    const grid = screen.getByTestId('finance-calendar-grid');
    expect(grid.textContent).toContain('V1'); // 21 oct / 27 oct overdue
    expect(grid.textContent).toContain('H1'); // today
    expect(screen.getByTestId('finance-calendar-agenda').textContent).toContain('V1');
  });
});

describe('dialog keyboard edge cases', () => {
  it('Shift+Tab from the panel itself goes to the LAST stop, Tab to the first', async () => {
    await renderCentre(['finance.view', 'finance.payments.record']);
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Registrar pago/ }));
    const dialog = screen.getByRole('dialog', { name: 'Registrar pago' });
    const stops = [...dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])')];
    dialog.focus();
    expect(document.activeElement).toBe(dialog);
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(stops[stops.length - 1]);
    dialog.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(stops[0]);
  });

  it('Esc still closes when the focus was dropped to the page (a button disabled mid-request)', async () => {
    await renderCentre(['finance.view', 'finance.payments.record']);
    fireEvent.click(within(row('Alquiler del local')).getByRole('button', { name: /Registrar pago/ }));
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
