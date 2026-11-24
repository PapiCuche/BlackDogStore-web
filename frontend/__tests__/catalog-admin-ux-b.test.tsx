import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CatalogModelEditor } from '@/app/admin/components/CatalogModelEditor';
import { CatalogModelsManager } from '@/app/admin/components/CatalogModelsManager';
import { ModelPalettePanel } from '@/app/admin/components/ModelPalettePanel';
import { catalogModelsPath, type AdminModelPalette } from '@/app/admin/lib/internal-api';
import {
  EMPTY_MODEL_LIST_STATE, readModelListState, writeModelListState,
} from '@/app/admin/lib/model-list-query';
import { pendingLineCreations } from '@/app/admin/lib/palette';
import { addressWithout } from '@/app/admin/lib/use-menu-request';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-ADMIN-UX-B — el editor de modelos y variantes a escala.
 *
 * Con la API simulada (la búsqueda, los filtros, las exclusiones y las reglas de verdad las prueba Django):
 *   - la lista de modelos busca y filtra EN EL SERVIDOR, con chips accesibles por teclado y la dirección al día;
 *   - «Crear variante» por LÍNEA: una combinación que sólo existe como seminuevo se crea como nueva eligiendo la línea;
 *   - las exclusiones se añaden y se quitan; un 409 nombra las variantes que ya son esa combinación;
 *   - cada modelo ofrece SUS colores: los de otros modelos van aparte y nunca entran en «Crear variante»;
 *   - precio y publicación en la fila, por el endpoint de la variante y con su identidad intacta;
 *   - un modelo sin paleta lo dice arriba y pide confirmación explícita para clasificar un producto.
 *
 * Nombres y valores inventados: nada es de un fabricante ni de la tienda piloto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; query: string; body: Record<string, unknown> };
let writes: Call[];
let reads: Call[];

const option = (id: number, label: string, in_palette: boolean, swatch_hex = '') => ({ id, label, swatch_hex, sort_order: 0, in_palette });

const PALETTE = {
  model_id: 5, model_name: 'Equipo Uno', has_palette: true,
  attributes: [
    { id: 3, name: 'Capacidad', kind: 'text', role: 'capacity', sort_order: 0, options: [option(11, '128 GB', true)] },
    { id: 4, name: 'Color', kind: 'color', role: 'color', sort_order: 1,
      options: [option(21, 'Azul', true, '#1F3A93'), option(22, 'Negro', true, '#111111'), option(23, 'Rojo', false, '#B91C1C')] },
    { id: 6, name: 'Tipo de SIM', kind: 'text', role: 'sim', sort_order: 2, options: [option(31, 'eSIM', true), option(32, 'Chip', true)] },
  ],
  combinations: [
    // Only as a used device: still missing in the new line.
    { key: '11-21-31', option_ids: [11, 21, 31], labels: { '3': '128 GB', '4': 'Azul', '6': 'eSIM' }, status: 'variante_existente',
      products: [{ product_id: 51, name: 'Equipo Uno 128 GB Azul eSIM Seminuevo', condition_line: 'used' }] },
    { key: '11-21-32', option_ids: [11, 21, 32], labels: { '3': '128 GB', '4': 'Azul', '6': 'Chip' }, status: 'combinacion_valida_sin_variante', products: [] },
    { key: '11-22-31', option_ids: [11, 22, 31], labels: { '3': '128 GB', '4': 'Negro', '6': 'eSIM' }, status: 'variante_existente',
      products: [{ product_id: 52, name: 'Equipo Uno 128 GB Negro eSIM', condition_line: 'new' }] },
  ],
  variants: [
    { product_id: 51, name: 'Equipo Uno 128 GB Azul eSIM Seminuevo', condition_line: 'used', option_ids: [11, 21, 31], status: 'variante_existente' },
    { product_id: 52, name: 'Equipo Uno 128 GB Negro eSIM', condition_line: 'new', option_ids: [11, 22, 31], status: 'variante_existente',
      draft_origin: '', draft_pending: false, sale_enabled_at: null, has_image: true, price_set: true, is_published_online: false,
      is_active: true, is_serialized: false },
  ],
  excluded_pairs: [[22, 32]],
  summary: { combinations: 3, existing: 2, valid_without_variant: 1, outside: 0, incomplete: 0, archived: 0,
    pending_drafts: 0, needs_review: 0, roles_missing: [], valid_without_variant_by_line: { new: 2, used: 2 } },
  truncated: false,
} as unknown as AdminModelPalette;

