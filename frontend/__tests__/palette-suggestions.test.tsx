import { readFileSync } from 'fs';
import path from 'path';

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CatalogModelEditor } from '@/app/admin/components/CatalogModelEditor';
import { CatalogReadinessPanel } from '@/app/admin/components/CatalogReadinessPanel';
import { PalettesToBuildPanel } from '@/app/admin/components/PalettesToBuildPanel';
import type { AdminModelPalette, PaletteSuggestions } from '@/app/admin/lib/internal-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * PALETTE-SUGGEST-01 — proponer la paleta de un modelo desde las variantes que ya tiene.
 *
 * Con la API simulada (la propuesta la calcula y la prueba Django):
 *   - en «Opciones de este modelo», «Proponer desde las variantes (N opciones)» MARCA las opciones sugeridas en el
 *     editor de siempre y no guarda nada; lo que se guarda es exactamente lo revisado, con «Guardar paleta»;
 *   - «Paletas por armar» lista los modelos sin paleta con lo que proponen sus variantes, agrupado por atributo;
 *     cada modelo se confirma por separado (no hay «guardar todo»), y un modelo sin variantes se arma a mano;
 *   - los errores (al leer, al guardar, una paleta que otra persona guardó entretanto) se dicen en español;
 *   - sin `products.manage` sólo se lee.
 *
 * Nombres y valores inventados: nada aquí es de Apple ni de la tienda piloto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: Record<string, unknown> };
let writes: Call[];
let reads: string[];

const option = (id: number, label: string, in_palette: boolean, swatch_hex = '') => ({ id, label, swatch_hex, sort_order: 0, in_palette });

/** A model WITHOUT a palette whose variants use 128 GB, 256 GB, Azul and Negro (and no SIM). */
const NO_PALETTE: AdminModelPalette = {
  model_id: 5, model_name: 'Equipo Z', has_palette: false,
  attributes: [
    { id: 3, name: 'Capacidad', kind: 'text', role: 'other', sort_order: 0,
      options: [option(11, '128 GB', false), option(12, '256 GB', false), option(13, '1 TB', false)] },
    { id: 4, name: 'Color', kind: 'color', role: 'other', sort_order: 1,
      options: [option(21, 'Azul', false, '#1F3A93'), option(22, 'Negro', false, '#111111'), option(23, 'Rojo', false, '#B91C1C')] },
  ],
  combinations: [],
  variants: [
    { product_id: 51, name: 'Equipo Z 128 Azul', condition_line: 'new', option_ids: [11, 21], status: null },
    { product_id: 52, name: 'Equipo Z 256 Negro', condition_line: 'used', option_ids: [12, 22], status: null },
  ],
  suggested_option_ids: [11, 12, 21, 22],
  summary: { combinations: 0, existing: 0, valid_without_variant: 0, outside: 0, incomplete: 0 },
  truncated: false,
};

const opt = (attribute_id: number, attribute_name: string, option_id: number, label: string) => ({
  attribute_id, attribute_name, option_id, label, swatch_hex: '',
});

const MODEL = {
  id: 5, name: 'Equipo Z', slug: 'equipo-z', category_id: null, category_name: null, image_url: '',
  description: '', is_published_online: true, sort_order: 0, variant_count: 2, used_unit_count: 0, has_palette: false,
  variants: [
    { product_id: 51, name: 'Equipo Z 128 Azul', condition_line: 'new', price: '100.00', stock: 0, is_published_online: true,
      options: [opt(3, 'Capacidad', 11, '128 GB'), opt(4, 'Color', 21, 'Azul')], history: { sold: 0, units: 0, reserved: 0 },
      identity_change: 'free', line_change: 'free', removal: 'free', palette_status: null },
    { product_id: 52, name: 'Equipo Z 256 Negro', condition_line: 'used', price: '100.00', stock: 0, is_published_online: true,
      options: [opt(3, 'Capacidad', 12, '256 GB'), opt(4, 'Color', 22, 'Negro')], history: { sold: 0, units: 0, reserved: 0 },
      identity_change: 'free', line_change: 'free', removal: 'free', palette_status: null },
  ],
};

function withPalette(palette: AdminModelPalette, chosen: Set<number>): AdminModelPalette {
  return {
    ...palette, has_palette: chosen.size > 0,
    attributes: palette.attributes.map((a) => ({ ...a, options: a.options.map((o) => ({ ...o, in_palette: chosen.has(o.id) })) })),
  };
}

