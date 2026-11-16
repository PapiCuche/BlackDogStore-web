import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { SupplierListAssistant } from '@/app/admin/components/SupplierListAssistant';
import { fetchWithAuth } from '@/app/lib/auth';
import type { AttributeRole, CatalogVocabulary, VocabularyOption } from '@/app/admin/lib/internal-api';
import { phraseKey } from '@/app/admin/lib/supplier-list-grammar';
import { modelId, trainingVocabulary } from './support/training-vocabulary';

/**
 * Decisión del propietario (2026-11-16): desde el asistente de listas de proveedor se puede crear una SIM o una región
 * NUEVA y añadir una opción existente a la paleta del modelo — siempre a mano, confirmado, con `products.manage`, en el
 * atributo de la empresa que tiene ESE rol, y sin crear variantes, stock, movimientos, precios ni publicaciones. Lo que
 * ya existe (variante, alias, la misma opción escrita de otra forma) se reutiliza: nunca se ofrece crearlo otra vez.
 *
 * El servidor de estas pruebas es un catálogo de mentira con estado: el vocabulario, la paleta de cada modelo, las
 * opciones y la lectura (`/resolve`) cambian con lo que se escribe, como en el de verdad.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response;

const ROLES: AttributeRole[] = ['capacity', 'color', 'sim', 'region'];
const ATTRIBUTE_OF: Record<string, number> = { capacity: 1, color: 2, sim: 3, region: 4 };
const ATTRIBUTE_NAMES: Record<string, string> = { capacity: 'Capacidad', color: 'Color', sim: 'SIM', region: 'Región' };

type Doubt = { role: string; text: string; reason: string; readings: unknown[]; actions: string[] };

let vocab: CatalogVocabulary;
/** Options the server has but the screen's vocabulary does not show yet (created in another tab). */
let hiddenLabels: Set<string>;
let attributeRoles: AttributeRole[];
let existing: (ids: number[]) => boolean;
let extraDoubts: (row: Record<string, string>) => Doubt[];
let log: string[];
let optionPosts: { attribute: number; body: { label: string } }[];
let palettePuts: { model: number; option_ids: number[] }[];
let nextId: number;
let optionGate: Promise<void> | null;

const allOptions = () => (Object.values(vocab.options) as VocabularyOption[][]).flat();
const compact = (text: string) => phraseKey(text).replace(/ /g, '');

function findOption(role: AttributeRole, text: string): VocabularyOption | undefined {
  const key = role === 'capacity' ? compact(text) : phraseKey(text);
  const same = (other: string) => (role === 'capacity' ? compact(other) : phraseKey(other)) === key;
  return vocab.options[role].find((o) => same(o.label) || o.aliases.some((a) => same(a.alias)));
}

