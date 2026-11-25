/**
 * SCAN-TO-STOCK-01 — la pantalla «Registrar equipo por escaneo» con un lector (teclado que escribe y pulsa Enter).
 *
 * Lecturas consecutivas llenan IMEI 1 → IMEI 2 → serie y el foco vuelve solo al campo del lector; un Enter doble
 * no hace nada; una lectura repetida se ignora y se dice; un código dudoso se pregunta; un código comercial elige la
 * variante; ninguna lectura confirma: sólo el botón, y una sola vez. IMEI sintéticos.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ScanIntake } from '@/app/admin/components/ScanIntake';
import * as lib from '@/app/admin/lib/scan-intake';
import type { ScanCheck, ScanConfigurations, ScanReading } from '@/app/admin/lib/scan-intake';

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
const IMEI_2 = '350000000000022';
const CAP = 1, COL = 2, G128 = 11, BLACK = 21;

function reading(over: Partial<ScanReading>): ScanReading {
  return { value: '', kind: 'unknown', certain: false, alternatives: [], hint: '', problem: '', detail: '', ...over };
}
function answer(over: Partial<ScanReading>, rest: Partial<ScanCheck> = {}): ScanCheck {
  return { reading: reading(over), product: null, unit: null, conflict: null, serial_elsewhere: '', ...rest };
}

const config: ScanConfigurations = {
  model: { id: 9, name: 'Teléfono Q' }, line: 'new', has_palette: true,
  attributes: [
    { id: CAP, name: 'Capacidad', role: 'capacity', kind: 'text', varies: false, options: [{ id: G128, label: '128 GB', swatch_hex: '' }] },
    { id: COL, name: 'Color', role: 'color', kind: 'color', varies: false, options: [{ id: BLACK, label: 'Negro', swatch_hex: '#111' }] },
  ],
  combinations: [{
    key: 'a', option_ids: [G128, BLACK], pending_order_lines: [],
    variant: { product_id: 100, name: 'Teléfono Q 128 Negro', price: '3000.00', available: 2, is_active: true,
      pending_draft: false, sellable: true, is_serialized: true, requires_imei: true, on_order: null },
  }],
  truncated: false,
  can: { receive: true, create_variant: false, set_price: false, view_costs: false, choose_supplier: true, open_suppliers: false },
  suppliers: [{ id: 5, name: 'Mayorista Q' }],
  used_variants: [],
  branch: { id: 1, name: 'Central' },
} as unknown as ScanConfigurations;

const product = {
  id: 100, name: 'Teléfono Q 128 Negro', model_id: 9, condition_line: 'new', is_serialized: true, requires_imei: true,
  is_active: true, archived: false, option_ids: [G128, BLACK],
};

function byValue(code: string): ScanCheck {
  if (code === IMEI_1 || code === IMEI_2) return answer({ value: code, kind: 'imei', certain: true });
  if (code === '194253000001') return answer({ value: code, kind: 'gtin', certain: true }, { product });
  if (code === '1234567890') return answer({ value: code, kind: 'serial', alternatives: ['serial'] });
  if (code === '8'.repeat(32)) return answer({ value: code, kind: 'eid', certain: true, detail: 'Es el EID: no es un IMEI.' });
  return answer({ value: code.toUpperCase(), kind: 'serial', certain: true });
}

beforeEach(() => {
  jest.clearAllMocks();
  check.mockImplementation(async (_company, code, opts) =>
    opts?.choice === 'serial' ? answer({ value: code, kind: 'serial', certain: true }) : byValue(code));
  configs.mockResolvedValue(config);
  search.mockResolvedValue([]);
});

function renderScreen() {
  render(<ScanIntake companyId={7} branches={[{ id: 1, name: 'Central' }]} defaultBranch={1} canPrintLabels />);
  return screen.getByTestId('scan-intake-input') as HTMLInputElement;
}

/** A keyboard-wedge scanner: types the code and presses Enter. */
async function scan(input: HTMLInputElement, code: string, key = 'Enter') {
  fireEvent.change(input, { target: { value: code } });
  await act(async () => { fireEvent.keyDown(input, { key }); });
}

