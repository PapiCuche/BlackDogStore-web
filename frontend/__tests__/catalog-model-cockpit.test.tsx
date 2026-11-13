import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CatalogModelEditor } from '@/app/admin/components/CatalogModelEditor';
import { ModelStatusSummary } from '@/app/admin/components/ModelStatusSummary';
import type { AdminModelPalette } from '@/app/admin/lib/internal-api';
import {
  COMMERCIAL_STATE_LABELS, commercialState, pendingCreations, problemMessages, reviewReasons, variantNamePreview,
} from '@/app/admin/lib/palette';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-COMMERCIAL-01 · A1b-2b — el «cockpit» del modelo.
 *
 * Con la API simulada (crear y habilitar de verdad lo prueban Django y Playwright):
 *   - el resumen del modelo sale de `payload.summary` (completas / borradores / por revisar), sin llamadas extra;
 *   - «Crear variante» sólo en las combinaciones completas sin variante, con el nombre propuesto editable;
 *   - «Habilitar para la venta» sólo en borradores pendientes y sólo con `products.manage`, con «publicar» opcional
 *     y apagado, y los problemas del servidor como lista legible;
 *   - «Borrador» / «Habilitada» / «Publicada» se distinguen; lo histórico se ve como siempre;
 *   - un doble clic no manda dos POST; 404/409/red se explican.
 *
 * Nombres y valores inventados: nada aquí es de Apple ni de la tienda piloto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: Record<string, unknown> };
let writes: Call[];

const option = (id: number, label: string, in_palette: boolean, swatch_hex = '') => ({ id, label, swatch_hex, sort_order: 0, in_palette });

const pv = (over: Record<string, unknown>) => ({
  condition_line: 'new', draft_origin: '', draft_pending: false, sale_enabled_at: null, has_image: true,
  price_set: true, is_published_online: false, is_active: true, is_serialized: false, status: 'variante_existente',
  ...over,
});

const PALETTE = {
  model_id: 5, model_name: 'Equipo Z', has_palette: true,
  attributes: [
    { id: 3, name: 'Capacidad', kind: 'text', role: 'capacity', sort_order: 0,
      options: [option(11, '128 GB', true), option(12, '256 GB', true)] },
    { id: 4, name: 'Color', kind: 'color', role: 'color', sort_order: 1,
      options: [option(21, 'Azul', true, '#1F3A93'), option(22, 'Negro', true, '#111111'), option(23, 'Rojo', false, '#B91C1C')] },
    { id: 6, name: 'Tipo de SIM', kind: 'text', role: 'sim', sort_order: 2, options: [option(31, 'eSIM', false)] },
  ],
  combinations: [
    { key: '11-21', option_ids: [11, 21], labels: { '3': '128 GB', '4': 'Azul' }, status: 'variante_existente',
      products: [{ product_id: 51, name: 'Equipo Z 128 Azul', condition_line: 'new' }] },
    { key: '11-22', option_ids: [11, 22], labels: { '3': '128 GB', '4': 'Negro' }, status: 'combinacion_valida_sin_variante', products: [] },
    { key: '12-21', option_ids: [12, 21], labels: { '3': '256 GB', '4': 'Azul' }, status: 'variante_existente',
      products: [{ product_id: 53, name: 'Equipo Z 256 GB Azul', condition_line: 'new' }] },
    { key: '12-22', option_ids: [12, 22], labels: { '3': '256 GB', '4': 'Negro' }, status: 'variante_existente',
      products: [{ product_id: 54, name: 'Equipo Z 256 GB Negro', condition_line: 'new' }] },
  ],
  variants: [
    // Historical, published: shown as always.
    pv({ product_id: 51, name: 'Equipo Z 128 Azul', option_ids: [11, 21], is_published_online: true }),
    // Historical, outside the palette, hidden: «Oculta», never «Borrador».
    pv({ product_id: 52, name: 'Equipo Z 128 Rojo', option_ids: [11, 23], status: 'fuera_de_paleta', has_image: false }),
    // A pending draft from the palette: no price, no picture.
    pv({ product_id: 53, name: 'Equipo Z 256 GB Azul', option_ids: [12, 21], draft_origin: 'palette', draft_pending: true,
      price_set: false, has_image: false }),
    // Enabled, not published.
    pv({ product_id: 54, name: 'Equipo Z 256 GB Negro', option_ids: [12, 22], draft_origin: 'palette',
      sale_enabled_at: '2026-11-12T15:00:00+00:00' }),
  ],
  summary: { combinations: 4, existing: 3, valid_without_variant: 1, outside: 1, incomplete: 0,
    pending_drafts: 1, needs_review: 2, roles_missing: ['sim'] },
  truncated: false,
} as unknown as AdminModelPalette;

const opt = (attribute_id: number, attribute_name: string, option_id: number, label: string) => ({
  attribute_id, attribute_name, option_id, label, swatch_hex: '',
});

const mv = (product_id: number, name: string, price: string, published: boolean, options: ReturnType<typeof opt>[]) => ({
  product_id, name, condition_line: 'new', price, stock: 0, is_published_online: published, options,
  history: { sold: 0, units: 0, reserved: 0 }, identity_change: 'free', line_change: 'free', removal: 'free',
  palette_status: 'variante_existente',
});

const MODEL = {
  id: 5, name: 'Equipo Z', slug: 'equipo-z', category_id: null, category_name: null, image_url: '',
  description: '', is_published_online: true, sort_order: 0, variant_count: 4, used_unit_count: 0, has_palette: true,
  variants: [
    mv(51, 'Equipo Z 128 Azul', '100.00', true, [opt(3, 'Capacidad', 11, '128 GB'), opt(4, 'Color', 21, 'Azul')]),
    { ...mv(52, 'Equipo Z 128 Rojo', '100.00', false, [opt(3, 'Capacidad', 11, '128 GB'), opt(4, 'Color', 23, 'Rojo')]),
      palette_status: 'fuera_de_paleta' },
    mv(53, 'Equipo Z 256 GB Azul', '0.00', false, [opt(3, 'Capacidad', 12, '256 GB'), opt(4, 'Color', 21, 'Azul')]),
    mv(54, 'Equipo Z 256 GB Negro', '150.00', false, [opt(3, 'Capacidad', 12, '256 GB'), opt(4, 'Color', 22, 'Negro')]),
  ],
};

