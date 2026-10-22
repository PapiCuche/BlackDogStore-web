import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { SupplierOffersManager } from '@/app/admin/components/SupplierOffersManager';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-SNAPSHOT-01 · lista completa de un proveedor: se elige el proveedor, la vista
 * previa dice qué ofertas vigentes ausentes se desactivarán (y cuáles tienen ventas,
 * que siguen su curso), y aplicar exige marcar que están todos los mensajes y
 * confirmar el número exacto. El modo parcial no cambia.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (body: unknown, status = 200) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers() }) as Response;

let previewBody: Record<string, unknown>;
let posts: Array<{ url: string; body: unknown }>;

const SNAPSHOT = {
  supplier_id: 5, supplier_name: 'Mayorista Lima', deactivate_count: 2, with_commitments: 1,
  absent: [
    { offer_id: 31, product_id: 44, product_name: 'Cable USB-C', committed: 0, available: 5 },
    { offer_id: 32, product_id: 45, product_name: 'Funda MagSafe', committed: 2, available: 1 },
  ],
};

beforeEach(() => {
  posts = [];
  previewBody = {
    id: 61, status: 'previewed', rows_total: 1, rows_create: 0, rows_update: 1, rows_no_change: 0, rows_error: 0,
    cost_column_ignored: false, summary: { new: 0, updated: 1, unchanged: 0, sold_out: 0, errors: 0, skipped: 0 },
    rows: [], snapshot: SNAPSHOT,
  };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if ((init.method ?? 'GET').toUpperCase() !== 'GET') {
      posts.push({ url, body: init.body instanceof FormData ? Object.fromEntries(init.body.entries()) : JSON.parse(String(init.body)) });
      if (url.includes('/import/preview/')) return reply(previewBody);
      if (url.includes('/import/61/apply/')) return reply({ status: 'applied', created: 0, updated: 1, unchanged: 0, deactivated: 2 });
      return reply({}, 404);
    }
    if (url.includes('/admin/suppliers/')) return reply({ count: 1, page: 1, page_size: 25, results: [{ id: 5, name: 'Mayorista Lima' }] });
    return reply({ count: 0, page: 1, page_size: 25, results: [] });
  });
});

async function previewComplete() {
  render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
  await screen.findByText('No hay ofertas que coincidan.');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Es la lista completa de un proveedor' }));
  fireEvent.change(screen.getByLabelText('Proveedor de la lista completa'), { target: { value: '5' } });
  fireEvent.change(screen.getByLabelText('Archivo de ofertas (.xlsx)'), { target: { files: [new File(['x'], 'ofertas.xlsx')] } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
  return screen.findByTestId('snapshot-absent');
}

it('la vista previa pide la lista completa del proveedor elegido', async () => {
  await previewComplete();
  expect(posts[0].body).toMatchObject({ complete_snapshot: 'true', supplier_id: '5' });
});

it('muestra las ausentes que se desactivarán y resalta las que tienen ventas', async () => {
  const absent = await previewComplete();
  expect(absent).toHaveTextContent(/se desactivarán 2 ofertas vigentes ausentes/i);
  expect(within(absent).getByText('Funda MagSafe').closest('li')).toHaveTextContent('2 vendidas: siguen su curso');
});

it('aplicar exige las dos confirmaciones y manda el número exacto', async () => {
  await previewComplete();
  const apply = screen.getByRole('button', { name: /Aplicar/ });
  expect(apply).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'La lista incluye todos los mensajes y archivos del proveedor' }));
  expect(apply).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Confirmo desactivar 2 ofertas ausentes' }));
  expect(apply).not.toBeDisabled();
  await act(async () => { fireEvent.click(apply); });
  const sent = posts.find(p => p.url.includes('/apply/'))!.body;
  expect(sent).toMatchObject({ all_messages_included: true, confirm_deactivations: 2 });
});

it('sin marcar la lista completa la vista previa es la parcial de siempre', async () => {
  delete previewBody.snapshot;
  render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
  await screen.findByText('No hay ofertas que coincidan.');
  fireEvent.change(screen.getByLabelText('Archivo de ofertas (.xlsx)'), { target: { files: [new File(['x'], 'ofertas.xlsx')] } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
  expect(posts[0].body).not.toHaveProperty('complete_snapshot');
  expect(screen.queryByTestId('snapshot-absent')).toBeNull();
});

it('una lista que parece incompleta lo dice y no se puede aplicar', async () => {
  previewBody = { ...previewBody, snapshot: { ...SNAPSHOT, looks_truncated: true } };
  await previewComplete();
  expect(screen.getByRole('alert')).toHaveTextContent('parece incompleta');
  fireEvent.click(screen.getByRole('checkbox', { name: 'La lista incluye todos los mensajes y archivos del proveedor' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Confirmo desactivar 2 ofertas ausentes' }));
  expect(screen.getByRole('button', { name: /Aplicar/ })).toBeDisabled();
});