function resolveRow(row: Record<string, string>) {
  const modelKey = phraseKey(row.model_text).split(' ').map((w) => (w === 'iph' ? 'iphone' : w)).join(' ');
  const model = vocab.models.find((m) => m.key === modelKey);
  if (!model) {
    return { key: row.key, model: { text: row.model_text, status: 'unknown', via: null, model_id: null, model_name: null, candidates: [], extracted: [] },
      line: 'new', readings: [], error: '', configurations: [] };
  }
  const inPalette = (o: VocabularyOption) => o.model_ids.includes(model.id);
  const hasPalette = allOptions().some(inPalette);
  const options: unknown[] = [];
  const ids: number[] = [];
  const doubts: Doubt[] = [...extraDoubts(row)];
  const said: [AttributeRole, string | undefined][] = [
    ['capacity', row.capacity_text], ['color', row.colour_text], ['sim', row.sim_text], ['region', row.region_text]];
  const saidRoles = new Set<string>();
  for (const [role, text] of said) {
    if (!text) continue;
    saidRoles.add(role);
    const option = findOption(role, text);
    if (!option) {
      doubts.push({ role, text, reason: 'unknown', readings: [], actions: ['add_to_palette', 'alias_of', 'keep_pending'] });
      continue;
    }
    const alias = phraseKey(option.label) !== phraseKey(text) && role !== 'capacity';
    options.push({ role, text, option_id: option.id, label: option.label, via: alias ? 'alias' : 'label', alias_id: null });
    ids.push(option.id);
    if (hasPalette && !inPalette(option)) {
      doubts.push({ role, text, reason: 'outside', readings: [[{ text, option_id: option.id, label: option.label }]],
        actions: ['add_to_palette', 'keep_pending'] });
    }
  }
  for (const doubt of doubts) saidRoles.add(doubt.role);
  const missing = ROLES.filter((role) => !saidRoles.has(role) && vocab.options[role].some(inPalette));
  const reasons = new Set(doubts.map((d) => d.reason));
  const complete = !reasons.size && !missing.length;
  const product = complete && existing(ids) ? 5000 : null;
  const status = reasons.has('ambiguous') ? 'ambigua' : reasons.has('unknown') ? 'opcion_desconocida'
    : reasons.has('outside') ? 'fuera_de_paleta' : missing.length ? 'incompleta'
      : product ? 'variante_existente' : hasPalette ? 'combinacion_valida_sin_variante' : 'sin_paleta';
  return {
    key: row.key, line: 'new', readings: [], error: '',
    model: { text: row.model_text, status: 'model_word', via: 'model_word', model_id: model.id, model_name: model.name, candidates: [], extracted: [] },
    configurations: [{
      status, complete: ['variante_existente', 'combinacion_valida_sin_variante'].includes(status), colour_text: row.colour_text ?? '',
      option_ids: [...ids].sort((a, b) => a - b), options, product_id: product, product_name: product ? `${model.name} existente` : null,
      draft_pending: false, missing, doubts, candidates: [],
    }],
  };
}

function palettePayload(id: number) {
  const model = vocab.models.find((m) => m.id === id)!;
  return {
    model_id: id, model_name: model.name, has_palette: allOptions().some((o) => o.model_ids.includes(id)),
    attributes: ROLES.map((role) => ({
      id: ATTRIBUTE_OF[role], name: ATTRIBUTE_NAMES[role], kind: role === 'color' ? 'color' : 'text', role, sort_order: 0,
      options: vocab.options[role].map((o) => ({ id: o.id, label: o.label, swatch_hex: o.swatch_hex, sort_order: 0, in_palette: o.model_ids.includes(id) })),
    })),
    combinations: [], variants: [], summary: { combinations: 0, existing: 0, valid_without_variant: 0, outside: 0, incomplete: 0 }, truncated: false,
  };
}

