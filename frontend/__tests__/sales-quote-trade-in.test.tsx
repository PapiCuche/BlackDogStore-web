import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

/**
 * TRADE-IN-QUOTE-00 — el equipo ofrecido en parte de pago dentro de una cotización.
 *
 * Lo que se fija (reglas del propietario):
 *  - el ejemplo 4500 / 1200 / 3300: el total cotizado no cambia; el saldo estimado sólo se muestra;
 *  - el bloque es opcional y plegable; se manda como `trade_in`, nunca como descuento manual;
 *  - del IMEI sólo los últimos 4 dígitos: un IMEI pegado se queda en sus 4 últimos;
 *  - la conversión avisa «La parte de pago aún no se puede aplicar: la venta se cobra completa» y cobra el total.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }), useParams: () => ({ id: '1' }) }));

/* eslint-disable @typescript-eslint/no-require-imports */
const { fetchWithAuth } = require('@/app/lib/auth');
const api = require('@/app/admin/lib/sales-quotes-api');
const { QuoteEditor } = require('@/app/admin/components/sales-quotes/QuoteEditor');
const { QuoteConvertDialog } = require('@/app/admin/components/sales-quotes/QuoteConvertDialog');
const { QuoteDetailView } = require('@/app/admin/components/sales-quotes/QuoteDetailView');
/* eslint-enable @typescript-eslint/no-require-imports */

const send = fetchWithAuth as jest.Mock;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => null }, json: async () => body } as unknown as Response;
}

const CONTEXT = {
  branches: [{ id: 3, name: 'Centro' }], default_branch: 3, default_validity_days: 7, max_validity_days: 365,
  seller: { id: 9, name: 'Ana' }, sellers: [],
  payment_methods: [{ value: 'cash', label: 'Efectivo' }, { value: 'card', label: 'Tarjeta' }],
  receipt_options: [], can_manage: true, can_approve: false, can_convert: true, can_apply_discount: false,
  can_assign_seller: false, can_order_over_stock: false, can_search_customers: false, can_use_device_picker: false,
};

const TRADE_IN = {
  device_description: 'Equipo usado modelo anterior', capacity: '128 GB', color: 'Azul', condition: 'good',
  condition_label: 'Buen estado', battery_health: 86, accessories: 'Caja y cable', imei_last4: '4321',
  customer_asking_price: '1500.00', estimated_valuation: '1200.00', estimated_balance: '3300.00',
  valuation_notes: 'Rayón leve', valued_by: 'Ana', valued_at: '2026-11-24T10:00:00Z', applicable: false,
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    id: 1, number: 'COT-000001', label: 'COT-000001', revision: 1, status: 'approved', status_label: 'Aprobada',
    branch: 3, branch_name: 'Centro', customer: null, customer_name: 'Prospecto', customer_document: '',
    seller_name: 'Ana', currency: 'PEN', total: '4500.00', valid_until: '2026-12-01',
    created_at: '2026-11-24T10:00:00Z', converted_order: null, origin: 'new', source_quote: null,
    customer_document_type: '', customer_document_number: '', customer_phone: '', customer_email: '', seller: 9,
    conditions: '', notes: '', subtotal: '4500.00', discount_amount: '0.00', discount_source: 'none',
    discount_type: '', discount_value: null, discount_reason: '', discount_authorized_by: '',
    taxable_amount: '3813.56', tax_amount: '686.44', tax_rate: '0.1800',
    items: [{ id: 11, product: 5, description: 'Equipo nuevo', details: '', code: '', quantity: 1,
      unit_price: '4500.00', line_total: '4500.00', on_order: false, supply_reason: '', priced_by: 'product',
      is_serialized: false, unit: null }],
    created_by: 'ana', approved_by: 'jefe', approved_at: null, sent_at: null, decided_by: '', decided_at: null,
    decision_note: '', converted_at: null, history: [], trade_in: TRADE_IN,
    actions: { edit: false, revise: true, duplicate: true, approve: false, send: true, decide: false, convert: true, print: true },
    ...overrides,
  };
}

const LINE = { key: 'a', product: 5, name: 'Equipo nuevo', quantity: 1, shownPrice: '4500.00', on_order: false,
  supply_reason: '', unit: null, unitLabel: '', pricedBy: 'product' };

beforeEach(() => send.mockReset());

