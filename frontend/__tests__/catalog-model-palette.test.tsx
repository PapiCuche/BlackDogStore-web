import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CatalogModelEditor } from '@/app/admin/components/CatalogModelEditor';
import { CatalogModelsManager } from '@/app/admin/components/CatalogModelsManager';
import type { AdminModelPalette } from '@/app/admin/lib/internal-api';
import { paletteSelection, sameSelection, PALETTE_STATUS_LABELS, ROLE_LABELS } from '@/app/admin/lib/palette';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-COMMERCIAL-01 · A1 — «Opciones de este modelo» (la paleta) en la página de un modelo.
 *
 * Con la API simulada (el guardado real lo prueban Django y Playwright):
 *   - la paleta lista los atributos de la empresa en el orden de sus roles, con cada opción marcada o no;
 *   - «Guardar paleta» envía el CONJUNTO de opciones elegidas a PUT …/palette/ y muestra lo que respondió;
 *   - la tabla de configuraciones dice cuáles ya tienen producto y cuáles no, y qué variantes quedaron fuera;
 *   - «Agregar variante» sólo ofrece lo que la paleta admite (la interfaz no ofrece lo que el servidor negará);
 *   - una variante fuera de la paleta se señala en la tabla de variantes, nunca se corrige sola;
 *   - sin `products.manage` todo es de sólo lectura;
 *   - en «Atributos y opciones» cada atributo tiene su rol y se puede cambiar.
 *
 * Nombres y valores inventados: nada aquí es de Apple ni de la tienda piloto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: Record<string, unknown> };
let writes: Call[];

const option = (id: number, label: string, in_palette: boolean, swatch_hex = '') => ({ id, label, swatch_hex, sort_order: 0, in_palette });

const PALETTE: AdminModelPalette = {
  model_id: 5, model_name: 'Equipo Z', has_palette: true,
  attributes: [
    { id: 3, name: 'Capacidad', kind: 'text', role: 'capacity', sort_order: 0,
      options: [option(11, '128 GB', true), option(12, '256 GB', true), option(13, '1 TB', false)] },
    { id: 4, name: 'Color', kind: 'color', role: 'color', sort_order: 1,
      options: [option(21, 'Azul', true, '#1F3A93'), option(22, 'Negro', true, '#111111'), option(23, 'Rojo', false, '#B91C1C')] },
    { id: 6, name: 'Tipo de SIM', kind: 'text', role: 'sim', sort_order: 2,
      options: [option(31, 'eSIM', false), option(32, 'Chip', false)] },
  ],
  combinations: [
    { key: '11-21', option_ids: [11, 21], labels: { '3': '128 GB', '4': 'Azul' }, status: 'variante_existente',
      products: [{ product_id: 51, name: 'Equipo Z 128 Azul', condition_line: 'new' }] },
    { key: '11-22', option_ids: [11, 22], labels: { '3': '128 GB', '4': 'Negro' }, status: 'combinacion_valida_sin_variante', products: [] },
    { key: '12-21', option_ids: [12, 21], labels: { '3': '256 GB', '4': 'Azul' }, status: 'combinacion_valida_sin_variante', products: [] },
    { key: '12-22', option_ids: [12, 22], labels: { '3': '256 GB', '4': 'Negro' }, status: 'combinacion_valida_sin_variante', products: [] },
  ],
  variants: [
    { product_id: 51, name: 'Equipo Z 128 Azul', condition_line: 'new', option_ids: [11, 21], status: 'variante_existente' },
    { product_id: 52, name: 'Equipo Z 128 Rojo', condition_line: 'new', option_ids: [11, 23], status: 'fuera_de_paleta' },
  ],
  summary: { combinations: 4, existing: 1, valid_without_variant: 3, outside: 1, incomplete: 0 },
  truncated: false,
};

const ATTRIBUTES = PALETTE.attributes.map(({ options, ...attribute }) => ({
  ...attribute, options: options.map((o) => ({ id: o.id, label: o.label, swatch_hex: o.swatch_hex, sort_order: o.sort_order })),
}));

const opt = (attribute_id: number, attribute_name: string, option_id: number, label: string) => ({
  attribute_id, attribute_name, option_id, label, swatch_hex: '',
});

