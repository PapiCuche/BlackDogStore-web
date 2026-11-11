import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ProductGallery } from '@/app/admin/components/ProductGallery';
import * as admin from '@/app/lib/admin';

/**
 * CATALOG-VARIANT-IMAGES · la galería con vista previa antes de subir.
 *
 * Con `stageBeforeUpload` lo elegido NO se sube al soltarlo: se ve primero (sobre un fondo de cuadros, para que la
 * transparencia se note), se puede quitar, y un solo botón sube todo al producto que corresponde. Sin esa opción la
 * galería se comporta como siempre (product-gallery.test.tsx).
 */

jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  uploadProductImage: jest.fn(),
  patchProductImage: jest.fn(),
  deleteProductImage: jest.fn(),
  reorderProductImages: jest.fn(),
}));

const api = admin as jest.Mocked<typeof admin>;

const image = (id: number, extra: Partial<admin.AdminProductImage> = {}): admin.AdminProductImage => ({
  id, url: `/api/storefront/images/${String(id).padStart(32, '0')}`, alt_text: '',
  is_primary: false, sort_order: id, width: 120, height: 80, ...extra,
});

const file = (name: string, type = 'image/png', bytes = 2048) => new File([new Uint8Array(bytes)], name, { type });

let previews = 0;
beforeEach(() => {
  jest.clearAllMocks();
  previews = 0;
  URL.createObjectURL = jest.fn(() => `blob:vista-${++previews}`);
  URL.revokeObjectURL = jest.fn();
});

function choose(files: File[]) {
  fireEvent.change(screen.getByLabelText(/Elegir imágenes/), { target: { files } });
}

const staged = () => screen.getByRole('list', { name: 'Imágenes por subir' });

