import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CatalogAliasesSection, ResolverTryPanel } from '@/app/admin/components/CatalogVocabulary';
import { CatalogModelsManager } from '@/app/admin/components/CatalogModelsManager';
import { ModelPalettePanel } from '@/app/admin/components/ModelPalettePanel';
import type {
  AdminModelPalette, CatalogAlias, CatalogVocabulary, ResolveConfiguration, ResolverStatus,
} from '@/app/admin/lib/internal-api';
import { ALIAS_ERROR_LABELS, DOUBT_ACTION_LABELS } from '@/app/admin/lib/catalog-vocabulary';
import { PALETTE_STATUS_LABELS, RESOLVER_STATUS_HELP, RESOLVER_STATUS_LABELS, RESOLVER_STATUS_ORDER } from '@/app/admin/lib/palette';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-COMMERCIAL-01 · A2-2 — «Vocabulario de proveedores», «Probar una descripción» y los alias en la paleta.
 *
 * Con la API simulada (las reglas las prueba Django; el recorrido real, Playwright):
 *   - la lista por ámbito (palabras de modelo, nombres completos, opciones por rol) con su destino real y «Sin efecto»;
 *   - crear, editar y borrar (con confirmación propia, nunca `window.confirm`), sólo con `products.manage`;
 *   - los rechazos del servidor junto al campo que nombra (`field`), con el significado del código; el cliente sólo
 *     comprueba que no falte nada;
 *   - 403 / 404 / 429 / red, cada uno con su frase; un `duplicate` (otra escritura llegó antes) relee la lista;
 *   - el resolutor: cada estado, lo leído y cómo, lo que falta, las dudas con sus lecturas y las acciones posibles
 *     COMO INFORMACIÓN (ningún botón las ejecuta);
 *   - la paleta muestra los alias de cada opción desde UNA lectura del vocabulario.
 *
 * Nombres y valores inventados: nada aquí es de Apple ni de la tienda piloto.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; query: string; body: Record<string, unknown> };
let calls: Call[];
let answers: Record<string, (body: Record<string, unknown>) => Response | Promise<Response>>;

const alias = (over: Partial<CatalogAlias>): CatalogAlias => ({
  id: 0, scope: 'option', alias: '', alias_key: '', model_id: null, model_name: null, option_id: null, option_label: null,
  attribute_id: null, attribute_name: null, role: null, replacement: '', shadowed: false,
  created_at: '2026-11-13T10:00:00+00:00', updated_at: '2026-11-13T10:00:00+00:00', ...over,
});

const ALIASES: CatalogAlias[] = [
  alias({ id: 1, scope: 'model_word', alias: 'Mx', alias_key: 'mx', replacement: 'max' }),
  alias({ id: 2, scope: 'model', alias: 'EqZ Max', alias_key: 'eqz max', model_id: 6, model_name: 'Equipo Z Max' }),
  alias({ id: 3, scope: 'option', alias: 'Black', alias_key: 'black', option_id: 22, option_label: 'Negro',
    attribute_id: 4, attribute_name: 'Tono', role: 'color' }),
  alias({ id: 4, scope: 'option', alias: 'Global', alias_key: 'global', option_id: 41, option_label: 'Internacional',
    attribute_id: 7, attribute_name: 'Región', role: 'region', shadowed: true }),
  alias({ id: 5, scope: 'option', alias: 'Dual chip', alias_key: 'dual chip', option_id: 31, option_label: 'eSIM',
    attribute_id: 6, attribute_name: 'Tipo de SIM', role: 'sim' }),
];

const VOCABULARY: CatalogVocabulary = {
  model_words: [{ id: 1, alias: 'Mx', replacement: 'max' }],
  models: [
    { id: 5, name: 'Equipo Z', key: 'equipo z', aliases: [] },
    { id: 6, name: 'Equipo Z Max', key: 'equipo z max', aliases: [{ id: 2, alias: 'EqZ Max' }] },
  ],
  options: {
    capacity: [{ id: 11, label: '128 GB', key: '128 gb', attribute_id: 3, swatch_hex: '', aliases: [], model_ids: [5] }],
    color: [
      { id: 21, label: 'Azul', key: 'azul', attribute_id: 4, swatch_hex: '#1F3A93', aliases: [], model_ids: [5] },
      { id: 22, label: 'Negro', key: 'negro', attribute_id: 4, swatch_hex: '#111111',
        aliases: [{ id: 3, alias: 'Black' }, { id: 9, alias: 'Noir' }], model_ids: [5] },
    ],
    sim: [{ id: 31, label: 'eSIM', key: 'esim', attribute_id: 6, swatch_hex: '', aliases: [{ id: 5, alias: 'Dual chip' }], model_ids: [] }],
    region: [{ id: 41, label: 'Internacional', key: 'internacional', attribute_id: 7, swatch_hex: '', aliases: [{ id: 4, alias: 'Global' }], model_ids: [] }],
    other: [],
  },
  separator_words: ['and', 'e', 'y'],
};

function mockApi() {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.replace(/^.*\/api/, '').replace(/\?.*$/, '');
    const query = url.includes('?') ? url.slice(url.indexOf('?') + 1) : '';
    const body = init.body ? JSON.parse(String(init.body)) : {};
    calls.push({ method, path, query, body });
    const answer = answers[`${method} ${path}`];
    if (answer) return answer(body);
    if (method === 'GET' && path === '/admin/catalog/aliases/') return reply({ results: ALIASES, count: ALIASES.length });
    if (method === 'GET' && path === '/admin/catalog/vocabulary/') return reply(VOCABULARY);
    if (method === 'POST' && path === '/admin/catalog/aliases/') {
      return reply({ ...alias({ id: 99, ...body }), model_name: 'Equipo Z' }, 201);
    }
    if (method === 'PATCH' && /^\/admin\/catalog\/aliases\/\d+\/$/.test(path)) {
      const id = Number(path.split('/')[4]);
      return reply({ ...ALIASES.find((a) => a.id === id), ...body });
    }
    if (method === 'DELETE' && /^\/admin\/catalog\/aliases\/\d+\/$/.test(path)) return reply(null, 204);
    return reply({}, 404);
  });
}

beforeEach(() => {
  calls = [];
  answers = {};
  window.history.replaceState(null, '', '/admin/products/models');
  mockApi();
});

const writes = () => calls.filter((c) => c.method !== 'GET');
const listReads = () => calls.filter((c) => c.method === 'GET' && c.path === '/admin/catalog/aliases/').length;

