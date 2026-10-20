import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CatalogModelEditor } from '@/app/admin/components/CatalogModelEditor';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-VARIANTS-UX — «Editar variante» en la página de un modelo.
 *
 * Editar es cambiar el MISMO producto: modelo, línea y opciones por la ruta de
 * la variante; precio y publicación por la del producto. Lo que la variante ya
 * lleva (ventas, equipos, apartados) decide qué se puede cambiar, y el
 * formulario lo explica antes de que el servidor lo rechace.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: Record<string, unknown> };
let writes: Call[];
let modelReads: number;
let variantReply: Response | null;

const ATTRIBUTES = [
  {
    id: 3, name: 'Capacidad', kind: 'text', sort_order: 0,
    options: [{ id: 11, label: '128 GB', swatch_hex: '', sort_order: 0 }, { id: 12, label: '256 GB', swatch_hex: '', sort_order: 1 }],
  },
  {
    id: 4, name: 'Color', kind: 'color', sort_order: 1,
    options: [{ id: 21, label: 'Azul', swatch_hex: '#1F3A93', sort_order: 0 }, { id: 22, label: 'Blanco', swatch_hex: '#FFFFFF', sort_order: 1 }],
  },
];

const opt = (attribute_id: number, attribute_name: string, option_id: number, label: string) => ({
  attribute_id, attribute_name, option_id, label, swatch_hex: '',
});

const variant = (product_id: number, name: string, extra: Record<string, unknown> = {}) => ({
  product_id, name, condition_line: 'new', price: '3999.00', stock: 0, is_published_online: true,
  options: [opt(3, 'Capacidad', 11, '128 GB'), opt(4, 'Color', 21, 'Azul')],
  history: { sold: 0, units: 0, reserved: 0 }, identity_change: 'free', line_change: 'free',
  ...extra,
});

const MODEL = {
  id: 5, name: 'Equipo X', slug: 'equipo-x', category_id: null, category_name: null, image_url: '',
  description: '', is_published_online: true, sort_order: 0, variant_count: 3, used_unit_count: 0,
  variants: [
    variant(51, 'Equipo X libre'),
    variant(52, 'Equipo X con historial', {
      options: [opt(3, 'Capacidad', 12, '256 GB'), opt(4, 'Color', 21, 'Azul')],
      history: { sold: 2, units: 1, reserved: 0 }, identity_change: 'superior', line_change: 'blocked',
    }),
    variant(53, 'Equipo X apartado', {
      options: [opt(3, 'Capacidad', 12, '256 GB'), opt(4, 'Color', 22, 'Blanco')],
      history: { sold: 0, units: 1, reserved: 1 }, identity_change: 'blocked', line_change: 'blocked',
    }),
  ],
};

beforeEach(() => {
  writes = [];
  modelReads = 0;
  variantReply = null;
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body: JSON.parse(String(init.body ?? '{}')) });
      if (url.includes('/variant/')) return variantReply ?? reply({ id: 51 });
      return reply({ id: 51, price: '4100.00' });
    }
    if (url.includes('/admin/catalog/models/5/')) { modelReads += 1; return reply(MODEL); }
    if (url.includes('/admin/catalog/models/')) {
      return reply([{ ...MODEL, variants: undefined }, { ...MODEL, id: 6, name: 'Equipo Y', slug: 'equipo-y', variants: undefined }]);
    }
    if (url.includes('/admin/catalog/attributes/')) return reply(ATTRIBUTES);
    if (url.includes('/admin/catalog/settings/')) return reply({ new_line_label: '', used_line_label: '' });
    if (url.includes('/admin/categories/')) return reply([]);
    return reply({}, 404);
  });
});

async function mount(canManage = true) {
  render(<CatalogModelEditor modelId={5} canManage={canManage} companyId={null} />);
  await screen.findByText('Equipo X libre');
}

async function openEditor(name: string) {
  fireEvent.click(screen.getByRole('button', { name: `Editar variante ${name}` }));
  const form = screen.getByRole('form', { name: `Editar variante ${name}` });
  // The attribute selects need the attributes, which arrive on their own.
  await within(form).findByLabelText('Capacidad');
  return form;
}

const field = (form: HTMLElement, label: string | RegExp) => within(form).getByLabelText(label) as HTMLSelectElement;

async function submit(form: HTMLElement) {
  await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar cambios' })); });
}

describe('quién ve «Editar variante»', () => {
  it('sólo quien gestiona productos, una por variante', async () => {
    await mount(false);
    expect(screen.queryByRole('button', { name: /Editar variante/ })).not.toBeInTheDocument();
  });

  it('un gestor la tiene en cada fila', async () => {
    await mount(true);
    expect(screen.getAllByRole('button', { name: /^Editar variante / })).toHaveLength(3);
  });
});

