/**
 * CATALOG-REFERENCE-01 · PR F — «Vender equipo por modelo» en la caja.
 *
 * El vendedor busca el modelo y elige capacidad → color → SIM/región (sólo lo que varía y sólo lo que existe), ve el
 * stock propio de su sucursal y lo agrega a la venta; si el equipo acaba de llegar, lo registra ahí mismo (IMEI 1,
 * IMEI 2, serie, proveedor, costo con permiso, precio) sin abrir pantallas del panel, y entra a la venta.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { PosDevicePicker } from '@/app/admin/sales/pos/PosDevicePicker';
import * as api from '@/app/admin/lib/internal-api';

jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  posModelSearch: jest.fn(),
  fetchPosModelConfigurations: jest.fn(),
  posReceiveDevice: jest.fn(),
}));
const search = api.posModelSearch as jest.MockedFunction<typeof api.posModelSearch>;
const configs = api.fetchPosModelConfigurations as jest.MockedFunction<typeof api.fetchPosModelConfigurations>;
const receive = api.posReceiveDevice as jest.MockedFunction<typeof api.posReceiveDevice>;

const CAP = 1, COL = 2, SIM = 3, REG = 4;
const G128 = 11, G256 = 12, BLACK = 21, BLUE = 22, ESIM = 31, CHIP = 32, JA = 42;
const GOOD_IMEI = '490154203237518';

function variant(id: number, available: number, over: Partial<api.PosConfigVariant> = {}): api.PosConfigVariant {
  return {
    product_id: id, name: `Teléfono Q ${id}`, price: '3000.00', available, is_active: true, pending_draft: false,
    sellable: true, is_serialized: true, requires_imei: true, on_order: null, ...over,
  };
}

function config(can: Partial<api.PosModelConfigurations['can']> = {}, extra: Partial<api.PosModelConfigurations> = {}) {
  return {
    model: { id: 9, name: 'Teléfono Q' }, line: 'new', has_palette: true,
    attributes: [
      { id: CAP, name: 'Capacidad', role: 'capacity', kind: 'text', varies: true,
        options: [{ id: G128, label: '128 GB', swatch_hex: '' }, { id: G256, label: '256 GB', swatch_hex: '' }] },
      { id: COL, name: 'Color', role: 'color', kind: 'color', varies: true,
        options: [{ id: BLACK, label: 'Negro', swatch_hex: '#111111' }, { id: BLUE, label: 'Azul', swatch_hex: '#1D4ED8' }] },
      { id: SIM, name: 'Tipo de SIM', role: 'sim', kind: 'text', varies: true,
        options: [{ id: ESIM, label: 'eSIM', swatch_hex: '' }, { id: CHIP, label: 'Chip', swatch_hex: '' }] },
      { id: REG, name: 'Región', role: 'region', kind: 'text', varies: false, options: [{ id: JA, label: 'J/A', swatch_hex: '' }] },
    ],
    combinations: [
      { key: 'a', option_ids: [G128, BLACK, ESIM, JA], variant: variant(100, 2, { on_order: { lead_time_days: 3, price: '3100.00' } }) },
      { key: 'b', option_ids: [G128, BLACK, CHIP, JA], variant: null },
      { key: 'c', option_ids: [G256, BLUE, ESIM, JA], variant: null },
    ],
    truncated: false,
    can: { receive: true, create_variant: true, set_price: true, view_costs: false, choose_supplier: true, open_suppliers: true,
      receive_on_order: true, ...can },
    suppliers: [{ id: 5, name: 'Mayorista Q' }],
    ...extra,
  } as api.PosModelConfigurations;
}

const received: api.PosDeviceReceived = {
  unit: { unit_id: 700, product_id: 300, product_name: 'Teléfono Q 256 GB Azul eSIM J/A', serial_number: '', imei: GOOD_IMEI,
          imei2: '', condition: 'new', condition_label: 'Nuevo', requires_imei: true, priced_by: 'product' },
  product: { id: 300, name: 'Teléfono Q 256 GB Azul eSIM J/A', price: '3900.00', available: 1, barcode: '', is_serialized: true,
             requires_imei: true, on_order: null, priced_by: 'product' },
  method: 'manual', replayed: false, created_variant: true, price_changed: false, warnings: [],
};

beforeEach(() => {
  search.mockReset();
  configs.mockReset();
  receive.mockReset();
  search.mockResolvedValue([{ id: 9, name: 'Teléfono Q', has_palette: true, variants: 1, available: 2 }]);
});

function renderPicker(props: Partial<React.ComponentProps<typeof PosDevicePicker>> = {}) {
  const handlers = { onAddProduct: jest.fn(), onAddOnOrder: jest.fn(), onReceived: jest.fn() };
  render(<PosDevicePicker companyId={3} branch={8} {...handlers} {...props} />);
  return handlers;
}

/**
 * Types `value` into the field labelled `label` key by key, `gapMs` apart on the clock the till measures (a scanner
 * writes in a burst; a person does not), then the field holds it — as `unit-entry.test.tsx` does.
 */