function mockEditorApi(palette: AdminModelPalette = NO_PALETTE) {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.replace(/^.*\/api/, '').replace(/\?.*$/, '');
    if (method !== 'GET') {
      const body = JSON.parse(String(init.body ?? '{}'));
      writes.push({ method, path, body });
      if (path === '/admin/catalog/models/5/palette/') return reply(withPalette(palette, new Set(body.option_ids as number[])));
      return reply({}, 404);
    }
    reads.push(path);
    if (path === '/admin/catalog/models/5/palette/') return reply(palette);
    if (path === '/admin/catalog/models/5/accessories/') return reply({ model_id: 5, accessories: [] });
    if (path === '/admin/catalog/models/5/') return reply(MODEL);
    if (path === '/admin/catalog/attributes/') return reply([]);
    if (path === '/admin/catalog/settings/') return reply({ new_line_label: '', used_line_label: '' });
    if (path === '/admin/catalog/grades/') return reply([]);
    if (path === '/admin/categories/') return reply([]);
    if (path === '/admin/products/') return reply({ results: [], count: 0 });
    return reply({}, 404);
  });
}

beforeEach(() => {
  writes = [];
  reads = [];
  window.history.replaceState(null, '', '/admin/products/models/5');
});

async function mountEditor(canManage = true) {
  render(<CatalogModelEditor modelId={5} canManage={canManage} companyId={null} />);
  const variants = await screen.findByRole('region', { name: 'Variantes' });
  await within(variants).findByText('Equipo Z 128 Azul', { selector: 'a' });
  return screen.findByRole('region', { name: 'Opciones de este modelo' });
}

describe('«Proponer desde las variantes» en la paleta de un modelo', () => {
  it('marca las opciones que usan sus variantes y no guarda nada', async () => {
    mockEditorApi();
    const panel = await mountEditor();
    expect(within(panel).getByRole('checkbox', { name: '128 GB' })).not.toBeChecked();
    const propose = within(panel).getByRole('button', { name: 'Proponer desde las variantes (4 opciones)' });
    fireEvent.click(propose);
    for (const name of ['128 GB', '256 GB', 'Azul', 'Negro']) {
      expect(within(panel).getByRole('checkbox', { name })).toBeChecked();
    }
    for (const name of ['1 TB', 'Rojo']) expect(within(panel).getByRole('checkbox', { name })).not.toBeChecked();
    expect(within(panel).getByText(/Revisa, desmarca lo que no corresponda y pulsa «Guardar paleta»/)).toBeInTheDocument();
    expect(within(panel).getByText('Cambios sin guardar.')).toBeInTheDocument();
    expect(writes).toEqual([]);
  });

  it('«Guardar paleta» envía exactamente lo revisado', async () => {
    mockEditorApi();
    const panel = await mountEditor();
    fireEvent.click(within(panel).getByRole('button', { name: /Proponer desde las variantes/ }));
    fireEvent.click(within(panel).getByRole('checkbox', { name: '256 GB' }));
    fireEvent.click(within(panel).getByRole('checkbox', { name: 'Rojo' }));
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Guardar paleta' })); });
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ method: 'PUT', path: '/admin/catalog/models/5/palette/' });
    // `expected_option_ids`: la paleta que leyó la página (vacía aquí); el servidor rechaza (409) si cambió.
    expect(writes[0].body).toEqual({ option_ids: [11, 21, 22, 23], expected_option_ids: [] });
    expect(await within(panel).findByRole('status')).toHaveTextContent('Paleta guardada.');
  });

  it('con paleta, sólo ofrece lo que le falta y lo suma a lo ya marcado', async () => {
    mockEditorApi(withPalette(NO_PALETTE, new Set([11, 21, 23])));
    const panel = await mountEditor();
    fireEvent.click(within(panel).getByRole('button', { name: 'Proponer desde las variantes (2 opciones)' }));
    for (const name of ['128 GB', '256 GB', 'Azul', 'Negro', 'Rojo']) {
      expect(within(panel).getByRole('checkbox', { name })).toBeChecked();
    }
    expect(writes).toEqual([]);
  });

  it('no aparece cuando la paleta ya tiene todo lo sugerido, ni sin `products.manage`', async () => {
    mockEditorApi(withPalette(NO_PALETTE, new Set([11, 12, 21, 22])));
    const panel = await mountEditor();
    expect(within(panel).queryByRole('button', { name: /Proponer desde las variantes/ })).toBeNull();
  });

  it('sin `products.manage` no se ofrece', async () => {
    mockEditorApi();
    const panel = await mountEditor(false);
    expect(within(panel).queryByRole('button', { name: /Proponer desde las variantes/ })).toBeNull();
  });

  it('un servidor anterior (sin `suggested_option_ids`) no rompe la pantalla', async () => {
    const older: AdminModelPalette = { ...NO_PALETTE };
    delete older.suggested_option_ids;
    mockEditorApi(older);
    const panel = await mountEditor();
    expect(within(panel).queryByRole('button', { name: /Proponer desde las variantes/ })).toBeNull();
  });

  it('un error al guardar se muestra y lo propuesto sigue marcado', async () => {
    mockEditorApi();
    const panel = await mountEditor();
    fireEvent.click(within(panel).getByRole('button', { name: /Proponer desde las variantes/ }));
    send.mockImplementationOnce(async () => reply({ detail: 'Alguna opción no existe.', code: 'not_found' }, 404));
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Guardar paleta' })); });
    expect(await within(panel).findByRole('alert')).toHaveTextContent('Alguna opción no existe.');
    expect(within(panel).getByRole('checkbox', { name: '128 GB' })).toBeChecked();
  });
});

