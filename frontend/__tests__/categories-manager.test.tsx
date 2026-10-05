import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CategoriesManager } from '@/app/admin/components/CategoriesManager';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * STOREFRONT-CATEGORIES · Productos › Categorías.
 *
 * Aquí la tienda decide qué familias ofrece, cuáles ilustra en la portada y en
 * qué orden. Cada cambio se guarda en el servidor; la lista se vuelve a pintar
 * con lo que el servidor devuelve.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Row = { id: number; name: string; slug: string; image_url: string; is_active: boolean; show_on_home: boolean; home_order: number; product_count: number };
let rows: Row[];
let writes: { method: string; path: string; body: Record<string, unknown> }[];
let failWith: { status: number; body: unknown } | null;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  rows = [
    { id: 1, name: 'Teléfonos', slug: 'telefonos', image_url: '', is_active: true, show_on_home: true, home_order: 1, product_count: 12 },
    { id: 2, name: 'Laptops', slug: 'laptops', image_url: '', is_active: true, show_on_home: false, home_order: 2, product_count: 4 },
    { id: 3, name: 'Accesorios', slug: 'accesorios', image_url: '', is_active: false, show_on_home: true, home_order: 3, product_count: 0 },
  ];
  writes = [];
  failWith = null;
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const path = String(input).replace(/^.*\/admin\/categories/, '');
    if (method === 'GET') return reply(200, [...rows].sort((a, b) => a.home_order - b.home_order));
    const body = JSON.parse(String(init.body));
    writes.push({ method, path, body });
    if (failWith) return reply(failWith.status, failWith.body);
    if (method === 'POST') {
      const created = { id: 9, name: body.name, slug: 'tablets', image_url: '', is_active: true, show_on_home: true, home_order: 4, product_count: 0 };
      rows.push(created);
      return reply(201, created);
    }
    const id = Number(path.replace(/\D/g, ''));
    rows = rows.map((row) => (row.id === id ? { ...row, ...body } : row));
    return reply(200, rows.find((row) => row.id === id));
  });
});

const row = async (name: string) => (await screen.findByText(name)).closest('li') as HTMLElement;

test('every category is listed in the shop\'s order with how it is shown', async () => {
  render(<CategoriesManager canManage />);

  const laptops = await row('Laptops');
  expect(within(laptops).getByText('4 productos')).toBeInTheDocument();
  expect(within(laptops).getByLabelText('Mostrar «Laptops» en la portada')).not.toBeChecked();
  expect(within(laptops).getByLabelText('«Laptops» activa')).toBeChecked();
  expect(within(await row('Accesorios')).getByLabelText('«Accesorios» activa')).not.toBeChecked();
  const names = screen.getAllByRole('listitem').map((li) => within(li).getByRole('heading').textContent);
  expect(names).toEqual(['Teléfonos', 'Laptops', 'Accesorios']);
});

test('showing a family on the home is saved at once', async () => {
  render(<CategoriesManager canManage />);

  fireEvent.click(within(await row('Laptops')).getByLabelText('Mostrar «Laptops» en la portada'));

  await waitFor(() => expect(writes).toEqual([{ method: 'PATCH', path: '/2/', body: { show_on_home: true } }]));
  expect(within(await row('Laptops')).getByLabelText('Mostrar «Laptops» en la portada')).toBeChecked();
});

test('retiring a category says its products stay', async () => {
  render(<CategoriesManager canManage />);

  fireEvent.click(within(await row('Teléfonos')).getByLabelText('«Teléfonos» activa'));

  await waitFor(() => expect(writes[0]).toEqual({ method: 'PATCH', path: '/1/', body: { is_active: false } }));
  expect(await screen.findByText(/sus productos siguen en el catálogo/)).toBeInTheDocument();
});

test('moving a family up swaps its place with the one above', async () => {
  render(<CategoriesManager canManage />);

  fireEvent.click(within(await row('Laptops')).getByRole('button', { name: 'Subir «Laptops»' }));

  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes).toEqual([
    { method: 'PATCH', path: '/2/', body: { home_order: 1 } },
    { method: 'PATCH', path: '/1/', body: { home_order: 2 } },
  ]);
  await waitFor(() => {
    const names = screen.getAllByRole('listitem').map((li) => within(li).getByRole('heading').textContent);
    expect(names).toEqual(['Laptops', 'Teléfonos', 'Accesorios']);
  });
});

test('categories nobody arranged yet are numbered by where they end up', async () => {
  rows = rows.map((row) => ({ ...row, home_order: 0 }));
  render(<CategoriesManager canManage />);

  // Con el mismo número, el orden es alfabético: Accesorios, Laptops, Teléfonos.
  fireEvent.click(within(await row('Laptops')).getByRole('button', { name: 'Subir «Laptops»' }));

  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes.map((w) => [w.path, w.body.home_order])).toEqual([['/2/', 1], ['/3/', 2], ['/1/', 3]]);
});

test('the first cannot go up and the last cannot go down', async () => {
  render(<CategoriesManager canManage />);
  expect(within(await row('Teléfonos')).getByRole('button', { name: 'Subir «Teléfonos»' })).toBeDisabled();
  expect(within(await row('Accesorios')).getByRole('button', { name: 'Bajar «Accesorios»' })).toBeDisabled();
});

test('a new category is created by name and appears in the list', async () => {
  render(<CategoriesManager canManage />);
  await row('Laptops');

  fireEvent.change(screen.getByLabelText('Nombre de la categoría nueva'), { target: { value: ' Tablets ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Crear categoría' }));

  expect(await screen.findByText('Tablets')).toBeInTheDocument();
  expect(writes[0]).toEqual({ method: 'POST', path: '/', body: { name: 'Tablets' } });
});

test('a refusal is shown and the list keeps what the server has', async () => {
  failWith = { status: 403, body: {} };
  render(<CategoriesManager canManage />);

  fireEvent.click(within(await row('Laptops')).getByLabelText('Mostrar «Laptops» en la portada'));

  expect(await screen.findByRole('alert')).toHaveTextContent(/permisos/);
  expect(within(await row('Laptops')).getByLabelText('Mostrar «Laptops» en la portada')).not.toBeChecked();
});

test('someone who can only look cannot change anything', async () => {
  render(<CategoriesManager canManage={false} />);

  expect(within(await row('Laptops')).getByLabelText('Mostrar «Laptops» en la portada')).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Crear categoría' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Subir «Laptops»' })).toBeNull();
});