function typeTimed(label: RegExp, value: string, gapMs: number) {
  const field = screen.getByLabelText(label);
  let now = 1000;
  const spy = jest.spyOn(performance, 'now');
  for (const char of value) {
    now += gapMs;
    spy.mockReturnValue(now);
    fireEvent.keyDown(field, { key: char });
  }
  fireEvent.change(field, { target: { value } });
  spy.mockRestore();
}

async function openModel(c = config()) {
  configs.mockResolvedValue(c);
  await userEvent.click(screen.getByRole('button', { name: /Vender equipo por modelo/ }));
  await userEvent.type(screen.getByLabelText('Modelo'), 'Telef');
  await userEvent.click(await screen.findByRole('button', { name: /Teléfono Q/ }));
  await screen.findByRole('radiogroup', { name: 'Capacidad' });
}

it('is a disclosure that asks only what varies, in reading order', async () => {
  renderPicker();
  const toggle = screen.getByRole('button', { name: /Vender equipo por modelo/ });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await openModel();
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getAllByRole('radiogroup').map((g) => g.getAttribute('aria-label'))).toEqual(['Capacidad', 'Color', 'Tipo de SIM']);
  expect(configs).toHaveBeenCalledWith(3, 8, 9);
});

it('offers only combinations that exist and shows the own stock of this branch', async () => {
  renderPicker();
  await openModel();
  await userEvent.click(screen.getByRole('radio', { name: /256 GB/ }));
  expect(screen.getByRole('radio', { name: /Negro/ })).toBeDisabled();
  await userEvent.click(screen.getByRole('radio', { name: /128 GB/ }));
  expect(screen.getByRole('radio', { name: /Negro/ })).toBeEnabled();
  await userEvent.click(screen.getByRole('radio', { name: /Negro/ }));
  await userEvent.click(screen.getByRole('radio', { name: /eSIM/ }));
  const result = screen.getByRole('region', { name: 'Equipo elegido' });
  expect(within(result).getByText(/2 en esta sucursal/)).toBeInTheDocument();
});

