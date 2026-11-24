import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

/**
 * SALES-QUOTE-01 — cotizaciones en el panel.
 *
 * Lo que se fija:
 *  - el editor nunca manda un precio ni un total: el servidor los calcula con las reglas de la caja;
 *  - el descuento manual sólo se ofrece con permiso;
 *  - la lista filtra por estado en el servidor;
 *  - convertir en venta manda una clave de idempotencia y la REUTILIZA al reintentar; un 409 de precio
 *    muestra los dos totales y sólo ofrece mantener el cotizado a quien puede (descuento auditado);
 *  - el menú activa «Cotizaciones» con su ruta y su permiso.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }), useParams: () => ({ id: '1' }) }));

/* eslint-disable @typescript-eslint/no-require-imports */
const { fetchWithAuth } = require('@/app/lib/auth');
const api = require('@/app/admin/lib/sales-quotes-api');
const { SalesQuotesList } = require('@/app/admin/components/sales-quotes/SalesQuotesList');
const { QuoteEditor } = require('@/app/admin/components/sales-quotes/QuoteEditor');
const { QuoteConvertDialog } = require('@/app/admin/components/sales-quotes/QuoteConvertDialog');
const { INTERNAL_MODULES } = require('@/app/admin/lib/internal-modules');
/* eslint-enable @typescript-eslint/no-require-imports */

const send = fetchWithAuth as jest.Mock;

function reply(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => headers[name] ?? null },
    json: async () => body,
    blob: async () => new Blob(['%PDF']),
  } as unknown as Response;
}

const CONTEXT = {
  branches: [{ id: 3, name: 'Centro' }],
  default_branch: 3,
  default_validity_days: 7,
  max_validity_days: 365,
  seller: { id: 9, name: 'Ana' },
  sellers: [],
  payment_methods: [{ value: 'cash', label: 'Efectivo' }, { value: 'card', label: 'Tarjeta' }],
  receipt_options: [],
  can_manage: true,
  can_approve: false,
  can_convert: true,
  can_apply_discount: false,
  can_assign_seller: false,
  can_order_over_stock: false,
  can_search_customers: false,
  can_use_device_picker: false,
};

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: 1, number: 'COT-000001', label: 'COT-000001', revision: 1, status: 'approved', status_label: 'Aprobada',
    branch: 3, branch_name: 'Centro', customer: null, customer_name: 'Prospecto', customer_document: '',
    seller_name: 'Ana', currency: 'PEN', total: '100.00', valid_until: '2026-12-01',
    created_at: '2026-11-24T10:00:00Z', converted_order: null, ...overrides,
  };
}

function detail(overrides: Record<string, unknown> = {}) {
  return {
    ...row(), origin: 'new', source_quote: null, customer_document_type: '', customer_document_number: '',
    customer_phone: '', customer_email: '', seller: 9, conditions: '', notes: '', subtotal: '100.00',
    discount_amount: '0.00', discount_source: 'none', discount_type: '', discount_value: null, discount_reason: '',
    discount_authorized_by: '', taxable_amount: '84.75', tax_amount: '15.25', tax_rate: '0.1800',
    items: [{
      id: 11, product: 5, description: 'Cable USB-C', details: '', code: '', quantity: 2, unit_price: '50.00',
      line_total: '100.00', on_order: false, supply_reason: '', priced_by: 'product', is_serialized: false, unit: null,
    }],
    created_by: 'ana', approved_by: 'jefe', approved_at: null, sent_at: null, decided_by: '', decided_at: null,
    decision_note: '', converted_at: null, history: [],
    actions: { edit: false, revise: true, duplicate: true, approve: false, send: true, decide: true, convert: true, print: true },
    ...overrides,
  };
}

beforeEach(() => send.mockReset());

