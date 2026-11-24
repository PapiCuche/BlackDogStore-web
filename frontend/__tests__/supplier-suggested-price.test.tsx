import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { SupplierOffersManager } from '@/app/admin/components/SupplierOffersManager';
import { SupplierPricingSettings, showsSupplierPricing } from '@/app/admin/components/SupplierPricingSettings';
import type { SupplierWeeklyRow } from '@/app/admin/lib/internal-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * SUPPLY-MARGIN-01 — «Precio sugerido: costo más margen configurable por empresa, presentado sólo como propuesta
 * revisable. No cambiar automáticamente los precios publicados.»
 *
 * El servidor calcula la sugerencia y la manda sólo a quien ve costos (`suggested_price`); la pantalla la muestra
 * junto al precio actual cuando la clave llega, nunca la calcula, y «Usar sugerido» sólo llena el campo del
 * formulario: nada se guarda hasta que la persona guarda.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/api', () => ({ API_BASE: '/api' }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown };
let writes: Call[];
let offers: Record<string, unknown>[];
let weekly: Record<string, unknown>;
let preview: unknown;

const SUPPLIER = {
  id: 5, name: 'Distribuidora Andina', document_type: 'ruc', document_number: '20123456789', contact_name: 'Rosa',
  phone: '999888777', email: 'ventas@andina.pe', notes: '', is_active: true, offer_count: 1,
};

const offerRow = (extra: Record<string, unknown> = {}) => ({
  id: 31, supplier_id: 5, supplier_name: 'Distribuidora Andina', product_id: 44, product_name: 'Equipo X 256 Blanco',
  product_slug: 'equipo-x-44', commercial_price: '4699.00', confirmed_quantity: 3, committed_quantity: 1, remaining: 2,
  valid_from: '2026-10-01', valid_until: '2026-10-31', lead_time_days: 7, is_active: true, deactivated_reason: '',
  state: 'active', updated_at: '2026-10-10T10:00:00Z', availability_confirmed: true, ...extra,
});

const weekRow = (extra: Partial<SupplierWeeklyRow> = {}): SupplierWeeklyRow => ({
  supplier_id: 5, supplier_name: 'Distribuidora Andina', supplier_active: true, product_id: 44,
  product_name: 'Equipo X 256 Blanco', offer_id: 31, model: 'Equipo X', capacity: '256 GB', color: 'Blanco',
  sim: null, region: null, condition_line: 'new', commercial_price: '105.00', variant_price: '120.00',
  confirmed_quantity: 8, committed_quantity: 0, remaining: 8, lead_time_days: 4, valid_from: '2026-11-23',
  valid_until: '2026-11-29', state: 'active', own_stock: 3, message_date: null, listed_price: null,
  declared_quantity: null, price_kept: false, previous: null, delta: 'sin_cambio', changes: [], ...extra,
});

const weeklyOf = (results: SupplierWeeklyRow[], withCost: boolean) => ({
  week: '2026-W48', week_start: '2026-11-23', week_end: '2026-11-29', previous_week_start: '2026-11-16',
  today: '2026-11-24', with_cost: withCost,
  counts: { nuevo: 0, precio_cambio: 0, cantidad_cambio: 0, retirado: 0, no_mencionado: 0, sin_cambio: results.length, inactivo: 0 },
  total: results.length, truncated: false, results,
});

const SUMMARY = { new: 0, updated: 1, unchanged: 0, sold_out: 0, errors: 0, skipped: 0, price_changes: 0, mappings: 0 };

const importRow = (data: Record<string, unknown>) => ({
  row_number: 2, action: 'update', errors: [], warnings: [],
  data: {
    supplier: 'Distribuidora Andina', product_name: 'Cargador', commercial_price: '60.00', confirmed_quantity: 5,
    valid_from: '2026-10-19', valid_until: '2026-10-25', lead_time_days: 4, offer_id: 32, declaration: 'new',
    changes: ['available'], ...data,
  },
});

const previewOf = (rows: unknown[]) => ({
  id: 61, status: 'previewed', rows_total: rows.length, rows_create: 0, rows_update: rows.length, rows_no_change: 0,
  rows_error: 0, cost_column_ignored: false, message_date: '2026-11-02', summary: SUMMARY, rows,
});

beforeEach(() => {
  writes = [];
  offers = [offerRow()];
  weekly = weeklyOf([weekRow()], false);
  preview = previewOf([]);
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      const body = init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}'));
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body });
      if (url.includes('/import/preview/')) return reply(preview);
      if (url.includes('/company-settings/')) return reply({ settings: body });
      return reply(offerRow(body as Record<string, unknown>));
    }
    if (url.includes('/admin/suppliers/')) return reply({ count: 1, page: 1, page_size: 25, results: [SUPPLIER] });
    if (url.includes('/admin/supplier-offers/weekly/')) return reply(weekly);
    if (url.includes('/admin/supplier-offers/')) return reply({ count: offers.length, page: 1, page_size: 25, results: offers });
    return reply({}, 404);
  });
});

const headersOf = (table: HTMLElement) => within(table).getAllByRole('columnheader').map((cell) => cell.textContent);