const ATTRIBUTES = PALETTE.attributes.map(({ options, ...attribute }) => ({
  ...attribute, options: options.map((o) => ({ id: o.id, label: o.label, swatch_hex: o.swatch_hex, sort_order: o.sort_order })),
}));

/** What the next write answers, by path; default: a created / enabled draft. */
let answers: Record<string, () => Response | Promise<Response>>;
/** A1b-2b debts: the next palette reads, in order (then the default palette). */
let paletteQueue: (() => Response | Promise<Response>)[];
/** The product serializer's `is_pending_draft`, by product (A1b-2b debt 1). Default: the palette's flag. */
let productDrafts: Record<number, boolean>;

function mockApi(palette: AdminModelPalette = PALETTE) {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.replace(/^.*\/api/, '').replace(/\?.*$/, '');
    if (method !== 'GET') {
      const body = JSON.parse(String(init.body ?? '{}'));
      writes.push({ method, path, body });
      if (answers[path]) return answers[path]();
      if (path === '/admin/catalog/models/5/variants/') {
        return reply({ id: 60, name: body.name || 'Equipo Z 128 GB Negro', price: '0.00', is_published_online: false,
          draft_origin: 'palette', sale_enabled_at: null, is_pending_draft: true, model_id: 5, option_ids: body.option_ids }, 201);
      }
      if (/^\/admin\/products\/\d+\/enable-sale\/$/.test(path)) {
        return reply({ id: 53, name: 'Equipo Z 256 GB Azul', price: '150.00', is_published_online: Boolean(body.publish),
          draft_origin: 'palette', sale_enabled_at: '2026-11-12T16:00:00+00:00', is_pending_draft: false });
      }
      return reply({ product_id: 7 });
    }
    if (path === '/admin/catalog/models/5/palette/') {
      const next = paletteQueue.shift();
      return next ? next() : reply(palette);
    }
    const product = /^\/admin\/products\/(\d+)\/$/.exec(path);
    if (product) {
      const id = Number(product[1]);
      const pending = id in productDrafts ? productDrafts[id] : Boolean(palette.variants.find((v) => v.product_id === id)?.draft_pending);
      return reply({ id, name: `Producto ${id}`, is_pending_draft: pending });
    }
    if (path === '/admin/catalog/models/5/') return reply(MODEL);
    if (path === '/admin/catalog/models/') return reply({ results: [{ ...MODEL, variants: undefined }] });
    if (path === '/admin/catalog/attributes/') return reply(ATTRIBUTES);
    if (path === '/admin/catalog/settings/') return reply({ new_line_label: 'De caja', used_line_label: 'Reacondicionados' });
    if (path === '/admin/categories/') return reply([]);
    return reply({}, 404);
  });
}

beforeEach(() => {
  writes = [];
  answers = {};
  paletteQueue = [];
  productDrafts = {};
  window.history.replaceState(null, '', '/admin/products/models/5');
  mockApi();
});

async function mount(canManage = true) {
  render(<CatalogModelEditor modelId={5} canManage={canManage} companyId={null} />);
  const variants = await screen.findByRole('region', { name: 'Variantes' });
  await within(variants).findByText('Equipo Z 128 Azul', { selector: 'a' });
  // The palette arrives on its own read; the states need it.
  await within(variants).findByText('Borrador');
  return variants;
}

const palettePanel = () => screen.getByRole('region', { name: 'Opciones de este modelo' });
const rowOf = (region: HTMLElement, name: string) => within(region).getByText(name, { selector: 'a' }).closest('tr')!;
const paletteReads = () => send.mock.calls.filter(([u, init]) => String(u).includes('/palette/') && !(init as RequestInit | undefined)?.method).length;

describe('ayudantes del cockpit', () => {
  it('el nombre propuesto es el del servidor: modelo + opciones en orden de rol, «Seminuevo» para la línea usada', () => {
    expect(variantNamePreview('Equipo Z', ['256 GB', 'Azul'], 'new')).toBe('Equipo Z 256 GB Azul');
    expect(variantNamePreview('Equipo Z', ['256 GB', 'Azul'], 'used')).toBe('Equipo Z 256 GB Azul Seminuevo');
    expect(variantNamePreview('  Equipo   Z ', [' 256  GB', 'Azul '], 'new')).toBe('Equipo Z 256 GB Azul');
    expect(Array.from(variantNamePreview('x'.repeat(300), ['a'], 'new'))).toHaveLength(255);
  });

  it('el estado comercial distingue borrador, habilitada, publicada y oculta', () => {
    const base = { draft_pending: false, sale_enabled_at: null, is_published_online: false };
    expect(commercialState({ ...base, draft_pending: true })).toBe('draft');
    expect(commercialState({ ...base, sale_enabled_at: '2026-11-12T00:00:00Z' })).toBe('enabled');
    expect(commercialState({ ...base, sale_enabled_at: '2026-11-12T00:00:00Z', is_published_online: true })).toBe('published');
    expect(commercialState({ ...base, is_published_online: true })).toBe('published');
    expect(commercialState(base)).toBe('hidden');
    // A pending draft never reads as sold, even if some other path left it «published».
    expect(commercialState({ ...base, draft_pending: true, is_published_online: true })).toBe('draft');
    expect(COMMERCIAL_STATE_LABELS).toEqual({ draft: 'Borrador', enabled: 'Habilitada', published: 'Publicada', hidden: 'Oculta' });
  });

  it('«Revisar» junta borrador, sin precio (sólo línea nueva), sin imagen, fuera de paleta e incompleta', () => {
    expect(reviewReasons(pv({ draft_pending: true, price_set: false, has_image: false }) as never)).toEqual(['draft', 'no_price', 'no_image']);
    expect(reviewReasons(pv({ condition_line: 'used', price_set: false }) as never)).toEqual([]);
    expect(reviewReasons(pv({ status: 'incompleta' }) as never)).toEqual(['incompleta']);
    expect(reviewReasons(pv({}) as never)).toEqual([]);
  });

  it('los problemas del servidor se leen con sus textos, y los desconocidos no se pierden', () => {
    expect(problemMessages(['no_price', 'no_category', 'raro'], { no_price: 'No tiene precio de venta.' })).toEqual([
      'No tiene precio de venta.', 'No tiene categoría.', 'raro',
    ]);
  });
});

