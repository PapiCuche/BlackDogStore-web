import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { UsedUnitsPanel } from '@/app/admin/components/UsedUnitsPanel';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * STOCK-UX-WALK follow-up — «Equipos seminuevos» is where every real used device is inspected, photographed, priced
 * and published. In a real browser its only action («Editar») sat at the far end of an 11-column table, off-screen at
 * 1440 px and at 390 px, and the «Pendiente de inspección» pill broke over three lines.
 *
 *   · the table opens with the action, so it is in view without scrolling sideways;
 *   · on a phone each device is a card (the `SupplierWeeklyPanel` pattern: cards below `md`, the table from `md`),
 *     with its own full-width button;
 *   · the pills never wrap.
 *
 * jsdom applies no CSS: both layouts are in the DOM here, and each is read inside its own container.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const UNIT = {
  id: 7, product_id: 52, product_name: 'iPhone 13 128 GB Medianoche Seminuevo', branch_id: 1, branch_name: 'Central',
  serial_number: '', imei: '356789012345678', imei2: null, condition: 'used', condition_label: 'Usado',
  status: 'available', status_label: 'Disponible', received_at: '2026-10-01T10:00:00Z', sold_at: null, order_id: null,
  notes: '', public_id: 'abc', battery_health: 89, cosmetic_grade: null, cosmetic_grade_label: '', public_notes: '',
  sale_price: null, condition_line: 'used', photos: [],
  inspection_status: 'pending', inspection_label: 'Pendiente de inspección', inspection_note: '', inspected_at: null,
  published_online: false, published_at: null, warranty_days: null, functional_state: '', defects: '', accessories: '',
  web: { offered: false, reasons: ['no_price', 'not_inspected', 'unit_not_published', 'no_photo'] },
};

beforeEach(() => {
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/admin/catalog/grades/')) return reply([]);
    if (url.includes('/admin/inventory/units/')) {
      return reply({ results: [UNIT], count: 1, page: 1, page_size: 25, statuses: [], conditions: [], can_inspect: true });
    }
    return reply({}, 404);
  });
});

async function mount() {
  render(<UsedUnitsPanel branch="all" canEdit canSeeCost={false} canManagePrice />);
  return {
    table: await screen.findByTestId('used-units-table'),
    cards: await screen.findByTestId('used-units-cards'),
  };
}

it('the table opens with the action: «Editar» is the first column, in view without scrolling sideways', async () => {
  const { table } = await mount();
  const headers = within(table).getAllByRole('columnheader').map((th) => th.textContent);
  expect(headers[0]).toBe('Acciones');
  const [, row] = within(table).getAllByRole('row');
  const firstCell = within(row).getAllByRole('cell')[0];
  expect(within(firstCell).getByRole('button', { name: 'Editar IMEI 356789012345678' })).toBeInTheDocument();
});

it('on a phone each device is a card with what matters and its own button that opens the ficha', async () => {
  const { cards } = await mount();
  const [card] = within(cards).getAllByRole('listitem');
  expect(card).toHaveTextContent('iPhone 13 128 GB Medianoche Seminuevo');
  expect(card).toHaveTextContent('356789012345678');
  expect(card).toHaveTextContent('Disponible');
  expect(card).toHaveTextContent('Pendiente de inspección');
  expect(card).toHaveTextContent('Sin precio (pendiente)');
  expect(card).toHaveTextContent('89%');
  expect(card).toHaveTextContent('No · no tiene precio de venta');
  expect(card).toHaveTextContent('0/0');
  const button = within(card).getByRole('button', { name: 'Editar IMEI 356789012345678' });
  expect(button.className).toMatch(/min-h-11/);
  await act(async () => { fireEvent.click(button); });
  expect(screen.getByRole('dialog', { name: 'iPhone 13 128 GB Medianoche Seminuevo' })).toBeInTheDocument();
});

it('the layouts swap at md: cards below it, the table from it', async () => {
  const { table, cards } = await mount();
  expect(cards.className).toMatch(/\bmd:hidden\b/);
  expect(table.className).toMatch(/\bhidden\b/);
  expect(table.className).toMatch(/\bmd:block\b/);
});

it('the status and inspection pills stay on one line', async () => {
  const { table, cards } = await mount();
  for (const scope of [table, cards]) {
    expect(within(scope).getByText('Pendiente de inspección').className).toMatch(/whitespace-nowrap/);
    expect(within(scope).getByText('Disponible').className).toMatch(/whitespace-nowrap/);
  }
});