function route() {
  send.mockImplementation(async (url, init) => {
    const path = String(url);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    log.push(`${method} ${path.replace(/^.*\/api/, '').replace(/\?.*$/, '')}`);
    if (path.includes('/admin/catalog/vocabulary/')) {
      const shown = JSON.parse(JSON.stringify(vocab)) as CatalogVocabulary;
      for (const role of ROLES) shown.options[role] = shown.options[role].filter((o) => !hiddenLabels.has(o.label));
      return reply(200, shown);
    }
    if (path.includes('/admin/suppliers/')) return reply(200, { count: 0, page: 1, page_size: 25, results: [] });
    if (path.includes('/admin/company-settings/')) return reply(200, { company: { id: 5 }, settings: { currency: 'PEN' } });
    if (path.includes('/admin/catalog/resolve/')) return reply(200, { rows: body.rows.map(resolveRow), summary: {} });
    const optionsPath = /\/admin\/catalog\/attributes\/(\d+)\/options\//.exec(path);
    if (optionsPath && method === 'POST') {
      const attribute = Number(optionsPath[1]);
      optionPosts.push({ attribute, body });
      if (optionGate) { const gate = optionGate; optionGate = null; await gate; }
      const role = ROLES.find((r) => ATTRIBUTE_OF[r] === attribute)!;
      const label = String(body.label);
      if (vocab.options[role].some((o) => phraseKey(o.label) === phraseKey(label))) {
        hiddenLabels.clear();
        return reply(400, { detail: `«${ATTRIBUTE_NAMES[role]}» ya tiene la opción «${label}».`, code: 'duplicate', field: 'label' });
      }
      const option: VocabularyOption = { id: ++nextId, label, key: phraseKey(label), attribute_id: attribute, swatch_hex: '', aliases: [], model_ids: [] };
      vocab.options[role].push(option);
      return reply(201, { id: option.id, label, swatch_hex: '', sort_order: 0 });
    }
    if (path.includes('/admin/catalog/attributes/')) {
      return reply(200, { results: attributeRoles.map((role) => ({ id: ATTRIBUTE_OF[role], name: ATTRIBUTE_NAMES[role], kind: role === 'color' ? 'color' : 'text', role, sort_order: 0, options: [] })) });
    }
    const palettePath = /\/admin\/catalog\/models\/(\d+)\/palette\//.exec(path);
    if (palettePath) {
      const id = Number(palettePath[1]);
      if (method === 'PUT') {
        // B1-SIMREG: the assistant only ADDS (`add_option_ids`); a whole-set PUT would be a lost-update risk.
        expect(body.option_ids).toBeUndefined();
        palettePuts.push({ model: id, option_ids: body.add_option_ids });
        for (const option of allOptions()) {
          if (body.add_option_ids.includes(option.id)) option.model_ids = [...new Set([...option.model_ids, id])];
        }
      }
      return reply(200, palettePayload(id));
    }
    return reply(404, {});
  });
}

beforeEach(() => {
  send.mockReset();
  vocab = trainingVocabulary();
  hiddenLabels = new Set();
  attributeRoles = ['capacity', 'color', 'sim', 'region'];
  existing = () => false;
  extraDoubts = () => [];
  log = [];
  optionPosts = [];
  palettePuts = [];
  nextId = 900;
  optionGate = null;
  route();
});

async function open({ manage = true } = {}) {
  await act(async () => {
    render(<SupplierListAssistant canManage={manage} canSeeCosts canViewCompany companyId={5} />);
  });
}