describe('API helpers', () => {
  test('the draft body carries what was chosen and never a price or a total', () => {
    const draft = {
      ...api.emptyDraft(CONTEXT, '2026-11-24'),
      customer_name: ' Prospecto ',
      lines: [{ key: 'a', product: 5, name: 'Cable', quantity: 2, shownPrice: '50.00', on_order: false,
        supply_reason: '', unit: null, unitLabel: '', pricedBy: 'product' }],
    };
    const body = api.draftBody(draft);
    expect(body.items).toEqual([{ product: 5, quantity: 2 }]);
    expect(body.customer_name).toBe('Prospecto');
    expect(body.valid_until).toBe('2026-12-01');
    expect(JSON.stringify(body)).not.toMatch(/price|total/);
  });

  test('a draft without permission cannot carry a manual discount, and a used line needs its device', () => {
    const base = { ...api.emptyDraft(CONTEXT, '2026-11-24') };
    expect(api.draftProblem(base, false)).toMatch(/al menos un producto/);
    const line = { key: 'a', product: 5, name: 'Usado', quantity: 1, shownPrice: '0', on_order: false,
      supply_reason: '', unit: null, unitLabel: '', pricedBy: 'unit' };
    expect(api.draftProblem({ ...base, lines: [line] }, false)).toMatch(/seminuevo/);
    const priced = { ...base, lines: [{ ...line, pricedBy: 'product' }], discount_type: 'amount',
      discount_value: '10', discount_reason: 'Cliente' };
    expect(api.draftProblem(priced, false)).toMatch(/permiso/);
    expect(api.draftProblem(priced, true)).toBe('');
  });

  test('convert sends the idempotency key, drops cash for a card and reads a price change', async () => {
    send.mockResolvedValueOnce(reply(409, {
      detail: 'El precio cambió', code: 'price_changed', quoted_total: '100.00', current_total: '120.00',
      can_keep_quoted: false,
    }));
    const error = await api.convertQuote(1, { payment_method: 'card', amount_received: '5', terms_confirmed: true },
      'cot-venta-abc12345').catch((e: unknown) => e);
    const [url, init] = send.mock.calls[0];
    expect(url).toContain('/admin/sales/quotes/1/convert/');
    expect(init.headers['Idempotency-Key']).toBe('cot-venta-abc12345');
    expect(JSON.parse(init.body)).toEqual({ payment_method: 'card', terms_confirmed: true });
    expect(api.priceChangedOf(error)).toEqual({
      quoted_total: '100.00', current_total: '120.00', can_keep_quoted: false, detail: 'El precio cambió',
    });
    expect(api.priceChangedOf(new Error('x'))).toBeNull();
  });
});

test('the menu opens «Cotizaciones» with its route and capability', () => {
  const entry = INTERNAL_MODULES.find((m: { id: string }) => m.id === 'sales.quotes');
  expect(entry).toMatchObject({
    href: '/admin/sales/quotes', requiredCapabilities: ['sales.quotes.view'], status: 'implemented',
  });
});

test('the list shows the quotes and filters by status on the server', async () => {
  send.mockImplementation(async (url: string) => reply(200, {
    results: url.includes('status=draft') ? [] : [row()], count: 1, page: 1, page_size: 25,
  }));
  render(<SalesQuotesList context={CONTEXT} />);
  expect(await screen.findByRole('link', { name: 'COT-000001' })).toHaveAttribute('href', '/admin/sales/quotes/1');
  expect(screen.getByText('Aprobada')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Nueva cotización' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Estado'), { target: { value: 'draft' } });
  await waitFor(() => expect(send.mock.calls.some(([url]: [string]) => url.includes('status=draft'))).toBe(true));
  expect(await screen.findByText('No hay cotizaciones con estos filtros.')).toBeInTheDocument();
});

