import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import { DEFAULT_DESIGN, DocumentDesignFieldError } from '@/app/admin/lib/document-design-api';

const mockPreview = jest.fn();
const mockLoad = jest.fn();
const mockSave = jest.fn();
jest.mock('@/app/admin/lib/document-preview-api', () => ({
  ...jest.requireActual('@/app/admin/lib/document-preview-api'),
  fetchDocumentPreview: (...a: unknown[]) => mockPreview(...a),
}));
jest.mock('@/app/admin/lib/document-design-api', () => ({
  ...jest.requireActual('@/app/admin/lib/document-design-api'),
  fetchDocumentDesign: (...a: unknown[]) => mockLoad(...a),
  updateDocumentDesign: (...a: unknown[]) => mockSave(...a),
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
const { DRAFT_DEBOUNCE_MS } = require('@/app/admin/settings/documents/DocumentPreview');
/* eslint-enable @typescript-eslint/no-require-imports */

function settings(overrides: Record<string, unknown> = {}) {
  return {
    design: { ...DEFAULT_DESIGN },
    logo_sizes: [{ value: 'small', label: 'Pequeño' }, { value: 'medium', label: 'Mediano' }, { value: 'large', label: 'Grande' }],
    limits: { footer_text: 200, thank_you_message: 120 },
    contact: { phone: '054 555 000', email: 'ventas@tienda.example', address: 'Av. Uno 1, Ciudad', website: '' },
    can_manage: true,
    ...overrides,
  };
}

beforeEach(() => {
  (globalThis as { __ctx?: InternalContext }).__ctx = {
    user: { id: 1, username: 'x' },
    dashboard: { company: { id: 3, slug: 'taller', name: 'Taller' }, access: { is_platform_admin: false, capabilities: ['company.view', 'company.manage'] } },
    selectedCompanyId: 3, selectCompany: () => undefined, reload: () => undefined,
  } as unknown as InternalContext;
  Object.defineProperty(window.navigator, 'pdfViewerEnabled', { configurable: true, get: () => true });
  mockPreview.mockReset().mockResolvedValue({ blob: new Blob(['%PDF'], { type: 'application/pdf' }), fileName: 'MUESTRA-a.pdf' });
  mockLoad.mockReset().mockResolvedValue(settings());
  mockSave.mockReset();
  let n = 0;
  URL.createObjectURL = jest.fn(() => `blob:test/${++n}`);
  URL.revokeObjectURL = jest.fn();
});
afterEach(() => { jest.useRealTimers(); });

const thanks = () => screen.getByLabelText('Mensaje final');

test('carga el diseño de la empresa; el mensaje final está vacío y la muestra no lleva borrador', async () => {
  render(<DocumentsPage />);
  expect(await screen.findByRole('heading', { name: 'Diseño de los documentos internos' })).toBeInTheDocument();
  expect(mockLoad).toHaveBeenCalledWith(3);
  expect(thanks()).toHaveValue('');
  expect(screen.getByRole('radio', { name: 'Mediano' })).toBeChecked();
  expect(screen.getByText('054 555 000')).toBeInTheDocument();
  expect(screen.getByText(/Sin dato en los ajustes de la empresa/)).toBeInTheDocument();     // web vacía
  await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));
  expect(mockPreview.mock.calls[0][3]).toBeUndefined();
  expect(screen.getByRole('button', { name: 'Guardar cambios' })).toBeDisabled();
});

test('teclear pide UNA muestra con el borrador tras la espera, cancelando la anterior', async () => {
  jest.useFakeTimers();
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  render(<DocumentsPage />);
  await screen.findByLabelText('Mensaje final');
  await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));

  await user.type(thanks(), 'Hola');
  expect(mockPreview).toHaveBeenCalledTimes(1);             // nada mientras se teclea
  act(() => { jest.advanceTimersByTime(DRAFT_DEBOUNCE_MS); });
  await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2));
  expect(mockPreview.mock.calls[1][0]).toBe('sales_note');
  expect(mockPreview.mock.calls[1][3]).toEqual({ thank_you_message: 'Hola' });
  expect(screen.getByTestId('draft-note')).toHaveTextContent(/sin guardar/);
  expect(mockSave).not.toHaveBeenCalled();                  // la muestra nunca guarda
});

