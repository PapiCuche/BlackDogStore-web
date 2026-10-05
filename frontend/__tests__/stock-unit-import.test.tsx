import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { StockUnitImport } from '@/app/admin/components/StockUnitImport';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * UNIT-IMPORT · «Equipos serializados.xlsx».
 *
 * Una fila es un equipo físico. La pantalla lo dice antes de adjuntar nada,
 * enseña qué haría el servidor con cada fila y sólo entonces ofrece cargar.
 * No valida un IMEI ni cuenta equipos: muestra lo que el servidor respondió.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const BRANCHES = [{ id: 1, name: 'Centro' }, { id: 2, name: 'Norte' }];

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: 31, import_type: 'units', status: 'previewed', original_filename: 'equipos.xlsx',
    counts: { total: 2, create: 2, update: 0, no_change: 0, skip: 0, error: 0 },
    summary: { units: 2 }, is_applicable: true, rows_truncated: false,
    rows: [
      { sheet: 'Equipos', row: 2, action: 'create', match_key: 'F2LXK1ABC1', errors: [], warnings: [],
        data: { name: 'iPhone 16', branch: 'Centro', serial_number: 'F2LXK1ABC1', imei: '356938035643809', imei2: '', condition: 'new', reason: 'Compra F001-204' } },
      { sheet: 'Equipos', row: 3, action: 'create', match_key: 'F2LXK1ABC2', errors: [], warnings: [],
        data: { name: 'iPhone 16', branch: 'Norte', serial_number: 'F2LXK1ABC2', imei: '356938035643817', imei2: '', condition: 'used', reason: 'Compra F001-204' } },
    ],
    ...overrides,
  };
}

type Call = { method: string; path: string; form: FormData | null };
let calls: Call[];
let previewReply: { status: number; body: unknown };
let applyReply: { status: number; body: unknown };

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  calls = [];
  previewReply = { status: 201, body: job() };
  applyReply = { status: 200, body: job({ status: 'applied', is_applicable: false, summary: { units: 2, applied: { units: 2, movements: 2 } } }) };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input).replace(/^.*\/admin\/inventory\/units/, '');
    calls.push({ method: (init.method ?? 'GET').toUpperCase(), path, form: init.body instanceof FormData ? init.body : null });
    if (path.startsWith('/import/preview/')) return reply(previewReply.status, previewReply.body);
    return reply(applyReply.status, applyReply.body);
  });
});

function attach() {
  const file = new File(['x'], 'equipos.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  fireEvent.change(screen.getByLabelText('Archivo de equipos (.xlsx)'), { target: { files: [file] } });
  return file;
}

test('the screen says one row is one device before anything is attached', () => {
  render(<StockUnitImport branches={BRANCHES} />);

  expect(screen.getByText(/Una fila = un equipo físico/)).toBeInTheDocument();
  expect(screen.getByText(/No hay columna de cantidad/)).toBeInTheDocument();
  const template = screen.getByRole('link', { name: 'Descargar plantilla' });
  expect(template.getAttribute('href')).toMatch(/\/admin\/inventory\/units\/import\/template\/$/);
  expect(screen.getByRole('button', { name: 'Previsualizar' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: /Registrar \d+ equipo/ })).toBeNull();
});

test('a preview shows each row and registering is a second, explicit step', async () => {
  render(<StockUnitImport branches={BRANCHES} />);
  const file = attach();
  fireEvent.change(screen.getByLabelText('Sucursal para las filas sin sucursal'), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('Motivo para las filas sin motivo'), { target: { value: 'Inventario inicial' } });
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));

  const table = await screen.findByRole('table', { name: 'Equipos del archivo' });
  const sent = calls[0];
  expect(sent.method).toBe('POST');
  expect(sent.path).toBe('/import/preview/');
  expect(sent.form?.get('file')).toBe(file);
  expect(sent.form?.get('branch')).toBe('2');
  expect(sent.form?.get('reason')).toBe('Inventario inicial');
  expect(within(table).getByText('F2LXK1ABC1')).toBeInTheDocument();
  expect(within(table).getByText('356938035643817')).toBeInTheDocument();
  expect(within(table).getByText('Norte')).toBeInTheDocument();
  expect(calls).toHaveLength(1);                       // previsualizar no carga

  fireEvent.click(screen.getByRole('button', { name: 'Registrar 2 equipos' }));

  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1]).toMatchObject({ method: 'POST', path: '/import/31/apply/' });
  expect(await screen.findByRole('status')).toHaveTextContent('Se registraron 2 equipos');
  expect(screen.getByRole('link', { name: 'Ver los equipos' })).toHaveAttribute('href', '/admin/inventory/units');
  expect(screen.queryByRole('button', { name: /Registrar \d+ equipo/ })).toBeNull();
});

test('a file with a bad row cannot be registered and says which row and why', async () => {
  previewReply = { status: 201, body: job({
    counts: { total: 2, create: 1, update: 0, no_change: 0, skip: 0, error: 1 }, is_applicable: false,
    rows: [
      { sheet: 'Equipos', row: 3, action: 'error', match_key: 'F2LXK1ABC2', warnings: [],
        errors: ['"iPhone 16" lleva IMEI: indícalo para cada equipo.'],
        data: { name: 'iPhone 16', branch: 'Centro', serial_number: 'F2LXK1ABC2' } },
      job().rows[0],
    ],
  }) };
  render(<StockUnitImport branches={BRANCHES} />);
  attach();
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));

  const table = await screen.findByRole('table', { name: 'Equipos del archivo' });
  const bad = within(table).getByText('F2LXK1ABC2').closest('tr') as HTMLElement;
  expect(within(bad).getByText('3')).toBeInTheDocument();
  expect(within(bad).getByText(/lleva IMEI/)).toBeInTheDocument();
  expect(screen.getByText(/1 con error/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Registrar \d+ equipo/ })).toBeNull();
  expect(screen.getByRole('link', { name: 'Descargar errores (CSV)' }).getAttribute('href')).toMatch(/\/admin\/imports\/31\/errors\.csv\/$/);
});

test('a refusal from the server is shown as the server said it', async () => {
  previewReply = { status: 400, body: { detail: 'Esta plantilla no lleva la columna «Cantidad»: una fila es un equipo.' } };
  render(<StockUnitImport branches={BRANCHES} />);
  attach();
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('no lleva la columna «Cantidad»');
  expect(screen.queryByRole('table')).toBeNull();
});

test('what changed between the preview and the load is reported and nothing claims success', async () => {
  applyReply = { status: 400, body: { detail: 'No se cargó ningún equipo: cambió algo desde la previsualización (fila 3).' } };
  render(<StockUnitImport branches={BRANCHES} />);
  attach();
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Registrar 2 equipos' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('No se cargó ningún equipo');
  expect(screen.queryByRole('status')).toBeNull();
});