// ---------------------------------------------------------------------------
// «Paletas por armar»
// ---------------------------------------------------------------------------

const SUGGESTIONS: PaletteSuggestions = {
  models: [
    { model_id: 5, name: 'Equipo Z', variant_count: 2, suggested_option_ids: [11, 12, 21, 22], incomplete_variants: 0,
      groups: [
        { attribute_id: 3, name: 'Capacidad', role: 'capacity', kind: 'text',
          options: [{ id: 11, label: '128 GB', swatch_hex: '' }, { id: 12, label: '256 GB', swatch_hex: '' }] },
        { attribute_id: 4, name: 'Tono', role: 'color', kind: 'color',
          options: [{ id: 21, label: 'Azul', swatch_hex: '#1F3A93' }, { id: 22, label: 'Negro', swatch_hex: '#111111' }] },
      ] },
    { model_id: 6, name: 'Equipo Y', variant_count: 3, suggested_option_ids: [11, 31], incomplete_variants: 2,
      groups: [
        { attribute_id: 3, name: 'Capacidad', role: 'capacity', kind: 'text', options: [{ id: 11, label: '128 GB', swatch_hex: '' }] },
        { attribute_id: 6, name: 'Tipo de SIM', role: 'sim', kind: 'text', options: [{ id: 31, label: 'eSIM', swatch_hex: '' }] },
      ] },
    { model_id: 7, name: 'Equipo Vacío', variant_count: 0, suggested_option_ids: [], incomplete_variants: 0, groups: [] },
  ],
  summary: { without_palette: 3, with_suggestion: 2 },
};

type BulkOptions = {
  suggestions?: PaletteSuggestions;
  /** What later reads of the list answer (a reload after a refusal); the first read answers `suggestions`. */
  next?: PaletteSuggestions;
  status?: number;
  current?: Record<number, boolean>;
  /** What the re-read of a model's palette suggests (default: the model's row as listed for Equipo Z). */
  rereadSuggested?: Record<number, number[]>;
  putStatus?: number;
  putBody?: unknown;
};

function mockBulkApi({
  suggestions = SUGGESTIONS, next, status = 200, current = {}, rereadSuggested = {}, putStatus = 200, putBody,
}: BulkOptions = {}) {
  send.mockReset();
  let listed = 0;
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.replace(/^.*\/api/, '').replace(/\?.*$/, '');
    const palettePath = path.match(/^\/admin\/catalog\/models\/(\d+)\/palette\/$/);
    if (method !== 'GET') {
      const body = JSON.parse(String(init.body ?? '{}'));
      writes.push({ method, path, body });
      if (putStatus !== 200) return reply(putBody ?? {}, putStatus);
      return reply(withPalette({ ...NO_PALETTE, model_id: Number(palettePath?.[1]) }, new Set(body.option_ids as number[])));
    }
    reads.push(url.replace(/^.*\/api/, ''));
    if (path === '/admin/catalog/palette-suggestions/') {
      listed += 1;
      if (status !== 200) return reply({ detail: 'No tienes permisos para esta operación.' }, status);
      return reply(listed > 1 && next ? next : suggestions);
    }
    if (palettePath) {
      const id = Number(palettePath[1]);
      return reply({
        ...NO_PALETTE, model_id: id, has_palette: Boolean(current[id]),
        suggested_option_ids: rereadSuggested[id] ?? NO_PALETTE.suggested_option_ids,
      });
    }
    return reply({}, 404);
  });
}

