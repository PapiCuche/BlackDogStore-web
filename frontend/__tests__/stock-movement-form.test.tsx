/**
 * INV-UX-A1 — «Registrar entrada o salida» with the product picker, the
 * ?product= preselect, the «Siguiente paso» bar and the branch memory.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { StockMovementForm } from '@/app/admin/components/StockMovementForm';
import { forgetBranch } from '@/app/admin/lib/last-branch';
import * as admin from '@/app/lib/admin';
import * as inventory from '@/app/lib/inventory';

jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
  fetchAdminProductDetail: jest.fn(),
}));
jest.mock('@/app/lib/inventory', () => ({
  ...jest.requireActual('@/app/lib/inventory'),
  createStockMovement: jest.fn(),
}));
const search = (admin as jest.Mocked<typeof admin>).fetchAdminProducts;
const detail = (admin as jest.Mocked<typeof admin>).fetchAdminProductDetail;
const create = (inventory as jest.Mocked<typeof inventory>).createStockMovement;

const BRANCHES = [{ id: 1, name: 'Centro' }, { id: 2, name: 'Norte' }];

function product(id: number, over: Partial<admin.AdminProduct> = {}): admin.AdminProduct {
  return {
    id, name: `Cable ${id}`, slug: `cable-${id}`, description: '', price: '10.00', inventory: 5, image_url: '',
    category_id: 1, category_name: 'Cables', is_active: true, is_published_online: true, publication_problems: [],
    is_serialized: false, sale_mode: 'stock', condition_line: 'new', product_model_id: null,
    primary_code: `BD-${id}`, created_at: '', updated_at: '', ...over,
  } as admin.AdminProduct;
}
const movement = (over: Record<string, unknown> = {}) => ({
  id: 1, branch: 2, branch_name: 'Norte', movement_type_label: 'Entrada por compra',
  stock_before: 5, stock_after: 8, ...over,
}) as never;

async function choose(term: string, id: number) {
  search.mockResolvedValueOnce({ count: 1, page: 1, page_size: 8, results: [product(id)] } as never);
  fireEvent.change(screen.getByRole('combobox', { name: 'Producto' }), { target: { value: term } });
  await act(async () => { fireEvent.keyDown(screen.getByRole('combobox', { name: 'Producto' }), { key: 'Enter' }); });
}
const fill = (label: string | RegExp, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

beforeEach(() => {
  forgetBranch();
  search.mockReset(); detail.mockReset(); create.mockReset();
  localStorage.clear(); sessionStorage.clear();
});

it('picks the product by typing: no <select> of products, no 100-item preload', async () => {
  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} />);
  expect(document.querySelector('select#sm-product')).toBeNull();
  expect(search).not.toHaveBeenCalled();
  await choose('BD-3', 3);
  expect(search.mock.calls[0][0]).toMatchObject({ search: 'BD-3', page_size: 8 });
  expect(screen.getByText('Cable 3')).toBeInTheDocument();
});

it('registers the movement for the picked product and branch', async () => {
  create.mockResolvedValue(movement());
  const onCreated = jest.fn();
  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} onCreated={onCreated} />);
  await choose('BD-3', 3);
  fill('Sucursal', '2');
  fill('Tipo de movimiento', 'purchase_entry');
  fill('Cantidad', '3');
  fill(/^Motivo/, 'Factura F001-9');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Registrar movimiento' })); });
  expect(create).toHaveBeenCalledWith({
    product_id: 3, branch: 2, movement_type: 'purchase_entry', quantity: 3, reason: 'Factura F001-9',
  });
  expect(onCreated).toHaveBeenCalled();
  expect(await screen.findByText(/stock 5 → 8/)).toBeInTheDocument();
});

it('?product= preselects the product and the purchase entry', async () => {
  detail.mockResolvedValue(product(41, { name: 'Funda MagSafe' }));
  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} initialProductId={41} initialType="purchase_entry" />);
  expect(await screen.findByText('Funda MagSafe')).toBeInTheDocument();
  expect(detail).toHaveBeenCalledWith(41);
  expect(screen.getByLabelText('Tipo de movimiento')).toHaveValue('purchase_entry');
});

it('a product from the link that does not exist is said in Spanish, and the picker stays usable', async () => {
  detail.mockRejectedValue(new Error('Producto no encontrado.'));
  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} initialProductId={99} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Producto no encontrado.');
  expect(screen.getByRole('combobox', { name: 'Producto' })).toBeEnabled();
});

it('«Guardar y recibir más stock» keeps branch, type and reason and clears product and quantity', async () => {
  create.mockResolvedValue(movement());
  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} />);
  await choose('BD-3', 3);
  fill('Sucursal', '2');
  fill('Tipo de movimiento', 'purchase_entry');
  fill('Cantidad', '7');
  fill(/^Motivo/, 'Factura F001-9');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar y recibir más stock' })); });

  await screen.findByRole('region', { name: 'Siguiente paso' });
  expect(screen.getByLabelText('Sucursal')).toHaveValue('2');
  expect(screen.getByLabelText('Tipo de movimiento')).toHaveValue('purchase_entry');
  expect(screen.getByLabelText(/^Motivo/)).toHaveValue('Factura F001-9');
  expect(screen.getByLabelText('Cantidad')).toHaveValue(1);
  expect(screen.getByRole('combobox', { name: 'Producto' })).toHaveValue('');
  expect(screen.getByRole('combobox', { name: 'Producto' })).toHaveFocus();
});

it('shows «Siguiente paso» after a plain save, with the published product on the storefront link', async () => {
  create.mockResolvedValue(movement());
  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} />);
  await choose('BD-3', 3);
  fill('Cantidad', '1'); fill(/^Motivo/, 'Compra');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Registrar movimiento' })); });
  const bar = await screen.findByRole('region', { name: 'Siguiente paso' });
  expect(bar).toHaveTextContent('Cable 3');
  expect(screen.getByRole('link', { name: 'Ver en la tienda' })).toHaveAttribute('href', '/product/cable-3');
  expect(screen.queryByText(/listas para imprimir/)).toBeNull();
});

it('announces label_batch when the server answers with one', async () => {
  create.mockResolvedValue(movement({ label_batch: { id: 9, count: 3 } }));
  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} />);
  await choose('BD-3', 3);
  fill('Cantidad', '1'); fill(/^Motivo/, 'Compra');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Registrar movimiento' })); });
  expect(await screen.findByText(/Etiquetas listas para imprimir/)).toBeInTheDocument();
});

it('remembers the last branch for the next time, in memory only', async () => {
  create.mockResolvedValue(movement({ branch: 2 }));
  const { unmount } = render(<StockMovementForm branches={BRANCHES} defaultBranch={1} />);
  await choose('BD-3', 3);
  fill('Sucursal', '2'); fill('Cantidad', '1'); fill(/^Motivo/, 'Compra');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Registrar movimiento' })); });
  await screen.findByRole('region', { name: 'Siguiente paso' });
  unmount();

  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} />);
  expect(screen.getByLabelText('Sucursal')).toHaveValue('2');
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});

it('explains each missing field next to it once touched', async () => {
  render(<StockMovementForm branches={BRANCHES} defaultBranch={null} />);
  const reason = screen.getByLabelText(/^Motivo/);
  fireEvent.blur(reason);
  expect(await screen.findByText('Escribe el motivo del movimiento.')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Cantidad'), { target: { value: '0' } });
  fireEvent.blur(screen.getByLabelText('Cantidad'));
  expect(screen.getByText('La cantidad debe ser un número entero mayor que cero.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Registrar movimiento' })).toBeDisabled();
});

it('a serial-tracked product is pointed to Equipos instead of failing later', async () => {
  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} />);
  search.mockResolvedValueOnce({ count: 1, page: 1, page_size: 8, results: [product(10, { name: 'iPhone 16', is_serialized: true })] } as never);
  fireEvent.change(screen.getByRole('combobox', { name: 'Producto' }), { target: { value: 'iphone' } });
  await act(async () => { fireEvent.keyDown(screen.getByRole('combobox', { name: 'Producto' }), { key: 'Enter' }); });
  const note = await screen.findByRole('note');
  expect(note).toHaveTextContent(/se recibe equipo por equipo/i);
  expect(screen.getByRole('link', { name: 'Registrar equipo' })).toHaveAttribute('href', '/admin/inventory/units?register=1&product=10');
});

it('a refused movement shows the server\'s message and keeps what was typed', async () => {
  create.mockRejectedValue(new Error('El stock de la sucursal no alcanza.'));
  render(<StockMovementForm branches={BRANCHES} defaultBranch={1} />);
  await choose('BD-3', 3);
  fill('Cantidad', '2'); fill(/^Motivo/, 'Compra');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Registrar movimiento' })); });
  expect(await screen.findByText('El stock de la sucursal no alcanza.')).toBeInTheDocument();
  expect(screen.getByLabelText(/^Motivo/)).toHaveValue('Compra');
  await waitFor(() => expect(screen.queryByRole('region', { name: 'Siguiente paso' })).toBeNull());
});
