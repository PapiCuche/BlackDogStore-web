import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { OfferStateBadge, SupplierOffersManager } from '@/app/admin/components/SupplierOffersManager';
import { SUPPLIER_OFFER_STATE_LABELS } from '@/app/admin/lib/internal-api';
import { applyWeeklyImport } from '@/app/admin/lib/supplier-import-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * SUPPLY-GUARDS-01 — una oferta de un proveedor inactivo no se ve «Vigente»; un precio por debajo del costo (o
 * sobre un costo que la persona no ve) se confirma antes de guardarse o aplicarse; el panel nunca muestra un costo
 * que el servidor no mandó.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown };
let writes: Call[];
let lookedUp: string[];
let offers: Record<string, unknown>[];
let preview: unknown;
let writeAnswers: Array<() => Response>;

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

const SUMMARY = { new: 0, updated: 1, unchanged: 0, sold_out: 0, errors: 0, skipped: 0, price_changes: 0, mappings: 0 };

const importRow = (data: Record<string, unknown>, warnings: string[] = []) => ({
  row_number: 2, action: 'update', errors: [], warnings,
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
  lookedUp = [];
  offers = [offerRow()];
  writeAnswers = [];
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body: init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}')) });
      if (url.includes('/import/preview/')) return reply(preview);
      const next = writeAnswers.shift();
      return next ? next() : reply({ status: 'applied', created: 0, updated: 1, unchanged: 0 });
    }
    if (url.includes('/admin/suppliers/')) return reply({ count: 1, page: 1, page_size: 25, results: [SUPPLIER] });
    if (url.includes('/admin/supplier-offers/')) {
      lookedUp.push(url);
      return reply({ count: offers.length, page: 1, page_size: 25, results: offers });
    }
    return reply({}, 404);
  });
});

describe('proveedor inactivo', () => {
  it('el estado se llama «Proveedor inactivo», nunca «Vigente»', () => {
    expect(SUPPLIER_OFFER_STATE_LABELS.supplier_inactive).toBe('Proveedor inactivo');
    render(<OfferStateBadge state="supplier_inactive" />);
    expect(screen.getByText('Proveedor inactivo')).toBeInTheDocument();
    expect(screen.queryByText('Vigente')).toBeNull();
  });

  it('la tabla lo muestra y el filtro lo pide al servidor', async () => {
    offers = [offerRow({ state: 'supplier_inactive' })];
    render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
    const row = await screen.findByTestId('offer-row-31');
    expect(row).toHaveTextContent('Proveedor inactivo');
    expect(row).not.toHaveTextContent('Vigente');
    const filter = within(screen.getByRole('group', { name: 'Filtrar ofertas' })).getByLabelText('Estado');
    expect(within(filter).getByRole('option', { name: 'Proveedor inactivo' })).toHaveValue('supplier_inactive');
    await act(async () => { fireEvent.change(filter, { target: { value: 'supplier_inactive' } }); });
    expect(lookedUp.at(-1)).toContain('state=supplier_inactive');
  });
});