describe('API helpers', () => {
  test('the block goes as `trade_in`, apart from the money, never as a manual discount', () => {
    const draft = { ...api.emptyDraft(CONTEXT, '2026-11-24'), lines: [LINE],
      trade_in: { ...api.emptyTradeIn(), device_description: ' Equipo usado ', condition: 'good',
        estimated_valuation: '1200.00', imei_last4: '4321', battery_health: '86' } };
    const body = api.draftBody(draft);
    expect(body.trade_in).toEqual({
      device_description: 'Equipo usado', capacity: '', color: '', condition: 'good', battery_health: 86,
      accessories: '', imei_last4: '4321', customer_asking_price: null, estimated_valuation: '1200.00',
      valuation_notes: '',
    });
    expect(body.discount_type).toBe('');
    expect(body.discount_value).toBeNull();
    expect(api.draftBody({ ...draft, trade_in: null }).trade_in).toBeNull();
  });

  test('the owner example: 4500 − 1200 = 3300, only shown', () => {
    expect(api.estimatedBalance('4500.00', '1200.00')).toBe(3300);
    expect(api.estimatedBalance(4500, '4500')).toBe(0);
  });

  test('the block is checked before saving, and never takes a full IMEI or a document', () => {
    const base = { ...api.emptyTradeIn(), device_description: 'Equipo', condition: 'good', estimated_valuation: '1200' };
    expect(api.tradeInProblem(base)).toBe('');
    expect(api.tradeInProblem({ ...base, device_description: '' })).toMatch(/Describe/);
    expect(api.tradeInProblem({ ...base, condition: '' })).toMatch(/estado/);
    expect(api.tradeInProblem({ ...base, estimated_valuation: '' })).toMatch(/valorización/);
    expect(api.tradeInProblem({ ...base, estimated_valuation: '-1' })).toMatch(/valorización/);
    expect(api.tradeInProblem({ ...base, battery_health: '101' })).toMatch(/batería/);
    expect(api.tradeInProblem({ ...base, imei_last4: '123' })).toMatch(/4 dígitos/);
    expect(api.tradeInProblem({ ...base, accessories: 'IMEI 3567 8901 2345 678' })).toMatch(/IMEI completo/);
    expect(api.tradeInProblem({ ...base, valuation_notes: 'DNI 45678912' })).toMatch(/IMEI completo/);
    for (const text of ['1234/5678/9012/345', '1234_5678_9012_345', '1234,5678,9012,345', '1234\u20135678\u20139012\u2013345',
      '3567\u00a08901\u00a02345\u00a0678', 'DNI 12,345,678', 'DNI 12.345.678', 'IMEI: 3567 / 8901 / 2345 / 678',
      '12,345,678', '12.345.678', 'S/ 356789012345678', '35678901234567.8']) {
      expect(api.looksLikeIdentifier(text)).toBe(true);
      expect(api.tradeInProblem({ ...base, accessories: text })).toMatch(/IMEI completo/);
    }
    for (const text of ['Comprado el 2024-03-15', 'Comprado el 15/03/2024', 'Factura del 15-03-2024', 'Desde 1/3/24',
      'Modelo 2023, 128 GB, 2 cables', 'Revisado 05/11/2026 por la tienda',
      'S/ 1,200.00', '1.200,50', '4 500', '1,200.00 / 1,500.00', 'S/4500 - S/1200 = S/3300',
      'Pide S/. 1,500 y ofrecemos USD 300', '$ 1,200 o PEN 1 250', 'Entre 12,345,678.00 y 1,200.50',
      'Valorado 1,200.00 el 15/03/2024, revisado 2024-03-20 por S/ 1,150']) {
      expect(api.looksLikeIdentifier(text)).toBe(false);
      expect(api.tradeInProblem({ ...base, valuation_notes: text })).toBe('');
    }
    const draft = { ...api.emptyDraft(CONTEXT, '2026-11-24'), lines: [LINE], trade_in: { ...base, condition: '' } };
    expect(api.draftProblem(draft, false)).toMatch(/estado/);
  });

  test('editing a quote loads its block', () => {
    expect(api.draftFromQuote(detail()).trade_in).toEqual({
      device_description: 'Equipo usado modelo anterior', capacity: '128 GB', color: 'Azul', condition: 'good',
      battery_health: '86', accessories: 'Caja y cable', imei_last4: '4321', customer_asking_price: '1500.00',
      estimated_valuation: '1200.00', valuation_notes: 'Rayón leve',
    });
    expect(api.draftFromQuote(detail({ trade_in: null })).trade_in).toBeNull();
  });
});