describe('ModelStatusSummary', () => {
  it('resume completas, borradores, por revisar y lo que falta en la paleta, sólo con `payload.summary`', () => {
    render(<ModelStatusSummary palette={PALETTE} />);
    const summary = screen.getByRole('region', { name: 'Resumen del modelo' });
    expect(within(summary).getByText('Completas').nextSibling).toHaveTextContent('2');
    expect(within(summary).getByText('Borradores').nextSibling).toHaveTextContent('1');
    expect(within(summary).getByText('Por revisar').nextSibling).toHaveTextContent('2');
    expect(within(summary).getByText('Sin variante').nextSibling).toHaveTextContent('1');
    expect(within(summary).getByText(/La paleta todavía no incluye: SIM/)).toBeInTheDocument();
  });

  it('sin paleta lo dice; con una respuesta antigua (sin los campos nuevos) no inventa números', () => {
    const { unmount } = render(<ModelStatusSummary palette={{ ...PALETTE, has_palette: false } as AdminModelPalette} />);
    expect(screen.getByText(/todavía no tiene paleta/)).toBeInTheDocument();
    unmount();
    const old = {
      ...PALETTE, variants: PALETTE.variants.map(({ product_id, name, condition_line, option_ids, status }) => ({ product_id, name, condition_line, option_ids, status })),
      summary: { combinations: 4, existing: 3, valid_without_variant: 1, outside: 1, incomplete: 0 },
    } as AdminModelPalette;
    render(<ModelStatusSummary palette={old} />);
    const summary = screen.getByRole('region', { name: 'Resumen del modelo' });
    expect(summary).not.toHaveTextContent('NaN');
    expect(within(summary).queryByText('Borradores')).toBeNull();
  });

  it('mientras carga no muestra cifras', () => {
    render(<ModelStatusSummary palette={null} />);
    expect(screen.getByRole('region', { name: 'Resumen del modelo' })).toHaveTextContent('Cargando');
  });
});

