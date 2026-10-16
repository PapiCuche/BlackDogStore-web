/**
 * COMMERCE-ADMIN-UX-01 — «Equipos nuevos» es «Equipos» abierto en la línea
 * nueva (`/admin/inventory/units?line=new`), y el enlace «Ver los equipos» del
 * registro lo abre en su producto (`?product=41`). La pantalla toma el filtro
 * de la dirección; sin él, como siempre.
 */
import { render, waitFor } from '@testing-library/react';

import { StockUnitsPanel } from '@/app/admin/components/StockUnitsPanel';
import { unitFiltersFromSearch } from '@/app/admin/components/StockUnitsPanel';
import { fetchWithAuth } from '@/app/lib/auth';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const reply = (body: unknown) => ({ ok: true, status: 200, json: async () => body } as Response);

beforeEach(() => {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/admin/catalog/')) return reply({ results: [] });
    if (url.includes('/units/products/')) return reply({ results: [] });
    return reply({ results: [], count: 0, page: 1, page_size: 25, statuses: [], conditions: [] });
  });
});

const listCalls = () => send.mock.calls.map(([u]) => String(u))
  .filter((u) => /\/admin\/inventory\/units\/(\?|$)/.test(u));

it('lee la línea y el producto de la dirección, y nada más', () => {
  expect(unitFiltersFromSearch('?line=new')).toEqual({ line: 'new' });
  expect(unitFiltersFromSearch('?line=used&product=41')).toEqual({ line: 'used', product: '41' });
  expect(unitFiltersFromSearch('?line=raro&product=x&status=sold')).toEqual({});
  expect(unitFiltersFromSearch('')).toEqual({});
});

it('abre la lista ya filtrada', async () => {
  render(<StockUnitsPanel branch="all" branches={[]} canAdjust={false} initialFilters={{ line: 'new', product: '41' }} />);
  await waitFor(() => expect(listCalls().length).toBeGreaterThan(0));
  expect(listCalls()[0]).toContain('line=new');
  expect(listCalls()[0]).toContain('product=41');
});
