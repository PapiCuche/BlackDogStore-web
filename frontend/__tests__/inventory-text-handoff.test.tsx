import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { InventoryTextPreview } from '@/app/admin/components/InventoryTextPreview';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-TEXT-04 · del borrador revisado al importador OFICIAL de ofertas, sin descargar ni volver a subir.
 * El borrador abre el mismo importador con su libro ya elegido y lanza SU vista previa; aplicar sigue siendo
 * su botón, con sus confirmaciones. Nada se aplica solo.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  ({
    ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers(headers),
    blob: async () => new Blob(['PK-draft'], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }),
  }) as unknown as Response;

const SUPPLIERS = { count: 1, page: 1, page_size: 25, results: [{ id: 3, name: 'Mayorista', is_active: true }] };
const MATCHES = { matches: {
  1: { status: 'exact', reason: '', options: [{ product_id: 7, name: 'iPhone 15 128 GB Negro', condition_line: 'new', options: [] }] },
} };
const DRAFT = {
  dry_run: true, rows_total: 1, rows_create: 1, rows_update: 0, rows_no_change: 0, rows_error: 0, cost_column_ignored: false,
  summary: { new: 1, updated: 0, unchanged: 0, sold_out: 0, errors: 0, skipped: 0, price_changes: 0, mappings: 0 },
  rows: [{ key: '1', row_number: 2, action: 'create', errors: [], warnings: [], data: {} }],
};
const OFFICIAL = {
  id: 77, status: 'previewed', rows_total: 1, rows_create: 1, rows_update: 0, rows_no_change: 0, rows_error: 0,
  cost_column_ignored: false, already_applied: null,
  summary: { new: 1, updated: 0, unchanged: 0, sold_out: 0, errors: 0, skipped: 0, price_changes: 0, mappings: 0 },
  rows: [{ row_number: 2, action: 'create', errors: [], warnings: [], data: {
    supplier: 'Mayorista', product_name: 'iPhone 15 128 GB Negro', commercial_price: '2999', confirmed_quantity: null,
    valid_from: '2026-10-23', valid_until: '2026-10-29' } }],
  ai: { requested: false, allowed: false, reason: null, used: false },
};

function route() {
  send.mockImplementation(async (url) => {
    const path = String(url);
    if (path.includes('/admin/suppliers/')) return reply(200, SUPPLIERS);
    if (path.includes('/text-preview/match/')) return reply(200, MATCHES);
    if (path.includes('/text-preview/offer-draft/file/')) {
      return reply(200, null, { 'Content-Disposition': 'attachment; filename="borrador-ofertas-whatsapp.xlsx"' });
    }
    if (path.includes('/text-preview/offer-draft/')) return reply(200, DRAFT);
    if (path.includes('/supplier-offers/import/preview/')) return reply(200, OFFICIAL);
    if (path.includes('/supplier-offers/import/77/apply/')) {
      return reply(200, { status: 'applied', created: 1, updated: 0, unchanged: 0, sold_out: 0, prices_changed: 0,
        prices_kept: 0, mappings: 0, price_decision: null });
    }
    return reply(404, {});
  });
}

const calls = (part: string) => send.mock.calls.filter(([url]) => String(url).includes(part));

beforeEach(() => send.mockReset());

async function draftReady({ manage = true, complete = false } = {}) {
  route();
  render(<InventoryTextPreview canSeeCosts canManageProducts={manage} canReceiveUnits companyId={5} />);
  fireEvent.change(screen.getByLabelText('Línea declarada'), { target: { value: 'new' } });
  fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: 'IPH 15 128 2300 negro' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Analizar/ })); });
  if (!manage) return;
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' })); });
  fireEvent.change(screen.getByLabelText('Precio comercial del registro 1'), { target: { value: '2999' } });
  fireEvent.change(screen.getByLabelText('Proveedor del borrador'), { target: { value: '3' } });
  if (complete) fireEvent.click(screen.getByLabelText(/lista completa/i));
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
}

async function handOff() {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Continuar al importador oficial' })); });
}

