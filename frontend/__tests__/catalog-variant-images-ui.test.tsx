import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CatalogModelEditor } from '@/app/admin/components/CatalogModelEditor';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-VARIANT-IMAGES — «Modelos y variantes»: las imágenes de cada variante, y la portada del modelo.
 *   - Cada fila muestra la imagen principal de su variante y cuántas tiene.
 *   - «Imágenes» (sólo con permiso de gestión) abre la galería de ESA variante, con vista previa antes de subir.
 *   - «Usar como portada del modelo» guarda esa imagen como la del modelo.
 *   - Tras cada cambio se vuelve a leer el modelo: miniaturas y recuentos al día.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const address = (n: number) => `/api/storefront/images/${String(n).padStart(32, '0')}`;

const row = (product_id: number, name: string, extra: Record<string, unknown> = {}) => ({
  product_id, name, slug: `v-${product_id}`, condition_line: 'new', price: '100.00', is_serialized: false, stock: 0,
  is_published_online: true, history: { sold: 0, units: 0, reserved: 0 }, identity_change: 'free',
  line_change: 'free', removal: 'free', options: [], image_url: '', image_count: 0, ...extra,
});

let model: Record<string, unknown>;
let galleries: Record<number, Record<string, unknown>[]>;
let writes: { method: string; path: string; body: unknown }[];
let nextId: number;

beforeEach(() => {
  writes = [];
  nextId = 100;
  galleries = { 13: [], 14: [{ id: 7, url: address(7), alt_text: '', is_primary: true, sort_order: 0, width: 10, height: 10 }] };
  model = {
    id: 5, name: 'Teléfono X', slug: 'telefono-x', category_id: null, category_name: '', image_url: '',
    description: '', is_published_online: true, sort_order: 0, variant_count: 2, used_unit_count: 0,
    variants: [row(13, 'Variante A'), row(14, 'Variante B', { image_url: address(7), image_count: 1 })],
  };
  URL.createObjectURL = jest.fn(() => 'blob:vista');
  URL.revokeObjectURL = jest.fn();
  window.history.replaceState(null, '', '/admin/products/models/5');
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const path = url.replace(/^.*\/api/, '');
    const method = (init.method ?? 'GET').toUpperCase();
    const images = path.match(/\/admin\/products\/(\d+)\/images\//);
    if (method !== 'GET') {
      const body = init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}'));
      writes.push({ method, path, body });
      if (images && method === 'POST') {
        const id = nextId++;
        const created = { id, url: address(id), alt_text: '', is_primary: false, sort_order: id, width: 10, height: 10 };
        const pid = Number(images[1]);
        created.is_primary = galleries[pid].length === 0;
        galleries[pid] = [...galleries[pid], created];
        // The server's mirror: the variant row now carries its primary and its count.
        model = {
          ...model,
          variants: (model.variants as Record<string, unknown>[]).map((v) => (v.product_id === pid
            ? { ...v, image_count: galleries[pid].length, image_url: galleries[pid].find((g) => g.is_primary)?.url ?? '' }
            : v)),
        };
        return reply(created, 201);
      }
      if (path.startsWith('/admin/catalog/models/5/') && method === 'PATCH') {
        model = { ...model, ...(body as Record<string, unknown>) };
        return reply(model);
      }
      return reply({}, 404);
    }
    if (images) return reply({ results: galleries[Number(images[1])] ?? [] });
    if (path.startsWith('/admin/catalog/models/5/')) return reply(model);
    if (path.startsWith('/admin/catalog/attributes/')) return reply([]);
    if (path.startsWith('/admin/catalog/settings/')) return reply({ new_line_label: '', used_line_label: '' });
    if (path.startsWith('/admin/categories/')) return reply([]);
    return reply({}, 404);
  });
});

async function mount(canManage = true, companyId: number | null = null) {
  const view = render(<CatalogModelEditor modelId={5} canManage={canManage} companyId={companyId} />);
  await screen.findByRole('heading', { name: 'Variantes' });
  return view;
}

const modelReads = () => send.mock.calls.filter(([input, init]) => (
  String(input).includes('/admin/catalog/models/5/') && !(init as RequestInit | undefined)?.method)).length;

describe('miniaturas de las variantes', () => {
  it('cada fila muestra su imagen principal y cuántas tiene; sin imagen, lo dice', async () => {
    await mount(false);
    const thumb = screen.getByRole('img', { name: 'Imagen de Variante B' });
    expect(thumb).toHaveAttribute('src', address(7));
    expect(thumb.parentElement).toHaveAttribute('data-transparency-grid', 'true');
    expect(screen.getByText('1 imagen')).toBeInTheDocument();
    expect(screen.getByText('Sin imágenes')).toBeInTheDocument();
  });

  it('quien sólo ve no tiene el botón «Imágenes»', async () => {
    await mount(false);
    expect(screen.queryByRole('button', { name: /^Imágenes de/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Usar como portada/ })).not.toBeInTheDocument();
  });
});

