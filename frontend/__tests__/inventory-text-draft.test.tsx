import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { InventoryTextPreview } from '@/app/admin/components/InventoryTextPreview';
import {
  UNIT_TEMPLATE_HEADERS, offerDraftPayload, planDraft, unitDraftTsv, type DraftCandidate,
} from '@/app/admin/lib/inventory-text-draft';
import type { TextCandidate } from '@/app/admin/lib/inventory-text-preview';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-TEXT-03 · de la lista revisada a un BORRADOR de la importación oficial. Las ofertas se ensayan en el
 * servidor con el importador oficial (nada queda escrito); los equipos propios se arman en la pestaña y sus
 * IMEI y series no viajan a ningún servidor. No hay botón de aplicar.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers() }) as Response;

const IMEI = '490154203237518';

function candidate(change: Partial<DraftCandidate> = {}): DraftCandidate {
  const base: TextCandidate = {
    id: 1, source: 'iPhone 15 128 negro 2300', kind: 'device', line: 'new', origin: 'supplier', model: 'iPhone 15',
    capacityGb: 128, ramGb: null, batteryPercent: null, colour: 'negro', sim: '', region: '', watts: null, length: '',
    generation: '', serial: '', imei: '', imei2: '', supplierCost: '2300', quantity: null, duplicateOf: null, warnings: [],
  };
  return { ...base, productId: 7, productName: 'iPhone 15 128 GB Negro', commercialPrice: '2999', confirmedQuantity: null, ...change };
}

const SUPPLIERS = { count: 1, page: 1, page_size: 25, results: [{ id: 3, name: 'Mayorista', is_active: true }] };
const MATCHES = { matches: {
  1: { status: 'exact', reason: '', options: [{ product_id: 7, name: 'iPhone 15 128 GB Negro', condition_line: 'new', options: [] }] },
  2: { status: 'exact', reason: '', options: [{ product_id: 8, name: 'iPhone 15 256 GB Azul', condition_line: 'new', options: [] }] },
} };
const DRAFT = {
  dry_run: true, rows_total: 2, rows_create: 1, rows_update: 1, rows_no_change: 0, rows_error: 0, cost_column_ignored: false,
  summary: { new: 1, updated: 0, unchanged: 0, sold_out: 1, errors: 0, skipped: 0, price_changes: 1, mappings: 0 },
  rows: [
    { key: '1', row_number: 2, action: 'create', errors: [], warnings: [], data: {} },
    { key: '2', row_number: 3, action: 'update', errors: [], warnings: [], data: { price_change: { before: '3100.00', after: '3200.00' } } },
  ],
  snapshot: { supplier_id: 3, supplier_name: 'Mayorista', deactivate_count: 2, with_commitments: 0, looks_truncated: false, absent: [] },
};

function route(draft: unknown = DRAFT) {
  send.mockImplementation(async (url) => {
    const path = String(url);
    if (path.includes('/admin/suppliers/')) return reply(200, SUPPLIERS);
    if (path.includes('/text-preview/match/')) return reply(200, MATCHES);
    if (path.includes('/text-preview/offer-draft/')) return reply(200, draft);
    return reply(404, {});
  });
}

beforeEach(() => send.mockReset());

async function analyze(text: string, { origin = 'supplier', manage = true, units = true, costs = true } = {}) {
  render(<InventoryTextPreview canSeeCosts={costs} canManageProducts={manage} canReceiveUnits={units} companyId={5} />);
  fireEvent.change(screen.getByLabelText('Línea declarada'), { target: { value: 'new' } });
  fireEvent.change(screen.getByLabelText('Origen declarado'), { target: { value: origin } });
  fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Analizar/ })); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar en el catálogo' })); });
}

const draftCalls = () => send.mock.calls.filter(([url]) => String(url).includes('/offer-draft/'));

