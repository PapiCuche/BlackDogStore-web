import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { SupplierListAssistant } from '@/app/admin/components/SupplierListAssistant';
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

function route() {
  send.mockImplementation(async (url, init) => {
    const path = String(url);
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (path.includes('/admin/catalog/vocabulary/')) return reply(200, trainingVocabulary());
    if (path.includes('/admin/suppliers/')) return reply(200, SUPPLIERS);
    if (path.includes('/admin/company-settings/')) return reply(200, COMPANY);
    if (path.includes('/admin/catalog/resolve/')) {
      calls.resolve.push(body);
      return reply(200, trainingResolveAnswer(body.rows, { drafts }));
    }
    if (/\/admin\/catalog\/models\/\d+\/variants\//.test(path)) {
      calls.variants.push({ url: path, body });
      drafts.set('3:2', 9001);
      return reply(201, { id: 9001, name: 'iPhone 17 Pro Max borrador', draft_pending: true });
    }
    if (path.includes('/admin/catalog/aliases/')) {
      calls.aliases.push(body);
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

const line = (n: number) => screen.getByRole('article', { name: `Línea ${n}` });
const config = (n: number, c: number) => screen.getByRole('article', { name: `Configuración ${c} de la línea ${n}` });
const amount = (n: number, k = 1) => screen.getByRole('group', { name: `Importe ${k} de la línea ${n}` });
const choose = (n: number, kind: string, k = 1) => fireEvent.click(within(amount(n, k)).getByRole('radio', { name: kind }));
const price = (n: number, c: number) => screen.getByLabelText(`Precio comercial · línea ${n}, configuración ${c}`);
const quantity = (n: number, c: number) => screen.getByLabelText(`Cantidad confirmada · línea ${n}, configuración ${c}`);
const pick = (n: number, c: number) => screen.getByRole('checkbox', { name: `Guardar · línea ${n}, configuración ${c}` });

function setList({ currency = 'PEN', date = '2026-11-10' } = {}) {
  fireEvent.change(screen.getByLabelText('Proveedor de la lista'), { target: { value: '3' } });
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
    for (const box of screen.getAllByRole('checkbox', { name: /^Guardar/ })) expect(box).not.toBeChecked();
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
    fireEvent.click(within(line(1)).getByRole('button', { name: '«japonesa» es un alias de…' }));
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
    expect(screen.queryByRole('checkbox', { name: /^Guardar/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Ensayar con el importador oficial' })).toBeNull();
    expect(screen.getByText(/Guardar ofertas requiere permiso para gestionar productos/)).toBeInTheDocument();
    expect(send.mock.calls.some(([url]) => String(url).includes('/admin/suppliers/'))).toBe(false);
  });
});