describe('semana de ofertas', () => {
  async function openWeek() {
    render(<SupplierOffersManager canManage canSeeCosts companyId={null} />);
    await act(async () => { fireEvent.click(screen.getByRole('tab', { name: 'Semana' })); });
    return screen.findByTestId('supplier-week-table');
  }

  it('con la clave del servidor, «Precio sugerido» va junto al precio de la oferta', async () => {
    weekly = weeklyOf([weekRow({ agreed_cost: '80.00', suggested_price: '110.00' }),
      weekRow({ product_id: 45, offer_id: 32, agreed_cost: null, suggested_price: null })], true);
    const table = await openWeek();
    const headers = headersOf(table);
    expect(headers.indexOf('Precio sugerido')).toBe(headers.indexOf('Precio oferta') + 1);
    const cells = within(within(table).getByTestId('week-row-5-44')).getAllByRole('cell').map((cell) => cell.textContent);
    expect(cells[headers.indexOf('Precio sugerido')]).toBe('S/ 110.00');
    const empty = within(within(table).getByTestId('week-row-5-45')).getAllByRole('cell').map((cell) => cell.textContent);
    expect(empty[headers.indexOf('Precio sugerido')]).toBe('Sin sugerencia');
    // Sólo se lee: ninguna escritura.
    expect(writes).toEqual([]);
  });

  it('en el teléfono la tarjeta también la muestra', async () => {
    weekly = weeklyOf([weekRow({ agreed_cost: '80.00', suggested_price: '110.00' })], true);
    await openWeek();
    expect(screen.getByTestId('supplier-week-cards')).toHaveTextContent('Precio sugeridoS/ 110.00');
  });

  it('sin la clave no hay columna', async () => {
    const table = await openWeek();
    expect(headersOf(table)).not.toContain('Precio sugerido');
    expect(screen.getByTestId('supplier-week-cards')).not.toHaveTextContent('Precio sugerido');
  });
});

describe('ofertas: lista y formulario', () => {
  async function editOffer() {
    render(<SupplierOffersManager canManage canSeeCosts companyId={null} />);
    const row = await screen.findByTestId('offer-row-31');
    fireEvent.click(within(row).getByRole('button', { name: /^Editar/ }));
    return screen.getByRole('form', { name: /Editar oferta/ });
  }

  it('la lista muestra la sugerencia sólo cuando el servidor la manda', async () => {
    offers = [offerRow({ agreed_cost: '4000.00', suggested_price: '5200.00' })];
    render(<SupplierOffersManager canManage canSeeCosts companyId={null} />);
    const row = await screen.findByTestId('offer-row-31');
    const table = row.closest('table') as HTMLElement;
    const headers = headersOf(table);
    expect(headers.indexOf('Precio sugerido')).toBe(headers.indexOf('Precio de venta') + 1);
    expect(within(row).getAllByRole('cell')[headers.indexOf('Precio sugerido')]).toHaveTextContent('S/ 5,200.00');
  });

  it('sin la clave, ni columna ni botón', async () => {
    const form = await editOffer();
    expect(headersOf(screen.getByTestId('offer-row-31').closest('table') as HTMLElement)).not.toContain('Precio sugerido');
    expect(within(form).queryByRole('button', { name: /Usar sugerido/ })).toBeNull();
    expect(within(form).queryByTestId('offer-suggested-price')).toBeNull();
  });

  it('«Usar sugerido» sólo llena el precio; se guarda cuando la persona guarda', async () => {
    offers = [offerRow({ agreed_cost: '4000.00', suggested_price: '5200.00' })];
    const form = await editOffer();
    const price = within(form).getByLabelText('Precio de venta bajo pedido (S/)');
    expect(price).toHaveValue('4699.00');
    expect(within(form).getByTestId('offer-suggested-price')).toHaveTextContent('S/ 5,200.00');
    fireEvent.click(within(form).getByRole('button', { name: 'Usar sugerido (S/ 5,200.00)' }));
    expect(price).toHaveValue('5200.00');
    expect(writes).toEqual([]);
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar oferta' })); });
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ method: 'PATCH', path: '/admin/supplier-offers/31/', body: { commercial_price: '5200.00' } });
    expect(writes[0].body).not.toHaveProperty('suggested_price');
  });

  it('sin costo o sin margen, lo dice y no ofrece el botón', async () => {
    offers = [offerRow({ agreed_cost: null, suggested_price: null })];
    const form = await editOffer();
    expect(within(form).getByTestId('offer-suggested-price')).toHaveTextContent('Sin precio sugerido');
    expect(within(form).queryByRole('button', { name: /Usar sugerido/ })).toBeNull();
  });
});