async function mountBulk(canManage = true, companyId: number | null = 4) {
  render(<PalettesToBuildPanel companyId={companyId} canManage={canManage} />);
  const panel = await screen.findByRole('region', { name: 'Paletas por armar' });
  await within(panel).findByRole('listitem', { name: 'Equipo Z' });
  return panel;
}

describe('«Paletas por armar»', () => {
  it('lista cada modelo sin paleta con lo que proponen sus variantes, agrupado por atributo', async () => {
    mockBulkApi();
    const panel = await mountBulk();
    expect(reads).toEqual(['/admin/catalog/palette-suggestions/?company=4']);
    const z = within(panel).getByRole('listitem', { name: 'Equipo Z' });
    expect(within(z).getByText('2 variantes')).toBeInTheDocument();
    const capacity = within(z).getByRole('group', { name: 'Capacidad' });
    expect(within(capacity).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['128 GB', '256 GB']);
    const colour = within(z).getByRole('group', { name: 'Tono' });
    expect(within(colour).getByText('rol: Color')).toBeInTheDocument();
    expect(within(colour).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Azul', 'Negro']);
    expect(within(z).getByRole('link', { name: 'Revisar y guardar' })).toHaveAttribute('href', '/admin/products/models/5#paleta');
    // Nunca un «guardar todo».
    expect(within(panel).queryByRole('button', { name: /todo|todas/i })).toBeNull();
    expect(within(panel).getByText(/3 modelos sin paleta/)).toBeInTheDocument();
  });

  it('un modelo sin variantes se arma a mano y no ofrece guardar', async () => {
    mockBulkApi();
    const panel = await mountBulk();
    const empty = within(panel).getByRole('listitem', { name: 'Equipo Vacío' });
    expect(within(empty).getByText('Sin variantes: arma la paleta a mano.')).toBeInTheDocument();
    expect(within(empty).queryByRole('button')).toBeNull();
    expect(within(empty).getByRole('link', { name: 'Armar a mano' })).toHaveAttribute('href', '/admin/products/models/7#paleta');
  });

  it('avisa las variantes que quedarían incompletas', async () => {
    mockBulkApi();
    const panel = await mountBulk();
    const y = within(panel).getByRole('listitem', { name: 'Equipo Y' });
    expect(within(y).getByText(/2 variantes no llevan todas estas opciones/)).toBeInTheDocument();
  });

  it('se confirma modelo por modelo y se envían exactamente los ids mostrados', async () => {
    mockBulkApi();
    const panel = await mountBulk();
    const z = within(panel).getByRole('listitem', { name: 'Equipo Z' });
    fireEvent.click(within(z).getByRole('button', { name: 'Guardar esta paleta' }));
    // Pedir confirmar no escribe nada.
    expect(writes).toEqual([]);
    const confirm = within(z).getByRole('group', { name: 'Confirmar la paleta de Equipo Z' });
    expect(confirm).toHaveTextContent('¿Guardar la paleta de «Equipo Z» con estas 4 opciones?');
    // Los otros modelos no se tocan.
    expect(within(within(panel).getByRole('listitem', { name: 'Equipo Y' })).queryByRole('group', { name: /Confirmar/ })).toBeNull();
    await act(async () => { fireEvent.click(within(confirm).getByRole('button', { name: 'Confirmar y guardar' })); });
    // Exactly the ids shown, and the palette it read (none): a palette saved meanwhile makes the server answer 409.
    expect(writes).toEqual([{ method: 'PUT', path: '/admin/catalog/models/5/palette/',
      body: { option_ids: [11, 12, 21, 22], expected_option_ids: [] } }]);
    // Antes de guardar, relee la paleta del modelo (nunca reemplaza una que otra persona guardó).
    expect(reads).toContain('/admin/catalog/models/5/palette/?company=4');
    expect(await within(z).findByRole('status')).toHaveTextContent('Paleta guardada con 4 opciones.');
    expect(within(z).queryByRole('button', { name: 'Guardar esta paleta' })).toBeNull();
  });

  it('cancelar no envía nada', async () => {
    mockBulkApi();
    const panel = await mountBulk();
    const z = within(panel).getByRole('listitem', { name: 'Equipo Z' });
    fireEvent.click(within(z).getByRole('button', { name: 'Guardar esta paleta' }));
    fireEvent.click(within(z).getByRole('button', { name: 'Cancelar' }));
    expect(within(z).queryByRole('group', { name: /Confirmar/ })).toBeNull();
    expect(writes).toEqual([]);
  });

  it('si otra persona ya guardó una paleta, no la reemplaza', async () => {
    mockBulkApi({ current: { 5: true } });
    const panel = await mountBulk();
    const z = within(panel).getByRole('listitem', { name: 'Equipo Z' });
    fireEvent.click(within(z).getByRole('button', { name: 'Guardar esta paleta' }));
    await act(async () => { fireEvent.click(within(z).getByRole('button', { name: 'Confirmar y guardar' })); });
    expect(writes).toEqual([]);
    expect(await within(z).findByRole('alert')).toHaveTextContent(/ya tiene paleta/);
  });

  it('un error al guardar se dice en el modelo, toma el foco y se puede volver a intentar', async () => {
    mockBulkApi({ putStatus: 400, putBody: { detail: 'Alguna opción no existe.', code: 'not_found' } });
    const panel = await mountBulk();
    const z = within(panel).getByRole('listitem', { name: 'Equipo Z' });
    fireEvent.click(within(z).getByRole('button', { name: 'Guardar esta paleta' }));
    await act(async () => { fireEvent.click(within(z).getByRole('button', { name: 'Confirmar y guardar' })); });
    const alert = await within(z).findByRole('alert');
    expect(alert).toHaveTextContent('Alguna opción no existe.');
    await waitFor(() => expect(alert).toHaveFocus());
    expect(within(z).getByRole('button', { name: 'Guardar esta paleta' })).toBeEnabled();
  });

  it('un 409 (otra persona guardó entretanto) lo dice claro y vuelve a leer ese modelo', async () => {
    const next: PaletteSuggestions = { ...SUGGESTIONS, models: SUGGESTIONS.models.filter((row) => row.model_id !== 5) };
    mockBulkApi({ next, putStatus: 409, putBody: { detail: 'Otra persona acaba de cambiar la paleta de este modelo. Vuelve a cargarla.', code: 'stale' } });
    const panel = await mountBulk();
    const z = within(panel).getByRole('listitem', { name: 'Equipo Z' });
    fireEvent.click(within(z).getByRole('button', { name: 'Guardar esta paleta' }));
    await act(async () => { fireEvent.click(within(z).getByRole('button', { name: 'Confirmar y guardar' })); });
    expect(await within(z).findByRole('alert')).toHaveTextContent(/Otra persona guardó la paleta de este modelo mientras tanto: no se reemplazó/);
    // The list was read again; the model now has a palette, so nothing more is offered for it.
    await waitFor(() => expect(reads.filter((r) => r.startsWith('/admin/catalog/palette-suggestions/'))).toHaveLength(2));
    await waitFor(() => expect(within(z).queryByRole('button', { name: 'Guardar esta paleta' })).toBeNull());
    expect(writes).toHaveLength(1);
  });

  it('si las variantes cambiaron desde la lista, no guarda y pide revisar la propuesta nueva', async () => {
    const changed: PaletteSuggestions = {
      ...SUGGESTIONS,
      models: SUGGESTIONS.models.map((row) => (row.model_id === 5
        ? { ...row, suggested_option_ids: [11, 12, 13, 21, 22],
          groups: [{ ...row.groups[0], options: [...row.groups[0].options, { id: 13, label: '1 TB', swatch_hex: '' }] }, row.groups[1]] }
        : row)),
    };
    mockBulkApi({ next: changed, rereadSuggested: { 5: [11, 12, 13, 21, 22] } });
    const panel = await mountBulk();
    const z = within(panel).getByRole('listitem', { name: 'Equipo Z' });
    fireEvent.click(within(z).getByRole('button', { name: 'Guardar esta paleta' }));
    await act(async () => { fireEvent.click(within(z).getByRole('button', { name: 'Confirmar y guardar' })); });
    expect(writes).toEqual([]);
    expect(await within(z).findByRole('alert')).toHaveTextContent(/Las variantes de este modelo cambiaron/);
    // The row now shows the new suggestion, to be confirmed again.
    expect(await within(within(z).getByRole('group', { name: 'Capacidad' })).findByText('1 TB')).toBeInTheDocument();
    expect(within(z).getByRole('button', { name: 'Guardar esta paleta' })).toBeEnabled();
  });

  it('«Cancelar» devuelve el foco a «Guardar esta paleta»', async () => {
    mockBulkApi();
    const panel = await mountBulk();
    const z = within(panel).getByRole('listitem', { name: 'Equipo Z' });
    fireEvent.click(within(z).getByRole('button', { name: 'Guardar esta paleta' }));
    await waitFor(() => expect(within(z).getByRole('group', { name: 'Confirmar la paleta de Equipo Z' })).toHaveFocus());
    fireEvent.click(within(z).getByRole('button', { name: 'Cancelar' }));
    await waitFor(() => expect(within(z).getByRole('button', { name: 'Guardar esta paleta' })).toHaveFocus());
  });

  it('después de guardar, el aviso toma el foco', async () => {
    mockBulkApi();
    const panel = await mountBulk();
    const z = within(panel).getByRole('listitem', { name: 'Equipo Z' });
    fireEvent.click(within(z).getByRole('button', { name: 'Guardar esta paleta' }));
    await act(async () => { fireEvent.click(within(z).getByRole('button', { name: 'Confirmar y guardar' })); });
    const status = await within(z).findByRole('status');
    await waitFor(() => expect(status).toHaveFocus());
  });

  it('el aviso de variantes incompletas dice que la recepción las señala', async () => {
    mockBulkApi();
    const panel = await mountBulk();
    const y = within(panel).getByRole('listitem', { name: 'Equipo Y' });
    expect(within(y).getByText(/en la vista previa de «Recibir mercadería» aparecen como «incompleta»/)).toBeInTheDocument();
  });

  it('sin `products.manage` sólo se lee', async () => {
    mockBulkApi();
    const panel = await mountBulk(false);
    expect(within(panel).queryByRole('button', { name: 'Guardar esta paleta' })).toBeNull();
    expect(within(within(panel).getByRole('listitem', { name: 'Equipo Z' })).getByRole('link', { name: 'Revisar' }))
      .toHaveAttribute('href', '/admin/products/models/5#paleta');
  });

  it('un error al leer se dice y se reintenta', async () => {
    mockBulkApi({ status: 403 });
    render(<PalettesToBuildPanel companyId={4} canManage />);
    const panel = await screen.findByRole('region', { name: 'Paletas por armar' });
    expect(await within(panel).findByRole('alert')).toHaveTextContent('No tienes permisos para esta operación.');
    mockBulkApi();
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Reintentar' })); });
    expect(await within(panel).findByRole('listitem', { name: 'Equipo Z' })).toBeInTheDocument();
  });

  it('sin modelos por armar no muestra nada', async () => {
    mockBulkApi({ suggestions: { models: [], summary: { without_palette: 0, with_suggestion: 0 } } });
    const { container } = render(<PalettesToBuildPanel companyId={4} canManage />);
    await waitFor(() => expect(reads).toHaveLength(1));
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});

describe('«Preparación del catálogo» lleva a «Paletas por armar»', () => {
  it('la comprobación de paletas pendiente enlaza a la lista', async () => {
    send.mockReset();
    send.mockImplementation(async () => reply({
      checks: [{ key: 'palette', status: 'missing', kind: 'required', title: 'Paleta de cada modelo',
        detail: '2 de 2 modelos no tienen paleta.', action: '/admin/products/models/7#paleta', action_label: 'Elegir paleta',
        counts: { without_palette: 2, models: 2 }, samples: [] }],
      summary: { ok: 0, warning: 0, missing: 1 }, ready: false,
    }));
    render(<CatalogReadinessPanel companyId={4} />);
    const item = await screen.findByRole('listitem', { name: 'Paleta de cada modelo' });
    expect(within(item).getByRole('link', { name: 'Ver paletas por armar' })).toHaveAttribute('href', '#paletas-por-armar');
  });

  it('la página de modelos muestra «Paletas por armar» después de la preparación', () => {
    const page = readFileSync(path.join(__dirname, '..', 'app/admin/products/models/page.tsx'), 'utf8');
    expect(page.indexOf('<PalettesToBuildPanel')).toBeGreaterThan(page.indexOf('<CatalogReadinessPanel'));
    expect(page.indexOf('<PalettesToBuildPanel')).toBeLessThan(page.indexOf('<CatalogModelsManager'));
    expect(readFileSync(path.join(__dirname, '..', 'app/admin/components/PalettesToBuildPanel.tsx'), 'utf8'))
      .toContain('id="paletas-por-armar"');
  });
});