async function readList(text: string) {
  fireEvent.change(screen.getByLabelText('Texto de la lista del proveedor'), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
}

const line = (n: number) => screen.getByRole('article', { name: `Línea ${n}` });
const NEW_OPTION = /^(Es una SIM \/ región nueva|Crear «.*» como opción de)/;
const ADD_TO_PALETTE = /^Añadir «.*» a la paleta de/;
const newOptionButtons = () => screen.queryAllByRole('button', { name: NEW_OPTION });
const paletteButtons = () => screen.queryAllByRole('button', { name: ADD_TO_PALETTE });
const writes = () => log.filter((entry) => !entry.startsWith('GET ') && !entry.includes('/admin/catalog/resolve/'));
/** Every request the screen may make while creating an option: none of them touches stock, receipts or Kardex. */
const ALLOWED = [/\/admin\/catalog\/vocabulary\/$/, /\/admin\/catalog\/resolve\/$/, /\/admin\/catalog\/attributes\/$/,
  /\/admin\/catalog\/attributes\/\d+\/options\/$/, /\/admin\/catalog\/models\/\d+\/palette\/$/, /\/admin\/suppliers\/$/,
  /\/admin\/company-settings\/$/];

async function confirmNewOption(form: HTMLElement) {
  fireEvent.click(within(form).getByRole('button', { name: 'Revisar' }));
  await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Confirmar: crear opción' })); });
}

describe('lo que ya existe se reutiliza: nunca se ofrece crearlo', () => {
  it('una variante existente: ni opción nueva, ni paleta, ni variante', async () => {
    existing = () => true;
    await open();
    await readList('iph 18 pro max esim LLA 256gb 5500 borgoña');
    expect(line(1)).toHaveTextContent('Con variante');
    expect(newOptionButtons()).toHaveLength(0);
    expect(paletteButtons()).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /Crear variante/ })).toBeNull();
  });

  it('un alias de la empresa («japan» → J/A) se lee con él: no se ofrece crear nada', async () => {
    await open();
    await readList('iph 18 pro esim japan 512gb 5290 negro');
    expect(line(1)).toHaveTextContent('«japan» → J/A');
    expect(line(1)).toHaveTextContent('Sin variante');
    expect(newOptionButtons()).toHaveLength(0);
    expect(paletteButtons()).toHaveLength(0);
  });

  it('mayúsculas y grafías distintas de una opción existente se leen como ella; el formulario no deja duplicarla', async () => {
    await open();
    await readList('IPH 18 PRO MAX ESIM lla 256GB 5500 BORGOÑA\niph 18 pro max japonesa esim 256gb 5500 borgoña');
    expect(line(1)).toHaveTextContent('«ESIM» → eSIM');
    expect(within(line(1)).queryAllByRole('button', { name: NEW_OPTION })).toHaveLength(0);
    // The person types an existing option (another spelling) or an alias in the form: refused before any request.
    fireEvent.click(within(line(2)).getByRole('button', { name: 'Es una SIM / región nueva… («japonesa»)' }));
    const form = screen.getByRole('group', { name: 'Nueva opción para «japonesa»' });
    fireEvent.change(within(form).getByLabelText('Es una opción de'), { target: { value: 'sim' } });
    fireEvent.change(within(form).getByLabelText('Nombre de la opción'), { target: { value: '  E SIM ' } });
    fireEvent.change(within(form).getByLabelText('Nombre de la opción'), { target: { value: 'ESIM' } });
    expect(form).toHaveTextContent('«ESIM» ya existe en la empresa como «eSIM» (SIM): no se crea otra.');
    expect(within(form).getByRole('button', { name: 'Revisar' })).toBeDisabled();
    fireEvent.change(within(form).getByLabelText('Nombre de la opción'), { target: { value: 'Japan' } });
    expect(form).toHaveTextContent('ya existe en la empresa como alias de «J/A» (Región)');
    expect(within(form).getByRole('button', { name: 'Revisar' })).toBeDisabled();
    expect(optionPosts).toHaveLength(0);
  });

  it('si el servidor responde «duplicate», se muestra su texto, se relee la lista y nunca se crea otra', async () => {
    vocab.options.region.push({ id: 880, label: 'Japonesa', key: 'japonesa', attribute_id: 4, swatch_hex: '', aliases: [], model_ids: [] });
    hiddenLabels = new Set(['Japonesa']);
    await open();
    await readList('iph 18 pro max japonesa esim 256gb 5500 borgoña');
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Es una SIM / región nueva… («japonesa»)' }));
    const form = screen.getByRole('group', { name: 'Nueva opción para «japonesa»' });
    fireEvent.change(within(form).getByLabelText('Es una opción de'), { target: { value: 'region' } });
    const resolvesBefore = log.filter((entry) => entry.includes('/resolve/')).length;
    await confirmNewOption(form);
    expect(optionPosts).toHaveLength(1);
    expect(screen.getByRole('alert')).toHaveTextContent('«Región» ya tiene la opción «japonesa».');
    expect(palettePuts).toHaveLength(0);
    expect(log.filter((entry) => entry.includes('/resolve/')).length).toBe(resolvesBefore + 1);
    // Read again with the option the server has: now a value of the company outside the palette, never a new one.
    expect(line(1)).toHaveTextContent('Región«japonesa»');
    expect(within(line(1)).queryAllByRole('button', { name: NEW_OPTION })).toHaveLength(0);
    expect(within(line(1)).getByRole('button', { name: 'Añadir «Japonesa» a la paleta de iPhone 18 Pro Max' })).toBeInTheDocument();
    expect(optionPosts).toHaveLength(1);
  });
});