describe('«Crear variante» desde la paleta', () => {
  it('sólo en combinaciones completas sin variante, y sólo con `products.manage`', async () => {
    await mount();
    const table = within(palettePanel()).getByRole('table', { name: 'Configuraciones de la paleta' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(within(rows[0]).queryByRole('button', { name: /Crear variante/ })).toBeNull();          // with a variant
    expect(within(rows[1]).getByRole('button', { name: 'Crear variante 128 GB · Negro' })).toBeInTheDocument();
    expect(within(table).getAllByRole('button', { name: /Crear variante/ })).toHaveLength(1);
  });

  it('sin `products.manage` no se ofrece', async () => {
    await mount(false);
    expect(within(palettePanel()).queryByRole('button', { name: /Crear variante/ })).toBeNull();
  });

  it('propone el nombre, lo deja editar y envía sólo lo que el servidor admite; luego recarga la paleta', async () => {
    await mount();
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    const name = within(form).getByLabelText('Nombre de la variante') as HTMLInputElement;
    expect(name.value).toBe('Equipo Z 128 GB Negro');
    expect(form).toHaveTextContent(/borrador/i);
    expect(form).toHaveTextContent(/sin precio/i);
    // The line changes the proposal while the name was not touched.
    fireEvent.change(within(form).getByLabelText('Línea'), { target: { value: 'used' } });
    expect(name.value).toBe('Equipo Z 128 GB Negro Seminuevo');
    fireEvent.change(within(form).getByLabelText('Línea'), { target: { value: 'new' } });
    fireEvent.change(name, { target: { value: 'Equipo Z 128 Negro (caja)' } });
    const before = paletteReads();
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' })); });
    expect(writes).toEqual([{ method: 'POST', path: '/admin/catalog/models/5/variants/',
      body: { condition_line: 'new', option_ids: [11, 22], name: 'Equipo Z 128 Negro (caja)' } }]);
    expect(await within(palettePanel()).findByRole('status')).toHaveTextContent(/Equipo Z 128 Negro \(caja\)/);
    expect(within(palettePanel()).getByRole('status')).toHaveTextContent(/borrador/i);
    await waitFor(() => expect(paletteReads()).toBeGreaterThan(before));
  });

  it('con el nombre propuesto sin tocar, deja que el servidor lo genere (no envía `name`)', async () => {
    await mount();
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' })); });
    expect(writes).toHaveLength(1);
    expect(writes[0].body).toEqual({ condition_line: 'new', option_ids: [11, 22] });
  });

  it('un doble clic manda un solo POST y el botón queda ocupado', async () => {
    let release: (r: Response) => void = () => {};
    answers['/admin/catalog/models/5/variants/'] = () => new Promise((resolve) => { release = resolve; });
    await mount();
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    const submit = within(form).getByRole('button', { name: 'Crear variante' });
    // Both clicks inside ONE act: React has not re-rendered (nor disabled the button) between them.
    act(() => { fireEvent.click(submit); fireEvent.click(submit); });
    expect(writes).toHaveLength(1);
    expect(within(form).getByRole('button', { name: 'Creando…' })).toBeDisabled();
    await act(async () => { release(reply({ id: 60, name: 'Equipo Z 128 GB Negro' }, 201)); });
    expect(writes).toHaveLength(1);
  });

  it('un duplicado se explica con el texto del servidor en el panel y la paleta se vuelve a leer', async () => {
    answers['/admin/catalog/models/5/variants/'] = () => reply(
      { detail: '«Equipo Z 128 Negro» ya es esa variante de «Equipo Z».', code: 'duplicate', field: 'option_ids' }, 400);
    await mount();
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    fireEvent.change(within(form).getByLabelText('Nombre de la variante'), { target: { value: 'Otro nombre' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' })); });
    // The palette moved: the panel keeps the reason (with the focus) and reads the palette again.
    const alert = within(palettePanel()).getByRole('alert');
    expect(alert).toHaveTextContent('ya es esa variante');
    expect(alert).toHaveFocus();
    expect(within(palettePanel()).queryByRole('form', { name: 'Crear variante 128 GB · Negro' })).toBeNull();
    await waitFor(() => expect(paletteReads()).toBeGreaterThan(1));
  });

  it('sin conexión lo dice en castellano', async () => {
    answers['/admin/catalog/models/5/variants/'] = () => { throw new TypeError('Failed to fetch'); };
    await mount();
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' })); });
    expect(within(form).getByRole('alert')).toHaveTextContent(/No se pudo conectar/);
  });

  it('con cambios de paleta sin guardar no se crea nada sobre una paleta vieja', async () => {
    await mount();
    fireEvent.click(within(palettePanel()).getByRole('checkbox', { name: 'Rojo' }));
    expect(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' })).toBeDisabled();
  });
});

describe('Estados y «Habilitar para la venta»', () => {
  it('distingue Borrador / Habilitada / Publicada; lo histórico se ve como siempre', async () => {
    const variants = await mount();
    expect(rowOf(variants, 'Equipo Z 256 GB Azul')).toHaveTextContent('Borrador');
    expect(rowOf(variants, 'Equipo Z 256 GB Azul')).toHaveTextContent(/Creada desde la paleta/);
    expect(rowOf(variants, 'Equipo Z 256 GB Negro')).toHaveTextContent('Habilitada');
    expect(rowOf(variants, 'Equipo Z 256 GB Negro')).toHaveTextContent(/12\/11\/2026/);
    const published = rowOf(variants, 'Equipo Z 128 Azul');
    expect(published).toHaveTextContent('Publicada');
    expect(published).not.toHaveTextContent(/Borrador|Creada desde la paleta|Habilitada/);
    const hidden = rowOf(variants, 'Equipo Z 128 Rojo');
    expect(hidden).toHaveTextContent('Oculta');
    expect(hidden).not.toHaveTextContent(/Borrador|Creada desde la paleta/);
  });

  it('el botón sólo está en el borrador pendiente', async () => {
    const variants = await mount();
    expect(within(variants).getAllByRole('button', { name: /Habilitar .* para la venta/ })).toHaveLength(1);
    expect(within(rowOf(variants, 'Equipo Z 256 GB Azul')).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' })).toBeInTheDocument();
  });

  it('sin `products.manage` no se ofrece y se explica quién puede', async () => {
    const variants = await mount(false);
    expect(within(variants).queryByRole('button', { name: /para la venta/ })).toBeNull();
    expect(rowOf(variants, 'Equipo Z 256 GB Azul')).toHaveTextContent(/Sólo quien administra el catálogo/);
  });

  it('pide confirmación con «publicar» apagado y envía `publish: false`', async () => {
    const variants = await mount();
    fireEvent.click(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' }));
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    const publish = within(dialog).getByRole('checkbox', { name: /Publicar también en la tienda/ });
    expect(publish).not.toBeChecked();
    expect(dialog).toHaveTextContent(/no crea stock/i);
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Habilitar para la venta' })); });
    expect(writes).toEqual([{ method: 'POST', path: '/admin/products/53/enable-sale/', body: { publish: false } }]);
    expect(await screen.findByText(/quedó habilitada para la venta/)).toHaveAttribute('role', 'status');
    expect(screen.getByText(/quedó habilitada para la venta/)).toHaveTextContent(/no está publicada/);
  });

  it('con «publicar» marcado envía `publish: true` y lo dice', async () => {
    const variants = await mount();
    fireEvent.click(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' }));
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    fireEvent.click(within(dialog).getByRole('checkbox', { name: /Publicar también en la tienda/ }));
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Habilitar para la venta' })); });
    expect(writes[0].body).toEqual({ publish: true });
    expect(await screen.findByText(/habilitada para la venta y publicada/)).toBeInTheDocument();
  });

  it('`not_ready` muestra cada problema en una lista legible y no cierra la confirmación', async () => {
    answers['/admin/products/53/enable-sale/'] = () => reply({
      detail: 'La variante no está lista para la venta.', code: 'not_ready', field: 'product',
      problems: ['no_price', 'no_category', 'incompleta'],
      labels: { no_price: 'No tiene precio de venta.', no_category: 'No tiene categoría.', incompleta: 'Le falta una opción de la paleta del modelo.' },
    }, 400);
    const variants = await mount();
    fireEvent.click(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' }));
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Habilitar para la venta' })); });
    const alert = within(dialog).getByRole('alert');
    expect(alert).toHaveTextContent('La variante no está lista para la venta.');
    const items = within(alert).getAllByRole('listitem').map((li) => li.textContent);
    expect(items).toEqual(['No tiene precio de venta.', 'No tiene categoría.', 'Le falta una opción de la paleta del modelo.']);
    expect(within(dialog).getByRole('button', { name: 'Habilitar para la venta' })).toBeEnabled();
  });

  it('`already_enabled` (409) lo explica y vuelve a leer el modelo', async () => {
    answers['/admin/products/53/enable-sale/'] = () => reply({ detail: 'Esta variante ya está habilitada para la venta.', code: 'already_enabled', field: 'product' }, 409);
    const variants = await mount();
    const before = paletteReads();
    fireEvent.click(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' }));
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Habilitar para la venta' })); });
    // The confirmation is gone (the row moved); the page says why, takes the focus and reads the model again.
    expect(within(variants).queryByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' })).toBeNull();
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/ya está habilitada/);
    expect(alert).toHaveFocus();
    await waitFor(() => expect(paletteReads()).toBeGreaterThan(before));
  });

  it('404: la variante ya no existe en esta empresa', async () => {
    answers['/admin/products/53/enable-sale/'] = () => reply({ detail: 'No encontrado.' }, 404);
    const variants = await mount();
    fireEvent.click(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' }));
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Habilitar para la venta' })); });
    expect(screen.getByRole('alert')).toHaveTextContent(/ya no existe/);
    expect(within(variants).queryByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' })).toBeNull();
  });

  it('500 sin cuerpo útil: mensaje genérico, nunca el HTML del servidor', async () => {
    answers['/admin/products/53/enable-sale/'] = () => ({
      ok: false, status: 500, json: () => Promise.reject(new Error('no json')), headers: new Headers(),
    } as unknown as Response);
    const variants = await mount();
    fireEvent.click(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' }));
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Habilitar para la venta' })); });
    expect(within(dialog).getByRole('alert')).toHaveTextContent('No se pudo habilitar la variante.');
  });

  it('un doble clic manda un solo POST', async () => {
    let release: (r: Response) => void = () => {};
    answers['/admin/products/53/enable-sale/'] = () => new Promise((resolve) => { release = resolve; });
    const variants = await mount();
    fireEvent.click(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' }));
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    const go = within(dialog).getByRole('button', { name: 'Habilitar para la venta' });
    act(() => { fireEvent.click(go); fireEvent.click(go); });
    expect(writes).toHaveLength(1);
    expect(within(dialog).getByRole('button', { name: 'Habilitando…' })).toBeDisabled();
    // Meanwhile the other row actions wait: nothing can close this confirmation and lose its answer.
    expect(within(variants).getByRole('button', { name: 'Editar variante Equipo Z 256 GB Azul' })).toBeDisabled();
    expect(within(variants).getByRole('button', { name: /^Por revisar/ })).toBeDisabled();
    await act(async () => { release(reply({ id: 53, name: 'Equipo Z 256 GB Azul', is_published_online: false })); });
    expect(writes).toHaveLength(1);
  });

  it('«Revisar» deja sólo lo que pide atención, con su motivo', async () => {
    const variants = await mount();
    fireEvent.click(within(variants).getByRole('button', { name: /^Por revisar/ }));
    expect(within(variants).getByRole('button', { name: /^Por revisar/ })).toHaveAttribute('aria-pressed', 'true');
    const names = within(variants).getAllByRole('link').map((a) => a.textContent).filter((t) => t?.startsWith('Equipo Z'));
    expect(names).toEqual(['Equipo Z 128 Rojo', 'Equipo Z 256 GB Azul']);
    const draft = rowOf(variants, 'Equipo Z 256 GB Azul');
    expect(draft).toHaveTextContent('Sin precio');
    expect(draft).toHaveTextContent('Sin imagen');
    fireEvent.click(within(variants).getByRole('button', { name: /^Todas/ }));
    expect(within(variants).getByText('Equipo Z 128 Azul', { selector: 'a' })).toBeInTheDocument();
  });

  it('al editar un borrador pendiente, «Publicado en la tienda» no se ofrece hasta habilitarlo', async () => {
    const variants = await mount();
    fireEvent.click(within(variants).getByRole('button', { name: 'Editar variante Equipo Z 256 GB Azul' }));
    const form = within(variants).getByRole('form', { name: 'Editar variante Equipo Z 256 GB Azul' });
    expect(within(form).getByRole('checkbox', { name: 'Publicado en la tienda' })).toBeDisabled();
    expect(form).toHaveTextContent(/habilítala para la venta/i);
    fireEvent.click(within(variants).getByRole('button', { name: 'Editar variante Equipo Z 256 GB Negro' }));
    const enabled = within(variants).getByRole('form', { name: 'Editar variante Equipo Z 256 GB Negro' });
    // Offered once the product itself says it is not a draft (its own read, not the palette's).
    await waitFor(() => expect(within(enabled).getByRole('checkbox', { name: 'Publicado en la tienda' })).toBeEnabled());
  });

  it('un borrador sin precio dice «Sin precio», nunca «S/ 0.00», y su motivo se ve sin filtrar', async () => {
    const variants = await mount();
    const draft = rowOf(variants, 'Equipo Z 256 GB Azul');
    expect(draft).toHaveTextContent('Sin precio');
    expect(draft).not.toHaveTextContent('S/ 0.00');
    // A historical row keeps its price as always.
    expect(rowOf(variants, 'Equipo Z 128 Azul')).toHaveTextContent(/S\/\s*100\.00/);
  });

  it('«Cancelar» devuelve el foco al botón que abrió la confirmación', async () => {
    const variants = await mount();
    const opener = within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' });
    fireEvent.click(opener);
    expect(opener).toHaveAttribute('aria-controls');
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    expect(dialog.id).toBe(opener.getAttribute('aria-controls'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancelar' }));
    expect(opener).toHaveFocus();
  });

  it('«Ver las variantes por revisar» activa el filtro y le lleva el foco', async () => {
    const variants = await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Ver las variantes por revisar' }));
    const review = within(variants).getByRole('button', { name: /^Por revisar/ });
    expect(review).toHaveAttribute('aria-pressed', 'true');
    expect(review).toHaveFocus();
  });
});

describe('coherencia y respuestas antiguas', () => {
  it('una variante a la que sólo le falta la imagen cuenta igual en el resumen y en el filtro', async () => {
    const onlyImage = {
      ...PALETTE,
      variants: PALETTE.variants.map((v) => (v.product_id === 51 ? { ...v, has_image: false } : v)),
    } as AdminModelPalette;
    mockApi(onlyImage);
    const variants = await mount();
    const summary = screen.getByRole('region', { name: 'Resumen del modelo' });
    expect(within(summary).getByText('Por revisar').nextSibling).toHaveTextContent('3');
    expect(within(summary).getByText('Completas').nextSibling).toHaveTextContent('1');
    expect(within(variants).getByRole('button', { name: /^Por revisar/ })).toHaveTextContent('Por revisar (3)');
  });

  it('si la paleta no se puede leer, el resumen lo dice y «Variantes» avisa (sin filtro inventado)', async () => {
    send.mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input).replace(/^.*\/api/, '').replace(/\?.*$/, '');
      if (path === '/admin/catalog/models/5/palette/') return reply({ detail: 'Error interno.' }, 500);
      if (path === '/admin/catalog/models/5/') return reply(MODEL);
      if (path === '/admin/catalog/attributes/') return reply(ATTRIBUTES);
      if (path === '/admin/catalog/settings/') return reply({ new_line_label: '', used_line_label: '' });
      if (path === '/admin/categories/') return reply([]);
      return reply({}, 404);
    });
    render(<CatalogModelEditor modelId={5} canManage companyId={null} />);
    const variants = await screen.findByRole('region', { name: 'Variantes' });
    const summary = screen.getByRole('region', { name: 'Resumen del modelo' });
    await within(summary).findByText(/No se pudo leer el resumen/);
    expect(variants).toHaveTextContent(/No se pudo leer el estado comercial/);
    expect(within(variants).queryByRole('button', { name: /^Por revisar/ })).toBeNull();
  });

  it('con una respuesta sin los campos nuevos no ofrece «Por revisar» ni inventa borradores', async () => {
    mockApi({
      ...PALETTE,
      variants: PALETTE.variants.map(({ product_id, name, condition_line, option_ids, status }) => ({ product_id, name, condition_line, option_ids, status })),
      summary: { combinations: 4, existing: 3, valid_without_variant: 1, outside: 1, incomplete: 0 },
    } as AdminModelPalette);
    render(<CatalogModelEditor modelId={5} canManage companyId={null} />);
    const variants = await screen.findByRole('region', { name: 'Variantes' });
    await within(variants).findByText('Equipo Z 128 Azul', { selector: 'a' });
    await waitFor(() => expect(paletteReads()).toBeGreaterThan(0));
    expect(within(variants).queryByRole('button', { name: /^Por revisar/ })).toBeNull();
    expect(within(variants).queryByText('Borrador')).toBeNull();
    expect(within(variants).queryByRole('button', { name: /para la venta/ })).toBeNull();
  });

  it('el nombre propuesto usa el nombre actual del modelo aunque la paleta traiga el anterior', async () => {
    mockApi({ ...PALETTE, model_name: 'Nombre anterior' } as AdminModelPalette);
    await mount();
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    expect((within(form).getByLabelText('Nombre de la variante') as HTMLInputElement).value).toBe('Equipo Z 128 GB Negro');
  });

  it('«Cancelar» del formulario de creación devuelve el foco a su botón; mientras crea, la paleta no se toca', async () => {
    let release: (r: Response) => void = () => {};
    answers['/admin/catalog/models/5/variants/'] = () => new Promise((resolve) => { release = resolve; });
    await mount();
    const opener = within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' });
    fireEvent.click(opener);
    let form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    expect(form.id).toBe(opener.getAttribute('aria-controls'));
    fireEvent.click(within(form).getByRole('button', { name: 'Cancelar' }));
    expect(opener).toHaveFocus();
    fireEvent.click(opener);
    form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' }));
    expect(within(palettePanel()).getByRole('checkbox', { name: 'Rojo' })).toBeDisabled();
    expect(opener).toBeDisabled();
    await act(async () => { release(reply({ id: 60, name: 'Equipo Z 128 GB Negro' }, 201)); });
  });
});

/**
 * A1b-2b — las tres deudas de frontend registradas en `02_` (regresión).
 *   1. «Publicado en la tienda» no se puede marcar en un borrador pendiente mientras la paleta carga o si su lectura
 *      falló, y el borrador se lee del PRODUCTO (`is_pending_draft` del serializador), no de la paleta.
 *   2. Si una relectura de la paleta falla, la tabla dice «No se pudo actualizar; datos de hh:mm» y bloquea «Crear
 *      variante» y «Habilitar» hasta que una lectura salga bien.
 *   3. «Crear variante» no reaparece mientras la relectura tras crear no confirma la combinación.
 */
describe('deudas A1b-2b', () => {
  const never = () => new Promise<Response>(() => {});

  async function mountModelOnly() {
    render(<CatalogModelEditor modelId={5} canManage companyId={null} />);
    const variants = await screen.findByRole('region', { name: 'Variantes' });
    await within(variants).findByText('Equipo Z 128 Azul', { selector: 'a' });
    return variants;
  }

  const publishBox = (variants: HTMLElement, name: string) => {
    fireEvent.click(within(variants).getByRole('button', { name: `Editar variante ${name}` }));
    const form = within(variants).getByRole('form', { name: `Editar variante ${name}` });
    return { form, box: within(form).getByRole('checkbox', { name: 'Publicado en la tienda' }) };
  };

  it('1a. mientras la paleta carga, «Publicado» no se puede marcar (aunque el producto no sea borrador)', async () => {
    paletteQueue.push(never);
    const variants = await mountModelOnly();
    const { form, box } = publishBox(variants, 'Equipo Z 256 GB Negro');
    expect(box).toBeDisabled();
    // Still disabled once the product answered: the palette is not loaded.
    await waitFor(() => expect(form).toHaveTextContent(/paleta del modelo no está al día|Comprobando/));
    expect(box).toBeDisabled();
    expect(box).not.toBeChecked();
  });

  it('1b. si la lectura de la paleta falló, «Publicado» no se puede marcar', async () => {
    paletteQueue.push(() => reply({ detail: 'Error interno.' }, 500));
    const variants = await mountModelOnly();
    const { form, box } = publishBox(variants, 'Equipo Z 256 GB Negro');
    await waitFor(() => expect(form).toHaveTextContent(/no está al día/));
    expect(box).toBeDisabled();
  });

  it('1c. el borrador se lee del producto: aunque la paleta diga que no lo es, no se ofrece publicar', async () => {
    productDrafts[54] = true;
    const variants = await mount();
    const { form, box } = publishBox(variants, 'Equipo Z 256 GB Negro');
    await waitFor(() => expect(form).toHaveTextContent(/Es un borrador/));
    expect(box).toBeDisabled();
    const productReads = send.mock.calls.filter(([u]) => /\/admin\/products\/54\/(\?|$)/.test(String(u)));
    expect(productReads.length).toBeGreaterThan(0);
  });

  it('1d. si no se puede leer el producto, no se ofrece publicar y se explica; ocultar una publicada sí', async () => {
    const variants = await mount();
    send.mockImplementationOnce(async () => reply({}, 500));
    const { form, box } = publishBox(variants, 'Equipo Z 256 GB Negro');
    await waitFor(() => expect(form).toHaveTextContent(/No se pudo comprobar si es un borrador/));
    expect(box).toBeDisabled();
    fireEvent.click(within(form).getByRole('button', { name: 'Cancelar' }));
    const published = publishBox(variants, 'Equipo Z 128 Azul');
    expect(published.box).toBeEnabled();
  });

  it('M1. marcado «Publicado» y luego bloqueado (paleta vieja): al guardar no se publica nada', async () => {
    const variants = await mount();
    const { form, box } = publishBox(variants, 'Equipo Z 256 GB Negro');
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.click(box);
    expect(box).toBeChecked();
    // A re-read fails (after a creation): the palette is stale and publishing is locked.
    paletteQueue.push(() => reply({ detail: 'Error interno.' }, 500));
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const create = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    await act(async () => { fireEvent.click(within(create).getByRole('button', { name: 'Crear variante' })); });
    await within(palettePanel()).findByText(/No se pudo actualizar/);
    expect(box).toBeDisabled();
    expect(box).not.toBeChecked();
    fireEvent.change(within(form).getByLabelText('Precio (S/)'), { target: { value: '160' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar cambios' })); });
    const put = writes.find((w) => w.method === 'PUT' && w.path === '/admin/products/54/variant/');
    expect(put).toBeDefined();
    expect(put!.body).not.toHaveProperty('is_published_online');
  });

  it('2. una relectura fallida marca la paleta como vieja con su hora y bloquea «Crear variante» y «Habilitar»', async () => {
    const variants = await mount();
    // The re-read after a creation fails.
    paletteQueue.push(() => reply({ detail: 'Error interno.' }, 500));
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' })); });
    const stale = await within(palettePanel()).findByText(/No se pudo actualizar; datos de \d{2}:\d{2}/);
    expect(stale.closest('[role="alert"]')).not.toBeNull();
    // The table still shows the last good state, but nothing is created or enabled on it.
    expect(within(palettePanel()).getByRole('table', { name: 'Configuraciones de la paleta' })).toBeInTheDocument();
    for (const button of within(palettePanel()).queryAllByRole('button', { name: /^Crear variante / })) expect(button).toBeDisabled();
    expect(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' })).toBeDisabled();
    // A good read lifts the block.
    await act(async () => { fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Volver a leer' })); });
    await waitFor(() => expect(within(palettePanel()).queryByText(/No se pudo actualizar/)).toBeNull());
    expect(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' })).toBeEnabled();
  });

  it('2b. con la confirmación de «Habilitar» abierta, una relectura fallida no deja enviar', async () => {
    const variants = await mount();
    fireEvent.click(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' }));
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    expect(within(dialog).getByRole('button', { name: 'Habilitar para la venta' })).toBeEnabled();
    paletteQueue.push(() => reply({ detail: 'Error interno.' }, 500));
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' })); });
    await within(palettePanel()).findByText(/No se pudo actualizar/);
    expect(within(dialog).getByRole('button', { name: 'Habilitar para la venta' })).toBeDisabled();
  });

  it('3. tras crear, «Crear variante» no reaparece mientras la relectura no confirma la combinación', async () => {
    await mount();
    let release: (r: Response) => void = () => {};
    paletteQueue.push(() => new Promise((resolve) => { release = resolve; }));
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' })); });
    // The re-read is on its way: the combination is occupied meanwhile.
    expect(within(palettePanel()).queryByRole('button', { name: 'Crear variante 128 GB · Negro' })).toBeNull();
    // It lands WITHOUT the new product (an answer older than the creation): still occupied, never offered twice.
    // A NEW answer object (as every real read is), still without product 60.
    await act(async () => { release(reply({ ...PALETTE, combinations: [...PALETTE.combinations], variants: [...PALETTE.variants] })); });
    await waitFor(() => expect(paletteReads()).toBeGreaterThan(1));
    expect(within(palettePanel()).getByRole('table', { name: 'Configuraciones de la paleta' })).toBeInTheDocument();
    expect(within(palettePanel()).queryByRole('button', { name: 'Crear variante 128 GB · Negro' })).toBeNull();
  });

  it('3b. una lectura confirma la creación cuando lista el producto, la combinación ya no está libre o desapareció', () => {
    const pending = new Map<string, number | null>([['11-22', 60]]);
    // Not confirmed: free, and the read does not know product 60.
    expect([...pendingCreations(pending, PALETTE).keys()]).toEqual(['11-22']);
    // Confirmed: the combination has its variant.
    const taken = { ...PALETTE, combinations: PALETTE.combinations.map((c) => (c.key === '11-22' ? { ...c, status: 'variante_existente' } : c)) } as AdminModelPalette;
    expect(pendingCreations(pending, taken).size).toBe(0);
    // Confirmed: the read lists the product (whatever the combination says).
    const known = { ...PALETTE, variants: [...PALETTE.variants, { ...PALETTE.variants[0], product_id: 60 }] } as AdminModelPalette;
    expect(pendingCreations(pending, known).size).toBe(0);
    // Gone from the palette.
    const gone = { ...PALETTE, combinations: PALETTE.combinations.filter((c) => c.key !== '11-22') } as AdminModelPalette;
    expect(pendingCreations(pending, gone).size).toBe(0);
    // Without a product id, only the combination's state confirms it.
    expect(pendingCreations(new Map([['11-22', null]]), PALETTE).size).toBe(1);
  });

  const describedText = (el: HTMLElement) => (el.getAttribute('aria-describedby') ?? '').split(' ').filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? '').join(' ');

  async function goStale() {
    paletteQueue.push(() => reply({ detail: 'Error interno.' }, 500));
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' }));
    const form = within(palettePanel()).getByRole('form', { name: 'Crear variante 128 GB · Negro' });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Crear variante' })); });
    await within(palettePanel()).findByText(/No se pudo actualizar/);
  }

  it('L7. si la PRIMERA lectura de la paleta falla, también se ofrece «Volver a leer», ocupado mientras lee', async () => {
    paletteQueue.push(() => reply({ detail: 'Error interno.' }, 500));
    let release: (r: Response) => void = () => {};
    paletteQueue.push(() => new Promise((resolve) => { release = resolve; }));
    render(<CatalogModelEditor modelId={5} canManage companyId={null} />);
    const retry = await within(await screen.findByRole('region', { name: 'Opciones de este modelo' }))
      .findByRole('button', { name: 'Volver a leer' });
    await act(async () => { fireEvent.click(retry); });
    expect(within(palettePanel()).getByRole('button', { name: /Leyendo|Volver a leer/ })).toBeDisabled();
    await act(async () => { release(reply(PALETTE)); });
    expect(await within(palettePanel()).findByRole('table', { name: 'Configuraciones de la paleta' })).toBeInTheDocument();
    expect(within(palettePanel()).queryByRole('button', { name: 'Volver a leer' })).toBeNull();
  });

  it('L7. con la paleta vieja, «Volver a leer» se ocupa mientras lee y «Guardar paleta» se bloquea diciendo por qué', async () => {
    await mount();
    await goStale();
    fireEvent.click(within(palettePanel()).getByRole('checkbox', { name: 'Rojo' }));
    const save = within(palettePanel()).getByRole('button', { name: 'Guardar paleta' });
    expect(save).toBeDisabled();
    expect(describedText(save)).toMatch(/No se pudo actualizar/);
    let release: (r: Response) => void = () => {};
    paletteQueue.push(() => new Promise((resolve) => { release = resolve; }));
    const retry = within(palettePanel()).getByRole('button', { name: 'Volver a leer' });
    await act(async () => { fireEvent.click(retry); });
    expect(within(palettePanel()).getByRole('button', { name: /Leyendo/ })).toBeDisabled();
    await act(async () => { release(reply({ detail: 'Error interno.' }, 500)); });
    expect(within(palettePanel()).getByRole('button', { name: 'Volver a leer' })).toBeEnabled();
    expect(writes.filter((w) => w.method === 'PUT')).toEqual([]);
  });

  it('L6. «Crear variante» y «Habilitar» dicen por qué están bloqueados (texto visible, no sólo `title`)', async () => {
    const variants = await mount();
    fireEvent.click(within(palettePanel()).getByRole('checkbox', { name: 'Rojo' }));
    const create = within(palettePanel()).getByRole('button', { name: 'Crear variante 128 GB · Negro' });
    expect(describedText(create)).toMatch(/Guarda o cancela los cambios/);
    fireEvent.click(within(palettePanel()).getByRole('button', { name: 'Cancelar' }));
    fireEvent.click(within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' }));
    const dialog = within(variants).getByRole('group', { name: 'Habilitar Equipo Z 256 GB Azul' });
    await goStale();
    const enable = within(variants).getByRole('button', { name: 'Habilitar Equipo Z 256 GB Azul para la venta' });
    expect(enable).toBeDisabled();
    expect(describedText(enable)).toMatch(/paleta/i);
    const submit = within(dialog).getByRole('button', { name: 'Habilitar para la venta' });
    expect(submit).toBeDisabled();
    expect(dialog).toHaveTextContent(/La paleta no se pudo actualizar/);
    expect(describedText(submit)).toMatch(/La paleta no se pudo actualizar/);
    for (const button of within(palettePanel()).queryAllByRole('button', { name: /^Crear variante / })) {
      expect(describedText(button)).toMatch(/No se pudo actualizar/);
    }
  });

  it('L6. el botón de alias de una opción se llama como lo que muestra («N alias de …»)', async () => {
    send.mockImplementation(((base) => async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/admin/catalog/vocabulary/')) {
        return reply({ model_words: [], models: [], separator_words: [], options: {
          capacity: [], sim: [], region: [], other: [],
          color: [{ id: 22, label: 'Negro', key: 'negro', attribute_id: 4, swatch_hex: '', aliases: [{ id: 1, alias: 'Black' }], model_ids: [5] }],
        } });
      }
      return base(input as never, init);
    })(send.getMockImplementation()!));
    await mount();
    const toggle = await within(palettePanel()).findByRole('button', { name: '1 alias de Negro' });
    expect(toggle).toHaveTextContent('1 alias');
  });
});
