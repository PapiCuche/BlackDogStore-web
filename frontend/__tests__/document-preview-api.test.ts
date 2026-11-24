import { fetchWithAuth } from '@/app/lib/auth';
import {
  DocumentPreviewError, PREVIEW_OPTIONS, FALLBACK_FILE_NAME, fetchDocumentPreview, fetchThermalPreview, fileNameFrom,
} from '@/app/admin/lib/document-preview-api';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = jest.mocked(fetchWithAuth);

function reply(status: number, body: string, headers: Record<string, string> = {}): Response {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => lower[name.toLowerCase()] ?? null },
    json: async () => JSON.parse(body),
    blob: async () => new Blob([body]),
  } as unknown as Response;
}

beforeEach(() => send.mockReset());

test('ofrece exactamente los nueve PDF del servidor y la térmica de la nota', () => {
  expect(PREVIEW_OPTIONS.map((o) => o.kind)).toEqual([
    'sales_note', 'order_receipt', 'ticket80_sales_note', 'thermal_sales_note', 'boleta', 'factura', 'ticket80_boleta',
    'ticket80_factura', 'sales_quote', 'ticket80_sales_quote',
  ]);
  expect(PREVIEW_OPTIONS.filter((o) => o.format === 'escpos').map((o) => [o.kind, o.fiscal]))
    .toEqual([['thermal_sales_note', false]]);
});

const ROLL = {
  document: 'sales_note', paper_width_mm: 80, columns: 48, dots: 576, dots_per_mm: 8, sample: 'MUESTRA · NO FISCAL',
  lines: [
    { type: 'image', align: 'left', width: 400, height: 133, png: 'iVBORw0KGgo=' },
    { type: 'text', text: 'NOTA DE VENTA', align: 'center', bold: true, large: false },
    { type: 'feed', lines: 3 },
    { type: 'cut' },
  ],
};

test('térmica: POST a /thermal/ con la nota y el borrador; devuelve el rollo', async () => {
  send.mockResolvedValue(reply(200, JSON.stringify(ROLL), { 'Content-Type': 'application/json' }));
  const out = await fetchThermalPreview(7, undefined, { footer_text: 'Pie' });
  const [url, init] = send.mock.calls[0];
  expect(String(url)).toMatch(/\/admin\/document-preview\/thermal\/\?company=7$/);
  expect(init?.method).toBe('POST');
  expect(JSON.parse(String(init?.body))).toEqual({ document: 'sales_note', design: { footer_text: 'Pie' } });
  expect(out.lines).toHaveLength(4);
  expect(out.columns).toBe(48);
});

test('THERMAL-512: la térmica se pide al ancho elegido', async () => {
  send.mockResolvedValue(reply(200, JSON.stringify({ ...ROLL, columns: 42, dots: 512 }), { 'Content-Type': 'application/json' }));
  const out = await fetchThermalPreview(7, undefined, undefined, 512);
  expect(JSON.parse(String(send.mock.calls[0][1]?.body))).toEqual({ document: 'sales_note', print_width_dots: 512 });
  expect(out.columns).toBe(42);
});

test('térmica: sin borrador ni empresa sólo pide la nota', async () => {
  send.mockResolvedValue(reply(200, JSON.stringify(ROLL), { 'Content-Type': 'application/json' }));
  await fetchThermalPreview(null);
  expect(String(send.mock.calls[0][0])).toMatch(/\/admin\/document-preview\/thermal\/$/);
  expect(JSON.parse(String(send.mock.calls[0][1]?.body))).toEqual({ document: 'sales_note' });
});

test.each([
  ['no es JSON', '<html>'],
  ['sin renglones', JSON.stringify({ ...ROLL, lines: undefined })],
  ['un renglón desconocido', JSON.stringify({ ...ROLL, lines: [{ type: 'drawer' }] })],
  ['una alineación inventada', JSON.stringify({ ...ROLL, lines: [{ ...ROLL.lines[1], align: 'justify' }] })],
  ['una imagen que no es base64', JSON.stringify({ ...ROLL, lines: [{ ...ROLL.lines[0], png: '"><script>' }] })],
])('térmica: una respuesta inesperada es un error (%s)', async (_case, body) => {
  send.mockResolvedValue(reply(200, body, { 'Content-Type': 'application/json' }));
  await expect(fetchThermalPreview(1)).rejects.toThrow('El servidor no devolvió la vista previa de la térmica.');
});

