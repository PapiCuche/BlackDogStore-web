import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ImageHealthPanel, healthCsv } from '@/app/admin/components/ImageHealthPanel';
import { ProductGallery } from '@/app/admin/components/ProductGallery';
import { NOT_PUBLIC_NOTICE } from '@/app/admin/components/ProductImageLicence';
import { ProductCard } from '@/app/components/ProductCard';
import ProductDetail, { modelPageHref } from '@/app/components/ProductDetail';
import * as internal from '@/app/admin/lib/internal-api';
import * as admin from '@/app/lib/admin';
import { productCanonicalPath, productJsonLd } from '@/app/lib/structured-data';

/**
 * IMG-GATE-01 — a device variant shows on the shop only pictures whose licence was verified.
 *
 * The server decides what is public (`public`, `requires_license`, `image_pending`); here: the panel says it and
 * verifies with the finish images' flow, the product page draws «Imagen pendiente» for a variant without a public
 * picture and points at the variant on its model's page, and IMG-HEALTH is shown read-only.
 */

jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  uploadProductImage: jest.fn(),
  patchProductImage: jest.fn(),
  deleteProductImage: jest.fn(),
  reorderProductImages: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchImageHealth: jest.fn(),
}));
jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(() => Promise.resolve(null)),
}));

const api = admin as jest.Mocked<typeof admin>;
const health = internal as jest.Mocked<typeof internal>;

const SOURCE: admin.AdminImageSource = {
  kind: 'unknown', kind_label: 'Sin registrar', url: '', license: '', attribution: '', is_demo: false,
  license_verified: false, license_verified_at: null, license_verified_by: null,
};

const image = (id: number, extra: Partial<admin.AdminProductImage> = {}): admin.AdminProductImage => ({
  id, url: `/api/storefront/images/${String(id).padStart(32, '0')}`, alt_text: '', is_primary: id === 1,
  sort_order: id, width: 120, height: 80, source: SOURCE, requires_license: true, public: false, ...extra,
});

beforeEach(() => {
  jest.clearAllMocks();
  global.fetch = jest.fn(() => Promise.resolve(new Response('[]'))) as unknown as typeof fetch;
});

