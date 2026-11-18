/**
 * DOC-DESIGN-04 · Ajustes › Documentos: las opciones nuevas de la nota de venta.
 *
 * Las mismas reglas que el servidor antes de enviar (título que no nombra un comprobante,
 * renglones, emoji, anchos del logo), y cada cambio se ve en la vista previa ANTES de guardar.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import {
  BLOCKS, DEFAULT_DESIGN, changedFields, designChanges, draftProblems, normaliseMessage, textProblem, titleProblem,
} from '@/app/admin/lib/document-design-api';

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

const LIMITS = {
  document_title: 40, footer_text: 200, thank_you_message: 300, post_sale_message: 400, commercial_message: 300,
  warranty_summary: 300,
};

function settings(overrides: Record<string, unknown> = {}) {
  return {
    design: { ...DEFAULT_DESIGN, block_order: [...BLOCKS] },
    logo_sizes: [{ value: 'small', label: 'Pequeño' }, { value: 'medium', label: 'Mediano' }, { value: 'large', label: 'Grande' }],
    logo_width_mm: { a4: [20, 90], ticket: [20, 72] },
    max_lines: 6,
    limits: LIMITS,
    contact: {
      phone: '054 555 000', email: 'ventas@tienda.example', address: 'Av. Uno 1, Ciudad', website: '',
      warranty_url: 'https://tienda.example/garantia',
    },
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

// ---------------------------------------------------------------------------
// Las reglas, como en el servidor
// ---------------------------------------------------------------------------

test('los valores por defecto son el papel de siempre y no traen texto de ninguna tienda', () => {
  expect(DEFAULT_DESIGN.document_title).toBe('Nota de venta');
  expect(DEFAULT_DESIGN.block_order).toEqual(BLOCKS);
  expect(DEFAULT_DESIGN.tax_breakdown_mode).toBe('detailed');
  expect(DEFAULT_DESIGN.emphasize_total).toBe(true);
  expect(JSON.stringify(DEFAULT_DESIGN).toLowerCase()).not.toMatch(/gracias|manada|black dog/);
});

test('un mensaje conserva sus renglones normalizados', () => {
  expect(normaliseMessage('  Línea   uno \r\n\n\n\n  línea\tdos  \n\n')).toBe('Línea uno\n\nlínea dos');
});

test('los renglones, los emoji y los caracteres que no se imprimen se explican', () => {
  expect(textProblem(Array.from({ length: 7 }, (_, i) => `l${i}`).join('\n'), 400, true)).toMatch(/Máximo 6 renglones/);
  expect(textProblem(Array.from({ length: 6 }, (_, i) => `l${i}`).join('\n'), 400, true)).toBeNull();
  expect(textProblem('Oferta ❤️', 300, true)).toMatch(/emoji/);
  expect(textProblem('Ok 👍🏽', 300, true)).toMatch(/emoji/);
  expect(textProblem('Ω omega', 300, true)).toMatch(/no puede imprimir: Ω/);
  expect(textProblem('¡Ñandú! € 5 · “oferta” … ™', 300, true)).toBeNull();
});

test.each([
  'Factura', 'FACTURA ELECTRÓNICA', 'Fáctura', 'Boleta de venta', 'B.O.L.E.T.A', 'Comprobante de pago',
  'comprobante electrónico', 'Recibo por honorarios', 'RECIBO X HONORARIOS', 'Ticket fiscal',
  'Liquidación de compra', 'Nota de crédito', 'NOTA DE DEBITO', 'F a c t u r a',
])('el título no puede nombrar un documento tributario: %s', (title) => {
  expect(titleProblem(title, 40)).toMatch(/comprobante de pago/);
});

test.each(['Nota de venta', 'Orden de venta', 'Nota de entrega'])('un título corriente pasa: %s', (title) => {
  expect(titleProblem(title, 40)).toBeNull();
});

test('el título vacío y los anchos fuera de rango son problemas del borrador', () => {
  const problems = draftProblems(
    { ...DEFAULT_DESIGN, document_title: '  ', logo_a4_width_mm: 19, logo_ticket_width_mm: 72.5 }, LIMITS);
  expect(problems.document_title).toMatch(/Escribe el título/);
  expect(problems.logo_a4_width_mm).toMatch(/entre 20 y 90/);
  expect(problems.logo_ticket_width_mm).toMatch(/entre 20 y 72/);
  expect(draftProblems({ ...DEFAULT_DESIGN, logo_a4_width_mm: 90, logo_ticket_width_mm: 0 }, LIMITS)).toEqual({});
});

test('el orden de los bloques y los mensajes viajan bien y se dicen en palabras', () => {
  const order = [...BLOCKS];
  [order[0], order[1]] = [order[1], order[0]];
  const draft = { ...DEFAULT_DESIGN, block_order: order, post_sale_message: ' Uno \n\n\n dos ', tax_breakdown_mode: 'hidden' as const };
  expect(changedFields(DEFAULT_DESIGN, draft)).toEqual({
    block_order: order, post_sale_message: 'Uno\n\ndos', tax_breakdown_mode: 'hidden',
  });
  const changes = designChanges(DEFAULT_DESIGN, draft);
  expect(changes.find((c) => c.field === 'block_order')?.to).toMatch(/^Datos de la venta → Cliente/);
  expect(changes.find((c) => c.field === 'tax_breakdown_mode')).toMatchObject({ from: 'Detallado', to: 'Oculto' });
  // El mismo orden en otra lista no es un cambio.
  expect(changedFields(DEFAULT_DESIGN, { ...DEFAULT_DESIGN, block_order: [...BLOCKS] })).toEqual({});
});

// ---------------------------------------------------------------------------
// El panel
// ---------------------------------------------------------------------------

async function loaded() {
  jest.useFakeTimers();
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  render(<DocumentsPage />);
  await screen.findByLabelText('Título de la nota de venta');
  await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));
  return user;
}

test('muestra las opciones nuevas con sus valores de siempre', async () => {
  await loaded();
  expect(screen.getByLabelText('Título de la nota de venta')).toHaveValue('Nota de venta');
  expect(screen.getByLabelText('Ancho del logo en A4 (mm)')).toHaveValue(0);
  for (const name of [/Vendedor/, /Número de pedido/, /Canal de venta/, /Comprobante solicitado/,
    /Productos y unidades/, /Importe en letras/, /Contacto del cliente/, /Dirección de entrega/, /Destacar el total/]) {
    expect(screen.getByRole('checkbox', { name })).toBeChecked();
  }
  expect(within(screen.getByRole('group', { name: 'Desglose referencial de impuestos' }))
    .getByRole('radio', { name: 'Detallado' })).toBeChecked();
  expect(within(screen.getByRole('group', { name: 'Espaciado de la nota de venta' }))
    .getByRole('radio', { name: 'Normal' })).toBeChecked();
  expect(screen.getByLabelText('Mensaje posventa').tagName).toBe('TEXTAREA');
  expect(screen.getByLabelText('Mensaje comercial').tagName).toBe('TEXTAREA');
  expect(screen.getByRole('button', { name: 'Subir «Cliente»' })).toBeDisabled();
});

test('la vista previa dibuja el borrador ANTES de guardar: mensajes, interruptores y desglose', async () => {
  const user = await loaded();
  await user.type(screen.getByLabelText('Mensaje posventa'), 'Conserve la caja{Enter}Cambios en 7 días');
  await user.click(screen.getByRole('checkbox', { name: /Vendedor/ }));
  await user.click(within(screen.getByRole('group', { name: 'Desglose referencial de impuestos' }))
    .getByRole('radio', { name: 'Resumido' }));
  act(() => { jest.advanceTimersByTime(DRAFT_DEBOUNCE_MS); });
  await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2));
  expect(mockPreview.mock.calls[1][3]).toEqual({
    post_sale_message: 'Conserve la caja\nCambios en 7 días', show_seller: false, tax_breakdown_mode: 'summary',
  });
  expect(screen.getByTestId('draft-note')).toHaveTextContent(/sin guardar/);
  expect(mockSave).not.toHaveBeenCalled();
});

test('subir un bloque cambia el orden del borrador', async () => {
  const user = await loaded();
  await user.click(screen.getByRole('button', { name: 'Subir «Datos de la venta»' }));
  const items = within(screen.getByRole('group', { name: 'Orden de los bloques informativos' })).getAllByRole('listitem');
  expect(items[0]).toHaveTextContent('1. Datos de la venta');
  expect(items[1]).toHaveTextContent('2. Cliente');
  act(() => { jest.advanceTimersByTime(DRAFT_DEBOUNCE_MS); });
  await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2));
  expect(mockPreview.mock.calls[1][3].block_order.slice(0, 2)).toEqual(['sale', 'customer']);
});

test('un título de comprobante bloquea la muestra y el guardado, y se explica en su campo', async () => {
  const user = await loaded();
  const title = screen.getByLabelText('Título de la nota de venta');
  await user.clear(title);
  await user.type(title, 'Boleta');
  act(() => { jest.advanceTimersByTime(DRAFT_DEBOUNCE_MS * 2); });
  expect(mockPreview).toHaveBeenCalledTimes(1);
  expect(title).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByText(/no puede nombrar un comprobante de pago/)).toBeInTheDocument();
  expect(screen.getByTestId('draft-blocked')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Guardar cambios' })).toBeDisabled();
});

test('un emoji en un mensaje se explica y bloquea el guardado', async () => {
  const user = await loaded();
  await user.type(screen.getByLabelText('Mensaje comercial'), 'Hola 🐾');
  expect(screen.getByText(/emoji no se pueden imprimir/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Guardar cambios' })).toBeDisabled();
});

test('el resumen de la garantía aparece sólo en su modo y dice qué enlace se imprime', async () => {
  const user = await loaded();
  expect(screen.queryByLabelText('Resumen de la garantía')).toBeNull();
  await user.click(screen.getByRole('radio', { name: 'Resumen y enlace' }));
  expect(screen.getByLabelText('Resumen de la garantía')).toBeInTheDocument();
  expect(screen.getByText(/Enlace que se imprime: https:\/\/tienda.example\/garantia/)).toBeInTheDocument();
});

test('guardar confirma los cambios nuevos en palabras y envía sólo ellos', async () => {
  jest.useRealTimers();
  const user = userEvent.setup();
  mockSave.mockImplementation(async (_c: unknown, change: Record<string, unknown>) =>
    settings({ design: { ...DEFAULT_DESIGN, ...change } }));
  render(<DocumentsPage />);
  const width = await screen.findByLabelText('Ancho del logo en A4 (mm)');
  await user.clear(width);
  await user.type(width, '70');
  await user.click(screen.getByRole('checkbox', { name: /Destacar el total/ }));
  await user.click(screen.getByRole('button', { name: 'Guardar cambios' }));
  const confirm = screen.getByTestId('design-confirm');
  expect(within(confirm).getByText(/Ancho del logo en A4:/).parentElement).toHaveTextContent('según el tamaño → 70 mm');
  expect(within(confirm).getByText(/Destacar el total:/).parentElement).toHaveTextContent('sí → no');
  await user.click(within(confirm).getByRole('button', { name: 'Confirmar y guardar' }));
  expect(mockSave).toHaveBeenCalledWith(3, { logo_a4_width_mm: 70, emphasize_total: false });
});

test('sin poder administrar, las opciones nuevas se ven pero no se editan', async () => {
  mockLoad.mockResolvedValue(settings({ can_manage: false }));
  render(<DocumentsPage />);
  expect(await screen.findByLabelText('Título de la nota de venta')).toBeDisabled();
  expect(screen.getByLabelText('Mensaje posventa')).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: /Vendedor/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Bajar «Cliente»' })).toBeDisabled();
});
