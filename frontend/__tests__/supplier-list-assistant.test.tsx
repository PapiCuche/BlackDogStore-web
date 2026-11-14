import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { SupplierListAssistant, SupplierListAssistantScreen } from '@/app/admin/components/SupplierListAssistant';
import { fetchWithAuth } from '@/app/lib/auth';
import { trainingFixture, trainingResolveAnswer, trainingVocabulary } from './support/training-vocabulary';

/**
 * B1-1 · «Actualizar lista de proveedor»: la pantalla que convierte una lista de WhatsApp en borradores de oferta.
 *
 * Decisiones del propietario (2026-11-14) que se fijan aquí: ningún importe se clasifica solo (D-B1-1), el costo sólo
 * con `costs.view` y en la moneda de la empresa, la referencia nunca viaja y el precio comercial nunca se llena
 * (D-B1-2), sólo se guardan las configuraciones limpias que la persona elige y se muestran antes (D-B1-3), y crear una
 * variante o un alias es una acción aparte, confirmada y con `products.manage` (D-B1-4). El texto no se guarda.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body, blob: async () => new Blob(['x']),
    headers: new Headers({ 'Content-Disposition': 'attachment; filename="borrador.xlsx"' }) }) as unknown as Response;

const FIXTURE = trainingFixture();
const LIST = FIXTURE.lines.map((line) => line.text).join('\n');
const SUPPLIERS = { count: 1, page: 1, page_size: 25, results: [{ id: 3, name: 'Mayorista', is_active: true }] };
const COMPANY = { company: { id: 5, name: 'Empresa', legal_name: '', tax_id: '', slug: 'e', is_active: true }, settings: { currency: 'PEN' } };

type Calls = { resolve: unknown[]; drafts: unknown[]; variants: { url: string; body: unknown }[]; aliases: unknown[] };
let calls: Calls;
let drafts: Map<string, number>;
let dryRun: (body: { rows: { key: string }[] }) => unknown;
let vocabulary: () => unknown;
let resolveFails: () => boolean;
let variantReply: () => Response;
let supplierPages: Record<number, unknown>;
let company: unknown;
let resolveGate: Promise<void> | null;

function route() {
  send.mockImplementation(async (url, init) => {
    const path = String(url);
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (path.includes('/admin/catalog/vocabulary/')) return reply(200, vocabulary());
    if (path.includes('/admin/suppliers/')) {
      const page = Number(new URL(path, 'http://x').searchParams.get('page') ?? '1');
      return reply(200, supplierPages[page] ?? SUPPLIERS);
    }
    if (path.includes('/admin/company-settings/')) return reply(200, company);
    if (path.includes('/admin/catalog/resolve/')) {
      calls.resolve.push(body);
      if (resolveGate) { const gate = resolveGate; resolveGate = null; await gate; }
      if (resolveFails()) return reply(500, {});
      return reply(200, trainingResolveAnswer(body.rows, { drafts }));
    }
    if (/\/admin\/catalog\/models\/\d+\/variants\//.test(path)) {
      calls.variants.push({ url: path, body });
      const answer = variantReply();
      if (answer.ok) drafts.set('3:2', 9001);
      return answer;
    }
    if (path.includes('/admin/catalog/aliases/')) {
      calls.aliases.push(body);
      if (body.scope === 'model_word') vocabulary = () => trainingVocabulary();
      return reply(201, { id: 77, scope: body.scope, alias: body.alias });
    }
    if (path.includes('/text-preview/offer-draft/file/')) return reply(200, {});
    if (path.includes('/text-preview/offer-draft/')) {
      calls.drafts.push(body);
      return reply(200, dryRun(body));
    }
    return reply(404, {});
  });
}

const cleanDryRun = (body: { rows: { key: string }[] }) => ({
  dry_run: true, cost_column_ignored: false,
  summary: { new: body.rows.length, updated: 0, unchanged: 0, sold_out: 0, errors: 0, skipped: 0, price_changes: 0, mappings: 0 },
  rows: body.rows.map((row, index) => ({ key: row.key, row_number: index + 2, action: 'create', errors: [], warnings: [], data: {} })),
});

beforeEach(() => {
  send.mockReset();
  calls = { resolve: [], drafts: [], variants: [], aliases: [] };
  drafts = new Map();
  dryRun = cleanDryRun;
  vocabulary = () => trainingVocabulary();
  resolveFails = () => false;
  variantReply = () => reply(201, { id: 9001, name: 'iPhone 17 Pro Max borrador', draft_pending: true });
  supplierPages = {};
  company = COMPANY;
  resolveGate = null;
  route();
});

async function open({ manage = true, costs = true, company = true } = {}) {
  await act(async () => {
    render(<SupplierListAssistant canManage={manage} canSeeCosts={costs} canViewCompany={company} companyId={5} />);
  });
}

async function readList(text = LIST) {
  fireEvent.change(screen.getByLabelText('Texto de la lista del proveedor'), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
}

const box = () => screen.getByLabelText('Texto de la lista del proveedor') as HTMLTextAreaElement;
/** A paste: the event (the box's selection decides whether it replaces everything), then the new value. */
const paste = (text: string) => {
  fireEvent.paste(box(), { clipboardData: { getData: () => text } });
  fireEvent.change(box(), { target: { value: text } });
};
const line = (n: number) => screen.getByRole('article', { name: `Línea ${n}` });
const config = (n: number, c: number) => screen.getByRole('article', { name: `Configuración ${c} de la línea ${n}` });
const amount = (n: number, k = 1) => screen.getByRole('group', { name: `Importe ${k} de la línea ${n}` });
const choose = (n: number, kind: string, k = 1) => fireEvent.click(within(amount(n, k)).getByRole('radio', { name: kind }));
const price = (n: number, c: number) => screen.getByLabelText(`Precio comercial · línea ${n}, configuración ${c}`);
const quantity = (n: number, c: number) => screen.getByLabelText(`Cantidad confirmada · línea ${n}, configuración ${c}`);
const pick = (n: number, c: number) => screen.getByRole('checkbox', { name: `Incluir en el guardado · línea ${n}, configuración ${c}` });