test('the editor block is optional, collapsible and saves the valuation apart from the total', async () => {
  send.mockResolvedValue(reply(201, detail({ status: 'draft' })));
  const onSaved = jest.fn();
  const initial = { ...api.emptyDraft(CONTEXT, '2026-11-24'), lines: [LINE] };
  render(<QuoteEditor context={CONTEXT} initial={initial} quoteId={null} onSaved={onSaved} />);
  const toggle = screen.getByRole('button', { name: 'Agregar equipo en parte de pago' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByLabelText('Valorización estimada')).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(screen.getByRole('button', { name: 'Ocultar detalle' })).toHaveAttribute('aria-expanded', 'true');
  fireEvent.change(screen.getByLabelText('Equipo (modelo)'), { target: { value: 'Equipo usado' } });
  fireEvent.change(screen.getByLabelText('Estado declarado'), { target: { value: 'good' } });
  // A pasted IMEI keeps only its last four digits.
  fireEvent.change(screen.getByLabelText('IMEI: últimos 4 dígitos'), { target: { value: '356789012344321' } });
  expect(screen.getByLabelText('IMEI: últimos 4 dígitos')).toHaveValue('4321');
  fireEvent.change(screen.getByLabelText('Valorización estimada'), { target: { value: '1200' } });
  const balance = screen.getByLabelText('Saldo estimado (referencial)');
  expect(within(balance).getByText('S/ 4500.00')).toBeInTheDocument();
  expect(within(balance).getByText('S/ 3300.00')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  const body = JSON.parse(send.mock.calls[0][1].body);
  expect(body.trade_in).toMatchObject({ device_description: 'Equipo usado', condition: 'good', imei_last4: '4321',
    estimated_valuation: '1200' });
  expect(JSON.stringify(body)).not.toContain('356789012344321');
  expect(body.discount_type).toBe('');
});

test('removing the block sends null', async () => {
  send.mockResolvedValue(reply(200, detail({ status: 'draft', trade_in: null })));
  const onSaved = jest.fn();
  render(<QuoteEditor context={CONTEXT} initial={api.draftFromQuote(detail({ status: 'draft' }))} quoteId={1}
    onSaved={onSaved} />);
  fireEvent.click(screen.getByRole('button', { name: 'Quitar parte de pago' }));
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(JSON.parse(send.mock.calls[0][1].body).trade_in).toBeNull();
});

test('the detail shows the block, the balance and the legend; internal data is labelled', () => {
  render(<QuoteDetailView quote={detail()} context={CONTEXT} onChange={jest.fn()} />);
  const block = screen.getByRole('region', { name: api.TRADE_IN_HEADING });
  expect(within(block).getByText('****4321')).toBeInTheDocument();
  expect(within(block).getByText('- S/ 1200.00')).toBeInTheDocument();
  expect(within(block).getByText('S/ 3300.00')).toBeInTheDocument();
  expect(within(block).getByText(api.TRADE_IN_LEGEND)).toBeInTheDocument();
  expect(within(block).getByText(/Interno/)).toBeInTheDocument();
  // The quote's own total stays the full price.
  expect(screen.getAllByText('S/ 4500.00').length).toBeGreaterThan(0);
});

test('the conversion warns and charges the FULL total', async () => {
  send.mockResolvedValueOnce(reply(201, { quote: detail({ status: 'converted' }), sale: { order_id: 7, total: '4500.00' } }));
  const onConverted = jest.fn();
  render(<QuoteConvertDialog quote={detail()} context={CONTEXT} onClose={jest.fn()} onConverted={onConverted} />);
  expect(screen.getByRole('note')).toHaveTextContent(api.TRADE_IN_NOT_APPLIED);
  const charge = screen.getByRole('button', { name: 'Cobrar S/ 4500.00' });
  fireEvent.change(screen.getByLabelText('Efectivo recibido'), { target: { value: '3300' } });
  fireEvent.click(screen.getByRole('checkbox'));
  expect(charge).toBeDisabled();                     // the balance does not pay the sale
  fireEvent.change(screen.getByLabelText('Efectivo recibido'), { target: { value: '4500' } });
  fireEvent.click(charge);
  await waitFor(() => expect(onConverted).toHaveBeenCalled());
  const body = JSON.parse(send.mock.calls[0][1].body);
  expect(body).not.toHaveProperty('trade_in');
  expect(JSON.stringify(body)).not.toMatch(/trade|discount/);
});

test('a quote without the block shows no warning', () => {
  render(<QuoteConvertDialog quote={detail({ trade_in: null })} context={CONTEXT} onClose={jest.fn()} onConverted={jest.fn()} />);
  expect(screen.queryByRole('note')).not.toBeInTheDocument();
});
