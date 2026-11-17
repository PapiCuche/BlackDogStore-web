/**
 * INV-INTAKE-GUARD-01 (revisión independiente) — «Registrar equipo» ofrece el estado según la línea de la variante:
 * una seminueva empieza en «Usado» y no ofrece «Nuevo»; una variante nueva de un modelo no ofrece «Usado». El
 * servidor rechaza igual lo que contradiga la línea.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';

import { StockUnitForm } from '@/app/admin/components/StockUnitForm';
import { forgetBranch } from '@/app/admin/lib/last-branch';
import { fetchWithAuth } from '@/app/lib/auth';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Product = { id: number; name: string; condition_line: 'new' | 'used'; product_model_id: number | null };
const PRODUCTS: Product[] = [
  { id: 20, name: 'iPhone 13 Seminuevo', condition_line: 'used', product_model_id: 3 },
  { id: 21, name: 'iPhone 13 Nuevo', condition_line: 'new', product_model_id: 3 },
  { id: 22, name: 'Equipo suelto', condition_line: 'new', product_model_id: null },
];
const CONDITIONS = [
  { value: 'new', label: 'Nuevo' }, { value: 'open_box', label: 'Caja abierta' },
  { value: 'refurbished', label: 'Reacondicionado' }, { value: 'used', label: 'Usado' },
];

const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
const asAdmin = (p: Product) => ({
  id: p.id, name: p.name, slug: `p-${p.id}`, description: '', price: '1.00', inventory: 0, image_url: '',
  category_id: null, category_name: null, is_active: true, is_published_online: true, is_serialized: true,
  requires_imei: false, can_change_tracking: false, primary_code: '', created_at: '', updated_at: '',
  condition_line: p.condition_line, product_model_id: p.product_model_id,
});

let posted: Record<string, unknown> | null;
beforeEach(() => {
  forgetBranch();
  posted = null;
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    if (method === 'GET' && String(input).includes('/admin/products/')) {
      const term = (new URL(String(input), 'http://localhost').searchParams.get('search') ?? '').toLowerCase();
      const hits = PRODUCTS.filter((p) => p.name.toLowerCase().includes(term)).map(asAdmin);
      return reply(200, { count: hits.length, page: 1, page_size: 8, results: hits });
    }
    if (method === 'POST') posted = JSON.parse(String(init.body));
    return reply(201, { results: [{ id: 1 }] });
  });
});

async function pick(name: string) {
  render(
    <StockUnitForm branch={1} branches={[{ id: 1, name: 'Centro' }]} canChangeTracking conditions={CONDITIONS}
      onSaved={jest.fn()} onClose={jest.fn()} />,
  );
  const box = screen.getByRole('combobox', { name: 'Producto / modelo' });
  fireEvent.change(box, { target: { value: name } });
  await act(async () => { fireEvent.keyDown(box, { key: 'Enter' }); });
  await screen.findByRole('button', { name: 'Cambiar producto' });
  const select = screen.getByLabelText('Condición') as HTMLSelectElement;
  return { select, labels: Array.from(select.options).map((o) => o.textContent) };
}

test('una variante seminueva empieza en «Usado» y no ofrece «Nuevo»', async () => {
  const { select, labels } = await pick('iPhone 13 Seminuevo');
  expect(select.value).toBe('used');
  expect(labels).not.toContain('Nuevo');
});

test('una variante nueva de un modelo empieza en «Nuevo» y no ofrece «Usado»', async () => {
  const { select, labels } = await pick('iPhone 13 Nuevo');
  expect(select.value).toBe('new');
  expect(labels).not.toContain('Usado');
});

test('un producto fuera del catálogo de modelos ofrece los cuatro estados', async () => {
  const { labels } = await pick('Equipo suelto');
  expect(labels).toEqual(['Nuevo', 'Caja abierta', 'Reacondicionado', 'Usado']);
});

test('se envía el estado de la línea', async () => {
  await pick('iPhone 13 Seminuevo');
  fireEvent.change(screen.getByLabelText(/^Número de serie/), { target: { value: 'SEMI0001' } });
  fireEvent.change(screen.getByLabelText(/^Motivo o documento de ingreso/), { target: { value: 'Compra' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' })); });
  expect((posted?.units as { condition: string }[])[0].condition).toBe('used');
});
