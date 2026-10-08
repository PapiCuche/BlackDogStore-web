/**
 * PRODUCT-DESTINATION-01 — «sólo stock interno», en el panel.
 *
 * Un producto puede estar en uso —caja, inventario, taller— y no venderse por
 * la web. Es otro dato que «activo», y estas pruebas fijan que el panel lo
 * trate así: se elige en la ficha, se ve en la lista y la carga masiva lo
 * muestra antes de aplicar nada.
 *
 * Quién ve un producto así lo decide el servidor (`test_product_visibility.py`).
 */
import { act, fireEvent, render, screen } from '@testing-library/react';

import { ProductForm } from '@/app/admin/components/ProductForm';
import { ProductStatusBadge } from '@/app/admin/components/ProductStatusBadge';
import { DefaultCategoryField, destinationLabel } from '@/app/admin/components/ImportWizard';
import * as admin from '@/app/lib/admin';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  createAdminProduct: jest.fn(),
  patchAdminProduct: jest.fn(),
  uploadProductImage: jest.fn(),
}));

const api = admin as jest.Mocked<typeof admin>;

const ONLINE = 'Publicar en e-commerce';
const INTERNAL = 'Solo stock interno';

const product: admin.AdminProduct = {
  id: 7, name: 'Flex de carga', slug: 'flex-de-carga', description: '', price: '35.00', inventory: 4,
  image_url: '', category_id: null, category_name: null, is_active: true, is_published_online: true,
  created_at: '2026-10-08T00:00:00Z', updated_at: '2026-10-08T00:00:00Z',
};

const category = (id: number, name: string): admin.AdminCategory =>
  ({ id, name, slug: name.toLowerCase(), is_active: true }) as admin.AdminCategory;

const destination = () => screen.getByLabelText('Destino') as HTMLSelectElement;
const save = async () => {
  await act(async () => { fireEvent.submit(screen.getByRole('button', { name: /Guardar|Crear/ }).closest('form')!); });
};

beforeEach(() => {
  api.createAdminProduct.mockReset();
  api.patchAdminProduct.mockReset();
});

describe('la ficha del producto', () => {
  it('un producto nuevo va a la web salvo que se diga otra cosa', async () => {
    api.createAdminProduct.mockResolvedValueOnce(product);
    render(<ProductForm categories={[]} onSaved={jest.fn()} />);

    expect(destination().value).toBe('online');
    expect([...destination().options].map((option) => option.textContent)).toEqual([ONLINE, INTERNAL]);

    fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Cable' } });
    fireEvent.change(screen.getByLabelText(/Precio/), { target: { value: '12' } });
    await save();

    expect(api.createAdminProduct.mock.calls[0][0]).toMatchObject({ is_published_online: true, is_active: true });
  });

  it('se puede crear sólo para stock interno, y sigue activo', async () => {
    api.createAdminProduct.mockResolvedValueOnce({ ...product, is_published_online: false });
    render(<ProductForm categories={[]} onSaved={jest.fn()} />);

    fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Pegamento' } });
    fireEvent.change(screen.getByLabelText(/Precio/), { target: { value: '12' } });
    fireEvent.change(destination(), { target: { value: 'internal' } });
    await save();

    expect(api.createAdminProduct.mock.calls[0][0]).toMatchObject({ is_published_online: false, is_active: true });
  });

  it('muestra dónde está un producto que ya existe, y guardar otra cosa no decide su destino', async () => {
    // Una pestaña abierta desde ayer no devuelve a la web lo que alguien quitó hoy:
    // el destino sólo viaja cuando la persona lo cambió.
    const internal = { ...product, is_published_online: false };
    api.patchAdminProduct.mockResolvedValueOnce(internal);
    render(<ProductForm product={internal} categories={[]} onSaved={jest.fn()} />);

    expect(destination().value).toBe('internal');
    fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Flex de carga iPhone 13' } });
    await save();

    expect(api.patchAdminProduct.mock.calls[0][1]).not.toHaveProperty('is_published_online');
  });

  it('al cambiarlo en un producto que ya existe, sí viaja', async () => {
    api.patchAdminProduct.mockResolvedValueOnce({ ...product, is_published_online: false });
    render(<ProductForm product={product} categories={[]} onSaved={jest.fn()} />);

    fireEvent.change(destination(), { target: { value: 'internal' } });
    await save();

    expect(api.patchAdminProduct.mock.calls[0][1]).toMatchObject({ is_published_online: false });
  });

  it('dice qué significa cada destino, y que no es lo mismo que desactivar', () => {
    render(<ProductForm categories={[]} onSaved={jest.fn()} />);

    fireEvent.change(destination(), { target: { value: 'internal' } });
    const help = screen.getByText(/no aparece en la tienda en línea/i);
    expect(help).toHaveTextContent(/caja/i);
    expect(help).toHaveTextContent(/inventario/i);
    expect(screen.getByLabelText(/Producto activo/)).toBeChecked();
  });
});