const LABELS = { new: 'De caja', used: 'Reacondicionados' } as const;

function routes(handlers: Record<string, (call: Call) => Response | Promise<Response>>) {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.replace(/^.*\/api/, '').replace(/\?.*$/, '');
    const query = url.includes('?') ? url.slice(url.indexOf('?') + 1) : '';
    const call = { method, path, query, body: method === 'GET' ? {} : JSON.parse(String(init.body ?? '{}')) };
    (method === 'GET' ? reads : writes).push(call);
    const handler = handlers[`${method} ${path}`];
    if (handler) return handler(call);
    if (method === 'GET' && path === '/admin/catalog/attributes/') return reply([]);
    if (method === 'GET' && path === '/admin/catalog/grades/') return reply([]);
    if (method === 'GET' && path === '/admin/catalog/settings/') return reply({ new_line_label: LABELS.new, used_line_label: LABELS.used });
    if (method === 'GET' && path === '/admin/catalog/aliases/') return reply({ results: [] });
    if (method === 'GET' && path === '/admin/categories/') return reply([{ id: 8, name: 'Móviles', slug: 'moviles' }]);
    return reply({}, 404);
  });
}

beforeEach(() => {
  writes = [];
  reads = [];
  window.history.replaceState(null, '', '/admin/products/models');
});

describe('la dirección de la lista de modelos', () => {
  it('lee y escribe la búsqueda y los filtros, y conserva los demás parámetros', () => {
    const state = readModelListState('?buscar=uno&categoria=8&estado=inactivos&filtro=sin-paleta,incompletas&pagina=3&seccion=colores');
    expect(state).toEqual({ search: 'uno', category: '8', active: 'false', flags: ['incomplete', 'no_palette'], page: 3 });
    expect(writeModelListState('?seccion=colores&buscar=viejo', { ...EMPTY_MODEL_LIST_STATE, search: 'nuevo', flags: ['missing'] }))
      .toBe('?seccion=colores&buscar=nuevo&filtro=sin-variante');
    expect(writeModelListState('?buscar=x', EMPTY_MODEL_LIST_STATE)).toBe('');
    expect(readModelListState('?categoria=sin&pagina=-2&filtro=raro').category).toBe('none');
    expect(readModelListState('?categoria=abc&pagina=-2').page).toBe(1);
  });

  it('la petición al servidor lleva la empresa, la búsqueda, los filtros y la página', () => {
    expect(catalogModelsPath({ search: ' uno ', category: 'none', active: 'true', flags: ['missing', 'no_palette'], page: 2, pageSize: 25 }, 4))
      .toBe('/admin/catalog/models/?company=4&search=uno&category=none&active=true&missing=1&no_palette=1&page_size=25&page=2');
    expect(catalogModelsPath({}, null)).toBe('/admin/catalog/models/');
  });

  it('el menú quita sólo su parámetro y deja la búsqueda', () => {
    expect(addressWithout('seccion', '/admin/products/models', '?seccion=colores&buscar=uno')).toBe('/admin/products/models?buscar=uno');
    expect(addressWithout('seccion', '/admin/products/models', '?seccion=colores')).toBe('/admin/products/models');
  });
});

