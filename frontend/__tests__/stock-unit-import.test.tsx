import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { StockUnitImport } from '@/app/admin/components/StockUnitImport';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * UNIT-IMPORT · la carga de equipos.
 *
 * Una fila es un equipo físico. La pantalla lo dice antes de adjuntar nada,
 * enseña qué haría el servidor con cada fila y sólo entonces ofrece cargar.
 * No valida un IMEI ni cuenta equipos: muestra lo que el servidor respondió.
 */

jest.mock('@/app/lib/auth', () => ({ ...jest.requireActual('@/app/lib/auth'), fetchWithAuth: jest.fn() }));
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
let voidReply: { status: number; body: unknown };
let voidPreviewReply: { status: number; body: unknown };

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  calls = [];
  previewReply = { status: 201, body: job() };
  applyReply = { status: 200, body: job({ status: 'applied', is_applicable: false, summary: { units: 2, applied: { units: 2, movements: 2 } } }) };
  voidPreviewReply = { status: 200, body: {
    units: 2, blocking: [], inspected: 0, published: 0, with_photos: 0, reason_max: 236, voided: null,
  } };
  voidReply = { status: 200, body: job({
    status: 'applied', is_applicable: false, changed: true,
    summary: { units: 2, applied: { units: 2, movements: 2 }, voided: { units: 2, at: '2026-11-24T10:00:00Z', by: 'ana', reason: 'Archivo duplicado' } },
  }) };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input).replace(/^.*\/admin\/inventory\/units/, '');
    calls.push({ method: (init.method ?? 'GET').toUpperCase(), path, form: init.body instanceof FormData ? init.body : null });
    if (path.startsWith('/import/preview/')) return reply(previewReply.status, previewReply.body);
    if (path.includes('/void/') && (init.method ?? 'GET').toUpperCase() === 'GET') {
      return reply(voidPreviewReply.status, voidPreviewReply.body);
    }
    if (path.includes('/void/')) return reply(voidReply.status, voidReply.body);
    return reply(applyReply.status, applyReply.body);
  });
});

