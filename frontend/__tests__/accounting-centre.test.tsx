import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

/**
 * ACCOUNTING-03 — la pantalla del centro contable, contra el contrato de #174 simulado.
 *
 * Lo que se fija:
 *  - el aviso «no es una declaración» está SIEMPRE, también antes de la primera respuesta;
 *  - el aviso de reglas provisionales aparece sólo si `policy.status` es `provisional`;
 *  - el enmascarado lo dice el servidor (`unmasked`) y la pantalla no lo deshace;
 *  - tres vistas en pestañas accesibles (flechas, Inicio, Fin), un mismo juego de filtros;
 *  - los filtros viajan al aplicar; un rango inválido se dice y no consulta;
 *  - la sucursal ofrece sólo las que devolvió el servidor;
 *  - un 403 se muestra como alerta; «Cargar más» añade la página siguiente;
 *  - B en pruebas avisa «sin validez tributaria»; C explica que la nota interna no vale.
 */
const mockOperations = jest.fn();
const mockFiscal = jest.fn();
const mockPending = jest.fn();
const mockSummary = jest.fn();
jest.mock('@/app/admin/lib/accounting-api', () => {
  const actual = jest.requireActual('@/app/admin/lib/accounting-api');
  return {
    ...actual,
    fetchOperations: (...args: unknown[]) => mockOperations(...args),
    fetchFiscalDocuments: (...args: unknown[]) => mockFiscal(...args),
    fetchPending: (...args: unknown[]) => mockPending(...args),
    fetchSummary: (...args: unknown[]) => mockSummary(...args),
  };
});

/* eslint-disable @typescript-eslint/no-require-imports */
const { AccountingCentre } = require('@/app/admin/accounting/_components/AccountingCentre');
const { AccountingError } = require('@/app/admin/lib/accounting-api');
const { INTERNAL_MODULES } = require('@/app/admin/lib/internal-modules');
const { canSeeAccounting, AccountingNoAccess } = require('@/app/admin/accounting/_components/AccountingAccess');
const { TotalsPanel } = require('@/app/admin/accounting/_components/TotalsPanel');
const { AccountingFiltersForm } = require('@/app/admin/accounting/_components/AccountingFiltersForm');
/* eslint-enable @typescript-eslint/no-require-imports */

const POLICY = {
  status: 'provisional', pending_owner_decision: ['P1', 'P2', 'P3', 'P4'],
  service_revenue_basis: 'collection', sale_period_field: 'paid_at',
  granted_to_holders_of: 'company.manage', exclude_test_payments: true,
};

function envelope(overrides: Record<string, unknown> = {}) {
  return {
    company: { id: 1, name: 'Tienda Contable' },
    period: { kind: 'month', from: '2026-09-01', to: '2026-09-30', timezone: 'America/Lima' },
    scope: { company_wide: true, branches: [{ id: 10, name: 'Centro' }, { id: 11, name: 'Norte' }], includes_unbranched: true },
    policy: POLICY,
    notice: 'Centro contable de ventas e ingresos. No incluye compras, gastos, bancos ni asientos contables.',
    unmasked: false,
    ...overrides,
  };
}

const TOTALS = {
  operations: 2, sales_gross: '177.00', sales_taxable: '150.00', sales_tax: '27.00', late_charges: '0.00',
  gross: '177.00', refunds_confirmed: '0.00', refunds_pending: '0.00', net: '177.00',
  service_collections: '50.00', service_reversals: '0.00', income: '227.00',
  excluded_test: { operations: 1, gross: '59.00', refunds_confirmed: '0.00' }, currencies: ['PEN'],
};

function saleRow(overrides: Record<string, unknown> = {}) {
  return {
    key: 'sale:41', kind: 'sale', order_id: 41, branch: { id: 10, name: 'Centro' }, channel: 'online', seller: null,
    customer: { name: 'Ana Compradora', document_type: 'dni', document_number: '****8912' }, currency: 'PEN',
    commercial_state: 'pagada', excluded_from_income: false,
    primary_document: { kind: 'boleta', number: 'B001-7', environment: 'production' }, flags: ['BOLETA_NOT_SUMMARISED'],
    row_type: 'operation', event: 'sale', date: '2026-09-15T12:00:00-05:00', amount: '118.00', gross: '118.00',
    taxable: '100.00', tax: '18.00', tax_rate: '0.1800', breakdown_source: 'frozen',
    documents: [{ kind: 'boleta', number: 'B001-7', state: 'pending_summary', environment: 'production' }],
    ...overrides,
  };
}

function operations(results = [saleRow()], extra: Record<string, unknown> = {}) {
  return { ...envelope(), totals: TOTALS, count: results.length, results, next_cursor: null, ...extra };
}

