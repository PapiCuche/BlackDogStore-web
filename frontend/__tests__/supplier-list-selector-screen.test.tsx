/**
 * SELECTOR-UX-01 — el selector visual dentro de «Actualizar lista de proveedor» (modo múltiple).
 *
 * Pegar → «Leer la lista» → cada línea ofrece «Elegir en el selector visual» sobre la paleta REAL del modelo: lo leído
 * viene marcado, la capacidad que el texto dice queda fija, los colores se marcan y desmarcan, y cada color es una
 * tarjeta con el estado que dice el servidor (variante existente por su id real, válida sin variante, no existe en
 * este modelo…). Nada se guarda, se crea ni se publica desde el selector; dos ofertas en una línea se separan sólo si
 * una persona lo pide.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { SupplierListAssistant } from '@/app/admin/components/SupplierListAssistant';
import { fetchWithAuth } from '@/app/lib/auth';
import { phraseKey } from '@/app/admin/lib/supplier-list-grammar';
import {
  BLACK, BLUE, CHIP, ESIM, G128, G256, GREEN, JA, MODEL, selectorPalette, selectorVocabulary,
} from './support/selector-catalog';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response;

const SUPPLIERS = { count: 1, page: 1, page_size: 25, results: [{ id: 3, name: 'Mayorista', is_active: true }] };
const COMPANY = { company: { id: 5, name: 'Empresa', legal_name: '', tax_id: '', slug: 'e', is_active: true }, settings: { currency: 'PEN' } };

let resolved: { key: string; colour_text?: string; capacity_text?: string; sim_text?: string; region_text?: string; model_text: string }[][];
let paletteUrls: string[];
let variantPosts: { url: string; body: { option_ids: number[] } }[];
let created: Set<string>;
/** Holds the next /resolve answer until released (review a: nothing typed is cleared before the answer). */
let resolveGate: Promise<void> | null;
let resolveFails: () => boolean;
/** The server reads a selector row as ANOTHER model (a name or alias collision, review c). */
let otherModelForSelector: boolean;

const vocabulary = selectorVocabulary();
const compact = (text: string) => phraseKey(text).replace(/ /g, '');

/** A small stand-in for the server's resolver over «Equipo Z»: labels, the palette, the excluded pair, the variants. */
function resolveRow(row: Record<string, string>) {
  const known = phraseKey(row.model_text) === 'equipo z';
  const swapped = known && otherModelForSelector && row.key.includes('-S');
  const model = { text: row.model_text, status: known ? 'exact' : 'unknown', via: known ? 'exact' : null,
    model_id: known ? (swapped ? 777 : MODEL) : null, model_name: known ? (swapped ? 'Equipo Z Pro' : 'Equipo Z') : null,
    candidates: [], extracted: [] };
  if (!known) return { key: row.key, model, line: 'new', readings: [], error: '', configurations: [] };
  const find = (role: 'capacity' | 'color' | 'sim' | 'region', text: string) => vocabulary.options[role]
    .find((o) => compact(o.label) === compact(text));
  const singles = (['capacity', 'sim', 'region'] as const).map((role) => {
    const text = row[`${role}_text`];
    return text ? { role, text, option: find(role, text) } : null;
  }).filter(Boolean) as { role: string; text: string; option: ReturnType<typeof find> }[];
  const colours = row.colour_text ? row.colour_text.split(/\s*(?:,|\by\b)\s*/).filter(Boolean) : [];
  const reading = (role: string, text: string, id: number, label: string) => ({ role, text, option_id: id, label, via: 'label', alias_id: null });
  const readings = singles.filter((s) => s.option).map((s) => reading(s.role, s.text, s.option!.id, s.option!.label));
  const configurations = (colours.length ? colours : [null]).map((colourText) => {
    const colour = colourText ? find('color', colourText) : undefined;
    const options = [...readings, ...(colour ? [reading('color', colourText!, colour.id, colour.label)] : [])];
    const ids = options.map((o) => o.option_id).sort((a, b) => a - b);
    const said = new Set(options.map((o) => o.role));
    const missing = ['capacity', 'color', 'sim', 'region'].filter((role) => !said.has(role) && !(role === 'color' && colourText));
    const outside = colour && !colour.model_ids.includes(MODEL);
    let status = 'combinacion_valida_sin_variante';
    let product: number | null = null;
    if (colourText && !colour) status = 'opcion_desconocida';
    else if (outside) status = 'fuera_de_paleta';
    else if (missing.length) status = 'incompleta';
    else if (ids.includes(GREEN) && ids.includes(CHIP)) status = 'combinacion_excluida';
    else if (ids.join() === [G128, BLACK, ESIM, JA].sort((a, b) => a - b).join()) { status = 'variante_existente'; product = 900; }
    else if (created.has(ids.join())) { status = 'variante_existente'; product = 950; }
    return {
      status, complete: ['variante_existente', 'combinacion_valida_sin_variante'].includes(status), colour_text: colourText ?? '',
      option_ids: ids, options, product_id: product, product_name: product ? `Equipo Z ${product}` : null,
      draft_pending: product === 950, missing, candidates: [],
      doubts: outside ? [{ role: 'color', text: colourText, reason: 'outside', readings: [], actions: ['add_to_palette', 'keep_pending'] }] : [],
    };
  });
  return { key: row.key, model, line: 'new', readings: [...readings, ...configurations.flatMap((c) => c.options.filter((o) => o.role === 'color'))], error: '', configurations };
}

