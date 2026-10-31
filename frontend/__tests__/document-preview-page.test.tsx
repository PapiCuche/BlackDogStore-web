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
/* eslint-enable @typescript-eslint/no-require-imports */

function context(capabilities: string[], master = false): InternalContext {
  return {
    user: { id: 1, username: 'x' },
    dashboard: { company: { id: 3, slug: 'taller', name: 'Taller' }, access: { is_platform_admin: master, capabilities } },
    selectedCompanyId: 3, selectCompany: () => undefined, reload: () => undefined,
  } as unknown as InternalContext;
}
const asManager = () => { (globalThis as { __ctx?: InternalContext }).__ctx = context(['company.view', 'company.manage']); };

const created: string[] = [];
const revoked: string[] = [];
beforeEach(() => {
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
  expect(await screen.findByTitle(/Vista previa: Nota de venta interna/)).toHaveAttribute('src', 'blob:test/1');
  expect(mockFetch.mock.calls[0].slice(0, 2)).toEqual(['sales_note', 3]);
  expect(screen.getByRole('link', { name: 'Descargar PDF' })).toHaveAttribute('download', 'MUESTRA-nota.pdf');
  const open = screen.getByRole('link', { name: 'Abrir PDF' });
  expect(open).toHaveAttribute('target', '_blank');
  expect(open).toHaveAttribute('href', 'blob:test/1');
  expect(screen.getByText(/no se edita aquí/i)).toBeInTheDocument();
  expect(screen.getByText(/Pendiente de decisión del propietario/)).toBeInTheDocument();
  expect(screen.queryByText('SUNAT BETA')).toBeNull();
});

test('elegir otro documento pide ese tipo y revoca el blob anterior; los fiscales avisan', async () => {
  asManager();
  mockFetch.mockImplementation(async (kind: string) => pdf(`MUESTRA-${kind}.pdf`));
  render(<DocumentsPage />);
  await screen.findByTitle(/Nota de venta interna/);
  expect(screen.queryByTestId('fiscal-note')).toBeNull();

  await userEvent.click(screen.getByRole('radio', { name: /Factura electrónica/ }));
  await screen.findByTitle(/Factura electrónica/);
  expect(mockFetch.mock.calls.at(-1)?.[0]).toBe('factura');
  expect(revoked).toContain('blob:test/1');
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

test('al desmontar se revoca el blob', async () => {
  asManager();
  mockFetch.mockResolvedValue(pdf());
  const { unmount } = render(<DocumentsPage />);
  await screen.findByTitle(/Vista previa:/);
  unmount();
  expect(revoked).toContain('blob:test/1');
});