test('un texto que el servidor rechazaría bloquea la muestra y el guardado, y se explica', async () => {
  jest.useFakeTimers();
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  render(<DocumentsPage />);
  await screen.findByLabelText('Pie de página');
  await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));

  await user.type(screen.getByLabelText('Pie de página'), 'Pie <b>');
  act(() => { jest.advanceTimersByTime(DRAFT_DEBOUNCE_MS * 2); });
  expect(mockPreview).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId('draft-blocked')).toHaveTextContent(/Corrige los campos/);
  expect(screen.getByLabelText('Pie de página')).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByText(/sin los signos < ni >/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Guardar cambios' })).toBeDisabled();
});

test('guardar muestra primero qué cambia y sólo confirma envía el cambio', async () => {
  const user = userEvent.setup();
  mockSave.mockImplementation(async (_c: unknown, change: Record<string, unknown>) =>
    settings({ design: { ...DEFAULT_DESIGN, ...change } }));
  render(<DocumentsPage />);
  await user.type(await screen.findByLabelText('Mensaje final'), 'Gracias por su compra.');
  await user.click(screen.getByRole('radio', { name: 'Grande' }));
  await user.click(screen.getByRole('button', { name: 'Guardar cambios' }));

  const confirm = screen.getByTestId('design-confirm');
  expect(within(confirm).getByText(/Tamaño del logo:/).parentElement).toHaveTextContent('Mediano → Grande');
  expect(within(confirm).getByText(/Mensaje final:/).parentElement).toHaveTextContent('(vacío) → «Gracias por su compra.»');
  expect(within(confirm).getByRole('heading', { name: '¿Guardar estos cambios?' })).toHaveFocus();
  expect(mockSave).not.toHaveBeenCalled();

  await user.click(within(confirm).getByRole('button', { name: 'Confirmar y guardar' }));
  expect(mockSave).toHaveBeenCalledWith(3, { logo_size: 'large', thank_you_message: 'Gracias por su compra.' });
  expect(await screen.findByText(/^Guardado\./)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Guardar cambios' })).toBeDisabled();       // nada pendiente
});

test('«Volver» no guarda y «Descartar cambios» vuelve a lo guardado', async () => {
  const user = userEvent.setup();
  render(<DocumentsPage />);
  await user.click(await screen.findByRole('checkbox', { name: /Teléfono/ }));
  await user.click(screen.getByRole('button', { name: 'Guardar cambios' }));
  await user.click(screen.getByRole('button', { name: 'Volver' }));
  expect(mockSave).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Descartar cambios' }));
  expect(screen.getByRole('checkbox', { name: /Teléfono/ })).toBeChecked();
  expect(screen.getByText('Sin cambios')).toBeInTheDocument();
});

test('un 400 del servidor se muestra en su campo y no se da por guardado', async () => {
  const user = userEvent.setup();
  mockSave.mockRejectedValue(new DocumentDesignFieldError('Revisa los campos marcados. No se guardó nada.',
    { thank_you_message: ['Máximo 120 caracteres (tiene 121).'] }));
  render(<DocumentsPage />);
  await user.type(await screen.findByLabelText('Mensaje final'), 'Hola');
  await user.click(screen.getByRole('button', { name: 'Guardar cambios' }));
  await user.click(screen.getByRole('button', { name: 'Confirmar y guardar' }));
  expect(await screen.findByText('Máximo 120 caracteres (tiene 121).')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('No se guardó nada');
  expect(screen.queryByText(/^Guardado\./)).toBeNull();
  expect(thanks()).toHaveAttribute('aria-invalid', 'true');
});

test('sin poder administrar, el diseño se ve pero no se edita', async () => {
  mockLoad.mockResolvedValue(settings({ can_manage: false }));
  render(<DocumentsPage />);
  expect(await screen.findByLabelText('Mensaje final')).toBeDisabled();
  expect(screen.getByRole('radio', { name: 'Grande' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Guardar cambios' })).toBeNull();
  expect(screen.getByText(/Sólo quien administra la empresa/)).toBeInTheDocument();
});

test('si el diseño no carga, la vista previa sigue con lo guardado y se avisa', async () => {
  mockLoad.mockRejectedValue(new Error('No se pudo cargar el diseño de los documentos.'));
  render(<DocumentsPage />);
  expect(await screen.findByText(/No se pudo cargar el diseño de los documentos/)).toBeInTheDocument();
  await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));
  expect(mockPreview.mock.calls[0][3]).toBeUndefined();
});

test('en un documento fiscal se dice que el diseño no se le aplica', async () => {
  const user = userEvent.setup();
  render(<DocumentsPage />);
  await screen.findByLabelText('Mensaje final');
  await user.click(screen.getByRole('radio', { name: /Factura electrónica/ }));
  expect(screen.getByTestId('fiscal-note')).toHaveTextContent(/no se aplica a boletas ni facturas/);
});