function setList({ currency = 'PEN', date = '2026-11-10', lead = '3' } = {}) {
  fireEvent.change(screen.getByLabelText('Proveedor de la lista'), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('Plazo de entrega (días)'), { target: { value: lead } });
  fireEvent.change(screen.getByLabelText('Fecha del mensaje del proveedor'), { target: { value: date } });
  fireEvent.change(screen.getByLabelText('Moneda de la lista'), { target: { value: currency } });
}

describe('lectura', () => {
  it('las 14 líneas dan 19 configuraciones con su estado; a /resolve no viaja ningún importe', async () => {
    await open();
    await readList();
    expect(screen.getByRole('status', { name: 'Resumen de la lectura' })).toHaveTextContent(
      '14 líneas · 19 configuraciones · 2 fuera de la paleta · 8 incompleta · 4 con variante · 5 sin variante');
    for (const fixtureLine of FIXTURE.lines) {
      expect(within(line(fixtureLine.n)).getAllByRole('article')).toHaveLength(fixtureLine.candidates.length);
    }
    const sent = JSON.stringify(calls.resolve);
    for (const fixtureLine of FIXTURE.lines) expect(sent).not.toContain(String(fixtureLine.amount));
    // What was read, per line: the model words, the options and the colour words as written.
    expect(line(3)).toHaveTextContent('iph 17 pro max');
    expect(line(3)).toHaveTextContent('blanco azul naranja');
    expect(line(3)).toHaveTextContent('Fuera de la paleta');
  });

  it('nada se clasifica, se confirma ni se llena solo', async () => {
    await open();
    await readList();
    for (const radio of screen.getAllByRole('radio')) expect(radio).not.toBeChecked();
    for (const fixtureLine of FIXTURE.lines) {
      fixtureLine.candidates.forEach((_, index) => {
        expect(price(fixtureLine.n, index + 1)).toHaveValue('');
        expect(quantity(fixtureLine.n, index + 1)).toHaveValue('');
      });
    }
    for (const box of screen.getAllByRole('checkbox', { name: /^Incluir en el guardado/ })) expect(box).not.toBeChecked();
  });

  it('una línea con datos personales: se ocultan y no viajan', async () => {
    await open();
    await readList('iph 18 pro esim LLA 256gb 4650 borgoña llamar 987654321 Carlos');
    const sent = JSON.stringify(calls.resolve);
    for (const secret of ['987654321', 'Carlos', '4650']) expect(sent).not.toContain(secret);
    // Only the text box (the person's own paste, in memory) still holds it.
    expect(line(1)).not.toHaveTextContent(/987654321|Carlos|llamar/);
    expect(screen.getByText(/Se ocultaron datos/)).toBeInTheDocument();
  });

  it('el texto pegado no se guarda en el navegador ni se escribe en la consola', async () => {
    const stored = jest.spyOn(Storage.prototype, 'setItem');
    const logged = jest.spyOn(console, 'log').mockImplementation(() => undefined);
    await open();
    await readList();
    expect(stored).not.toHaveBeenCalled();
    expect(logged).not.toHaveBeenCalled();
    stored.mockRestore();
    logged.mockRestore();
  });
});

