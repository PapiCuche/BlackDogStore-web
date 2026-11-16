/**
 * INV-DRAFT-01 — el borrador de «Recibir mercadería» guardado en el SERVIDOR.
 *
 *   · la librería habla con `/admin/inventory/receipts/draft/` (GET, PUT, DELETE) y nunca manda un costo a quien no
 *     puede verlo;
 *   · el asistente guarda solo (~1,5 s después del último cambio) cuando hay algo que conservar;
 *   · al volver, OFRECE recuperar el borrador (nunca lo aplica sin preguntar) y lo borra al descartarlo, al confirmar
 *     la recepción y al empezar otra;
 *   · nada de esto toca `localStorage` / `sessionStorage`.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ReceivingWizard, type ReceivingAccess } from '@/app/admin/components/ReceivingWizard';
import * as receipts from '@/app/admin/lib/inventory-receipts';
import { forgetBranch } from '@/app/admin/lib/last-branch';
import * as internalApi from '@/app/admin/lib/internal-api';
import * as admin from '@/app/lib/admin';
import { fetchWithAuth } from '@/app/lib/auth';

const mockRouter = { push: jest.fn(), replace: jest.fn(), refresh: jest.fn(), back: jest.fn(), forward: jest.fn(), prefetch: jest.fn() };
jest.mock('next/navigation', () => ({ usePathname: () => '/admin/inventory/receive', useRouter: () => mockRouter }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
  fetchAdminProductDetail: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchSuppliers: jest.fn(),
}));

const mockFetch = fetchWithAuth as jest.Mock;
const detail = admin.fetchAdminProductDetail as jest.Mock;

const json = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response;

const phone: receipts.ReceiptProduct = { id: 5, name: 'iPhone 15 128GB', is_serialized: true, requires_imei: true };
const cable: receipts.ReceiptProduct = { id: 9, name: 'Cable USB-C 1m', is_serialized: false, requires_imei: false };
const ORIGIN: receipts.ReceiptOrigin = { branch: 1, supplier: null, document_ref: 'F001-9', notes: '' };

// ── The library ───────────────────────────────────────────────────────────

describe('cliente del borrador', () => {
  beforeEach(() => mockFetch.mockReset());

  it('lee el borrador y lo devuelve con claves nuevas; distingue «no hay» de «no se pudo leer»', async () => {
    const line = { ...receipts.newLine(phone), units: [{ ...receipts.blankUnit(), serial_number: 'SER1', imei: '356789012345678' }] };
    mockFetch.mockResolvedValueOnce(json(200, { draft: {
      origin: ORIGIN, lines: [line], staged: [], branch: 1, updated_at: '2026-11-16T10:00:00Z', expires_at: '2026-11-17T10:00:00Z',
    } }));
    const read = await receipts.fetchReceiptDraft();
    expect(String(mockFetch.mock.calls[0][0])).toMatch(/\/admin\/inventory\/receipts\/draft\/$/);
    expect(read.status).toBe('found');
    const draft = read.status === 'found' ? read.draft : null;
    expect(draft?.branch).toBe(1);
    expect(draft?.updatedAt).toBe('2026-11-16T10:00:00Z');
    expect(draft?.lines[0].product.id).toBe(5);
    expect(draft?.lines[0].units[0].serial_number).toBe('SER1');
    // Fresh local keys: a key of the page that wrote it must not collide with one of this page.
    expect(draft?.lines[0].key).not.toBe(line.key);

    mockFetch.mockResolvedValueOnce(json(200, { draft: null }));
    expect(await receipts.fetchReceiptDraft()).toEqual({ status: 'none' });
    // A refusal, a throttle, a server error, no network or an unreadable answer is NOT «no draft».
    for (const failure of [json(403, { detail: 'no' }), json(429, {}), json(500, {}), json(200, 'x')]) {
      mockFetch.mockResolvedValueOnce(failure);
      expect(await receipts.fetchReceiptDraft()).toEqual({ status: 'failed' });
    }
    mockFetch.mockRejectedValueOnce(new Error('offline'));
    expect(await receipts.fetchReceiptDraft()).toEqual({ status: 'failed' });
  });

  it('guarda con PUT {branch, payload, base}, devuelve la versión nueva y no manda costos sin permiso', async () => {
    const line = { ...receipts.newLine(phone), units: [{ ...receipts.blankUnit(), serial_number: 'SER1', cost: '1200' }] };
    const qty = { ...receipts.newLine(cable), unitCost: '12.50' };
    const staged = [{ id: 1, kind: 'device', serial: 'S2', cost: '99', amounts: ['777'],
      hints: ['El costo 555 se dedujo.', 'El texto trae un importe sin etiqueta clara: no se usa.'] }];
    mockFetch.mockResolvedValue(json(200, { draft: { updated_at: 'v2' } }));

    expect(await receipts.saveReceiptDraft(ORIGIN.branch, receipts.draftPayload(ORIGIN, [line, qty], staged, { costs: false }), null))
      .toEqual({ outcome: 'saved', version: 'v2' });
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toMatch(/\/receipts\/draft\/$/);
    expect(init.method).toBe('PUT');
    const sent = JSON.parse(init.body);
    expect(sent.branch).toBe(1);
    expect(sent.base).toBeNull();
    expect(sent.payload.lines[0].units[0].serial_number).toBe('SER1');
    expect(JSON.stringify(sent)).not.toMatch(/1200|12\.50|"99"|777|555/);
    expect(sent.payload.staged[0].hints).toEqual(['El texto trae un importe sin etiqueta clara: no se usa.']);

    await receipts.saveReceiptDraft(1, receipts.draftPayload(ORIGIN, [line], staged, { costs: true }), 'v2');
    const second = JSON.parse(mockFetch.mock.calls[1][1].body);
    expect(second.base).toBe('v2');
    expect(second.payload.lines[0].units[0].cost).toBe('1200');
    expect(second.payload.staged[0].amounts).toEqual(['777']);
  });

  it('un 409 es un conflicto de versión', async () => {
    mockFetch.mockResolvedValueOnce(json(409, { code: 'draft_conflict' }));
    expect(await receipts.saveReceiptDraft(1, receipts.draftPayload(ORIGIN, [], [], { costs: false }), 'v1'))
      .toEqual({ outcome: 'conflict', version: null });
  });

  it('no manda un borrador demasiado grande y avisa', async () => {
    const huge = receipts.draftPayload({ ...ORIGIN, notes: 'x'.repeat(receipts.DRAFT_MAX_BYTES) }, [], [], { costs: false });
    expect((await receipts.saveReceiptDraft(1, huge, null)).outcome).toBe('too_large');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('un fallo al guardar no rompe nada', async () => {
    mockFetch.mockRejectedValueOnce(new Error('offline'));
    expect((await receipts.saveReceiptDraft(1, receipts.draftPayload(ORIGIN, [], [], { costs: false }), null)).outcome).toBe('failed');
  });

  it('descarta con DELETE', async () => {
    mockFetch.mockResolvedValueOnce(json(204, null));
    await receipts.deleteReceiptDraft();
    expect(mockFetch.mock.calls[0][1].method).toBe('DELETE');
  });

  it('dice hace cuánto se guardó', () => {
    const now = Date.parse('2026-11-16T10:30:00Z');
    expect(receipts.draftAge('2026-11-16T10:29:50Z', now)).toBe('hace menos de 1 min');
    expect(receipts.draftAge('2026-11-16T10:25:00Z', now)).toBe('hace 5 min');
    expect(receipts.draftAge('2026-11-16T07:30:00Z', now)).toBe('hace 3 h');
  });
});

// ── The wizard ────────────────────────────────────────────────────────────

const ACCESS: ReceivingAccess = { costs: false, manageProducts: false, viewProducts: false, labels: true };
const SCOPE = { user: 1, company: 7 };
const BRANCHES = [{ id: 1, name: 'Miraflores' }, { id: 2, name: 'Surco' }];

function product(id: number, name: string, over: Partial<admin.AdminProduct> = {}): admin.AdminProduct {
  return {
    id, name, slug: `p-${id}`, description: '', price: '10.00', inventory: 0, image_url: '', category_id: 1,
    category_name: 'x', is_active: true, is_published_online: false, publication_problems: [],
    is_serialized: false, requires_imei: false, sale_mode: 'stock', condition_line: 'new', product_model_id: null,
    primary_code: `SKU-${id}`, search_exact: true, created_at: '', updated_at: '', ...over,
  } as admin.AdminProduct;
}

type Call = { method: string; url: string; body: unknown };
let calls: Call[];
let serverDraft: unknown;
/** The version the fake server gives each save: v1, v2… */
let stamp: number;
/** Overrides of the fake server's draft answers (a failure, a conflict, an answer that waits). */
let draftReply: Partial<Record<'GET' | 'PUT' | 'DELETE', () => Response | Promise<Response>>>;

