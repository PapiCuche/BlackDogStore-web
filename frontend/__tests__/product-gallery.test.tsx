import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ProductGallery } from '@/app/admin/components/ProductGallery';
import { ProductForm } from '@/app/admin/components/ProductForm';
import * as admin from '@/app/lib/admin';

/**
 * PRODUCT-MEDIA · la galería de un producto en el panel.
 *
 * El servidor decide: qué es una imagen, cuántas caben, cuál es la principal.
 * Aquí se fija lo que el panel le pide y lo que le enseña a quien edita —
 * incluido lo que sale mal, archivo por archivo.
 */

jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  uploadProductImage: jest.fn(),
  patchProductImage: jest.fn(),
  deleteProductImage: jest.fn(),
  reorderProductImages: jest.fn(),
  createAdminProduct: jest.fn(),
  patchAdminProduct: jest.fn(),
}));

const api = admin as jest.Mocked<typeof admin>;

const image = (id: number, extra: Partial<admin.AdminProductImage> = {}): admin.AdminProductImage => ({
  id, url: `/api/storefront/images/${String(id).padStart(32, '0')}`, alt_text: '',
  is_primary: false, sort_order: id, width: 120, height: 80, ...extra,
});

const file = (name: string, type = 'image/png', bytes = 10) =>
  new File([new Uint8Array(bytes)], name, { type });

const product: admin.AdminProduct = {
  id: 7, name: 'Teléfono', slug: 'telefono', description: '', price: '100.00', inventory: 3,
  image_url: '', category_id: null, category_name: null, is_active: true,
  created_at: '', updated_at: '', images: [],
};

beforeEach(() => {
  jest.clearAllMocks();
  URL.createObjectURL = jest.fn(() => 'blob:vista-previa');
  URL.revokeObjectURL = jest.fn();
});

function choose(files: File[]) {
  fireEvent.change(screen.getByLabelText(/Elegir imágenes/), { target: { files } });
}