describe('panel: licencia de una imagen de la galería', () => {
  it('una variante sin licencia verificada dice que la tienda no la muestra; un accesorio no', () => {
    render(<ProductGallery productId={7} productName="Teléfono" onChange={jest.fn()}
      images={[image(1), image(2, { requires_license: false, public: true })]} />);
    const [variant, accessory] = screen.getAllByRole('listitem');
    expect(within(variant).getByTestId('product-image-not-public')).toHaveTextContent(NOT_PUBLIC_NOTICE);
    expect(within(variant).getByText('Sin verificar')).toBeInTheDocument();
    expect(within(accessory).queryByTestId('product-image-not-public')).toBeNull();
  });

  it('con origen desconocido pide registrarlo antes de verificar, y guarda la procedencia', async () => {
    const onChange = jest.fn();
    api.patchProductImage.mockResolvedValueOnce(image(1, {
      source: { ...SOURCE, kind: 'supplier_authorized', kind_label: 'Material autorizado', license: 'Carta 2026' },
    }));
    render(<ProductGallery productId={7} productName="Teléfono" onChange={onChange} images={[image(1)]} />);
    expect(screen.queryByRole('button', { name: 'Verificar licencia: imagen 1' })).toBeNull();
    expect(screen.getByText(/Registra de dónde sale la imagen/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Registrar procedencia: imagen 1' }));
    fireEvent.change(screen.getByLabelText(/Origen de la imagen/), { target: { value: 'supplier_authorized' } });
    // A licence is required for anything but an own photo: refused here, before the trip.
    fireEvent.click(screen.getByRole('button', { name: 'Guardar procedencia' }));
    expect(api.patchProductImage).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Licencia o autorización/), { target: { value: 'Carta 2026' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar procedencia' })); });

    expect(api.patchProductImage).toHaveBeenCalledWith(7, 1, {
      source_kind: 'supplier_authorized', license: 'Carta 2026', source_url: '', attribution: '',
    });
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ id: 1, source: expect.objectContaining({ kind: 'supplier_authorized' }) }),
    ]));
  });

  it('«Verificar licencia» la verifica y la tienda pasa a mostrarla', async () => {
    const onChange = jest.fn();
    const known = image(1, { source: { ...SOURCE, kind: 'own_photo', kind_label: 'Foto propia' } });
    api.patchProductImage.mockResolvedValueOnce({
      ...known, public: true,
      source: { ...known.source!, license_verified: true, license_verified_by: 'Ana', license_verified_at: '2026-11-24T10:00:00Z' },
    });
    render(<ProductGallery productId={7} productName="Teléfono" onChange={onChange} images={[known]} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Verificar licencia: imagen 1' })); });
    expect(api.patchProductImage).toHaveBeenCalledWith(7, 1, { license_verified: true });
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith([expect.objectContaining({ id: 1, public: true })]));
  });

  it('verificada ofrece quitar la verificación; en sólo lectura no hay acciones', () => {
    const verified = image(1, {
      public: true, source: { ...SOURCE, kind: 'own_photo', kind_label: 'Foto propia', license_verified: true, license_verified_by: 'Ana' },
    });
    const { unmount } = render(<ProductGallery productId={7} productName="Teléfono" onChange={jest.fn()} images={[verified]} />);
    expect(screen.getByText(/Sí, por Ana/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Quitar la verificación de la licencia: imagen 1' })).toBeInTheDocument();
    expect(screen.queryByTestId('product-image-not-public')).toBeNull();
    unmount();
    render(<ProductGallery productId={7} productName="Teléfono" onChange={jest.fn()} images={[image(1)]} readOnly />);
    expect(screen.getByTestId('product-image-not-public')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /procedencia/ })).toBeNull();
  });

  it('una dirección de otro sitio no se puede verificar', () => {
    render(<ProductGallery productId={7} productName="Teléfono" onChange={jest.fn()}
      images={[image(1, { url: 'https://cdn.example.com/a.png' })]} />);
    expect(screen.getByText(/su licencia no se puede verificar/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Verificar licencia|procedencia/ })).toBeNull();
  });

  it('un servidor anterior (sin `source`) no muestra nada nuevo', () => {
    const old: admin.AdminProductImage = {
      id: 1, url: `/api/storefront/images/${'1'.padStart(32, '0')}`, alt_text: '', is_primary: true, sort_order: 1,
      width: 120, height: 80,
    };
    render(<ProductGallery productId={7} productName="Teléfono" onChange={jest.fn()} images={[old]} />);
    expect(screen.queryByTestId('product-image-licence')).toBeNull();
  });
});

describe('tienda: ficha de una variante', () => {
  const VARIANT = {
    id: 3, slug: 'tx-128-azul', name: 'Teléfono X 128 GB Azul', price: '3100.00', inventory: 2,
    product_model: 'telefono-x', condition_line: 'new' as const,
  };

  it('sin foto pública dibuja «Imagen pendiente», nunca la inicial', async () => {
    await act(async () => { render(<ProductDetail product={{ ...VARIANT, image_url: '', images: [], image_pending: true }} />); });
    expect(screen.getByTestId('image-pending')).toBeInTheDocument();
    expect(screen.queryByTestId('product-main-image')).toBeNull();
  });

  it('con la foto que manda el servidor la muestra', async () => {
    const url = '/api/storefront/images/' + 'a'.repeat(32);
    await act(async () => {
      render(<ProductDetail product={{ ...VARIANT, image_url: url, image_pending: false,
        images: [{ url, alt_text: '', is_primary: true, width: 10, height: 10 }] }} />);
    });
    expect(screen.getByTestId('product-main-image')).toBeInTheDocument();
    expect(screen.queryByTestId('image-pending')).toBeNull();
  });

  it('«Ver todas las opciones» abre la variante elegida en la página del modelo', async () => {
    await act(async () => { render(<ProductDetail product={{ ...VARIANT, image_pending: true }} />); });
    expect(screen.getByRole('link', { name: /Ver todas las opciones/ })).toHaveAttribute('href', '/modelo/telefono-x?variante=tx-128-azul');
    expect(modelPageHref({ ...VARIANT, condition_line: 'used' })).toBe('/modelo/telefono-x?line=used');
    expect(modelPageHref({ ...VARIANT, product_model: null })).toBeNull();
  });

  it('un producto sin modelo conserva su marcador de siempre', async () => {
    await act(async () => { render(<ProductDetail product={{ id: 4, slug: 'funda', name: 'Funda', price: '50', inventory: 1 }} />); });
    expect(screen.queryByTestId('image-pending')).toBeNull();
    expect(screen.queryByRole('link', { name: /Ver todas las opciones/ })).toBeNull();
  });

  it('la dirección canónica de una variante es su lugar en la página del modelo', () => {
    expect(productCanonicalPath(VARIANT)).toBe('/modelo/telefono-x?variante=tx-128-azul');
    expect(productCanonicalPath({ ...VARIANT, condition_line: 'used' })).toBe('/modelo/telefono-x');
    expect(productCanonicalPath({ slug: 'funda', product_model: null })).toBe('/product/funda');
  });

  it('los datos estructurados sólo llevan la foto que el servidor dejó pasar', () => {
    const data = productJsonLd({ ...VARIANT, image_url: '', images: [] }, 'https://tienda.example');
    expect(data.image).toBeUndefined();
  });

  it('la tarjeta del catálogo dibuja «Imagen pendiente» para una variante sin foto pública', () => {
    render(<ProductCard {...VARIANT} image_url="" image_pending />);
    expect(screen.getByTestId('image-pending')).toBeInTheDocument();
  });
});