function route(url: string, init: RequestInit = {}): Response {
  const method = (init.method ?? 'GET').toUpperCase();
  calls.push({ method, url, body: init.body ? JSON.parse(String(init.body)) : undefined });
  if (url.includes('/receipts/draft/')) {
    const custom = draftReply[method as 'GET' | 'PUT' | 'DELETE'];
    if (custom) return custom() as Response;
    if (method === 'GET') return json(200, { draft: serverDraft });
    if (method === 'PUT') { stamp += 1; return json(200, { draft: { branch: 1, updated_at: `v${stamp}`, expires_at: '' } }); }
    if (method === 'DELETE') return json(204, null);
  }
  if (url.includes('/receipts/preview/')) {
    const body = JSON.parse(String(init.body));
    return json(200, {
      ok: true, branch: { id: body.branch, name: 'Miraflores' }, supplier: null, document_ref: body.document_ref,
      totals: { lines: 1, quantity_units: 2, devices: 0, labels: 1 },
      lines: [{ index: 0, kind: 'quantity', product: { id: 9, name: cable.name, sku: 'SKU-9', is_serialized: false },
        status: 'ok', errors: [], warnings: [], will_receive: 2, labels: 1, duplicates: [] }],
    });
  }
  if (url.includes('/receipts/confirm/')) {
    const body = JSON.parse(String(init.body));
    return json(201, {
      receipt_id: 44, branch: { id: body.branch, name: 'Miraflores' }, supplier: null, document_ref: body.document_ref,
      totals: { lines: 1, quantity_units: 2, devices: 0, labels: 1 },
      lines: [{ index: 0, kind: 'quantity', product: { id: 9, name: cable.name }, received: 2, movement_id: 1, units: 0, label_batch: null, label_error: false }],
      label_batches: [], label_error: false,
    });
  }
  return json(404, { detail: 'no' });
}