function fiscal(environment = 'production') {
  return {
    ...envelope(), environment, count: 1, next_cursor: null,
    totals: {
      '01': { count: 2, total: '236.00' }, '03': { count: 1, total: '118.00' },
      '07': { count: 1, total: '-18.00' }, '08': { count: 0, total: '0.00' },
    },
    excluded_totals: {
      rejected: { count: 1, total: '118.00' }, voided: { count: 0, total: '0.00' },
      notes_not_settled: { count: 0, total: '0.00' },
    },
    results: [{
      id: 5, key: 'sale:41', order_id: 41, type: '03', type_label: 'Boleta de venta electrónica', document_id: 'B001-7',
      issued_at: '2026-09-15T12:00:00-05:00', environment, test_environment: environment !== 'production',
      customer: { name: 'Ana Compradora', document_type: '1', document_number: '****8912' }, currency: 'PEN',
      taxable: '100.00', tax: '18.00', total: '118.00', tax_rate: '0.1800',
      state: { code: 'pending_summary', label: 'Firmada, pendiente de resumen diario', settled: false },
      sunat: { code: '', message: '' }, original: null,
      granted: { granted_at: null, method: '', not_granted: false },
      cdr: { present: false, response_code: '', hash_ok: null, authenticity: 'unverified' }, summary: null,
    }],
  };
}

function pending() {
  return {
    ...envelope(), counts: { INTERNAL_ONLY: 1 }, count: 1, next_cursor: null,
    results: [{
      key: 'sale:42', code: 'INTERNAL_ONLY', severity: 'regularizar',
      message: 'Sólo tiene nota de venta interna, que no tiene validez tributaria: la venta sigue sin comprobante.',
      kind: 'sale', order_id: 42, repair_order_id: null, branch: { id: 11, name: 'Norte' }, channel: 'pos',
      date: '2026-09-16T10:00:00-05:00', document: 'NV-000001',
    }],
  };
}

function summary(overrides: Record<string, unknown> = {}) {
  return { ...envelope(overrides), totals: TOTALS, pending: { INTERNAL_ONLY: 1, BOLETA_NOT_SUMMARISED: 1 } };
}

beforeEach(() => {
  mockOperations.mockReset().mockResolvedValue(operations());
  mockFiscal.mockReset().mockResolvedValue(fiscal());
  mockPending.mockReset().mockResolvedValue(pending());
  mockSummary.mockReset().mockResolvedValue(summary());
});

async function renderCentre(companyId: number | null = 1) {
  render(<AccountingCentre companyId={companyId} />);
  await screen.findByText('sale:41');
}

describe('notices', () => {
  it('says it is not a declaration even before the first answer', () => {
    mockSummary.mockReturnValue(new Promise(() => {}));
    mockOperations.mockReturnValue(new Promise(() => {}));
    render(<AccountingCentre companyId={1} />);
    expect(screen.getByText(/no es una declaración/i)).toBeInTheDocument();
    expect(screen.getByText(/Nada de lo que aparece aquí es una declaración presentada ante SUNAT/)).toBeInTheDocument();
  });

  it('shows the provisional banner from policy.status', async () => {
    await renderCentre();
    const banner = screen.getByTestId('accounting-policy-banner');
    expect(within(banner).getByText(/Reglas provisionales/)).toBeInTheDocument();
    expect(within(banner).getAllByRole('listitem')).toHaveLength(4);
    expect(banner).toHaveTextContent('P1, P2, P3, P4');
    expect(screen.getByText(/No incluye compras, gastos, bancos/)).toBeInTheDocument();
  });

  it('hides the banner once the rules are no longer provisional', async () => {
    const settled = { ...POLICY, status: 'final', pending_owner_decision: [] };
    mockSummary.mockResolvedValue(summary({ policy: settled }));
    mockOperations.mockResolvedValue({ ...operations(), policy: settled });
    await renderCentre();
    expect(screen.queryByTestId('accounting-policy-banner')).not.toBeInTheDocument();
  });

  it('says documents are masked and shows them as the server sent them', async () => {
    await renderCentre();
    expect(screen.getByTestId('accounting-masked-note')).toBeInTheDocument();
    expect(screen.getByText('****8912')).toBeInTheDocument();
    expect(screen.queryByText('45678912')).not.toBeInTheDocument();
  });

  it('drops the masked note when the server unmasked', async () => {
    mockSummary.mockResolvedValue(summary({ unmasked: true }));
    mockOperations.mockResolvedValue({
      ...operations([saleRow({ customer: { name: 'Ana', document_type: 'dni', document_number: '45678912' } })]),
      unmasked: true,
    });
    await renderCentre();
    expect(screen.queryByTestId('accounting-masked-note')).not.toBeInTheDocument();
    expect(screen.getByText('45678912')).toBeInTheDocument();
  });
});

