export {};
/**
 * FIN-REMIND-01 — el cliente de «Pagos fijos y vencimientos» (contrato de backend/store/finance_views.py).
 *
 *  - la consulta lleva sólo lo que la persona eligió, con los nombres del servidor, y siempre `direction=payable`;
 *  - los importes viajan y vuelven como cadenas: el cliente nunca los recalcula;
 *  - el pago manda `Idempotency-Key` en la CABECERA y la misma clave en un reintento;
 *  - posponer es POST/DELETE sobre `snooze/` y no toca nada más;
 *  - un error del servidor muestra su `detail`; un 403 sin detalle se dice con palabras;
 *  - las páginas se siguen hasta un tope y se declara cuando la lista quedó incompleta;
 *  - las capacidades salen del contexto del panel (el master pasa; el servidor sigue decidiendo).
 */
const mockAuthFetch = jest.fn();
jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: (...args: unknown[]) => mockAuthFetch(...args),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const api = require('@/app/admin/lib/finance-api');
/* eslint-enable @typescript-eslint/no-require-imports */

function ok(body: unknown, status = 200) {
  return Promise.resolve({ ok: true, status, json: async () => body });
}
function fail(status: number, body: unknown = {}) {
  return Promise.resolve({ ok: false, status, json: async () => body });
}
function lastCall() {
  const [url, init] = mockAuthFetch.mock.calls[mockAuthFetch.mock.calls.length - 1];
  return { url: String(url), init: (init ?? {}) as { method?: string; body?: string; headers?: Record<string, string> } };
}
function params(url: string) {
  return Object.fromEntries(new URLSearchParams(url.split('?')[1] ?? ''));
}

beforeEach(() => mockAuthFetch.mockReset());

describe('queries', () => {
  it('asks only for payables, for the selected company', async () => {
    mockAuthFetch.mockReturnValue(ok({ results: [], count: 0, page: 1, page_size: 50 }));
    await api.fetchObligations(7, {});
    const { url } = lastCall();
    expect(url.split('?')[0]).toBe('/api/admin/finance/obligations/');
    expect(params(url)).toEqual({ company: '7', direction: 'payable' });
  });

  it('carries state, branch, category, dates and paging with the server names', async () => {
    mockAuthFetch.mockReturnValue(ok({ results: [], count: 0, page: 2, page_size: 200 }));
    await api.fetchObligations(7, {
      state: 'open', branch: 3, category: 9, rule: 4, disputed: true, dueFrom: '2026-10-01', dueTo: '2026-10-31',
      page: 2, pageSize: 200,
    });
    expect(params(lastCall().url)).toEqual({
      company: '7', direction: 'payable', state: 'open', branch: '3', category: '9', rule: '4', disputed: 'true',
      due_from: '2026-10-01', due_to: '2026-10-31', page: '2', page_size: '200',
    });
  });

  it('leaves out what was not chosen (null branch, empty state, false flags)', () => {
    const q = params(api.buildObligationsQuery(null, { state: '', branch: null, category: null, disputed: false }));
    expect(q).toEqual({ direction: 'payable' });
  });

  it('reads the summary of payables', async () => {
    mockAuthFetch.mockReturnValue(ok({ today: '2026-10-31' }));
    await api.fetchSummary(2);
    expect(lastCall().url).toBe('/api/admin/finance/summary/?company=2&direction=payable');
  });
});