describe('plan del borrador', () => {
  it('ofertas: los duplicados no viajan, un seminuevo de proveedor nunca, y lo que falta se revisa', () => {
    const plan = planDraft([
      candidate({ id: 1 }),
      candidate({ id: 2, duplicateOf: 1 }),
      candidate({ id: 3, line: 'used' }),
      candidate({ id: 4, productId: null }),
      candidate({ id: 5, commercialPrice: '' }),
      candidate({ id: 6, confirmedQuantity: 0 }),
    ], 'supplier');
    expect(plan.included.map(row => row.id)).toEqual([1, 6]);
    expect(plan.duplicates).toEqual([2]);
    expect(plan.unknownQuantities).toBe(1);
    const reasons = Object.fromEntries(plan.review.map(item => [item.id, item.reason]));
    expect(reasons[2]).toMatch(/duplicado/i);
    expect(reasons[3]).toMatch(/seminuevo de proveedor/i);
    expect(reasons[4]).toMatch(/variante/i);
    expect(reasons[5]).toMatch(/precio comercial/i);
  });

  it('equipos propios: serie obligatoria, IMEI válido y los accesorios van por cantidad', () => {
    const own = (change: Partial<DraftCandidate>) => candidate({ origin: 'own', serial: 'F2LX0001', imei: IMEI, ...change });
    const plan = planDraft([
      own({ id: 1 }), own({ id: 2, serial: '' }), own({ id: 3, imei: '490154203237519' }),
      own({ id: 4, kind: 'accessory', line: 'accessory' }), own({ id: 5, imei2: IMEI }),
    ], 'own');
    expect(plan.included.map(row => row.id)).toEqual([1]);
    const reasons = Object.fromEntries(plan.review.map(item => [item.id, item.reason]));
    expect(reasons[2]).toMatch(/número de serie/i);
    expect(reasons[3]).toMatch(/IMEI/);
    expect(reasons[4]).toMatch(/Carga de stock/);
    expect(reasons[5]).toMatch(/IMEI 2/);
  });

  it('al servidor sólo viajan los campos de la plantilla de ofertas: nunca texto, IMEI ni serie', () => {
    const plan = planDraft([candidate({ serial: 'ABC12345', imei: IMEI, confirmedQuantity: 3 })], 'supplier');
    const settings = { supplierId: 3, validFrom: '2026-10-22', validUntil: '2026-10-29', leadTimeDays: '4', complete: false };
    expect(offerDraftPayload(plan, settings, true)).toEqual({
      supplier_id: 3, valid_from: '2026-10-22', valid_until: '2026-10-29', lead_time_days: '4', complete: false,
      rows: [{ key: '1', product_id: 7, commercial_price: '2999', confirmed_quantity: 3, agreed_cost: '2300' }],
    });
    const withoutCosts = JSON.stringify(offerDraftPayload(plan, settings, false));
    expect(withoutCosts).not.toContain('agreed_cost');
    for (const secret of [IMEI, 'ABC12345', 'iPhone 15 128 negro']) expect(withoutCosts).not.toContain(secret);
  });

  it('el borrador de equipos usa la plantilla oficial, sin precio de venta y sin costo para quien no lo ve', () => {
    const plan = planDraft([candidate({ origin: 'own', serial: 'F2LX0001', imei: IMEI, batteryPercent: 91, line: 'used' })], 'own');
    const tsv = unitDraftTsv(plan, { branch: 'Principal', reason: 'Compra 12' }, false);
    const [header, row] = tsv.split('\n').map(line => line.split('\t'));
    expect(header).toEqual(UNIT_TEMPLATE_HEADERS);
    const cell = (name: string) => row[UNIT_TEMPLATE_HEADERS.indexOf(name)];
    expect([cell('Producto'), cell('Sucursal'), cell('Número de serie'), cell('IMEI'), cell('Línea'), cell('Batería %')])
      .toEqual(['iPhone 15 128 GB Negro', 'Principal', 'F2LX0001', IMEI, 'Seminuevo', '91']);
    expect([cell('Costo'), cell('Precio de venta')]).toEqual(['', '']);
    expect(unitDraftTsv(plan, { branch: '=HYPERLINK("x")', reason: '' }, true)).toContain("'=HYPERLINK");
  });
});