beforeEach(() => {
  send.mockReset();
  resolved = [];
  paletteUrls = [];
  variantPosts = [];
  created = new Set();
  resolveGate = null;
  resolveFails = () => false;
  otherModelForSelector = false;
  send.mockImplementation(async (url, init) => {
    const path = String(url);
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (path.includes('/admin/catalog/vocabulary/')) return reply(200, vocabulary);
    if (path.includes('/admin/suppliers/')) return reply(200, SUPPLIERS);
    if (path.includes('/admin/company-settings/')) return reply(200, COMPANY);
    if (path.includes('/admin/catalog/attributes/')) return reply(200, []);
    if (path.includes(`/admin/catalog/models/${MODEL}/palette/`)) { paletteUrls.push(path); return reply(200, selectorPalette()); }
    if (path.includes(`/admin/catalog/models/${MODEL}/variants/`)) {
      variantPosts.push({ url: path, body });
      const ids = [...body.option_ids].sort((a: number, b: number) => a - b).join();
      // The server never creates a variant twice: `duplicate`, as `device_catalog.set_variant` answers.
      if (created.has(ids)) return reply(400, { detail: 'Otra persona acaba de crear esa misma variante.', code: 'duplicate', field: 'option_ids' });
      created.add(ids);
      return reply(201, { id: 950, name: 'Equipo Z borrador', draft_pending: true });
    }
    if (path.includes('/admin/catalog/resolve/')) {
      resolved.push(body.rows);
      if (resolveGate) { const gate = resolveGate; resolveGate = null; await gate; }
      if (resolveFails()) return reply(500, {});
      const rows = body.rows.map(resolveRow);
      return reply(200, { rows, summary: {} });
    }
    return reply(404, {});
  });
});

async function open({ manage = true, costs = true } = {}) {
  await act(async () => {
    render(<SupplierListAssistant canManage={manage} canSeeCosts={costs} canViewCompany companyId={5} />);
  });
}