const putBody = (index: number) => draftCalls('PUT')[index].body as { base: string | null; payload: receipts.DraftPayload };

const draftCalls = (method: string) => calls.filter((c) => c.method === method && c.url.includes('/receipts/draft/'));

function savedDraft(minutesAgo = 5) {
  const now = Date.now();
  return {
    origin: { branch: 2, supplier: null, document_ref: 'F001-77', notes: '' },
    lines: [
      { key: 'l1', product: phone, quantity: '', unitCost: '', units: [{ ...receipts.blankUnit(), key: 'u1', serial_number: 'SERGUARDADO', imei: '356789012345678' }] },
    ],
    staged: [],
    branch: 2,
    updated_at: new Date(now - minutesAgo * 60_000).toISOString(),
    expires_at: new Date(now - minutesAgo * 60_000 + 86_400_000).toISOString(),
  };
}

let storage: { set: jest.SpyInstance };

async function renderWizard(access: Partial<ReceivingAccess> = {}, props: Partial<React.ComponentProps<typeof ReceivingWizard>> = {}) {
  await act(async () => {
    render(<ReceivingWizard access={{ ...ACCESS, ...access }} scope={SCOPE} branches={BRANCHES} defaultBranch={1} {...props} />);
  });
}

beforeEach(() => {
  window.scrollTo = jest.fn();
  window.history.replaceState(null, '');
  forgetBranch();
  receipts.forgetStashedDraft();
  calls = [];
  serverDraft = null;
  stamp = 0;
  draftReply = {};
  mockFetch.mockReset().mockImplementation(async (url: string, init?: RequestInit) => route(String(url), init));
  detail.mockReset().mockResolvedValue(product(9, cable.name));
  (internalApi.fetchSuppliers as jest.Mock).mockReset().mockResolvedValue({ count: 0, page: 1, page_size: 25, results: [] });
  storage = { set: jest.spyOn(Storage.prototype, 'setItem') };
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

const storedNothingSensitive = () => {
  for (const [, value] of storage.set.mock.calls) expect(String(value)).not.toMatch(/SER|3567890/);
};

describe('guardado automático', () => {
  it('guarda ~1,5 s después del último cambio, y sólo cuando hay algo que conservar', async () => {
    jest.useFakeTimers();
    await renderWizard({}, { initialProductId: 9 });
    await screen.findByRole('article', { name: cable.name });
    // Nothing yet: the debounce waits for the person to stop.
    await act(async () => { jest.advanceTimersByTime(1000); });
    expect(draftCalls('PUT')).toHaveLength(0);
    await act(async () => { jest.advanceTimersByTime(600); });
    expect(draftCalls('PUT')).toHaveLength(1);
    const body = draftCalls('PUT')[0].body as { branch: number; payload: { lines: { product: { id: number } }[] } };
    expect(body.branch).toBe(1);
    expect(body.payload.lines[0].product.id).toBe(9);

    // An edit starts the wait again, and saves once.
    const qty = within(screen.getByRole('article', { name: cable.name })).getByLabelText('Cantidad');
    fireEvent.change(qty, { target: { value: '3' } });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('PUT')).toHaveLength(2);
    expect(JSON.stringify(draftCalls('PUT')[1].body)).toMatch(/"quantity":"3"/);
    storedNothingSensitive();
  });

  it('no guarda una pantalla vacía', async () => {
    jest.useFakeTimers();
    await renderWizard();
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(draftCalls('PUT')).toHaveLength(0);
  });

  it('no manda costos si la persona no puede verlos', async () => {
    jest.useFakeTimers();
    serverDraft = null;
    receipts.stashDraft(ORIGIN, [{ ...receipts.newLine(cable), quantity: '2', unitCost: '12.50' }], SCOPE);
    await renderWizard({ costs: false });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('PUT')).toHaveLength(1);
    expect(JSON.stringify(draftCalls('PUT')[0].body)).not.toMatch(/12\.50/);
  });
});

