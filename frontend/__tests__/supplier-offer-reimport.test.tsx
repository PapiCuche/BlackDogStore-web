import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { InventoryTextPreview } from '@/app/admin/components/InventoryTextPreview';
import { SupplierOffersManager } from '@/app/admin/components/SupplierOffersManager';
import {
  applyWeeklyImport, declarationLabel, messageDateProblem, previewWeeklyImport, validityFromMessage,
  type WeeklyImportRow,
} from '@/app/admin/lib/supplier-import-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * B0 — reimportar una lista de proveedor sin riesgos (docs/adr-catalog-commercial.md §7): la fecha del mensaje
 * (obligatoria, de ella sale la vigencia), la lista ya cargada (quién, cuándo; un motivo para registrarla otra vez),
 * la clasificación de cada fila y las decisiones que el servidor exige antes de aplicar.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const BEFORE = {
  commercial_price: '110.00', confirmed_quantity: 5, committed_quantity: 1, valid_from: '2026-10-19',
  valid_until: '2026-10-25', lead_time_days: 4, is_active: true, available: 4,
};

const row = (row_number: number, action: string, data: Record<string, unknown>, warnings: string[] = []) => ({
  row_number, action, errors: [], warnings,
  data: {
    supplier: 'Distribuidora Andina', product_name: `Cargador ${row_number}`, commercial_price: '110.00',
    confirmed_quantity: 5, valid_from: '2026-10-19', valid_until: '2026-10-25', lead_time_days: 4, offer_id: 30 + row_number,
    before: BEFORE, changes: [], ...data,
  },
});

const SUMMARY = { new: 0, updated: 0, unchanged: 0, sold_out: 0, errors: 0, skipped: 0, price_changes: 0, mappings: 0 };

function previewOf(rows: unknown[], extra: Record<string, unknown> = {}) {
  return {
    id: 61, status: 'previewed', rows_total: rows.length, rows_create: 0, rows_update: rows.length, rows_no_change: 0,
    rows_error: 0, cost_column_ignored: false, message_date: '2026-11-02', summary: SUMMARY, rows, ...extra,
  };
}

type Call = { path: string; body: unknown };
let writes: Call[];
let preview: unknown;
let applyAnswer: () => Response;

function routeManager() {
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      writes.push({ path: url.replace(/^.*\/api/, ''), body: init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}')) });
      if (url.includes('/import/preview/')) return reply(preview);
      if (url.includes('/import/61/apply/')) return applyAnswer();
      return reply({}, 404);
    }
    if (url.includes('/admin/suppliers/')) return reply({ count: 0, page: 1, page_size: 25, results: [] });
    if (url.includes('/admin/supplier-offers/')) return reply({ count: 0, page: 1, page_size: 25, results: [] });
    return reply({}, 404);
  });
}

beforeEach(() => {
  writes = [];
  send.mockReset();
  applyAnswer = () => reply({ status: 'applied', created: 0, updated: 1, unchanged: 1, repeated: 1 });
  routeManager();
});

const applied = () => writes.filter((call) => call.path.includes('/apply/'));

async function openImporter() {
  render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
  await screen.findByText('No hay ofertas que coincidan.');
  fireEvent.change(screen.getByLabelText('Archivo de ofertas (.xlsx)'), { target: { files: [new File(['x'], 'ofertas.xlsx')] } });
}