describe('una SIM o región nueva desde una palabra sin reconocer', () => {
  it('crear la opción, añadirla a la paleta y releer, en ese orden; la palabra pasa a leerse con ese rol', async () => {
    await open();
    const text = 'iph 18 pro max japonesa esim 256gb 5500 borgoña';
    await readList(text);
    expect(line(1)).toHaveTextContent('Incompleta');
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Es una SIM / región nueva… («japonesa»)' }));
    expect(optionPosts).toHaveLength(0);
    const form = screen.getByRole('group', { name: 'Nueva opción para «japonesa»' });
    expect(within(form).getByLabelText('Nombre de la opción')).toHaveValue('japonesa');
    expect(within(form).getByRole('button', { name: 'Revisar' })).toBeDisabled();
    fireEvent.change(within(form).getByLabelText('Es una opción de'), { target: { value: 'region' } });
    fireEvent.change(within(form).getByLabelText('Nombre de la opción'), { target: { value: '  Japonesa  ' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Revisar' }));
    expect(form).toHaveTextContent('Se creará «Japonesa» como opción de Región de la empresa');
    expect(form).toHaveTextContent('se añadirá a la paleta de iPhone 18 Pro Max');
    expect(form).toHaveTextContent('No se crean variantes, stock ni movimientos');
    log = [];
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Confirmar: crear opción' })); });
    const model = modelId(vocab, 'iPhone 18 Pro Max');
    expect(log).toEqual([
      'GET /admin/catalog/attributes/',
      'POST /admin/catalog/attributes/4/options/',
      `GET /admin/catalog/models/${model}/palette/`,
      `PUT /admin/catalog/models/${model}/palette/`,
      'GET /admin/catalog/vocabulary/',
      'POST /admin/catalog/resolve/',
    ]);
    expect(optionPosts).toEqual([{ attribute: 4, body: { label: 'Japonesa' } }]);
    const before = trainingVocabulary();
    const previous = (Object.values(before.options) as VocabularyOption[][]).flat().filter((o) => o.model_ids.includes(model)).map((o) => o.id);
    // Only the new option travels (`add_option_ids`); the server joins it to the palette under its lock.
    expect(palettePuts).toEqual([{ model, option_ids: [901] }]);
    expect(previous.length).toBeGreaterThan(0);
    expect(screen.getByText(/«Japonesa» quedó creada como opción de Región de la empresa y se añadió a la paleta de iPhone 18 Pro Max/)).toBeInTheDocument();
    // The same pasted text, read again: «japonesa» is now the region, and the configuration is valid.
    expect(screen.getByLabelText('Texto de la lista del proveedor')).toHaveValue(text);
    expect(line(1)).toHaveTextContent('Región«japonesa»');
    expect(line(1)).toHaveTextContent('Sin variante');
    expect(within(line(1)).getByRole('button', { name: /Crear variante/ })).toBeInTheDocument();
    // A second attempt sees it as existing: nothing is offered again.
    expect(newOptionButtons()).toHaveLength(0);
    await readList(`${text}\nIPH 18 PRO MAX JAPONESA ESIM 512GB 6150 BORGOÑA`);
    expect(newOptionButtons()).toHaveLength(0);
    expect(line(2)).toHaveTextContent('«JAPONESA» → Japonesa');
    expect(optionPosts).toHaveLength(1);
  });

  it('nunca para cifras, importes o palabras conocidas; sin atributos SIM ni región, no se ofrece', async () => {
    await open();
    await readList('iph 18 pro max kz esim LLA 256gb 5500 borgoña');
    expect(line(1)).toHaveTextContent('«kz»');
    expect(within(line(1)).queryAllByRole('button', { name: /«5500»|«256gb»|«esim»|«LLA»|«borgoña»|«iph»/ })).toHaveLength(0);
    expect(newOptionButtons()).toHaveLength(1);
    expect(within(line(1)).getByRole('button', { name: 'Es una SIM / región nueva… («kz»)' })).toBeInTheDocument();
  });

  it('una palabra pegada a un color («borgoña oscuro») es duda del color (D-B1-5): nunca se ofrece como SIM ni región', async () => {
    await open();
    await readList('iph 18 pro max esim LLA 256gb 5500 borgoña oscuro');
    expect(line(1)).toHaveTextContent('oscuro');
    expect(within(line(1)).queryByRole('button', { name: /Es una SIM \/ región nueva/ })).toBeNull();
  });

  it('una empresa sin atributo de SIM: sólo se ofrece región; sin ninguno de los dos, nada', async () => {
    attributeRoles = ['capacity', 'color', 'region'];
    await open();
    await readList('iph 18 pro max japonesa esim 256gb 5500 borgoña');
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Es una SIM / región nueva… («japonesa»)' }));
    const form = screen.getByRole('group', { name: 'Nueva opción para «japonesa»' });
    expect(within(form).queryByLabelText('Es una opción de')).toBeNull();
    await confirmNewOption(form);
    expect(optionPosts).toEqual([{ attribute: 4, body: { label: 'japonesa' } }]);
  });

  it('sin atributos con rol SIM ni región no hay botón', async () => {
    attributeRoles = ['capacity', 'color'];
    await open();
    await readList('iph 18 pro max japonesa esim 256gb 5500 borgoña');
    expect(within(line(1)).getByRole('button', { name: 'Es un alias de… («japonesa»)' })).toBeInTheDocument();
    expect(newOptionButtons()).toHaveLength(0);
  });
});

describe('dudas de SIM y región del servidor', () => {
  const unknown = (role: string, text: string) => (row: Record<string, string>) =>
    (row.model_text && !vocab.options[role as AttributeRole].some((o) => phraseKey(o.label) === phraseKey(text))
      ? [{ role, text, reason: 'unknown', readings: [], actions: ['add_to_palette', 'alias_of', 'keep_pending'] }] : []);

  it('SIM desconocida: «Crear «dual» como opción de SIM» en el atributo con ese rol', async () => {
    extraDoubts = unknown('sim', 'dual');
    await open();
    await readList('iph 18 pro max LLA 256gb 5500 borgoña');
    expect(line(1)).toHaveTextContent('Opción desconocida');
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Crear «dual» como opción de SIM' }));
    const form = screen.getByRole('group', { name: 'Nueva opción para «dual»' });
    expect(within(form).queryByLabelText('Es una opción de')).toBeNull();
    fireEvent.change(within(form).getByLabelText('Nombre de la opción'), { target: { value: 'Dual' } });
    await confirmNewOption(form);
    expect(optionPosts).toEqual([{ attribute: 3, body: { label: 'Dual' } }]);
    expect(palettePuts).toHaveLength(1);
    expect(palettePuts[0].option_ids).toContain(901);
    expect(screen.queryByRole('button', { name: 'Crear «dual» como opción de SIM' })).toBeNull();
  });

  it('región desconocida: se crea en el atributo de región', async () => {
    extraDoubts = unknown('region', 'HN');
    await open();
    await readList('iph 18 pro max esim 256gb 5500 borgoña');
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Crear «HN» como opción de Región' }));
    await confirmNewOption(screen.getByRole('group', { name: 'Nueva opción para «HN»' }));
    expect(optionPosts).toEqual([{ attribute: 4, body: { label: 'HN' } }]);
  });

  it('una empresa sin atributo de SIM: se dice cómo configurarlo y no se ofrece nada más', async () => {
    attributeRoles = ['capacity', 'color', 'region'];
    extraDoubts = unknown('sim', 'dual');
    await open();
    await readList('iph 18 pro max LLA 256gb 5500 borgoña');
    expect(line(1)).toHaveTextContent('Configura primero un atributo con el rol SIM en Catálogo › Modelos');
    expect(screen.queryByRole('button', { name: /como opción de SIM/ })).toBeNull();
  });

  it('una duda ambigua sigue siendo duda: no se crea ni se añade nada', async () => {
    extraDoubts = () => [{ role: 'sim', text: 'chip esim', reason: 'ambiguous',
      readings: [[{ text: 'chip', option_id: 1, label: 'Chip' }], [{ text: 'esim', option_id: 2, label: 'eSIM' }]],
      actions: ['add_to_palette', 'alias_of', 'keep_pending'] }];
    await open();
    await readList('iph 18 pro max LLA 256gb 5500 borgoña');
    expect(line(1)).toHaveTextContent('Ambigua');
    expect(newOptionButtons()).toHaveLength(0);
    expect(paletteButtons()).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /Crear variante/ })).toBeNull();
  });

  it('fuera de la paleta: «Añadir «eSIM» a la paleta de iPhone 15», confirmado, sin crear ninguna opción', async () => {
    await open();
    await readList('IPH 15 esim 128gb 2295 negro');
    expect(line(1)).toHaveTextContent('Fuera de la paleta');
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Añadir «eSIM» a la paleta de iPhone 15' }));
    expect(palettePuts).toHaveLength(0);
    const confirm = screen.getByRole('group', { name: 'Añadir «eSIM» a la paleta' });
    // The palette of iPhone 15 has no SIM yet: what that changes is said before confirming.
    expect(confirm).toHaveTextContent('todavía no tiene ninguna opción de SIM');
    await act(async () => { fireEvent.click(within(confirm).getByRole('button', { name: 'Confirmar: añadir a la paleta' })); });
    const model = modelId(vocab, 'iPhone 15');
    const esim = vocab.options.sim.find((o) => o.label === 'eSIM')!.id;
    expect(palettePuts).toHaveLength(1);
    expect(palettePuts[0].model).toBe(model);
    expect(palettePuts[0].option_ids).toContain(esim);
    expect(optionPosts).toHaveLength(0);
    expect(line(1)).not.toHaveTextContent('Fuera de la paleta');
    expect(paletteButtons()).toHaveLength(0);
  });
});