const MODEL = {
  id: 5, name: 'Equipo Z', slug: 'equipo-z', category_id: null, category_name: null, image_url: '',
  description: '', is_published_online: true, sort_order: 0, variant_count: 2, used_unit_count: 0, has_palette: true,
  variants: [
    { product_id: 51, name: 'Equipo Z 128 Azul', condition_line: 'new', price: '100.00', stock: 0, is_published_online: true,
      options: [opt(3, 'Capacidad', 11, '128 GB'), opt(4, 'Color', 21, 'Azul')], history: { sold: 0, units: 0, reserved: 0 },
      identity_change: 'free', line_change: 'free', removal: 'free', palette_status: 'variante_existente' },
    { product_id: 52, name: 'Equipo Z 128 Rojo', condition_line: 'new', price: '100.00', stock: 0, is_published_online: true,
      options: [opt(3, 'Capacidad', 11, '128 GB'), opt(4, 'Color', 23, 'Rojo')], history: { sold: 1, units: 0, reserved: 0 },
      identity_change: 'superior', line_change: 'free', removal: 'superior', palette_status: 'fuera_de_paleta' },
  ],
};

function mockApi(palette: AdminModelPalette = PALETTE) {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.replace(/^.*\/api/, '').replace(/\?.*$/, '');
    if (method !== 'GET') {
      const body = JSON.parse(String(init.body ?? '{}'));
      writes.push({ method, path, body });
      if (path === '/admin/catalog/models/5/palette/') {
        const chosen = new Set(body.option_ids as number[]);
        return reply({
          ...palette,
          attributes: palette.attributes.map((a) => ({ ...a, options: a.options.map((o) => ({ ...o, in_palette: chosen.has(o.id) })) })),
          changed: { added: 1, removed: 0, total: chosen.size },
        });
      }
      if (path.startsWith('/admin/catalog/attributes/')) return reply({ ...ATTRIBUTES[2], ...body });
      return reply({ product_id: 7 });
    }
    if (path === '/admin/catalog/models/5/palette/') return reply(palette);
    if (path === '/admin/catalog/models/5/') return reply(MODEL);
    if (path === '/admin/catalog/models/') return reply({ results: [{ ...MODEL, variants: undefined }] });
    if (path === '/admin/catalog/attributes/') return reply(ATTRIBUTES);
    if (path === '/admin/catalog/settings/') return reply({ new_line_label: '', used_line_label: '' });
    if (path === '/admin/catalog/grades/') return reply([]);
    if (path === '/admin/categories/') return reply([]);
    if (path === '/admin/products/') return reply({ results: [{ id: 9, name: 'Equipo Z suelto', price: '10.00' }], count: 1 });
    return reply({}, 404);
  });
}

beforeEach(() => {
  writes = [];
  window.history.replaceState(null, '', '/admin/products/models/5');
  mockApi();
});

async function mountEditor(canManage = true) {
  render(<CatalogModelEditor modelId={5} canManage={canManage} companyId={null} />);
  const variants = await screen.findByRole('region', { name: 'Variantes' });
  await within(variants).findByText('Equipo Z 128 Azul', { selector: 'a' });
  return screen.findByRole('region', { name: 'Opciones de este modelo' });
}

describe('los ayudantes de la paleta', () => {
  it('leen el conjunto elegido y lo comparan sin importar el orden', () => {
    expect([...paletteSelection(PALETTE)].sort()).toEqual([11, 12, 21, 22]);
    expect(sameSelection(new Set([1, 2]), new Set([2, 1]))).toBe(true);
    expect(sameSelection(new Set([1, 2]), new Set([1]))).toBe(false);
    expect(ROLE_LABELS.capacity).toBe('Capacidad');
    expect(PALETTE_STATUS_LABELS.fuera_de_paleta).toBe('Fuera de la paleta');
  });
});

