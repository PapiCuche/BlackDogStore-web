import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { DocumentPreviewError } from '@/app/admin/lib/document-preview-api';

const mockFetch = jest.fn();
jest.mock('@/app/admin/lib/document-preview-api', () => ({
  ...jest.requireActual('@/app/admin/lib/document-preview-api'),
  fetchDocumentPreview: (...a: unknown[]) => mockFetch(...a),
}));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: InternalContext) => React.ReactNode }) =>
    children((globalThis as { __ctx?: InternalContext }).__ctx as InternalContext),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const DocumentsPage = require('@/app/admin/settings/documents/page').default;
const { REVOKE_DELAY_MS } = require('@/app/admin/settings/documents/DocumentPreview');
/* eslint-enable @typescript-eslint/no-require-imports */

afterEach(() => { jest.useRealTimers(); });

/** Relojes falsos para la revocación diferida, con un userEvent que los hace avanzar. */
function fakeClock() {
  jest.useFakeTimers();
  return userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
}
const passDelay = () => act(() => { jest.advanceTimersByTime(REVOKE_DELAY_MS); });

function context(capabilities: string[], master = false): InternalContext {
  return {
    user: { id: 1, username: 'x' },
    dashboard: { company: { id: 3, slug: 'taller', name: 'Taller' }, access: { is_platform_admin: master, capabilities } },
    selectedCompanyId: 3, selectCompany: () => undefined, reload: () => undefined,
  } as unknown as InternalContext;
}
const asManager = () => { (globalThis as { __ctx?: InternalContext }).__ctx = context(['company.view', 'company.manage']); };

const DESKTOP_SAFARI = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15';
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const ANDROID_CHROME = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';

/** El navegador del caso: si tiene visor de PDF, su agente y si la pantalla es táctil. */
function browserIs({ pdf, ua = DESKTOP_SAFARI, touch = 0 }: { pdf: boolean | undefined; ua?: string; touch?: number }) {
  Object.defineProperty(window.navigator, 'pdfViewerEnabled', { configurable: true, get: () => pdf });
  Object.defineProperty(window.navigator, 'userAgent', { configurable: true, get: () => ua });
  Object.defineProperty(window.navigator, 'maxTouchPoints', { configurable: true, get: () => touch });
}

const created: string[] = [];
const revoked: string[] = [];
beforeEach(() => {
  browserIs({ pdf: true });
  mockFetch.mockReset();
  created.length = 0; revoked.length = 0;
  let n = 0;
  URL.createObjectURL = jest.fn(() => { const u = `blob:test/${++n}`; created.push(u); return u; });
  URL.revokeObjectURL = jest.fn((u: string) => { revoked.push(u); });
});

const pdf = (name = 'MUESTRA-a.pdf') => ({ blob: new Blob(['%PDF'], { type: 'application/pdf' }), fileName: name });

test('sin company.manage: sin acceso y no se pide ningún PDF', () => {
  (globalThis as { __ctx?: InternalContext }).__ctx = context(['company.view']);
  render(<DocumentsPage />);
  expect(screen.getByText('Sin acceso')).toBeInTheDocument();
  expect(screen.queryByTestId('sample-badge')).toBeNull();
  expect(mockFetch).not.toHaveBeenCalled();
});

test('carga la primera muestra, con la insignia y el nombre del servidor', async () => {
  asManager();
  mockFetch.mockResolvedValue(pdf('MUESTRA-nota.pdf'));
  render(<DocumentsPage />);
  expect(screen.getByTestId('sample-badge')).toHaveTextContent('MUESTRA · NO FISCAL');
  const viewer = await screen.findByTitle(/Vista previa: Nota de venta interna/);
  // <object>, no <iframe>: Safari aplica al blob la política `frame-ancestors 'none'` de la página y deja el marco
  // vacío (DOC-PREVIEW-03).
  expect(viewer.tagName).toBe('OBJECT');
  expect(viewer).toHaveAttribute('type', 'application/pdf');
  expect(viewer).toHaveAttribute('data', 'blob:test/1');
  expect(document.querySelector('iframe')).toBeNull();
  expect(screen.getByRole('status')).toHaveTextContent('Vista previa lista.');
  expect(mockFetch.mock.calls[0].slice(0, 2)).toEqual(['sales_note', 3]);
  expect(screen.getByRole('link', { name: 'Descargar PDF' })).toHaveAttribute('download', 'MUESTRA-nota.pdf');
  const open = screen.getByRole('link', { name: 'Abrir PDF' });
  expect(open).toHaveAttribute('target', '_blank');
  expect(open).toHaveAttribute('href', 'blob:test/1');
  expect(screen.getByText(/no se edita aquí/i)).toBeInTheDocument();
  expect(screen.getByText(/Pendiente de decisión del propietario/)).toBeInTheDocument();
  expect(screen.queryByText('SUNAT BETA')).toBeNull();
});

