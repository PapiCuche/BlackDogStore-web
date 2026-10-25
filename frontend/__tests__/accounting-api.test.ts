/**
 * ACCOUNTING-03 — el cliente del centro contable (contrato de #174).
 *
 *  - la consulta lleva sólo lo que la persona eligió, con los nombres del servidor;
 *  - el período tributario viaja como `tax_period`, el rango como `period=range&from&to`;
 *  - `environment` sólo para la vista B;
 *  - un 403 o un 404 se dicen con palabras, sin filtrar el detalle del servidor;
 *  - las reglas provisionales se leen de `policy`, no se suponen.
 */
const mockFetch = jest.fn();
jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: (...args: unknown[]) => mockFetch(...args),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const api = require('@/app/admin/lib/accounting-api');
/* eslint-enable @typescript-eslint/no-require-imports */

function params(query: string) {
  return Object.fromEntries(new URLSearchParams(query));
}

describe('buildAccountingQuery', () => {
  it('asks for the current month by default, for the selected company', () => {
    expect(params(api.buildAccountingQuery(7, api.DEFAULT_FILTERS))).toEqual({ company: '7', period: 'month' });
  });

  it('sends a tax period alone', () => {
    const q = params(api.buildAccountingQuery(null, { ...api.DEFAULT_FILTERS, period: 'tax_period', taxPeriod: '202609' }));
    expect(q).toEqual({ tax_period: '202609' });
  });

  it('sends a custom range', () => {
    const q = params(api.buildAccountingQuery(null, {
      ...api.DEFAULT_FILTERS, period: 'range', from: '2026-09-01', to: '2026-09-15',
    }));
    expect(q).toEqual({ period: 'range', from: '2026-09-01', to: '2026-09-15' });
  });

  it('sends a day with its anchor date', () => {
    const q = params(api.buildAccountingQuery(null, { ...api.DEFAULT_FILTERS, period: 'day', date: '2026-09-30' }));
    expect(q).toEqual({ period: 'day', date: '2026-09-30' });
  });

  it('carries branch, channel, type and documents with the server names', () => {
    const q = params(api.buildAccountingQuery(1, {
      ...api.DEFAULT_FILTERS, branch: 4, channel: 'pos', kind: 'sale', documents: ['nota_interna', 'boleta'],
    }));
    expect(q).toEqual({ company: '1', period: 'month', branch: '4', channel: 'pos', kind: 'sale', doc: 'boleta,nota_interna' });
  });

  it('adds the environment only when asked, and the cursor', () => {
    const plain = params(api.buildAccountingQuery(null, { ...api.DEFAULT_FILTERS, environment: 'beta' }));
    expect(plain.environment).toBeUndefined();
    const fiscal = params(api.buildAccountingQuery(null, { ...api.DEFAULT_FILTERS, environment: 'beta' },
      { environment: true, cursor: 'abc' }));
    expect(fiscal).toMatchObject({ environment: 'beta', cursor: 'abc' });
  });
});

describe('fetchers', () => {
  beforeEach(() => mockFetch.mockReset());

  it('reads each view from its route', async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({ results: [] }) });
    await api.fetchOperations(3, api.DEFAULT_FILTERS);
    await api.fetchFiscalDocuments(3, api.DEFAULT_FILTERS);
    await api.fetchPending(3, api.DEFAULT_FILTERS);
    await api.fetchSummary(3, api.DEFAULT_FILTERS);
    const urls = mockFetch.mock.calls.map(([url]) => String(url).split('?')[0]);
    expect(urls).toEqual([
      '/api/admin/accounting/operations/',
      '/api/admin/accounting/fiscal-documents/',
      '/api/admin/accounting/pending/',
      '/api/admin/accounting/summary/',
    ]);
    expect(String(mockFetch.mock.calls[1][0])).toContain('environment=production');
    expect(String(mockFetch.mock.calls[0][0])).not.toContain('environment=');
  });

  it('says a 403 and a 404 in words, with the status', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ detail: 'x' }) });
    await expect(api.fetchOperations(null, api.DEFAULT_FILTERS)).rejects.toMatchObject({
      status: 403, message: 'No tienes permiso para ver el centro contable.',
    });
    mockFetch.mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({ detail: 'No encontrado.' }) });
    await expect(api.fetchOperations(null, api.DEFAULT_FILTERS)).rejects.toMatchObject({ status: 404 });
  });

  it('keeps the server sentence for a 400', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 400, json: async () => ({ detail: 'Un período abarca como mucho 366 días.' }),
    });
    await expect(api.fetchPending(null, api.DEFAULT_FILTERS)).rejects.toThrow('Un período abarca como mucho 366 días.');
  });
});

describe('policyLines', () => {
  it('describes each provisional rule the server states', () => {
    const lines = api.policyLines({
      status: 'provisional', pending_owner_decision: ['P1', 'P2', 'P3', 'P4'],
      service_revenue_basis: 'collection', sale_period_field: 'paid_at',
      granted_to_holders_of: 'company.manage', exclude_test_payments: true,
    });
    expect(lines).toHaveLength(4);
    expect(lines.join(' ')).toMatch(/servicio técnico.*cobra/);
    expect(lines.join(' ')).toMatch(/credenciales de prueba/);
  });

  it('never words anything as declared', () => {
    const words = Object.values({ ...api.DOCUMENT_LABELS, ...api.EVENT_LABELS, ...api.COMMERCIAL_STATE_LABELS })
      .join(' ').toLowerCase();
    for (const word of ['declarad', 'presentad', 'conciliad']) expect(words).not.toContain(word);
  });
});