describe('recuperar o descartar', () => {
  it('ofrece el borrador sin aplicarlo y lo recupera al pedirlo', async () => {
    serverDraft = savedDraft(5);
    await renderWizard();
    const recover = await screen.findByRole('button', { name: /Recuperar el borrador guardado \(hace 5 min\)/ });
    expect(screen.getByRole('button', { name: 'Descartar' })).toBeInTheDocument();
    // Not applied silently.
    expect(screen.queryByRole('article', { name: phone.name })).not.toBeInTheDocument();
    expect(draftCalls('PUT')).toHaveLength(0);

    await act(async () => { fireEvent.click(recover); });
    const card = await screen.findByRole('article', { name: phone.name });
    expect(within(card).getAllByLabelText(/Número de serie/)[0]).toHaveValue('SERGUARDADO');
    expect(screen.queryByRole('button', { name: /Recuperar el borrador/ })).not.toBeInTheDocument();
    expect(draftCalls('DELETE')).toHaveLength(0);
    storedNothingSensitive();
  });

  it('mientras no se elige, lo escrito no pisa el borrador del servidor', async () => {
    jest.useFakeTimers();
    serverDraft = savedDraft(5);
    await renderWizard({}, { initialProductId: 9 });
    await screen.findByRole('button', { name: /Recuperar el borrador guardado/ });
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(draftCalls('PUT')).toHaveLength(0);
  });

  it('descartar borra el borrador del servidor y no aplica nada', async () => {
    serverDraft = savedDraft(5);
    await renderWizard();
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Descartar' })); });
    await waitFor(() => expect(draftCalls('DELETE')).toHaveLength(1));
    expect(screen.queryByRole('button', { name: /Recuperar el borrador/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('article', { name: phone.name })).not.toBeInTheDocument();
  });

  it('un borrador vacío no se ofrece', async () => {
    serverDraft = { ...savedDraft(), lines: [], staged: [] };
    await renderWizard();
    await waitFor(() => expect(draftCalls('GET')).toHaveLength(1));
    expect(screen.queryByRole('button', { name: /Recuperar el borrador/ })).not.toBeInTheDocument();
  });
});

describe('fin de la recepción', () => {
  it('confirmar con éxito borra el borrador, y «Registrar otra recepción» también', async () => {
    serverDraft = { ...savedDraft(), lines: [{ key: 'l9', product: cable, quantity: '2', unitCost: '', units: [] }], branch: 1, origin: ORIGIN };
    await renderWizard();
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: /Recuperar el borrador guardado/ })); });
    await screen.findByRole('article', { name: cable.name });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Siguiente: revisar/ })); });
    await act(async () => { fireEvent.click(await screen.findByRole('checkbox')); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Confirmar recepción/ })); });
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    await waitFor(() => expect(draftCalls('DELETE')).toHaveLength(1));

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Registrar otra recepción' })); });
    await waitFor(() => expect(draftCalls('DELETE')).toHaveLength(2));
  });
});