it('adds an existing device line to the sale; the IMEI is then scanned as always', async () => {
  const handlers = renderPicker();
  await openModel();
  await userEvent.click(screen.getByRole('radio', { name: /128 GB/ }));
  await userEvent.click(screen.getByRole('radio', { name: /Negro/ }));
  await userEvent.click(screen.getByRole('radio', { name: /eSIM/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Agregar a la venta' }));
  expect(handlers.onAddProduct).toHaveBeenCalledWith(expect.objectContaining({ id: 100, price: '3000.00', available: 2, is_serialized: true }));
  await userEvent.click(screen.getByRole('button', { name: /bajo pedido/i }));
  expect(handlers.onAddOnOrder).toHaveBeenCalledWith(expect.objectContaining({ id: 100, on_order: { lead_time_days: 3, price: '3100.00' } }));
});

it('a seller who may not receive is told to ask an authorised user, and gets no form', async () => {
  renderPicker();
  await openModel(config({ receive: false, create_variant: false, set_price: false, choose_supplier: false }));
  await userEvent.click(screen.getByRole('radio', { name: /256 GB/ }));
  await userEvent.click(screen.getByRole('radio', { name: /Azul/ }));
  expect(screen.queryByRole('button', { name: 'Llegó el equipo' })).toBeNull();
  expect(screen.getByText(/pide a un usuario autorizado/)).toBeInTheDocument();
});

it('receives a device of a new combination and puts it into the sale', async () => {
  receive.mockResolvedValue(received);
  const handlers = renderPicker();
  await openModel();
  await userEvent.click(screen.getByRole('radio', { name: /256 GB/ }));
  await userEvent.click(screen.getByRole('radio', { name: /Azul/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Llegó el equipo' }));
  expect(screen.getByText(/no es una segunda SIM física/)).toBeInTheDocument();
  expect(screen.queryByLabelText(/Costo/)).toBeNull();

  await userEvent.type(screen.getByLabelText(/^IMEI 1/), '490154203237519');
  await userEvent.type(screen.getByLabelText(/Precio de venta/), '3900.00');
  await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
  expect(receive).not.toHaveBeenCalled();
  expect(screen.getByLabelText(/^IMEI 1/)).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByText(/no es válido/)).toBeInTheDocument();

  await userEvent.clear(screen.getByLabelText(/^IMEI 1/));
  typeTimed(/^IMEI 1/, GOOD_IMEI, 200);
  await userEvent.selectOptions(screen.getByLabelText(/Proveedor/), '5');
  await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
  await waitFor(() => expect(handlers.onReceived).toHaveBeenCalledWith(received.unit, received.product, 'manual'));
  const [company, branch, body, key] = receive.mock.calls[0];
  expect([company, branch]).toEqual([3, 8]);
  expect(body).toEqual({ model: 9, option_ids: [G256, BLUE, ESIM, JA], imei: GOOD_IMEI, price: '3900.00', supplier: 5,
    method: 'manual' });
  expect(key).toMatch(/\S{8,}/);
});

it('a duplicate IMEI says which device has it, and the retry keeps the same key', async () => {
  receive.mockRejectedValueOnce(new api.PosDeviceReceiveError('Ese IMEI ya está registrado en el equipo U123.', 'duplicate_imei', 'imei', 409,
    { id: 1, label_code: 'U123', product_name: 'Teléfono Q 128', status_label: 'Disponible', branch_name: 'Centro' }));
  receive.mockRejectedValueOnce(new Error('Sin conexión.'));
  renderPicker();
  await openModel();
  await userEvent.click(screen.getByRole('radio', { name: /256 GB/ }));
  await userEvent.click(screen.getByRole('radio', { name: /Azul/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Llegó el equipo' }));
  await userEvent.type(screen.getByLabelText(/^IMEI 1/), GOOD_IMEI);
  await userEvent.type(screen.getByLabelText(/Precio de venta/), '3900.00');
  await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
  expect(await screen.findByText(/U123/)).toBeInTheDocument();
  expect(screen.getByLabelText(/^IMEI 1/)).toHaveAttribute('aria-invalid', 'true');
  await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
  await screen.findByText('Sin conexión.');
  expect(receive.mock.calls[0][3]).toBe(receive.mock.calls[1][3]);
});

it('the cost field exists only for who sees costs', async () => {
  renderPicker();
  await openModel(config({ view_costs: true }));
  await userEvent.click(screen.getByRole('radio', { name: /256 GB/ }));
  await userEvent.click(screen.getByRole('radio', { name: /Azul/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Llegó el equipo' }));
  expect(screen.getByLabelText(/Costo/)).toBeInTheDocument();
});

async function openReceiveForm(c = config()) {
  await openModel(c);
  await userEvent.click(screen.getByRole('radio', { name: /256 GB/ }));
  await userEvent.click(screen.getByRole('radio', { name: /Azul/ }));
  await userEvent.click(screen.getByRole('button', { name: 'Llegó el equipo' }));
}

describe('D-F-10 — el proveedor lo elige quien recibe', () => {
  it('sin proveedores activos el campo dice cómo crearlo, con enlace sólo si puede abrir Proveedores', async () => {
    renderPicker();
    await openReceiveForm(config({}, { suppliers: [] }));
    expect(screen.getByText(/No hay proveedores activos/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Proveedores' })).toHaveAttribute('href', '/admin/products/suppliers');
  });

  it('quien recibe sin ver la pantalla de proveedores elige igual, sin enlace', async () => {
    renderPicker();
    await openReceiveForm(config({ open_suppliers: false }, { suppliers: [] }));
    expect(screen.getByText(/No hay proveedores activos/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Proveedores' })).toBeNull();
  });

  it('con proveedores, el selector está aunque no pueda abrir su pantalla', async () => {
    renderPicker();
    await openReceiveForm(config({ open_suppliers: false }));
    expect(screen.getByLabelText(/Proveedor/)).toBeInTheDocument();
  });
});

it('changing the till branch reloads the model for that branch and drops the choice', async () => {
  const handlers = { onAddProduct: jest.fn(), onAddOnOrder: jest.fn(), onReceived: jest.fn() };
  const view = render(<PosDevicePicker companyId={3} branch={8} {...handlers} />);
  await openModel();
  await userEvent.click(screen.getByRole('radio', { name: /256 GB/ }));
  expect(screen.getByRole('radio', { name: /256 GB/ })).toBeChecked();
  view.rerender(<PosDevicePicker companyId={3} branch={12} {...handlers} />);
  await waitFor(() => expect(configs).toHaveBeenLastCalledWith(3, 12, 9));
  await waitFor(() => expect(screen.getByRole('radio', { name: /256 GB/ })).not.toBeChecked());
});

it('an idempotency conflict changes the key and says the device may already be registered', async () => {
  receive.mockRejectedValueOnce(new api.PosDeviceReceiveError('Esa clave ya registró otro equipo.', 'idempotency_conflict', '', 409, null));
  receive.mockRejectedValueOnce(new Error('Sin conexión.'));
  renderPicker();
  await openReceiveForm();
  await userEvent.type(screen.getByLabelText(/^IMEI 1/), GOOD_IMEI);
  await userEvent.type(screen.getByLabelText(/Precio de venta/), '3900.00');
  await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
  expect(await screen.findByText(/pudo quedar registrado/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
  await screen.findByText('Sin conexión.');
  expect(receive.mock.calls[1][3]).not.toBe(receive.mock.calls[0][3]);
});

it('an optional role of a model without palette offers «Sin <atributo>» and both variants are reachable', async () => {
  const LLA = 41;
  const loose = config({}, {
    has_palette: false,
    attributes: [
      { id: CAP, name: 'Capacidad', role: 'capacity', kind: 'text', varies: true,
        options: [{ id: G128, label: '128 GB', swatch_hex: '' }, { id: G256, label: '256 GB', swatch_hex: '' }] },
      { id: COL, name: 'Color', role: 'color', kind: 'color', varies: false, options: [{ id: BLACK, label: 'Negro', swatch_hex: '#111111' }] },
      { id: REG, name: 'Región', role: 'region', kind: 'text', varies: true, options: [{ id: LLA, label: 'LL/A', swatch_hex: '' }] },
    ],
    combinations: [
      { key: 'a', option_ids: [G128, BLACK], variant: variant(300, 1) },
      { key: 'c', option_ids: [G128, BLACK, LLA], variant: variant(301, 1) },
      { key: 'b', option_ids: [G256, BLACK], variant: variant(302, 1) },
    ],
  });
  renderPicker();
  await openModel(loose);
  await userEvent.click(screen.getByRole('radio', { name: /128 GB/ }));
  // Until «Región» is answered there is no device to add: A must not stand in for C.
  expect(screen.queryByRole('region', { name: 'Equipo elegido' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Agregar a la venta' })).toBeNull();
  const result = () => screen.getByRole('region', { name: 'Equipo elegido' });
  await userEvent.click(screen.getByRole('radio', { name: /Sin Región/ }));
  expect(within(result()).getByText('Teléfono Q 300')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('radio', { name: /LL\/A/ }));
  expect(within(result()).getByText('Teléfono Q 301')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('radio', { name: /Sin Región/ }));
  expect(within(result()).getByText('Teléfono Q 300')).toBeInTheDocument();
});

it('a receipt that answers after the till changed branch is not put into the new branch\'s sale', async () => {
  let answer: (value: api.PosDeviceReceived) => void = () => undefined;
  receive.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
  const handlers = { onAddProduct: jest.fn(), onAddOnOrder: jest.fn(), onReceived: jest.fn() };
  const view = render(<PosDevicePicker companyId={3} branch={8} {...handlers} />);
  await openReceiveForm();
  await userEvent.type(screen.getByLabelText(/^IMEI 1/), GOOD_IMEI);
  await userEvent.type(screen.getByLabelText(/Precio de venta/), '3900.00');
  await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
  view.rerender(<PosDevicePicker companyId={3} branch={12} {...handlers} />);
  await act(async () => { answer(received); });
  expect(handlers.onReceived).not.toHaveBeenCalled();
  expect(await screen.findByText(/no se agregó a esta venta/)).toBeInTheDocument();
});

it('a colour chip shows its finish picture, else the company swatch, else only its name (G, owner order 4)', async () => {
  const c = config();
  c.attributes[1] = { ...c.attributes[1], options: [
    { id: BLACK, label: 'Negro', swatch_hex: '', thumbnail: '/api/storefront/images/' + 'b'.repeat(32) },
    { id: BLUE, label: 'Azul', swatch_hex: '' , thumbnail: null },
  ] };
  renderPicker();
  await openModel(c);
  const black = screen.getByRole('radio', { name: /Negro/ }).closest('label')!;
  const blue = screen.getByRole('radio', { name: /Azul/ }).closest('label')!;
  const picture = within(black).getByTestId('pos-chip-picture').querySelector('img')!;
  expect(picture.getAttribute('src')).toContain('bbbb');
  // Review of #377: through the optimizer at chip size, never the original file.
  expect(picture).toHaveAttribute('sizes', '20px');
  expect(picture.getAttribute('src')).toContain('/_next/image?url=');
  // No picture and no swatch: the name alone, never a grey dot pretending to be the colour.
  expect(within(blue).queryByTestId('pos-chip-picture')).toBeNull();
  expect(blue.querySelector('[style*="background"]')).toBeNull();
  expect(blue).toHaveTextContent('Azul');
});

describe('NAV-SALES-01 — «Venta rápida de equipo» abre este bloque', () => {
  const scrolled = jest.fn();
  beforeAll(() => { Element.prototype.scrollIntoView = scrolled; });
  afterAll(() => { delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView; });
  beforeEach(() => scrolled.mockReset());

  it('pedido desde el menú: se abre, se muestra y el cursor queda en la búsqueda del modelo', () => {
    const onFocusHandled = jest.fn();
    renderPicker({ focusRequested: true, onFocusHandled });
    expect(screen.getByRole('button', { name: /Vender equipo por modelo/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Modelo')).toHaveFocus();
    expect(scrolled).toHaveBeenCalled();
    expect(onFocusHandled).toHaveBeenCalledTimes(1);
  });

  it('sin sucursal todavía, se abre y espera a que la caja tenga una para poner el cursor', () => {
    const onFocusHandled = jest.fn();
    const handlers = { onAddProduct: jest.fn(), onAddOnOrder: jest.fn(), onReceived: jest.fn() };
    const view = render(<PosDevicePicker companyId={3} branch={null} focusRequested onFocusHandled={onFocusHandled} {...handlers} />);
    expect(screen.getByRole('button', { name: /Vender equipo por modelo/ })).toHaveAttribute('aria-expanded', 'true');
    expect(onFocusHandled).not.toHaveBeenCalled();
    view.rerender(<PosDevicePicker companyId={3} branch={8} focusRequested onFocusHandled={onFocusHandled} {...handlers} />);
    expect(screen.getByLabelText('Modelo')).toHaveFocus();
    expect(onFocusHandled).toHaveBeenCalledTimes(1);
  });

  it('sin pedido, sigue cerrado y no toca el foco (el escáner es de la caja)', () => {
    renderPicker();
    expect(screen.getByRole('button', { name: /Vender equipo por modelo/ })).toHaveAttribute('aria-expanded', 'false');
    expect(scrolled).not.toHaveBeenCalled();
  });

  it('con un modelo ya elegido, el cursor va al botón del bloque', async () => {
    const onFocusHandled = jest.fn();
    const handlers = { onAddProduct: jest.fn(), onAddOnOrder: jest.fn(), onReceived: jest.fn() };
    const view = render(<PosDevicePicker companyId={3} branch={8} {...handlers} />);
    await openModel();
    await userEvent.click(document.body);
    view.rerender(<PosDevicePicker companyId={3} branch={8} focusRequested onFocusHandled={onFocusHandled} {...handlers} />);
    expect(screen.getByRole('button', { name: /Vender equipo por modelo/ })).toHaveFocus();
    expect(onFocusHandled).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// POS-ORDER-ARRIVAL-01 — a device a paid order is waiting for is that order's
// ---------------------------------------------------------------------------

describe('POS-ORDER-ARRIVAL-01 — el equipo que espera un pedido pagado', () => {
  const LINE: api.PosPendingOrderLine = {
    order_item: 55, order_id: 501, order_number: 501, quantity: 1, customer: 'Ana Q.', created_at: '2026-11-20T10:00:00-05:00',
    branch: { id: 8, name: 'Centro' }, here: true,
  };

  function waiting(lines: api.PosPendingOrderLine[] = [LINE], can: Partial<api.PosModelConfigurations['can']> = {}) {
    const c = config(can);
    c.combinations[0] = { ...c.combinations[0], pending_order_lines: lines };
    return c;
  }

  async function arrive(c: api.PosModelConfigurations) {
    await openModel(c);
    await userEvent.click(screen.getByRole('radio', { name: /128 GB/ }));
    await userEvent.click(screen.getByRole('radio', { name: /Negro/ }));
    await userEvent.click(screen.getByRole('radio', { name: /eSIM/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Llegó el equipo' }));
  }

  const toOrder: api.PosDeviceReceived = {
    ...received, mode: 'order', order: { id: 501, item_id: 55 }, branch: { id: 8, name: 'Centro' }, created_variant: false,
  };

  it('offers «Es del pedido #N» first and sends the order line; the device does not go into this sale', async () => {
    receive.mockResolvedValue(toOrder);
    const handlers = renderPicker();
    await arrive(waiting());
    // The choice comes before the form: nothing is typed until the seller says whose device it is.
    expect(screen.queryByLabelText(/^IMEI 1/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Ingresar como stock' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Es del pedido #501 \(Ana Q\.\)/ }));
    expect(screen.queryByLabelText(/Proveedor/)).toBeNull();
    expect(screen.queryByLabelText(/Precio de venta/)).toBeNull();
    typeTimed(/^IMEI 1/, GOOD_IMEI, 200);
    await userEvent.click(screen.getByRole('button', { name: 'Registrar y entregar al pedido #501' }));
    await waitFor(() => expect(receive).toHaveBeenCalled());
    expect(receive.mock.calls[0][2]).toEqual({ order_item: 55, imei: GOOD_IMEI, method: 'manual' });
    expect(await screen.findByText(/entregado al pedido #501/)).toBeInTheDocument();
    expect(handlers.onReceived).not.toHaveBeenCalled();
  });

  it('«Ingresar como stock» over a waiting order asks why, and sends the confirmation with the reason', async () => {
    receive.mockResolvedValue({ ...received, created_variant: false });
    const handlers = renderPicker();
    await arrive(waiting());
    await userEvent.click(screen.getByRole('button', { name: 'Ingresar como stock' }));
    typeTimed(/^IMEI 1/, GOOD_IMEI, 200);
    await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
    expect(receive).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Motivo/)).toHaveAttribute('aria-invalid', 'true');
    await userEvent.type(screen.getByLabelText(/Motivo/), 'Es otro equipo para vitrina');
    await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
    await waitFor(() => expect(handlers.onReceived).toHaveBeenCalled());
    expect(receive.mock.calls[0][2]).toEqual(expect.objectContaining({
      model: 9, option_ids: [G128, BLACK, ESIM, JA], imei: GOOD_IMEI, confirm_stock: true, reason: 'Es otro equipo para vitrina',
    }));
    expect(receive.mock.calls[0][2]).not.toHaveProperty('order_item');
  });

  it('a line dispatched from another branch is named with it, and stock there needs no reason', async () => {
    receive.mockResolvedValue({ ...received, created_variant: false });
    renderPicker();
    await arrive(waiting([{ ...LINE, here: false, branch: { id: 12, name: 'Norte' } }]));
    expect(screen.getByText(/Norte/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Ingresar como stock' }));
    expect(screen.queryByLabelText(/Motivo/)).toBeNull();
  });

  it('without «Administrar pedidos» the order is shown but delivering it is left to an authorised user', async () => {
    renderPicker();
    await arrive(waiting([LINE], { receive_on_order: false }));
    expect(screen.queryByRole('button', { name: /Es del pedido/ })).toBeNull();
    expect(screen.getByText(/#501/)).toBeInTheDocument();
    expect(screen.getByText(/Administrar pedidos/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ingresar como stock' })).toBeInTheDocument();
  });

  it('a line of several devices is received from its order', async () => {
    renderPicker();
    await arrive(waiting([{ ...LINE, quantity: 2 }]));
    expect(screen.queryByRole('button', { name: /Es del pedido/ })).toBeNull();
    expect(screen.getByText(/recíbela desde el pedido/)).toBeInTheDocument();
  });

  it('a 409 «pending_order_line» from the server brings the choice back with its lines', async () => {
    receive.mockRejectedValueOnce(new api.PosDeviceReceiveError('Hay un pedido pagado que espera este equipo.', 'pending_order_line', '', 409,
      null, [LINE]));
    renderPicker();
    await arrive(config());
    typeTimed(/^IMEI 1/, GOOD_IMEI, 200);
    await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
    expect(await screen.findByRole('button', { name: /Es del pedido #501/ })).toBeInTheDocument();
    expect(screen.getByText(/Hay un pedido pagado que espera este equipo/)).toBeInTheDocument();
  });

  it.each(['already_received', 'wrong_state', 'not_paid', 'return_open'])(
    'an order line the server says is no longer waiting (%s) leaves the choice, and stock then needs no reason',
    async (code) => {
      receive.mockRejectedValueOnce(new api.PosDeviceReceiveError('Esa línea ya no espera un equipo.', code, '', 409, null));
      receive.mockResolvedValueOnce({ ...received, created_variant: false });
      const handlers = renderPicker();
      await arrive(waiting());
      await userEvent.click(screen.getByRole('button', { name: /Es del pedido #501/ }));
      typeTimed(/^IMEI 1/, GOOD_IMEI, 200);
      await userEvent.click(screen.getByRole('button', { name: 'Registrar y entregar al pedido #501' }));
      expect(await screen.findByText('Esa línea ya no espera un equipo.')).toBeInTheDocument();
      // Nothing waits any more: the form is plain stock, without the fulfilled line and without a reason.
      expect(screen.queryByRole('button', { name: /Es del pedido/ })).toBeNull();
      expect(screen.queryByLabelText(/Motivo/)).toBeNull();
      await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
      await waitFor(() => expect(handlers.onReceived).toHaveBeenCalled());
      const body = receive.mock.calls[1][2];
      expect(body).toEqual(expect.objectContaining({ model: 9, option_ids: [G128, BLACK, ESIM, JA], imei: GOOD_IMEI }));
      expect(body).not.toHaveProperty('order_item');
      expect(body).not.toHaveProperty('confirm_stock');
    },
  );

  it('with another order still waiting, a fulfilled one is dropped and the choice stays', async () => {
    receive.mockRejectedValueOnce(new api.PosDeviceReceiveError('Esta línea ya se recibió del proveedor.', 'already_received', '', 409, null));
    renderPicker();
    await arrive(waiting([LINE, { ...LINE, order_item: 56, order_id: 502, order_number: 502, customer: 'Luis P.' }]));
    await userEvent.click(screen.getByRole('button', { name: /Es del pedido #501/ }));
    typeTimed(/^IMEI 1/, GOOD_IMEI, 200);
    await userEvent.click(screen.getByRole('button', { name: 'Registrar y entregar al pedido #501' }));
    expect(await screen.findByRole('button', { name: /Es del pedido #502/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Es del pedido #501/ })).toBeNull();
  });

  it('a price typed for stock does not stop a delivery to the order, where there is no price', async () => {
    receive.mockResolvedValue(toOrder);
    renderPicker();
    await arrive(waiting());
    await userEvent.click(screen.getByRole('button', { name: 'Ingresar como stock' }));
    await userEvent.clear(screen.getByLabelText(/Precio de venta/));
    await userEvent.type(screen.getByLabelText(/Precio de venta/), 'abc');
    await userEvent.click(screen.getByRole('button', { name: 'Cambiar' }));
    await userEvent.click(screen.getByRole('button', { name: /Es del pedido #501/ }));
    typeTimed(/^IMEI 1/, GOOD_IMEI, 200);
    await userEvent.click(screen.getByRole('button', { name: 'Registrar y entregar al pedido #501' }));
    await waitFor(() => expect(receive).toHaveBeenCalled());
    expect(receive.mock.calls[0][2]).toEqual({ order_item: 55, imei: GOOD_IMEI, method: 'manual' });
  });

  it('a device read by the scanner is registered and sold as read by the scanner', async () => {
    receive.mockResolvedValue({ ...received, method: 'scanner' });
    const handlers = renderPicker();
    await openReceiveForm();
    typeTimed(/^IMEI 1/, GOOD_IMEI, 8);
    await userEvent.type(screen.getByLabelText(/Precio de venta/), '3900.00');
    await userEvent.click(screen.getByRole('button', { name: 'Registrar y agregar a la venta' }));
    await waitFor(() => expect(handlers.onReceived).toHaveBeenCalledWith(received.unit, received.product, 'scanner'));
    expect(receive.mock.calls[0][2]).toEqual(expect.objectContaining({ method: 'scanner' }));
  });
});