async function mountSection(canManage = true) {
  render(<CatalogAliasesSection canManage={canManage} companyId={null} />);
  const section = await screen.findByRole('region', { name: 'Vocabulario de proveedores' });
  await within(section).findByText('«Mx»');
  return section;
}

const tab = (section: HTMLElement, name: RegExp) => within(section).getByRole('tab', { name });
const rowOf = (section: HTMLElement, text: string) => within(section).getByRole('listitem', { name: `Alias ${text}` });

describe('la lista, por ámbito', () => {
  it('tres pestañas con su cantidad; cada alias dice a qué apunta de verdad', async () => {
    const section = await mountSection();
    expect(tab(section, /Palabras de modelo/)).toHaveTextContent('(1)');
    expect(tab(section, /Nombres completos de modelo/)).toHaveTextContent('(1)');
    expect(tab(section, /Opciones/)).toHaveTextContent('(3)');
    expect(tab(section, /Palabras de modelo/)).toHaveAttribute('aria-selected', 'true');
    expect(rowOf(section, 'Mx')).toHaveTextContent('«Mx» se lee como «max»');

    fireEvent.click(tab(section, /Nombres completos/));
    expect(rowOf(section, 'EqZ Max')).toHaveTextContent('«EqZ Max» es el modelo Equipo Z Max');
    expect(within(section).queryByText('«Mx»')).toBeNull();
  });

  it('las opciones se agrupan por rol y muestran etiqueta, atributo y rol; un alias sin efecto se señala', async () => {
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    const panel = within(section).getByRole('tabpanel');
    const groups = within(panel).getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
    expect(groups).toEqual(['Color', 'SIM', 'Región']);
    expect(rowOf(section, 'Black')).toHaveTextContent('«Black» es la opción Negro (Tono · Color)');
    expect(rowOf(section, 'Dual chip')).toHaveTextContent('eSIM (Tipo de SIM · SIM)');
    // The attribute already says its role: not repeated.
    expect(rowOf(section, 'Global')).toHaveTextContent('Internacional (Región)');
    expect(rowOf(section, 'Global')).toHaveTextContent('Sin efecto');
    expect(rowOf(section, 'Global')).toHaveTextContent(/un nombre o una etiqueta real se lee igual/);
    expect(rowOf(section, 'Black')).not.toHaveTextContent('Sin efecto');
  });

  it('las pestañas se manejan con el teclado (flechas, Inicio, Fin)', async () => {
    const section = await mountSection();
    const first = tab(section, /Palabras de modelo/);
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(tab(section, /Nombres completos/)).toHaveAttribute('aria-selected', 'true');
    expect(tab(section, /Nombres completos/)).toHaveFocus();
    fireEvent.keyDown(tab(section, /Nombres completos/), { key: 'End' });
    expect(tab(section, /Opciones/)).toHaveFocus();
    fireEvent.keyDown(tab(section, /Opciones/), { key: 'ArrowRight' });
    expect(tab(section, /Palabras de modelo/)).toHaveFocus();
    const panel = within(section).getByRole('tabpanel');
    expect(panel).toHaveAttribute('aria-labelledby', tab(section, /Palabras de modelo/).id);
  });

  it('buscar lo hace el servidor (`q`), y un vacío lo dice', async () => {
    const section = await mountSection();
    answers['GET /admin/catalog/aliases/'] = () => reply({ results: [], count: 0 });
    fireEvent.change(within(section).getByLabelText(/Buscar alias/), { target: { value: 'zz' } });
    await act(async () => { fireEvent.click(within(section).getByRole('button', { name: 'Buscar' })); });
    expect(calls.filter((c) => c.path === '/admin/catalog/aliases/').at(-1)?.query).toBe('q=zz');
    expect(await within(section).findByText('Ningún alias de este tipo coincide con «zz».')).toBeInTheDocument();
  });

  it('un 403 al leer se explica en castellano; la red caída también', async () => {
    answers['GET /admin/catalog/aliases/'] = () => reply({ detail: 'x' }, 403);
    render(<CatalogAliasesSection canManage={false} companyId={null} />);
    expect(await screen.findByText('No tienes permiso para ver el vocabulario del catálogo.')).toBeInTheDocument();
  });

  it('sin conexión al leer, lo dice y ofrece volver a cargar', async () => {
    answers['GET /admin/catalog/aliases/'] = () => { throw new TypeError('Failed to fetch'); };
    render(<CatalogAliasesSection canManage companyId={null} />);
    expect(await screen.findByText(/No se pudo conectar con el servidor/)).toBeInTheDocument();
    delete answers['GET /admin/catalog/aliases/'];
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Volver a cargar' })); });
    expect(await screen.findByText('«Mx»')).toBeInTheDocument();
  });
});

describe('sólo lectura sin `products.manage`', () => {
  it('ve la lista, sin formularios ni botones de escritura', async () => {
    const section = await mountSection(false);
    expect(within(section).queryByRole('button', { name: /Agregar alias/ })).toBeNull();
    expect(within(section).queryByRole('button', { name: /Editar alias/ })).toBeNull();
    expect(within(section).queryByRole('button', { name: /Borrar alias/ })).toBeNull();
    fireEvent.click(tab(section, /Opciones/));
    expect(rowOf(section, 'Black')).toBeInTheDocument();
    expect(within(section).queryByRole('button', { name: /Borrar/ })).toBeNull();
  });
});