async function chooseVariantByBoxCode(input: HTMLInputElement) {
  await scan(input, '194253000001');
  await waitFor(() => expect(screen.getByTestId('scan-intake-variant')).toHaveTextContent('Teléfono Q 128 Negro'));
}

it('el código comercial de la caja elige la variante y no llena ninguna casilla', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  expect(configs).toHaveBeenCalledWith(7, 1, 9);
  expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent('—');
  expect(screen.getByTestId('scan-intake-log')).toHaveTextContent('identifica la variante, no el equipo');
});

it('lecturas consecutivas llenan IMEI 1, IMEI 2 y serie, y el indicador avanza', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  expect(screen.getByTestId('scan-intake-expected')).toHaveTextContent('IMEI 1');
  await scan(input, IMEI_1);
  await waitFor(() => expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent(IMEI_1));
  expect(screen.getByTestId('scan-intake-expected')).toHaveTextContent('IMEI 2');
  await scan(input, IMEI_2);
  await waitFor(() => expect(screen.getByTestId('scan-intake-value-imei2')).toHaveTextContent(IMEI_2));
  await scan(input, 'qz7k2m9xw1');
  await waitFor(() => expect(screen.getByTestId('scan-intake-value-serial')).toHaveTextContent('QZ7K2M9XW1'));
  expect(screen.getByTestId('scan-intake-expected')).toHaveTextContent('revisa y confirma');
  expect(input.value).toBe('');
});

it('un Enter doble (o con el campo vacío) no consulta ni confirma nada', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, IMEI_1);
  await waitFor(() => expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent(IMEI_1));
  const calls = check.mock.calls.length;
  await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }); fireEvent.keyDown(input, { key: 'Enter' }); });
  expect(check.mock.calls.length).toBe(calls);
  expect(receive).not.toHaveBeenCalled();
});

it('una lectura repetida se ignora y se dice', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, IMEI_1);
  await scan(input, IMEI_1);
  await waitFor(() => expect(screen.getByTestId('scan-intake-log')).toHaveTextContent('Lectura repetida'));
  expect(screen.getByTestId('scan-intake-value-imei2')).toHaveTextContent('—');
});

it('un EID se rechaza y nunca llena el IMEI', async () => {
  const input = renderScreen();
  await scan(input, '8'.repeat(32));
  await waitFor(() => expect(screen.getByTestId('scan-intake-log')).toHaveTextContent('Es el EID'));
  expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent('—');
});

it('un código dudoso se pregunta; elegida la serie, el servidor la confirma y se coloca', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, '1234567890');
  const dialog = await screen.findByTestId('scan-intake-ask');
  expect(screen.getByTestId('scan-intake-value-serial')).toHaveTextContent('—');
  await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Es la serie' })); });
  await waitFor(() => expect(screen.getByTestId('scan-intake-value-serial')).toHaveTextContent('1234567890'));
  expect(check).toHaveBeenLastCalledWith(7, '1234567890', expect.objectContaining({ productId: 100, choice: 'serial' }));
});

it('el foco vuelve al campo del lector después de cada lectura', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  screen.getByTestId('scan-intake-supplier').focus();
  await scan(input, IMEI_1);
  await waitFor(() => expect(document.activeElement).toBe(input));
});

it('Tab termina la lectura sólo si así está configurado', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, IMEI_1, 'Tab');
  expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent('—');
  fireEvent.change(screen.getByDisplayValue('Enter'), { target: { value: 'both' } });
  await scan(input, IMEI_1, 'Tab');
  await waitFor(() => expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent(IMEI_1));
});