async function readList(text: string) {
  fireEvent.change(screen.getByLabelText('Texto de la lista del proveedor'), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
}

const line = (n: number) => screen.getByRole('article', { name: `Línea ${n}` });
const selector = (n: number) => screen.getByRole('region', { name: `Selector visual · línea ${n}` });
async function openSelector(n: number) {
  await act(async () => { fireEvent.click(within(selector(n)).getByRole('button', { name: /Elegir en el selector visual/ })); });
}
const chip = (n: number, group: string, name: RegExp) => within(within(selector(n)).getByRole(group === 'Color' ? 'group' : 'radiogroup', { name: group }))
  .getByRole(group === 'Color' ? 'checkbox' : 'radio', { name });
async function useSelection(n: number) {
  await act(async () => { fireEvent.click(within(selector(n)).getByRole('button', { name: /Usar esta selección/ })); });
}

describe('el selector visual de una línea', () => {
  it('lo leído viene marcado; la capacidad que el texto dice queda fija; cerrado no toca nada', async () => {
    await open();
    await readList('equipo z 128gb esim j/a negro, azul 2500');
    // Closed by default: nothing of the selector is drawn, nothing is chosen, no palette is read.
    expect(within(selector(1)).getByRole('button', { name: /Elegir en el selector visual/ })).toHaveAttribute('aria-expanded', 'false');
    expect(paletteUrls).toEqual([]);
    await openSelector(1);
    expect(paletteUrls).toHaveLength(1);
    expect(paletteUrls[0]).toContain('company=5');
    expect(chip(1, 'Color', /Negro/)).toBeChecked();
    expect(chip(1, 'Color', /Azul/)).toBeChecked();
    expect(chip(1, 'Color', /Verde/)).not.toBeChecked();
    expect(within(selector(1)).getAllByText('leído').length).toBeGreaterThanOrEqual(3);
    expect(chip(1, 'Capacidad', /128 GB/)).toBeChecked();
    expect(chip(1, 'Capacidad', /256 GB/)).toBeDisabled();
    expect(selector(1)).toHaveTextContent('La lista dice «128gb»');
    // A colour outside the model's palette is never offered.
    expect(within(selector(1)).queryByRole('checkbox', { name: /Rosa/ })).toBeNull();
  });

  it('marcar y desmarcar colores: una tarjeta por color, con el estado que dice el servidor', async () => {
    await open();
    await readList('equipo z 128gb esim j/a negro, azul 2500');
    expect(within(line(1)).getAllByRole('article')).toHaveLength(2);
    await openSelector(1);
    fireEvent.click(chip(1, 'Color', /Azul/));
    fireEvent.click(chip(1, 'Color', /Verde/));
    expect(within(selector(1)).getByRole('group', { name: 'Resultado del selector · línea 1' })).toHaveTextContent(
      /Equipo Z · 128 GB · Negro · eSIM · J\/A.*Variante existente.*Equipo Z · 128 GB · Verde · eSIM · J\/A.*color a mano/);
    await useSelection(1);
    // One row per colour, the company's labels; no amount.
    expect(resolved.at(-1)).toEqual([
      { key: 'L1-S21', model_text: 'Equipo Z', capacity_text: '128 GB', colour_text: 'Negro', sim_text: 'eSIM', region_text: 'J/A' },
      { key: 'L1-S23', model_text: 'Equipo Z', capacity_text: '128 GB', colour_text: 'Verde', sim_text: 'eSIM', region_text: 'J/A' },
    ]);
    expect(JSON.stringify(resolved.at(-1))).not.toContain('2500');
    const cards = within(line(1)).getAllByRole('article');
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent('Con variante');
    expect(cards[0]).toHaveTextContent('Equipo Z 900');
    expect(cards[1]).toHaveTextContent('Sin variante');
    expect(cards[1]).toHaveTextContent('Elegido a mano (la lista no lo dice): Verde');
    // Nothing is chosen, priced or confirmed by itself.
    expect(screen.getByLabelText('Precio comercial · línea 1, configuración 1')).toHaveValue('');
    expect(screen.getByRole('checkbox', { name: 'Incluir en el guardado · línea 1, configuración 1' })).not.toBeChecked();
  });

  it('una combinación que el modelo excluye no se puede crear ni guardar', async () => {
    await open();
    await readList('equipo z 128gb chip j/a 2500');
    await openSelector(1);
    expect(chip(1, 'Tipo de SIM', /Chip/)).toBeChecked();
    fireEvent.click(chip(1, 'Color', /Verde/));
    expect(selector(1)).toHaveTextContent('No existe en este modelo');
    await useSelection(1);
    const card = within(line(1)).getByRole('article');
    expect(card).toHaveTextContent('No existe en este modelo');
    expect(within(card).queryByRole('button', { name: /Crear variante/ })).toBeNull();
    expect(within(card).getByRole('checkbox', { name: /Incluir en el guardado/ })).toBeDisabled();
  });

  it('un paso que el texto no dice se elige a mano; crear la variante que falta pide confirmación y se vuelve a leer', async () => {
    await open();
    await readList('equipo z 256gb j/a azul 2500');
    expect(within(line(1)).getByRole('article')).toHaveTextContent('Incompleta');
    await openSelector(1);
    fireEvent.click(chip(1, 'Tipo de SIM', /eSIM/));
    expect(within(selector(1)).getAllByText('a mano').length).toBeGreaterThanOrEqual(1);
    await useSelection(1);
    const card = within(line(1)).getByRole('article');
    expect(card).toHaveTextContent('Sin variante');
    expect(card).toHaveTextContent('Elegido a mano (la lista no lo dice): eSIM');
    fireEvent.click(within(card).getByRole('button', { name: /Crear variante/ }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirmar: crear borrador' })); });
    expect(variantPosts).toHaveLength(1);
    expect(variantPosts[0].url).toContain('company=5');
    expect(variantPosts[0].body).toEqual({ condition_line: 'new', option_ids: expect.arrayContaining([BLUE, ESIM, JA]) });
    // The selection is read again after the write: the card is the new draft, by its real id.
    expect(within(line(1)).getByRole('article')).toHaveTextContent('Equipo Z 950');
    expect(within(line(1)).getByRole('article')).toHaveTextContent('Borrador pendiente');
  });

  it('«Volver a lo leído en la línea» deja las configuraciones del texto', async () => {
    await open();
    await readList('equipo z 128gb esim j/a negro, azul 2500');
    await openSelector(1);
    fireEvent.click(chip(1, 'Color', /Azul/));
    await useSelection(1);
    expect(within(line(1)).getAllByRole('article')).toHaveLength(1);
    await act(async () => { fireEvent.click(within(selector(1)).getByRole('button', { name: 'Volver a lo leído en la línea' })); });
    expect(within(line(1)).getAllByRole('article')).toHaveLength(2);
  });

  it('se usa con el teclado: Tab llega a los colores y Espacio los marca', async () => {
    const user = userEvent.setup();
    await open();
    await readList('equipo z 128gb esim j/a negro 2500');
    const toggle = within(selector(1)).getByRole('button', { name: /Elegir en el selector visual/ });
    toggle.focus();
    await act(async () => { await user.keyboard('{Enter}'); });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const azul = chip(1, 'Color', /Azul/);
    azul.focus();
    await act(async () => { await user.keyboard(' '); });
    expect(azul).toBeChecked();
  });
});

describe('crear en lote las variantes que faltan', () => {
  it('vista previa detallada, confirmación explícita, una vez por configuración aunque dos líneas la repitan', async () => {
    await open();
    await readList('equipo z 256gb esim j/a azul, verde 2500\nequipo z 256gb esim j/a azul 2600');
    const batch = screen.getByRole('region', { name: 'Variantes que faltan' });
    expect(batch).toHaveTextContent('2 configuraciones válidas no tienen variante');
    fireEvent.click(within(batch).getByRole('button', { name: /Revisar y crear 2 variantes/ }));
    // The preview names each configuration and where it comes from; nothing is written yet.
    expect(batch).toHaveTextContent('Equipo Z · 256 GB · Azul · eSIM · J/A');
    expect(batch).toHaveTextContent('líneas 1, 2');
    expect(batch).toHaveTextContent('Equipo Z · 256 GB · Verde · eSIM · J/A');
    expect(batch).toHaveTextContent('borradores: ocultos, a 0,00, sin stock ni movimientos y sin publicar');
    expect(variantPosts).toEqual([]);
    await act(async () => { fireEvent.click(within(batch).getByRole('button', { name: 'Confirmar: crear 2 borradores' })); });
    expect(variantPosts).toHaveLength(2);
    expect(variantPosts.every((post) => post.url.includes('company=5'))).toBe(true);
    // Read again: every card is now the draft, and nothing is left to create.
    for (const card of within(line(1)).getAllByRole('article')) expect(card).toHaveTextContent('Borrador pendiente');
    expect(screen.queryByRole('region', { name: 'Variantes que faltan' })).toBeNull();
    expect(screen.getByText(/Se crearon 2 variantes como borrador/)).toBeInTheDocument();
  });

  it('sin permiso para gestionar productos no se ofrece', async () => {
    await open({ manage: false });
    await readList('equipo z 256gb esim j/a azul, verde 2500');
    expect(screen.queryByRole('region', { name: 'Variantes que faltan' })).toBeNull();
  });
});

describe('revisión de #438', () => {
  it('a · lo escrito en la línea no se borra hasta que el servidor responde, ni si la respuesta falla', async () => {
    await open();
    await readList('equipo z 128gb esim j/a negro, azul 2500');
    fireEvent.change(screen.getByLabelText('Precio comercial · línea 1, configuración 1'), { target: { value: '2999' } });
    await openSelector(1);
    fireEvent.click(chip(1, 'Color', /Azul/));
    let release!: () => void;
    resolveGate = new Promise((resolve) => { release = resolve; });
    resolveFails = () => true;
    act(() => { fireEvent.click(within(selector(1)).getByRole('button', { name: /Usar esta selección/ })); });
    // In flight: the cards and what was typed are still there.
    expect(screen.getByLabelText('Precio comercial · línea 1, configuración 1')).toHaveValue('2999');
    await act(async () => { release(); });
    // The answer failed: nothing was replaced or cleared, and the failure is said.
    expect(screen.getByLabelText('Precio comercial · línea 1, configuración 1')).toHaveValue('2999');
    expect(within(line(1)).getAllByRole('article')).toHaveLength(2);
    expect(screen.getByRole('alert')).toHaveTextContent(/No se pudo/);
    // Now it answers: the line's cards are the selection's, with nothing typed carried over.
    resolveFails = () => false;
    await useSelection(1);
    expect(within(line(1)).getAllByRole('article')).toHaveLength(1);
    expect(screen.getByLabelText('Precio comercial · línea 1, configuración 1')).toHaveValue('');
  });

  it('a · una respuesta que llega después de cambiar el texto se descarta sin borrar nada', async () => {
    await open();
    await readList('equipo z 128gb esim j/a negro, azul 2500');
    await openSelector(1);
    fireEvent.click(chip(1, 'Color', /Azul/));
    let release!: () => void;
    resolveGate = new Promise((resolve) => { release = resolve; });
    act(() => { fireEvent.click(within(selector(1)).getByRole('button', { name: /Usar esta selección/ })); });
    fireEvent.change(screen.getByLabelText('Texto de la lista del proveedor'), { target: { value: 'equipo z 128gb esim j/a negro 2500' } });
    await act(async () => { release(); });
    expect(screen.queryByRole('article', { name: 'Línea 1' })).toBeNull();
    expect(screen.getByLabelText('Texto de la lista del proveedor')).toHaveValue('equipo z 128gb esim j/a negro 2500');
  });

  it('c · una fila que el servidor lee como OTRO modelo no se usa: se dice, y nunca se ofrece crearla', async () => {
    await open();
    await readList('equipo z 256gb esim j/a azul 2500');
    await openSelector(1);
    fireEvent.click(chip(1, 'Color', /Verde/));
    otherModelForSelector = true;
    await useSelection(1);
    expect(selector(1)).toHaveTextContent('la leyó como «Equipo Z Pro»');
    // The text's own reading stays: no selector card, no «Crear variante» on the wrong model.
    expect(within(line(1)).getAllByRole('article')).toHaveLength(1);
    expect(within(line(1)).queryByText(/Elegida en el selector visual/)).toBeNull();
    expect(variantPosts).toEqual([]);
  });

  it('e · crear en lote lo que otra pestaña ya creó: el «duplicate» del servidor cuenta como hecho y se vuelve a leer', async () => {
    await open();
    await readList('equipo z 256gb esim j/a azul, verde 2500');
    // Another tab created «256 GB · Azul» meanwhile: this reading still says it is missing.
    created.add([G256, BLUE, ESIM, JA].sort((a, b) => a - b).join());
    const batch = screen.getByRole('region', { name: 'Variantes que faltan' });
    fireEvent.click(within(batch).getByRole('button', { name: /Revisar y crear 2 variantes/ }));
    await act(async () => { fireEvent.click(within(batch).getByRole('button', { name: 'Confirmar: crear 2 borradores' })); });
    expect(variantPosts).toHaveLength(2);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText(/1 ya existía/)).toBeInTheDocument();
    for (const card of within(line(1)).getAllByRole('article')) expect(card).toHaveTextContent('Con variante');
    expect(screen.queryByRole('region', { name: 'Variantes que faltan' })).toBeNull();
  });
});

describe('dos ofertas en una línea', () => {
  it('se propone separarla; nada se separa ni se clasifica solo', async () => {
    await open();
    await readList('equipo z 128gb esim j/a negro 2500 azul 2600');
    const group = screen.getByRole('group', { name: 'Varias ofertas en la línea 1' });
    expect(group).toHaveTextContent('equipo z 128gb esim j/a negro 2500');
    expect(group).toHaveTextContent('equipo z 128gb esim j/a azul 2600');
    // Never saved as it is, and no selector on a line that holds two offers.
    expect(within(line(1)).queryByRole('region', { name: /Selector visual/ })).toBeNull();
    for (const box of within(line(1)).queryAllByRole('checkbox', { name: /Incluir en el guardado/ })) expect(box).toBeDisabled();
    await act(async () => { fireEvent.click(within(group).getByRole('button', { name: /Separar en 2 líneas/ })); });
    expect(screen.getByLabelText('Texto de la lista del proveedor')).toHaveValue(
      'equipo z 128gb esim j/a negro 2500\nequipo z 128gb esim j/a azul 2600');
    expect(screen.getByRole('article', { name: 'Línea 1' })).toHaveTextContent('2500');
    expect(screen.getByRole('article', { name: 'Línea 2' })).toHaveTextContent('2600');
    for (const radio of within(line(2)).getAllByRole('radio')) expect(radio).not.toBeChecked();
  });

  it('sin permiso para ver costos la propuesta no muestra importes', async () => {
    await open({ costs: false });
    await readList('equipo z 128gb esim j/a negro 2500 azul 2600');
    const group = screen.getByRole('group', { name: 'Varias ofertas en la línea 1' });
    expect(group).not.toHaveTextContent(/2500|2600/);
  });
});