describe('el formulario', () => {
  it('llega con lo que la variante es hoy', async () => {
    await mount();
    const form = await openEditor('Equipo X libre');
    await waitFor(() => expect(within(field(form, 'Modelo')).getByRole('option', { name: 'Equipo Y' })).toBeInTheDocument());
    expect(field(form, 'Modelo').value).toBe('5');
    expect(field(form, /^Línea/).value).toBe('new');
    expect(field(form, 'Capacidad').value).toBe('11');
    expect(field(form, 'Color').value).toBe('21');
    expect((within(form).getByLabelText('Precio (S/)') as HTMLInputElement).value).toBe('3999.00');
    expect(within(form).getByLabelText('Publicado en la tienda')).toBeChecked();
    expect(within(form).getByRole('link', { name: 'Fotos y datos del producto' })).toHaveAttribute('href', '/admin/products/51');
    expect(within(form).getByRole('link', { name: 'Costo y stock' })).toHaveAttribute('href', '/admin/products/51/stock-card');
  });

  it('cambiar una opción guarda el MISMO producto con sus opciones, y recarga el modelo', async () => {
    await mount();
    const form = await openEditor('Equipo X libre');
    fireEvent.change(field(form, 'Capacidad'), { target: { value: '12' } });
    const before = modelReads;
    await submit(form);
    expect(writes).toEqual([
      { method: 'PUT', path: '/admin/products/51/variant/', body: { model_id: 5, condition_line: 'new', option_ids: [12, 21] } },
    ]);
    expect(await screen.findByRole('status')).toHaveTextContent('Variante actualizada.');
    expect(modelReads).toBeGreaterThan(before);
  });

  it('cambiar el precio lo guarda en el producto', async () => {
    await mount();
    const form = await openEditor('Equipo X libre');
    fireEvent.change(within(form).getByLabelText('Precio (S/)'), { target: { value: '4100' } });
    await submit(form);
    expect(writes).toEqual([{ method: 'PATCH', path: '/admin/products/51/', body: { price: '4100' } }]);
  });

  it('si sólo cambian precio y publicación, la variante no se toca', async () => {
    await mount();
    const form = await openEditor('Equipo X libre');
    fireEvent.change(within(form).getByLabelText('Precio (S/)'), { target: { value: '4100' } });
    fireEvent.click(within(form).getByLabelText('Publicado en la tienda'));
    await submit(form);
    expect(writes.map((w) => w.method)).toEqual(['PATCH']);
    expect(writes[0].body).toEqual({ price: '4100', is_published_online: false });
  });

  it('la respuesta del servidor se muestra tal cual', async () => {
    await mount();
    const form = await openEditor('Equipo X libre');
    variantReply = reply({
      detail: 'Hay equipos de esta variante apartados o en un carrito: libera esas reservas antes de cambiarla.',
      code: 'variant_reserved', field: 'options',
    }, 409);
    fireEvent.change(field(form, 'Color'), { target: { value: '22' } });
    await submit(form);
    expect(within(form).getByRole('alert')).toHaveTextContent(
      'Hay equipos de esta variante apartados o en un carrito: libera esas reservas antes de cambiarla.',
    );
    expect(field(form, 'Color')).toHaveAttribute('aria-invalid', 'true');
  });
});

describe('lo que la variante ya lleva', () => {
  it('con ventas o equipos lo dice, y pide el motivo para cambiar sus opciones', async () => {
    await mount();
    const form = await openEditor('Equipo X con historial');
    expect(within(form).getByRole('note')).toHaveTextContent(
      'Ya tiene 2 ventas y 1 equipo: cambiar modelo u opciones lo hace un administrador de la empresa, con motivo.',
    );
    expect(within(form).queryByLabelText(/Motivo del cambio/)).not.toBeInTheDocument();

    fireEvent.change(field(form, 'Color'), { target: { value: '22' } });
    const reason = within(form).getByLabelText(/Motivo del cambio/);
    await submit(form);
    expect(within(form).getByRole('alert')).toHaveTextContent('Escribe el motivo del cambio');
    expect(writes).toEqual([]);

    fireEvent.change(reason, { target: { value: 'El color se registró mal al ingresar.' } });
    await submit(form);
    expect(writes).toEqual([{
      method: 'PUT', path: '/admin/products/52/variant/',
      body: { model_id: 5, condition_line: 'new', option_ids: [12, 22], reason: 'El color se registró mal al ingresar.' },
    }]);
  });

  it('con equipos propios la línea no cambia y lo explica; las opciones sí', async () => {
    await mount();
    const form = await openEditor('Equipo X con historial');
    expect(field(form, /^Línea/)).toBeDisabled();
    expect(within(form).getByText(/Tiene equipos propios: su línea es la de esos equipos y no cambia/)).toBeInTheDocument();
    expect(field(form, 'Capacidad')).toBeEnabled();
    expect(field(form, 'Modelo')).toBeEnabled();
  });

  it('con equipos apartados modelo, opciones y línea quedan bloqueados; precio y publicación no', async () => {
    await mount();
    const form = await openEditor('Equipo X apartado');
    expect(within(form).getByRole('note')).toHaveTextContent(/Hay equipos apartados o en un carrito/);
    for (const label of ['Modelo', 'Capacidad', 'Color']) expect(field(form, label)).toBeDisabled();
    expect(field(form, /^Línea/)).toBeDisabled();
    expect(within(form).getByLabelText('Precio (S/)')).toBeEnabled();
    expect(within(form).getByLabelText('Publicado en la tienda')).toBeEnabled();
  });
});