describe('permisos, seguridad y doble clic', () => {
  it('sin products.manage no hay botones ni se leen los atributos', async () => {
    extraDoubts = () => [{ role: 'sim', text: 'dual', reason: 'unknown', readings: [], actions: ['add_to_palette', 'alias_of', 'keep_pending'] }];
    await open({ manage: false });
    await readList('iph 18 pro max japonesa LLA 256gb 5500 borgoña\nIPH 15 esim 128gb 2295 negro');
    expect(newOptionButtons()).toHaveLength(0);
    expect(paletteButtons()).toHaveLength(0);
    expect(log.some((entry) => entry.includes('/admin/catalog/attributes/'))).toBe(false);
  });

  it('nada se escribe al cargar ni al leer; crear nunca toca stock, recepciones ni Kardex', async () => {
    await open();
    await readList('iph 18 pro max japonesa esim 256gb 5500 borgoña\nIPH 15 esim 128gb 2295 negro');
    expect(writes()).toEqual([]);
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Es una SIM / región nueva… («japonesa»)' }));
    const form = screen.getByRole('group', { name: 'Nueva opción para «japonesa»' });
    fireEvent.change(within(form).getByLabelText('Es una opción de'), { target: { value: 'region' } });
    await confirmNewOption(form);
    fireEvent.click(within(line(2)).getByRole('button', { name: 'Añadir «eSIM» a la paleta de iPhone 15' }));
    await act(async () => {
      fireEvent.click(within(screen.getByRole('group', { name: 'Añadir «eSIM» a la paleta' })).getByRole('button', { name: 'Confirmar: añadir a la paleta' }));
    });
    expect(writes()).toEqual([
      'POST /admin/catalog/attributes/4/options/',
      `PUT /admin/catalog/models/${modelId(vocab, 'iPhone 18 Pro Max')}/palette/`,
      `PUT /admin/catalog/models/${modelId(vocab, 'iPhone 15')}/palette/`,
    ]);
    for (const entry of log) {
      const path = entry.split(' ')[1];
      expect(ALLOWED.some((allowed) => allowed.test(path))).toBe(true);
      expect(path).not.toMatch(/stock|receipt|recepci|kardex|movement|inventory|variants|products|offer/i);
    }
  });

  it('un doble clic en «Confirmar» envía una sola opción; mientras tanto el botón está ocupado', async () => {
    let release: () => void = () => undefined;
    optionGate = new Promise<void>((resolve) => { release = resolve; });
    await open();
    await readList('iph 18 pro max japonesa esim 256gb 5500 borgoña');
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Es una SIM / región nueva… («japonesa»)' }));
    const form = screen.getByRole('group', { name: 'Nueva opción para «japonesa»' });
    fireEvent.change(within(form).getByLabelText('Es una opción de'), { target: { value: 'region' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Revisar' }));
    const confirm = within(form).getByRole('button', { name: 'Confirmar: crear opción' });
    await act(async () => { fireEvent.click(confirm); fireEvent.click(confirm); });
    expect(confirm).toBeDisabled();
    expect(screen.getByLabelText('Texto de la lista del proveedor')).toBeDisabled();
    await act(async () => { release(); });
    expect(optionPosts).toHaveLength(1);
    expect(palettePuts).toHaveLength(1);
    expect(newOptionButtons()).toHaveLength(0);
  });
});

describe('revisión M2 (P1-02): un modelo heredado de la cabecera no se usa para escribir el catálogo hasta confirmarlo', () => {
  const confirm = () => fireEvent.click(screen.getByRole('checkbox', { name: 'Confirmar el modelo de la cabecera para la línea 2' }));

  it('«Es una SIM / región nueva…» sólo aparece cuando la línea confirma el modelo de la cabecera', async () => {
    await open();
    await readList('iph 18 pro max\nesim japonesa 256gb 5500 borgoña');
    expect(line(2)).toHaveTextContent('modelo de la cabecera: línea 1');
    expect(within(line(2)).queryAllByRole('button', { name: NEW_OPTION })).toHaveLength(0);
    confirm();
    expect(within(line(2)).getByRole('button', { name: 'Es una SIM / región nueva… («japonesa»)' })).toBeInTheDocument();
  });

  it('«Añadir «X» a la paleta» y «Crear «X» como opción» tampoco, hasta confirmar', async () => {
    vocab.options.region.push({ id: 880, label: 'Japonesa', key: 'japonesa', attribute_id: 4, swatch_hex: '', aliases: [], model_ids: [] });
    extraDoubts = () => [{ role: 'sim', text: 'dual', reason: 'unknown', readings: [], actions: ['add_to_palette', 'alias_of', 'keep_pending'] }];
    await open();
    await readList('iph 18 pro max\nesim japonesa 256gb 5500 borgoña');
    expect(within(line(2)).queryAllByRole('button', { name: ADD_TO_PALETTE })).toHaveLength(0);
    expect(within(line(2)).queryAllByRole('button', { name: NEW_OPTION })).toHaveLength(0);
    confirm();
    expect(within(line(2)).getByRole('button', { name: 'Añadir «Japonesa» a la paleta de iPhone 18 Pro Max' })).toBeInTheDocument();
    expect(within(line(2)).getByRole('button', { name: 'Crear «dual» como opción de SIM' })).toBeInTheDocument();
    expect(writes()).toEqual([]);
  });
});