describe('importes (D-B1-1, D-B1-2)', () => {
  it('sin moneda de la lista no se puede clasificar; la de la empresa se sugiere', async () => {
    await open();
    await readList();
    for (const radio of within(amount(1)).getAllByRole('radio')) expect(radio).toBeDisabled();
    expect(amount(1)).toHaveTextContent('Elige primero la moneda de la lista');
    expect(screen.getByText(/Sugerida: PEN/)).toBeInTheDocument();
    expect(screen.getByLabelText('Moneda de la lista')).toHaveValue('');
    fireEvent.change(screen.getByLabelText('Moneda de la lista'), { target: { value: 'PEN' } });
    for (const radio of within(amount(1)).getAllByRole('radio')) expect(radio).toBeEnabled();
  });

  it('una lista en otra moneda: sólo referencia o ignorar, nunca costo', async () => {
    await open();
    await readList();
    fireEvent.change(screen.getByLabelText('Moneda de la lista'), { target: { value: 'USD' } });
    expect(within(amount(1)).getByRole('radio', { name: 'Costo del proveedor' })).toBeDisabled();
    expect(within(amount(1)).getByRole('radio', { name: 'Referencia' })).toBeEnabled();
    expect(amount(1)).toHaveTextContent('nunca se convierte');
  });

  it('el costo viaja como agreed_cost; la referencia nunca; el precio es el que se escribe', async () => {
    await open();
    await readList();
    setList();
    choose(1, 'Costo del proveedor');
    choose(5, 'Referencia');
    expect(config(5, 1)).toHaveTextContent('Referencia del proveedor: 4650 PEN');
    expect(price(5, 1)).toHaveValue('');
    fireEvent.change(price(1, 1), { target: { value: '2599' } });
    fireEvent.change(price(5, 1), { target: { value: '5199' } });
    fireEvent.click(pick(1, 1));
    fireEvent.click(pick(5, 1));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Ensayar con el importador oficial' })); });
    expect(calls.drafts).toHaveLength(1);
    const body = calls.drafts[0] as { complete: boolean; rows: Record<string, unknown>[]; message_date: string };
    expect(body.complete).toBe(false);
    expect(body.message_date).toBe('2026-11-10');
    expect(body.rows).toEqual([
      { key: 'L1-C1', product_id: 1010, commercial_price: '2599', confirmed_quantity: null, agreed_cost: '2295' },
      { key: 'L5-C1', product_id: 1050, commercial_price: '5199', confirmed_quantity: null },
    ]);
    expect(JSON.stringify(body)).not.toContain('4650');
  });

  it('un importe ambiguo no es costo hasta que se escribe el exacto', async () => {
    await open();
    await readList('IPH 15 128gb 2,295 negro');
    setList();
    expect(amount(1)).toHaveTextContent('Separador ambiguo');
    choose(1, 'Costo del proveedor');
    fireEvent.change(price(1, 1), { target: { value: '2599' } });
    expect(pick(1, 1)).toBeDisabled();
    expect(config(1, 1)).toHaveTextContent('escribe el costo exacto');
    fireEvent.change(screen.getByLabelText('Costo exacto del importe 1 de la línea 1'), { target: { value: '2295' } });
    expect(pick(1, 1)).toBeEnabled();
  });

  it('«x3» se muestra como dicha y la cantidad sigue sin confirmar', async () => {
    await open();
    await readList('IPH 15 128gb 2295 x3 negro');
    expect(line(1)).toHaveTextContent('x3');
    expect(line(1)).toHaveTextContent('sin confirmar');
    expect(quantity(1, 1)).toHaveValue('');
  });
});

describe('sin costs.view', () => {
  it('los importes no se muestran, no se pueden marcar como costo y no viaja ningún costo', async () => {
    await open({ costs: false });
    await readList();
    setList();
    for (const fixtureLine of FIXTURE.lines) expect(line(fixtureLine.n)).not.toHaveTextContent(String(fixtureLine.amount));
    expect(within(amount(1)).queryByRole('radio', { name: 'Costo del proveedor' })).toBeNull();
    expect(amount(1)).toHaveTextContent('Importe oculto');
    choose(1, 'Ignorar');
    fireEvent.change(price(1, 1), { target: { value: '2599' } });
    fireEvent.click(pick(1, 1));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Ensayar con el importador oficial' })); });
    expect(JSON.stringify(calls.drafts)).not.toContain('agreed_cost');
    expect(screen.queryByRole('columnheader', { name: 'Costo' })).toBeNull();
  });
});