describe('Productos › Modelos: búsqueda y filtros en el servidor', () => {
  const row = (id: number, name: string, extra: Record<string, unknown> = {}) => ({
    id, name, slug: name.toLowerCase().replace(/ /g, '-'), category_id: null, category_name: '', image_url: '', description: '',
    is_published_online: true, is_active: true, sort_order: 0, variant_count: 2, used_unit_count: 0, has_palette: true,
    valid_without_variant: 0, incomplete_variant_count: 0, outside_variant_count: 0, ...extra,
  });

  function mockList() {
    routes({
      'GET /admin/catalog/models/': ({ query }) => {
        const params = new URLSearchParams(query);
        const all = [
          row(1, 'Equipo Uno', { valid_without_variant: 3 }),
          row(2, 'Equipo Dos', { has_palette: false, valid_without_variant: null }),
          row(3, 'Equipo Tres', { incomplete_variant_count: 1 }),
        ];
        let rows = all;
        if (params.get('search')) rows = rows.filter((r) => r.name.toLowerCase().includes(String(params.get('search')).toLowerCase()));
        if (params.get('no_palette')) rows = rows.filter((r) => !r.has_palette);
        return reply({ results: rows, count: rows.length, facets: { incomplete: 1, missing: 1, no_palette: 1 }, page: 1, page_size: 25 });
      },
    });
  }

  const listReads = () => reads.filter((r) => r.path === '/admin/catalog/models/');

  it('cada fila dice el estado de su paleta, y los chips llevan la cuenta del servidor', async () => {
    mockList();
    render(<CatalogModelsManager canManage companyId={null} />);
    const table = await screen.findByRole('table');
    const uno = within(table).getByText('Equipo Uno').closest('tr')!;
    expect(uno).toHaveTextContent('3 combinaciones sin variante');
    expect(within(table).getByText('Equipo Dos').closest('tr')!).toHaveTextContent('Sin paleta');
    expect(within(table).getByText('Equipo Tres').closest('tr')!).toHaveTextContent('1 variante incompleta');
    const chips = screen.getByRole('group', { name: 'Filtrar por estado de la paleta' });
    expect(within(chips).getByRole('button', { name: /Sin paleta \(1\)/ })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/^3 modelos/)).toHaveTextContent('3 modelos · 1 sin paleta');
    expect(listReads()[0].query).toContain('page_size=25');
  });

  it('un chip filtra en el servidor, se marca pulsado y queda en la dirección', async () => {
    mockList();
    render(<CatalogModelsManager canManage companyId={null} />);
    await screen.findByRole('table');
    const chip = screen.getByRole('button', { name: /Sin paleta \(1\)/ });
    await act(async () => { fireEvent.click(chip); });
    await waitFor(() => expect(listReads().at(-1)?.query).toContain('no_palette=1'));
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    expect(window.location.search).toBe('?filtro=sin-paleta');
    await waitFor(() => expect(within(screen.getByRole('table')).queryByText('Equipo Uno')).toBeNull());
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Limpiar filtros' })); });
    await waitFor(() => expect(window.location.search).toBe(''));
  });

  it('la búsqueda se envía al servidor (Intro, sin esperar) y vuelve a la primera página', async () => {
    window.history.replaceState(null, '', '/admin/products/models?pagina=2');
    mockList();
    render(<CatalogModelsManager canManage companyId={null} />);
    await screen.findByRole('table');
    expect(listReads()[0].query).toContain('page=2');
    const box = screen.getByRole('searchbox', { name: 'Buscar' });
    fireEvent.change(box, { target: { value: 'tres' } });
    await act(async () => { fireEvent.keyDown(box, { key: 'Enter' }); });
    await waitFor(() => expect(listReads().at(-1)?.query).toContain('search=tres'));
    expect(listReads().at(-1)?.query).toContain('page=1');
    expect(window.location.search).toBe('?buscar=tres');
    await waitFor(() => expect(screen.getByRole('table')).not.toHaveTextContent('Equipo Uno'));
  });

  it('una página que ya no existe (menos resultados) vuelve a la primera', async () => {
    window.history.replaceState(null, '', '/admin/products/models?pagina=9');
    routes({
      'GET /admin/catalog/models/': ({ query }) => (new URLSearchParams(query).get('page') === '9'
        ? reply({ results: [], count: 3, facets: {}, page: 9, page_size: 25 })
        : reply({ results: [row(1, 'Equipo Uno')], count: 3, facets: {}, page: 1, page_size: 25 })),
    });
    render(<CatalogModelsManager canManage companyId={null} />);
    expect(await screen.findByRole('link', { name: 'Equipo Uno' })).toBeInTheDocument();
    expect(listReads().map((r) => new URLSearchParams(r.query).get('page'))).toEqual(['9', '1']);
    expect(window.location.search).toBe('');
    expect(screen.queryByText('Ningún modelo coincide con la búsqueda o los filtros.')).toBeNull();
  });

  it('la dirección abre la lista ya filtrada', async () => {
    window.history.replaceState(null, '', '/admin/products/models?buscar=dos&filtro=sin-paleta');
    mockList();
    render(<CatalogModelsManager canManage companyId={null} />);
    await screen.findByRole('table');
    expect(listReads()[0].query).toContain('search=dos');
    expect(listReads()[0].query).toContain('no_palette=1');
    expect(screen.getByRole('searchbox', { name: 'Buscar' })).toHaveValue('dos');
  });
});