it('sólo el botón confirma, una vez, con la clave de idempotencia y cómo se leyó', async () => {
  receive.mockResolvedValue({
    replayed: false, mode: 'stock', condition: 'new',
    unit: { id: 55, product_id: 100, product_name: 'Teléfono Q 128 Negro', branch_name: 'Central', label_code: 'U4827193056',
      imei: IMEI_1, imei2: null, serial_number: '', status_label: 'Disponible' },
    branch: { id: 1, name: 'Central' }, created_variant: false, order: null, method: 'manual', warnings: [],
  });
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, IMEI_1);
  await waitFor(() => expect(screen.getByTestId('scan-intake-confirm')).toBeEnabled());
  expect(receive).not.toHaveBeenCalled();
  const button = screen.getByTestId('scan-intake-confirm');
  await act(async () => { fireEvent.click(button); fireEvent.click(button); });
  await screen.findByTestId('scan-intake-done');
  expect(receive).toHaveBeenCalledTimes(1);
  const [company, branch, body, key] = receive.mock.calls[0];
  expect([company, branch]).toEqual([7, 1]);
  expect(body).toMatchObject({ condition: 'new', model: 9, option_ids: [G128, BLACK], imei: IMEI_1, method: 'manual' });
  expect(key).toBeTruthy();
  expect(screen.getByText('U4827193056')).toBeInTheDocument();
});

it('un pedido pagado que espera aquí se resuelve antes de confirmar', async () => {
  const waiting = {
    ...config,
    can: { ...config.can, receive_on_order: true },
    combinations: [{ ...config.combinations[0], pending_order_lines: [{
      order_item: 31, order_id: 8, order_number: 8, quantity: 1, customer: 'Ana Q.', created_at: null,
      branch: { id: 1, name: 'Central' }, here: true }] }],
  } as ScanConfigurations;
  configs.mockResolvedValue(waiting);
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, IMEI_1);
  await screen.findByTestId('scan-intake-waiting');
  expect(screen.getByTestId('scan-intake-confirm')).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/Es del pedido #8/));
  await waitFor(() => expect(screen.getByTestId('scan-intake-confirm')).toBeEnabled());
});

// ---------------------------------------------------------------------------
// Review of #437
// ---------------------------------------------------------------------------

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

it('F1: una respuesta que llega después de limpiar el equipo se descarta y se dice', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  const late = deferred<ScanCheck>();
  check.mockImplementationOnce(() => late.promise);
  await scan(input, IMEI_1);
  fireEvent.click(screen.getByRole('button', { name: 'Limpiar este equipo' }));
  await act(async () => { late.resolve(byValue(IMEI_1)); await late.promise; });
  await waitFor(() => expect(screen.getByTestId('scan-intake-log')).toHaveTextContent('descartada'));
  expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent('—');
});

it('F2/F5: con una pregunta abierta el foco sigue en el lector, Enter en una opción no elige y otra lectura espera', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, '1234567890');
  const dialog = await screen.findByTestId('scan-intake-ask');
  await waitFor(() => expect(document.activeElement).toBe(input));
  const calls = check.mock.calls.length;
  // Another read while the question is open: not sent, and said.
  await scan(input, IMEI_1);
  expect(check.mock.calls.length).toBe(calls);
  expect(screen.getByTestId('scan-intake-log')).toHaveTextContent('Responde primero');
  // A scanner's trailing Enter on a focused option, with characters in the buffer: no choice is made.
  const option = within(dialog).getByRole('button', { name: 'Es la serie' });
  fireEvent.change(input, { target: { value: '99' } });
  option.focus();
  await act(async () => { fireEvent.keyDown(option, { key: 'Enter' }); });
  expect(screen.getByTestId('scan-intake-ask')).toBeInTheDocument();
  expect(check.mock.calls.length).toBe(calls);
  fireEvent.change(input, { target: { value: '' } });
  await act(async () => { fireEvent.click(option); });
  await waitFor(() => expect(screen.getByTestId('scan-intake-value-serial')).toHaveTextContent('1234567890'));
  expect(document.activeElement).toBe(input);
});

it('F3: con «Confirmar ingreso» enfocado, una lectura va al lector y su Enter no confirma', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, IMEI_1);
  const confirm = screen.getByTestId('scan-intake-confirm');
  await waitFor(() => expect(confirm).toBeEnabled());
  confirm.focus();
  for (const key of IMEI_2) fireEvent.keyDown(confirm, { key });
  expect(input.value).toBe(IMEI_2);
  await act(async () => { fireEvent.keyDown(confirm, { key: 'Enter' }); });
  await waitFor(() => expect(screen.getByTestId('scan-intake-value-imei2')).toHaveTextContent(IMEI_2));
  expect(receive).not.toHaveBeenCalled();
});

