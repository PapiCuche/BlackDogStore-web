/**
 * COMMERCE-ADMIN-UX-01 — Productos › Publicación e-commerce.
 *
 * Una pantalla para TODOS los tipos de artículo: equipos nuevos, seminuevos,
 * accesorios y repuestos, y lo que se vende bajo pedido. Reutiliza la acción
 * en bloque de INVENTORY-UNIFIED (productos y variantes: una variante ES un
 * producto) y la nueva de modelos. La regla de publicación no cambia: lo que no
 * está listo no se publica y se dice por qué.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { PublicationCentre } from '@/app/admin/components/PublicationCentre';
import * as admin from '@/app/lib/admin';
import * as api from '@/app/admin/lib/internal-api';
import * as registration from '@/app/lib/registration';

jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
  bulkSetProductVisibility: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchAdminCatalogModels: jest.fn(),
}));
jest.mock('@/app/lib/registration', () => ({
  ...jest.requireActual('@/app/lib/registration'),
  bulkSetModelVisibility: jest.fn(),
}));

const mAdmin = admin as jest.Mocked<typeof admin>;
const mApi = api as jest.Mocked<typeof api>;
const mReg = registration as jest.Mocked<typeof registration>;

function product(id: number, over: Partial<admin.AdminProduct> = {}): admin.AdminProduct {
  return {
    id, name: `Producto ${id}`, slug: `p-${id}`, description: '', price: '10.00', inventory: 1, image_url: '',
    category_id: 1, category_name: 'Fundas', is_active: true, is_published_online: false, publication_problems: [],
    is_serialized: false, sale_mode: 'stock', condition_line: 'new', product_model_id: null,
    created_at: '', updated_at: '', ...over,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mAdmin.fetchAdminProducts.mockResolvedValue({
    count: 3, next: null, previous: null, page: 1, page_size: 50, total_pages: 1,
    results: [
      product(1),
      product(2, { name: 'Sin categoría', category_id: null, publication_problems: ['no_category'] }),
      product(3, { name: 'iPhone usado', is_serialized: true, condition_line: 'used', product_model_id: 9,
        is_published_online: true }),
    ],
  } as unknown as Awaited<ReturnType<typeof admin.fetchAdminProducts>>);
  mApi.fetchAdminCatalogModels.mockResolvedValue([
    { id: 9, name: 'iPhone 15', slug: 'iphone-15', category_id: 6, category_name: 'Celulares', image_url: '',
      description: '', is_published_online: true, sort_order: 0, variant_count: 2, used_unit_count: 1 },
  ]);
  mAdmin.bulkSetProductVisibility.mockResolvedValue({
    action: 'publish', changed: 1, unchanged: 0, total: 2,
    refused: [{ id: 2, name: 'Sin categoría', problems: ['no_category'] }],
  });
  mReg.bulkSetModelVisibility.mockResolvedValue({ action: 'hide', changed: 1, unchanged: 0, total: 1 });
});

describe('Publicación e-commerce', () => {
  it('lista todos los tipos y dice qué le falta a cada uno', async () => {
    render(<PublicationCentre canManage companyId={7} />);
    const table = await screen.findByRole('table', { name: 'Productos y variantes' });
    expect(within(table).getByText('Sin categoría')).toBeInTheDocument();
    expect(within(table).getByText(/no tiene categoría/)).toBeInTheDocument();
    expect(within(table).getByText('Equipo seminuevo')).toBeInTheDocument();
    expect(within(table).getAllByText('Accesorio o repuesto').length).toBe(2);
  });

  it('INV-QA-E: la tabla se desplaza en su propio contenedor `relative` (sus etiquetas sr-only no ensanchan la página a 390 px)', async () => {
    render(<PublicationCentre canManage companyId={7} />);
    const table = await screen.findByRole('table', { name: 'Productos y variantes' });
    const scroller = table.parentElement as HTMLElement;
    expect(scroller.className).toContain('overflow-x-auto');
    expect(scroller.className).toContain('relative');
  });

  it('filtra por tipo con los filtros del servidor', async () => {
    render(<PublicationCentre canManage companyId={7} />);
    await screen.findByRole('table', { name: 'Productos y variantes' });
    fireEvent.change(screen.getByLabelText('Tipo'), { target: { value: 'used' } });
    await waitFor(() => expect(mAdmin.fetchAdminProducts).toHaveBeenLastCalledWith(
      expect.objectContaining({ company: 7, condition_line: 'used' })));
    fireEvent.change(screen.getByLabelText('Tipo'), { target: { value: 'on_order' } });
    await waitFor(() => expect(mAdmin.fetchAdminProducts).toHaveBeenLastCalledWith(
      expect.objectContaining({ sale_mode: 'stock_or_order' })));
    fireEvent.change(screen.getByLabelText('Tipo'), { target: { value: 'accessory' } });
    await waitFor(() => expect(mAdmin.fetchAdminProducts).toHaveBeenLastCalledWith(
      expect.objectContaining({ is_serialized: 'false', sale_mode: 'stock' })));
    fireEvent.change(screen.getByLabelText('En e-commerce'), { target: { value: 'false' } });
    await waitFor(() => expect(mAdmin.fetchAdminProducts).toHaveBeenLastCalledWith(
      expect.objectContaining({ is_published_online: 'false' })));
  });

  it('publica los marcados con la acción en bloque de siempre y dice los que no', async () => {
    render(<PublicationCentre canManage companyId={7} />);
    await screen.findByRole('table', { name: 'Productos y variantes' });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Seleccionar Producto 1' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Seleccionar Sin categoría' }));
    fireEvent.click(screen.getByRole('button', { name: 'Publicar en e-commerce' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirmar' }));
    await waitFor(() => expect(mAdmin.bulkSetProductVisibility).toHaveBeenCalledWith('publish', { ids: [1, 2] }, 7));
    expect(await screen.findByText(/1 no se publicó porque le falta algo/)).toBeInTheDocument();
  });

  it('INV-UX-A1: abre ya filtrada al producto que llega por ?search=', async () => {
    render(<PublicationCentre canManage companyId={7} initialSearch="Cable USB-C" />);
    await screen.findByRole('table', { name: 'Productos y variantes' });
    expect(mAdmin.fetchAdminProducts).toHaveBeenCalledWith(expect.objectContaining({ search: 'Cable USB-C' }));
    expect(screen.getByLabelText('Buscar')).toHaveValue('Cable USB-C');
  });

  it('publica u oculta modelos enteros', async () => {
    render(<PublicationCentre canManage companyId={7} />);
    const models = await screen.findByRole('table', { name: 'Modelos' });
    fireEvent.click(within(models).getByRole('checkbox', { name: 'Seleccionar modelo iPhone 15' }));
    fireEvent.click(screen.getByRole('button', { name: 'Ocultar modelos' }));
    await waitFor(() => expect(mReg.bulkSetModelVisibility).toHaveBeenCalledWith('hide', [9], 7));
    expect(await screen.findByText(/Se ocultó 1 modelo/)).toBeInTheDocument();
  });

  it('sin «Gestionar productos» se ve, pero no se cambia', async () => {
    render(<PublicationCentre canManage={false} companyId={7} />);
    await screen.findByRole('table', { name: 'Productos y variantes' });
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Publicar en e-commerce' })).toBeNull();
    expect(screen.getByText(/requiere el permiso «Gestionar productos»/)).toBeInTheDocument();
  });
});