describe('«Crear variante» por línea', () => {
  function mountPanel(palette: AdminModelPalette = PALETTE, onCreated = jest.fn()) {
    routes({
      'POST /admin/catalog/models/5/variants/': ({ body }) => reply({ id: 70, name: 'Equipo Uno 128 GB Azul eSIM', option_ids: body.option_ids }, 201),
    });
    render(
      <ModelPalettePanel modelId={5} companyId={null} canManage palette={palette} error={null} onSaved={jest.fn()}
        lineLabels={LABELS} onCreated={onCreated} onStale={jest.fn()} modelName="Equipo Uno" />,
    );
    return screen.getByRole('region', { name: 'Opciones de este modelo' });
  }
  const table = (panel: HTMLElement) => within(panel).getByRole('table', { name: 'Configuraciones de la paleta' });
  const rowWith = (panel: HTMLElement, text: string) =>
    within(table(panel)).getAllByRole('row').find((r) => r.textContent?.includes(text) && r.textContent?.includes('128 GB'))!;

  it('con todas las líneas, sólo lo que no existe en ninguna; en una línea, lo que falta en ella', async () => {
    const panel = mountPanel();
    expect(within(table(panel)).getAllByRole('button', { name: /Crear variante/ })).toHaveLength(1);
    const lines = within(panel).getByRole('group', { name: 'Línea de las configuraciones' });
    expect(within(lines).getByRole('button', { name: /De caja \(2 sin variante\)/ })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(within(lines).getByRole('button', { name: /De caja/ }));
    // «128 GB Azul eSIM» existe sólo reacondicionada: en «De caja» falta y se ofrece.
    const azulEsim = within(table(panel)).getAllByRole('row').find((r) => /Azul.*eSIM/.test(r.textContent ?? ''))!;
    expect(azulEsim).toHaveTextContent('Sin variante');
    expect(within(azulEsim).getByRole('button', { name: 'Crear variante 128 GB · Azul · eSIM en De caja' })).toBeInTheDocument();
    // «128 GB Negro eSIM» ya es de caja: nada que crear.
    expect(within(rowWith(panel, 'Negro')).queryByRole('button', { name: /Crear variante/ })).toBeNull();
  });

  it('el formulario crea en la línea elegida, sin ofrecer otra', async () => {
    const onCreated = jest.fn();
    const panel = mountPanel(PALETTE, onCreated);
    fireEvent.click(within(panel).getByRole('button', { name: /De caja/ }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Crear variante 128 GB · Azul · eSIM en De caja' }));
    const form = within(panel).getByRole('form', { name: 'Crear variante 128 GB · Azul · eSIM' });
    expect(within(form).queryByRole('combobox', { name: 'Línea' })).toBeNull();
    expect(form).toHaveTextContent('Línea: De caja');
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' })); });
    expect(writes).toEqual([expect.objectContaining({ method: 'POST', path: '/admin/catalog/models/5/variants/',
      body: { condition_line: 'new', option_ids: [11, 21, 31] } })]);
    expect(onCreated).toHaveBeenCalled();
    // Ya creada en «De caja»: no se ofrece otra vez antes de la relectura.
    expect(within(panel).queryByRole('button', { name: 'Crear variante 128 GB · Azul · eSIM en De caja' })).toBeNull();
  });

  it('una creación pendiente se olvida cuando la lectura ya trae esa línea', () => {
    const pending = new Map([['11-21-31|new', 70], ['11-21-32|used', 71]]);
    const confirmed = {
      ...PALETTE,
      combinations: PALETTE.combinations.map((c) => (c.key === '11-21-31'
        ? { ...c, products: [...c.products, { product_id: 70, name: 'x', condition_line: 'new' as const }] } : c)),
    };
    expect([...pendingLineCreations(pending, confirmed).keys()]).toEqual(['11-21-32|used']);
  });
});

describe('colores del modelo', () => {
  it('los de la paleta van primero; los demás de la empresa, aparte y sin marcar; «Crear variante» sólo usa los suyos', async () => {
    routes({
      'POST /admin/catalog/models/5/variants/': ({ body }) => reply({ id: 71, name: 'x', option_ids: body.option_ids }, 201),
    });
    render(
      <ModelPalettePanel modelId={5} companyId={null} canManage palette={PALETTE} error={null} onSaved={jest.fn()}
        lineLabels={LABELS} onCreated={jest.fn()} onStale={jest.fn()} modelName="Equipo Uno" />,
    );
    const panel = screen.getByRole('region', { name: 'Opciones de este modelo' });
    const colours = within(panel).getAllByRole('group').find((g) => g.getAttribute('aria-label') === 'Color')!;
    const names = within(colours).getAllByRole('checkbox').map((c) => c.closest('label')?.textContent);
    expect(names).toEqual(['Azul', 'Negro', 'Rojo']);
    expect(colours).toHaveTextContent('Otros colores de la empresa (1): márcalos sólo si este modelo también se fabrica en ellos.');
    expect(within(colours).getByRole('checkbox', { name: 'Rojo' })).not.toBeChecked();
    const configs = within(panel).getByRole('table', { name: 'Configuraciones de la paleta' });
    expect(configs).not.toHaveTextContent('Rojo');
    fireEvent.click(within(configs).getByRole('button', { name: /Crear variante/ }));
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Crear variante' })); });
    expect(writes[0].body.option_ids).toEqual([11, 21, 32]);
    expect(writes[0].body.option_ids).not.toContain(23);
  });
});

describe('«Combinaciones que no existen»', () => {
  function mountEditorPanel(onSaved = jest.fn()) {
    render(
      <ModelPalettePanel modelId={5} companyId={null} canManage palette={PALETTE} error={null} onSaved={onSaved}
        lineLabels={LABELS} onCreated={jest.fn()} onStale={jest.fn()} modelName="Equipo Uno" />,
    );
    return screen.getByRole('region', { name: 'Combinaciones que no existen' });
  }

  it('se añade un par de dos atributos distintos de la paleta', async () => {
    routes({ 'POST /admin/catalog/models/5/exclusions/': () => reply({ ...PALETTE, excluded_pairs: [[22, 32], [21, 32]], changed: { added: 1, total: 2 } }) });
    const onSaved = jest.fn();
    const section = mountEditorPanel(onSaved);
    expect(within(section).getByRole('list', { name: 'Combinaciones que no existen' })).toHaveTextContent('Negro + Chip');
    fireEvent.change(within(section).getByRole('combobox', { name: 'Esta opción' }), { target: { value: '21' } });
    const second = within(section).getByRole('combobox', { name: 'no existe con' });
    // The colour's own attribute is not offered as its pair, nor the options outside the palette.
    expect(within(second).queryByRole('option', { name: 'Negro' })).toBeNull();
    expect(within(section).queryByRole('option', { name: 'Rojo' })).toBeNull();
    fireEvent.change(second, { target: { value: '32' } });
    await act(async () => { fireEvent.click(within(section).getByRole('button', { name: 'Añadir exclusión' })); });
    expect(writes).toEqual([expect.objectContaining({ method: 'POST', path: '/admin/catalog/models/5/exclusions/', body: { pairs: [[21, 32]] } })]);
    expect(onSaved).toHaveBeenCalled();
    expect(within(section).getByRole('status')).toHaveTextContent('«Azul + Chip» ya no se ofrece');
  });

  it('un 409 nombra las variantes que ya son esa combinación y no cambia nada', async () => {
    routes({ 'POST /admin/catalog/models/5/exclusions/': () => reply({
      detail: 'Esa combinación ya existe en «Equipo Uno»: «Equipo Uno 128 GB Azul eSIM Seminuevo».',
      code: 'exclusion_held_by_variants', field: 'pairs',
      variants: [{ product_id: 51, name: 'Equipo Uno 128 GB Azul eSIM Seminuevo', condition_line: 'used', archived: false }],
    }, 409) });
    const onSaved = jest.fn();
    const section = mountEditorPanel(onSaved);
    fireEvent.change(within(section).getByRole('combobox', { name: 'Esta opción' }), { target: { value: '21' } });
    fireEvent.change(within(section).getByRole('combobox', { name: 'no existe con' }), { target: { value: '31' } });
    await act(async () => { fireEvent.click(within(section).getByRole('button', { name: 'Añadir exclusión' })); });
    const alert = within(section).getByRole('alert');
    expect(within(alert).getByRole('link', { name: 'Equipo Uno 128 GB Azul eSIM Seminuevo' })).toHaveAttribute('href', '/admin/products/51');
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('se quita una exclusión', async () => {
    routes({ 'DELETE /admin/catalog/models/5/exclusions/': () => reply({ ...PALETTE, excluded_pairs: undefined, changed: { removed: 1, total: 0 } }) });
    const section = mountEditorPanel();
    await act(async () => { fireEvent.click(within(section).getByRole('button', { name: 'Quitar la exclusión Negro + Chip' })); });
    expect(writes).toEqual([expect.objectContaining({ method: 'DELETE', body: { pairs: [[22, 32]] } })]);
  });

  it('sin `products.manage` sólo se leen', () => {
    routes({});
    render(
      <ModelPalettePanel modelId={5} companyId={null} canManage={false} palette={PALETTE} error={null} onSaved={jest.fn()}
        lineLabels={LABELS} modelName="Equipo Uno" />,
    );
    expect(screen.queryByRole('region', { name: 'Combinaciones que no existen' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Añadir exclusión' })).toBeNull();
  });
});

describe('el editor del modelo', () => {
  const opt = (attribute_id: number, attribute_name: string, option_id: number, label: string) => ({
    attribute_id, attribute_name, option_id, label, swatch_hex: '',
  });
  const MODEL = {
    id: 5, name: 'Equipo Uno', slug: 'equipo-uno', category_id: null, category_name: '', image_url: '', description: '',
    is_published_online: true, sort_order: 0, variant_count: 1, used_unit_count: 0, has_palette: true, is_active: true,
    variants: [{
      product_id: 52, name: 'Equipo Uno 128 GB Negro eSIM', condition_line: 'new', price: '100.00', stock: 3,
      is_published_online: false, image_count: 0, image_url: '', is_active: true, archived: false,
      options: [opt(3, 'Capacidad', 11, '128 GB'), opt(4, 'Color', 22, 'Negro'), opt(6, 'Tipo de SIM', 31, 'eSIM')],
      history: { sold: 0, units: 2, reserved: 1, stock: 3, open_orders: 1 }, identity_change: 'superior', line_change: 'blocked',
      removal: 'blocked', palette_status: 'variante_existente',
    }],
  };

  function mountEditor(palette: AdminModelPalette = PALETTE, extra: Parameters<typeof routes>[0] = {}) {
    routes({
      'GET /admin/catalog/models/5/': () => reply(MODEL),
      'GET /admin/catalog/models/5/palette/': () => reply(palette),
      'GET /admin/catalog/models/5/accessories/': () => reply({ model_id: 5, accessories: [] }),
      'PUT /admin/products/52/variant/': ({ body }) => reply({ product_id: 52, ...body }),
      ...extra,
    });
    render(<CatalogModelEditor modelId={5} canManage companyId={null} />);
  }

  const variantsRegion = async () => {
    const region = await screen.findByRole('region', { name: 'Variantes' });
    await within(region).findByText('Equipo Uno 128 GB Negro eSIM', { selector: 'a' });
    return region;
  };

  it('cambia el precio en la fila con la misma identidad de la variante', async () => {
    mountEditor();
    const region = await variantsRegion();
    fireEvent.click(within(region).getByRole('button', { name: 'Cambiar precio de Equipo Uno 128 GB Negro eSIM' }));
    const form = within(region).getByRole('form', { name: 'Precio de Equipo Uno 128 GB Negro eSIM' });
    const field = within(form).getByLabelText('Precio de venta de Equipo Uno 128 GB Negro eSIM');
    expect(field).toHaveFocus();
    fireEvent.change(field, { target: { value: '12,5' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar' })); });
    expect(writes).toEqual([expect.objectContaining({ method: 'PUT', path: '/admin/products/52/variant/',
      body: { model_id: 5, condition_line: 'new', option_ids: [11, 22, 31], price: '12.5',
        expected_model_id: 5, expected_condition_line: 'new', expected_option_ids: [11, 22, 31] } })]);
    expect(await screen.findByText('Precio de «Equipo Uno 128 GB Negro eSIM» guardado.')).toBeInTheDocument();
  });

  it('un precio mal escrito no se envía', async () => {
    mountEditor();
    const region = await variantsRegion();
    fireEvent.click(within(region).getByRole('button', { name: 'Cambiar precio de Equipo Uno 128 GB Negro eSIM' }));
    const form = within(region).getByRole('form', { name: 'Precio de Equipo Uno 128 GB Negro eSIM' });
    fireEvent.change(within(form).getByRole('textbox'), { target: { value: 'doce' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar' })); });
    expect(within(form).getByRole('alert')).toHaveTextContent('hasta dos decimales');
    expect(writes).toEqual([]);
  });

  it('publica desde la fila, por las reglas del servidor; dice el stock, los equipos y la imagen', async () => {
    mountEditor();
    const region = await variantsRegion();
    const row = within(region).getByText('Equipo Uno 128 GB Negro eSIM', { selector: 'a' }).closest('tr')!;
    expect(row).toHaveTextContent('2 equipos · 1 apartado · 1 pedido por entregar');
    expect(row).toHaveTextContent('Sin imágenes');
    const publish = await within(row).findByRole('button', { name: 'Publicar en la tienda Equipo Uno 128 GB Negro eSIM' });
    await act(async () => { fireEvent.click(publish); });
    expect(writes).toEqual([expect.objectContaining({ method: 'PUT', path: '/admin/products/52/variant/',
      body: { model_id: 5, condition_line: 'new', option_ids: [11, 22, 31], is_published_online: true,
        expected_model_id: 5, expected_condition_line: 'new', expected_option_ids: [11, 22, 31] } })]);
  });

  it('si otra persona cambió la variante, el servidor responde 409 `stale`: se dice y se vuelve a leer', async () => {
    mountEditor(PALETTE, {
      'PUT /admin/products/52/variant/': () => reply(
        { detail: 'Otra persona acaba de cambiar la variante.', code: 'stale', field: 'product' }, 409),
    });
    const region = await variantsRegion();
    const modelReads = () => reads.filter((r) => r.path === '/admin/catalog/models/5/').length;
    const before = modelReads();
    const row = within(region).getByText('Equipo Uno 128 GB Negro eSIM', { selector: 'a' }).closest('tr')!;
    await act(async () => { fireEvent.click(await within(row).findByRole('button', { name: /Publicar en la tienda/ })); });
    expect(await screen.findByText(/Otra persona cambió esta variante mientras la veías/)).toBeInTheDocument();
    await waitFor(() => expect(modelReads()).toBeGreaterThan(before));
    fireEvent.click(within(region).getByRole('button', { name: 'Cambiar precio de Equipo Uno 128 GB Negro eSIM' }));
    const form = within(region).getByRole('form', { name: 'Precio de Equipo Uno 128 GB Negro eSIM' });
    fireEvent.change(within(form).getByRole('textbox'), { target: { value: '99' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar' })); });
    expect(within(region).queryByRole('form', { name: 'Precio de Equipo Uno 128 GB Negro eSIM' })).toBeNull();
    expect(screen.queryByText('Precio de «Equipo Uno 128 GB Negro eSIM» guardado.')).toBeNull();
  });

  it('un modelo sin paleta lo dice arriba y pide confirmación para clasificar un producto', async () => {
    mountEditor({ ...PALETTE, has_palette: false, combinations: [], excluded_pairs: undefined } as AdminModelPalette);
    const note = await screen.findByRole('note', { name: 'Modelo sin paleta' });
    expect(note).toHaveTextContent('Este modelo no tiene paleta.');
    const region = await variantsRegion();
    fireEvent.click(within(region).getByRole('button', { name: '+ Agregar variante' }));
    const form = within(region).getByRole('form', { name: 'Agregar variante' });
    expect(within(form).getByRole('checkbox', { name: 'Confirmo que estas opciones corresponden a este modelo' })).not.toBeChecked();
  });
});