describe('«Opciones de este modelo»', () => {
  it('lista los atributos por rol con cada opción marcada según la paleta y resume las configuraciones', async () => {
    const panel = await mountEditor();
    const groups = within(panel).getAllByRole('group');
    expect(groups.map((g) => g.getAttribute('aria-label'))).toEqual(['Capacidad', 'Color', 'Tipo de SIM']);
    expect(within(groups[0]).getByRole('checkbox', { name: '128 GB' })).toBeChecked();
    expect(within(groups[0]).getByRole('checkbox', { name: '1 TB' })).not.toBeChecked();
    expect(within(groups[1]).getByRole('checkbox', { name: 'Rojo' })).not.toBeChecked();
    expect(within(groups[2]).getByText('rol: SIM')).toBeInTheDocument();
    expect(within(groups[0]).queryByText(/rol:/)).toBeNull();                 // «Capacidad» ya dice su rol
    expect(within(panel).getByText(/4 configuraciones/)).toBeInTheDocument();
    expect(within(panel).getByText(/1 con variante/)).toBeInTheDocument();
    expect(within(panel).getByText(/3 sin variante/)).toBeInTheDocument();
    expect(within(panel).getByText(/1 fuera de la paleta/)).toBeInTheDocument();
    const table = within(panel).getByRole('table', { name: 'Configuraciones de la paleta' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent('128 GB');
    expect(rows[0]).toHaveTextContent('Con variante');
    expect(rows[0]).toHaveTextContent('Equipo Z 128 Azul');
    expect(rows[1]).toHaveTextContent('Sin variante');
    // Lo que quedó fuera se nombra; no se corrige.
    const outside = within(panel).getByRole('list', { name: 'Variantes fuera de la paleta o incompletas' });
    expect(within(outside).getByText('Equipo Z 128 Rojo')).toBeInTheDocument();
    expect(within(outside).getByText('Fuera de la paleta')).toBeInTheDocument();
  });

  it('«Guardar paleta» envía el conjunto elegido y muestra lo que respondió el servidor', async () => {
    const panel = await mountEditor();
    const save = within(panel).getByRole('button', { name: 'Guardar paleta' });
    expect(save).toBeDisabled();
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'Rojo' }));
    fireEvent.click(within(panel).getByRole('checkbox', { name: '256 GB' }));
    expect(save).toBeEnabled();
    await act(async () => { fireEvent.click(save); });
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ method: 'PUT', path: '/admin/catalog/models/5/palette/' });
    expect([...(writes[0].body.option_ids as number[])].sort()).toEqual([11, 21, 22, 23]);
    expect(await within(panel).findByRole('status')).toHaveTextContent('Paleta guardada.');
    expect(within(panel).getByRole('checkbox', { name: 'Rojo' })).toBeChecked();
    expect(within(panel).getByRole('button', { name: 'Guardar paleta' })).toBeDisabled();
    // La página del modelo se vuelve a leer: los estados de las variantes salen del servidor.
    await waitFor(() => expect(send.mock.calls.filter(([u]) => String(u).endsWith('/admin/catalog/models/5/')).length).toBeGreaterThan(1));
  });

  it('lo marcado sin guardar sobrevive a una relectura que trae la misma paleta', async () => {
    const panel = await mountEditor();
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'Rojo' }));
    // A variant removed below re-reads the model and the palette (same saved set): the tick stays.
    fireEvent.click(screen.getByRole('button', { name: 'Quitar Equipo Z 128 Azul del modelo' }));
    await screen.findByText('El producto ya no es una variante de este modelo.');
    await waitFor(() => expect(send.mock.calls.filter(([u]) => String(u).includes('/palette/')).length).toBeGreaterThan(1));
    expect(within(panel).getByRole('checkbox', { name: 'Rojo' })).toBeChecked();
    expect(within(panel).getByRole('button', { name: 'Guardar paleta' })).toBeEnabled();
  });

  it('un error del servidor se muestra y no se pierde lo elegido', async () => {
    const panel = await mountEditor();
    send.mockImplementationOnce(async () => reply({ detail: 'Alguna opción no existe.', code: 'not_found', field: 'option_ids' }, 404));
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'Rojo' }));
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Guardar paleta' })); });
    expect(await within(panel).findByRole('alert')).toHaveTextContent('Alguna opción no existe.');
    expect(within(panel).getByRole('checkbox', { name: 'Rojo' })).toBeChecked();
  });

  it('sin `products.manage` se lee pero no se cambia', async () => {
    const panel = await mountEditor(false);
    expect(within(panel).getByRole('checkbox', { name: '128 GB' })).toBeDisabled();
    expect(within(panel).queryByRole('button', { name: 'Guardar paleta' })).toBeNull();
  });

  it('sin paleta, lo explica y las configuraciones están vacías', async () => {
    mockApi({
      ...PALETTE, has_palette: false, combinations: [],
      attributes: PALETTE.attributes.map((a) => ({ ...a, options: a.options.map((o) => ({ ...o, in_palette: false })) })),
      variants: PALETTE.variants.map((v) => ({ ...v, status: null })),
      summary: { combinations: 0, existing: 0, valid_without_variant: 0, outside: 0, incomplete: 0 },
    });
    const panel = await mountEditor();
    expect(within(panel).getByText(/Este modelo todavía no dice qué opciones admite/)).toBeInTheDocument();
    expect(within(panel).queryByRole('table', { name: 'Configuraciones de la paleta' })).toBeNull();
  });
});

