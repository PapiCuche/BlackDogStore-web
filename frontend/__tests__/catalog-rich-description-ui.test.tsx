import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { CatalogModelEditor } from '@/app/admin/components/CatalogModelEditor';
import { ProductCard } from '@/app/components/ProductCard';
import ProductDetail from '@/app/components/ProductDetail';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-RICH-DESC-01 — dónde se ve la descripción con formato.
 *   - En «Modelos y variantes»: el detalle la muestra formateada, con la miniatura de la imagen y la dirección
 *     plegada; «Editar descripción» abre el editor; se guarda el Markdown tal cual y se vuelve a editar igual.
 *   - En la tienda: la ficha la pinta con títulos y listas; la tarjeta usa el texto sin marcas.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const DESCRIPTION = '**Apple iPhone 13 Pro Max**\n\n## Características principales\n\n- Face ID.\n- MagSafe.\n\n## Capacidades\n\n1. 128 GB\n2. 256 GB';
const IMAGE = `https://cdn.example.com/${'muy-larga/'.repeat(30)}iphone.png`;

const MODEL = {
  id: 5, name: 'iPhone 13 Pro Max', slug: 'iphone-13-pro-max', category_id: null, category_name: null, image_url: IMAGE,
  description: DESCRIPTION, is_published_online: true, sort_order: 0, variant_count: 0, used_unit_count: 0, variants: [],
};

let writes: { method: string; path: string; body: Record<string, unknown> }[];
let saved: Record<string, unknown>;

beforeEach(() => {
  writes = [];
  saved = { ...MODEL };
  window.history.replaceState(null, '', '/admin/products/models/5');
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      const body = JSON.parse(String(init.body ?? '{}'));
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body });
      saved = { ...saved, ...body };
      return reply(saved);
    }
    if (url.includes('/admin/catalog/models/5/')) return reply(saved);
    if (url.includes('/admin/catalog/attributes/')) return reply([]);
    if (url.includes('/admin/catalog/settings/')) return reply({ new_line_label: '', used_line_label: '' });
    if (url.includes('/admin/catalog/grades/')) return reply([]);
    if (url.includes('/admin/categories/')) return reply([]);
    return reply({}, 404);
  });
});

async function mount(canManage = true) {
  const view = render(<CatalogModelEditor modelId={5} canManage={canManage} companyId={null} />);
  await screen.findByTestId('model-description-preview');
  return view;
}

describe('detalle del modelo en el panel', () => {
  it('muestra la descripción con formato, sin marcas de Markdown', async () => {
    await mount();
    const box = screen.getByTestId('model-description-preview');
    expect(within(box).getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['Características principales', 'Capacidades']);
    expect(within(box).getByText('Apple iPhone 13 Pro Max').tagName).toBe('STRONG');
    expect(box.textContent).not.toMatch(/\*\*|##/);
  });

  it('una miniatura de la imagen y la dirección larga plegada, que se puede consultar', async () => {
    await mount();
    const img = screen.getByRole('img', { name: 'Imagen de iPhone 13 Pro Max' });
    expect(img).toHaveAttribute('src', IMAGE);
    const details = screen.getByText('Ver la dirección de la imagen').closest('details')!;
    expect(details).not.toHaveAttribute('open');
    expect(within(details).getByText(IMAGE)).toBeInTheDocument();
    expect(within(details).getByRole('link', { name: 'Abrir la imagen original' })).toHaveAttribute('href', IMAGE);
  });

  it('una dirección que no es http(s) ni del sitio no se usa como imagen', async () => {
    saved = { ...MODEL, image_url: 'javascript:alert(1)' };
    await mount();
    expect(screen.queryByRole('img', { name: /Imagen de/ })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Abrir la imagen original' })).toBeNull();
    expect(screen.getByText('La dirección no es una imagen que se pueda mostrar.')).toBeInTheDocument();
  });

  it('«Editar descripción» abre el editor; se guarda el Markdown tal cual y al volver a editar está igual', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Editar descripción' }));
    const form = screen.getByRole('form', { name: 'Editar modelo' });
    expect(within(form).getByRole('toolbar', { name: 'Formato de la descripción' })).toBeInTheDocument();
    const field = within(form).getByLabelText('Descripción');
    expect(field).toHaveValue(DESCRIPTION);
    const next = `${DESCRIPTION}\n\n---\n\n*Garantía de la tienda.*`;
    fireEvent.change(field, { target: { value: next } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar cambios' })); });
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ method: 'PATCH', path: '/admin/catalog/models/5/' });
    expect(writes[0].body.description).toBe(next);
    const box = await screen.findByTestId('model-description-preview');
    expect(within(box).getByText('Garantía de la tienda.').tagName).toBe('EM');
    expect(box.querySelector('hr')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Editar descripción' }));
    expect(within(screen.getByRole('form', { name: 'Editar modelo' })).getByLabelText('Descripción')).toHaveValue(next);
  });

  it('una descripción demasiado larga no se puede guardar', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Editar modelo' }));
    const form = screen.getByRole('form', { name: 'Editar modelo' });
    fireEvent.change(within(form).getByLabelText('Descripción'), { target: { value: 'x'.repeat(20001) } });
    expect(within(form).getByRole('button', { name: 'Guardar cambios' })).toBeDisabled();
    expect(within(form).getByText(/demasiado larga/)).toBeInTheDocument();
  });

  it('sin permiso de gestión se ve formateada, sin botones de edición', async () => {
    await mount(false);
    expect(screen.queryByRole('button', { name: 'Editar descripción' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Editar modelo' })).toBeNull();
    expect(screen.getByTestId('model-description-preview').querySelector('ol')).not.toBeNull();
  });
});

describe('tienda', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {} }),
    });
    class Observer { observe() {} unobserve() {} disconnect() {} }
    (window as unknown as { IntersectionObserver: unknown }).IntersectionObserver = Observer;
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = Observer;
  });

  it('la ficha del producto pinta títulos y listas; el nombre sigue siendo el único h1', () => {
    render(<ProductDetail product={{ id: 7, slug: 'p-7', name: 'iPhone 13 Pro Max 128 GB', price: 3999, inventory: 3, description: DESCRIPTION }} />);
    const box = screen.getByTestId('product-description');
    expect(within(box).getAllByRole('heading', { level: 2 })).toHaveLength(2);
    expect(within(box).getAllByRole('listitem')).toHaveLength(4);
    expect(screen.getAllByRole('heading', { level: 1 }).map((h) => h.textContent)).toEqual(['iPhone 13 Pro Max 128 GB']);
    expect(box.textContent).not.toMatch(/\*\*|##/);
  });

  it('sin descripción la ficha conserva su texto de respaldo', () => {
    render(<ProductDetail product={{ id: 8, slug: 'p-8', name: 'Cable', price: 50, inventory: 3, description: '' }} />);
    expect(screen.getByText('Consulta el detalle, precio y disponibilidad de este producto.')).toBeInTheDocument();
    expect(screen.queryByTestId('product-description')).toBeNull();
  });

  it('la tarjeta usa el texto sin marcas', () => {
    const { container } = render(<ProductCard id={9} slug="p-9" name="iPhone" price={10} inventory={1} description={DESCRIPTION} />);
    expect(container.textContent).toContain('Apple iPhone 13 Pro Max. Características principales.');
    expect(container.textContent).not.toMatch(/\*\*|##/);
  });
});