describe('versión del borrador (revisión de #303)', () => {
  it('cada guardado nombra la versión que vio: null la primera vez, después la que dio el servidor', async () => {
    jest.useFakeTimers();
    await renderWizard({}, { initialProductId: 9 });
    await screen.findByRole('article', { name: cable.name });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(putBody(0).base).toBeNull();
    fireEvent.change(within(screen.getByRole('article', { name: cable.name })).getByLabelText('Cantidad'), { target: { value: '3' } });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(putBody(1).base).toBe('v1');
  });

  it('el borrador recuperado se guarda sobre su propia versión', async () => {
    jest.useFakeTimers();
    serverDraft = { ...savedDraft(5), updated_at: '2026-11-16T10:00:00.123456+00:00' };
    await renderWizard();
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: /Recuperar el borrador guardado/ })); });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(putBody(0).base).toBe('2026-11-16T10:00:00.123456+00:00');
  });

  it('un conflicto detiene el guardado, lo dice y se puede descartar lo guardado para seguir', async () => {
    jest.useFakeTimers();
    draftReply.PUT = () => json(409, { code: 'draft_conflict' });
    await renderWizard({}, { initialProductId: 9 });
    await screen.findByRole('article', { name: cable.name });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('PUT')).toHaveLength(1);
    expect(await screen.findByText(/Hay un borrador más reciente guardado \(u otra pestaña lo cambió, o ya se confirmó\)\. Recárgalo o descártalo para seguir guardando\./)).toBeInTheDocument();
    // Nothing more is sent while it stands.
    fireEvent.change(within(screen.getByRole('article', { name: cable.name })).getByLabelText('Cantidad'), { target: { value: '3' } });
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(draftCalls('PUT')).toHaveLength(1);

    delete draftReply.PUT;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Descartar el guardado y seguir' })); });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('DELETE')).toHaveLength(1);
    expect(draftCalls('PUT')).toHaveLength(2);
    expect(putBody(1).base).toBeNull();
    expect(JSON.stringify(putBody(1).payload)).toMatch(/"quantity":"3"/);
    expect(screen.queryByText(/Hay un borrador más reciente/)).not.toBeInTheDocument();
  });

  it('«Ver el borrador guardado» lo vuelve a leer y lo ofrece', async () => {
    jest.useFakeTimers();
    draftReply.PUT = () => json(409, { code: 'draft_conflict' });
    await renderWizard({}, { initialProductId: 9 });
    await screen.findByRole('article', { name: cable.name });
    await act(async () => { jest.advanceTimersByTime(1600); });
    serverDraft = savedDraft(1);
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Ver el borrador guardado' })); });
    expect(await screen.findByRole('button', { name: /Recuperar el borrador guardado/ })).toBeInTheDocument();
    expect(screen.queryByText(/Hay un borrador más reciente/)).not.toBeInTheDocument();
    // Still nothing saved until the person chooses.
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(draftCalls('PUT')).toHaveLength(1);
  });
});

describe('no se pudo comprobar el borrador (revisión de #303)', () => {
  it('un fallo al leer no es «no hay»: no guarda, lo dice y permite reintentar', async () => {
    jest.useFakeTimers();
    draftReply.GET = () => json(500, {});
    await renderWizard({}, { initialProductId: 9 });
    await screen.findByRole('article', { name: cable.name });
    expect(await screen.findByText('No se pudo comprobar si hay un borrador guardado')).toBeInTheDocument();
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(draftCalls('PUT')).toHaveLength(0);

    delete draftReply.GET;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reintentar' })); });
    expect(screen.queryByText('No se pudo comprobar si hay un borrador guardado')).not.toBeInTheDocument();
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('PUT')).toHaveLength(1);
    expect(putBody(0).base).toBeNull();
  });

  it('volver de «crear artículo» con la pregunta abierta no guarda hasta que la persona responda', async () => {
    jest.useFakeTimers();
    serverDraft = savedDraft(5);
    // The list was kept in memory while the question about the server draft was still open.
    receipts.stashDraft(ORIGIN, [{ ...receipts.newLine(cable), quantity: '2' }], SCOPE);
    await renderWizard();
    expect(await screen.findByRole('button', { name: /Recuperar el borrador guardado/ })).toBeInTheDocument();
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(draftCalls('PUT')).toHaveLength(0);
  });

  it('volver de «crear artículo» ya resuelto guarda sobre la versión que se tenía, sin preguntar', async () => {
    jest.useFakeTimers();
    const view = await (async () => {
      let out: ReturnType<typeof render> | null = null;
      await act(async () => {
        out = render(<ReceivingWizard access={{ ...ACCESS, manageProducts: true }} scope={SCOPE} branches={BRANCHES} defaultBranch={1} initialProductId={9} />);
      });
      return out!;
    })();
    await screen.findByRole('article', { name: cable.name });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('PUT')).toHaveLength(1);
    fireEvent.click(screen.getByRole('link', { name: /No existe: crear artículo/ }));
    view.unmount();
    const gets = draftCalls('GET').length;
    await renderWizard({ manageProducts: true }, { initialProductId: 9 });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('GET')).toHaveLength(gets);
    expect(screen.queryByRole('button', { name: /Recuperar el borrador guardado/ })).not.toBeInTheDocument();
    expect(draftCalls('PUT')).toHaveLength(2);
    expect(putBody(1).base).toBe('v1');
  });
});

