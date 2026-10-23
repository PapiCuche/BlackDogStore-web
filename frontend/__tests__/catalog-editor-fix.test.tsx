import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { CatalogModelEditor } from '@/app/admin/components/CatalogModelEditor';
import { CatalogModelsManager } from '@/app/admin/components/CatalogModelsManager';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-EDITOR-FIX-01 — el propietario no encontraba cómo editar sus modelos y variantes.
 *
 * Lo que se comprueba aquí, con la API simulada (el guardado real lo prueban Django y Playwright):
 *   - el listado dice en cada fila qué se puede hacer (no hay que adivinar que el nombre es un enlace);
 *   - el modelo se edita con «Editar modelo» → «Guardar cambios» / «Cancelar», precargado, sin crear otro;
 *   - cambiar la dirección de un modelo publicado pide confirmarlo (los enlaces antiguos dejan de funcionar);
 *   - una variante se guarda en UNA operación: modelo, línea, opciones, precio y publicación juntos;
 *   - sin atributos, el editor explica dónde crearlos para poder elegir capacidad o color.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: Record<string, unknown> };
let writes: Call[];
let attributes: unknown[];

const ATTRIBUTES = [
  { id: 3, name: 'Capacidad', kind: 'text', sort_order: 0, options: [
    { id: 11, label: '128 GB', swatch_hex: '', sort_order: 0 }, { id: 12, label: '256 GB', swatch_hex: '', sort_order: 1 }] },
];

const MODEL = {
  id: 5, name: 'iPhone 17 Pro', slug: 'iphone-17-pro', category_id: null, category_name: null, image_url: '',
  description: 'Antes', is_published_online: true, sort_order: 0, variant_count: 1, used_unit_count: 0,
  variants: [{
    product_id: 7, name: 'iPhone 17 Pro Azul 256Gb', condition_line: 'used', price: '4090.00', stock: 0,
    is_published_online: true, options: [], history: { sold: 0, units: 0, reserved: 0 },
    identity_change: 'free', line_change: 'free', removal: 'free',
  }, {
    product_id: 8, name: 'iPhone 17 Pro oculto', condition_line: 'new', price: '5000.00', stock: 2,
    is_published_online: false, options: [], history: { sold: 0, units: 0, reserved: 0 },
    identity_change: 'free', line_change: 'free', removal: 'free',
  }],
};

beforeEach(() => {
  writes = [];
  attributes = ATTRIBUTES;
  window.history.replaceState(null, '', '/admin/products/models/5');
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      const body = JSON.parse(String(init.body ?? '{}'));
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body });
      if (url.includes('/admin/catalog/models/5/')) return reply({ ...MODEL, ...body });
      return reply({ product_id: 7 });
    }
    if (url.includes('/admin/catalog/models/5/')) return reply(MODEL);
    if (url.includes('/admin/catalog/models/')) return reply({ results: [{ ...MODEL, variants: undefined }] });
    if (url.includes('/admin/catalog/attributes/')) return reply(attributes);
    if (url.includes('/admin/catalog/settings/')) return reply({ new_line_label: '', used_line_label: '' });
    if (url.includes('/admin/catalog/grades/')) return reply([]);
    if (url.includes('/admin/categories/')) return reply([]);
    return reply({}, 404);
  });
});

describe('el listado dice qué se puede hacer con cada modelo', () => {
  it('un gestor ve «Ver detalles», «Editar modelo» y «Gestionar variantes» en la fila', async () => {
    render(<CatalogModelsManager canManage companyId={null} />);
    const row = (await screen.findByRole('link', { name: 'iPhone 17 Pro' })).closest('tr')!;
    expect(within(row).getByRole('link', { name: 'Ver detalles de iPhone 17 Pro' }))
      .toHaveAttribute('href', '/admin/products/models/5');
    expect(within(row).getByRole('link', { name: 'Editar modelo iPhone 17 Pro' }))
      .toHaveAttribute('href', '/admin/products/models/5?editar=modelo');
    expect(within(row).getByRole('link', { name: 'Gestionar variantes de iPhone 17 Pro' }))
      .toHaveAttribute('href', '/admin/products/models/5#variantes');
    expect(screen.getByRole('columnheader', { name: 'Acciones' })).toBeInTheDocument();
  });

  it('sin `products.manage` sólo «Ver detalles»', async () => {
    render(<CatalogModelsManager canManage={false} companyId={null} />);
    const row = (await screen.findByRole('link', { name: 'iPhone 17 Pro' })).closest('tr')!;
    expect(within(row).getByRole('link', { name: 'Ver detalles de iPhone 17 Pro' })).toBeInTheDocument();
    expect(within(row).queryByRole('link', { name: /Editar modelo/ })).toBeNull();
    expect(within(row).queryByRole('link', { name: /Gestionar variantes/ })).toBeNull();
  });
});

async function mountEditor(canManage = true) {
  render(<CatalogModelEditor modelId={5} canManage={canManage} companyId={null} />);
  await screen.findByText('iPhone 17 Pro Azul 256Gb');
}