describe('fetchAllObligations', () => {
  const row = (id: number) => ({ id, concept: `Pago ${id}` });

  it('follows the pages until it has them all', async () => {
    mockAuthFetch
      .mockReturnValueOnce(ok({ count: 3, page: 1, page_size: 200, results: [row(1), row(2)] }))
      .mockReturnValueOnce(ok({ count: 3, page: 2, page_size: 200, results: [row(3)] }));
    const loaded = await api.fetchAllObligations(1, { state: 'open' });
    expect(loaded.results.map((r: { id: number }) => r.id)).toEqual([1, 2, 3]);
    expect(loaded.truncated).toBe(false);
    expect(mockAuthFetch).toHaveBeenCalledTimes(2);
    expect(params(String(mockAuthFetch.mock.calls[1][0])).page).toBe('2');
    expect(params(String(mockAuthFetch.mock.calls[0][0])).page_size).toBe(String(api.PAGE_SIZE));
  });

  it('stops at the page limit and says the list is incomplete', async () => {
    mockAuthFetch.mockImplementation(() => ok({ count: 5000, page: 1, page_size: 200, results: [row(1)] }));
    const loaded = await api.fetchAllObligations(1, {}, 3);
    expect(mockAuthFetch).toHaveBeenCalledTimes(3);
    expect(loaded.truncated).toBe(true);
    expect(loaded.count).toBe(5000);
  });

  it('does not loop on an empty page', async () => {
    mockAuthFetch.mockReturnValue(ok({ count: 10, page: 1, page_size: 200, results: [] }));
    const loaded = await api.fetchAllObligations(1, {});
    expect(mockAuthFetch).toHaveBeenCalledTimes(1);
    expect(loaded.truncated).toBe(true);
  });
});

describe('payments', () => {
  it('sends the key in the Idempotency-Key header and the amount as the string it was typed', async () => {
    mockAuthFetch.mockReturnValue(ok({ payment: { id: 1 }, obligation: { id: 5 } }, 201));
    await api.recordPayment(1, 5, { amount: '1250.00', method: 'transfer', paid_on: '2026-10-31', reference: 'OP-1' }, 'key-123');
    const { url, init } = lastCall();
    expect(url).toBe('/api/admin/finance/obligations/5/payments/?company=1');
    expect(init.method).toBe('POST');
    expect(init.headers?.['Idempotency-Key']).toBe('key-123');
    expect(JSON.parse(init.body!)).toEqual({
      amount: '1250.00', method: 'transfer', paid_on: '2026-10-31', reference: 'OP-1', notes: '',
    });
  });

  it('reuses the key it is given on a retry', async () => {
    mockAuthFetch.mockReturnValue(ok({ payment: {}, obligation: {} }, 201));
    const input = { amount: '10.00', method: 'cash', paid_on: '2026-10-31' };
    await api.recordPayment(1, 5, input, 'same');
    await api.recordPayment(1, 5, input, 'same');
    const keys = mockAuthFetch.mock.calls.map(([, init]) => init.headers['Idempotency-Key']);
    expect(keys).toEqual(['same', 'same']);
  });

  it('makes a different key per call to newIdempotencyKey', () => {
    expect(api.newIdempotencyKey()).not.toBe(api.newIdempotencyKey());
  });

  it('reverses a payment with its reason', async () => {
    mockAuthFetch.mockReturnValue(ok({ payment: {}, obligation: {} }));
    await api.reversePayment(1, 5, 8, 'Se registró dos veces');
    const { url, init } = lastCall();
    expect(url).toBe('/api/admin/finance/obligations/5/payments/8/reverse/?company=1');
    expect(JSON.parse(init.body!)).toEqual({ reason: 'Se registró dos veces' });
  });
});

describe('snooze', () => {
  it('posts the days and deletes to remove it, on the obligation only', async () => {
    mockAuthFetch.mockReturnValueOnce(ok({ snoozed_until: '2026-11-07' })).mockReturnValueOnce(ok({ snoozed_until: null }));
    expect(await api.snoozeObligation(1, 5, 7)).toEqual({ snoozed_until: '2026-11-07' });
    let call = lastCall();
    expect(call.url).toBe('/api/admin/finance/obligations/5/snooze/?company=1');
    expect(call.init.method).toBe('POST');
    expect(JSON.parse(call.init.body!)).toEqual({ days: 7 });

    await api.unsnoozeObligation(1, 5);
    call = lastCall();
    expect(call.init.method).toBe('DELETE');
    expect(call.init.body).toBeUndefined();
  });
});