test('the editor adds a product from the search and saves without prices', async () => {
  jest.useFakeTimers();
  send.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.includes('/products/')) {
      return reply(200, { results: [{ id: 5, name: 'Cable USB-C', price: '50.00', available: 4, barcode: '',
        is_serialized: false, priced_by: 'product', on_order: null }] });
    }
    if (init?.method === 'POST') return reply(201, detail({ status: 'draft' }));
    return reply(404, {});
  });
  const onSaved = jest.fn();
  render(<QuoteEditor context={CONTEXT} initial={api.emptyDraft(CONTEXT, '2026-11-24')} quoteId={null} onSaved={onSaved} />);
  expect(screen.queryByLabelText('Valor')).not.toBeInTheDocument();     // no discount without permission
  fireEvent.change(screen.getByRole('combobox', { name: 'Buscar producto' }), { target: { value: 'cab' } });
  await act(async () => { jest.advanceTimersByTime(300); });
  jest.useRealTimers();
  fireEvent.click(await screen.findByRole('button', { name: 'Agregar' }));
  fireEvent.change(screen.getByLabelText('Cantidad de Cable USB-C'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  const post = send.mock.calls.find(([, init]: [string, RequestInit | undefined]) => init?.method === 'POST');
  const body = JSON.parse(post[1].body);
  expect(body.items).toEqual([{ product: 5, quantity: 3 }]);
  expect(body.branch).toBe(3);
  expect(JSON.stringify(body)).not.toMatch(/"price"|"total"|unit_price/);
});

test('the convert dialog shows both totals on a price change and retries with the SAME key', async () => {
  send
    .mockResolvedValueOnce(reply(409, { detail: 'Cambió', code: 'price_changed', quoted_total: '100.00',
      current_total: '120.00', can_keep_quoted: false }))
    .mockResolvedValueOnce(reply(201, { quote: detail({ status: 'converted' }), sale: { order_id: 77, total: '120.00' } }));
  const onConverted = jest.fn();
  render(<QuoteConvertDialog quote={detail()} context={CONTEXT} onClose={jest.fn()} onConverted={onConverted} />);
  const charge = screen.getByRole('button', { name: /Cobrar S\/ 100.00/ });
  expect(charge).toBeDisabled();                                       // terms and cash first
  fireEvent.change(screen.getByLabelText('Efectivo recibido'), { target: { value: '200' } });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(charge);
  expect(await screen.findByText('El precio cambió desde la cotización.')).toBeInTheDocument();
  expect(screen.getByText('S/ 120.00')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Mantener precio cotizado' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Cobrar precio de hoy' }));
  await waitFor(() => expect(onConverted).toHaveBeenCalled());
  const [first, second] = send.mock.calls;
  expect(first[1].headers['Idempotency-Key']).toBe(second[1].headers['Idempotency-Key']);
  expect(JSON.parse(second[1].body)).toMatchObject({ price_decision: 'current', expected_total: '120.00' });
});

test('keeping the quoted price is offered only when the server says the caller may', async () => {
  send.mockResolvedValueOnce(reply(409, { detail: 'Cambió', code: 'price_changed', quoted_total: '100.00',
    current_total: '120.00', can_keep_quoted: true }));
  render(<QuoteConvertDialog quote={detail()} context={{ ...CONTEXT, payment_methods: [{ value: 'card', label: 'Tarjeta' }] }}
    onClose={jest.fn()} onConverted={jest.fn()} />);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: /Cobrar S\/ 100.00/ }));
  expect(await screen.findByRole('button', { name: 'Mantener precio cotizado' })).toBeInTheDocument();
});

test('a serialized line asks for its devices before charging', () => {
  const quote = detail({
    items: [{ ...detail().items[0], description: 'Teléfono', is_serialized: true, quantity: 1 }],
  });
  render(<QuoteConvertDialog quote={quote} context={{ ...CONTEXT, payment_methods: [{ value: 'card', label: 'Tarjeta' }] }}
    onClose={jest.fn()} onConverted={jest.fn()} />);
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByLabelText('IMEI o serie para Teléfono')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Cobrar/ })).toBeDisabled();
});