describe('«Editar modelo»', () => {
  it('abre el formulario precargado; «Guardar cambios» actualiza ESE modelo y muestra lo que respondió el servidor', async () => {
    await mountEditor();
    expect(screen.queryByRole('form', { name: 'Editar modelo' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Editar modelo' }));
    const form = screen.getByRole('form', { name: 'Editar modelo' });
    expect(within(form).getByLabelText('Nombre')).toHaveValue('iPhone 17 Pro');
    expect(within(form).getByLabelText('Descripción')).toHaveValue('Antes');
    fireEvent.change(within(form).getByLabelText('Descripción'), { target: { value: 'Después' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar cambios' })); });
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ method: 'PATCH', path: '/admin/catalog/models/5/' });
    expect(writes[0].body).toMatchObject({ name: 'iPhone 17 Pro', slug: 'iphone-17-pro', description: 'Después' });
    expect(await screen.findByRole('status')).toHaveTextContent('Modelo guardado.');
    expect(screen.queryByRole('form', { name: 'Editar modelo' })).toBeNull();
    expect(screen.getByText('Después')).toBeInTheDocument();
  });

  it('«Cancelar» descarta lo escrito y no guarda nada', async () => {
    await mountEditor();
    fireEvent.click(screen.getByRole('button', { name: 'Editar modelo' }));
    const form = screen.getByRole('form', { name: 'Editar modelo' });
    fireEvent.change(within(form).getByLabelText('Nombre'), { target: { value: 'Otro nombre' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Cancelar' }));
    expect(writes).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Editar modelo' }));
    expect(within(screen.getByRole('form', { name: 'Editar modelo' })).getByLabelText('Nombre')).toHaveValue('iPhone 17 Pro');
  });

  it('llegar con «?editar=modelo» abre el formulario directamente; al cerrarlo la dirección lo olvida', async () => {
    window.history.replaceState(null, '', '/admin/products/models/5?editar=modelo');
    await mountEditor();
    const form = await screen.findByRole('form', { name: 'Editar modelo' });
    fireEvent.click(within(form).getByRole('button', { name: 'Cancelar' }));
    expect(window.location.search).toBe('');
  });

  it('cambiar la dirección de un modelo publicado pide confirmarlo antes de guardar', async () => {
    await mountEditor();
    fireEvent.click(screen.getByRole('button', { name: 'Editar modelo' }));
    const form = screen.getByRole('form', { name: 'Editar modelo' });
    fireEvent.change(within(form).getByLabelText('Dirección'), { target: { value: 'iphone-17-pro-max' } });
    expect(within(form).getByRole('note')).toHaveTextContent('/modelo/iphone-17-pro');
    expect(within(form).getByRole('button', { name: 'Guardar cambios' })).toBeDisabled();
    fireEvent.click(within(form).getByLabelText(/dejará de funcionar/));
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar cambios' })); });
    expect(writes[0].body).toMatchObject({ slug: 'iphone-17-pro-max' });
  });

  it('sin `products.manage` no hay «Editar modelo»', async () => {
    await mountEditor(false);
    expect(screen.queryByRole('button', { name: 'Editar modelo' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Editar variante/ })).toBeNull();
  });
});

describe('variantes', () => {
  it('la tabla dice si cada variante está publicada', async () => {
    await mountEditor();
    const rows = screen.getAllByRole('row');
    expect(rows.find((r) => r.textContent?.includes('Azul 256Gb'))).toHaveTextContent('Publicada');
    expect(rows.find((r) => r.textContent?.includes('oculto'))).toHaveTextContent('Oculta');
  });

  it('opciones y precio se guardan en UNA operación, sin una segunda petición', async () => {
    await mountEditor();
    fireEvent.click(screen.getByRole('button', { name: 'Editar variante iPhone 17 Pro Azul 256Gb' }));
    const form = screen.getByRole('form', { name: 'Editar variante iPhone 17 Pro Azul 256Gb' });
    fireEvent.change(await within(form).findByLabelText('Capacidad'), { target: { value: '12' } });
    fireEvent.change(within(form).getByLabelText('Precio (S/)'), { target: { value: '3999' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar cambios' })); });
    expect(writes).toEqual([{
      method: 'PUT', path: '/admin/products/7/variant/',
      body: { model_id: 5, condition_line: 'used', option_ids: [12], price: '3999' },
    }]);
  });

  it('sin atributos, explica dónde crearlos para elegir capacidad o color', async () => {
    attributes = [];
    await mountEditor();
    const panel = screen.getByRole('region', { name: 'Variantes' });
    const link = within(panel).getByRole('link', { name: /Atributos y opciones/ });
    expect(link).toHaveAttribute('href', '/admin/products/models#atributos');
    fireEvent.click(screen.getByRole('button', { name: 'Editar variante iPhone 17 Pro Azul 256Gb' }));
    const form = screen.getByRole('form', { name: 'Editar variante iPhone 17 Pro Azul 256Gb' });
    expect(within(form).getByText(/todavía no tiene atributos/)).toBeInTheDocument();
  });
});