describe('galería guardada', () => {
  it('muestra las imágenes en orden y dice cuál es la principal', () => {
    render(<ProductGallery productId={7} productName="Teléfono" images={[image(1, { is_primary: true, alt_text: 'Frontal' }), image(2)]} onChange={jest.fn()} />);

    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText('Principal')).toBeInTheDocument();
    expect(within(items[0]).getByRole('img')).toHaveAttribute('alt', 'Frontal');
    // Sin texto alternativo, la imagen se describe con el nombre del producto.
    expect(within(items[1]).getByRole('img')).toHaveAttribute('alt', 'Teléfono');
    expect(within(items[1]).getByRole('button', { name: 'Marcar como principal: imagen 2' })).toBeEnabled();
  });

  it('sube varios archivos, uno tras otro, y los añade a la galería', async () => {
    const onChange = jest.fn();
    api.uploadProductImage
      .mockResolvedValueOnce(image(1, { is_primary: true }))
      .mockResolvedValueOnce(image(2));
    render(<ProductGallery productId={7} productName="Teléfono" images={[]} onChange={onChange} />);

    await act(async () => { choose([file('a.png'), file('b.png')]); });

    await waitFor(() => expect(api.uploadProductImage).toHaveBeenCalledTimes(2));
    expect(api.uploadProductImage.mock.calls.map(([id, f]) => [id, (f as File).name])).toEqual([[7, 'a.png'], [7, 'b.png']]);
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ id: 1, is_primary: true }), expect.objectContaining({ id: 2 }),
    ]));
  });

  it('lo que no es una imagen admitida no se envía, y se explica', async () => {
    render(<ProductGallery productId={7} productName="Teléfono" images={[]} onChange={jest.fn()} />);

    await act(async () => { choose([file('manual.pdf', 'application/pdf'), file('enorme.png', 'image/png', 9 * 1024 * 1024)]); });

    expect(api.uploadProductImage).not.toHaveBeenCalled();
    const problems = screen.getByRole('alert');
    expect(problems).toHaveTextContent('manual.pdf');
    expect(problems).toHaveTextContent('PNG, JPEG o WebP');
    expect(problems).toHaveTextContent('enorme.png');
    expect(problems).toHaveTextContent('8 MB');
  });

  it('un archivo que el servidor rechaza no detiene a los demás, y se puede reintentar', async () => {
    api.uploadProductImage
      .mockRejectedValueOnce(new Error('La imagen está incompleta o dañada.'))
      .mockResolvedValueOnce(image(2, { is_primary: true }))
      .mockResolvedValueOnce(image(3));
    const onChange = jest.fn();
    render(<ProductGallery productId={7} productName="Teléfono" images={[]} onChange={onChange} />);

    await act(async () => { choose([file('rota.png'), file('buena.png')]); });
    await waitFor(() => expect(api.uploadProductImage).toHaveBeenCalledTimes(2));

    expect(await screen.findByText(/rota\.png/)).toBeInTheDocument();
    expect(screen.getByText('La imagen está incompleta o dañada.')).toBeInTheDocument();
    expect(onChange).toHaveBeenLastCalledWith([expect.objectContaining({ id: 2 })]);

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reintentar rota.png' })); });
    await waitFor(() => expect(api.uploadProductImage).toHaveBeenCalledTimes(3));
  });

  it('arrastrar archivos sobre la zona los sube', async () => {
    api.uploadProductImage.mockResolvedValueOnce(image(1, { is_primary: true }));
    render(<ProductGallery productId={7} productName="Teléfono" images={[]} onChange={jest.fn()} />);

    await act(async () => {
      fireEvent.drop(screen.getByTestId('image-dropzone'), { dataTransfer: { files: [file('a.png')] } });
    });

    await waitFor(() => expect(api.uploadProductImage).toHaveBeenCalledTimes(1));
  });

  it('elegir otra principal lo pide al servidor y actualiza la galería', async () => {
    const onChange = jest.fn();
    api.patchProductImage.mockResolvedValueOnce(image(2, { is_primary: true }));
    render(<ProductGallery productId={7} productName="Teléfono" images={[image(1, { is_primary: true }), image(2)]} onChange={onChange} />);

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Marcar como principal: imagen 2' })); });

    expect(api.patchProductImage).toHaveBeenCalledWith(7, 2, { is_primary: true });
    expect(onChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ id: 1, is_primary: false }), expect.objectContaining({ id: 2, is_primary: true }),
    ]);
  });

  it('el texto alternativo se guarda al salir del campo, sólo si cambió', async () => {
    api.patchProductImage.mockResolvedValueOnce(image(1, { is_primary: true, alt_text: 'Vista frontal' }));
    render(<ProductGallery productId={7} productName="Teléfono" images={[image(1, { is_primary: true })]} onChange={jest.fn()} />);
    const field = screen.getByLabelText('Texto alternativo de la imagen 1');

    fireEvent.blur(field);
    expect(api.patchProductImage).not.toHaveBeenCalled();

    fireEvent.change(field, { target: { value: 'Vista frontal' } });
    await act(async () => { fireEvent.blur(field); });
    expect(api.patchProductImage).toHaveBeenCalledWith(7, 1, { alt_text: 'Vista frontal' });
  });

  it('quitar pide confirmación y, si era la principal, toma la que el servidor eligió', async () => {
    const onChange = jest.fn();
    api.deleteProductImage.mockResolvedValueOnce([image(2, { is_primary: true })]);
    render(<ProductGallery productId={7} productName="Teléfono" images={[image(1, { is_primary: true }), image(2)]} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: 'Quitar imagen 1' }));
    expect(api.deleteProductImage).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Sí, quitar' })); });

    expect(api.deleteProductImage).toHaveBeenCalledWith(7, 1);
    expect(onChange).toHaveBeenLastCalledWith([expect.objectContaining({ id: 2, is_primary: true })]);
  });

  it('se reordena con botones, también con teclado', async () => {
    const onChange = jest.fn();
    api.reorderProductImages.mockResolvedValueOnce([image(2), image(1, { is_primary: true })]);
    render(<ProductGallery productId={7} productName="Teléfono" images={[image(1, { is_primary: true }), image(2)]} onChange={onChange} />);

    expect(screen.getByRole('button', { name: 'Mover imagen 1 antes' })).toBeDisabled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Mover imagen 1 después' })); });

    expect(api.reorderProductImages).toHaveBeenCalledWith(7, [2, 1]);
    expect(onChange).toHaveBeenLastCalledWith([expect.objectContaining({ id: 2 }), expect.objectContaining({ id: 1 })]);
  });

  it('quien sólo puede ver no tiene controles de edición', () => {
    render(<ProductGallery productId={7} productName="Teléfono" images={[image(1, { is_primary: true })]} onChange={jest.fn()} readOnly />);
    expect(screen.queryByLabelText(/Elegir imágenes/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Quitar/ })).not.toBeInTheDocument();
    expect(screen.getByRole('img')).toBeInTheDocument();
  });
});