test('elegir otro documento pide ese tipo y revoca el blob anterior (con margen); los fiscales avisan', async () => {
  const user = fakeClock();
  asManager();
  mockFetch.mockImplementation(async (kind: string) => pdf(`MUESTRA-${kind}.pdf`));
  render(<DocumentsPage />);
  await screen.findByTitle(/Nota de venta interna/);
  expect(screen.queryByTestId('fiscal-note')).toBeNull();

  await user.click(screen.getByRole('radio', { name: /Factura electrónica/ }));
  await screen.findByTitle(/Factura electrónica/);
  expect(mockFetch.mock.calls.at(-1)?.[0]).toBe('factura');
  // El visor viejo ya no está, pero WebKit puede seguir leyendo su blob un instante: todavía no se revoca.
  expect(revoked).toEqual([]);
  passDelay();
  expect(revoked).toEqual(['blob:test/1']);
  expect(screen.getByTestId('fiscal-note')).toHaveTextContent(/sin validez tributaria/);
  expect(screen.getByTestId('sample-badge')).toBeInTheDocument();
  expect(screen.getAllByRole('radio')).toHaveLength(7);
});

test('una respuesta lenta anterior no pisa a la elección nueva', async () => {
  asManager();
  const resolvers: Record<string, (v: unknown) => void> = {};
  mockFetch.mockImplementation((kind: string, _c: unknown, signal: AbortSignal) => new Promise((res) => {
    resolvers[kind] = res;
    signal.addEventListener('abort', () => undefined);
  }));
  render(<DocumentsPage />);
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
  const firstSignal = mockFetch.mock.calls[0][2] as AbortSignal;
  await userEvent.click(screen.getByRole('radio', { name: /Boleta de venta/ }));
  expect(firstSignal.aborted).toBe(true);
  await act(async () => { resolvers.boleta(pdf('MUESTRA-boleta.pdf')); });
  await act(async () => { resolvers.sales_note(pdf('MUESTRA-vieja.pdf')); });
  expect(await screen.findByTitle(/Boleta de venta/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Descargar PDF' })).toHaveAttribute('download', 'MUESTRA-boleta.pdf');
  expect(created).toHaveLength(1);
});

test.each([
  ['No tienes permiso para ver la vista previa de documentos de esta empresa.'],
  ['Pediste demasiadas vistas previas seguidas. Espera un momento y vuelve a intentarlo.'],
  ['La petición es demasiado grande.'],
])('el error se muestra, con la insignia y sin visor: %s', async (message) => {
  asManager();
  mockFetch.mockRejectedValue(new DocumentPreviewError(message, 403));
  render(<DocumentsPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  expect(screen.getByTestId('sample-badge')).toBeInTheDocument();
  expect(screen.queryByTitle(/Vista previa:/)).toBeNull();
  expect(screen.queryByRole('link', { name: 'Abrir PDF' })).toBeNull();
});

test('Reintentar vuelve a pedir la misma muestra', async () => {
  asManager();
  mockFetch.mockRejectedValueOnce(new DocumentPreviewError('boom', 500)).mockResolvedValue(pdf());
  render(<DocumentsPage />);
  await userEvent.click(await screen.findByRole('button', { name: 'Reintentar' }));
  expect(await screen.findByTitle(/Vista previa:/)).toBeInTheDocument();
  expect(mockFetch).toHaveBeenCalledTimes(2);
});

test('la región viva anuncia la carga', async () => {
  asManager();
  mockFetch.mockImplementation(() => new Promise(() => undefined));
  render(<DocumentsPage />);
  expect(screen.getByRole('status')).toHaveTextContent('Generando la vista previa…');
});

test('al desmontar se revoca el blob (el temporizador sobrevive al desmontaje)', async () => {
  fakeClock();
  asManager();
  mockFetch.mockResolvedValue(pdf());
  const { unmount } = render(<DocumentsPage />);
  await screen.findByTitle(/Vista previa:/);
  unmount();
  passDelay();
  expect(revoked).toEqual(['blob:test/1']);
});

test('el blob que se está mostrando no se revoca mientras sigue en pantalla', async () => {
  fakeClock();
  asManager();
  mockFetch.mockResolvedValue(pdf());
  render(<DocumentsPage />);
  const viewer = await screen.findByTitle(/Vista previa:/);
  // Mucho después del margen de revocación sigue siendo el mismo objeto-URL, vivo.
  act(() => { jest.advanceTimersByTime(REVOKE_DELAY_MS * 3); });
  expect(viewer).toHaveAttribute('data', 'blob:test/1');
  expect(revoked).toEqual([]);
  expect(screen.getByRole('link', { name: 'Abrir PDF' })).toHaveAttribute('href', 'blob:test/1');
});

test('el visor trae dentro su explicación y el pie dice qué hacer si queda vacío', async () => {
  asManager();
  mockFetch.mockResolvedValue(pdf());
  render(<DocumentsPage />);
  const viewer = await screen.findByTitle(/Vista previa:/);
  expect(viewer).toHaveTextContent(/no pudo mostrar el PDF aquí/);
  expect(screen.getByTestId('pdf-viewer-hint')).toHaveTextContent(/Abrir PDF|Descargar PDF/);
});

describe.each([
  ['sin visor de PDF (Chrome de Android)', { pdf: false, ua: ANDROID_CHROME }, /no muestra PDF dentro de la página/],
  ['navegador antiguo que no declara el visor', { pdf: undefined }, /no muestra PDF dentro de la página/],
  ['iPhone', { pdf: true, ua: IPHONE, touch: 5 }, /iPhone o iPad/],
  ['iPad que se presenta como Mac', { pdf: true, ua: DESKTOP_SAFARI, touch: 5 }, /iPhone o iPad/],
])('%s: sin visor en la página, con explicación y las dos salidas', (_name, nav, reason) => {
  test('nunca dice «Vista previa lista» y no deja un recuadro vacío', async () => {
    browserIs(nav);
    asManager();
    mockFetch.mockResolvedValue(pdf('MUESTRA-nota.pdf'));
    render(<DocumentsPage />);
    const fallback = await screen.findByTestId('pdf-fallback');
    expect(fallback).toHaveTextContent(reason);
    expect(screen.queryByTitle(/Vista previa:/)).toBeNull();
    expect(document.querySelector('object, iframe, embed')).toBeNull();
    expect(screen.getByRole('status')).not.toHaveTextContent(/Vista previa lista/);
    expect(screen.getByRole('status')).toHaveTextContent(/listo para abrir o descargar/);
    expect(screen.getByRole('link', { name: 'Abrir PDF' })).toHaveAttribute('href', 'blob:test/1');
    expect(screen.getByRole('link', { name: 'Descargar PDF' })).toHaveAttribute('download', 'MUESTRA-nota.pdf');
    expect(screen.getByTestId('sample-badge')).toBeInTheDocument();
  });
});

test('sin visor: cambiar de documento sigue revocando el anterior y descartando la respuesta vieja', async () => {
  const user = fakeClock();
  browserIs({ pdf: false, ua: ANDROID_CHROME });
  asManager();
  const resolvers: Record<string, (v: unknown) => void> = {};
  mockFetch.mockImplementation((kind: string) => new Promise((res) => { resolvers[kind] = res; }));
  render(<DocumentsPage />);
  await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
  await act(async () => { resolvers.sales_note(pdf('MUESTRA-nota.pdf')); });
  await screen.findByTestId('pdf-fallback');
  await user.click(screen.getByRole('radio', { name: /Factura electrónica/ }));
  expect((mockFetch.mock.calls[0][2] as AbortSignal).aborted).toBe(true);
  passDelay();
  expect(revoked).toEqual(['blob:test/1']);
  expect(screen.queryByTestId('pdf-fallback')).toBeNull();
  expect(screen.queryByRole('link', { name: 'Abrir PDF' })).toBeNull();
  await act(async () => { resolvers.factura(pdf('MUESTRA-factura.pdf')); });
  expect(await screen.findByRole('link', { name: 'Descargar PDF' })).toHaveAttribute('download', 'MUESTRA-factura.pdf');
});