describe('la etiqueta de estado', () => {
  it('un producto activo que no va a la web lo dice', () => {
    render(<ProductStatusBadge isActive publishedOnline={false} />);
    expect(screen.getByText('Activo')).toBeInTheDocument();
    expect(screen.getByText(INTERNAL)).toBeInTheDocument();
  });

  it('uno que va a la web no lleva nada de más', () => {
    render(<ProductStatusBadge isActive publishedOnline />);
    expect(screen.getByText('Activo')).toBeInTheDocument();
    expect(screen.queryByText(INTERNAL)).toBeNull();
  });

  it('sin el dato —una pantalla de antes— no afirma nada', () => {
    render(<ProductStatusBadge isActive={false} />);
    expect(screen.getByText('Inactivo')).toBeInTheDocument();
    expect(screen.queryByText(INTERNAL)).toBeNull();
  });
});

describe('la vista previa de la carga masiva', () => {
  const row = (action: string, value?: string) => ({ action, data: value === undefined ? {} : { destination: value } });

  it('dice a dónde irá cada fila', () => {
    expect(destinationLabel(row('create', 'online'))).toBe(ONLINE);
    expect(destinationLabel(row('create', 'internal'))).toBe(INTERNAL);
    expect(destinationLabel(row('update', 'internal'))).toBe(INTERNAL);
  });

  it('una celda vacía en un producto que ya existe no cambia nada, y lo dice', () => {
    expect(destinationLabel(row('update', ''))).toBe('Sin cambios');
  });

  it('una vista previa de antes de esta columna no inventa un destino', () => {
    expect(destinationLabel(row('update'))).toBe('Sin cambios');
    expect(destinationLabel(row('error', ''))).toBe('');
    expect(destinationLabel(row('skip'))).toBe('');
  });
});

describe('la categoría para las filas que no traen una', () => {
  it('ofrece las categorías de la empresa y ninguna más', () => {
    const onChange = jest.fn();
    render(<DefaultCategoryField categories={[category(3, 'Repuestos'), category(5, 'Teléfonos')]} value={null} onChange={onChange} />);

    const field = screen.getByLabelText(/Categoría para las filas que no traen una/) as HTMLSelectElement;
    expect([...field.options].map((option) => option.textContent)).toEqual(['Dejarlas sin categoría', 'Repuestos', 'Teléfonos']);

    fireEvent.change(field, { target: { value: '5' } });
    expect(onChange).toHaveBeenLastCalledWith(5);
    fireEvent.change(field, { target: { value: '' } });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('sin categorías lo dice en vez de ofrecer una lista vacía', () => {
    render(<DefaultCategoryField categories={[]} value={null} onChange={jest.fn()} />);
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.getByText(/todavía no tiene categorías/i)).toBeInTheDocument();
  });

  it('si las categorías no se pudieron leer, lo dice: no es lo mismo que no tener ninguna', () => {
    render(<DefaultCategoryField categories={null} value={null} onChange={jest.fn()} />);
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.getByText(/No se pudieron leer las categorías/i)).toBeInTheDocument();
    expect(screen.queryByText(/todavía no tiene categorías/i)).toBeNull();
  });
});