describe('formulario de producto', () => {
  it('con galería no envía la dirección de la imagen ni ofrece escribirla', async () => {
    api.patchAdminProduct.mockResolvedValueOnce({ ...product, name: 'Otro' });
    const withGallery = { ...product, image_url: image(1).url, images: [image(1, { is_primary: true })] };
    render(<ProductForm product={withGallery} categories={[]} onSaved={jest.fn()} />);

    expect(screen.queryByLabelText(/Dirección de imagen externa/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Otro' } });
    await act(async () => { fireEvent.submit(screen.getByRole('button', { name: /Guardar/ }).closest('form')!); });

    expect(api.patchAdminProduct).toHaveBeenCalledTimes(1);
    expect(api.patchAdminProduct.mock.calls[0][1]).not.toHaveProperty('image_url');
  });

  it('sin galería, una ruta del sitio es una dirección válida', async () => {
    api.patchAdminProduct.mockResolvedValueOnce(product);
    render(<ProductForm product={product} categories={[]} onSaved={jest.fn()} />);
    const field = screen.getByLabelText(/Dirección de imagen externa/);

    // `type="url"` rechazaba en el navegador lo que el servidor acepta.
    expect(field).toHaveAttribute('type', 'text');
    fireEvent.change(field, { target: { value: '/assets/products/telefono.png' } });
    await act(async () => { fireEvent.submit(field.closest('form')!); });

    expect(api.patchAdminProduct.mock.calls[0][1]).toMatchObject({ image_url: '/assets/products/telefono.png' });
  });

  it('al crear, sube las imágenes elegidas después de crear el producto', async () => {
    const onSaved = jest.fn();
    api.createAdminProduct.mockResolvedValueOnce({ ...product, id: 9 });
    api.uploadProductImage.mockResolvedValueOnce(image(1, { is_primary: true })).mockResolvedValueOnce(image(2));
    render(<ProductForm categories={[]} onSaved={onSaved} />);

    fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Teléfono' } });
    fireEvent.change(screen.getByLabelText(/Precio/), { target: { value: '100' } });
    choose([file('a.png'), file('b.png')]);
    expect(screen.getByText('a.png')).toBeInTheDocument();
    expect(api.uploadProductImage).not.toHaveBeenCalled();

    await act(async () => { fireEvent.submit(screen.getByRole('button', { name: /Crear producto/ }).closest('form')!); });

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: 9 })));
    expect(api.createAdminProduct.mock.calls[0][0]).not.toHaveProperty('image_url');
    expect(api.uploadProductImage.mock.calls.map(([id, f]) => [id, (f as File).name])).toEqual([[9, 'a.png'], [9, 'b.png']]);
  });

  it('si una imagen falla al crear, lo dice y no finge que todo salió bien', async () => {
    const onSaved = jest.fn();
    api.createAdminProduct.mockResolvedValueOnce({ ...product, id: 9 });
    api.uploadProductImage.mockRejectedValueOnce(new Error('La imagen está incompleta o dañada.'));
    render(<ProductForm categories={[]} onSaved={onSaved} />);

    fireEvent.change(screen.getByLabelText(/Nombre/), { target: { value: 'Teléfono' } });
    fireEvent.change(screen.getByLabelText(/Precio/), { target: { value: '100' } });
    choose([file('rota.png')]);
    await act(async () => { fireEvent.submit(screen.getByRole('button', { name: /Crear producto/ }).closest('form')!); });

    expect(await screen.findByRole('alert')).toHaveTextContent('El producto se creó');
    expect(screen.getByRole('alert')).toHaveTextContent('rota.png');
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: 'Abrir el producto' })).toHaveAttribute('href', '/admin/products/9');
  });
});

describe('lista de productos del panel', () => {
  /* eslint-disable-next-line @typescript-eslint/no-require-imports */
  const { ProductsTable } = require('@/app/admin/components/ProductsTable');

  it('enseña la imagen principal de cada producto, o un hueco neutro', () => {
    render(<ProductsTable
      canManage={false} onChanged={jest.fn()}
      products={[
        { ...product, id: 1, name: 'Con foto', image_url: image(1).url },
        { ...product, id: 2, name: 'Sin foto', image_url: '' },
      ]}
    />);

    const rows = screen.getAllByRole('row').slice(1);
    // Decorativa: el nombre del producto ya está al lado.
    const thumb = rows[0].querySelector('img') as HTMLImageElement;
    expect(thumb.getAttribute('src')).toBe(image(1).url);
    expect(thumb).toHaveAttribute('alt', '');
    expect(rows[1].querySelector('img')).toBeNull();
    expect(rows[1].querySelector('[data-thumb="empty"]')).not.toBeNull();
  });
});


describe('zona para soltar archivos', () => {
  /* eslint-disable-next-line @typescript-eslint/no-require-imports */
  const { ImageDropzone } = require('@/app/admin/components/ImageDropzone');

  it('deshabilitada no entrega nada, pero tampoco deja que el navegador abra el archivo', () => {
    // Sin `preventDefault`, soltar una foto sobre la zona hace que la pestaña
    // navegue a la imagen: se pierde la subida en curso y lo que había en cola.
    const onFiles = jest.fn();
    render(<ImageDropzone label="Elegir fotos" accept="image/png" onFiles={onFiles} disabled />);
    const zone = screen.getByTestId('image-dropzone');

    const over = new Event('dragover', { bubbles: true, cancelable: true });
    zone.dispatchEvent(over);
    const drop = new Event('drop', { bubbles: true, cancelable: true });
    Object.defineProperty(drop, 'dataTransfer', { value: { files: [file('a.png')] } });
    zone.dispatchEvent(drop);

    expect(over.defaultPrevented).toBe(true);
    expect(drop.defaultPrevented).toBe(true);
    expect(onFiles).not.toHaveBeenCalled();
  });
});
