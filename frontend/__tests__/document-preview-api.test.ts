import { fetchWithAuth } from '@/app/lib/auth';
import {
  DocumentPreviewError, PREVIEW_OPTIONS, FALLBACK_FILE_NAME, fetchDocumentPreview, fileNameFrom,
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

test('ofrece exactamente los siete documentos del servidor', () => {
  expect(PREVIEW_OPTIONS.map((o) => o.kind)).toEqual([
    'sales_note', 'order_receipt', 'ticket80_sales_note', 'boleta', 'factura', 'ticket80_boleta', 'ticket80_factura',
  ]);
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
