/**
 * SELECTOR-UX-01 · C — «Registrar equipo por escaneo» elige modelo y configuración con EL configurador (modo único).
 *
 * Nuevo: los chips de la caja (radios por paso, muestra de color, lo que no existe apagado, los equipos propios de la
 * sucursal) sobre la paleta menos sus exclusiones. Seminuevo: el mismo configurador sobre las variantes de seminuevos
 * que existen (por sus opciones); con una variante que no se puede expresar por opciones, la lista de siempre. El
 * código comercial de la caja sigue eligiendo la variante y el lector sigue recibiendo las lecturas.
 *
 * Y dos puntos de la revisión final de #437: la guarda del historial nunca navega después de desmontar la pantalla, y
 * un segundo envío rápido espera a que termine el «atrás» del anterior antes de empujar su guarda.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ScanIntake } from '@/app/admin/components/ScanIntake';
import * as lib from '@/app/admin/lib/scan-intake';
import type { ScanConfigurations } from '@/app/admin/lib/scan-intake';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/lib/use-cosmetic-grades', () => ({ useCosmeticGrades: () => [] }));
jest.mock('@/app/admin/lib/scan-intake', () => ({
  ...jest.requireActual('@/app/admin/lib/scan-intake'),
  scanCheck: jest.fn(),
  scanConfigurations: jest.fn(),
  scanModelSearch: jest.fn(),
  scanReceive: jest.fn(),
}));
const check = lib.scanCheck as jest.MockedFunction<typeof lib.scanCheck>;
const configs = lib.scanConfigurations as jest.MockedFunction<typeof lib.scanConfigurations>;
const search = lib.scanModelSearch as jest.MockedFunction<typeof lib.scanModelSearch>;
const receive = lib.scanReceive as jest.MockedFunction<typeof lib.scanReceive>;

const IMEI_1 = '350000000000014';
const CAP = 1, COL = 2, SIM = 3, G128 = 11, G256 = 12, BLACK = 21, BLUE = 22, ESIM = 31, CHIP = 32;

const variant = (id: number, available = 1) => ({
  product_id: id, name: `Teléfono Q ${id}`, price: '3000.00', available, is_active: true, pending_draft: false, sellable: true,
  is_serialized: true, requires_imei: true, on_order: null,
});

function config(over: Partial<ScanConfigurations> = {}): ScanConfigurations {
  return {
    model: { id: 9, name: 'Teléfono Q' }, line: 'new', has_palette: true,
    attributes: [
      { id: CAP, name: 'Capacidad', role: 'capacity', kind: 'text', varies: true,
        options: [{ id: G128, label: '128 GB', swatch_hex: '' }, { id: G256, label: '256 GB', swatch_hex: '' }] },
      { id: COL, name: 'Color', role: 'color', kind: 'color', varies: true,
        options: [{ id: BLACK, label: 'Negro', swatch_hex: '#111111' }, { id: BLUE, label: 'Azul', swatch_hex: '#1d4ed8' }] },
      { id: SIM, name: 'Tipo de SIM', role: 'sim', kind: 'text', varies: true,
        options: [{ id: ESIM, label: 'eSIM', swatch_hex: '' }, { id: CHIP, label: 'Chip', swatch_hex: '' }] },
    ],
    combinations: [
      { key: 'a', option_ids: [G128, BLACK, ESIM], variant: variant(100, 2), pending_order_lines: [] },
      { key: 'b', option_ids: [G128, BLACK, CHIP], variant: null, pending_order_lines: [] },
      { key: 'c', option_ids: [G256, BLUE, ESIM], variant: variant(101, 0), pending_order_lines: [] },
    ],
    truncated: false,
    can: { receive: true, create_variant: true, set_price: true, view_costs: false, choose_supplier: true, open_suppliers: false },
    suppliers: [],
    used_variants: [
      { id: 200, name: 'Teléfono Q 128 GB Negro eSIM seminuevo', is_serialized: true, requires_imei: true, pending_draft: false,
        option_ids: [G128, BLACK, ESIM] },
      { id: 201, name: 'Teléfono Q 256 GB Azul eSIM seminuevo', is_serialized: true, requires_imei: true, pending_draft: false,
        option_ids: [G256, BLUE, ESIM] },
    ],
    branch: { id: 1, name: 'Central' },
    ...over,
  } as unknown as ScanConfigurations;
}

beforeEach(() => {
  jest.clearAllMocks();
  check.mockImplementation(async (_company, code) => ({
    reading: { value: code, kind: 'imei', certain: true, alternatives: [], hint: '', problem: '', detail: '' },
    product: null, unit: null, conflict: null, serial_elsewhere: '',
  }) as never);
  configs.mockResolvedValue(config());
  search.mockResolvedValue([{ id: 9, name: 'Teléfono Q', has_palette: true, variants: 2, available: 2 }]);
});

function renderScreen() {
  return render(<ScanIntake companyId={7} branches={[{ id: 1, name: 'Central' }]} defaultBranch={1} canPrintLabels />);
}

async function chooseModel() {
  fireEvent.change(screen.getByTestId('scan-intake-model'), { target: { value: 'Teléfono' } });
  fireEvent.click(await screen.findByRole('button', { name: 'Teléfono Q' }));
  return screen.findByRole('group', { name: 'Configuración del equipo' });
}

const radio = (group: HTMLElement, step: string, name: RegExp) =>
  within(within(group).getByRole('radiogroup', { name: step })).getByRole('radio', { name });

async function scan(code: string) {
  const input = screen.getByTestId('scan-intake-input') as HTMLInputElement;
  fireEvent.change(input, { target: { value: code } });
  await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }); });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((r, j) => { resolve = r; reject = j; });
  return { promise, resolve, reject };
}

const received = {
  replayed: false, mode: 'stock', condition: 'new',
  unit: { id: 55, product_id: 100, product_name: 'Teléfono Q 100', branch_name: 'Central', label_code: 'U1', imei: IMEI_1, imei2: null,
    serial_number: '', status_label: 'Disponible' },
  branch: { id: 1, name: 'Central' }, created_variant: false, order: null, method: 'manual', warnings: [],
} as unknown as lib.ScanReceived;

describe('nuevo: el configurador de la caja', () => {
  it('pasos como radios con muestras, lo que no existe apagado, los equipos propios de la sucursal', async () => {
    renderScreen();
    const group = await chooseModel();
    expect(within(group).getAllByRole('radiogroup').map((g) => g.getAttribute('aria-label'))).toEqual(['Capacidad', 'Color', 'Tipo de SIM']);
    fireEvent.click(radio(group, 'Capacidad', /256 GB/));
    expect(radio(group, 'Color', /Negro/)).toBeDisabled();
    // 256 GB has only Azul · eSIM: chosen by itself, as at the till.
    expect(radio(group, 'Color', /Azul/)).toBeChecked();
    expect(radio(group, 'Tipo de SIM', /eSIM/)).toBeChecked();
    expect(screen.getByTestId('scan-intake-variant')).toHaveTextContent('Teléfono Q 101');
    fireEvent.click(radio(group, 'Capacidad', /128 GB/));
    expect(within(group).getByRole('radiogroup', { name: 'Capacidad' })).toHaveTextContent('2 disponibles aquí');
  });

  it('elegir con el configurador y escanear: se envían las opciones de esa combinación', async () => {
    receive.mockResolvedValue(received);
    renderScreen();
    const group = await chooseModel();
    fireEvent.click(radio(group, 'Capacidad', /128 GB/));
    fireEvent.click(radio(group, 'Color', /Negro/));
    fireEvent.click(radio(group, 'Tipo de SIM', /eSIM/));
    await scan(IMEI_1);
    await waitFor(() => expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent(IMEI_1));
    const confirm = screen.getByTestId('scan-intake-confirm');
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
    await screen.findByTestId('scan-intake-done');
    expect(receive.mock.calls[0][2]).toEqual(expect.objectContaining({ condition: 'new', model: 9, option_ids: [G128, BLACK, ESIM] }));
  });
});

describe('seminuevo: el mismo configurador sobre sus variantes', () => {
  it('sólo las configuraciones que existen en seminuevos; elegir una es elegir esa variante', async () => {
    receive.mockResolvedValue({ ...received, condition: 'used' } as lib.ScanReceived);
    renderScreen();
    fireEvent.click(screen.getByRole('button', { name: 'Seminuevo' }));
    const group = await chooseModel();
    fireEvent.click(radio(group, 'Capacidad', /256 GB/));
    expect(radio(group, 'Color', /Negro/)).toBeDisabled();
    expect(screen.getByTestId('scan-intake-used-variant')).toHaveTextContent('Teléfono Q 256 GB Azul eSIM seminuevo');
    await scan(IMEI_1);
    await waitFor(() => expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent(IMEI_1));
    fireEvent.change(screen.getByTestId('scan-intake-battery'), { target: { value: '88' } });
    const confirm = screen.getByTestId('scan-intake-confirm');
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
    expect(receive.mock.calls[0][2]).toEqual(expect.objectContaining({ condition: 'used', product_id: 201 }));
  });

  it('con una variante sin opciones (no se puede expresar), la lista de siempre', async () => {
    configs.mockResolvedValue(config({
      used_variants: [{ id: 300, name: 'Teléfono Q seminuevo', is_serialized: true, requires_imei: true, pending_draft: false, option_ids: [] }],
    } as Partial<ScanConfigurations>));
    renderScreen();
    fireEvent.click(screen.getByRole('button', { name: 'Seminuevo' }));
    fireEvent.change(screen.getByTestId('scan-intake-model'), { target: { value: 'Teléfono' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Teléfono Q' }));
    expect(await screen.findByLabelText(/Teléfono Q seminuevo/)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Configuración del equipo' })).toBeNull();
  });
});

describe('revisión final de #437: la guarda del historial', () => {
  it('nunca llama a «atrás» después de desmontar la pantalla, aunque el registro termine luego', async () => {
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    const pending = deferred<lib.ScanReceived>();
    receive.mockImplementationOnce(() => pending.promise);
    const back = jest.spyOn(window.history, 'back').mockImplementation(() => undefined);
    const view = renderScreen();
    const group = await chooseModel();
    fireEvent.click(radio(group, 'Capacidad', /128 GB/));
    fireEvent.click(radio(group, 'Color', /Negro/));
    fireEvent.click(radio(group, 'Tipo de SIM', /eSIM/));
    await scan(IMEI_1);
    const confirm = screen.getByTestId('scan-intake-confirm');
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
    view.unmount();
    await act(async () => { pending.resolve(received); await pending.promise; });
    expect(back).not.toHaveBeenCalled();
    back.mockRestore();
  });

  it('un segundo envío rápido espera el «atrás» del anterior antes de empujar su guarda', async () => {
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    window.history.replaceState(null, '', '/admin/inventory/scan');
    const order: string[] = [];
    const push = jest.spyOn(window.history, 'pushState');
    push.mockImplementation((state) => { order.push(`push:${(state as { scanIntakeGuard?: number })?.scanIntakeGuard ?? '-'}`); });
    const back = jest.spyOn(window.history, 'back').mockImplementation(() => {
      order.push('back');
      // The traversal lands later, as a browser does.
      setTimeout(() => { order.push('popstate'); window.dispatchEvent(new PopStateEvent('popstate', { state: null })); }, 40);
    });
    const state = jest.spyOn(window.history, 'state', 'get');
    // The guard just pushed is on top until the traversal lands.
    let top: unknown = null;
    push.mockImplementation((value) => { top = value; order.push(`push:${(value as { scanIntakeGuard?: number }).scanIntakeGuard}`); });
    state.mockImplementation(() => top);
    receive
      .mockRejectedValueOnce(new lib.ScanIntakeError('Revisa el IMEI', 'invalid', 'imei', 400))
      .mockResolvedValueOnce(received);
    renderScreen();
    const group = await chooseModel();
    fireEvent.click(radio(group, 'Capacidad', /128 GB/));
    fireEvent.click(radio(group, 'Color', /Negro/));
    fireEvent.click(radio(group, 'Tipo de SIM', /eSIM/));
    await scan(IMEI_1);
    const confirm = screen.getByTestId('scan-intake-confirm');
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
    await screen.findByTestId('scan-intake-done', {}, { timeout: 3000 });
    const second = order.findIndex((entry, index) => entry.startsWith('push:') && index > order.indexOf('back'));
    expect(order.indexOf('popstate')).toBeGreaterThan(-1);
    expect(second).toBeGreaterThan(order.indexOf('popstate'));
    push.mockRestore();
    back.mockRestore();
    state.mockRestore();
  });
});

describe('revisión de #440', () => {
  it('C1 · mientras se registra, el configurador y «Limpiar este equipo» no se pueden usar', async () => {
    const pending = deferred<lib.ScanReceived>();
    receive.mockImplementationOnce(() => pending.promise);
    renderScreen();
    const group = await chooseModel();
    fireEvent.click(radio(group, 'Capacidad', /128 GB/));
    fireEvent.click(radio(group, 'Color', /Negro/));
    fireEvent.click(radio(group, 'Tipo de SIM', /eSIM/));
    await scan(IMEI_1);
    const confirm = screen.getByTestId('scan-intake-confirm');
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
    for (const option of within(group).getAllByRole('radio')) expect(option).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Limpiar este equipo' })).toBeDisabled();
    await act(async () => { pending.resolve(received); await pending.promise; });
  });

  it('C1 · si cambia el equipo (otra sucursal) mientras espera la guarda anterior, no se envía nada y se dice', async () => {
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    let top: unknown = null;
    const push = jest.spyOn(window.history, 'pushState').mockImplementation((value) => { top = value; });
    const state = jest.spyOn(window.history, 'state', 'get').mockImplementation(() => top);
    const back = jest.spyOn(window.history, 'back').mockImplementation(() => {
      setTimeout(() => { top = null; window.dispatchEvent(new PopStateEvent('popstate', { state: null })); }, 150);
    });
    receive.mockRejectedValueOnce(new lib.ScanIntakeError('Revisa el IMEI', 'invalid', 'imei', 400));
    render(<ScanIntake companyId={7} branches={[{ id: 1, name: 'Central' }, { id: 2, name: 'Norte' }]} defaultBranch={1} canPrintLabels />);
    const group = await chooseModel();
    fireEvent.click(radio(group, 'Capacidad', /128 GB/));
    fireEvent.click(radio(group, 'Color', /Negro/));
    fireEvent.click(radio(group, 'Tipo de SIM', /eSIM/));
    await scan(IMEI_1);
    const confirm = screen.getByTestId('scan-intake-confirm');
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
    await waitFor(() => expect(confirm).toBeEnabled());
    // Second submit: it waits for the first guard's back(); meanwhile the clerk moves to another branch.
    await act(async () => { fireEvent.click(confirm); });
    fireEvent.change(screen.getByTestId('scan-intake-branch'), { target: { value: '2' } });
    await act(async () => { await new Promise((r) => setTimeout(r, 250)); });
    expect(receive).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('scan-intake-log')).toHaveTextContent('No se envió');
    push.mockRestore(); state.mockRestore(); back.mockRestore();
  });

  it('C2 · seminuevo: con una variante que no lleva un paso que otra sí (A con eSIM, B sin SIM) se usa la lista, nunca un chip que elija la otra', async () => {
    configs.mockResolvedValue(config({
      used_variants: [
        { id: 400, name: 'Teléfono Q 128 GB Azul eSIM seminuevo', is_serialized: true, requires_imei: true, pending_draft: false, option_ids: [G128, BLUE, ESIM] },
        { id: 401, name: 'Teléfono Q 128 GB Azul seminuevo', is_serialized: true, requires_imei: true, pending_draft: false, option_ids: [G128, BLUE] },
      ],
    } as Partial<ScanConfigurations>));
    expect(lib.usedSpace(await configs.getMockImplementation()!(7, 1, 9) as ScanConfigurations)).toBeNull();
    renderScreen();
    fireEvent.click(screen.getByRole('button', { name: 'Seminuevo' }));
    fireEvent.change(screen.getByTestId('scan-intake-model'), { target: { value: 'Teléfono' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Teléfono Q' }));
    expect(await screen.findByLabelText('Teléfono Q 128 GB Azul seminuevo')).toBeInTheDocument();
    expect(screen.getByLabelText(/Teléfono Q 128 GB Azul eSIM seminuevo/)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Configuración del equipo' })).toBeNull();
  });
});

describe('revisión 2 de #440', () => {
  it('una lectura que llega mientras espera la guarda anterior: no se envía nada y se dice', async () => {
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    let top: unknown = null;
    const push = jest.spyOn(window.history, 'pushState').mockImplementation((value) => { top = value; });
    const state = jest.spyOn(window.history, 'state', 'get').mockImplementation(() => top);
    const back = jest.spyOn(window.history, 'back').mockImplementation(() => {
      setTimeout(() => { top = null; window.dispatchEvent(new PopStateEvent('popstate', { state: null })); }, 150);
    });
    receive.mockRejectedValueOnce(new lib.ScanIntakeError('Revisa el IMEI', 'invalid', 'imei', 400));
    renderScreen();
    const group = await chooseModel();
    fireEvent.click(radio(group, 'Capacidad', /128 GB/));
    fireEvent.click(radio(group, 'Color', /Negro/));
    fireEvent.click(radio(group, 'Tipo de SIM', /eSIM/));
    await scan(IMEI_1);
    const confirm = screen.getByTestId('scan-intake-confirm');
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
    // While it waits: the scanner field does not take a read, «Leer» is off — and a read that lands anyway is caught.
    const input = screen.getByTestId('scan-intake-input') as HTMLInputElement;
    expect(input).toHaveAttribute('readonly');
    expect(screen.getByRole('button', { name: 'Leer' })).toBeDisabled();
    await scan('350000000000022');
    await act(async () => { await new Promise((r) => setTimeout(r, 250)); });
    expect(receive).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('scan-intake-log')).toHaveTextContent('No se envió');
    push.mockRestore(); state.mockRestore(); back.mockRestore();
  });
});