describe('policy and rules', () => {
  it('patches only the fields it is given', async () => {
    mockAuthFetch.mockReturnValue(ok({ enabled: false, can_manage: true }));
    await api.updatePolicy(1, { enabled: false, offsets: [7, 1] });
    const { url, init } = lastCall();
    expect(url).toBe('/api/admin/finance/reminder-policy/?company=1');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body!)).toEqual({ enabled: false, offsets: [7, 1] });
  });

  it('routes the rule actions', async () => {
    mockAuthFetch.mockReturnValue(ok({ id: 4 }));
    await api.actOnRule(1, 4, 'pause', { reason: 'Local cerrado' });
    expect(lastCall().url).toBe('/api/admin/finance/rules/4/pause/?company=1');
    await api.actOnRule(1, 4, 'end', { reason: 'Mudanza', end_date: '2026-12-31' });
    expect(lastCall().url).toBe('/api/admin/finance/rules/4/end/?company=1');
    expect(JSON.parse(lastCall().init.body!)).toEqual({ reason: 'Mudanza', end_date: '2026-12-31' });
    await api.updateRule(1, 4, { amount: '900.00' });
    expect(lastCall().init.method).toBe('PATCH');
    expect(lastCall().url).toBe('/api/admin/finance/rules/4/?company=1');
  });

  it('unwraps the lists of rules and categories', async () => {
    mockAuthFetch.mockReturnValueOnce(ok({ results: [{ id: 1 }] })).mockReturnValueOnce(ok({ results: [{ id: 2 }] }));
    expect(await api.fetchRules(1)).toEqual([{ id: 1 }]);
    expect(await api.fetchCategories(1)).toEqual([{ id: 2 }]);
    expect(params(lastCall().url).direction).toBe('payable');
  });
});

describe('errors', () => {
  it('shows the sentence the server wrote', async () => {
    mockAuthFetch.mockReturnValue(fail(400, { detail: 'El saldo pendiente es 100.00 PEN.' }));
    await expect(api.recordPayment(1, 5, { amount: '999', method: 'cash', paid_on: '2026-10-31' }, 'k'))
      .rejects.toMatchObject({ message: 'El saldo pendiente es 100.00 PEN.', status: 400, name: 'FinanceApiError' });
  });

  it('keeps the fallback sentence when the body is not JSON', async () => {
    mockAuthFetch.mockReturnValue(Promise.resolve({ ok: false, status: 500, json: async () => { throw new Error('html'); } }));
    await expect(api.fetchSummary(1)).rejects.toMatchObject({ message: 'No se pudo cargar el resumen.', status: 500 });
  });

  it('explains a bare 403 and carries a 404 as it is', async () => {
    mockAuthFetch.mockReturnValueOnce(fail(403)).mockReturnValueOnce(fail(404, { detail: 'No encontrado.' }));
    await expect(api.snoozeObligation(1, 5, 3)).rejects.toMatchObject({ status: 403, message: expect.stringMatching(/permiso/) });
    await expect(api.fetchObligation(1, 99)).rejects.toMatchObject({ status: 404, message: 'No encontrado.' });
  });

  it('says so when the network is down', async () => {
    mockAuthFetch.mockRejectedValue(new Error('Failed to fetch'));
    await expect(api.fetchSummary(1)).rejects.toMatchObject({ status: 0, message: expect.stringMatching(/conectar/) });
  });
});

describe('capabilities', () => {
  const dashboard = (caps: string[], master = false) => ({ access: { is_platform_admin: master, capabilities: caps } });

  it('reads them from the panel context', () => {
    expect(api.hasFinanceCapability(dashboard(['finance.view']), api.CAP_VIEW)).toBe(true);
    expect(api.hasFinanceCapability(dashboard(['finance.view']), api.CAP_RECORD)).toBe(false);
    expect(api.hasFinanceCapability(dashboard([], true), api.CAP_REVERSE)).toBe(true);
    expect(api.hasFinanceCapability(null, api.CAP_VIEW)).toBe(false);
  });
});

describe('words', () => {
  it('names the due day the way the rule reads it', () => {
    expect(api.dueDayLabel({ frequency: 'monthly', due_day: 15 })).toBe('Día 15 de cada mes');
    expect(api.dueDayLabel({ frequency: 'weekly', due_day: 0 })).toBe('Cada lunes');
    expect(api.dueDayLabel({ frequency: 'yearly', due_day: 3 })).toBe('Día 3 de cada año');
  });

  it('formats money per currency without converting', () => {
    expect(api.money('1250.00', 'PEN')).toMatch(/1[.,\s]?250/);
    expect(api.money(null)).toBe('—');
  });
});