describe('precio por debajo del costo: el formulario', () => {
  async function editPrice(canSeeCosts: boolean, price: string) {
    offers = [offerRow(canSeeCosts ? { agreed_cost: '4000.00' } : {})];
    render(<SupplierOffersManager canManage canSeeCosts={canSeeCosts} companyId={null} />);
    const row = await screen.findByTestId('offer-row-31');
    fireEvent.click(within(row).getByRole('button', { name: /^Editar/ }));
    const form = screen.getByRole('form', { name: /Editar oferta/ });
    fireEvent.change(within(form).getByLabelText('Precio de venta bajo pedido (S/)'), { target: { value: price } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar oferta' })); });
    return form;
  }

  it('con costos: muestra lo que dijo el servidor y guarda sólo al confirmar', async () => {
    writeAnswers = [() => reply({
      detail: 'El precio comercial (S/ 3500.00) queda por debajo del costo pactado (S/ 4000.00): la oferta se vendería a pérdida. Confírmalo para guardarla así.',
      code: 'below_cost_confirmation_required', field: 'commercial_price', commercial_price: '3500.00', agreed_cost: '4000.00',
    }, 409)];
    const form = await editPrice(true, '3500.00');
    const ask = within(form).getByTestId('offer-margin-confirm');
    expect(ask).toHaveTextContent('por debajo del costo pactado');
    expect(writes).toHaveLength(1);
    expect(writes[0].body).not.toHaveProperty('confirm_below_cost');
    await act(async () => { fireEvent.click(within(ask).getByRole('button', { name: 'Guardar de todas formas' })); });
    expect(writes).toHaveLength(2);
    expect(writes[1]).toMatchObject({ method: 'PATCH', path: '/admin/supplier-offers/31/', body: { commercial_price: '3500.00', confirm_below_cost: true } });
  });

  it('sin costos: el aviso genérico del servidor, sin importes, y la misma confirmación', async () => {
    writeAnswers = [() => reply({
      detail: 'Esta oferta tiene un costo pactado que no puedes ver, así que no se comprueba si el nuevo precio lo cubre.',
      code: 'cost_unseen_confirmation_required', field: 'commercial_price',
    }, 409)];
    const form = await editPrice(false, '3500.00');
    const ask = within(form).getByTestId('offer-margin-confirm');
    expect(ask).toHaveTextContent('costo pactado que no puedes ver');
    expect(ask).not.toHaveTextContent(/4[.,]?000/);
    await act(async () => { fireEvent.click(within(ask).getByRole('button', { name: 'Guardar de todas formas' })); });
    expect(writes[1].body).toMatchObject({ confirm_below_cost: true });
  });

  it('cambiar el precio otra vez retira la confirmación pendiente', async () => {
    writeAnswers = [() => reply({ detail: 'Por debajo del costo.', code: 'below_cost_confirmation_required' }, 409)];
    const form = await editPrice(true, '3500.00');
    expect(within(form).getByTestId('offer-margin-confirm')).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText('Precio de venta bajo pedido (S/)'), { target: { value: '4500.00' } });
    expect(within(form).queryByTestId('offer-margin-confirm')).toBeNull();
  });
});

describe('precio por debajo del costo: el Excel', () => {
  async function upload() {
    render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
    await screen.findByTestId('offer-row-31');
    fireEvent.change(screen.getByLabelText('Archivo de ofertas (.xlsx)'), { target: { files: [new File(['x'], 'ofertas.xlsx')] } });
    fireEvent.change(screen.getByLabelText('Fecha del mensaje del proveedor'), { target: { value: '2026-11-02' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
    return screen.findByTestId('supplier-import-preview');
  }
  const applied = () => writes.filter((call) => call.path.includes('/apply/'));

  it('con costos: lista las filas con precio y costo, y pide confirmarlas antes de aplicar', async () => {
    preview = previewOf([importRow({ below_cost: { commercial_price: '60.00', agreed_cost: '70.00' } },
      ['El precio comercial (S/ 60.00) queda por debajo del costo pactado (S/ 70.00).'])]);
    const shown = await upload();
    const group = within(shown).getByRole('group', { name: 'Ofertas que se venderían a pérdida' });
    expect(group).toHaveTextContent('Fila 2: Cargador');
    expect(group).toHaveTextContent('70.00');
    const applyButton = within(shown).getByRole('button', { name: 'Aplicar cambios' });
    expect(applyButton).toBeDisabled();
    fireEvent.click(within(group).getByRole('checkbox', { name: /Confirmo aplicar/ }));
    await act(async () => { fireEvent.click(applyButton); });
    expect(applied()[0].body).toEqual({ confirm_below_cost: true });
  });

  it('sin costos: filas sin importes de costo y la misma confirmación', async () => {
    preview = previewOf([importRow({ cost_unverified: true }, ['La oferta tiene un costo pactado que no puedes ver.'])]);
    const shown = await upload();
    const group = within(shown).getByRole('group', { name: 'Precios sobre un costo que no puedes ver' });
    expect(group).toHaveTextContent('Fila 2: Cargador');
    expect(group).not.toHaveTextContent('70');
    const applyButton = within(shown).getByRole('button', { name: 'Aplicar cambios' });
    expect(applyButton).toBeDisabled();
    fireEvent.click(within(group).getByRole('checkbox', { name: /Confirmo aplicar/ }));
    await act(async () => { fireEvent.click(applyButton); });
    expect(applied()[0].body).toEqual({ confirm_below_cost: true });
  });

  it('si el servidor lo descubre al aplicar (409), muestra las filas y no deja aplicar sin confirmar', async () => {
    preview = previewOf([importRow({})]);
    writeAnswers = [() => reply({
      detail: '1 fila(s) dejarían el precio comercial por debajo del costo pactado.', code: 'below_cost_confirmation_required',
      below_cost: [{ row_number: 2, offer_id: 32, product_name: 'Cargador', commercial_price: '60.00', agreed_cost: '70.00' }],
    }, 409)];
    const shown = await upload();
    await act(async () => { fireEvent.click(within(shown).getByRole('button', { name: 'Aplicar cambios' })); });
    const group = await within(shown).findByRole('group', { name: 'Ofertas que se venderían a pérdida' });
    expect(group).toHaveTextContent('Fila 2: Cargador');
    expect(within(shown).getByRole('button', { name: 'Aplicar cambios' })).toBeDisabled();
  });

  it('una fila de un proveedor inactivo muestra el aviso del servidor', async () => {
    preview = previewOf([importRow({ supplier_inactive: true },
      ['El proveedor «Distribuidora Andina» está inactivo: la oferta se registra, pero no se vende hasta reactivarlo.'])]);
    const shown = await upload();
    expect(within(shown).getByTestId('import-row-2')).toHaveTextContent('está inactivo');
  });

  it('el cliente de la API manda la confirmación sólo cuando se pide', async () => {
    send.mockImplementation(async () => reply({ status: 'applied', created: 0, updated: 0, unchanged: 0 }));
    await applyWeeklyImport(61, null, null, { confirmBelowCost: true });
    await applyWeeklyImport(61, null, null, {});
    const bodies = send.mock.calls.map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(bodies).toEqual([{ confirm_below_cost: true }, {}]);
  });
});
