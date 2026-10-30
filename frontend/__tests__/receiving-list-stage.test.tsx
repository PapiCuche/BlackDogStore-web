/**
 * INV-RECEIVE-UX-02 · «Pegar una lista» dentro del asistente de recepción, de punta a punta en la pantalla:
 * pegar, analizar, asociar solo, resolver lo que falta UNA vez por grupo, crear lo aprobado, revisar cada equipo
 * y confirmar una sola recepción idempotente. El servidor es un catálogo falso en memoria; todo lo demás es real.
 * Datos ficticios: ni IMEI ni series ni precios reales.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ReceivingWizard, type ReceivingAccess } from '@/app/admin/components/ReceivingWizard';
import { luhnCheckDigit } from '@/app/admin/lib/inventory-text-preview';
import * as receipts from '@/app/admin/lib/inventory-receipts';
import * as internalApi from '@/app/admin/lib/internal-api';
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
  fetchSuppliers: jest.fn(),
}));

const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const suppliersApi = internalApi.fetchSuppliers as jest.Mock;
const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers() }) as Response;

const BRANCHES = [{ id: 1, name: 'Miraflores' }];
const ACCESS: ReceivingAccess = { costs: false, manageProducts: true, viewProducts: true, labels: true };
const SCOPE = { user: 1, company: 7 };

const imei = (body: string) => body + luhnCheckDigit(body);
const IMEIS = Array.from({ length: 12 }, (_, i) => imei(String(35000000000000 + i * 137)));
const block = (head: string, n: number, extra: string[] = []) =>
  [head, `Batería ${90 + (n % 8)}%`, `NS: FAKE${String(n).padStart(6, '0')}`, `IMEI ${IMEIS[n]}`, ...extra].join('\n');

const ELEVEN = [
  block('iPhone 13 128 GB azul', 0), block('iPhone 13 128 GB azul', 1), block('iPhone 13 128 GB azul', 2),
  block('iPhone 13 256 GB negro', 3), block('iPhone 13 256 GB negro', 4),
  block('iPhone 12 64 GB blanco', 5), block('iPhone 12 64 GB blanco', 6),
  block('iPhone 14 Pro 128 GB morado', 7), block('iPhone 14 Pro 128 GB morado', 8),
  block('Galaxy S23 256 GB negro', 9), block('Galaxy S23 256 GB negro', 10),
].join('\n\n');

// ── The fake server: a catalogue in memory ────────────────────────────────

type Variant = { id: number; name: string; model: string; capacityGb: number; colour: string; line: 'new' | 'used' };
let catalogue: Variant[];
let nextProduct: number;
type Candidate = { key: string; kind: string; line: 'new' | 'used'; model: string; capacityGb: number; colour: string };
type SentLine = { kind: string; product: number; units: Record<string, unknown>[] };
type Sent = {
  candidates: Candidate[]; rows: Candidate[]; approve: { variants: string[] };
  lines: SentLine[]; branch: number; document_ref: string;
};
let seen: { url: string; body: Sent; headers: Record<string, string> }[];

const sig = (v: { model: string; capacityGb: number | null; colour: string; line: string }) =>
  [v.model.toLowerCase(), v.capacityGb, v.colour.toLowerCase(), v.line].join('|');
const optionOf = (v: Variant) => ({
  product_id: v.id, name: v.name, condition_line: v.line, options: [], is_serialized: true, requires_imei: true, is_active: true,
});
const nameOf = (c: { model: string; capacityGb: number | null; colour: string; line: string }) =>
  `${c.model} ${c.capacityGb} GB ${c.colour[0].toUpperCase()}${c.colour.slice(1)}${c.line === 'used' ? ' Seminuevo' : ''}`;

function route(url: string, init?: RequestInit): Response {
  const path = String(url);
  const body = (init?.body ? JSON.parse(String(init.body)) : undefined) as Sent;
  seen.push({ url: path, body, headers: (init?.headers ?? {}) as Record<string, string> });
  if (path.includes('/text-preview/match/')) {
    const matches: Record<string, unknown> = {};
    for (const c of body.candidates) {
      const found = catalogue.filter((v) => sig(v) === sig(c));
      matches[c.key] = found.length === 1
        ? { status: 'exact', reason: '', options: [optionOf(found[0])] }
        : { status: 'none', reason: 'No existe esa variante del modelo.', options: [] };
    }
    return reply(200, { matches });
  }
  if (path.includes('/catalog-proposal/apply/')) {
    const created = body.approve.variants.map((ref) => {
      const row = body.rows.find((r) => `v:${sig(r)}` === ref)!;
      const variant: Variant = { id: nextProduct++, name: nameOf(row), model: row.model, capacityGb: row.capacityGb, colour: row.colour, line: row.line };
      catalogue.push(variant);
      return { ref, name: variant.name };
    });
    return reply(200, { created: { models: [], options: [], variants: created }, existing: { models: [], variants: [] }, replayed: false });
  }
  if (path.includes('/catalog-proposal/')) {
    const missing = new Map<string, Candidate>();
    for (const r of body.rows) if (!catalogue.some((v) => sig(v) === sig(r))) missing.set(sig(r), r);
    const variants = [...missing.values()].map((r) => ({
      ref: `v:${sig(r)}`, model_ref: `m:${r.model}`, line: r.line, option_refs: [],
      row_keys: body.rows.filter((x) => sig(x) === sig(r)).map((x) => x.key),
      status: 'missing', product_id: null, suggested_name: nameOf(r), suggested_price: null, requires_imei: true,
      creates: { model: false, options: [] },
    }));
    const models = [...new Set(variants.map((v) => v.model_ref))].map((ref) => ({ ref, name: ref.slice(2), status: 'existing', model_id: 1, row_keys: [] }));
    return reply(200, {
      plan: { models, options: [], variants },
      rows: body.rows.map((r) => ({ key: r.key, variant_ref: `v:${sig(r)}`, model_ref: `m:${r.model}`, reason: '', warnings: [] })),
      counts: {
        models_missing: 0, models_existing: models.length, options_missing: 0, options_existing: 0,
        variants_missing: variants.length, variants_existing: 0, rows_resolved: body.rows.length, rows_unresolved: 0,
      },
    });
  }
  if (path.includes('/receipts/preview/')) {
    const lines = body.lines.map((l, index) => ({
      index, kind: l.kind, product: { id: l.product, name: catalogue.find((v) => v.id === l.product)?.name ?? 'x', sku: '', is_serialized: true },
      status: 'ok', errors: [], warnings: [], will_receive: l.units.length, labels: l.units.length, duplicates: [], unit_issues: [],
    }));
    return reply(200, {
      ok: true, branch: { id: body.branch, name: 'Miraflores' }, supplier: null, document_ref: body.document_ref,
      totals: { lines: lines.length, quantity_units: 0, devices: lines.reduce((n, l) => n + l.will_receive, 0), labels: lines.reduce((n, l) => n + l.labels, 0) },
      lines,
    });
  }
  if (path.includes('/receipts/confirm/')) {
    const lines = body.lines.map((l, index) => ({
      index, kind: l.kind, product: { id: l.product, name: catalogue.find((v) => v.id === l.product)?.name ?? 'x' },
      received: l.units.length, movement_id: index + 1, units: l.units.length,
      label_batch: { id: 8 + index, count: l.units.length, url: `/admin/inventory/labels?batch=${8 + index}` }, label_error: false,
    }));
    const devices = lines.reduce((n, l) => n + l.units, 0);
    return reply(201, {
      receipt_id: 'rcp-1', branch: { id: 1, name: 'Miraflores' }, supplier: null, document_ref: body.document_ref,
      totals: { lines: lines.length, quantity_units: 0, devices, labels: devices }, lines,
      label_batches: lines.map((l) => l.label_batch), label_error: false,
    });
  }
  if (path.includes('/catalog/grades/')) {
    return reply(200, { results: [{ id: 7, code: 'muy-bueno', label: 'Muy bueno', description: '', sort_order: 1, is_active: true }] });
  }
  return reply(404, { detail: 'no' });
}

const calls = (part: string) => seen.filter((c) => c.url.includes(part));

const user = () => userEvent.setup();
async function renderWizard(access: Partial<ReceivingAccess> = {}) {
  const u = user();
  await act(async () => {
    render(<ReceivingWizard access={{ ...ACCESS, ...access }} scope={SCOPE} branches={BRANCHES} defaultBranch={1} />);
  });
  return u;
}

async function toPaste(u: ReturnType<typeof user>) {
  await u.click(screen.getByRole('button', { name: /Siguiente: artículos/ }));
  await screen.findByRole('heading', { name: '¿Qué llegó?' });
  await u.click(screen.getByRole('button', { name: /^Pegar una lista/ }));
}

async function paste(text: string) {
  fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Analizar y preparar' })); });
}

const progress = () => within(screen.getByRole('list', { name: 'Avance de la lista' })).getAllByRole('listitem').map((li) => li.textContent);

let storage: jest.SpyInstance[];

beforeEach(() => {
  window.scrollTo = jest.fn();
  window.history.replaceState(null, '');
  forgetBranch();
  receipts.forgetStashedDraft();
  catalogue = [
    { id: 13, name: 'iPhone 13 128 GB Azul Seminuevo', model: 'iPhone 13', capacityGb: 128, colour: 'azul', line: 'used' },
    { id: 23, name: 'Galaxy S23 256 GB Negro Seminuevo', model: 'Galaxy S23', capacityGb: 256, colour: 'negro', line: 'used' },
  ];
  nextProduct = 100;
  seen = [];
  send.mockReset().mockImplementation(async (url, init) => route(String(url), init as RequestInit));
  suppliersApi.mockReset().mockResolvedValue({ count: 0, page: 1, page_size: 25, results: [] });
  storage = [jest.spyOn(Storage.prototype, 'setItem'), jest.spyOn(Storage.prototype, 'getItem')];
});
afterEach(() => jest.restoreAllMocks());

describe('el caso de los once seminuevos', () => {
  it('pegar, analizar, asociar solo, crear lo que falta UNA vez y confirmar una sola recepción', async () => {
    const u = await renderWizard();
    await toPaste(u);
    await paste(ELEVEN);

    // 3 + 2 asociados solos; los otros seis equipos son tres combinaciones por resolver, no seis ni once.
    await screen.findByText(/Reconocimos 11 equipos/);
    await waitFor(() => expect(progress()).toEqual([
      expect.stringMatching(/^5Listos/), expect.stringMatching(/^6Requieren asociación/), expect.stringMatching(/^0Falta completar/), expect.stringMatching(/^0Duplicados/),
    ]));
    expect(calls('/text-preview/match/')).toHaveLength(1);
    expect(calls('/text-preview/match/')[0].body.candidates).toHaveLength(5); // una pregunta por combinación
    expect(JSON.stringify(calls('/text-preview/match/')[0].body)).not.toMatch(/FAKE|\d{15}|Batería/); // nunca identificadores
    expect(screen.getAllByText('Asociado automáticamente')).toHaveLength(2);
    expect(screen.getAllByText('No existe en el catálogo')).toHaveLength(3);

    // Una aprobación crea las tres variantes.
    await u.click(screen.getByRole('button', { name: 'Proponer lo que falta' }));
    await u.click(await screen.findByLabelText('Seleccionar todo'));
    await u.click(screen.getByRole('button', { name: 'Crear lo aprobado' }));
    await screen.findByText(/Creado: 0 modelo\(s\), 0 opción\(es\), 3 variante\(s\)/);
    expect(calls('/catalog-proposal/apply/')).toHaveLength(1);
    expect(calls('/catalog-proposal/apply/')[0].headers['Idempotency-Key']).toBeTruthy();

    // Se busca de nuevo y las once quedan listas, sin perder la lista.
    await waitFor(() => expect(progress()[0]).toMatch(/^11Listos/));
    expect(progress().slice(1)).toEqual([expect.stringMatching(/^0Requieren/), expect.stringMatching(/^0Falta/), expect.stringMatching(/^0Duplicados/)]);

    await u.click(screen.getByRole('button', { name: 'Agregar 11 equipos listos a la recepción' }));
    expect(screen.queryByRole('list', { name: 'Avance de la lista' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(5);

    // Revisar y confirmar.
    await u.click(screen.getByRole('button', { name: /Siguiente: revisar/ }));
    await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
    await screen.findByText(/Entrarán 0 unidades por cantidad y 11 equipos identificados/);
    // La revisión muestra cada equipo con sus identificadores enmascarados, nunca completos.
    expect(screen.getAllByText('Ver los 3 equipos')).toHaveLength(1);
    const threeIphones = screen.getByRole('list', { name: 'Equipos de iPhone 13 128 GB Azul Seminuevo' });
    expect(within(threeIphones).getAllByRole('listitem')).toHaveLength(3);
    expect(threeIphones.textContent).toContain('serie ••••0000');
    expect(threeIphones.textContent).toContain('IMEI ••••' + IMEIS[0].slice(-4));
    expect(threeIphones.textContent).not.toContain('FAKE000000');
    expect(threeIphones.textContent).not.toContain(IMEIS[0]);
    await u.click(screen.getByLabelText(/Revisé los datos/));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });

    const confirms = calls('/receipts/confirm/');
    expect(confirms).toHaveLength(1);
    const lines = confirms[0].body.lines;
    expect(lines.map((l) => l.units.length).sort()).toEqual([2, 2, 2, 2, 3]);
    const units = lines.flatMap((l) => l.units);
    expect(units).toHaveLength(11);
    expect(new Set(units.map((x) => x.imei)).size).toBe(11);
    expect(units.every((x) => x.condition === 'used' && /^9\d$/.test(String(x.battery_health)))).toBe(true);
    expect(confirms[0].headers['Idempotency-Key']).toMatch(/^rcv-/);

    // Nada de esto se guardó en el navegador.
    for (const spy of storage) expect(JSON.stringify(spy.mock.calls)).not.toMatch(/FAKE|35000000/);
  });
});

describe('sin permiso para crear catálogo', () => {
  it('explica qué falta, no crea nada y no deja pasar con filas sin resolver', async () => {
    const u = await renderWizard({ manageProducts: false });
    await toPaste(u);
    await paste(ELEVEN);
    await screen.findByText(/Reconocimos 11 equipos/);
    await waitFor(() => expect(progress()[1]).toMatch(/^6Requieren/));
    expect(screen.getByText(/requiere permiso para gestionar productos/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Proponer lo que falta' })).not.toBeInTheDocument();
    expect(calls('/catalog-proposal/')).toHaveLength(0);
    expect(screen.getAllByText(/Tu usuario no puede crear artículos/).length).toBeGreaterThan(0);

    await u.click(screen.getByRole('button', { name: /Siguiente: revisar/ }));
    expect(await screen.findByText(/Quedan 11 filas de la lista sin agregar/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
  });

  it('puede agregar lo que ya está listo y dejar el resto pendiente, sin perderlo', async () => {
    const u = await renderWizard({ manageProducts: false });
    await toPaste(u);
    await paste(ELEVEN);
    await waitFor(() => expect(progress()[0]).toMatch(/^5Listos/));
    await u.click(screen.getByRole('button', { name: 'Agregar 5 equipos listos a la recepción' }));
    expect(screen.getAllByRole('article')).toHaveLength(2);
    expect(progress()[1]).toMatch(/^6Requieren/);
    expect(screen.getByText(/Quedan 6 filas en la lista/)).toBeInTheDocument();
  });
});

describe('datos de cada equipo', () => {
  it('«Resolver pendientes» lleva al primer problema accionable y el lector avanza de campo en campo', async () => {
    const u = await renderWizard();
    await toPaste(u);
    const bare = ['iPhone 13 128 GB azul\nBatería 90%', 'iPhone 13 128 GB azul\nBatería 91%'].join('\n\n');
    await paste(bare);
    await waitFor(() => expect(progress()[1]).toMatch(/^0Requieren/));
    expect(progress()[2]).toMatch(/^2Falta completar/);

    await u.click(screen.getByRole('button', { name: 'Resolver pendientes' }));
    const firstSerial = (await screen.findAllByLabelText(/Número de serie/))[0];
    await waitFor(() => expect(firstSerial).toHaveFocus());

    // El lector escribe la serie y pulsa Enter: el cursor pasa al IMEI del mismo equipo, luego al siguiente equipo.
    await u.keyboard('SN-AAA1{Enter}');
    const imeiBox = screen.getAllByLabelText(/^IMEI \*/)[0];
    await waitFor(() => expect(imeiBox).toHaveFocus());
    await u.keyboard(`${IMEIS[0]}{Enter}`);
    const secondSerial = screen.getAllByLabelText(/Número de serie/)[1];
    await waitFor(() => expect(secondSerial).toHaveFocus());
    await u.keyboard(`SN-AAA2{Enter}${IMEIS[1]}{Enter}`);
    await waitFor(() => expect(progress()[0]).toMatch(/^2Listos/));
  });

  it('un IMEI repetido o inválido bloquea ESA fila con su motivo y no al resto', async () => {
    const u = await renderWizard();
    await toPaste(u);
    const text = [block('iPhone 13 128 GB azul', 0), block('iPhone 13 128 GB azul', 1).replace(IMEIS[1], IMEIS[0])].join('\n\n');
    await paste(text);
    await waitFor(() => expect(progress()[3]).toMatch(/^2Duplicados/));
    await u.click(screen.getByRole('button', { name: 'Resolver pendientes' }));
    expect((await screen.findAllByText(/IMEI está repetido en la recepción/)).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Nada listo todavía' })).toBeDisabled();
  });

  it('el estado estético y la batería viajan sólo si se escribieron; un seminuevo los ofrece y uno nuevo no', async () => {
    const u = await renderWizard();
    await toPaste(u);
    await paste(block('iPhone 13 128 GB azul', 0));
    await waitFor(() => expect(progress()[0]).toMatch(/^1Listos/));
    await u.click(screen.getByText(/^Equipo 1/));
    expect(await screen.findByLabelText('Batería medida (%)')).toHaveValue('90');
    await u.selectOptions(await screen.findByLabelText('Estado estético'), 'Muy bueno');
    fireEvent.change(screen.getByLabelText('Observaciones (opcional)'), { target: { value: 'rayón leve' } });
    await u.click(screen.getByRole('button', { name: /Agregar 1 equipo listo/ }));
    await u.click(screen.getByRole('button', { name: /Siguiente: revisar/ }));
    await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
    await u.click(screen.getByLabelText(/Revisé los datos/));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    expect(calls('/receipts/confirm/')[0].body.lines[0].units[0]).toEqual({
      serial: 'FAKE000000', imei: IMEIS[0], condition: 'used', battery_health: '90', cosmetic_grade: 7, notes: 'rayón leve',
    });
  });

  it('en una lista de nuevos, un grupo de seminuevos se corrige de una vez y se busca de nuevo', async () => {
    const u = await renderWizard();
    await toPaste(u);
    fireEvent.change(screen.getByLabelText('Línea declarada'), { target: { value: 'new' } });
    await paste([block('iPhone 13 128 GB azul', 0), block('iPhone 13 128 GB azul', 1)].join('\n\n'));
    await screen.findByText('No existe en el catálogo');
    await u.click(screen.getByRole('button', { name: 'Son seminuevos' }));
    await waitFor(() => expect(progress()[0]).toMatch(/^2Listos/));
    expect(calls('/text-preview/match/')).toHaveLength(2);
  });
});