describe('crear', () => {
  it('el cliente sólo comprueba que no falte nada; luego envía exactamente el ámbito, el texto y el destino', async () => {
    const section = await mountSection();
    fireEvent.click(tab(section, /Nombres completos/));
    const opener = within(section).getByRole('button', { name: '+ Agregar alias' });
    fireEvent.click(opener);
    expect(opener).toHaveAttribute('aria-expanded', 'true');
    const form = within(section).getByRole('form', { name: /Nuevo alias/ });
    expect(opener).toHaveAttribute('aria-controls', form.parentElement!.id);
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(writes()).toEqual([]);
    expect(within(form).getByLabelText('Nombre del proveedor')).toHaveAttribute('aria-invalid', 'true');
    expect(within(form).getByText('Escribe el alias.')).toBeInTheDocument();
    expect(within(form).getByText('Elige el modelo.')).toBeInTheDocument();

    fireEvent.change(within(form).getByLabelText('Nombre del proveedor'), { target: { value: '  EqZ  ' } });
    await waitFor(() => expect(within(form).getByLabelText('Modelo')).toBeEnabled());
    fireEvent.change(within(form).getByLabelText('Modelo'), { target: { value: '5' } });
    const before = listReads();
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(writes()).toEqual([{ method: 'POST', path: '/admin/catalog/aliases/', query: '',
      body: { scope: 'model', alias: 'EqZ', model_id: 5 } }]);
    expect(await within(section).findByRole('status')).toHaveTextContent('Alias «EqZ» agregado.');
    await waitFor(() => expect(listReads()).toBeGreaterThan(before));
  });

  it('una palabra de modelo envía su reemplazo; una opción, el id de la opción elegida por rol', async () => {
    const section = await mountSection();
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    let form = within(section).getByRole('form', { name: /Nuevo alias/ });
    fireEvent.change(within(form).getByLabelText('Palabra del proveedor'), { target: { value: 'Prx' } });
    fireEvent.change(within(form).getByLabelText(/Se lee como/), { target: { value: 'pro' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(writes()[0].body).toEqual({ scope: 'model_word', alias: 'Prx', replacement: 'pro' });

    fireEvent.click(tab(section, /Opciones/));
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    form = within(section).getByRole('form', { name: /Nuevo alias/ });
    const select = within(form).getByLabelText('Opción') as HTMLSelectElement;
    await waitFor(() => expect(select).toBeEnabled());
    const groups = Array.from(select.querySelectorAll('optgroup')).map((g) => g.label);
    expect(groups).toEqual(['Capacidad', 'Color', 'SIM', 'Región']);
    fireEvent.change(within(form).getByLabelText('Texto del proveedor'), { target: { value: 'Midnight' } });
    fireEvent.change(select, { target: { value: '22' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(writes()[1].body).toEqual({ scope: 'option', alias: 'Midnight', option_id: 22 });
  });

  it.each([
    ['duplicate', 'alias', '«Black» ya es un alias de esta empresa con ese texto.'],
    ['conflict_with_label', 'alias', '«Negro» es su propia etiqueta: se lee sin alias.'],
    ['conflict_with_alias', 'alias', 'Con esta palabra, el alias «X» se leería también como «Y».'],
    ['contains_options', 'alias', '«Equipo Z 128» termina con una opción (128): un alias de modelo es sólo el nombre.'],
    ['too_many', 'alias', 'Esta empresa ya tiene 2000 alias: borra los que no use.'],
    ['invalid', 'alias', 'Un número o una capacidad («256gb») se leen solos, sin alias.'],
  ])('el rechazo `%s` se muestra junto al campo `%s`, con su significado y el texto del servidor', async (code, field, detail) => {
    answers['POST /admin/catalog/aliases/'] = () => reply({ detail, code, field }, 400);
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    const form = within(section).getByRole('form', { name: /Nuevo alias/ });
    fireEvent.change(within(form).getByLabelText('Texto del proveedor'), { target: { value: 'Black' } });
    await waitFor(() => expect(within(form).getByLabelText('Opción')).toBeEnabled());
    fireEvent.change(within(form).getByLabelText('Opción'), { target: { value: '22' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    const input = within(form).getByLabelText('Texto del proveedor');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    const message = document.getElementById(input.getAttribute('aria-describedby')!.split(' ')[0])!;
    expect(message).toHaveTextContent(`${ALIAS_ERROR_LABELS[code]}: ${detail}`);
    await waitFor(() => expect(input).toHaveFocus());
  });

  it('`chain` va junto al reemplazo de la palabra de modelo', async () => {
    answers['POST /admin/catalog/aliases/'] = () => reply({
      detail: 'Ese reemplazo encadena con otra palabra de modelo.', code: 'chain', field: 'replacement' }, 400);
    const section = await mountSection();
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    const form = within(section).getByRole('form', { name: /Nuevo alias/ });
    fireEvent.change(within(form).getByLabelText('Palabra del proveedor'), { target: { value: 'Prx' } });
    fireEvent.change(within(form).getByLabelText(/Se lee como/), { target: { value: 'mx' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(within(form).getByLabelText(/Se lee como/)).toHaveAttribute('aria-invalid', 'true');
    expect(within(form).getByLabelText('Palabra del proveedor')).not.toHaveAttribute('aria-invalid');
    expect(within(form).getByText(/Encadena con otra palabra: Ese reemplazo encadena/)).toBeInTheDocument();
  });

  it('404 de un destino (modelo de otra empresa o borrado) se dice junto a ese campo y se vuelve a leer', async () => {
    answers['POST /admin/catalog/aliases/'] = () => reply({ detail: 'Modelo no encontrado.', code: 'not_found', field: 'model_id' }, 404);
    const section = await mountSection();
    fireEvent.click(tab(section, /Nombres completos/));
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    const form = within(section).getByRole('form', { name: /Nuevo alias/ });
    fireEvent.change(within(form).getByLabelText('Nombre del proveedor'), { target: { value: 'EqZ' } });
    await waitFor(() => expect(within(form).getByLabelText('Modelo')).toBeEnabled());
    fireEvent.change(within(form).getByLabelText('Modelo'), { target: { value: '5' } });
    const before = listReads();
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(within(form).getByLabelText('Modelo')).toHaveAttribute('aria-invalid', 'true');
    expect(within(form).getByText(/Ya no existe: Modelo no encontrado\. Puede que lo hayan borrado/)).toBeInTheDocument();
    await waitFor(() => expect(listReads()).toBeGreaterThan(before));
  });

  it('403 al escribir: dice que hace falta permiso', async () => {
    answers['POST /admin/catalog/aliases/'] = () => reply({ detail: 'You do not have permission.' }, 403);
    const section = await mountSection();
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    const form = within(section).getByRole('form', { name: /Nuevo alias/ });
    fireEvent.change(within(form).getByLabelText('Palabra del proveedor'), { target: { value: 'Prx' } });
    fireEvent.change(within(form).getByLabelText(/Se lee como/), { target: { value: 'pro' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(within(form).getByRole('alert')).toHaveTextContent('No tienes permiso para cambiar el vocabulario');
  });

  it('sin conexión al escribir, lo dice en castellano', async () => {
    answers['POST /admin/catalog/aliases/'] = () => { throw new TypeError('Failed to fetch'); };
    const section = await mountSection();
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    const form = within(section).getByRole('form', { name: /Nuevo alias/ });
    fireEvent.change(within(form).getByLabelText('Palabra del proveedor'), { target: { value: 'Prx' } });
    fireEvent.change(within(form).getByLabelText(/Se lee como/), { target: { value: 'pro' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(within(form).getByRole('alert')).toHaveTextContent(/No se pudo conectar con el servidor/);
  });

  it('un 429 pide esperar', async () => {
    answers['POST /admin/catalog/aliases/'] = () => reply({ detail: 'Request was throttled.' }, 429);
    const section = await mountSection();
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    const form = within(section).getByRole('form', { name: /Nuevo alias/ });
    fireEvent.change(within(form).getByLabelText('Palabra del proveedor'), { target: { value: 'Prx' } });
    fireEvent.change(within(form).getByLabelText(/Se lee como/), { target: { value: 'pro' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(within(form).getByRole('alert')).toHaveTextContent('Demasiados cambios seguidos');
  });
});

describe('editar', () => {
  it('envía el texto y el destino, nunca el ámbito; el formulario se abre en la fila', async () => {
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    const button = within(rowOf(section, 'Black')).getByRole('button', { name: 'Editar alias Black' });
    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    const form = within(section).getByRole('form', { name: 'Editar alias «Black»' });
    expect(form.closest(`#${CSS.escape(button.getAttribute('aria-controls')!)}`)).not.toBeNull();
    const text = within(form).getByLabelText('Texto del proveedor') as HTMLInputElement;
    expect(text.value).toBe('Black');
    await waitFor(() => expect((within(form).getByLabelText('Opción') as HTMLSelectElement).value).toBe('22'));
    fireEvent.change(text, { target: { value: 'Jet black' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar alias' })); });
    expect(writes()).toEqual([{ method: 'PATCH', path: '/admin/catalog/aliases/3/', query: '',
      body: { alias: 'Jet black', option_id: 22 } }]);
    expect(await within(section).findByRole('status')).toHaveTextContent('Alias «Jet black» guardado.');
    await waitFor(() => expect(button).toHaveFocus());
  });

  it('`duplicate` (otra escritura llegó antes): se dice junto al campo y la lista se vuelve a leer, sin cerrar el formulario', async () => {
    answers['PATCH /admin/catalog/aliases/3/'] = () => reply({
      detail: '«Black» ya es un alias de esta empresa con ese texto.', code: 'duplicate', field: 'alias' }, 400);
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    fireEvent.click(within(rowOf(section, 'Black')).getByRole('button', { name: 'Editar alias Black' }));
    const form = within(section).getByRole('form', { name: 'Editar alias «Black»' });
    const before = listReads();
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar alias' })); });
    expect(within(form).getByText(/^Ya existe: «Black» ya es un alias/)).toBeInTheDocument();
    await waitFor(() => expect(listReads()).toBeGreaterThan(before));
    expect(within(section).getByRole('form', { name: 'Editar alias «Black»' })).toBeInTheDocument();
  });

  it('404 del alias (borrado o de otra empresa): se cierra, se avisa y la lista se vuelve a leer', async () => {
    answers['PATCH /admin/catalog/aliases/3/'] = () => reply({ detail: 'No encontrado.' }, 404);
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    fireEvent.click(within(rowOf(section, 'Black')).getByRole('button', { name: 'Editar alias Black' }));
    const form = within(section).getByRole('form', { name: 'Editar alias «Black»' });
    const before = listReads();
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar alias' })); });
    const alert = within(section).getByRole('alert');
    expect(alert).toHaveTextContent('Ese alias ya no existe en esta empresa. La lista se volvió a cargar.');
    expect(alert).toHaveFocus();
    expect(within(section).queryByRole('form', { name: 'Editar alias «Black»' })).toBeNull();
    await waitFor(() => expect(listReads()).toBeGreaterThan(before));
  });
});

describe('borrar, con confirmación propia', () => {
  it('pide confirmar antes de enviar; «Cancelar» devuelve el foco; nunca usa window.confirm', async () => {
    const confirmSpy = jest.spyOn(window, 'confirm').mockImplementation(() => true);
    const alertSpy = jest.spyOn(window, 'alert').mockImplementation(() => {});
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    const button = within(rowOf(section, 'Black')).getByRole('button', { name: 'Borrar alias Black' });
    fireEvent.click(button);
    expect(writes()).toEqual([]);
    const group = within(section).getByRole('group', { name: 'Borrar alias «Black»' });
    expect(group).toHaveTextContent('dejarán de leerse como Negro (Tono · Color)');
    expect(within(group).getByText('¿Borrar el alias «Black»?')).toHaveFocus();
    fireEvent.click(within(group).getByRole('button', { name: 'Cancelar' }));
    await waitFor(() => expect(button).toHaveFocus());
    expect(writes()).toEqual([]);

    fireEvent.click(button);
    const again = within(section).getByRole('group', { name: 'Borrar alias «Black»' });
    await act(async () => { fireEvent.click(within(again).getByRole('button', { name: 'Sí, borrar' })); });
    expect(writes()).toEqual([{ method: 'DELETE', path: '/admin/catalog/aliases/3/', query: '', body: {} }]);
    expect(await within(section).findByRole('status')).toHaveTextContent('Se borró el alias «Black».');
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
    alertSpy.mockRestore();
  });

  it('404 al borrar: ya no existía; se avisa y se vuelve a leer', async () => {
    answers['DELETE /admin/catalog/aliases/3/'] = () => reply({ detail: 'No encontrado.' }, 404);
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    fireEvent.click(within(rowOf(section, 'Black')).getByRole('button', { name: 'Borrar alias Black' }));
    const before = listReads();
    await act(async () => { fireEvent.click(within(section).getByRole('button', { name: 'Sí, borrar' })); });
    expect(within(section).getByRole('alert')).toHaveTextContent('Ese alias ya no existe en esta empresa.');
    await waitFor(() => expect(listReads()).toBeGreaterThan(before));
  });

  it('403 y red al borrar se explican dentro de la confirmación', async () => {
    answers['DELETE /admin/catalog/aliases/3/'] = () => reply({}, 403);
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    fireEvent.click(within(rowOf(section, 'Black')).getByRole('button', { name: 'Borrar alias Black' }));
    const group = within(section).getByRole('group', { name: 'Borrar alias «Black»' });
    await act(async () => { fireEvent.click(within(group).getByRole('button', { name: 'Sí, borrar' })); });
    expect(within(group).getByRole('alert')).toHaveTextContent('No tienes permiso');
    answers['DELETE /admin/catalog/aliases/3/'] = () => { throw new TypeError('Failed to fetch'); };
    await act(async () => { fireEvent.click(within(group).getByRole('button', { name: 'Sí, borrar' })); });
    expect(within(group).getByRole('alert')).toHaveTextContent(/No se pudo conectar/);
  });
});

describe('en la página de modelos', () => {
  it('la sección está junto a «Atributos y opciones», con su ancla; «#vocabulario-opciones» abre la pestaña de opciones', async () => {
    window.history.replaceState(null, '', '/admin/products/models#vocabulario-opciones');
    render(<CatalogModelsManager canManage={false} companyId={null} />);
    const section = await screen.findByRole('region', { name: 'Vocabulario de proveedores' });
    expect(section).toHaveAttribute('id', 'vocabulario');
    expect(within(section).getByRole('tab', { name: /Opciones/ })).toHaveAttribute('aria-selected', 'true');
    expect(within(section).getByRole('tab', { name: /Opciones/ })).toHaveAttribute('id', 'vocabulario-opciones');
    expect(screen.getByRole('region', { name: 'Probar una descripción' })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// «Probar una descripción»
// ---------------------------------------------------------------------------

const configuration = (status: ResolverStatus, over: Partial<ResolveConfiguration> = {}): ResolveConfiguration => ({
  status, complete: status === 'variante_existente' || status === 'combinacion_valida_sin_variante', colour_text: '',
  option_ids: [], options: [], product_id: null, product_name: null, draft_pending: false, missing: [], doubts: [],
  candidates: [], ...over,
});

const EVERY_STATUS: ResolveConfiguration[] = [
  configuration('variante_existente', {
    colour_text: 'Black', product_id: 70, product_name: 'Equipo Z 128 GB Negro',
    options: [
      { role: 'capacity', text: '128gb', option_id: 11, label: '128 GB', via: 'capacity', alias_id: null },
      { role: 'color', text: 'Black', option_id: 22, label: 'Negro', via: 'alias', alias_id: 3 },
    ],
  }),
  configuration('combinacion_valida_sin_variante', { colour_text: 'Azul',
    options: [{ role: 'color', text: 'Azul', option_id: 21, label: 'Azul', via: 'label', alias_id: null }] }),
  configuration('fuera_de_paleta', { colour_text: 'Rojo', doubts: [
    { role: 'color', text: 'Rojo', reason: 'outside', readings: [[{ text: 'Rojo', option_id: 23, label: 'Rojo' }]],
      actions: ['add_to_palette', 'keep_pending'] }] }),
  configuration('incompleta', { missing: ['sim', 'other:Grado'], candidates: [
    { product_id: 71, name: 'Equipo Z 128 GB Azul eSIM', draft_pending: true }] }),
  configuration('ambigua', { colour_text: 'Azul Marino Claro', doubts: [
    { role: 'color', text: 'Azul Marino Claro', reason: 'ambiguous', readings: [
      [{ text: 'Azul Marino Claro', option_id: null, label: null }],
      [{ text: 'Azul Marino', option_id: 24, label: 'Azul Marino' }, { text: 'Claro', option_id: null, label: null }],
    ], actions: ['add_to_palette', 'alias_of', 'keep_pending'] }] }),
  configuration('opcion_desconocida', { colour_text: 'Grafito', doubts: [
    { role: 'color', text: 'Grafito', reason: 'unknown', readings: [], actions: ['add_to_palette', 'alias_of', 'keep_pending'] }] }),
  configuration('nombre_con_opciones', { doubts: [
    { role: 'model', text: 'Equipo Z 128 (eSIM)', reason: 'name_with_options',
      readings: [[{ text: 'eSIM', option_id: 31, label: 'eSIM' }]], actions: ['alias_of', 'keep_pending'] }] }),
  configuration('modelo_desconocido', { doubts: [
    { role: 'model', text: 'Equipo Q', reason: 'unknown', readings: [], actions: ['alias_of', 'keep_pending'] }] }),
  configuration('sin_paleta'),
];

const RESOLVED = {
  rows: [{
    key: 'prueba',
    model: { text: 'EqZ 128gb', status: 'alias', via: 'alias', model_id: 5, model_name: 'Equipo Z', candidates: [],
      extracted: [] },
    line: 'new', configurations: EVERY_STATUS, readings: [], error: '',
  }],
  summary: Object.fromEntries(RESOLVER_STATUS_ORDER.map((s) => [s, 1])),
};

async function mountResolver() {
  render(<ResolverTryPanel companyId={null} />);
  return screen.getByRole('region', { name: 'Probar una descripción' });
}

describe('«Probar una descripción»', () => {
  it('sólo pide modelo, línea, capacidad, color, SIM y región: ningún importe, cantidad, precio ni IMEI', async () => {
    const panel = await mountResolver();
    const boxes = within(panel).getAllByRole('textbox');
    expect(boxes).toHaveLength(5);
    ['Modelo', 'Capacidad', 'Color (uno o varios)', 'SIM', 'Región'].forEach((name, index) => {
      expect(boxes[index]).toHaveAccessibleName(name);
    });
    expect(within(panel).getByLabelText('Línea')).toBeInTheDocument();
    expect(within(panel).queryByLabelText(/precio|importe|cantidad|imei|costo/i)).toBeNull();
    expect(within(panel).queryByRole('spinbutton')).toBeNull();
  });

  it('sin modelo no consulta; con él envía sólo los campos escritos', async () => {
    answers['POST /admin/catalog/resolve/'] = () => reply(RESOLVED);
    const panel = await mountResolver();
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Probar' })); });
    expect(writes()).toEqual([]);
    expect(within(panel).getByLabelText('Modelo')).toHaveAttribute('aria-invalid', 'true');
    fireEvent.change(within(panel).getByLabelText('Modelo'), { target: { value: ' EqZ ' } });
    fireEvent.change(within(panel).getByLabelText('Capacidad'), { target: { value: '128gb' } });
    fireEvent.change(within(panel).getByLabelText('Línea'), { target: { value: 'used' } });
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Probar' })); });
    expect(writes()).toEqual([{ method: 'POST', path: '/admin/catalog/resolve/', query: '',
      body: { rows: [{ key: 'prueba', model_text: 'EqZ', line: 'used', capacity_text: '128gb' }] } }]);
  });

  it('muestra cada estado con su explicación, lo leído y cómo, lo que falta, las dudas y los candidatos', async () => {
    answers['POST /admin/catalog/resolve/'] = () => reply(RESOLVED);
    const panel = await mountResolver();
    fireEvent.change(within(panel).getByLabelText('Modelo'), { target: { value: 'EqZ' } });
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Probar' })); });
    const result = within(panel).getByRole('region', { name: 'Resultado' });
    expect(within(result).getByRole('heading', { name: 'Resultado' })).toHaveFocus();
    expect(result).toHaveTextContent('Por un alias de nombre completo → Equipo Z');
    const cards = within(result).getAllByRole('article');
    expect(cards).toHaveLength(9);
    EVERY_STATUS.forEach((config, index) => {
      expect(cards[index]).toHaveAccessibleName(`Configuración ${index + 1} de 9: ${RESOLVER_STATUS_LABELS[config.status]}`);
      expect(cards[index]).toHaveTextContent(RESOLVER_STATUS_HELP[config.status]);
    });
    // The palette's four keep the palette's words.
    expect(RESOLVER_STATUS_LABELS.variante_existente).toBe(PALETTE_STATUS_LABELS.variante_existente);
    // Read, and how.
    expect(cards[0]).toHaveTextContent('Capacidad: «128gb» → 128 GB (por capacidad (sin espacio))');
    expect(cards[0]).toHaveTextContent('Color: «Black» → Negro (por un alias de la empresa)');
    expect(within(cards[0]).getByRole('link', { name: 'Equipo Z 128 GB Negro' })).toHaveAttribute('href', '/admin/products/70');
    // Missing (a role and an attribute without a role) and the partial fits, as information.
    expect(cards[3]).toHaveTextContent('Falta decir');
    expect(within(cards[3]).getByText('SIM')).toBeInTheDocument();
    expect(within(cards[3]).getByText('Grado')).toBeInTheDocument();
    expect(cards[3]).toHaveTextContent('Variantes que encajan en parte (sólo información; ninguna se elige)');
    expect(cards[3]).toHaveTextContent('(borrador pendiente)');
    // A doubt with every reading, never chosen.
    expect(cards[4]).toHaveTextContent('Ambiguo');
    expect(cards[4]).toHaveTextContent('«Azul Marino Claro» → sin opción conocida');
    expect(cards[4]).toHaveTextContent('«Azul Marino» → Azul Marino + «Claro» → sin opción conocida');
    expect(cards[2]).toHaveTextContent('Fuera de la paleta');
    expect(cards[6]).toHaveTextContent('Opciones dentro del nombre');
    expect(cards[7]).toHaveTextContent('Modelo: «Equipo Q»');
    expect(result).toHaveTextContent('1 con variante');
  });

  it('las acciones posibles son información: no hay botones que las ejecuten ni escrituras', async () => {
    answers['POST /admin/catalog/resolve/'] = () => reply(RESOLVED);
    const panel = await mountResolver();
    fireEvent.change(within(panel).getByLabelText('Modelo'), { target: { value: 'EqZ' } });
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Probar' })); });
    const result = within(panel).getByRole('region', { name: 'Resultado' });
    for (const text of Object.values(DOUBT_ACTION_LABELS)) {
      expect(within(result).getAllByText(text).length).toBeGreaterThan(0);
      expect(within(result).queryByRole('button', { name: text })).toBeNull();
      expect(within(result).queryByRole('link', { name: text })).toBeNull();
    }
    expect(within(result).queryAllByRole('button')).toHaveLength(0);
    expect(result).toHaveTextContent('Qué puede hacer una persona (no se hace desde aquí)');
    expect(writes().map((w) => w.path)).toEqual(['/admin/catalog/resolve/']);
  });

  it('el error de la fila (una línea no válida, un identificador) se muestra; un 400 y la red también', async () => {
    answers['POST /admin/catalog/resolve/'] = () => reply({ rows: [{ key: 'prueba', model: null, line: null,
      configurations: [], readings: [], error: 'Parece un IMEI, una serie o un teléfono: no se busca.' }], summary: {} });
    const panel = await mountResolver();
    fireEvent.change(within(panel).getByLabelText('Modelo'), { target: { value: 'x' } });
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Probar' })); });
    expect(within(panel).getByRole('alert')).toHaveTextContent('Parece un IMEI');

    answers['POST /admin/catalog/resolve/'] = () => reply({ detail: 'Campos no admitidos: price.', code: 'invalid' }, 400);
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Probar' })); });
    expect(within(panel).getByText('Campos no admitidos: price.')).toBeInTheDocument();

    answers['POST /admin/catalog/resolve/'] = () => { throw new TypeError('Failed to fetch'); };
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Probar' })); });
    expect(within(panel).getByText(/No se pudo conectar con el servidor/)).toBeInTheDocument();

    answers['POST /admin/catalog/resolve/'] = () => reply({ detail: 'x' }, 403);
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Probar' })); });
    expect(within(panel).getByText('No tienes permiso para ver el vocabulario del catálogo.')).toBeInTheDocument();
  });

  it('la leyenda de estados es un desplegable accesible con los nueve estados', async () => {
    const panel = await mountResolver();
    const toggle = within(panel).getByRole('button', { name: '¿Qué significa cada estado?' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const legend = document.getElementById(toggle.getAttribute('aria-controls')!)!;
    for (const status of RESOLVER_STATUS_ORDER) expect(legend).toHaveTextContent(RESOLVER_STATUS_LABELS[status]);
  });
});

// ---------------------------------------------------------------------------
// The palette's chips
// ---------------------------------------------------------------------------

const option = (id: number, label: string, in_palette: boolean, swatch_hex = '') => ({ id, label, swatch_hex, sort_order: 0, in_palette });

const PALETTE = {
  model_id: 5, model_name: 'Equipo Z', has_palette: true,
  attributes: [
    { id: 3, name: 'Capacidad', kind: 'text', role: 'capacity', sort_order: 0, options: [option(11, '128 GB', true)] },
    { id: 4, name: 'Tono', kind: 'color', role: 'color', sort_order: 1,
      options: [option(21, 'Azul', true, '#1F3A93'), option(22, 'Negro', true, '#111111')] },
  ],
  combinations: [], variants: [],
  summary: { combinations: 0, existing: 0, valid_without_variant: 0, outside: 0, incomplete: 0 },
  truncated: false,
} as unknown as AdminModelPalette;

describe('los alias en la paleta del modelo', () => {
  it('cada opción con alias muestra cuántos y los despliega con un enlace al vocabulario', () => {
    render(<ModelPalettePanel modelId={5} companyId={null} canManage palette={PALETTE} error={null} onSaved={() => {}}
      vocabulary={VOCABULARY} vocabularyError={null} />);
    const panel = screen.getByRole('region', { name: 'Opciones de este modelo' });
    expect(within(panel).queryByRole('button', { name: /alias de Azul/ })).toBeNull();
    const toggle = within(panel).getByRole('button', { name: '2 alias de Negro' });
    expect(toggle).toHaveTextContent('2 alias');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).not.toHaveAttribute('aria-controls');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const region = document.getElementById(toggle.getAttribute('aria-controls')!)!;
    const list = within(region).getByRole('list', { name: 'Alias de Negro' });
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Black', 'Noir']);
    expect(within(region).getByRole('link', { name: 'Gestionar en Vocabulario de proveedores' }))
      .toHaveAttribute('href', '/admin/products/models#vocabulario-opciones');
    // The checkbox of the option is still its own control.
    expect(within(panel).getByRole('checkbox', { name: 'Negro' })).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('sin vocabulario (falló su lectura) lo dice y la paleta sigue funcionando', () => {
    render(<ModelPalettePanel modelId={5} companyId={null} canManage palette={PALETTE} error={null} onSaved={() => {}}
      vocabulary={null} vocabularyError="No tienes permiso para ver el vocabulario del catálogo." />);
    const panel = screen.getByRole('region', { name: 'Opciones de este modelo' });
    expect(panel).toHaveTextContent('No se pudieron leer los alias de proveedores');
    expect(within(panel).getByRole('checkbox', { name: 'Negro' })).toBeEnabled();
  });

  it('la página del modelo lee el vocabulario UNA vez, no una por opción', async () => {
    const MODEL = { id: 5, name: 'Equipo Z', slug: 'equipo-z', category_id: null, category_name: null, image_url: '',
      description: '', is_published_online: true, sort_order: 0, variant_count: 0, used_unit_count: 0, has_palette: true, variants: [] };
    answers['GET /admin/catalog/models/5/'] = () => reply(MODEL);
    answers['GET /admin/catalog/models/5/palette/'] = () => reply(PALETTE);
    answers['GET /admin/catalog/attributes/'] = () => reply([]);
    answers['GET /admin/catalog/settings/'] = () => reply({ new_line_label: '', used_line_label: '' });
    answers['GET /admin/categories/'] = () => reply([]);
    const { CatalogModelEditor } = await import('@/app/admin/components/CatalogModelEditor');
    render(<CatalogModelEditor modelId={5} canManage companyId={null} />);
    const toggle = await screen.findByRole('button', { name: '2 alias de Negro' });
    expect(toggle).toBeInTheDocument();
    expect(calls.filter((c) => c.path === '/admin/catalog/vocabulary/')).toHaveLength(1);
    expect(calls.filter((c) => c.path.startsWith('/admin/catalog/aliases/'))).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Review of PR #279 (A2-2): regressions
// ---------------------------------------------------------------------------

function deferred() {
  let release: (r: Response) => void = () => {};
  const promise = new Promise<Response>((resolve) => { release = resolve; });
  return { promise, release: (r: Response) => release(r) };
}

describe('revisión A2-2', () => {
  it('L1. con una escritura en curso, nada más se mueve; su rechazo llega a su formulario y no se pierde', async () => {
    const pending = deferred();
    answers['PATCH /admin/catalog/aliases/3/'] = () => pending.promise;
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    fireEvent.click(within(rowOf(section, 'Black')).getByRole('button', { name: 'Editar alias Black' }));
    const form = within(section).getByRole('form', { name: 'Editar alias «Black»' });
    act(() => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar alias' })); });
    // Meanwhile: other rows, the tabs and «Agregar» are held still.
    expect(within(rowOf(section, 'Dual chip')).getByRole('button', { name: 'Editar alias Dual chip' })).toBeDisabled();
    expect(within(rowOf(section, 'Dual chip')).getByRole('button', { name: 'Borrar alias Dual chip' })).toBeDisabled();
    expect(tab(section, /Palabras de modelo/)).toBeDisabled();
    expect(within(section).getByRole('button', { name: '+ Agregar alias' })).toBeDisabled();
    await act(async () => { pending.release(reply({ detail: 'Texto no válido para un alias.', code: 'invalid', field: 'alias' }, 400)); });
    expect(within(section).getByRole('form', { name: 'Editar alias «Black»' })).toHaveTextContent('Texto no válido: Texto no válido para un alias.');
    expect(within(rowOf(section, 'Dual chip')).getByRole('button', { name: 'Editar alias Dual chip' })).toBeEnabled();
  });

  it('L1. con un borrado en curso no se abre otra fila; al terminar sólo se cierra la suya', async () => {
    const pending = deferred();
    answers['DELETE /admin/catalog/aliases/3/'] = () => pending.promise;
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    fireEvent.click(within(rowOf(section, 'Black')).getByRole('button', { name: 'Borrar alias Black' }));
    const group = within(section).getByRole('group', { name: 'Borrar alias «Black»' });
    act(() => { fireEvent.click(within(group).getByRole('button', { name: 'Sí, borrar' })); });
    const other = within(rowOf(section, 'Dual chip')).getByRole('button', { name: 'Editar alias Dual chip' });
    expect(other).toBeDisabled();
    fireEvent.click(other);
    expect(within(section).queryByRole('form', { name: 'Editar alias «Dual chip»' })).toBeNull();
    await act(async () => { pending.release(reply(null, 204)); });
    expect(within(section).getByRole('status')).toHaveTextContent('Se borró el alias «Black».');
  });

  it('L2. una lectura vieja de la lista no pisa la nueva', async () => {
    const first = deferred();
    let call = 0;
    answers['GET /admin/catalog/aliases/'] = () => (++call === 1 ? first.promise : reply({ results: [], count: 0 }));
    render(<CatalogAliasesSection canManage companyId={null} />);
    const section = await screen.findByRole('region', { name: 'Vocabulario de proveedores' });
    fireEvent.change(within(section).getByLabelText(/Buscar alias/), { target: { value: 'zz' } });
    await act(async () => { fireEvent.click(within(section).getByRole('button', { name: 'Buscar' })); });
    expect(await within(section).findByText('Ningún alias de este tipo coincide con «zz».')).toBeInTheDocument();
    await act(async () => { first.release(reply({ results: ALIASES, count: ALIASES.length })); });
    expect(within(section).queryByText('«Mx»')).toBeNull();
  });

  it('L2. al cambiar de empresa, el vocabulario de la anterior no llega tarde a los formularios', async () => {
    const first = deferred();
    let call = 0;
    const OTHER = { ...VOCABULARY, models: [{ id: 80, name: 'Modelo de otra empresa', key: 'x', aliases: [] }] };
    answers['GET /admin/catalog/vocabulary/'] = () => (++call === 1 ? first.promise : reply(OTHER));
    const { rerender } = render(<CatalogAliasesSection canManage companyId={1} />);
    rerender(<CatalogAliasesSection canManage companyId={2} />);
    const section = await screen.findByRole('region', { name: 'Vocabulario de proveedores' });
    await within(section).findByText('«Mx»');
    fireEvent.click(tab(section, /Nombres completos/));
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    const select = within(section).getByLabelText('Modelo') as HTMLSelectElement;
    await waitFor(() => expect(select).toBeEnabled());
    await act(async () => { first.release(reply(VOCABULARY)); });
    const names = Array.from(select.options).map((o) => o.textContent);
    expect(names).toContain('Modelo de otra empresa');
    expect(names).not.toContain('Equipo Z');
  });

  it('L4. «Opción» conserva la opción del alias aunque no esté en el vocabulario, y distingue etiquetas repetidas por atributo', async () => {
    answers['GET /admin/catalog/attributes/'] = () => reply([
      { id: 4, name: 'Tono', kind: 'color', role: 'color', sort_order: 0, options: [] },
      { id: 8, name: 'Acabado', kind: 'color', role: 'other', sort_order: 1, options: [] },
    ]);
    const twins = { ...VOCABULARY, options: { ...VOCABULARY.options, color: [
      ...VOCABULARY.options.color,
      { id: 25, label: 'Negro', key: 'negro', attribute_id: 8, swatch_hex: '', aliases: [], model_ids: [] },
    ] } };
    answers['GET /admin/catalog/vocabulary/'] = () => reply(twins);
    answers['GET /admin/catalog/aliases/'] = () => reply({ results: [...ALIASES,
      alias({ id: 6, scope: 'option', alias: 'Viejo', alias_key: 'viejo', option_id: 99, option_label: 'Retirado',
        attribute_id: 9, attribute_name: 'Grado', role: 'other' })], count: 6 });
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    fireEvent.click(within(rowOf(section, 'Viejo')).getByRole('button', { name: 'Editar alias Viejo' }));
    const form = within(section).getByRole('form', { name: 'Editar alias «Viejo»' });
    const select = within(form).getByLabelText('Opción') as HTMLSelectElement;
    await waitFor(() => expect(select).toBeEnabled());
    await waitFor(() => expect(Array.from(select.options).map((o) => o.textContent)).toContain('Negro (Acabado)'));
    expect(select.value).toBe('99');
    expect(select.selectedOptions[0].textContent).toBe('Retirado (Grado)');
    const labels = Array.from(select.options).map((o) => o.textContent);
    expect(labels).toContain('Negro (Tono)');
    expect(labels).toContain('Azul');
  });

  it('L6. las pestañas sólo apuntan (aria-controls) a un panel que existe', async () => {
    const section = await mountSection();
    for (const item of within(section).getAllByRole('tab')) {
      const target = item.getAttribute('aria-controls');
      if (target) expect(document.getElementById(target)).not.toBeNull();
    }
    expect(tab(section, /Palabras de modelo/)).toHaveAttribute('aria-controls');
  });

  it('L8. tras «Agregar alias» el foco sigue en el formulario abierto (no salta al aviso)', async () => {
    const section = await mountSection();
    fireEvent.click(within(section).getByRole('button', { name: '+ Agregar alias' }));
    const form = within(section).getByRole('form', { name: /Nuevo alias/ });
    fireEvent.change(within(form).getByLabelText('Palabra del proveedor'), { target: { value: 'Prx' } });
    fireEvent.change(within(form).getByLabelText(/Se lee como/), { target: { value: 'pro' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Agregar alias' })); });
    expect(within(section).getByRole('status')).toHaveTextContent('Alias «Prx» agregado.');
    await waitFor(() => expect(within(form).getByLabelText('Palabra del proveedor')).toHaveFocus());
    expect(within(section).getByRole('status')).not.toHaveFocus();
  });

  it('L5. el resolutor usa los nombres de línea de la empresa', async () => {
    answers['GET /admin/catalog/settings/'] = () => reply({ new_line_label: 'De caja', used_line_label: 'Reacondicionados' });
    answers['POST /admin/catalog/resolve/'] = () => reply({ ...RESOLVED, rows: [{ ...RESOLVED.rows[0], line: 'used' }] });
    const panel = await mountResolver();
    const line = within(panel).getByLabelText('Línea') as HTMLSelectElement;
    await waitFor(() => expect(Array.from(line.options).map((o) => o.textContent)).toEqual(
      ['Sin indicar (De caja)', 'De caja', 'Reacondicionados']));
    fireEvent.change(within(panel).getByLabelText('Modelo'), { target: { value: 'EqZ' } });
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Probar' })); });
    expect(within(panel).getByRole('region', { name: 'Resultado' })).toHaveTextContent('LíneaReacondicionados');
  });

  it('L5. sin los nombres de la empresa, usa los de la plataforma', async () => {
    answers['GET /admin/catalog/settings/'] = () => reply({}, 500);
    const panel = await mountResolver();
    const line = within(panel).getByLabelText('Línea') as HTMLSelectElement;
    await waitFor(() => expect(Array.from(line.options).map((o) => o.textContent)).toEqual(
      ['Sin indicar (Nuevos)', 'Nuevos', 'Seminuevos']));
  });

  it('R2-2. si la fila editada desaparece de una búsqueda, el foco va al buscador, nunca a <body>; «Ver todos» espera la escritura', async () => {
    const section = await mountSection();
    fireEvent.click(tab(section, /Opciones/));
    let filtered = false;
    answers['GET /admin/catalog/aliases/'] = () => reply(filtered
      ? { results: [], count: 0 }
      : { results: ALIASES.filter((a) => a.alias === 'Black'), count: 1 });
    const search = within(section).getByLabelText(/Buscar alias/);
    fireEvent.change(search, { target: { value: 'black' } });
    await act(async () => { fireEvent.click(within(section).getByRole('button', { name: 'Buscar' })); });
    await within(section).findByRole('button', { name: 'Ver todos' });
    fireEvent.click(within(rowOf(section, 'Black')).getByRole('button', { name: 'Editar alias Black' }));
    const form = within(section).getByRole('form', { name: 'Editar alias «Black»' });
    fireEvent.change(within(form).getByLabelText('Texto del proveedor'), { target: { value: 'Jet' } });
    const pending = deferred();
    answers['PATCH /admin/catalog/aliases/3/'] = () => pending.promise;
    act(() => { fireEvent.click(within(form).getByRole('button', { name: 'Guardar alias' })); });
    expect(within(section).getByRole('button', { name: 'Ver todos' })).toBeDisabled();
    filtered = true;
    await act(async () => { pending.release(reply({ ...ALIASES[2], alias: 'Jet', alias_key: 'jet' })); });
    await waitFor(() => expect(within(section).queryByRole('listitem', { name: 'Alias Black' })).toBeNull());
    await waitFor(() => expect(search).toHaveFocus());
    expect(document.body).not.toHaveFocus();
  });
});