describe('vista previa del Excel', () => {
  async function upload() {
    render(<SupplierOffersManager canManage canSeeCosts companyId={null} />);
    await screen.findByTestId('offer-row-31');
    fireEvent.change(screen.getByLabelText('Archivo de ofertas (.xlsx)'), { target: { files: [new File(['x'], 'ofertas.xlsx')] } });
    fireEvent.change(screen.getByLabelText('Fecha del mensaje del proveedor'), { target: { value: '2026-11-02' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
    return screen.findByTestId('supplier-import-preview');
  }

  it('cada fila muestra la sugerencia junto a su precio, sin aplicar nada', async () => {
    preview = previewOf([importRow({ agreed_cost: '50.00', suggested_price: '70.00' })]);
    const shown = await upload();
    const table = within(shown).getByTestId('import-row-2').closest('table') as HTMLElement;
    const headers = headersOf(table);
    expect(headers.indexOf('Precio sugerido')).toBe(headers.indexOf('Precio') + 1);
    const cells = within(within(shown).getByTestId('import-row-2')).getAllByRole('cell');
    expect(cells[headers.indexOf('Precio')]).toHaveTextContent('S/ 60.00');
    expect(cells[headers.indexOf('Precio sugerido')]).toHaveTextContent('S/ 70.00');
    expect(writes.filter((call) => call.path.includes('/apply/'))).toEqual([]);
  });

  it('sin la clave no hay columna', async () => {
    preview = previewOf([importRow({})]);
    const shown = await upload();
    expect(headersOf(within(shown).getByTestId('import-row-2').closest('table') as HTMLElement)).not.toContain('Precio sugerido');
  });
});

describe('configuración: margen del precio sugerido', () => {
  function shown(props: Partial<React.ComponentProps<typeof SupplierPricingSettings>> = {}) {
    const onSaved = jest.fn();
    render(<SupplierPricingSettings companyId={7} margin={null} roundTo={null} canEdit onSaved={onSaved} {...props} />);
    return onSaved;
  }
  const save = () => screen.getByRole('button', { name: 'Guardar margen sugerido' });

  it('sólo se muestra a quien recibió las claves (ve costos)', () => {
    expect(showsSupplierPricing({ supplier_margin_percent: null, supplier_price_round_to: null })).toBe(true);
    expect(showsSupplierPricing({ city: 'Lima' })).toBe(false);
  });

  it('guarda sólo sus dos campos', async () => {
    const onSaved = shown();
    fireEvent.change(screen.getByLabelText('Margen sobre el costo (%)'), { target: { value: '30' } });
    fireEvent.change(screen.getByLabelText('Redondear hacia arriba a múltiplos de'), { target: { value: '10' } });
    await act(async () => { fireEvent.click(save()); });
    expect(writes).toEqual([{ method: 'PATCH', path: '/admin/company-settings/?company=7',
      body: { supplier_margin_percent: '30', supplier_price_round_to: 10 } }]);
    expect(onSaved).toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('Cambios guardados.');
  });

  it('vacío apaga la sugerencia', async () => {
    shown({ margin: '30.00', roundTo: 10 });
    fireEvent.change(screen.getByLabelText('Margen sobre el costo (%)'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Redondear hacia arriba a múltiplos de'), { target: { value: '' } });
    await act(async () => { fireEvent.click(save()); });
    expect(writes[0].body).toEqual({ supplier_margin_percent: null, supplier_price_round_to: null });
  });

  it('fuera de los límites no se envía', async () => {
    shown();
    for (const value of ['-1', '500.01', '12.345', 'abc']) {
      fireEvent.change(screen.getByLabelText('Margen sobre el costo (%)'), { target: { value } });
      await act(async () => { fireEvent.click(save()); });
    }
    expect(screen.getByText('Indica un porcentaje entre 0 y 500, con hasta dos decimales.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Margen sobre el costo (%)'), { target: { value: '30' } });
    for (const value of ['0', '2.5', '100001']) {
      fireEvent.change(screen.getByLabelText('Redondear hacia arriba a múltiplos de'), { target: { value } });
      await act(async () => { fireEvent.click(save()); });
    }
    expect(screen.getByText('Indica un número entero entre 1 y 100000, o déjalo vacío.')).toBeInTheDocument();
    expect(writes).toEqual([]);
  });

  it('el error del servidor se dice bajo su campo', async () => {
    send.mockImplementationOnce(async () => reply({ supplier_margin_percent: ['Asegúrese de que este valor es menor o igual a 500.'] }, 400));
    shown();
    fireEvent.change(screen.getByLabelText('Margen sobre el costo (%)'), { target: { value: '30' } });
    await act(async () => { fireEvent.click(save()); });
    expect(screen.getByText('Asegúrese de que este valor es menor o igual a 500.')).toBeInTheDocument();
  });

  it('sin autoridad se ve pero no se cambia', () => {
    shown({ canEdit: false, margin: '30.00' });
    expect(screen.getByLabelText('Margen sobre el costo (%)')).toBeDisabled();
    expect(screen.getByLabelText('Margen sobre el costo (%)')).toHaveValue('30.00');
    expect(screen.queryByRole('button', { name: 'Guardar margen sugerido' })).toBeNull();
    expect(screen.getByText(/settings\.manage/)).toBeInTheDocument();
  });

  it('dice que es sólo una propuesta', () => {
    shown();
    expect(screen.getByText(/nunca cambia un precio/)).toBeInTheDocument();
  });
});
