/**
 * INV-INTAKE-BULK-01 — Ctrl/⌘+Enter en el paso «Artículos» lleva a «Revisar y confirmar» cuando todo está completo;
 * con datos que faltan se queda y los marca, como el botón «Siguiente: revisar». Sin red.
 */
import React from 'react';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ReceivingWizard, type ReceivingAccess } from '@/app/admin/components/ReceivingWizard';
import * as receipts from '@/app/admin/lib/inventory-receipts';
import { forgetBranch } from '@/app/admin/lib/last-branch';
import * as internalApi from '@/app/admin/lib/internal-api';
import * as admin from '@/app/lib/admin';
import { fetchWithAuth } from '@/app/lib/auth';

jest.mock('next/navigation', () => ({
  usePathname: () => '/admin/inventory/receive',
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), refresh: jest.fn(), back: jest.fn(), forward: jest.fn(), prefetch: jest.fn() }),
}));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ ...jest.requireActual('@/app/lib/admin'), fetchAdminProducts: jest.fn() }));
jest.mock('@/app/admin/lib/internal-api', () => ({ ...jest.requireActual('@/app/admin/lib/internal-api'), fetchSuppliers: jest.fn() }));
jest.mock('@/app/admin/lib/inventory-receipts', () => ({
  ...jest.requireActual('@/app/admin/lib/inventory-receipts'), previewReceipt: jest.fn(), confirmReceipt: jest.fn(),
}));

const search = admin.fetchAdminProducts as jest.Mock;
const preview = receipts.previewReceipt as jest.Mock;

const ACCESS: ReceivingAccess = { costs: false, manageProducts: false, viewProducts: true, labels: true };
const BRANCHES = [{ id: 1, name: 'Miraflores' }];

function product(id: number, name: string, over: Partial<admin.AdminProduct> = {}): admin.AdminProduct {
  return {
    id, name, slug: `p-${id}`, description: '', price: '10.00', inventory: 0, image_url: '', category_id: 1,
    category_name: 'x', is_active: true, is_published_online: false, publication_problems: [],
    is_serialized: false, requires_imei: false, sale_mode: 'stock', condition_line: 'new', product_model_id: null,
    primary_code: `SKU-${id}`, search_exact: true, created_at: '', updated_at: '', ...over,
  } as admin.AdminProduct;
}
const CABLE = product(9, 'Cable USB-C 1m');
const PHONE = product(5, 'iPhone 15 128GB', { is_serialized: true, requires_imei: true });
const page = (rows: admin.AdminProduct[]) => ({ count: rows.length, page: 1, page_size: 8, results: rows });

async function toArticles() {
  const u = userEvent.setup();
  await act(async () => {
    render(<ReceivingWizard access={ACCESS} scope={{ user: 1, company: 7 }} branches={BRANCHES} defaultBranch={1} />);
  });
  await u.click(screen.getByRole('button', { name: /Siguiente: artículos/ }));
  await screen.findByRole('heading', { name: '¿Qué llegó?' });
  return u;
}

async function addProduct(u: ReturnType<typeof userEvent.setup>, p: admin.AdminProduct) {
  search.mockResolvedValueOnce(page([p]));
  await u.type(screen.getByRole('combobox', { name: /Buscar el artículo/ }), `${p.name.slice(0, 4)}{Enter}`);
  await screen.findByRole('article', { name: p.name });
}

beforeEach(() => {
  window.scrollTo = jest.fn();
  window.history.replaceState(null, '');
  forgetBranch();
  receipts.forgetStashedDraft();
  search.mockReset();
  preview.mockReset().mockImplementation(async (body: receipts.ReceiptBody) => ({
    ok: true, branch: { id: 1, name: 'Miraflores' }, supplier: null, document_ref: '',
    totals: { lines: body.lines.length, quantity_units: 1, devices: 0, labels: 1 },
    lines: body.lines.map((l, index) => ({
      index, kind: l.kind, product: { id: l.product, name: CABLE.name, sku: 'SKU-9', is_serialized: false },
      status: 'ok', errors: [], warnings: [], will_receive: 1, labels: 1, duplicates: [],
    })),
  }));
  (fetchWithAuth as jest.Mock).mockReset().mockImplementation(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    const body = !String(url).includes('/receipts/draft/') ? null
      : method === 'GET' ? { draft: null } : method === 'PUT' ? { draft: { branch: null, updated_at: 'v1', expires_at: '' } } : {};
    return { ok: body !== null, status: body === null ? 404 : method === 'DELETE' ? 204 : 200, json: async () => body ?? {} };
  });
  (internalApi.fetchSuppliers as jest.Mock).mockReset().mockResolvedValue({ count: 0, page: 1, page_size: 1, results: [] });
});

describe('atajo Ctrl/⌘+Enter', () => {
  it('se anuncia en la ayuda del paso y en el botón', async () => {
    await toArticles();
    expect(screen.getByText(/Ctrl \+ Enter \(⌘ \+ Enter en Mac\)/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Siguiente: revisar/ })).toHaveAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter');
  });

  it('Ctrl+Enter desde la cantidad abre la revisión cuando todo está completo', async () => {
    const u = await toArticles();
    await addProduct(u, CABLE);
    screen.getByLabelText('Cantidad').focus();
    await u.keyboard('{Control>}{Enter}{/Control}');
    expect(await screen.findByRole('heading', { name: 'Revisa antes de registrar' })).toBeInTheDocument();
    expect(preview).toHaveBeenCalledTimes(1);
  });

  it('⌘+Enter también', async () => {
    const u = await toArticles();
    await addProduct(u, CABLE);
    screen.getByLabelText('Cantidad').focus();
    await u.keyboard('{Meta>}{Enter}{/Meta}');
    expect(await screen.findByRole('heading', { name: 'Revisa antes de registrar' })).toBeInTheDocument();
  });

  it('con datos que faltan se queda, los marca y no agrega una fila vacía', async () => {
    const u = await toArticles();
    await addProduct(u, PHONE);
    const card = screen.getByRole('article', { name: PHONE.name });
    const serial = within(card).getByLabelText(/Número de serie/);
    await u.type(serial, 'A1B2');
    await u.keyboard('{Control>}{Enter}{/Control}');
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
    expect(screen.getByText(/Falta el IMEI: este equipo lo necesita/)).toBeInTheDocument();
    expect(within(card).getAllByLabelText(/Número de serie/)).toHaveLength(1);
    expect(preview).not.toHaveBeenCalled();
  });

  it('Enter solo sigue con la cadena del lector (no abre la revisión)', async () => {
    const u = await toArticles();
    await addProduct(u, CABLE);
    screen.getByLabelText('Cantidad').focus();
    await u.keyboard('{Enter}');
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
    expect(preview).not.toHaveBeenCalled();
  });

  it('dentro de «Pegar varios» revisa lo pegado y no sale del paso (nada pegado se pierde)', async () => {
    const u = await toArticles();
    await addProduct(u, PHONE);
    const card = screen.getByRole('article', { name: PHONE.name });
    await u.click(within(card).getByRole('button', { name: 'Pegar varios' }));
    const box = within(card).getByLabelText(/Series o IMEI, uno por línea/);
    await u.click(box);
    await u.paste('F2LXK1ABCD12');
    await u.keyboard('{Control>}{Enter}{/Control}');
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
    expect(within(card).getByRole('region', { name: 'Vista previa de lo pegado' })).toBeInTheDocument();
    expect(box).toHaveValue('F2LXK1ABCD12');
    expect(preview).not.toHaveBeenCalled();
  });
});
