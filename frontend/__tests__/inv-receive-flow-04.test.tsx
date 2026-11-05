/**
 * INV-RECEIVE-FLOW-04 · ocho seminuevos pegados desde WhatsApp, un precio de venta distinto por equipo, sin abrir
 * ocho formularios, y una sola recepción. Más: el analizador de «Carga masiva» ya no registra equipos por su
 * cuenta: entrega la lista a «Recibir mercadería» (memoria de la página, nunca almacenamiento).
 * El servidor es un catálogo falso en memoria. Datos ficticios: ni IMEI, ni series, ni precios reales.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ReceivingWizard, type ReceivingAccess } from '@/app/admin/components/ReceivingWizard';
import { InventoryTextPreview } from '@/app/admin/components/InventoryTextPreview';
import { luhnCheckDigit } from '@/app/admin/lib/inventory-text-preview';
import * as receipts from '@/app/admin/lib/inventory-receipts';
import { applyPrice, priceSummary, type StagedRow } from '@/app/admin/lib/receive-list';
import { forgetBranch } from '@/app/admin/lib/last-branch';
import { fetchWithAuth } from '@/app/lib/auth';

const mockRouter = { push: jest.fn(), replace: jest.fn(), refresh: jest.fn(), back: jest.fn(), forward: jest.fn(), prefetch: jest.fn() };
jest.mock('next/navigation', () => ({ usePathname: () => '/admin/inventory/receive', useRouter: () => mockRouter }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(async () => ({ count: 0, page: 1, page_size: 8, results: [] })),
  fetchAdminProductDetail: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchSuppliers: jest.fn(async () => ({ count: 0, page: 1, page_size: 25, results: [] })),
}));

const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers() }) as Response;

const BRANCHES = [{ id: 1, name: 'Miraflores' }];
const ACCESS: ReceivingAccess = { costs: true, manageProducts: true, viewProducts: true, labels: true };
const SCOPE = { user: 1, company: 7 };

const imei = (body: string) => body + luhnCheckDigit(body);
const IMEIS = Array.from({ length: 8 }, (_, i) => imei(String(35999900000100 + i * 7)));
const COMBOS = [
  ['iPhone 13', 128, 'azul'], ['iPhone 13', 128, 'negro'], ['iPhone 14', 128, 'azul'], ['iPhone 14 Pro', 256, 'morado'],
  ['iPhone 12', 64, 'blanco'], ['iPhone 13', 256, 'azul'], ['iPhone 14', 256, 'negro'], ['iPhone 15', 128, 'rosa'],
] as const;
const EIGHT = COMBOS.map(([model, gb, colour], n) => [
  model, `${gb} GB`, `Batería ${85 + n}%`, `NS: QAFAKE${String(n).padStart(4, '0')}`, `IMEI: ${IMEIS[n]}`, `Color: ${colour}`, `Costo ${1000 + n * 100}`,
].join('\n')).join('\n\n');
const PRICES = ['1499.00', '1549.00', '1899.00', '2999.00', '899.00', '1699.00', '2199.00', '2799.00'];

type Variant = { id: number; name: string; model: string; capacityGb: number; colour: string };
const catalogue: Variant[] = COMBOS.map(([model, gb, colour], n) => ({
  id: 300 + n, model, capacityGb: gb, colour, name: `${model} ${gb} GB ${colour[0].toUpperCase()}${colour.slice(1)} Seminuevo`,
}));
type Body = {
  candidates: { key: string; model: string; capacityGb: number | null; colour: string; line: string }[];
  lines: { kind: string; product: number; units: Record<string, unknown>[] }[]; branch: number; document_ref: string;
};
let seen: { url: string; body: Body }[];

function route(url: string, init?: RequestInit): Response {
  const body = (init?.body ? JSON.parse(String(init.body)) : undefined) as Body;
  seen.push({ url, body });
  if (url.includes('/text-preview/match/')) {
    const matches: Record<string, unknown> = {};
    for (const c of body.candidates) {
      const v = catalogue.find((x) => x.model.toLowerCase() === c.model.toLowerCase() && x.capacityGb === c.capacityGb
        && x.colour === c.colour.toLowerCase() && c.line === 'used');
      matches[c.key] = v
        ? { status: 'exact', reason: '', options: [{ product_id: v.id, name: v.name, condition_line: 'used', options: [], is_serialized: true, requires_imei: true, is_active: true }] }
        : { status: 'none', reason: 'No existe.', options: [] };
    }
    return reply(200, { matches });
  }
  if (url.includes('/receipts/preview/')) {
    const lines = body.lines.map((l, index) => {
      const v = catalogue.find((x) => x.id === l.product)!;
      const priced = l.units.filter((u) => u.sale_price).length;
      return {
        index, kind: l.kind, product: { id: v.id, name: v.name, sku: '', is_serialized: true, price: '0.00', condition_line: 'used' },
        status: 'ok', errors: [], warnings: [], will_receive: l.units.length, labels: l.units.length, duplicates: [], unit_issues: [],
        commercial: {
          placement: 'model_page', state: 'pending', published: false, problems: [], has_dispatch_branch: true, in_dispatch_branch: true,
          model: { id: 1, name: v.model, published: true }, units_priced: priced, units_unpriced: l.units.length - priced, priced_by: 'unit',
        },
      };
    });
    return reply(200, {
      ok: true, branch: { id: body.branch, name: 'Miraflores' }, supplier: null, document_ref: body.document_ref,
      totals: { lines: lines.length, quantity_units: 0, devices: lines.reduce((n, l) => n + l.will_receive, 0), labels: lines.reduce((n, l) => n + l.labels, 0) },
      lines,
    });
  }
  if (url.includes('/receipts/confirm/')) {
    const lines = body.lines.map((l, index) => ({
      index, kind: l.kind, product: { id: l.product, name: catalogue.find((v) => v.id === l.product)!.name },
      received: l.units.length, movement_id: index + 1, units: l.units.length,
      units_unpriced: l.units.filter((u) => !u.sale_price).length,
      label_batch: { id: 8 + index, count: l.units.length, url: `/admin/inventory/labels?batch=${8 + index}` }, label_error: false,
    }));
    const devices = lines.reduce((n, l) => n + l.units, 0);
    return reply(201, {
      receipt_id: 'rcp-1', branch: { id: 1, name: 'Miraflores' }, supplier: null, document_ref: body.document_ref,
      totals: { lines: lines.length, quantity_units: 0, devices, labels: devices }, lines,
      label_batches: lines.map((l) => l.label_batch), label_error: false,
    });
  }
  if (url.includes('/catalog/grades/')) return reply(200, { results: [] });
  if (url.includes('/catalog-proposal/')) return reply(200, { plan: { models: [], options: [], variants: [] }, rows: [], counts: {} });
  return reply(404, { detail: 'no' });
}
const calls = (part: string) => seen.filter((c) => c.url.includes(part));

async function renderWizard(access: Partial<ReceivingAccess> = {}) {
  const u = userEvent.setup();
  await act(async () => {
    render(<ReceivingWizard access={{ ...ACCESS, ...access }} scope={SCOPE} branches={BRANCHES} defaultBranch={1} />);
  });
  return u;
}

async function pasteEight(u: ReturnType<typeof userEvent.setup>) {
  await u.click(screen.getByRole('button', { name: /Siguiente: artículos/ }));
  await screen.findByRole('heading', { name: '¿Qué llegó?' });
  await u.click(screen.getByRole('button', { name: /^Pegar una lista/ }));
  fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: EIGHT } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Analizar y preparar' })); });
  await screen.findByText(/Reconocimos 8 equipos/);
  await waitFor(() => expect(screen.getAllByText('Asociado automáticamente')).toHaveLength(8));
}

const priceInput = (n: number) => screen.getByLabelText(`Precio de venta del equipo ${n} (S/)`) as HTMLInputElement;

beforeEach(() => {
  window.scrollTo = jest.fn();
  window.history.replaceState(null, '');
  forgetBranch();
  receipts.forgetStashedDraft();
  seen = [];
  mockRouter.push.mockReset();
  send.mockReset().mockImplementation(async (url, init) => route(String(url), init as RequestInit));
});
afterEach(() => jest.restoreAllMocks());

describe('ocho seminuevos, ocho precios', () => {
  it('cada fila cerrada muestra su estado comercial y un campo de precio; se escriben ocho precios sin abrir ninguna', async () => {
    const storage = [jest.spyOn(Storage.prototype, 'setItem'), jest.spyOn(Storage.prototype, 'getItem')];
    const u = await renderWizard();
    await pasteEight(u);

    // Ninguna fila está abierta y todas dicen «Pendiente de precio».
    expect(screen.queryByLabelText('Número de serie *')).not.toBeInTheDocument();
    expect(screen.getAllByText('Pendiente de precio')).toHaveLength(8);
    expect(screen.getByLabelText('Resumen de precios')).toHaveTextContent('0 de 8 seminuevos con precio de venta · 8 pendientes');
    // El costo autorizado se ve en la fila, separado del precio; el precio nunca sale del costo.
    expect(screen.getByText('Costo S/ 1000')).toBeInTheDocument();
    expect(priceInput(1).value).toBe('');

    for (let n = 1; n <= 8; n++) fireEvent.change(priceInput(n), { target: { value: PRICES[n - 1] } });
    expect(screen.queryAllByText('Pendiente de precio')).toHaveLength(0);
    expect(screen.getByLabelText('Resumen de precios')).toHaveTextContent('8 de 8 seminuevos con precio de venta · 0 pendientes');
    // Escribir en una fila no abre ni reinicia las demás.
    expect(priceInput(3).value).toBe('1899.00');
    expect(screen.queryByLabelText('Número de serie *')).not.toBeInTheDocument();

    await u.click(screen.getByRole('button', { name: 'Agregar 8 equipos listos a la recepción' }));
    await u.click(screen.getByRole('button', { name: /Siguiente: revisar/ }));
    await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
    await u.click(screen.getByLabelText(/Revisé los datos/));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });

    const confirms = calls('/receipts/confirm/');
    expect(confirms).toHaveLength(1);
    const sent = confirms[0].body.lines.flatMap((l) => l.units.map((unit) => [unit.serial, unit.sale_price, unit.unit_cost]));
    expect(sent).toEqual(expect.arrayContaining(PRICES.map((price, n) => [`QAFAKE${String(n).padStart(4, '0')}`, price, String(1000 + n * 100)])));
    expect(sent).toHaveLength(8);
    // Nada de la lista, series ni IMEI pasó por el almacenamiento del navegador.
    for (const spy of storage) for (const call of spy.mock.calls) expect(JSON.stringify(call)).not.toMatch(/QAFAKE|35999900/);
  }, 60_000);

  it('«mismo precio a los elegidos» sólo actúa con la acción expresa y sólo sobre los elegidos', async () => {
    const u = await renderWizard();
    await pasteEight(u);
    const bulk = screen.getByRole('group', { name: 'Precio para varios equipos' });
    fireEvent.change(within(bulk).getByLabelText('Precio para los elegidos (S/)'), { target: { value: '1200.00' } });
    // Escribir el importe no cambia nada todavía, y sin elegidos no hay nada que aplicar.
    expect(screen.getAllByText('Pendiente de precio')).toHaveLength(8);
    expect(within(bulk).getByRole('button', { name: /Aplicar/ })).toBeDisabled();

    await u.click(screen.getByLabelText('Elegir el equipo 2 para un mismo precio'));
    await u.click(screen.getByLabelText('Elegir el equipo 5 para un mismo precio'));
    await u.click(within(bulk).getByRole('button', { name: 'Aplicar S/ 1200.00 a los 2 elegidos' }));
    expect(priceInput(2).value).toBe('1200.00');
    expect(priceInput(5).value).toBe('1200.00');
    expect(priceInput(1).value).toBe('');
    expect(screen.getAllByText('Pendiente de precio')).toHaveLength(6);
    // La selección se vacía después de aplicar: un segundo clic no toca otras filas.
    expect(screen.getByLabelText('Elegir el equipo 2 para un mismo precio')).not.toBeChecked();
  }, 60_000);

  it('un importe inválido se dice en la fila cerrada y la fila no entra', async () => {
    const u = await renderWizard();
    await pasteEight(u);
    fireEvent.change(priceInput(4), { target: { value: '0' } });
    // Dicho una vez, en la fila cerrada, sin abrirla.
    expect(screen.getAllByText(/mayor que cero/)).toHaveLength(1);
    expect(priceInput(4)).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', { name: 'Agregar 7 equipos listos a la recepción' })).toBeInTheDocument();
    void u;
  }, 60_000);

  it('«Siguiente sin precio» lleva el cursor al precio del próximo equipo pendiente', async () => {
    const u = await renderWizard();
    await pasteEight(u);
    fireEvent.change(priceInput(1), { target: { value: '1499.00' } });
    await u.click(screen.getByRole('button', { name: 'Siguiente sin precio' }));
    expect(priceInput(2)).toHaveFocus();
  }, 60_000);

  it('sin permiso de precios: ningún campo de precio, el estado pendiente y a quién pedirlo; nada de precio viaja', async () => {
    const u = await renderWizard({ manageProducts: false, costs: false });
    await pasteEight(u);
    expect(screen.queryByLabelText(/Precio de venta del equipo 1/)).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Precio para varios equipos' })).not.toBeInTheDocument();
    expect(screen.getAllByText('Pendiente de precio')).toHaveLength(8);
    expect(screen.getByLabelText('Resumen de precios')).toHaveTextContent(/permiso «Administrar productos»/);
    expect(screen.queryByText(/Costo S\//)).not.toBeInTheDocument();

    await u.click(screen.getByRole('button', { name: 'Agregar 8 equipos listos a la recepción' }));
    await u.click(screen.getByRole('button', { name: /Siguiente: revisar/ }));
    await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
    await u.click(screen.getByLabelText(/Revisé los datos/));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    const units = calls('/receipts/confirm/')[0].body.lines.flatMap((l) => l.units);
    expect(units).toHaveLength(8);
    for (const unit of units) { expect(unit).not.toHaveProperty('sale_price'); expect(unit).not.toHaveProperty('unit_cost'); }
  }, 60_000);

  it('después de registrar, los siguientes pasos: ver equipos, completar precios, etiquetas, otra recepción, stock y recuentos', async () => {
    const u = await renderWizard();
    await pasteEight(u);
    fireEvent.change(priceInput(1), { target: { value: '1499.00' } });
    await u.click(screen.getByRole('button', { name: 'Agregar 8 equipos listos a la recepción' }));
    await u.click(screen.getByRole('button', { name: /Siguiente: revisar/ }));
    await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
    await u.click(screen.getByLabelText(/Revisé los datos/));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });

    const next = screen.getByRole('navigation', { name: 'Siguientes pasos' });
    expect(within(next).getByRole('link', { name: 'Ver equipos registrados' })).toHaveAttribute('href', '/admin/inventory/used');
    expect(within(next).getByRole('link', { name: 'Completar 7 precios pendientes' })).toHaveAttribute('href', '/admin/inventory/used?precio=sin');
    expect(within(next).getByRole('link', { name: 'Consultar stock' })).toHaveAttribute('href', '/admin/inventory/stock');
    expect(within(next).getByRole('link', { name: 'Ir a recuentos' })).toHaveAttribute('href', '/admin/inventory/counts');
    expect(screen.getAllByRole('button', { name: 'Registrar otra recepción' }).length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: 'Imprimir o ver etiquetas' })).toBeInTheDocument();
    // Nada se publica solo.
    expect(calls('/publication')).toHaveLength(0);
  }, 60_000);
});

describe('el analizador de Carga masiva entrega la recepción física al asistente', () => {
  it('origen propio: sin tabla ni registro propio; «Continuar en Recibir mercadería» lleva el texto en memoria', async () => {
    const setItem = jest.spyOn(Storage.prototype, 'setItem');
    render(<InventoryTextPreview canSeeCosts canManageProducts canReceiveUnits companyId={7} userId={1} />);
    fireEvent.change(screen.getByLabelText('Origen declarado'), { target: { value: 'own' } });
    fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: EIGHT } });
    expect(screen.queryByRole('button', { name: 'Analizar localmente' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Borrador de importación' })).not.toBeInTheDocument();
    const go = screen.getByRole('link', { name: 'Continuar en Recibir mercadería' });
    expect(go).toHaveAttribute('href', '/admin/inventory/receive');
    fireEvent.click(go);
    for (const call of setItem.mock.calls) expect(JSON.stringify(call)).not.toMatch(/QAFAKE|35999900/);

    // El asistente de la misma persona y empresa recibe la lista y la analiza al llegar al paso 2.
    const u = await renderWizard();
    expect(screen.getByText(/Trajimos tu lista pegada/)).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: /Siguiente: artículos/ }));
    await screen.findByText(/Reconocimos 8 equipos/);
    expect(calls('/text-preview/units/')).toHaveLength(0);
  }, 60_000);

  it('la lista entregada no llega a otra persona ni a otra empresa', async () => {
    render(<InventoryTextPreview canSeeCosts canManageProducts canReceiveUnits companyId={8} userId={1} />);
    fireEvent.change(screen.getByLabelText('Origen declarado'), { target: { value: 'own' } });
    fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: EIGHT } });
    fireEvent.click(screen.getByRole('link', { name: 'Continuar en Recibir mercadería' }));
    await renderWizard(); // empresa 7
    expect(screen.queryByText(/Trajimos tu lista pegada/)).not.toBeInTheDocument();
  });

  it('sin permiso para recibir: lo explica y no ofrece continuar', () => {
    render(<InventoryTextPreview canSeeCosts={false} canReceiveUnits={false} companyId={7} userId={1} />);
    fireEvent.change(screen.getByLabelText('Origen declarado'), { target: { value: 'own' } });
    expect(screen.queryByRole('link', { name: 'Continuar en Recibir mercadería' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Continuar en Recibir mercadería' })).not.toBeInTheDocument();
    expect(screen.getByText(/permiso para ajustar inventario/)).toBeInTheDocument();
  });
});

describe('lógica pura del precio por equipo', () => {
  const row = (id: number, change: Partial<StagedRow> = {}) => ({
    id, kind: 'device', line: 'used', price: '', product: { id: 1, name: 'x', condition_line: 'used' }, ...change,
  }) as unknown as StagedRow;

  it('applyPrice escribe sólo en los elegidos que son seminuevos y nunca con un importe inválido', () => {
    const rows = [row(1), row(2), row(3, { kind: 'accessory' } as Partial<StagedRow>), row(4, { product: { id: 2, name: 'n', condition_line: 'new' } } as Partial<StagedRow>)];
    const next = applyPrice(rows, [1, 3, 4], '999.90');
    expect(next.map((r) => r.price)).toEqual(['999.90', '', '', '']);
    expect(applyPrice(rows, [1], '0')).toBe(rows);
    expect(applyPrice(rows, [1], '12.345')).toBe(rows);
  });

  it('priceSummary cuenta los seminuevos con y sin precio', () => {
    const rows = [row(1, { price: '100.00' }), row(2), row(3, { kind: 'accessory' } as Partial<StagedRow>)];
    expect(priceSummary(rows, true)).toEqual({ priceable: 2, priced: 1, pending: 1 });
    // Sin permiso, un precio escrito no cuenta: no viaja.
    expect(priceSummary(rows, false)).toEqual({ priceable: 2, priced: 0, pending: 2 });
  });
});