describe('flujo en pantalla', () => {
  it('ofertas: genera el borrador con el ensayo oficial y muestra el resumen por operación', async () => {
    route();
    await analyze('IPH 15 128 2300 negro\nIPH 15 256 2500 azul');
    fireEvent.change(screen.getByLabelText('Precio comercial del registro 1'), { target: { value: '2999' } });
    fireEvent.change(screen.getByLabelText('Precio comercial del registro 2'), { target: { value: '3200' } });
    fireEvent.change(screen.getByLabelText('Cantidad del registro 2'), { target: { value: '0' } });
    fireEvent.change(screen.getByLabelText('Proveedor del borrador'), { target: { value: '3' } });
    fireEvent.click(screen.getByLabelText(/lista completa/i));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });

    const [url, init] = draftCalls()[0];
    expect(String(url)).toContain('/admin/inventory/text-preview/offer-draft/?company=5');
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({ supplier_id: 3, complete: true });
    expect(body.rows.map((row: { product_id: number }) => row.product_id)).toEqual([7, 8]);
    const summary = screen.getByRole('region', { name: 'Resumen del borrador' });
    for (const text of ['Nuevas ofertas: 1', 'Agotadas: 1', 'Cambios de precio: 1', 'Posibles desactivaciones: 2',
      'Cantidades sin confirmar: 1']) expect(within(summary).getByText(text)).toBeInTheDocument();
    expect(within(summary).getByText(/no se escribió nada/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Aplicar|Guardar|Importar/ })).toBeNull();
    // The table's sr-only labels are absolute: the scroll box is their containing block (no page overflow at 360 px).
    expect(screen.getByRole('table').parentElement).toHaveClass('relative', 'overflow-x-auto');
  });

  it('las filas que el importador rechaza vuelven como «necesitan revisión»', async () => {
    route({ ...DRAFT, summary: { ...DRAFT.summary, errors: 1 }, snapshot: undefined,
      rows: [{ key: '1', row_number: 2, action: 'error', errors: ['La vigencia termina antes de empezar.'], warnings: [], data: {} }] });
    await analyze('IPH 15 128 2300 negro');
    fireEvent.change(screen.getByLabelText('Precio comercial del registro 1'), { target: { value: '2999' } });
    fireEvent.change(screen.getByLabelText('Proveedor del borrador'), { target: { value: '3' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    const summary = screen.getByRole('region', { name: 'Resumen del borrador' });
    expect(within(summary).getByText('Necesitan revisión: 1')).toBeInTheDocument();
    expect(within(summary).getByText(/Registro 1: La vigencia termina antes de empezar/)).toBeInTheDocument();
  });

  it('«Seguir corrigiendo» conserva lo corregido; una corrección invalida el borrador; «Cancelar» lo descarta', async () => {
    route();
    await analyze('IPH 15 128 2300 negro');
    fireEvent.change(screen.getByLabelText('Precio comercial del registro 1'), { target: { value: '2999' } });
    fireEvent.change(screen.getByLabelText('Proveedor del borrador'), { target: { value: '3' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    fireEvent.click(screen.getByRole('button', { name: 'Seguir corrigiendo' }));
    expect(screen.queryByRole('region', { name: 'Resumen del borrador' })).toBeNull();
    expect(screen.getByLabelText('Precio comercial del registro 1')).toHaveValue('2999');

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    fireEvent.change(screen.getByLabelText('Color del registro 1'), { target: { value: 'azul' } });
    expect(screen.queryByRole('region', { name: 'Resumen del borrador' })).toBeNull();

    // The correction dropped the row's variant: the draft now says why it stays out, without asking the server.
    const calls = draftCalls().length;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    expect(draftCalls()).toHaveLength(calls);
    expect(screen.getByText(/Registro 1: Elige la variante/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(screen.queryByLabelText('Vista previa de lista')).toBeNull();
    expect(screen.getByLabelText('Texto para reconocer')).toHaveValue('IPH 15 128 2300 negro');
  });

  it('sin proveedor elegido no se pide nada al servidor', async () => {
    route();
    await analyze('IPH 15 128 2300 negro');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    expect(draftCalls()).toHaveLength(0);
    expect(screen.getByText('Elige el proveedor de la lista.')).toBeInTheDocument();
  });

  it('sin permiso para gestionar productos no se ofrece el borrador de ofertas', async () => {
    route();
    await analyze('IPH 15 128 2300 negro', { manage: false });
    expect(screen.queryByRole('button', { name: 'Generar borrador' })).toBeNull();
    expect(screen.getByText(/requiere permiso para gestionar productos/i)).toBeInTheDocument();
    expect(send.mock.calls.some(([url]) => String(url).includes('/admin/suppliers/'))).toBe(false);
  });

  it('equipos propios: el borrador se arma en la pestaña y los IMEI no salen a ningún servidor', async () => {
    route();
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    await analyze(`iPhone 15\n128 GB\nColor: negro\nNS: F2LX0001\nIMEI: ${IMEI}`, { origin: 'own' });
    const before = send.mock.calls.length;
    fireEvent.change(screen.getByLabelText('Sucursal del borrador'), { target: { value: 'Principal' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Generar borrador' })); });
    expect(send.mock.calls.length).toBe(before);
    for (const [, init] of send.mock.calls) expect(String(init?.body ?? '')).not.toContain(IMEI);
    const summary = screen.getByRole('region', { name: 'Resumen del borrador' });
    expect(within(summary).getByText('Equipos para recibir: 1')).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copiar filas para la Carga de equipos' })); });
    expect(writeText.mock.calls[0][0]).toContain(IMEI);
    expect(screen.queryByLabelText('Precio comercial del registro 1')).toBeNull();
  });
});
