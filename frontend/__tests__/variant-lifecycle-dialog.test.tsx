import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { VariantLifecycleDialog, stockSentence } from '@/app/admin/components/VariantLifecycleDialog';
import { ArchivedBadge } from '@/app/admin/components/ModelPalettePanel';
import type { VariantDeletionCheck } from '@/app/admin/lib/internal-api';
import { RESOLVER_STATUS_LABELS } from '@/app/admin/lib/palette';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * VARIANT-SAFE-01 — el diálogo de «Archivar / Desarchivar / Eliminar» de una variante.
 *
 * Con la API simulada (la regla la prueban Django: bloqueos, permisos, carrera):
 *   - primero lee `deletion-check` y explica cada bloqueo con su etiqueta y cuántos hay;
 *   - eliminar exige motivo y el nombre exacto; sin «Eliminar variantes» no se ofrece; con historial ofrece archivar;
 *   - archivar con stock se explica con las cifras y no se ofrece el botón;
 *   - un rechazo del servidor se muestra y se vuelve a leer.
 *
 * Nombres inventados: nada aquí es de una tienda concreta.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const CHECK: VariantDeletionCheck = {
  product_id: 51, name: 'Equipo Z 128 Azul', is_variant: true, is_active: true, archived: false, archived_at: null,
  archive_reason: '', deletable: true, archivable: true, blockers: [],
  stock: { available_units: 0, reserved_units: 0, in_transit_units: 0, stock: 0 },
  can_archive: true, can_delete: true,
};

type Call = { method: string; path: string; body: Record<string, unknown> };
let calls: Call[];

function serve(check: Partial<VariantDeletionCheck>, writes: Record<string, () => Response> = {}) {
  calls = [];
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input).replace(/^.*\/api/, '').replace(/\?.*$/, '');
    const method = (init.method ?? 'GET').toUpperCase();
    calls.push({ method, path, body: init.body ? JSON.parse(String(init.body)) : {} });
    if (method === 'GET' && path === '/admin/products/51/deletion-check/') return reply({ ...CHECK, ...check });
    const key = `${method} ${path}`;
    if (writes[key]) return writes[key]();
    return reply({}, 404);
  });
}

const done = jest.fn();
const cancel = jest.fn();

function open(mode: 'archive' | 'unarchive' | 'delete') {
  return render(
    <VariantLifecycleDialog productId={51} name="Equipo Z 128 Azul" mode={mode} companyId={null} onDone={done}
      onCancel={cancel} />,
  );
}

beforeEach(() => { done.mockReset(); cancel.mockReset(); send.mockReset(); });

test('deleting a variant without history asks for a reason and the exact name', async () => {
  serve({}, { 'DELETE /admin/products/51/': () => reply({ deleted: true, product_id: 51, name: 'Equipo Z 128 Azul' }) });
  open('delete');
  const button = await screen.findByRole('button', { name: 'Eliminar definitivamente' });
  expect(screen.getByRole('dialog', { name: /Eliminar variante/ })).toBeInTheDocument();
  expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Motivo/), { target: { value: 'Creada por error' } });
  expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Escribe «Equipo Z 128 Azul» para confirmar/), { target: { value: 'Equipo Z 128' } });
  expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Escribe «Equipo Z 128 Azul» para confirmar/), { target: { value: 'Equipo Z 128 Azul' } });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await waitFor(() => expect(done).toHaveBeenCalledWith(expect.stringContaining('se eliminó'), 'delete'));
  const write = calls.find((c) => c.method === 'DELETE');
  expect(write?.body).toEqual({ reason: 'Creada por error', confirm_name: 'Equipo Z 128 Azul' });
});

test('a variant with history explains every blocker and offers archiving instead', async () => {
  serve({
    deletable: false,
    blockers: [{ code: 'order_items', count: 3, label: 'Líneas de pedidos o ventas' },
      { code: 'cart_items', count: 1, label: 'Carritos de clientes que la contienen' }],
  });
  open('delete');
  const list = await screen.findByRole('list', { name: 'Lo que impide eliminarla' });
  expect(list).toHaveTextContent('Líneas de pedidos o ventas: 3');
  expect(list).toHaveTextContent('Carritos de clientes que la contienen: 1');
  expect(screen.queryByRole('button', { name: 'Eliminar definitivamente' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Archivar en su lugar' }));
  expect(await screen.findByRole('button', { name: 'Archivar' })).toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: /Archivar variante/ })).toBeInTheDocument();
});

test('without the delete permission nothing is deleted from here', async () => {
  serve({ can_delete: false });
  open('delete');
  expect(await screen.findByText(/necesita el permiso «Eliminar variantes»/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Eliminar definitivamente' })).not.toBeInTheDocument();
});

test('archiving with stock says what is left and offers no button', async () => {
  serve({ archivable: false, stock: { available_units: 2, reserved_units: 1, in_transit_units: 0, stock: 0 } });
  open('archive');
  expect(await screen.findByText(/2 equipos disponibles, 1 equipo apartado/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Archivar' })).not.toBeInTheDocument();
});

test('archiving sends the reason; a refusal of the server is shown and read again', async () => {
  serve({}, {
    'POST /admin/products/51/archive/': () => reply({ detail: 'Todavía tiene stock.', code: 'stock_available' }, 409),
  });
  open('archive');
  const button = await screen.findByRole('button', { name: 'Archivar' });
  fireEvent.change(screen.getByLabelText(/Motivo/), { target: { value: 'Ya no se vende' } });
  fireEvent.click(button);
  expect(await screen.findByRole('alert')).toHaveTextContent('Todavía tiene stock.');
  await waitFor(() => expect(calls.filter((c) => c.path === '/admin/products/51/deletion-check/')).toHaveLength(2));
  expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ reason: 'Ya no se vende' });
  expect(done).not.toHaveBeenCalled();
});

test('unarchiving says it comes back inactive', async () => {
  serve({ archived: true, is_active: false, archive_reason: 'Ya no se vende' }, {
    'POST /admin/products/51/unarchive/': () => reply({ ...CHECK, archived: false, is_active: false }),
  });
  open('unarchive');
  fireEvent.change(await screen.findByLabelText(/Motivo/), { target: { value: 'Vuelve a venderse' } });
  fireEvent.click(screen.getByRole('button', { name: 'Desarchivar' }));
  await waitFor(() => expect(done).toHaveBeenCalledWith(expect.stringContaining('Sigue inactiva'), 'unarchive'));
});

test('the archived state reads «Archivada» in the matrix and the resolver', () => {
  render(<ArchivedBadge />);
  expect(screen.getByText('Archivada')).toBeInTheDocument();
  expect(RESOLVER_STATUS_LABELS.variante_archivada).toBe('Archivada');
  expect(stockSentence({ available_units: 0, reserved_units: 0, in_transit_units: 1, stock: 4 }))
    .toBe('1 equipo en tránsito, 4 unidades en stock');
  expect(stockSentence({ available_units: 0, reserved_units: 0, in_transit_units: 0, stock: 0, in_transit_quantity: 3 }))
    .toBe('3 unidades en traslado');
});