describe('panel: salud de las imágenes (IMG-HEALTH)', () => {
  const REPORT: internal.ImageHealthReport = {
    summary: {
      broken_file: 1, external_hotlink: 1, unverified_public: 0, shared_across_colours: 0, variant_without_photo: 1,
      external_hosts: [{ host: 'cdn.example.com', images: 1 }], variants_hidden_by_gate: 1, storage_checked: true,
    },
    rows: [
      { issue: 'broken_file', slot: 'product_gallery', object_id: 5, object_name: 'Teléfono X 128 GB Azul', model_id: 2,
        model_name: 'Teléfono X', colour: '', host: '', public_id: 'a'.repeat(32), detail: 'missing_object',
        admin_url: '/admin/products/5' },
      { issue: 'external_hotlink', slot: 'product_image', object_id: 6, object_name: 'Teléfono, "Negro"', model_id: 2,
        model_name: 'Teléfono X', colour: '', host: 'cdn.example.com', public_id: '', detail: '',
        admin_url: '/admin/products/6' },
      { issue: 'variant_without_photo', slot: 'product_image', object_id: 5, object_name: 'Teléfono X 128 GB Azul',
        model_id: 2, model_name: 'Teléfono X', colour: '', host: '', public_id: '', detail: 'own_hidden',
        admin_url: '/admin/products/5' },
    ],
    count: 3, truncated: false,
  };

  it('no pide nada hasta «Revisar ahora»; luego muestra los contadores y cada fila con su ficha', async () => {
    health.fetchImageHealth.mockResolvedValueOnce(REPORT);
    render(<ImageHealthPanel companyId={4} />);
    expect(health.fetchImageHealth).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar ahora' })); });
    expect(health.fetchImageHealth).toHaveBeenCalledWith(4);
    const counters = screen.getByRole('list', { name: 'Problemas de imágenes' });
    expect(within(counters).getByText('Archivos rotos').nextSibling).toHaveTextContent('1');
    expect(screen.getByTestId('health-hidden-by-gate')).toHaveTextContent('1 variante tiene fotos propias');
    const rows = within(screen.getByRole('list', { name: 'Detalle de problemas' })).getAllByRole('listitem');
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText(/el archivo ya no está en el almacenamiento/)).toBeInTheDocument();
    expect(within(rows[1]).getByRole('link', { name: /Corregir/ })).toHaveAttribute('href', '/admin/products/6');
    expect(screen.getByRole('button', { name: 'Descargar CSV' })).toBeInTheDocument();
    // Nothing on this panel deletes.
    expect(screen.queryByRole('button', { name: /Borrar|Eliminar|Quitar/ })).toBeNull();
  });

  it('el CSV lleva las columnas del comando y escapa comillas y comas', () => {
    const text = healthCsv(REPORT.rows);
    const [header, , second] = text.trim().split('\n');
    expect(header).toBe('issue,slot,object_id,object_name,model_id,model_name,colour,host,public_id,detail,admin_url');
    expect(second).toContain('"Teléfono, ""Negro"""');
  });

  it('un fallo se dice y se puede reintentar', async () => {
    health.fetchImageHealth.mockRejectedValueOnce(new Error('Sin conexión.'));
    render(<ImageHealthPanel companyId={null} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Revisar ahora' })); });
    expect(screen.getByRole('alert')).toHaveTextContent('Sin conexión.');
    expect(screen.getByRole('button', { name: 'Revisar ahora' })).toBeEnabled();
  });
});
