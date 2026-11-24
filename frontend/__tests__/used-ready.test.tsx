import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { UsedUnitsPanel, publicationBlockers, warrantyText } from '@/app/admin/components/UsedUnitsPanel';
import { UsedUnitCardV4 } from '@/app/components/v4/UsedUnitCardV4';
import { fetchWithAuth } from '@/app/lib/auth';
import { unitWarrantyText, type UsedUnit } from '@/app/lib/catalog-models';
import type { StockUnit } from '@/app/lib/stock-units';

import { reply } from './support/device-commerce-fixtures';

/**
 * USED-READY-01 — a used device is sold as THAT device: the panel shows and decides its inspection and its
 * publication (the server says why a publication is refused), and the shop's card says what was stated about it.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown };
let writes: Call[];

const BASE = {
  id: 7, product_id: 52, product_name: 'Equipo X seminuevo', branch_id: 1, branch_name: 'Central',
  serial_number: 'SER123', imei: '356789012345678', imei2: null, condition: 'used', condition_label: 'Usado',
  status: 'available', status_label: 'Disponible', received_at: '2026-10-01T10:00:00Z', sold_at: null, order_id: null,
  notes: '', public_id: 'abc', battery_health: 90, cosmetic_grade: null, cosmetic_grade_label: '', public_notes: '',
  sale_price: '1999.00', condition_line: 'used', photos: [] as { id: number; url: string; alt_text: string }[],
  inspection_status: 'pending', inspection_label: 'Pendiente de inspección', inspection_note: '', inspected_at: null,
  published_online: false, published_at: null, warranty_days: null, functional_state: '', defects: '', accessories: '',
  web: { offered: false, reasons: ['not_inspected', 'unit_not_published', 'no_photo'] },
};
let unit: typeof BASE;

beforeEach(() => {
  writes = [];
  unit = { ...BASE };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      const body = JSON.parse(String(init.body ?? '{}'));
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body });
      if (url.endsWith('/inspection/')) return reply({ ...unit, inspection_status: body.decision, inspection_note: body.note });
      if (url.endsWith('/publication/')) return reply({ ...unit, published_online: body.published });
      return reply({ ...unit, ...body });
    }
    if (url.includes('/admin/catalog/grades/')) return reply([]);
    if (url.includes('/admin/inventory/units/')) {
      return reply({ results: [unit], count: 1, page: 1, page_size: 25, statuses: [], conditions: [], can_inspect: false });
    }
    return reply({}, 404);
  });
});

async function openDialog(props: Partial<React.ComponentProps<typeof UsedUnitsPanel>> = {}) {
  render(<UsedUnitsPanel branch="all" canEdit canSeeCost={false} {...props} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Editar SER123' }));
  return screen.getByRole('dialog', { name: 'Equipo X seminuevo' });
}

describe('la lista', () => {
  it('muestra la inspección de cada equipo y filtra por ella', async () => {
    render(<UsedUnitsPanel branch="all" canEdit canSeeCost={false} />);
    const row = (await screen.findByText('Equipo X seminuevo')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Pendiente de inspección')).toBeInTheDocument();
    expect(within(row).getByText('No · su inspección está pendiente')).toBeInTheDocument();
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Inspección'), { target: { value: 'rejected' } });
    });
    const last = send.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('/inventory/units/')).pop()!;
    expect(last).toContain('inspection=rejected');
  });
});

describe('la inspección', () => {
  it('quien puede inspeccionar aprueba, y la petición va a su propia ruta', async () => {
    const dialog = await openDialog({ canInspect: true });
    expect(within(dialog).getByTestId('unit-inspection')).toHaveTextContent('Pendiente de inspección');
    fireEvent.change(within(dialog).getByLabelText(/Nota de la inspección/), { target: { value: 'Todo bien' } });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Aprobar inspección' })); });
    expect(writes).toEqual([
      { method: 'POST', path: '/admin/inventory/units/7/inspection/', body: { decision: 'approved', note: 'Todo bien' } },
    ]);
    expect(within(dialog).getByTestId('unit-inspection')).toHaveTextContent('Inspección aprobada');
  });

  it('rechazar sin nota no se envía', async () => {
    const dialog = await openDialog({ canInspect: true });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Rechazar inspección' })); });
    expect(writes).toEqual([]);
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Indica por qué se rechaza la inspección.');
  });

  it('sin el permiso no hay botones, y se dice de quién es', async () => {
    const dialog = await openDialog();
    expect(within(dialog).queryByRole('button', { name: 'Aprobar inspección' })).toBeNull();
    expect(dialog).toHaveTextContent('Inspeccionar seminuevos');
  });
});

describe('la publicación', () => {
  it('sin inspección ni fotos no se ofrece publicar, y se dice qué falta', async () => {
    const dialog = await openDialog({ canManagePrice: true });
    expect(within(dialog).queryByRole('button', { name: 'Publicar este equipo' })).toBeNull();
    expect(within(dialog).getByTestId('publication-blockers'))
      .toHaveTextContent('la inspección aprobada, al menos una foto real del equipo');
  });

  it('un equipo listo se publica y se retira', async () => {
    unit = { ...BASE, inspection_status: 'approved', photos: [{ id: 1, url: 'http://localhost/p.png', alt_text: '' }] };
    const dialog = await openDialog({ canManagePrice: true });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Publicar este equipo' })); });
    expect(writes[0]).toEqual({ method: 'POST', path: '/admin/inventory/units/7/publication/', body: { published: true } });
    expect(within(dialog).getByTestId('unit-publication')).toHaveTextContent('Publicado en línea');
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Retirar de la tienda en línea' })); });
    expect(writes[1].body).toEqual({ published: false });
  });

  it('sin gestionar productos no se publica', async () => {
    const dialog = await openDialog();
    expect(dialog).toHaveTextContent('Publicar o retirar un equipo pide gestionar productos y ajustar inventario.');
  });

  it('la regla del panel es la del servidor', () => {
    const ready = { ...BASE, inspection_status: 'approved', photos: [{ id: 1, url: 'x', alt_text: '' }] } as unknown as StockUnit;
    expect(publicationBlockers(ready)).toEqual([]);
    expect(publicationBlockers({ ...ready, sale_price: null })).toEqual(['precio de venta']);
    expect(publicationBlockers({ ...ready, inspection_status: 'rejected' })).toEqual(['una inspección aprobada (fue rechazada)']);
    expect(publicationBlockers({ ...ready, status: 'sold' })).toEqual(['ya no está en la tienda']);
    expect(publicationBlockers(ready, 0)).toEqual(['al menos una foto real del equipo']);
  });
});

describe('lo que el comprador lee', () => {
  it('estado funcional, defectos, accesorios y garantía se guardan con lo que cambió', async () => {
    const dialog = await openDialog({ canManagePrice: true });
    fireEvent.change(within(dialog).getByLabelText('Estado funcional'), { target: { value: 'with_faults' } });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    expect(writes).toEqual([]);
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Un equipo con fallas dice cuáles.');
    fireEvent.change(within(dialog).getByLabelText(/^Defectos/), { target: { value: 'Botón duro' } });
    fireEvent.change(within(dialog).getByLabelText('Accesorios incluidos'), { target: { value: 'Caja' } });
    fireEvent.change(within(dialog).getByLabelText(/Garantía \(días\)/), { target: { value: '90' } });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    expect(writes[0]).toEqual({
      method: 'PATCH', path: '/admin/inventory/units/7/',
      body: { functional_state: 'with_faults', defects: 'Botón duro', accessories: 'Caja', warranty_days: 90 },
    });
  });

  it('la garantía se lee en palabras', () => {
    expect(warrantyText(null)).toBe('Según la política de garantía de la empresa');
    expect(warrantyText(0)).toBe('Sin garantía');
    expect(warrantyText(90)).toBe('90 días de garantía');
    expect(unitWarrantyText(null)).toBeNull();
    expect(unitWarrantyText(1)).toBe('1 día de garantía');
  });

  it('la tarjeta del equipo dice sus fallas y su garantía, nada inventado', () => {
    const card: UsedUnit = {
      id: 'u1', product_id: 52, options: { Capacidad: '128 GB' }, battery_health: null, cosmetic_grade: null,
      price: '1999.00', photos: [{ url: 'http://localhost/p.png', alt_text: '', width: 10, height: 10 }], notes: '',
      imei_hint: '•••• 5678', warranty_days: 90, warranty_source: 'unit', functional_state: 'with_faults',
      functional_state_label: 'Con fallas declaradas', defects: 'Botón duro', accessories: 'Caja',
    };
    const { rerender } = render(<UsedUnitCardV4 unit={card} modelName="Equipo X" selected={false} onSelect={() => {}} />);
    const button = screen.getByRole('button');
    expect(button).toHaveTextContent('Con fallas declaradas');
    expect(button).toHaveTextContent('90 días de garantía');
    expect(button).not.toHaveTextContent('Batería');
    rerender(<UsedUnitCardV4 unit={{ ...card, warranty_days: null, functional_state: null }} modelName="Equipo X"
      selected={false} onSelect={() => {}} />);
    expect(screen.getByRole('button')).not.toHaveTextContent('garantía');
    expect(screen.getByRole('button')).not.toHaveTextContent('fallas');
  });
});