describe('totals', () => {
  it('shows the server totals and the TEST payments apart', async () => {
    await renderCentre();
    expect(screen.getByText('Ingresos cobrados').nextSibling).toHaveTextContent('227.00');
    expect(screen.getByTestId('accounting-excluded-test')).toHaveTextContent('59.00');
  });
});

describe('tabs', () => {
  it('starts on view A and switches to B and C, asking each view', async () => {
    await renderCentre();
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
    fireEvent.click(screen.getByRole('tab', { name: /Comprobantes/ }));
    expect(await screen.findByText('Firmada, pendiente de resumen diario')).toBeInTheDocument();
    expect(mockFiscal).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', { name: /pendientes/ }));
    expect(await screen.findByText('INTERNAL_ONLY')).toBeInTheDocument();
    expect(screen.getByText(/Una nota de venta interna no tiene validez tributaria/)).toBeInTheDocument();
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', screen.getByRole('tab', { selected: true }).id);
  });

  it('moves with the arrow keys, Home and End', async () => {
    await renderCentre();
    const first = screen.getByRole('tab', { name: /Operaciones/ });
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: /Comprobantes/ })).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: /Comprobantes/ }));
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(screen.getByRole('tab', { name: /pendientes/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: /Operaciones/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(screen.getByRole('tab', { name: /Operaciones/ })).toHaveAttribute('tabindex', '0');
  });

  it('says what is listed but not added on view B', async () => {
    await renderCentre();
    fireEvent.click(screen.getByRole('tab', { name: /Comprobantes/ }));
    expect(await screen.findByTestId('accounting-excluded-totals')).toHaveTextContent('Rechazados 1');
  });

  it('warns that BETA documents have no tax validity', async () => {
    mockFiscal.mockResolvedValue(fiscal('beta'));
    await renderCentre();
    fireEvent.click(screen.getByRole('tab', { name: /Comprobantes/ }));
    expect(await screen.findByText(/no tienen validez tributaria/)).toBeInTheDocument();
    expect(screen.getByText('PRUEBAS')).toBeInTheDocument();
  });
});

describe('filters', () => {
  it('offers only the branches the server returned', async () => {
    await renderCentre();
    const branch = screen.getByLabelText('Sucursal');
    const names = within(branch).getAllByRole('option').map((o) => o.textContent);
    expect(names).toEqual(['Todas las que puedo ver', 'Centro', 'Norte']);
  });

  it('applies a tax period, branch, channel, type and documents together', async () => {
    await renderCentre();
    fireEvent.change(screen.getByLabelText('Período'), { target: { value: 'tax_period' } });
    fireEvent.change(screen.getByLabelText('Mes'), { target: { value: '09' } });
    fireEvent.change(screen.getByLabelText('Año'), { target: { value: '2026' } });
    fireEvent.change(screen.getByLabelText('Sucursal'), { target: { value: '11' } });
    fireEvent.change(screen.getByLabelText('Canal'), { target: { value: 'pos' } });
    fireEvent.change(screen.getByLabelText('Tipo de operación'), { target: { value: 'sale' } });
    fireEvent.click(screen.getByLabelText('Nota de venta interna'));
    mockOperations.mockClear();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    });
    await waitFor(() => expect(mockOperations).toHaveBeenCalled());
    const [companyId, filters] = mockOperations.mock.calls.at(-1);
    expect(companyId).toBe(1);
    expect(filters).toMatchObject({
      period: 'tax_period', taxPeriod: '202609', branch: 11, channel: 'pos', kind: 'sale', documents: ['nota_interna'],
    });
  });

  it('refuses an inverted range without asking the server', async () => {
    await renderCentre();
    fireEvent.change(screen.getByLabelText('Período'), { target: { value: 'range' } });
    fireEvent.change(screen.getByLabelText('Desde'), { target: { value: '2026-10-02' } });
    fireEvent.change(screen.getByLabelText('Hasta'), { target: { value: '2026-10-01' } });
    mockOperations.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    expect(screen.getByRole('alert')).toHaveTextContent('El rango termina antes de empezar.');
    expect(mockOperations).not.toHaveBeenCalled();
  });

  it('refuses a year and a range over 92 days on view A, and admits them on view B', async () => {
    await renderCentre();
    fireEvent.change(screen.getByLabelText('Período'), { target: { value: 'year' } });
    mockOperations.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    expect(screen.getByRole('alert')).toHaveTextContent('92 días');
    expect(mockOperations).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Período'), { target: { value: 'range' } });
    fireEvent.change(screen.getByLabelText('Desde'), { target: { value: '2026-01-01' } });
    fireEvent.change(screen.getByLabelText('Hasta'), { target: { value: '2026-04-30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    expect(screen.getByRole('alert')).toHaveTextContent('92 días');

    fireEvent.click(screen.getByRole('tab', { name: /Comprobantes/ }));
    await screen.findByLabelText('Ambiente fiscal');
    mockFiscal.mockClear();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    });
    await waitFor(() => expect(mockFiscal).toHaveBeenCalled());
    expect(mockFiscal.mock.calls.at(-1)[1]).toMatchObject({ period: 'range', from: '2026-01-01', to: '2026-04-30' });
  });

  it('shows the fiscal environment only on view B', async () => {
    await renderCentre();
    expect(screen.queryByLabelText('Ambiente fiscal')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /Comprobantes/ }));
    expect(await screen.findByLabelText('Ambiente fiscal')).toBeInTheDocument();
  });
});