it('F6: elegir «Seminuevo» no le quita el foco a ese botón', async () => {
  renderScreen();
  const used = screen.getByRole('button', { name: 'Seminuevo' });
  used.focus();
  fireEvent.click(used);
  await new Promise((r) => setTimeout(r, 5));
  expect(document.activeElement).toBe(used);
});

it('F7: con pantalla táctil el lector no abre el teclado del móvil, salvo que se pida', () => {
  const original = window.matchMedia;
  window.matchMedia = ((query: string) => ({
    matches: query.includes('coarse'), media: query, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  try {
    const input = renderScreen();
    expect(input.getAttribute('inputmode')).toBe('none');
    expect(document.activeElement).not.toBe(input);
    fireEvent.click(screen.getByRole('button', { name: /Teclado/ }));
    expect(input.getAttribute('inputmode')).toBe('text');
  } finally {
    window.matchMedia = original;
  }
});

it('F8: una combinación nueva sin «Administrar productos» no pide precio y no se confirma', async () => {
  configs.mockResolvedValue({
    ...config, combinations: [{ ...config.combinations[0], variant: null }],
  } as ScanConfigurations);
  const input = renderScreen();
  await scan(input, '194253000001');
  await waitFor(() => expect(screen.getByTestId('scan-intake-variant')).toBeInTheDocument());
  await scan(input, IMEI_1);
  await waitFor(() => expect(screen.getByTestId('scan-intake-value-imei')).toHaveTextContent(IMEI_1));
  expect(screen.queryByTestId('scan-intake-price')).not.toBeInTheDocument();
  expect(screen.getByTestId('scan-intake-problems')).toHaveTextContent('Administrar productos');
  expect(screen.getByTestId('scan-intake-confirm')).toBeDisabled();
});

it('F10: cada botón de casilla dice qué casilla toca', async () => {
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, IMEI_1);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Borrar IMEI 1' })).toBeInTheDocument());
  const toggle = screen.getByRole('button', { name: 'Marcar que no tiene IMEI 2' });
  expect(toggle).not.toHaveAttribute('aria-pressed');
});

it('F11: tras un 5xx se reintenta con la misma clave; tras un rechazo 4xx, con otra', async () => {
  receive
    .mockRejectedValueOnce(new lib.ScanIntakeError('Error del servidor', '', '', 502))
    .mockRejectedValueOnce(new lib.ScanIntakeError('Ese IMEI ya está registrado', 'duplicate_imei', 'imei', 409))
    .mockRejectedValueOnce(new lib.ScanIntakeError('Otra cosa', 'invalid', '', 400));
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, IMEI_1);
  const confirm = screen.getByTestId('scan-intake-confirm');
  for (let n = 0; n < 3; n += 1) {
    await waitFor(() => expect(confirm).toBeEnabled());
    await act(async () => { fireEvent.click(confirm); });
  }
  const keys = receive.mock.calls.map((call) => call[3]);
  expect(keys[1]).toBe(keys[0]);
  expect(keys[2]).not.toBe(keys[1]);
});

it('F12: mientras se registra, salir de la página se detiene', async () => {
  const pending = deferred<lib.ScanReceived>();
  receive.mockImplementationOnce(() => pending.promise);
  const input = renderScreen();
  await chooseVariantByBoxCode(input);
  await scan(input, IMEI_1);
  await waitFor(() => expect(screen.getByTestId('scan-intake-confirm')).toBeEnabled());
  await act(async () => { fireEvent.click(screen.getByTestId('scan-intake-confirm')); });
  const leave = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(leave);
  expect(leave.defaultPrevented).toBe(true);
  const link = document.createElement('a');
  link.href = '/admin';
  document.body.appendChild(link);
  const click = new MouseEvent('click', { bubbles: true, cancelable: true });
  link.dispatchEvent(click);
  expect(click.defaultPrevented).toBe(true);
  link.remove();
});