describe('un guardado a la vez (revisión de #303)', () => {
  it('nunca hay dos PUT en vuelo; el cambio más nuevo espera y sale solo el último', async () => {
    jest.useFakeTimers();
    let release: (() => void) | null = null;
    draftReply.PUT = () => new Promise<Response>((resolve) => {
      release = () => { stamp += 1; resolve(json(200, { draft: { branch: 1, updated_at: `v${stamp}`, expires_at: '' } })); };
    });
    await renderWizard({}, { initialProductId: 9 });
    await screen.findByRole('article', { name: cable.name });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('PUT')).toHaveLength(1);
    const qty = () => within(screen.getByRole('article', { name: cable.name })).getByLabelText('Cantidad');
    fireEvent.change(qty(), { target: { value: '3' } });
    await act(async () => { jest.advanceTimersByTime(1600); });
    fireEvent.change(qty(), { target: { value: '4' } });
    await act(async () => { jest.advanceTimersByTime(1600); });
    // The first is still on its way: nothing else left.
    expect(draftCalls('PUT')).toHaveLength(1);
    await act(async () => { release!(); });
    await act(async () => { jest.advanceTimersByTime(10); });
    expect(draftCalls('PUT')).toHaveLength(2);
    expect(putBody(1).base).toBe('v1');
    expect(JSON.stringify(putBody(1).payload)).toMatch(/"quantity":"4"/);
  });

  it('vaciar la pantalla borra lo guardado, por la misma cola', async () => {
    jest.useFakeTimers();
    await renderWizard({}, { initialProductId: 9 });
    const card = await screen.findByRole('article', { name: cable.name });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('PUT')).toHaveLength(1);
    fireEvent.click(within(card).getByRole('button', { name: /Quitar/ }));
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('DELETE')).toHaveLength(1);
  });

  it('vaciar la pantalla con la versión desconocida no borra nada', async () => {
    jest.useFakeTimers();
    // A save the server took but whose answer names no version: what is there is no longer known.
    draftReply.PUT = () => json(200, { draft: {} });
    await renderWizard({}, { initialProductId: 9 });
    const card = await screen.findByRole('article', { name: cable.name });
    await act(async () => { jest.advanceTimersByTime(1600); });
    expect(draftCalls('PUT')).toHaveLength(1);
    fireEvent.click(within(card).getByRole('button', { name: /Quitar/ }));
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(draftCalls('DELETE')).toHaveLength(0);
  });

  it('tras confirmar no sale ningún guardado más', async () => {
    serverDraft = { ...savedDraft(), lines: [{ key: 'l9', product: cable, quantity: '2', unitCost: '', units: [] }], branch: 1, origin: ORIGIN };
    await renderWizard();
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: /Recuperar el borrador guardado/ })); });
    await screen.findByRole('article', { name: cable.name });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Siguiente: revisar/ })); });
    await act(async () => { fireEvent.click(await screen.findByRole('checkbox')); });
    const before = draftCalls('PUT').length;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Confirmar recepción/ })); });
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    await waitFor(() => expect(draftCalls('DELETE')).toHaveLength(1));
    await act(async () => { await new Promise((r) => setTimeout(r, 1700)); });
    expect(draftCalls('PUT')).toHaveLength(before);
  });
});
