import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { StockUnitImport } from '@/app/admin/components/StockUnitImport';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * IMPORT-TERMS-APPROVAL · precios y garantías de seminuevos en una carga de equipos.
 *
 * Quien no administra productos puede registrar los equipos SIN los precios ni las garantías del archivo, o esperar a
 * que alguien que administra productos los apruebe. Quien los administra ve «Aprobar precios y garantías». La pantalla
 * enseña quién preparó y quién aprobó; el servidor decide todo lo demás.
 */

jest.mock('@/app/lib/auth', () => ({ ...jest.requireActual('@/app/lib/auth'), fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const BRANCHES = [{ id: 1, name: 'Centro' }];

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: 41, import_type: 'units', status: 'previewed', original_filename: 'stock-seminuevos.xlsx',
    counts: { total: 1, create: 1, update: 0, no_change: 0, skip: 0, error: 0 },
    summary: { units: 1 }, is_applicable: true, rows_truncated: false,
    options: { line: 'used', entry_kind: 'purchase' }, created_by: 'ana', applied_by: '',
    can_update_prices: false, carries_commercial_terms: true, commercial_terms_rows: 1, terms_approval: null,
    rows: [
      { sheet: 'Seminuevos', row: 2, action: 'create', match_key: 'SN1', errors: [], warnings: [],
        data: { name: 'iPhone 13 128 GB Azul', branch: 'Centro', serial_number: 'SN1', imei: '356938035643809',
          condition: 'used' } },
    ],
    ...overrides,
  };
}

type Call = { method: string; url: string; body: unknown };
let calls: Call[];
let previewBody: unknown;
let detailBody: unknown;
let approveBody: unknown;
let applyBody: unknown;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  calls = [];
  previewBody = job();
  detailBody = job();
  approveBody = job({ can_update_prices: true, terms_approval: { by_name: 'Bruno', at: '2026-11-25T10:00:00Z', valid: true } });
  applyBody = job({ status: 'applied', is_applicable: false,
    summary: { units: 1, applied: { units: 1, movements: 1, commercial_terms: 'dropped' } } });
  window.history.pushState({}, '', '/admin/inventory/units/import');
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const body = typeof init.body === 'string' ? JSON.parse(init.body) : null;
    calls.push({ method: (init.method ?? 'GET').toUpperCase(), url, body });
    if (url.includes('/import/preview/')) return reply(201, previewBody);
    if (url.includes('/approve-terms/')) return reply(200, approveBody);
    if (url.includes('/apply/')) return reply(200, applyBody);
    if (url.includes('/admin/imports/41/')) return reply(200, detailBody);
    return reply(404, { detail: 'No encontrado.' });
  });
});

async function previewed() {
  render(<StockUnitImport branches={BRANCHES} />);
  const file = new File(['x'], 'stock-seminuevos.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  fireEvent.change(screen.getByLabelText('Archivo de equipos (.xlsx)'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('radio', { name: 'Seminuevos' }));
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  return screen.findByRole('group', { name: 'Precios y garantías del archivo' });
}

test('without products.manage and without an approval: the block says so and offers the operational entry', async () => {
  const block = await previewed();
  expect(block).toHaveTextContent(
    'Esta carga trae precios o garantías de seminuevos. Necesita la aprobación de quien administra productos.');
  expect(block).toHaveTextContent('Preparó: ana');
  expect(within(block).getByText('/admin/inventory/units/import?job=41')).toBeInTheDocument();
  // Registering WITH the terms is not offered: the server would refuse it.
  expect(screen.queryByRole('button', { name: 'Registrar 1 equipo' })).not.toBeInTheDocument();
  expect(within(block).queryByRole('button', { name: 'Aprobar precios y garantías' })).not.toBeInTheDocument();

  fireEvent.click(within(block).getByRole('button', { name: 'Registrar sin precios ni garantías' }));
  await waitFor(() => expect(calls.some((c) => c.url.includes('/apply/'))).toBe(true));
  const apply = calls.find((c) => c.url.includes('/apply/'));
  expect(apply).toMatchObject({ method: 'POST', body: { without_commercial_terms: true } });
  expect(await screen.findByRole('note', { name: 'Sin precios ni garantías' })).toHaveTextContent(
    'sin los precios ni las garantías del archivo');
});

test('a manager approves the terms and sees who approved them', async () => {
  previewBody = job({ can_update_prices: true });
  const block = await previewed();
  expect(block).not.toHaveTextContent('Necesita la aprobación');
  // The manager may still register directly.
  expect(screen.getByRole('button', { name: 'Registrar 1 equipo' })).toBeInTheDocument();
  fireEvent.click(within(block).getByRole('button', { name: 'Aprobar precios y garantías' }));
  await waitFor(() => expect(calls.some((c) => c.url.includes('/approve-terms/'))).toBe(true));
  expect(calls.find((c) => c.url.includes('/approve-terms/'))?.method).toBe('POST');
  expect(await within(block).findByText(/Aprobados por Bruno/)).toBeInTheDocument();
  expect(within(block).queryByRole('button', { name: 'Aprobar precios y garantías' })).not.toBeInTheDocument();
  // The rows of the preview are still on screen after the approval.
  expect(screen.getByRole('table', { name: 'Equipos del archivo' })).toBeInTheDocument();
});

test('a load reopened by its link, already approved, is registered with its terms', async () => {
  window.history.pushState({}, '', '/admin/inventory/units/import?job=41');
  detailBody = job({ terms_approval: { by_name: 'Bruno', at: '2026-11-25T10:00:00Z', valid: true } });
  applyBody = job({ status: 'applied', is_applicable: false,
    summary: { units: 1, applied: { units: 1, movements: 1, commercial_terms: 'applied' } } });
  render(<StockUnitImport branches={BRANCHES} />);
  const block = await screen.findByRole('group', { name: 'Precios y garantías del archivo' });
  expect(calls[0]).toMatchObject({ method: 'GET' });
  expect(calls[0].url).toContain('/admin/imports/41/');
  expect(block).toHaveTextContent('Aprobados por Bruno');
  expect(block).toHaveTextContent('Preparó: ana');
  fireEvent.click(screen.getByRole('button', { name: 'Registrar 1 equipo' }));
  await waitFor(() => expect(calls.some((c) => c.url.includes('/apply/'))).toBe(true));
  expect(calls.find((c) => c.url.includes('/apply/'))?.body).toEqual({});
});

test('an approval that no longer holds is said, and only the operational entry is offered', async () => {
  previewBody = job({ terms_approval: { by_name: 'Bruno', at: '2026-11-25T10:00:00Z', valid: false } });
  const block = await previewed();
  expect(block).toHaveTextContent('La aprobación de Bruno ya no vale');
  expect(screen.queryByRole('button', { name: 'Registrar 1 equipo' })).not.toBeInTheDocument();
  expect(within(block).getByRole('button', { name: 'Registrar sin precios ni garantías' })).toBeInTheDocument();
});

test('a load without prices or warranties shows no block', async () => {
  previewBody = job({ carries_commercial_terms: false, commercial_terms_rows: 0 });
  render(<StockUnitImport branches={BRANCHES} />);
  const file = new File(['x'], 'stock-seminuevos.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  fireEvent.change(screen.getByLabelText('Archivo de equipos (.xlsx)'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('radio', { name: 'Seminuevos' }));
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  expect(await screen.findByRole('button', { name: 'Registrar 1 equipo' })).toBeInTheDocument();
  expect(screen.queryByRole('group', { name: 'Precios y garantías del archivo' })).not.toBeInTheDocument();
});