describe('selección y guardado parcial (D-B1-3)', () => {
  it('sólo una configuración limpia con variante se puede elegir; la vista previa lista exactamente lo que se envía', async () => {
    await open();
    await readList();
    setList();
    // Incomplete, outside the palette and without a variant: never selectable.
    expect(pick(2, 1)).toBeDisabled();
    expect(pick(3, 1)).toBeDisabled();
    expect(pick(3, 3)).toBeDisabled();
    expect(pick(1, 1)).toBeDisabled();
    expect(config(1, 1)).toHaveTextContent('Clasifica el importe');
    choose(1, 'Ignorar');
    expect(config(1, 1)).toHaveTextContent('Escribe el precio comercial');
    fireEvent.change(price(1, 1), { target: { value: '2599' } });
    fireEvent.change(quantity(1, 1), { target: { value: '2' } });
    fireEvent.click(pick(1, 1));
    const preview = screen.getByRole('region', { name: 'Vista previa de lo que se guardará' });
    const rows = within(preview).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('Línea 1');
    expect(rows[0]).toHaveTextContent('2599');
    expect(rows[0]).toHaveTextContent('2');
    expect(preview).toHaveTextContent('lista parcial');
  });

  it('la misma variante dos veces: elegida una, la otra queda bloqueada', async () => {
    await open();
    await readList('IPH 15 128gb 2295 negro\nIPH 15 128gb 2299 negro');
    setList();
    for (const n of [1, 2]) {
      choose(n, 'Ignorar');
      fireEvent.change(price(n, 1), { target: { value: '2599' } });
    }
    fireEvent.click(pick(1, 1));
    expect(pick(2, 1)).toBeDisabled();
    expect(config(2, 1)).toHaveTextContent('Repetida');
  });

  it('los errores del ensayo vuelven a su fila y no se ofrece continuar', async () => {
    dryRun = (body) => ({
      ...(cleanDryRun(body) as object),
      summary: { new: 0, updated: 0, unchanged: 0, sold_out: 0, errors: 1, skipped: 0, price_changes: 0, mappings: 0 },
      rows: body.rows.map((row, index) => ({ key: row.key, row_number: index + 2, action: 'error',
        errors: ['Esta oferta tiene ventas: indica la cantidad.'], warnings: [], data: {} })),
    });
    await open();
    await readList();
    setList();
    choose(5, 'Ignorar');
    fireEvent.change(price(5, 1), { target: { value: '5199' } });
    fireEvent.click(pick(5, 1));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Ensayar con el importador oficial' })); });
    expect(config(5, 1)).toHaveTextContent('Esta oferta tiene ventas: indica la cantidad.');
    const preview = screen.getByRole('region', { name: 'Vista previa de lo que se guardará' });
    expect(preview).toHaveTextContent('Esta oferta tiene ventas');
    expect(screen.queryByRole('button', { name: 'Continuar al importador oficial' })).toBeNull();
  });

  it('un ensayo limpio continúa al importador oficial como lista parcial', async () => {
    await open();
    await readList();
    setList();
    choose(5, 'Ignorar');
    fireEvent.change(price(5, 1), { target: { value: '5199' } });
    fireEvent.click(pick(5, 1));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Ensayar con el importador oficial' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Continuar al importador oficial' })); });
    const importer = screen.getByRole('region', { name: 'Importador oficial de ofertas' });
    expect(within(importer).queryByLabelText(/lista completa/i)).toBeNull();
    expect(within(importer).getByLabelText('Fecha del mensaje del proveedor')).toHaveValue('2026-11-10');
  });

  it('sin plazo de entrega no se ensaya: el importador oficial lo exige', async () => {
    await open();
    await readList();
    setList({ lead: '' });
    choose(5, 'Ignorar');
    fireEvent.change(price(5, 1), { target: { value: '5199' } });
    fireEvent.click(pick(5, 1));
    expect(screen.getByRole('button', { name: 'Ensayar con el importador oficial' })).toBeDisabled();
    expect(screen.getByText(/Indica el plazo de entrega/)).toBeInTheDocument();
    expect(calls.drafts).toHaveLength(0);
  });

  it('una fecha del mensaje posterior a hoy (Lima) no deja ensayar', async () => {
    await open();
    await readList();
    setList({ date: '2999-01-01' });
    choose(5, 'Ignorar');
    fireEvent.change(price(5, 1), { target: { value: '5199' } });
    fireEvent.click(pick(5, 1));
    expect(screen.getByRole('button', { name: 'Ensayar con el importador oficial' })).toBeDisabled();
    expect(screen.getAllByText(/posterior a hoy/).length).toBeGreaterThan(0);
  });
});