describe('galería de una variante', () => {
  it('«Imágenes» abre la galería de ESA variante, y lo subido va a su producto', async () => {
    await mount(true, 9);
    const before = modelReads();

    fireEvent.click(screen.getByRole('button', { name: 'Imágenes de Variante A' }));
    const panel = await screen.findByRole('region', { name: 'Imágenes de Variante A' });
    expect(screen.getByRole('button', { name: 'Imágenes de Variante A' })).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => expect(within(panel).getByText('Este producto todavía no tiene imágenes.')).toBeInTheDocument());

    fireEvent.change(within(panel).getByLabelText(/Elegir imágenes/), {
      target: { files: [new File([new Uint8Array(64)], 'recorte.png', { type: 'image/png' })] },
    });
    // Vista previa antes de subir.
    expect(within(panel).getByRole('img', { name: 'Vista previa de recorte.png' })).toHaveAttribute('src', 'blob:vista');
    expect(writes).toHaveLength(0);

    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Subir 1 imagen' })); });

    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].method).toBe('POST');
    expect(writes[0].path).toMatch(/^\/admin\/products\/13\/images\/\?company=9$/);
    // The model is read again: the row of A now shows its picture; B keeps its own.
    await waitFor(() => expect(modelReads()).toBeGreaterThan(before));
    await waitFor(() => expect(screen.getByRole('img', { name: 'Imagen de Variante A' })).toHaveAttribute('src', address(100)));
    expect(screen.getByRole('img', { name: 'Imagen de Variante B' })).toHaveAttribute('src', address(7));
    expect(galleries[14]).toHaveLength(1);
  });

  it('«Usar como portada del modelo» guarda esa imagen como la del modelo', async () => {
    await mount(true);
    fireEvent.click(screen.getByRole('button', { name: 'Imágenes de Variante B' }));
    const panel = await screen.findByRole('region', { name: 'Imágenes de Variante B' });
    const use = await within(panel).findByRole('button', { name: 'Usar como portada del modelo: imagen 1' });

    await act(async () => { fireEvent.click(use); });

    const patch = writes.find((w) => w.method === 'PATCH');
    expect(patch?.path).toMatch(/^\/admin\/catalog\/models\/5\//);
    expect(patch?.body).toEqual({ image_url: address(7) });
    expect(await screen.findByText('Portada del modelo actualizada.')).toBeInTheDocument();
    expect(within(panel).getByText('Portada del modelo')).toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: /Usar como portada/ })).not.toBeInTheDocument();
    // The model's own cover now shows it.
    expect(screen.getByRole('img', { name: 'Imagen de Teléfono X' })).toHaveAttribute('src', address(7));
  });

  it('se cierra y devuelve el foco al botón', async () => {
    await mount(true);
    const open = screen.getByRole('button', { name: 'Imágenes de Variante A' });
    fireEvent.click(open);
    const panel = await screen.findByRole('region', { name: 'Imágenes de Variante A' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Cerrar imágenes de Variante A' }));
    expect(screen.queryByRole('region', { name: 'Imágenes de Variante A' })).not.toBeInTheDocument();
    expect(open).toHaveFocus();
  });
});

describe('cambiar de variante con la galería abierta', () => {
  it('una respuesta tardía de la variante anterior no llena la galería de la actual', async () => {
    let finishA: (value: Response) => void = () => {};
    const base = send.getMockImplementation()!;
    send.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/admin/products/13/images/') && !init?.method) {
        return new Promise<Response>((resolve) => { finishA = resolve; });
      }
      return base(input, init);
    });
    await mount(true);
    fireEvent.click(screen.getByRole('button', { name: 'Imágenes de Variante A' }));
    fireEvent.click(screen.getByRole('button', { name: 'Imágenes de Variante B' }));
    const panel = await screen.findByRole('region', { name: 'Imágenes de Variante B' });
    await within(panel).findByRole('button', { name: 'Usar como portada del modelo: imagen 1' });

    await act(async () => {
      finishA(reply({ results: [{ id: 99, url: address(99), alt_text: '', is_primary: true, sort_order: 0, width: 1, height: 1 }] }));
    });

    expect(within(panel).getAllByRole('img').map((img) => img.getAttribute('src'))).toEqual([address(7)]);
  });
});