describe('vista previa antes de subir', () => {
  it('lo elegido se ve sobre cuadros, con nombre y tamaño, y no se sube todavía', () => {
    render(<ProductGallery productId={13} productName="Variante A" images={[]} onChange={jest.fn()} stageBeforeUpload />);

    choose([file('recorte.png'), file('foto.jpg', 'image/jpeg', 3 * 1024 * 1024)]);

    expect(api.uploadProductImage).not.toHaveBeenCalled();
    const items = within(staged()).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    const preview = within(items[0]).getByRole('img', { name: 'Vista previa de recorte.png' });
    expect(preview).toHaveAttribute('src', 'blob:vista-1');
    // El fondo de cuadros: una imagen transparente se ve transparente, nunca sobre blanco.
    expect(preview.parentElement).toHaveAttribute('data-transparency-grid', 'true');
    expect(items[0]).toHaveTextContent('2 KB');
    expect(items[1]).toHaveTextContent('foto.jpg');
    expect(items[1]).toHaveTextContent('3.0 MB');
    expect(screen.getByRole('button', { name: 'Subir 2 imágenes' })).toBeEnabled();
  });

  it('«Quitar» saca el archivo de la lista y libera su vista previa', () => {
    render(<ProductGallery productId={13} productName="Variante A" images={[]} onChange={jest.fn()} stageBeforeUpload />);
    choose([file('a.png'), file('b.png')]);

    fireEvent.click(screen.getByRole('button', { name: 'Quitar a.png de la lista' }));

    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:vista-1');
    expect(within(staged()).getAllByRole('listitem')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Subir 1 imagen' })).toBeInTheDocument();
  });

  it('al cerrar, las vistas previas pendientes se liberan', () => {
    const view = render(<ProductGallery productId={13} productName="Variante A" images={[]} onChange={jest.fn()} stageBeforeUpload />);
    choose([file('a.png')]);
    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:vista-1');
  });

  it('«Subir» manda cada archivo al producto de la galería, con la empresa de la pantalla', async () => {
    const onChange = jest.fn();
    api.uploadProductImage
      .mockResolvedValueOnce(image(1, { is_primary: true }))
      .mockResolvedValueOnce(image(2));
    render(<ProductGallery productId={13} productName="Variante A" images={[]} onChange={onChange} stageBeforeUpload companyId={4} />);
    choose([file('a.png'), file('b.webp', 'image/webp')]);

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir 2 imágenes' })); });

    await waitFor(() => expect(api.uploadProductImage).toHaveBeenCalledTimes(2));
    expect(api.uploadProductImage.mock.calls.map(([id, f, , company]) => [id, (f as File).name, company]))
      .toEqual([[13, 'a.png', 4], [13, 'b.webp', 4]]);
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ id: 1, is_primary: true }), expect.objectContaining({ id: 2 }),
    ]));
    expect(screen.queryByRole('list', { name: 'Imágenes por subir' })).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:vista-1');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:vista-2');
  });

  it('lo que no es PNG, JPEG o WebP no llega a la lista', () => {
    render(<ProductGallery productId={13} productName="Variante A" images={[]} onChange={jest.fn()} stageBeforeUpload />);

    choose([file('manual.pdf', 'application/pdf'), file('foto.gif', 'image/gif')]);

    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(screen.queryByRole('list', { name: 'Imágenes por subir' })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('manual.pdf');
    expect(screen.getByRole('alert')).toHaveTextContent('PNG, JPEG o WebP');
  });

  it('el error del servidor se ve en su archivo, y los demás se suben igual', async () => {
    api.uploadProductImage
      .mockRejectedValueOnce(new Error('El archivo no es una imagen válida.'))
      .mockResolvedValueOnce(image(2, { is_primary: true }));
    render(<ProductGallery productId={13} productName="Variante A" images={[]} onChange={jest.fn()} stageBeforeUpload />);
    choose([file('rota.png'), file('buena.png')]);

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir 2 imágenes' })); });

    await waitFor(() => expect(api.uploadProductImage).toHaveBeenCalledTimes(2));
    const items = within(staged()).getAllByRole('listitem');
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent('rota.png');
    expect(items[0]).toHaveTextContent('El archivo no es una imagen válida.');
    expect(screen.getByRole('button', { name: 'Subir 1 imagen' })).toBeEnabled();
  });

  it('las imágenes ya subidas también van sobre cuadros, y la principal se distingue', () => {
    render(<ProductGallery productId={13} productName="Variante A" images={[image(1), image(2, { is_primary: true })]} onChange={jest.fn()} stageBeforeUpload />);
    const items = screen.getAllByRole('listitem');
    expect(within(items[1]).getByText('Principal')).toBeInTheDocument();
    expect(within(items[0]).getByRole('img').parentElement).toHaveAttribute('data-transparency-grid', 'true');
  });

  it('acciones propias por imagen, sólo para quien edita', () => {
    const extra = (img: admin.AdminProductImage) => <button type="button">Extra {img.id}</button>;
    const { rerender } = render(
      <ProductGallery productId={13} productName="Variante A" images={[image(1)]} onChange={jest.fn()} renderImageActions={extra} />,
    );
    expect(screen.getByRole('button', { name: 'Extra 1' })).toBeInTheDocument();
    rerender(<ProductGallery productId={13} productName="Variante A" images={[image(1)]} onChange={jest.fn()} renderImageActions={extra} readOnly />);
    expect(screen.queryByRole('button', { name: 'Extra 1' })).not.toBeInTheDocument();
  });
});

describe('sin vista previa (el comportamiento de siempre)', () => {
  it('sube al soltar, sin lista de pendientes ni botón «Subir»', async () => {
    api.uploadProductImage.mockResolvedValueOnce(image(1, { is_primary: true }));
    render(<ProductGallery productId={7} productName="Teléfono" images={[]} onChange={jest.fn()} />);

    await act(async () => { choose([file('a.png')]); });

    await waitFor(() => expect(api.uploadProductImage).toHaveBeenCalledWith(7, expect.any(File)));
    expect(screen.queryByRole('button', { name: /^Subir \d/ })).not.toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