test('térmica: los errores del servidor se dicen como en los PDF', async () => {
  send.mockResolvedValue(reply(403, JSON.stringify({ detail: 'x' })));
  await expect(fetchThermalPreview(1)).rejects.toThrow(/No tienes permiso/);
  send.mockResolvedValue(reply(400, JSON.stringify({ design: { logo_ticket_align: ['Elige izquierda, centro o derecha.'] } })));
  await expect(fetchThermalPreview(1, undefined, {})).rejects.toThrow(/El diseño tiene un error: Elige izquierda/);
});

test('POST con {document} y ?company=; devuelve el blob y el nombre del servidor', async () => {
  send.mockResolvedValue(reply(200, '%PDF-1.4 x', {
    'Content-Type': 'application/pdf',
    'Content-Disposition': 'inline; filename="MUESTRA-factura.pdf"',
  }));
  const out = await fetchDocumentPreview('factura', 7);
  const [url, init] = send.mock.calls[0];
  expect(String(url)).toMatch(/\/admin\/document-preview\/\?company=7$/);
  expect(init?.method).toBe('POST');
  expect(JSON.parse(String(init?.body))).toEqual({ document: 'factura' });
  expect(out.fileName).toBe('MUESTRA-factura.pdf');
  expect(out.blob.size).toBeGreaterThan(0);
});

test('sin empresa no añade ?company=', async () => {
  send.mockResolvedValue(reply(200, 'x', { 'Content-Type': 'application/pdf' }));
  const out = await fetchDocumentPreview('boleta', null);
  expect(String(send.mock.calls[0][0])).toMatch(/\/admin\/document-preview\/$/);
  expect(out.fileName).toBe(FALLBACK_FILE_NAME);
});

test('el nombre del archivo no admite rutas', () => {
  expect(fileNameFrom('inline; filename="../etc/x.pdf"')).toBe(FALLBACK_FILE_NAME);
  expect(fileNameFrom(null)).toBe(FALLBACK_FILE_NAME);
});

test('una respuesta que no es PDF es un error', async () => {
  send.mockResolvedValue(reply(200, '<html>', { 'Content-Type': 'text/html' }));
  await expect(fetchDocumentPreview('factura', 1)).rejects.toThrow('El servidor no devolvió un PDF.');
});

test.each([
  [403, { detail: 'x' }, {}, /No tienes permiso/],
  [429, { detail: 'throttled' }, { 'Retry-After': '30' }, /demasiadas vistas previas.*30 segundos/],
  [429, {}, {}, /Espera un momento/],
  [413, { detail: 'La petición es demasiado grande.' }, {}, /demasiado grande/],
  [400, { document: ['Elige uno de: a, b.'] }, {}, /Elige uno de/],
  [500, {}, {}, /No se pudo generar/],
])('error %s se traduce', async (status, body, headers, re) => {
  send.mockResolvedValue(reply(status, JSON.stringify(body), { 'Content-Type': 'application/json', ...headers }));
  const err = await fetchDocumentPreview('factura', 1).catch((e) => e);
  expect(err).toBeInstanceOf(DocumentPreviewError);
  expect(err.status).toBe(status);
  expect(err.message).toMatch(re);
});

test('un AbortError se propaga sin convertirse en fallo', async () => {
  const controller = new AbortController();
  send.mockImplementation((_u, init) => new Promise((_res, rej) => {
    init?.signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')));
  }));
  const pending = fetchDocumentPreview('factura', 1, controller.signal);
  controller.abort();
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  expect(send.mock.calls[0][1]?.signal).toBe(controller.signal);
});

test('un fallo de red se dice como tal', async () => {
  send.mockRejectedValue(new TypeError('Failed to fetch'));
  await expect(fetchDocumentPreview('factura', 1)).rejects.toThrow(/No se pudo conectar/);
});