it('abre el importador oficial con el libro del borrador y lanza SU vista previa, sin aplicar nada', async () => {
  await draftReady();
  await handOff();
  const [fileUrl, fileInit] = calls('/text-preview/offer-draft/file/')[0];
  expect(String(fileUrl)).toContain('?company=5');
  // The very body that was rehearsed.
  expect(JSON.parse(String(fileInit?.body))).toEqual(JSON.parse(String(calls('/text-preview/offer-draft/')[0][1]?.body)));
  const [previewUrl, previewInit] = calls('/supplier-offers/import/preview/')[0];
  expect(String(previewUrl)).toContain('?company=5');
  const form = previewInit?.body as FormData;
  expect(form).toBeInstanceOf(FormData);
  expect((form.get('file') as File).name).toBe('borrador-ofertas-whatsapp.xlsx');
  expect(form.get('complete_snapshot')).toBeNull();
  expect(form.get('use_ai')).toBeNull();
  expect(screen.getByTestId('supplier-import-preview')).toBeInTheDocument();
  expect(calls('/apply/')).toHaveLength(0);
});

it('aplicar es el botón del importador oficial, y sólo cuando la persona lo pulsa', async () => {
  await draftReady();
  await handOff();
  const official = screen.getByRole('region', { name: 'Importador oficial de ofertas' });
  await act(async () => { fireEvent.click(within(official).getByRole('button', { name: 'Aplicar cambios' })); });
  expect(calls('/supplier-offers/import/77/apply/')).toHaveLength(1);
  expect(String(calls('/apply/')[0][0])).toContain('?company=5');
  expect(within(official).getByRole('status')).toHaveTextContent(/1/);
});

it('una lista completa llega al importador oficial como lista completa de ese proveedor', async () => {
  await draftReady({ complete: true });
  await handOff();
  const form = calls('/supplier-offers/import/preview/')[0][1]?.body as FormData;
  expect([form.get('complete_snapshot'), form.get('supplier_id')]).toEqual(['true', '3']);
});

it('corregir el borrador, «Seguir corrigiendo» o «Cancelar» cierran el importador sin aplicar', async () => {
  await draftReady();
  await handOff();
  fireEvent.change(screen.getByLabelText('Precio comercial del registro 1'), { target: { value: '3100' } });
  expect(screen.queryByRole('region', { name: 'Importador oficial de ofertas' })).toBeNull();

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
  await handOff();
  fireEvent.click(screen.getByRole('button', { name: 'Seguir corrigiendo' }));
  expect(screen.queryByRole('region', { name: 'Importador oficial de ofertas' })).toBeNull();

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
  await handOff();
  fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
  expect(screen.queryByRole('region', { name: 'Importador oficial de ofertas' })).toBeNull();
  expect(calls('/apply/')).toHaveLength(0);
});

it('si el libro no se puede generar, no se abre el importador', async () => {
  await draftReady();
  send.mockImplementationOnce(async () => reply(404, { detail: 'No encontrado.' }));
  await handOff();
  expect(screen.queryByRole('region', { name: 'Importador oficial de ofertas' })).toBeNull();
  expect(screen.getByText('No encontrado.')).toBeInTheDocument();
});

it('sin permiso para gestionar productos no hay continuación', async () => {
  await draftReady({ manage: false });
  expect(screen.queryByRole('button', { name: 'Continuar al importador oficial' })).toBeNull();
});

it('revisión: un doble clic en «Continuar» prepara el archivo una sola vez', async () => {
  await draftReady();
  const button = screen.getByRole('button', { name: 'Continuar al importador oficial' });
  await act(async () => { fireEvent.click(button); fireEvent.click(button); });
  expect(calls('/text-preview/offer-draft/file/')).toHaveLength(1);
  expect(calls('/supplier-offers/import/preview/')).toHaveLength(1);
});

it('revisión: dentro del traspaso no se elige otro archivo; el foco va al importador', async () => {
  await draftReady();
  await handOff();
  const official = screen.getByRole('region', { name: 'Importador oficial de ofertas' });
  expect(within(official).queryByLabelText(/Archivo de ofertas/)).toBeNull();
  expect(within(official).getByText(/Del borrador de WhatsApp: borrador-ofertas-whatsapp\.xlsx/)).toBeInTheDocument();
  expect(official).toHaveFocus();
});

it('revisión: tras aplicar, el importador ya no presenta el archivo del borrador como pendiente', async () => {
  await draftReady();
  await handOff();
  const official = screen.getByRole('region', { name: 'Importador oficial de ofertas' });
  await act(async () => { fireEvent.click(within(official).getByRole('button', { name: 'Aplicar cambios' })); });
  expect(within(official).queryByText(/Del borrador de WhatsApp:/)).toBeNull();
  expect(within(official).getByText(/El borrador ya se usó/)).toBeInTheDocument();
});