describe('acciones sugeridas (D-B1-4)', () => {
  it('«Crear variante» sólo en una combinación válida, con confirmación; nace como borrador', async () => {
    await open();
    await readList();
    expect(within(config(3, 2)).queryByRole('button', { name: /Crear variante/ })).toBeNull();
    fireEvent.click(within(config(3, 3)).getByRole('button', { name: /Crear variante/ }));
    expect(calls.variants).toHaveLength(0);
    expect(config(3, 3)).toHaveTextContent('borrador');
    await act(async () => { fireEvent.click(within(config(3, 3)).getByRole('button', { name: 'Confirmar: crear borrador' })); });
    expect(calls.variants).toHaveLength(1);
    expect(calls.variants[0].body).toEqual({ condition_line: 'new', option_ids: [11, 22, 33, 51] });
    expect(config(3, 3)).toHaveTextContent('Con variante');
    expect(config(3, 3)).toHaveTextContent('no vende hasta habilitar');
  });

  it('«Es un alias de…» registra un alias sólo tras confirmarlo', async () => {
    await open();
    await readList('iph 18 pro max japonesa esim LLA 256gb 5500 borgoña');
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Es un alias de… («japonesa»)' }));
    const form = screen.getByRole('group', { name: 'Alias para «japonesa»' });
    fireEvent.change(within(form).getByLabelText('Es un alias de'), { target: { value: `option:${trainingVocabulary().options.region[1].id}` } });
    fireEvent.click(within(form).getByRole('button', { name: 'Revisar' }));
    expect(calls.aliases).toHaveLength(0);
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Confirmar alias' })); });
    expect(calls.aliases).toEqual([{ scope: 'option', alias: 'japonesa', option_id: trainingVocabulary().options.region[1].id }]);
  });

  it('sin products.manage no hay acciones ni guardado: sólo lectura', async () => {
    await open({ manage: false });
    await readList();
    expect(screen.queryByRole('button', { name: /Crear variante/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /es un alias de/i })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /^Incluir en el guardado/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Ensayar con el importador oficial' })).toBeNull();
    expect(screen.getByText(/Guardar ofertas requiere permiso para gestionar productos/)).toBeInTheDocument();
    expect(send.mock.calls.some(([url]) => String(url).includes('/admin/suppliers/'))).toBe(false);
  });
});

describe('revisión H1: un color nunca se recorta ni se parte', () => {
  it('sin colores compuestos en la paleta, «glaciar blanco» es una duda que bloquea: no viaja «blanco»', async () => {
    vocabulary = () => trainingVocabulary({ compounds: false });
    await open();
    await readList('Iph 18 pro esim LLA 256gb 4620 glaciar blanco');
    const sent = calls.resolve[0] as { rows: Record<string, string>[] };
    expect(sent.rows[0]).not.toHaveProperty('colour_text');
    expect(line(1)).toHaveTextContent('Color con palabras sin reconocer: «glaciar blanco»');
    expect(within(line(1)).queryByRole('checkbox', { name: /Ignorar/ })).toBeNull();
    expect(within(line(1)).queryByRole('button', { name: /Crear variante/ })).toBeNull();
    expect(within(line(1)).getByRole('button', { name: 'Es un alias de… («glaciar blanco»)' })).toBeInTheDocument();
  });

  it('«Crear variante» no se ofrece mientras la línea tenga palabras sin reconocer', async () => {
    await open();
    await readList('iph 17 pro max esim oferta lla 256gb 4390 blanco azul naranja');
    expect(within(config(1, 3)).queryByRole('button', { name: /Crear variante/ })).toBeNull();
    fireEvent.click(within(line(1)).getByRole('checkbox', { name: 'Ignorar las palabras sin reconocer de la línea 1' }));
    expect(within(config(1, 3)).getByRole('button', { name: /Crear variante/ })).toBeInTheDocument();
  });
});

describe('revisión M2: una clasificación nunca pasa a otro importe', () => {
  it('tras registrar un alias que cambia la lectura, el «Ignorar» de «15» no se aplica a 2295', async () => {
    vocabulary = () => trainingVocabulary({ modelWords: false });
    await open();
    await readList('IPH 15 128gb 2295 negro');
    fireEvent.change(screen.getByLabelText('Moneda de la lista'), { target: { value: 'PEN' } });
    expect(amount(1, 1)).toHaveTextContent('15');
    choose(1, 'Ignorar', 1);
    fireEvent.click(within(line(1)).getByRole('button', { name: 'Es un alias de… («IPH»)' }));
    const form = screen.getByRole('group', { name: 'Alias para «IPH»' });
    fireEvent.change(within(form).getByLabelText('Es un alias de'), { target: { value: 'word' } });
    fireEvent.change(within(form).getByLabelText('Se lee como la palabra'), { target: { value: 'iphone' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Revisar' }));
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Confirmar alias' })); });
    expect(amount(1, 1)).toHaveTextContent('2295');
    for (const radio of within(amount(1, 1)).getAllByRole('radio')) expect(radio).not.toBeChecked();
  });
});

describe('revisión M3: escribir y releer son dos cosas', () => {
  it('si la relectura falla tras crear la variante, se dice así y no se ofrece crearla otra vez', async () => {
    await open();
    await readList();
    resolveFails = () => true;
    fireEvent.click(within(config(3, 3)).getByRole('button', { name: /Crear variante/ }));
    await act(async () => { fireEvent.click(within(config(3, 3)).getByRole('button', { name: 'Confirmar: crear borrador' })); });
    expect(calls.variants).toHaveLength(1);
    expect(screen.getByText(/La variante se creó como borrador, pero no se pudo volver a leer la lista/)).toBeInTheDocument();
    expect(screen.queryByText(/No se pudo crear la variante/)).toBeNull();
    expect(within(config(3, 3)).queryByRole('button', { name: /Crear variante/ })).toBeNull();
    resolveFails = () => false;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Volver a leer la lista' })); });
    expect(config(3, 3)).toHaveTextContent('Con variante');
  });

  it('un rechazo de /variants se explica con su propio texto', async () => {
    variantReply = () => reply(403, { detail: '' });
    await open();
    await readList();
    fireEvent.click(within(config(3, 3)).getByRole('button', { name: /Crear variante/ }));
    await act(async () => { fireEvent.click(within(config(3, 3)).getByRole('button', { name: 'Confirmar: crear borrador' })); });
    expect(screen.getByRole('alert')).toHaveTextContent('No tienes permiso para crear variantes');
  });

  it('el texto no se puede cambiar mientras una acción está en curso', async () => {
    let release: (value: Response) => void = () => undefined;
    variantReply = () => new Promise<Response>((resolve) => { release = resolve; }) as unknown as Response;
    await open();
    await readList();
    fireEvent.click(within(config(3, 3)).getByRole('button', { name: /Crear variante/ }));
    act(() => { fireEvent.click(within(config(3, 3)).getByRole('button', { name: 'Confirmar: crear borrador' })); });
    expect(screen.getByLabelText('Texto de la lista del proveedor')).toBeDisabled();
    await act(async () => { release(reply(201, { id: 9001 })); });
    expect(screen.getByLabelText('Texto de la lista del proveedor')).toBeEnabled();
  });
});

describe('revisión M4: sin costs.view no se ve ningún importe del proveedor', () => {
  it('ni etiquetado como venta, ni pegado a la moneda, ni en las palabras sin reconocer', async () => {
    await open({ costs: false });
    await readList('iph 18 pro max esim LLA 256gb precio venta 5500 borgoña\nIPH 15 128gb 4370usd negro\niph 15 128gb esim 2295x negro');
    for (const n of [1, 2, 3]) expect(line(n)).not.toHaveTextContent(/5500|4370|2295/);
    expect(amount(1)).toHaveTextContent('Importe oculto');
    expect(within(line(3)).queryByRole('button', { name: /2295/ })).toBeNull();
  });
});

describe('revisión M5 y menores', () => {
  it('se cargan todas las páginas de proveedores activos', async () => {
    const many = Array.from({ length: 25 }, (_, i) => ({ id: i + 1, name: `Proveedor ${i + 1}`, is_active: true }));
    supplierPages = {
      1: { count: 26, page: 1, page_size: 25, results: many },
      2: { count: 26, page: 2, page_size: 25, results: [{ id: 26, name: 'Proveedor 26', is_active: true }] },
    };
    await open();
    expect(within(screen.getByLabelText('Proveedor de la lista')).getAllByRole('option')).toHaveLength(27);
  });

  it('«seminuevo» viaja como línea y una línea seminueva no se guarda ni crea variantes', async () => {
    await open();
    await readList('iph 17 pro max esim lla 256gb 4390 blanco azul naranja seminuevo');
    const sent = calls.resolve[0] as { rows: Record<string, string>[] };
    expect(sent.rows[0]).toMatchObject({ line: 'seminuevo' });
    setList();
    choose(1, 'Ignorar');
    fireEvent.change(price(1, 2), { target: { value: '4999' } });
    expect(pick(1, 2)).toBeDisabled();
    expect(config(1, 2)).toHaveTextContent('no admite seminuevos');
    expect(within(config(1, 3)).queryByRole('button', { name: /Crear variante/ })).toBeNull();
  });

  it('«$2295» en una lista en soles nunca es costo: «$» es otra moneda y nunca se convierte (N1)', async () => {
    await open();
    await readList('IPH 15 128gb $2295 negro');
    fireEvent.change(screen.getByLabelText('Moneda de la lista'), { target: { value: 'PEN' } });
    expect(amount(1)).toHaveTextContent('«$»');
    expect(within(amount(1)).getByRole('radio', { name: 'Costo del proveedor' })).toBeDisabled();
    expect(within(amount(1)).getByRole('radio', { name: 'Referencia' })).toBeEnabled();
  });

  it('«$2295» en una lista en dólares de una empresa en dólares sí puede ser costo', async () => {
    company = { ...COMPANY, settings: { currency: 'USD' } };
    await open();
    await readList('IPH 15 128gb $2295 negro');
    fireEvent.change(screen.getByLabelText('Moneda de la lista'), { target: { value: 'USD' } });
    expect(within(amount(1)).getByRole('radio', { name: 'Costo del proveedor' })).toBeEnabled();
  });

  it('sin company.view el costo queda bloqueado y se explica', async () => {
    await open({ company: false });
    await readList('IPH 15 128gb 2295 negro');
    fireEvent.change(screen.getByLabelText('Moneda de la lista'), { target: { value: 'PEN' } });
    expect(within(amount(1)).getByRole('radio', { name: 'Costo del proveedor' })).toBeDisabled();
    expect(amount(1)).toHaveTextContent('permiso para ver la configuración de la empresa');
  });

  it('si hay más proveedores de los que se cargan, se dice', async () => {
    const page = (n: number) => ({ count: 600, page: n, page_size: 25,
      results: Array.from({ length: 25 }, (_, i) => ({ id: n * 100 + i, name: `P ${n}-${i}`, is_active: true })) });
    supplierPages = Object.fromEntries(Array.from({ length: 30 }, (_, i) => [i + 1, page(i + 1)]));
    await open();
    expect(screen.getByText(/Se muestran los primeros 500 de 600 proveedores/)).toBeInTheDocument();
  });

  it('los enlaces abren otra pestaña y sin products.manage el precio y la cantidad son de sólo lectura', async () => {
    await open({ manage: false });
    await readList();
    const link = within(config(1, 1)).getByRole('link', { name: /iPhone 15/ });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(price(1, 1)).toHaveAttribute('readonly');
    expect(quantity(1, 1)).toHaveAttribute('readonly');
  });
});

describe('revisión: cambiar de empresa', () => {
  it('borra la lectura, el texto y las decisiones de la empresa anterior', async () => {
    const { rerender } = render(<SupplierListAssistantScreen canManage canSeeCosts canViewCompany companyId={5} />);
    await act(async () => undefined);
    await readList();
    expect(screen.getByRole('status', { name: 'Resumen de la lectura' })).toBeInTheDocument();
    await act(async () => { rerender(<SupplierListAssistantScreen canManage canSeeCosts canViewCompany companyId={6} />); });
    expect(screen.queryByRole('status', { name: 'Resumen de la lectura' })).toBeNull();
    expect(screen.getByLabelText('Texto de la lista del proveedor')).toHaveValue('');
  });
});

describe('regla del propietario (2026-11-14): ningún color se recorta, ni eligiendo palabras ni borrándolas del texto', () => {
  it('la duda de color no ofrece quitar palabras: explica cómo resolverla y no envía ningún color', async () => {
    await open();
    await readList('IPH 15 128gb 2295 negro sellado');
    expect((calls.resolve[0] as { rows: Record<string, string>[] }).rows[0]).not.toHaveProperty('colour_text');
    const doubt = screen.getByRole('group', { name: 'Duda de color de la línea 1' });
    expect(within(doubt).queryByRole('button', { name: /Elegir qué palabras/ })).toBeNull();
    expect(within(doubt).queryByRole('checkbox')).toBeNull();
    expect(doubt).toHaveTextContent('No se quita «sellado» para dejar sólo el color: «negro sellado» podría ser el nombre comercial de un color.');
    expect(doubt).toHaveTextContent('agrégalo a la paleta del modelo');
    expect(doubt).toHaveTextContent('regístralo como alias de la empresa');
    expect(doubt).toHaveTextContent('Borrar la palabra no lo resuelve: la línea sigue bloqueada.');
    expect(within(doubt).getByRole('link', { name: /Abrir la paleta del modelo/ })).toHaveAttribute('target', '_blank');
    expect(within(doubt).getByRole('button', { name: 'Corregir el texto y volver a leer' })).toBeInTheDocument();
    expect(pick(1, 1)).toBeDisabled();
  });

  it('borrar «sellado» en el texto y volver a leer: la línea sigue bloqueada y muestra lo recibido y lo corregido', async () => {
    await open();
    await readList('IPH 15 128gb 2295 negro sellado');
    await readList('IPH 15 128gb 2295 negro');
    // The server proposes «Negro»; the line stays blocked and says how to resolve it.
    expect(config(1, 1)).toHaveTextContent('Con variante');
    const stop = screen.getByRole('group', { name: 'Corrección bloqueada de la línea 1' });
    expect(stop).toHaveTextContent('La corrección quitó palabras del color recibido «negro sellado» y dejó «negro»');
    expect(line(1)).toHaveTextContent('Texto recibido');
    expect(line(1)).toHaveTextContent('IPH 15 128gb 2295 negro sellado');
    expect(line(1)).toHaveTextContent('Corregido a mano');
    setList();
    choose(1, 'Ignorar');
    fireEvent.change(price(1, 1), { target: { value: '2599' } });
    expect(pick(1, 1)).toBeDisabled();
    expect(within(line(1)).queryByRole('button', { name: /Crear variante/ })).toBeNull();
    expect(calls.drafts).toHaveLength(0);
  });

  it('un error de escritura corregido se guarda, y la vista previa separa lo recibido, lo corregido y lo interpretado', async () => {
    await open();
    await readList('IPH 15 128gb 2295 negro\nIPH 15 128gb 2295 negroo');
    await readList('IPH 15 128gb 2295 negro\nIPH 15 128gb 2296 negro');
    expect(screen.queryByRole('group', { name: /Corrección bloqueada/ })).toBeNull();
    expect(within(line(1)).queryByText('Texto recibido')).toBeNull();
    expect(line(2)).toHaveTextContent('IPH 15 128gb 2295 negroo');
    setList();
    choose(2, 'Ignorar');
    fireEvent.change(price(2, 1), { target: { value: '2599' } });
    fireEvent.click(pick(2, 1));
    const preview = screen.getByRole('region', { name: 'Vista previa de lo que se guardará' });
    const row = within(preview).getAllByRole('row')[1];
    expect(row).toHaveTextContent('Recibido: IPH 15 128gb 2295 negroo');
    expect(row).toHaveTextContent('Corregido a mano: IPH 15 128gb 2296 negro');
    expect(row).toHaveTextContent(/Color: «negro» → negro \(por su etiqueta\)/i);
  });

  it('HIGH 2: lo recibido es lo PEGADO; borrar «sellado» antes de la primera lectura también bloquea', async () => {
    await open();
    paste('IPH 15 128gb 2295 negro sellado');
    fireEvent.change(box(), { target: { value: 'IPH 15 128gb 2295 negro' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
    expect(screen.getByRole('group', { name: 'Corrección bloqueada de la línea 1' })).toBeInTheDocument();
    expect(line(1)).toHaveTextContent('IPH 15 128gb 2295 negro sellado');
  });

  it('M3: vaciar el cuadro o pegar encima una copia corregida no cuenta como lista nueva', async () => {
    await open();
    paste('IPH 15 128gb 2295 negro sellado\nIPH 15 256gb 2600 azul');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
    fireEvent.change(box(), { target: { value: '' } });
    paste('IPH 15 128gb 2295 negro\nIPH 15 256gb 2600 azul');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
    expect(screen.getByRole('group', { name: 'Corrección bloqueada de la línea 1' })).toBeInTheDocument();
    // Pasting over everything an edited copy: the same.
    box().setSelectionRange(0, box().value.length);
    paste('IPH 15 128gb 2295 negro \nIPH 15 256gb 2600 azul');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
    expect(screen.getByRole('group', { name: 'Corrección bloqueada de la línea 1' })).toBeInTheDocument();
  });

  it('N2: pegar encima otra lista también es una corrección (se avisa); sólo «Empezar otra lista» empieza de cero', async () => {
    await open();
    paste('IPH 15 128gb 2295 negro sellado');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
    box().setSelectionRange(0, box().value.length);
    paste('IPH 15 128gb 2310 negro');
    expect(screen.getByText(/Si es otra lista, pulsa «Empezar otra lista»/)).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
    expect(screen.getByRole('group', { name: 'Corrección bloqueada de la línea 1' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Empezar otra lista/ }));
    expect(box()).toHaveValue('');
    paste('IPH 15 128gb 2310 negro');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
    expect(screen.queryByRole('group', { name: /Corrección bloqueada/ })).toBeNull();
    expect(screen.queryByText('Texto recibido')).toBeNull();
  });

  it('N3: un pegado que no cambia nada no convierte la siguiente edición en lista nueva', async () => {
    await open();
    paste('Iph 18 pro esim LLA 256gb 4620 glaciar blanco');
    // The same text pasted over itself: no change event.
    box().setSelectionRange(0, box().value.length);
    fireEvent.paste(box(), { clipboardData: { getData: () => box().value } });
    fireEvent.change(box(), { target: { value: 'Iph 18 pro esim LLA 256gb 4620 blanco' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
    expect(screen.getByRole('group', { name: 'Corrección bloqueada de la línea 1' })).toBeInTheDocument();
  });

  it('M6: cada lectura usa el catálogo de ahora (la paleta cambiada en otra pestaña)', async () => {
    vocabulary = () => trainingVocabulary({ compounds: false });
    await open();
    await readList('Iph 18 pro esim LLA 256gb 4620 glaciar blanco');
    expect(screen.getByRole('group', { name: 'Duda de color de la línea 1' })).toBeInTheDocument();
    vocabulary = () => trainingVocabulary();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Leer la lista' })); });
    expect(screen.queryByRole('group', { name: 'Duda de color de la línea 1' })).toBeNull();
    expect((calls.resolve[1] as { rows: Record<string, string>[] }).rows[0]).toMatchObject({ colour_text: 'glaciar blanco' });
  });

  it('sin costs.view, la vista previa de una línea corregida no muestra importes', async () => {
    await open({ costs: false });
    await readList('IPH 15 128gb 2295 negroo');
    await readList('IPH 15 128gb 2296 negro');
    setList();
    choose(1, 'Ignorar');
    fireEvent.change(price(1, 1), { target: { value: '2599' } });
    fireEvent.click(pick(1, 1));
    const row = within(screen.getByRole('region', { name: 'Vista previa de lo que se guardará' })).getAllByRole('row')[1];
    expect(row).toHaveTextContent('Recibido:');
    expect(row).toHaveTextContent('Corregido a mano:');
    expect(row).not.toHaveTextContent('2295');
    expect(row).not.toHaveTextContent('2296');
  });

  it('sin costs.view, lo recibido de una línea corregida tampoco muestra importes', async () => {
    await open({ costs: false });
    await readList('IPH 15 128gb 2295 negro sellado');
    await readList('IPH 15 128gb 2296 negro');
    expect(line(1)).toHaveTextContent('Texto recibido');
    expect(line(1)).not.toHaveTextContent('2295');
    expect(line(1)).not.toHaveTextContent('2296');
  });

  it('sin costs.view las líneas sin oferta no muestran cifras', async () => {
    await open({ costs: false });
    await readList('Hola, precios 23k\nIPH 15 128gb 2295 negro');
    expect(screen.getByText(/líneas sin oferta/).closest('details')).not.toHaveTextContent('23k');
  });
});