function attach() {
  const file = new File(['x'], 'equipos.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  fireEvent.change(screen.getByLabelText('Archivo de equipos (.xlsx)'), { target: { files: [file] } });
  // INV-LOAD-02 review: the line of the file is always said.
  fireEvent.click(screen.getByRole('radio', { name: 'Nuevos' }));
  return file;
}

test('the screen says one row is one device before anything is attached', () => {
  render(<StockUnitImport branches={BRANCHES} />);

  expect(screen.getByText(/Una fila = un equipo físico/)).toBeInTheDocument();
  expect(screen.getByText(/No hay columna de cantidad/)).toBeInTheDocument();
  // IMPORT-SPLIT-01: y a dónde ir si el producto aún no existe.
  expect(screen.getByRole('link', { name: 'Carga de productos' })).toHaveAttribute('href', '/admin/products/import');
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

test('an IMEI under a product not marked as carrying one is said above the table, with how many', async () => {
  previewReply = { status: 201, body: job({ summary: { units: 2, imei_on_unmarked_products: 1 }, rows_truncated: true }) };
  render(<StockUnitImport branches={BRANCHES} />);
  attach();
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));

  // Aunque la fila avisada no quepa entre las que se muestran, la cuenta está a la vista.
  const said = await screen.findByRole('note', { name: 'IMEI en productos sin marca' });
  expect(said).toHaveTextContent('1 equipo trae IMEI');
  expect(said).toHaveTextContent('no está marcado «lleva IMEI»');
  expect(said).toHaveTextContent('El IMEI se guardará');
  expect(screen.getByText(/las de error primero y las que llevan un aviso después/)).toBeInTheDocument();
  // No impide registrar: es un aviso.
  expect(screen.getByRole('button', { name: 'Registrar 2 equipos' })).toBeEnabled();
});

test('without such rows nothing is said', async () => {
  previewReply = { status: 201, body: job({ summary: { units: 2, imei_on_unmarked_products: 0 } }) };
  render(<StockUnitImport branches={BRANCHES} />);
  attach();
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  await screen.findByRole('button', { name: 'Registrar 2 equipos' });
  expect(screen.queryByRole('note', { name: 'IMEI en productos sin marca' })).toBeNull();
});

test('IMPORT-STALE-01: an expired preview is refused with «Subir el archivo otra vez» and registering is disabled', async () => {
  const sentence = 'Esta vista previa caducó: tiene más de 60 minutos. No se aplicó nada. Sube el archivo otra vez.';
  applyReply = { status: 409, body: { detail: sentence, code: 'preview_expired' } };
  render(<StockUnitImport branches={BRANCHES} />);
  attach();
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  await screen.findByRole('table', { name: 'Equipos del archivo' });
  fireEvent.click(screen.getByRole('button', { name: 'Registrar 2 equipos' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('La vista previa caducó · no se aplicó nada');
  expect(alert).toHaveTextContent(sentence);
  expect(screen.getByRole('button', { name: 'Registrar 2 equipos' })).toBeDisabled();

  fireEvent.click(within(alert).getByRole('button', { name: 'Subir el archivo otra vez' }));
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByRole('table', { name: 'Equipos del archivo' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Previsualizar' })).toBeDisabled();   // the file is chosen again
});

test('INV-OPENING-01: a purchase by default, and «Inventario inicial» travels as opening', async () => {
  render(<StockUnitImport branches={BRANCHES} />);
  expect(screen.getByRole('radio', { name: /Compra a proveedor/ })).toBeChecked();
  attach();
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  await screen.findByRole('table', { name: 'Equipos del archivo' });
  expect(calls[0].form?.get('entry_kind')).toBe('purchase');

  const opening = screen.getByRole('radio', { name: 'Inventario inicial' });   // the help is its description, not its name
  expect(opening).toHaveAccessibleDescription(/^Equipos que ya tenías\. Se pueden cargar en varias tandas/);
  fireEvent.click(opening);
  expect(screen.queryByRole('table', { name: 'Equipos del archivo' })).toBeNull();   // the old preview is dropped
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1].form?.get('entry_kind')).toBe('opening');
});

test('STOCK-EXISTING-01: «Ingreso de inventario existente» travels as existing', async () => {
  render(<StockUnitImport branches={BRANCHES} />);
  const existing = screen.getByRole('radio', { name: 'Ingreso de inventario existente' });
  expect(existing).toHaveAccessibleDescription(/^Equipos que ya tenías; no es una compra\. Vale en cualquier estante/);
  expect(existing).toHaveAccessibleDescription(/elige «Inventario inicial»/);
  attach();
  fireEvent.click(existing);
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0].form?.get('entry_kind')).toBe('existing');
});

// ---------------------------------------------------------------------------
// INV-LOAD-02 — two stock templates, the before/after and annulling a load whole
// ---------------------------------------------------------------------------

async function applied(overrides: Record<string, unknown> = {}) {
  previewReply = { status: 201, body: job(overrides) };
  applyReply = { status: 200, body: job({ ...overrides, status: 'applied', is_applicable: false, summary: { units: 2, applied: { units: 2, movements: 2 } } }) };
  render(<StockUnitImport branches={BRANCHES} />);
  attach();
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Registrar 2 equipos' }));
  await screen.findByText(/Se registraron 2 equipos/);
}

test('INV-LOAD-02: one template per line, and the combined one still there', () => {
  render(<StockUnitImport branches={BRANCHES} />);
  expect(screen.getByRole('link', { name: 'Stock de equipos nuevos' }).getAttribute('href'))
    .toMatch(/\/admin\/inventory\/units\/import\/template\/\?line=new$/);
  expect(screen.getByRole('link', { name: 'Stock de seminuevos' }).getAttribute('href'))
    .toMatch(/\/admin\/inventory\/units\/import\/template\/\?line=used$/);
  expect(screen.getByRole('link', { name: 'Descargar plantilla' }).getAttribute('href'))
    .toMatch(/\/admin\/inventory\/units\/import\/template\/$/);
});

test('INV-LOAD-02: the preview says which template the file is', async () => {
  previewReply = { status: 201, body: job({ options: { line: 'used', entry_kind: 'opening' } }) };
  render(<StockUnitImport branches={BRANCHES} />);
  attach();
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  expect(await screen.findByText(/plantilla de seminuevos/)).toBeInTheDocument();
});

test('INV-LOAD-02: an applied load offers its before/after and, for used devices, what comes next', async () => {
  await applied({ options: { line: 'used' } });
  expect(screen.getByRole('link', { name: 'Antes y después (CSV)' }).getAttribute('href'))
    .toMatch(/\/admin\/inventory\/units\/import\/31\/report\/\?as=csv$/);
  expect(screen.getByRole('link', { name: 'Antes y después (Excel)' }).getAttribute('href'))
    .toMatch(/\/import\/31\/report\/\?as=xlsx$/);
  expect(screen.getByRole('note', { name: 'Después de cargar seminuevos' }))
    .toHaveTextContent('pendientes de inspección y sin publicar');
});

test('INV-LOAD-02: annulling a load asks for a reason and says what it did', async () => {
  await applied();
  fireEvent.click(screen.getByRole('button', { name: 'Anular esta carga' }));
  const confirm = await screen.findByRole('button', { name: 'Anular 2 equipos' });
  expect(confirm).toBeDisabled();
  await waitFor(() => expect(screen.getByLabelText('Motivo de la anulación')).toHaveAttribute('maxLength', '236'));
  fireEvent.change(screen.getByLabelText('Motivo de la anulación'), { target: { value: 'Archivo duplicado' } });
  fireEvent.click(confirm);

  await screen.findByText(/Esta carga se anuló/);
  const sent = send.mock.calls.find(([input]) => String(input).includes('/void/'));
  expect(String(sent?.[0])).toMatch(/\/import\/31\/void\/$/);
  expect(JSON.parse(String(sent?.[1]?.body ?? 'null'))).toBeNull();      // the first call is the preview (GET)
  const posted = send.mock.calls.find(([input, init]) => String(input).includes('/void/') && init?.method === 'POST');
  expect(JSON.parse(String(posted?.[1]?.body))).toEqual({ reason: 'Archivo duplicado' });
  expect(screen.getByText(/2 equipos salieron del stock/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Anular esta carga' })).toBeNull();
});

test('INV-LOAD-02: a load with a device that moved is not annulled, and the device is named by its label', async () => {
  voidReply = { status: 409, body: {
    detail: 'No se anuló ningún equipo: 1 equipo(s) de esta carga ya se movieron.', code: 'batch_not_voidable',
    blocking: [{ unit_id: 7, label_code: 'U4827193056', product_id: 3, branch_id: 1, status: 'sold', reason: 'Está vendido.' }],
  } };
  await applied();
  fireEvent.click(screen.getByRole('button', { name: 'Anular esta carga' }));
  fireEvent.change(await screen.findByLabelText('Motivo de la anulación'), { target: { value: 'Error' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Anular 2 equipos' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Anular 2 equipos' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('No se anuló ningún equipo');
  const list = screen.getByRole('list', { name: 'Equipos que impiden anular' });
  expect(within(list).getByText(/U4827193056: Está vendido\./)).toBeInTheDocument();
  expect(screen.queryByText(/Esta carga se anuló/)).toBeNull();
});

test('INV-LOAD-02 review: nothing is previewed until the line of the file is said, and it travels', async () => {
  render(<StockUnitImport branches={BRANCHES} />);
  const file = new File(['x'], 'copia.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  fireEvent.change(screen.getByLabelText('Archivo de equipos (.xlsx)'), { target: { files: [file] } });
  expect(screen.getByRole('button', { name: 'Previsualizar' })).toBeDisabled();
  expect(screen.getByText('Elige si el archivo trae equipos nuevos o seminuevos.')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('radio', { name: 'Seminuevos' }));
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  await screen.findByRole('table', { name: 'Equipos del archivo' });
  expect(calls[0].form?.get('line')).toBe('used');

  fireEvent.click(screen.getByRole('radio', { name: /Ambos: la columna «Línea»/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1].form?.has('line')).toBe(false);
});

test('INV-LOAD-02 review: devices already inspected, published or photographed need an explicit confirmation', async () => {
  voidPreviewReply = { status: 200, body: {
    units: 2, blocking: [], inspected: 2, published: 1, with_photos: 1, reason_max: 236, voided: null,
  } };
  await applied();
  fireEvent.click(screen.getByRole('button', { name: 'Anular esta carga' }));
  const said = await screen.findByRole('note', { name: 'Trabajo hecho sobre estos equipos' });
  expect(said).toHaveTextContent('2 inspeccionados, 1 publicados y 1 con fotos');
  fireEvent.change(screen.getByLabelText('Motivo de la anulación'), { target: { value: 'Error' } });
  const confirm = screen.getByRole('button', { name: 'Anular 2 equipos' });
  expect(confirm).toBeDisabled();
  fireEvent.click(within(said).getByRole('checkbox'));
  expect(confirm).toBeEnabled();
  fireEvent.click(confirm);
  await screen.findByText(/Esta carga se anuló/);
  const posted = send.mock.calls.find(([input, init]) => String(input).includes('/void/') && init?.method === 'POST');
  expect(JSON.parse(String(posted?.[1]?.body))).toEqual({ reason: 'Error', confirm_used: true });
});

test('INV-LOAD-02 review: devices that moved are named before anything is tried', async () => {
  voidPreviewReply = { status: 200, body: {
    units: 1, inspected: 0, published: 0, with_photos: 0, reason_max: 236, voided: null,
    blocking: [{ unit_id: 9, label_code: 'U1111111116', product_id: 3, branch_id: 1, status: 'available',
      reason: 'Está en una transferencia en borrador: quítalo de ella primero.' }],
  } };
  await applied();
  fireEvent.click(screen.getByRole('button', { name: 'Anular esta carga' }));
  const list = await screen.findByRole('list', { name: 'Equipos que impiden anular' });
  expect(within(list).getByText(/U1111111116: Está en una transferencia en borrador/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Motivo de la anulación'), { target: { value: 'Error' } });
  expect(screen.getByRole('button', { name: 'Anular 1 equipo' })).toBeDisabled();
});
