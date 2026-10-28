/**
 * ECOM-READY-D1 — «Completar ficha», vista previa para el cliente y «Publicar listos».
 *
 * El panel no trae reglas: lo pendiente, la tarjeta y la disponibilidad vienen del
 * servidor; publicar pasa por la acción en bloque con un id. Aquí se fija lo que el
 * operador ve y hace, y que nada llega al almacenamiento del navegador.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { PublicationCentre } from '@/app/admin/components/PublicationCentre';
import { validatePrice } from '@/app/admin/components/CompleteProductPanel';
import * as admin from '@/app/lib/admin';
import * as api from '@/app/admin/lib/internal-api';

jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
  bulkSetProductVisibility: jest.fn(),
  fetchProductPreview: jest.fn(),
  patchAdminProduct: jest.fn(),
  uploadProductImage: jest.fn(),
  fetchAdminCategories: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchAdminCatalogModels: jest.fn(),
}));

const mAdmin = admin as jest.Mocked<typeof admin>;
const mApi = api as jest.Mocked<typeof api>;

function product(id: number, over: Partial<admin.AdminProduct> = {}): admin.AdminProduct {
  return {
    id, name: `Producto ${id}`, slug: `p-${id}`, description: '', price: '10.00', inventory: 0, image_url: '',
    category_id: 1, category_name: 'Fundas', is_active: true, is_published_online: false, publication_problems: [],
    is_serialized: false, sale_mode: 'stock', condition_line: 'new', product_model_id: null,
    created_at: '', updated_at: '', ...over,
  };
}

function preview(over: Partial<admin.StorefrontPreview> = {}, productOver: Partial<admin.StorefrontPreview['product']> = {}): admin.StorefrontPreview {
  return {
    product: {
      id: 1, name: 'Producto 1', slug: 'p-1', description: 'Funda de silicona', price: '59.00', inventory: 0,
      image_url: '', images: [], category: { id: 1, name: 'Fundas' }, condition_line: 'new', sells_by_unit: false,
      on_order: null, ...productOver,
    },
    visibility: {
      state: 'hidden', visible: false, published: false, label: 'Oculto: el cliente no lo ve',
      model_hidden: false, placement: 'card',
    },
    availability: { state: 'none', label: 'Sin existencias: no se ofrece para entrega inmediata' },
    publication_problems: [],
    offered_units: [],
    ...over,
  };
}

const NO_PRICE = preview({ publication_problems: ['no_price'] }, { price: '0.00' });

function listWith(rows: admin.AdminProduct[], count = rows.length) {
  mAdmin.fetchAdminProducts.mockResolvedValue({
    count, next: null, previous: null, page: 1, page_size: 50, total_pages: 1, results: rows,
  } as unknown as Awaited<ReturnType<typeof admin.fetchAdminProducts>>);
}

async function openPanel(name = 'Producto 1', canManage = true) {
  render(<PublicationCentre canManage={canManage} companyId={7} />);
  await screen.findByRole('table', { name: 'Productos y variantes' });
  fireEvent.click(screen.getByRole('button', { name: `${canManage ? 'Completar ficha' : 'Ver ficha'} de ${name}` }));
  return screen.findByRole('dialog');
}

let setItem: jest.SpyInstance;

beforeEach(() => {
  jest.clearAllMocks();
  setItem = jest.spyOn(Storage.prototype, 'setItem');
  listWith([product(1, { price: '0.00', publication_problems: ['no_price'] }), product(2)]);
  mApi.fetchAdminCatalogModels.mockResolvedValue([]);
  mAdmin.fetchProductPreview.mockResolvedValue(NO_PRICE);
  mAdmin.fetchAdminCategories.mockResolvedValue([
    { id: 1, name: 'Fundas', slug: 'fundas' }, { id: 2, name: 'Cargadores', slug: 'cargadores' },
  ] as admin.AdminCategory[]);
  mAdmin.patchAdminProduct.mockResolvedValue(product(1));
  mAdmin.bulkSetProductVisibility.mockResolvedValue({ action: 'publish', changed: 1, unchanged: 0, refused: [], total: 1 });
  mAdmin.uploadProductImage.mockResolvedValue({
    id: 5, url: '/api/storefront/images/' + 'a'.repeat(32), alt_text: '', is_primary: true, sort_order: 0, width: 1, height: 1,
  });
});

afterEach(() => {
  setItem.mockRestore();
});

describe('Completar ficha — pendientes y editores', () => {
  it('un producto sin precio lo pone primero y lo enfoca', async () => {
    const dialog = await openPanel();
    expect(within(dialog).getByText('Le falta: no tiene precio de venta')).toBeInTheDocument();
    expect(within(dialog).getByText(/Empieza por el precio/)).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByLabelText('Precio de venta (S/)')).toHaveFocus());
    expect(mAdmin.fetchProductPreview).toHaveBeenCalledWith(1, 7);
  });

  it('un precio inválido no viaja y dice por qué; uno válido se guarda y refresca lo pendiente', async () => {
    const dialog = await openPanel();
    const input = within(dialog).getByLabelText('Precio de venta (S/)');
    fireEvent.change(input, { target: { value: '0' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar precio' }));
    expect(await within(dialog).findByText(/mayor que 0/)).toBeInTheDocument();
    expect(mAdmin.patchAdminProduct).not.toHaveBeenCalled();

    mAdmin.fetchProductPreview.mockResolvedValue(preview({}, { price: '99.90' }));
    fireEvent.change(input, { target: { value: '99,90' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar precio' }));
    await waitFor(() => expect(mAdmin.patchAdminProduct).toHaveBeenCalledWith(1, { price: '99.90' }, 7));
    expect(await within(dialog).findByText('Listo: no le falta nada.')).toBeInTheDocument();
    expect(within(dialog).queryByText(/Empieza por el precio/)).toBeNull();
    expect(within(dialog).getByText('S/ 99.90')).toBeInTheDocument();
    expect(mAdmin.fetchProductPreview).toHaveBeenCalledTimes(2);
  });

  it('el rechazo del servidor aparece en el campo y no se pierde lo escrito', async () => {
    mAdmin.patchAdminProduct.mockRejectedValue(new Error('El precio debe ser mayor que 0.'));
    const dialog = await openPanel();
    const input = within(dialog).getByLabelText('Precio de venta (S/)');
    fireEvent.change(input, { target: { value: '5' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar precio' }));
    expect(await within(dialog).findByText('El precio debe ser mayor que 0.')).toBeInTheDocument();
    expect(input).toHaveValue('5');
  });

  it('guarda categoría y descripción', async () => {
    const dialog = await openPanel();
    fireEvent.change(within(dialog).getByLabelText('Categoría'), { target: { value: '2' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar categoría' }));
    await waitFor(() => expect(mAdmin.patchAdminProduct).toHaveBeenCalledWith(1, { category: 2 }, 7));
    fireEvent.change(within(dialog).getByLabelText('Descripción corta'), { target: { value: '  Silicona suave ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar descripción' }));
    await waitFor(() => expect(mAdmin.patchAdminProduct).toHaveBeenCalledWith(1, { description: 'Silicona suave' }, 7));
  });

  it('la foto respeta formato y peso antes de viajar, y sube por la galería', async () => {
    const dialog = await openPanel();
    const input = within(dialog).getByLabelText('Foto') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['x'], 'nota.pdf', { type: 'application/pdf' })] } });
    expect(await within(dialog).findByText('Usa una imagen PNG, JPG o WebP.')).toBeInTheDocument();
    const big = new File(['x'], 'grande.png', { type: 'image/png' });
    Object.defineProperty(big, 'size', { value: admin.PRODUCT_IMAGE_MAX_BYTES + 1 });
    fireEvent.change(input, { target: { files: [big] } });
    expect(await within(dialog).findByText('La imagen pesa más de 8 MB.')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Subir foto' })).toBeDisabled();

    const ok = new File(['x'], 'funda.webp', { type: 'image/webp' });
    fireEvent.change(input, { target: { files: [ok] } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Subir foto' }));
    await waitFor(() => expect(mAdmin.uploadProductImage).toHaveBeenCalledWith(1, ok, 'Producto 1'));
    expect(await within(dialog).findByText('Foto subida.')).toBeInTheDocument();
  });

  it('sin «Gestionar productos» ve la vista previa pero no hay editores ni publicar', async () => {
    mAdmin.fetchProductPreview.mockResolvedValue(preview());
    const dialog = await openPanel('Producto 1', false);
    expect(await within(dialog).findByText('Oculto: el cliente no lo ve')).toBeInTheDocument();
    expect(within(dialog).getByRole('article', { name: 'Tarjeta del cliente' })).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('Precio de venta (S/)')).toBeNull();
    expect(within(dialog).queryByRole('button', { name: /Guardar/ })).toBeNull();
    expect(within(dialog).queryByRole('button', { name: 'Publicar en e-commerce' })).toBeNull();
    expect(mAdmin.fetchAdminCategories).not.toHaveBeenCalled();
  });

  it('Escape cierra el panel', async () => {
    await openPanel();
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('no escribe nada en el almacenamiento del navegador', async () => {
    const dialog = await openPanel();
    fireEvent.change(within(dialog).getByLabelText('Precio de venta (S/)'), { target: { value: '12' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar precio' }));
    await waitFor(() => expect(mAdmin.patchAdminProduct).toHaveBeenCalled());
    expect(setItem).not.toHaveBeenCalled();
  });
});

describe('Vista previa para el cliente', () => {
  it('oculto: lo dice y la disponibilidad no promete entrega inmediata', async () => {
    mAdmin.fetchProductPreview.mockResolvedValue(preview());
    const dialog = await openPanel();
    expect(await within(dialog).findByTestId('visibility-banner')).toHaveTextContent('Oculto: el cliente no lo ve');
    const card = within(dialog).getByRole('article', { name: 'Tarjeta del cliente' });
    expect(within(card).getByText('Sin existencias: no se ofrece para entrega inmediata')).toBeInTheDocument();
    expect(within(card).getByText('S/ 59.00')).toBeInTheDocument();
    expect(within(card).getByText('Funda de silicona')).toBeInTheDocument();
  });

  it('visible: lo dice, ofrece ocultar y no publicar', async () => {
    mAdmin.fetchProductPreview.mockResolvedValue(preview({
      visibility: { state: 'visible', visible: true, published: true, label: 'Visible', model_hidden: false, placement: 'card' },
      availability: { state: 'own_stock', label: 'Con existencias: entrega inmediata' },
    }));
    const dialog = await openPanel();
    expect(await within(dialog).findByTestId('visibility-banner')).toHaveTextContent('Visible');
    expect(within(dialog).queryByRole('button', { name: 'Publicar en e-commerce' })).toBeNull();
    mAdmin.fetchProductPreview.mockResolvedValue(preview());
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ocultar del e-commerce' }));
    await waitFor(() => expect(mAdmin.bulkSetProductVisibility).toHaveBeenCalledWith('hide', { ids: [1] }, 7));
    expect(await within(dialog).findByText('Oculto de la tienda en línea.')).toBeInTheDocument();
  });

  it('bajo pedido muestra su plazo', async () => {
    mAdmin.fetchProductPreview.mockResolvedValue(preview({
      availability: { state: 'supplier', label: 'Bajo pedido: entrega en 4 días' },
    }));
    const dialog = await openPanel();
    expect(await within(dialog).findByText('Bajo pedido: entrega en 4 días')).toBeInTheDocument();
  });

  it('un seminuevo no enseña un precio de tarjeta y lista sus unidades con el motivo', async () => {
    mAdmin.fetchProductPreview.mockResolvedValue(preview({
      visibility: { state: 'hidden', visible: false, published: false, label: 'Oculto: el cliente no lo ve', model_hidden: true, placement: 'model_page' },
      availability: { state: 'own_stock', label: '1 equipo a la venta, entrega inmediata', offered_count: 1 },
      offered_units: [
        { label_code: 'BD-0001', offered: true, reason: '', reason_label: '', price: '2000.00', condition: 'used', battery_health: 90, cosmetic_grade: 'Excelente', photo_count: 2 },
        { label_code: 'BD-0002', offered: false, reason: 'no_sale_price', reason_label: 'Falta el precio de este equipo', price: null, condition: 'used', battery_health: null, cosmetic_grade: null, photo_count: 0 },
      ],
    }));
    const dialog = await openPanel();
    const card = await within(dialog).findByRole('article', { name: 'Tarjeta del cliente' });
    expect(within(card).getByText('Precio por equipo')).toBeInTheDocument();
    expect(within(card).getByText(/Su modelo está oculto/)).toBeInTheDocument();
    expect(within(dialog).getByText('Unidades ofrecidas')).toBeInTheDocument();
    expect(within(dialog).getByText('BD-0001')).toBeInTheDocument();
    expect(within(dialog).getByText('BD-0002')).toBeInTheDocument();
    expect(within(dialog).getByText('Falta el precio de este equipo')).toBeInTheDocument();
    expect(within(dialog).getByText(/S\/ 2000.00 · Excelente · Batería 90% · 2 fotos/)).toBeInTheDocument();
    expect(dialog.textContent).not.toMatch(/\d{15}/); // no IMEI-looking number anywhere
  });
});

describe('Publicar desde la ficha', () => {
  it('con pendientes no se puede publicar y se dice por qué', async () => {
    const dialog = await openPanel();
    const button = await within(dialog).findByRole('button', { name: 'Publicar en e-commerce' });
    expect(button).toBeDisabled();
    expect(within(dialog).getByText('Completa lo que falta para poder publicarlo.')).toBeInTheDocument();
    fireEvent.click(button);
    expect(mAdmin.bulkSetProductVisibility).not.toHaveBeenCalled();
  });

  it('listo: publica con un id por la acción en bloque y refleja el nuevo estado', async () => {
    mAdmin.fetchProductPreview.mockResolvedValueOnce(preview());
    const dialog = await openPanel();
    const button = await within(dialog).findByRole('button', { name: 'Publicar en e-commerce' });
    await waitFor(() => expect(button).toBeEnabled());
    mAdmin.fetchProductPreview.mockResolvedValue(preview({
      visibility: { state: 'visible', visible: true, published: true, label: 'Visible', model_hidden: false, placement: 'card' },
    }));
    fireEvent.click(button);
    await waitFor(() => expect(mAdmin.bulkSetProductVisibility).toHaveBeenCalledWith('publish', { ids: [1] }, 7));
    expect(await within(dialog).findByText('Publicado en la tienda en línea.')).toBeInTheDocument();
    expect(within(dialog).getByTestId('visibility-banner')).toHaveTextContent('Visible');
  });

  it('si el servidor lo rechaza, dice el motivo', async () => {
    mAdmin.fetchProductPreview.mockResolvedValue(preview());
    mAdmin.bulkSetProductVisibility.mockResolvedValue({
      action: 'publish', changed: 0, unchanged: 0, total: 1, refused: [{ id: 1, name: 'Producto 1', problems: ['no_category'] }],
    });
    const dialog = await openPanel();
    const button = await within(dialog).findByRole('button', { name: 'Publicar en e-commerce' });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    expect(await within(dialog).findByText(/No se publicó: no tiene categoría/)).toBeInTheDocument();
  });
});

describe('Listos para publicar', () => {
  const ready = [product(1, { name: 'Funda azul' }), product(2, { name: 'Cable USB-C' })];

  it('el filtro pide al servidor sólo los listos', async () => {
    render(<PublicationCentre canManage companyId={7} />);
    await screen.findByRole('table', { name: 'Productos y variantes' });
    listWith(ready);
    fireEvent.click(screen.getByRole('button', { name: 'Listos para publicar' }));
    await waitFor(() => expect(mAdmin.fetchAdminProducts).toHaveBeenLastCalledWith(
      expect.objectContaining({ ready_to_publish: 'true', company: 7 })));
    expect(screen.getByRole('button', { name: 'Listos para publicar' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByText('2 productos ocultos y listos para publicar.')).toBeInTheDocument();
  });

  it('confirma con el conteo exacto y nombres de muestra, y publica por el filtro con ese conteo', async () => {
    listWith(ready);
    render(<PublicationCentre canManage companyId={7} />);
    await screen.findByRole('table', { name: 'Productos y variantes' });
    fireEvent.click(screen.getByRole('button', { name: 'Listos para publicar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Publicar listos (2)' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Publicar 2 productos en e-commerce')).toBeInTheDocument();
    const names = within(within(dialog).getByRole('list', { name: 'Productos que se publicarán' })).getAllByRole('listitem');
    expect(names.map((n) => n.textContent)).toEqual(['Funda azul', 'Cable USB-C']);
    expect(mAdmin.bulkSetProductVisibility).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirmar' }));
    await waitFor(() => expect(mAdmin.bulkSetProductVisibility).toHaveBeenCalledWith(
      'publish', { filter: { search: undefined, ready_to_publish: 'true' }, expectedCount: 2 }, 7));
  });

  it('con más de los que caben en la muestra dice cuántos más', async () => {
    const many = Array.from({ length: 12 }, (_, i) => product(100 + i, { name: `Lote ${i + 1}` }));
    listWith(many, 40);
    render(<PublicationCentre canManage companyId={7} />);
    await screen.findByRole('table', { name: 'Productos y variantes' });
    fireEvent.click(screen.getByRole('button', { name: 'Listos para publicar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Publicar listos (40)' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Algunos de los 40:')).toBeInTheDocument();
    expect(within(dialog).getByText('… y 32 más')).toBeInTheDocument();
    // The count the operator saw (40), not the 12 rows loaded on this page.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirmar' }));
    await waitFor(() => expect(mAdmin.bulkSetProductVisibility).toHaveBeenCalledWith(
      'publish', { filter: { search: undefined, ready_to_publish: 'true' }, expectedCount: 40 }, 7));
  });

  it('si la selección cambió, no se publicó nada y se avisa', async () => {
    mAdmin.bulkSetProductVisibility.mockRejectedValue(new admin.SelectionChangedError(3));
    listWith(ready);
    render(<PublicationCentre canManage companyId={7} />);
    await screen.findByRole('table', { name: 'Productos y variantes' });
    fireEvent.click(screen.getByRole('button', { name: 'Listos para publicar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Publicar listos (2)' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirmar' }));
    expect(await screen.findByText(/ahora coinciden 3 productos/)).toBeInTheDocument();
  });

  it('quien sólo ve no tiene «Publicar listos»', async () => {
    listWith(ready);
    render(<PublicationCentre canManage={false} companyId={7} />);
    await screen.findByRole('table', { name: 'Productos y variantes' });
    fireEvent.click(screen.getByRole('button', { name: 'Listos para publicar' }));
    await screen.findByText('2 productos ocultos y listos para publicar.');
    expect(screen.queryByRole('button', { name: /Publicar listos/ })).toBeNull();
  });
});

describe('validatePrice', () => {
  it.each([['', false], ['0', false], ['-3', false], ['abc', false], ['1.234', false], ['10', true], ['10.5', true], ['99,90', true]])(
    '%p -> válido %p', (raw, ok) => {
      expect(validatePrice(raw) === null).toBe(ok);
    });
});
