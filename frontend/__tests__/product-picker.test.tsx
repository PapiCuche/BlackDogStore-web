/**
 * INV-UX-A1 — ProductPicker: the product is found by typing a name, an SKU or a
 * scanned code, not by scrolling a 100-item <select>.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';

import { ProductPicker, type PickerProduct } from '@/app/admin/components/ProductPicker';
import * as admin from '@/app/lib/admin';

jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
}));
const search = (admin as jest.Mocked<typeof admin>).fetchAdminProducts;

function row(id: number, over: Partial<admin.AdminProduct> = {}): admin.AdminProduct {
  return {
    id, name: `Producto ${id}`, slug: `p-${id}`, description: '', price: '10.00', inventory: 4, image_url: '',
    category_id: 1, category_name: 'Cables', is_active: true, is_published_online: true, publication_problems: [],
    is_serialized: false, sale_mode: 'stock', condition_line: 'new', product_model_id: null,
    primary_code: `BD-${id}`, search_exact: false, created_at: '', updated_at: '', ...over,
  } as admin.AdminProduct;
}
const page = (rows: admin.AdminProduct[]) => ({ count: rows.length, page: 1, page_size: 8, results: rows }) as never;

function Harness({ initial = null, onPick }: { initial?: PickerProduct | null; onPick?: (p: PickerProduct | null) => void }) {
  const [value, setValue] = useState<PickerProduct | null>(initial);
  return (
    <ProductPicker
      label="Producto"
      value={value}
      onChange={(next) => { setValue(next); onPick?.(next); }}
    />
  );
}

const box = () => screen.getByRole('combobox', { name: 'Producto' });
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const settle = async (ms = 300) => { await act(async () => { jest.advanceTimersByTime(ms); }); };

beforeEach(() => {
  jest.useFakeTimers();
  search.mockReset();
});
afterEach(() => jest.useRealTimers());

describe('search as you type', () => {
  it('waits for a pause before asking, and asks once for what was typed', async () => {
    search.mockResolvedValue(page([row(1)]));
    render(<Harness />);
    type('ca'); type('cab'); type('cabl');
    await settle(100);
    expect(search).not.toHaveBeenCalled();
    await settle(300);
    expect(search).toHaveBeenCalledTimes(1);
    expect(search.mock.calls[0][0]).toMatchObject({ search: 'cabl', page_size: 8 });
  });

  it('does not ask for a single character', async () => {
    render(<Harness />);
    type('c');
    await settle(500);
    expect(search).not.toHaveBeenCalled();
    expect(screen.getByText(/al menos 2 letras/i)).toBeInTheDocument();
  });

  it('shows name, SKU, price, condition and total stock for each result', async () => {
    search.mockResolvedValue(page([
      row(7, { name: 'iPhone 15 128 GB Negro', condition_line: 'used', price: '3200.00', inventory: 2, primary_code: 'BD-000007', is_serialized: true }),
    ]));
    render(<Harness />);
    type('iphone');
    await settle();
    const option = await screen.findByRole('option', { name: /iPhone 15 128 GB Negro/ });
    expect(option).toHaveTextContent('BD-000007');
    expect(option).toHaveTextContent('S/ 3200.00');
    expect(option).toHaveTextContent('Seminuevo');
    expect(option).toHaveTextContent('2 u. en total');
    expect(option).toHaveTextContent('Por serie');
  });

  it('never prints a cost or an IMEI, even if the response carried one', async () => {
    search.mockResolvedValue(page([{ ...row(1), reference_cost: '99.00', imei: '356938035643809' } as admin.AdminProduct]));
    render(<Harness />);
    type('prod');
    await settle();
    await screen.findByRole('option');
    expect(document.body).not.toHaveTextContent('99.00');
    expect(document.body).not.toHaveTextContent('356938035643809');
  });

  it('a slow answer to an old search never replaces the newer one', async () => {
    let late: (value: never) => void = () => {};
    search.mockImplementationOnce(() => new Promise((resolve) => { late = resolve; }));
    search.mockResolvedValueOnce(page([row(2, { name: 'Nuevo resultado' })]));
    render(<Harness />);
    type('viejo'); await settle();
    type('nuevo'); await settle();
    await screen.findByRole('option', { name: /Nuevo resultado/ });
    await act(async () => { late(page([row(1, { name: 'Resultado viejo' })])); });
    expect(screen.queryByRole('option', { name: /Resultado viejo/ })).toBeNull();
    expect(screen.getByRole('option', { name: /Nuevo resultado/ })).toBeInTheDocument();
  });
});

describe('keyboard', () => {
  it('arrow keys move through the options and Enter picks the highlighted one', async () => {
    const onPick = jest.fn();
    search.mockResolvedValue(page([row(1), row(2), row(3)]));
    render(<Harness onPick={onPick} />);
    type('prod'); await settle();
    await screen.findAllByRole('option');
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    expect(box()).toHaveAttribute('aria-activedescendant', screen.getAllByRole('option')[1].id);
    fireEvent.keyDown(box(), { key: 'ArrowUp' });
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }));
  });

  it('Escape closes the list without choosing', async () => {
    const onPick = jest.fn();
    search.mockResolvedValue(page([row(1)]));
    render(<Harness onPick={onPick} />);
    type('prod'); await settle();
    await screen.findByRole('option');
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(box()).toHaveAttribute('aria-expanded', 'false');
    expect(onPick).not.toHaveBeenCalled();
  });

  it('Enter never submits the surrounding form', async () => {
    search.mockResolvedValue(page([]));
    render(<form><Harness /></form>);
    type('ab');
    let notPrevented = true;
    await act(async () => { notPrevented = fireEvent.keyDown(box(), { key: 'Enter' }); });
    expect(notPrevented).toBe(false);
  });
});

describe('scanner', () => {
  it('Enter on a scanned code asks at once and picks the exact match', async () => {
    const onPick = jest.fn();
    search.mockResolvedValue(page([row(9, { search_exact: true, name: 'Cable exacto' }), row(10)]));
    render(<Harness onPick={onPick} />);
    type('7750001234567');
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }); });
    expect(search).toHaveBeenCalledTimes(1);
    expect(search.mock.calls[0][0]).toMatchObject({ search: '7750001234567' });
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 9 }));
    await settle(500);
    expect(search).toHaveBeenCalledTimes(1); // the pending debounce was cancelled
  });

  it('picks the only result even when it is not flagged exact', async () => {
    const onPick = jest.fn();
    search.mockResolvedValue(page([row(4)]));
    render(<Harness onPick={onPick} />);
    type('BD-4');
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }); });
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 4 }));
  });

  it('with several loose matches it chooses nothing and asks the person to choose', async () => {
    const onPick = jest.fn();
    search.mockResolvedValue(page([row(1), row(2)]));
    render(<Harness onPick={onPick} />);
    type('cable');
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }); });
    expect(onPick).not.toHaveBeenCalled();
    expect(screen.getByText(/varios productos coinciden/i)).toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(2);
  });

  it('a code nobody has is said plainly', async () => {
    search.mockResolvedValue(page([]));
    render(<Harness />);
    type('0000000000');
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }); });
    expect(screen.getByText(/ningún producto coincide con «0000000000»/i)).toBeInTheDocument();
  });
});

describe('states', () => {
  it('an empty search says so and keeps the field usable', async () => {
    search.mockResolvedValue(page([]));
    render(<Harness />);
    type('zzzz'); await settle();
    expect(await screen.findByText(/ningún producto coincide con «zzzz»/i)).toBeInTheDocument();
    expect(box()).toBeEnabled();
  });

  it('a failed search shows a readable message and can be retried', async () => {
    search.mockRejectedValueOnce(new Error('No se pudieron cargar los productos.'));
    search.mockResolvedValueOnce(page([row(1)]));
    render(<Harness />);
    type('prod'); await settle();
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudieron cargar los productos.');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reintentar' })); });
    expect(await screen.findByRole('option')).toBeInTheDocument();
  });

  it('an unknown failure never leaks a raw exception', async () => {
    search.mockRejectedValueOnce('TypeError: x is undefined');
    render(<Harness />);
    type('prod'); await settle();
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo buscar. Revisa la conexión e inténtalo de nuevo.');
  });

  it('the chosen product is shown and can be changed', async () => {
    const onPick = jest.fn();
    render(<Harness initial={row(5, { name: 'Cargador 20 W' })} onPick={onPick} />);
    expect(screen.getByText('Cargador 20 W')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /cambiar producto/i }));
    expect(onPick).toHaveBeenCalledWith(null);
    expect(box()).toHaveFocus();
  });

  it('shows the per-field error next to the input', () => {
    render(<ProductPicker label="Producto" value={null} onChange={() => {}} error="Elige un producto." />);
    expect(box()).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Elige un producto.')).toBeInTheDocument();
  });
});

describe('privacy', () => {
  it('whatever is typed or scanned (even an IMEI) never reaches the browser storage', async () => {
    localStorage.clear(); sessionStorage.clear();
    search.mockResolvedValue(page([row(1, { search_exact: true })]));
    render(<Harness />);
    type('356938035643809');
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }); });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});