async function upload(date = '2026-11-02') {
  await openImporter();
  fireEvent.change(screen.getByLabelText('Fecha del mensaje del proveedor'), { target: { value: date } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
  return screen.findByTestId('supplier-import-preview');
}

describe('la fecha del mensaje', () => {
  it('la vigencia por defecto sale de la fecha del mensaje, nunca de hoy', () => {
    expect(validityFromMessage('2026-11-02')).toEqual({ validFrom: '2026-11-02', validUntil: '2026-11-08' });
    expect(validityFromMessage('2026-12-29')).toEqual({ validFrom: '2026-12-29', validUntil: '2027-01-04' });
    expect(validityFromMessage('')).toBeNull();
    expect(validityFromMessage('2026-02-30')).toBeNull();
  });

  it('es obligatoria y no puede ser posterior a hoy', () => {
    const today = new Date(2026, 10, 10);
    expect(messageDateProblem('', today)).toMatch(/fecha del mensaje/);
    expect(messageDateProblem('2026-11-11', today)).toMatch(/posterior a hoy/);
    expect(messageDateProblem('2026-11-10', today)).toBeNull();
  });

  it('sin fecha no se previsualiza; con ella viaja al servidor', async () => {
    preview = previewOf([]);
    await openImporter();
    expect(screen.getByRole('button', { name: 'Previsualizar' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Fecha del mensaje del proveedor'), { target: { value: '2026-11-02' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
    const form = writes[0].body as FormData;
    expect(form.get('message_date')).toBe('2026-11-02');
  });

  it('el cliente de la API manda la fecha y las decisiones de B0', async () => {
    send.mockResolvedValue(reply({ id: 1, rows: [] }));
    await previewWeeklyImport(new File(['x'], 'ofertas.xlsx'), 7, { messageDate: '2026-11-01' });
    expect((send.mock.calls[0][1]?.body as FormData).get('message_date')).toBe('2026-11-01');
    await applyWeeklyImport(9, null, 7, {
      confirmReapply: true, reapplyReason: '  reenviada  ', confirmReactivations: 2,
      quantityDecisions: { 31: 'keep', 32: 'clear' }, confirmNewDeclaration: true, newDeclarationNote: ' mismo mensaje ' });
    expect(JSON.parse(String(send.mock.calls[1][1]?.body))).toEqual({
      confirm_reapply: true, reapply_reason: 'reenviada', confirm_reactivations: 2,
      quantity_decisions: { 31: 'keep', 32: 'clear' }, confirm_new_declaration: true, new_declaration_note: 'mismo mensaje' });
  });
});

describe('clasificación de cada fila', () => {
  it('dice si es una declaración nueva, una repetición, un mensaje anterior o una decisión pendiente', () => {
    const label = (data: Record<string, unknown>, action = 'update') =>
      declarationLabel(row(2, action, data) as unknown as WeeklyImportRow)?.text;
    expect(label({ declaration: 'new' })).toBe('Declaración nueva');
    expect(label({ declaration: 'repetition' }, 'no_change')).toBe('Repetición: no cambia nada');
    expect(label({ declaration: 'repetition', changes: ['sale_mode'] })).toMatch(/sólo cambia «Activa» \/ «Bajo pedido»/);
    expect(label({ declaration: 'older' }, 'no_change')).toBe('Mensaje anterior al registrado');
    expect(label({ declaration: 'new', reactivation_requires_review: true })).toMatch(/Reactivación/);
    expect(label({ declaration: 'new', quantity_cleared: true })).toMatch(/Cantidad vacía/);
    expect(label({}, 'error')).toBeUndefined();
  });

  it('las repeticiones se ven en la tabla aunque no cambien nada, con su explicación', async () => {
    preview = previewOf([
      row(2, 'update', { declaration: 'new', available: 3, sold_since_message: 1, changes: ['available'] },
        ['Desde el 02/11/2026 (día del mensaje) se vendieron 1 de este proveedor y variante: se descuentan de las 4 que declaró. Quedan 3 para vender.']),
      row(3, 'no_change', { declaration: 'repetition', available: 4 },
        ['Repite la declaración ya registrada (mensaje del 02/11/2026): no cambia nada. Lo vendido desde entonces sigue descontado (quedan 4) y la vigencia no se renueva.']),
    ]);
    const shown = await upload();
    expect(within(shown).getByTestId('declaration-2')).toHaveTextContent('Declaración nueva');
    expect(within(shown).getByTestId('declaration-3')).toHaveTextContent('Repetición: no cambia nada');
    expect(within(shown).getByText(/sigue descontado/)).toBeInTheDocument();
    expect(within(shown).getByText(/se descuentan de las 4 que declaró/)).toBeInTheDocument();
  });
});

describe('una lista ya cargada', () => {
  const CONTENT = {
    job_id: 58, applied_at: '2026-11-02T15:00:00Z', applied_by: 'ana', message_date: '2026-11-02',
    detail: 'Esta lista ya se aplicó: es idéntica a la carga n.º 58 (mensaje del 02/11/2026), aplicada el 02/11/2026 a las 10:00 por ana.',
  };

  it('explica cuál, cuándo y quién; para registrarla otra vez pide la casilla y un motivo', async () => {
    preview = previewOf([row(2, 'no_change', { declaration: 'repetition' })], { already_applied_content: CONTENT });
    const shown = await upload();
    const group = within(shown).getByRole('group', { name: 'Lista ya aplicada' });
    expect(within(group).getByRole('alert')).toHaveTextContent('carga n.º 58');
    expect(within(group).getByRole('alert')).toHaveTextContent('por ana');
    const applyButton = within(shown).getByRole('button', { name: 'Aplicar cambios' });
    expect(applyButton).toBeDisabled();
    fireEvent.click(within(group).getByRole('checkbox', { name: /Registrarla otra vez/ }));
    expect(applyButton).toBeDisabled();
    fireEvent.change(within(group).getByLabelText('Motivo para registrar otra vez la lista'), { target: { value: 'El proveedor la reenvió' } });
    expect(applyButton).toBeEnabled();
    await act(async () => { fireEvent.click(applyButton); });
    expect(applied()[0].body).toEqual({ confirm_reapply: true, reapply_reason: 'El proveedor la reenvió' });
  });

  it('si el servidor lo descubre al aplicar (409), muestra la carga y no deja aplicar sin motivo', async () => {
    preview = previewOf([row(2, 'update', { declaration: 'new', changes: ['available'], available: 7 })]);
    applyAnswer = () => reply({ detail: CONTENT.detail, code: 'already_applied_content', already_applied_content: CONTENT }, 409);
    const shown = await upload();
    await act(async () => { fireEvent.click(within(shown).getByRole('button', { name: 'Aplicar cambios' })); });
    expect(await within(shown).findByTestId('already-applied-content')).toBeInTheDocument();
    expect(within(shown).getByRole('button', { name: 'Aplicar cambios' })).toBeDisabled();
  });

  it('las mismas filas con una fecha del mensaje posterior dicen qué volvería a venderse y piden confirmación y nota', async () => {
    preview = previewOf([row(2, 'update', { declaration: 'new', changes: ['available'], available: 3, would_restore: 2 })],
      { same_rows_applied: { job_id: 57, applied_at: '2026-10-30T15:00:00Z', applied_by: 'ana', message_date: '2026-10-30',
        restores: [{ row_number: 2, offer_id: 32, product_name: 'Cargador 2', units: 2 }],
        detail: 'Estas filas son las mismas de la carga n.º 57 (mensaje del 30/10/2026). Con la fecha de este mensaje volverían a venderse 2 unidad(es).' } });
    const shown = await upload();
    const group = within(shown).getByRole('group', { name: 'Mismas filas con otra fecha del mensaje' });
    expect(within(group).getByRole('alert')).toHaveTextContent('carga n.º 57');
    expect(within(group).getByText(/Cargador 2 · 2 vendida\(s\) desde la carga anterior volverían a venderse/)).toBeInTheDocument();
    expect(group).not.toHaveTextContent(/corrige la fecha|revisa la fecha/i);
    const applyButton = within(shown).getByRole('button', { name: 'Aplicar cambios' });
    expect(applyButton).toBeDisabled();
    fireEvent.click(within(group).getByRole('checkbox', { name: /El proveedor volvió a enviar esta misma lista el 02\/11\/2026/ }));
    expect(applyButton).toBeDisabled();
    fireEvent.change(within(group).getByLabelText('Nota sobre el nuevo mensaje del proveedor'), { target: { value: 'Mensaje de hoy' } });
    expect(applyButton).toBeEnabled();
    await act(async () => { fireEvent.click(applyButton); });
    expect(applied()[0].body).toEqual({ confirm_new_declaration: true, new_declaration_note: 'Mensaje de hoy' });
  });

  it('si el servidor lo descubre al aplicar (409 same_rows_new_date), muestra lo que volvería a venderse', async () => {
    preview = previewOf([row(2, 'update', { declaration: 'new', changes: ['available'], available: 3 })]);
    applyAnswer = () => reply({ detail: 'Estas filas son las mismas de la carga n.º 57.', code: 'same_rows_new_date',
      same_rows_applied: { job_id: 57, applied_at: null, applied_by: null, message_date: '2026-10-30',
        restores: [{ row_number: 2, offer_id: 32, product_name: 'Cargador 2', units: 1 }] } }, 409);
    const shown = await upload();
    await act(async () => { fireEvent.click(within(shown).getByRole('button', { name: 'Aplicar cambios' })); });
    expect(await within(shown).findByTestId('same-rows-review')).toHaveTextContent('1 vendida(s)');
    expect(within(shown).getByRole('button', { name: 'Aplicar cambios' })).toBeDisabled();
  });
});

describe('decisiones antes de aplicar', () => {
  it('una oferta vencida sólo se reactiva confirmando exactamente cuántas', async () => {
    preview = previewOf([
      row(2, 'update', { declaration: 'new', reactivation_requires_review: true, is_active: true, valid_until: '2026-11-08',
        changes: ['is_active', 'valid_until'], before: { ...BEFORE, is_active: false } }),
    ]);
    const shown = await upload();
    const group = within(shown).getByRole('group', { name: 'Ofertas vencidas que se reactivarían' });
    expect(within(group).getByText(/vencía el 25\/10\/2026; vigente hasta el 08\/11\/2026/)).toBeInTheDocument();
    const applyButton = within(shown).getByRole('button', { name: 'Aplicar cambios' });
    expect(applyButton).toBeDisabled();
    fireEvent.click(within(group).getByRole('checkbox', { name: 'Confirmo reactivar esta oferta vencida' }));
    await act(async () => { fireEvent.click(applyButton); });
    expect(applied()[0].body).toEqual({ confirm_reactivations: 1 });
  });

  it('cada cantidad vacía sobre una oferta confirmada pide su propia decisión', async () => {
    const cleared = { declaration: 'new', quantity_cleared: true, confirmed_quantity: null, available: null, changes: ['available'] };
    preview = previewOf([row(2, 'update', cleared), row(3, 'update', cleared)]);
    const shown = await upload();
    const group = within(shown).getByRole('group', { name: 'Filas sin cantidad sobre ofertas confirmadas' });
    expect(within(group).getAllByText(/tenía 5 confirmada/)).toHaveLength(2);
    const applyButton = within(shown).getByRole('button', { name: 'Aplicar cambios' });
    expect(applyButton).toBeDisabled();
    fireEvent.click(within(within(group).getByRole('radiogroup', { name: 'Decisión de la fila 2' }))
      .getByRole('radio', { name: 'Conservar la cantidad confirmada' }));
    expect(applyButton).toBeDisabled();
    fireEvent.click(within(within(group).getByRole('radiogroup', { name: 'Decisión de la fila 3' }))
      .getByRole('radio', { name: /sin confirmar/ }));
    await act(async () => { fireEvent.click(applyButton); });
    expect(applied()[0].body).toEqual({ quantity_decisions: { 32: 'keep', 33: 'clear' } });
  });
});

// ---------------------------------------------------------------------------
// The WhatsApp draft
// ---------------------------------------------------------------------------

const SUPPLIERS = { count: 1, page: 1, page_size: 25, results: [{ id: 3, name: 'Mayorista', is_active: true }] };
const MATCHES = { matches: {
  1: { status: 'exact', reason: '', options: [{ product_id: 7, name: 'iPhone 15 128 GB Negro', condition_line: 'new', options: [] }] },
} };
const draftAnswer = (extra: Record<string, unknown> = {}) => ({
  dry_run: true, rows_total: 1, rows_create: 0, rows_update: 0, rows_no_change: 1, rows_error: 0, cost_column_ignored: false,
  summary: { ...SUMMARY, unchanged: 1, repeated: 1 },
  rows: [{ key: '1', row_number: 2, action: 'no_change', errors: [], warnings: [], data: { declaration: 'repetition' } }],
  ...extra,
});

function routeDraft(answer: unknown) {
  send.mockImplementation(async (url) => {
    const path = String(url);
    if (path.includes('/admin/suppliers/')) return reply(SUPPLIERS);
    if (path.includes('/text-preview/match/')) return reply(MATCHES);
    if (path.includes('/text-preview/offer-draft/')) return reply(answer);
    return reply({}, 404);
  });
}

async function pasteList() {
  render(<InventoryTextPreview canSeeCosts canManageProducts canReceiveUnits companyId={5} />);
  fireEvent.change(screen.getByLabelText('Línea declarada'), { target: { value: 'new' } });
  fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: 'IPH 15 128 2300 negro' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Analizar/ })); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' })); });
  fireEvent.change(screen.getByLabelText('Precio comercial del registro 1'), { target: { value: '2999' } });
  fireEvent.change(screen.getByLabelText('Proveedor del borrador'), { target: { value: '3' } });
}

const draftCalls = () => send.mock.calls.filter(([url]) => String(url).includes('/offer-draft/'));

describe('el borrador de WhatsApp', () => {
  it('no supone ninguna fecha: la pide, y la vigencia sale de ella', async () => {
    routeDraft(draftAnswer());
    await pasteList();
    expect(screen.getByLabelText('Fecha del mensaje del borrador')).toHaveValue('');
    expect(screen.getByLabelText('Vigente desde del borrador')).toHaveValue('');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    expect(screen.getByRole('alert')).toHaveTextContent(/fecha del mensaje/);
    expect(draftCalls()).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('Fecha del mensaje del borrador'), { target: { value: '2026-11-02' } });
    expect(screen.getByLabelText('Vigente desde del borrador')).toHaveValue('2026-11-02');
    expect(screen.getByLabelText('Vigente hasta del borrador')).toHaveValue('2026-11-08');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    expect(JSON.parse(String(draftCalls()[0][1]?.body))).toMatchObject({
      message_date: '2026-11-02', valid_from: '2026-11-02', valid_until: '2026-11-08' });
  });

  it('una lista pegada otra vez se señala antes de subirla, con la carga que ya la aplicó', async () => {
    routeDraft(draftAnswer({ already_applied_content: {
      job_id: 58, applied_at: '2026-11-02T15:00:00Z', applied_by: 'ana', message_date: '2026-11-02',
      detail: 'Esta lista ya se aplicó: es idéntica a la carga n.º 58 (mensaje del 02/11/2026).' } }));
    await pasteList();
    fireEvent.change(screen.getByLabelText('Fecha del mensaje del borrador'), { target: { value: '2026-11-02' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    const summary = screen.getByRole('region', { name: 'Resumen del borrador' });
    expect(within(summary).getByTestId('draft-already-applied')).toHaveTextContent('carga n.º 58');
    expect(within(summary).getByText('Repetidas (no cambian nada): 1')).toBeInTheDocument();
  });
});