describe('las variantes y la paleta', () => {
  it('«Agregar variante» sólo ofrece las opciones de la paleta, y sólo sus atributos', async () => {
    await mountEditor();
    fireEvent.click(screen.getByRole('button', { name: '+ Agregar variante' }));
    const form = await screen.findByRole('form', { name: 'Agregar variante' });
    const colour = within(form).getByLabelText('Color') as HTMLSelectElement;
    expect([...colour.options].map((o) => o.textContent)).toEqual(['Elige…', 'Azul', 'Negro']);
    const capacity = within(form).getByLabelText('Capacidad') as HTMLSelectElement;
    expect([...capacity.options].map((o) => o.textContent)).toEqual(['Elige…', '128 GB', '256 GB']);
    expect(within(form).queryByLabelText('Tipo de SIM')).toBeNull();
    // Every attribute of the palette takes one option: nothing is sent without it.
    const search = screen.getByRole('search', { name: 'Buscar producto' });
    fireEvent.change(within(search).getByRole('textbox'), { target: { value: 'suelto' } });
    await act(async () => { fireEvent.click(within(search).getByRole('button', { name: 'Buscar' })); });
    fireEvent.click(await screen.findByRole('radio', { name: /Equipo Z suelto/ }));
    fireEvent.change(within(form).getByLabelText('Capacidad'), { target: { value: '12' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar variante' })); });
    expect(within(form).getByRole('alert')).toHaveTextContent('Elige una opción de cada atributo de la paleta.');
    expect(writes).toHaveLength(0);
  });

  it('una variante fuera de la paleta se señala en la tabla de variantes', async () => {
    await mountEditor();
    const variants = screen.getByRole('region', { name: 'Variantes' });
    const row = within(variants).getByText('Equipo Z 128 Rojo', { selector: 'a' }).closest('tr')!;
    expect(within(row).getByText('Fuera de la paleta')).toBeInTheDocument();
    const fine = within(variants).getByText('Equipo Z 128 Azul', { selector: 'a' }).closest('tr')!;
    expect(within(fine).queryByText('Fuera de la paleta')).toBeNull();
  });
});

describe('«Atributos y opciones» con roles', () => {
  it('muestra el rol de cada atributo, lo cambia con PATCH y lo pide al crear uno nuevo', async () => {
    render(<CatalogModelsManager canManage companyId={null} />);
    const sim = (await screen.findByRole('heading', { name: 'Tipo de SIM' })).closest('li')!;
    const select = within(sim).getByLabelText('Rol de Tipo de SIM') as HTMLSelectElement;
    expect(select.value).toBe('sim');
    await act(async () => { fireEvent.change(select, { target: { value: 'other' } }); });
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ method: 'PATCH', path: '/admin/catalog/attributes/6/', body: { role: 'other' } });
    const create = screen.getByRole('form', { name: 'Nuevo atributo' });
    fireEvent.change(within(create).getByLabelText('Nuevo atributo'), { target: { value: 'Región' } });
    fireEvent.change(within(create).getByLabelText('Rol'), { target: { value: 'region' } });
    await act(async () => { fireEvent.click(within(create).getByRole('button', { name: 'Crear atributo' })); });
    expect(writes[1]).toMatchObject({ method: 'POST', path: '/admin/catalog/attributes/', body: { name: 'Región', role: 'region' } });
  });
});