describe('errors and paging', () => {
  it('shows a 403 as an alert', async () => {
    mockSummary.mockRejectedValue(new AccountingError('No tienes permiso para ver el centro contable.', 403));
    mockOperations.mockRejectedValue(new AccountingError('No tienes permiso para ver el centro contable.', 403));
    render(<AccountingCentre companyId={1} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permiso para ver el centro contable.');
  });

  it('appends the next page', async () => {
    mockOperations.mockResolvedValueOnce(operations([saleRow()], { count: 2, next_cursor: 'b2s=' }));
    mockOperations.mockResolvedValueOnce(operations([saleRow({ key: 'sale:43', order_id: 43 })], { count: 2 }));
    await renderCentre();
    fireEvent.click(screen.getByRole('button', { name: 'Cargar más' }));
    expect(await screen.findByText('sale:43')).toBeInTheDocument();
    expect(screen.getByText('sale:41')).toBeInTheDocument();
    expect(mockOperations.mock.calls.at(-1)[2]).toBe('b2s=');
    expect(screen.getByText('2 de 2')).toBeInTheDocument();
  });

  it('marks adjustments and rows out of income', async () => {
    mockOperations.mockResolvedValue(operations([
      saleRow({ row_type: 'adjustment', event: 'refund', amount: '-118.00' }),
      saleRow({ key: 'sale:44', order_id: 44, excluded_from_income: true }),
    ]));
    await renderCentre();
    expect(screen.getByText('Ajuste')).toBeInTheDocument();
    expect(screen.getByText('Reembolso confirmado')).toBeInTheDocument();
    expect(screen.getByText('Fuera del ingreso (prueba)')).toBeInTheDocument();
  });

  it('names a repair reversal as its own adjustment', async () => {
    mockOperations.mockResolvedValue(operations([
      saleRow({ key: 'repair:9', kind: 'repair', order_id: undefined, repair_order_id: 9, channel: 'service',
                row_type: 'adjustment', event: 'service_reversal', amount: '-30.00', primary_document: null }),
    ]));
    render(<AccountingCentre companyId={1} />);
    expect(await screen.findByText('Reverso de cobro de servicio')).toBeInTheDocument();
    expect(screen.getByText('repair:9')).toBeInTheDocument();
  });
});

describe('menu entry', () => {
  it('opens the screen with accounting.view, without a legacy bridge', () => {
    const entry = INTERNAL_MODULES.find((m: { id: string }) => m.id === 'reports.accounting');
    expect(entry).toMatchObject({ href: '/admin/accounting', requiredCapabilities: ['accounting.view'], status: 'implemented' });
    expect(entry.legacyRoles).toBeUndefined();
  });
});

describe('review of #177', () => {
  it('renders view B over 92 days even when the summary answers 400', async () => {
    mockSummary.mockRejectedValue(new AccountingError('Esta vista abarca como mucho 92 días: elige un período más corto.', 400));
    render(<AccountingCentre companyId={1} />);
    fireEvent.click(await screen.findByRole('tab', { name: /Comprobantes/ }));
    expect(await screen.findByText('Firmada, pendiente de resumen diario')).toBeInTheDocument();
    expect(screen.getByTestId('accounting-summary-note')).toHaveTextContent('92 días');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('does not ask for the summary of a period it cannot answer', async () => {
    await renderCentre();
    fireEvent.click(screen.getByRole('tab', { name: /Comprobantes/ }));
    await screen.findByLabelText('Ambiente fiscal');
    fireEvent.change(screen.getByLabelText('Período'), { target: { value: 'year' } });
    mockSummary.mockClear();
    mockFiscal.mockClear();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    });
    await waitFor(() => expect(mockFiscal).toHaveBeenCalled());
    expect(mockSummary).not.toHaveBeenCalled();
    expect(screen.getByTestId('accounting-summary-note')).toBeInTheDocument();
  });

  it('shows the per-type totals of view B exactly as returned', async () => {
    await renderCentre();
    fireEvent.click(screen.getByRole('tab', { name: /Comprobantes/ }));
    const totals = await screen.findByTestId('accounting-fiscal-totals');
    const { money } = jest.requireActual('@/app/admin/lib/accounting-api');
    expect(totals).toHaveTextContent('Facturas');
    // Raw text: `money()` separates the symbol with a no-break space.
    expect(totals.textContent).toContain(`2 · ${money('236.00', 'PEN')}`);
    expect(totals.textContent).toContain(money('-18.00', 'PEN'));
    expect(totals.textContent).not.toContain('236.00 PEN');
  });

  it('counts view-C flags only on operation rows', async () => {
    mockOperations.mockResolvedValue(operations([
      saleRow({ key: 'sale:50', order_id: 50, row_type: 'adjustment', event: 'refund', amount: '-1.00', flags: ['NO_DOCUMENT'] }),
    ]));
    render(<AccountingCentre companyId={1} />);
    await screen.findByText('sale:50');
    expect(screen.queryByText(/aviso\(s\) en la vista C/)).not.toBeInTheDocument();
  });

  it('names the table scrollers as regions', async () => {
    await renderCentre();
    expect(screen.getByRole('region', { name: 'Tabla de operaciones' })).toBeInTheDocument();
  });
});

describe('totals panel', () => {
  it('shows sales gross with its base and tax, and late charges on their own', () => {
    render(<TotalsPanel totals={{ ...TOTALS, late_charges: '12.00', gross: '189.00' }} pending={{}} />);
    const sales = screen.getByText('Ventas brutas').parentElement!;
    expect(sales).toHaveTextContent('177.00');
    expect(sales).toHaveTextContent('Base');
    expect(screen.getByTestId('accounting-late-charges')).toHaveTextContent('12.00');
  });
});

describe('access', () => {
  const base = { access: { is_platform_admin: false, capabilities: ['company.view'] } };

  it('opens with accounting.view or as platform master, and not otherwise', () => {
    expect(canSeeAccounting(null)).toBe(false);
    expect(canSeeAccounting(base)).toBe(false);
    expect(canSeeAccounting({ access: { ...base.access, capabilities: ['accounting.view'] } })).toBe(true);
    expect(canSeeAccounting({ access: { is_platform_admin: true, capabilities: [] } })).toBe(true);
  });

  it('says there is no access in words', () => {
    render(<AccountingNoAccess />);
    expect(screen.getByRole('heading', { name: 'Sin acceso al centro contable' })).toBeInTheDocument();
    expect(screen.getByText(/Ver el centro contable/)).toBeInTheDocument();
  });
});

describe('default tax period', () => {
  afterEach(() => jest.useRealTimers());

  it('is the company month, not the browser one', () => {
    jest.useFakeTimers().setSystemTime(new Date('2026-09-30T12:00:00Z'));
    const onApply = jest.fn();
    const { DEFAULT_FILTERS } = jest.requireActual('@/app/admin/lib/accounting-api');
    render(<AccountingFiltersForm value={DEFAULT_FILTERS} branches={[]} showEnvironment={false}
                                  timezone="Pacific/Kiritimati" onApply={onApply} />);
    fireEvent.change(screen.getByLabelText('Período'), { target: { value: 'tax_period' } });
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    expect(onApply.mock.calls[0][0].taxPeriod).toBe('202610');
  });
});

describe('switching off view B', () => {
  it('re-checks the period: a year applied on B is not asked of view A', async () => {
    await renderCentre();
    fireEvent.click(screen.getByRole('tab', { name: /Comprobantes/ }));
    await screen.findByLabelText('Ambiente fiscal');
    fireEvent.change(screen.getByLabelText('Período'), { target: { value: 'year' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    });
    await waitFor(() => expect(mockFiscal).toHaveBeenCalled());
    mockOperations.mockClear();
    mockPending.mockClear();
    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: /Operaciones/ }));
    });
    expect(mockOperations).not.toHaveBeenCalled();
    expect(within(screen.getByRole('tabpanel')).getByRole('alert')).toHaveTextContent('92 días');
    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: /pendientes/ }));
    });
    expect(mockPending).not.toHaveBeenCalled();
  });
});