describe('la búsqueda del catálogo falla', () => {
  it('lo dice, no pierde la lista y permite buscar de nuevo', async () => {
    let calls_ = 0;
    send.mockImplementation(async (url, init) => {
      if (String(url).includes('/text-preview/match/') && calls_++ === 0) return reply(503, { detail: 'No se pudo consultar el catálogo.' });
      return route(String(url), init as RequestInit);
    });
    const u = await renderWizard();
    await toPaste(u);
    await paste(block('iPhone 13 128 GB azul', 0));
    expect(await screen.findByText(/No se pudo consultar el catálogo/)).toBeInTheDocument();
    expect(progress()[1]).toMatch(/^1Requieren/);
    await u.click(screen.getByRole('button', { name: 'Buscar de nuevo' }));
    await waitFor(() => expect(progress()[0]).toMatch(/^1Listos/));
  });
});

describe('seguridad de la memoria', () => {
  it('la lista pegada vive con el borrador de quien la escribió y se entrega sólo a esa persona en esa empresa', () => {
    receipts.stashDraft({ branch: 1, supplier: null, document_ref: '', notes: '' }, [], SCOPE, [{ id: 1 }]);
    expect(receipts.takeStashedDraft({ user: 2, company: 7 })).toBeNull();
    receipts.stashDraft({ branch: 1, supplier: null, document_ref: '', notes: '' }, [], SCOPE, [{ id: 1 }]);
    expect(receipts.takeStashedDraft({ user: 1, company: 8 })).toBeNull();
    receipts.stashDraft({ branch: 1, supplier: null, document_ref: '', notes: '' }, [], SCOPE, [{ id: 1 }]);
    expect(receipts.takeStashedDraft(SCOPE)?.staged).toEqual([{ id: 1 }]);
    expect(receipts.takeStashedDraft(SCOPE)).toBeNull();
  });
});
