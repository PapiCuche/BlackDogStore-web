import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { OrderSupplyPanel } from '@/app/admin/components/OrderSupplyPanel';
import { ProductSaleModeControl } from '@/app/admin/components/ProductSaleModeControl';
import { SupplierOffersManager } from '@/app/admin/components/SupplierOffersManager';
import { SuppliersManager } from '@/app/admin/components/SuppliersManager';
import { OrdersTable } from '@/app/admin/components/OrdersTable';
import { INTERNAL_MODULES } from '@/app/admin/lib/internal-modules';
import type { AdminOrder, AdminOrderDetail } from '@/app/lib/admin';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * DEVICE-COMMERCE-01 · DC3 — el panel: proveedores, ofertas (con el costo sólo
 * cuando el servidor lo manda), modo de venta y el abastecimiento de un pedido.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown };
let writes: Call[];
let offers: Record<string, unknown>[];
let order: AdminOrderDetail;

const SUPPLIER = {
  id: 5, name: 'Distribuidora Andina', document_type: 'ruc', document_number: '20123456789', contact_name: 'Rosa',
  phone: '999888777', email: 'ventas@andina.pe', notes: '', is_active: true, offer_count: 2,
};

const offerRow = (extra: Record<string, unknown> = {}) => ({
  id: 31, supplier_id: 5, supplier_name: 'Distribuidora Andina', product_id: 44, product_name: 'Equipo X 256 Blanco',
  product_slug: 'equipo-x-44', commercial_price: '4699.00', confirmed_quantity: 3, committed_quantity: 1, remaining: 2,
  valid_from: '2026-10-01', valid_until: '2026-10-31', lead_time_days: 7, is_active: true, deactivated_reason: '',
  state: 'active', updated_at: '2026-10-10T10:00:00Z', ...extra,
});

function orderWith(itemExtra: Record<string, unknown>, flags: Partial<AdminOrderDetail> = {}): AdminOrderDetail {
  return {
    id: 900, items: [
      { id: 1, product_id: 4, product_name: 'Funda', quantity: 1, price: '50.00', subtotal: '50.00', supply_origin: 'own', supply_status: '' },
      {
        id: 2, product_id: 44, product_name: 'Equipo X 256 Blanco', quantity: 2, price: '4699.00', subtotal: '9398.00',
        supply_origin: 'supplier', supply_status: 'pending', supply_note: '', lead_time_days: 7, supplier_offer_id: 31,
        supplier_name: 'Distribuidora Andina', product_is_serialized: false, ...itemExtra,
      },
    ],
    supply_status: String(itemExtra.supply_status ?? 'pending'),
    can_manage_supply: false,
    can_receive_supply: false,
    ...flags,
  } as unknown as AdminOrderDetail;
}

beforeEach(() => {
  writes = [];
  offers = [offerRow()];
  order = orderWith({});
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body: init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}')) });
      if (url.includes('/sale-mode/')) return reply({ sale_mode: 'stock_or_order' });
      if (url.includes('/supply/')) return reply({ id: 2, supply_origin: 'supplier', supply_status: 'received' });
      return reply({ id: 1 }, 201);
    }
    if (url.includes('/admin/suppliers/settlement/')) {
      return reply({ results: [{ supplier_id: 5, supplier_name: 'Distribuidora Andina', units: 3, amount: '9000.00', units_without_cost: 1 }] });
    }
    if (url.includes('/admin/suppliers/')) return reply({ count: 1, page: 1, page_size: 25, results: [SUPPLIER] });
    if (url.includes('/admin/supplier-offers/')) return reply({ count: offers.length, page: 1, page_size: 25, results: offers });
    if (url.includes('/admin/orders/900/')) return reply(order);
    return reply({}, 404);
  });
});

describe('ofertas: el costo acordado', () => {
  it('sin la clave `agreed_cost` no hay columna ni campo de costo', async () => {
    render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
    const row = await screen.findByTestId('offer-row-31');
    expect(row).toHaveTextContent('Equipo X 256 Blanco');
    expect(screen.queryByText('Costo acordado')).toBeNull();
    fireEvent.click(within(row).getByRole('button', { name: /^Editar/ }));
    expect(screen.queryByLabelText(/Costo acordado/)).toBeNull();
  });

  it('con la clave, se muestra; y «sin costo» cuando viene vacía', async () => {
    offers = [offerRow({ agreed_cost: '3900.00' }), offerRow({ id: 32, product_name: 'Otro', agreed_cost: null })];
    render(<SupplierOffersManager canManage canSeeCosts companyId={null} />);
    const row = await screen.findByTestId('offer-row-31');
    expect(screen.getByRole('columnheader', { name: 'Costo acordado' })).toBeInTheDocument();
    expect(row).toHaveTextContent('3,900.00');
    expect(screen.getByTestId('offer-row-32')).toHaveTextContent('Sin costo');
    fireEvent.click(within(row).getByRole('button', { name: /^Editar/ }));
    expect(screen.getByLabelText('Costo acordado con el proveedor (S/)')).toHaveValue('3900.00');
  });

  it('aunque la persona pudiera ver costos, una oferta sin la clave no inventa uno', async () => {
    render(<SupplierOffersManager canManage canSeeCosts companyId={null} />);
    await screen.findByTestId('offer-row-31');
    expect(screen.queryByText('Costo acordado')).toBeNull();
  });

  it('desactivar pide el motivo y lo envía', async () => {
    render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
    const row = await screen.findByTestId('offer-row-31');
    fireEvent.click(within(row).getByRole('button', { name: /^Desactivar/ }));
    const form = screen.getByRole('form', { name: /Desactivar oferta/ });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Desactivar oferta' })); });
    expect(within(form).getByRole('alert')).toHaveTextContent('Escribe el motivo.');
    expect(writes).toEqual([]);
    fireEvent.change(within(form).getByLabelText('Motivo de la desactivación'), { target: { value: 'Sin unidades' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Desactivar oferta' })); });
    expect(writes[0]).toMatchObject({ method: 'POST', path: '/admin/supplier-offers/31/deactivate/', body: { reason: 'Sin unidades' } });
  });

  it('sin `products.manage` no hay formularios ni subida de Excel', async () => {
    render(<SupplierOffersManager canManage={false} canSeeCosts={false} companyId={null} />);
    const row = await screen.findByTestId('offer-row-31');
    expect(within(row).queryByRole('button', { name: /^Editar/ })).toBeNull();
    expect(screen.queryByRole('button', { name: '+ Nueva oferta' })).toBeNull();
    expect(screen.queryByLabelText('Archivo de ofertas (.xlsx)')).toBeNull();
    expect(screen.getByRole('link', { name: 'Descargar plantilla' })).toHaveAttribute('href', expect.stringContaining('/admin/supplier-offers/import/template/'));
  });
});

describe('ofertas: Excel', () => {
  const PREVIEW = {
    id: 61, status: 'previewed', rows_total: 3, rows_create: 1, rows_update: 0, rows_no_change: 1, rows_error: 1,
    cost_column_ignored: true,
    rows: [
      { row_number: 2, action: 'create', errors: [], warnings: [], data: { supplier: 'Distribuidora Andina', product_name: 'Equipo X', commercial_price: '4699.00', confirmed_quantity: 3, valid_from: '2026-10-01', valid_until: '2026-10-31', lead_time_days: 7 } },
      { row_number: 3, action: 'no_change', errors: [], warnings: [], data: { supplier: 'Distribuidora Andina', product_name: 'Igual' } },
      { row_number: 4, action: 'error', errors: ['Producto no encontrado: «Fantasma».'], warnings: [], data: { supplier: 'Distribuidora Andina', product_name: 'Fantasma' } },
    ],
  };

  async function previewWith(body: unknown) {
    const base = send.getMockImplementation()!;
    send.mockImplementation(async (input: string, init: RequestInit = {}) => {
      if (String(input).includes('/import/preview/')) return reply(body);
      if (String(input).includes('/import/61/apply/')) return reply({ status: 'applied', created: 1, updated: 0, unchanged: 1 });
      return base(input, init);
    });
    render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
    await screen.findByTestId('offer-row-31');
    const file = new File(['x'], 'ofertas.xlsx');
    fireEvent.change(screen.getByLabelText('Archivo de ofertas (.xlsx)'), { target: { files: [file] } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
    return screen.findByTestId('supplier-import-preview');
  }

  it('dice que la columna de costo se ignoró, muestra los errores por fila y no deja aplicar', async () => {
    const preview = await previewWith(PREVIEW);
    expect(within(preview).getByRole('note')).toHaveTextContent('esa columna se ignoró');
    expect(preview).toHaveTextContent('Producto no encontrado: «Fantasma».');
    expect(within(preview).queryByText('Igual')).toBeNull();
    expect(within(preview).getByRole('button', { name: 'Aplicar cambios' })).toBeDisabled();
  });

  it('sin errores se aplica y lo dice', async () => {
    const preview = await previewWith({ ...PREVIEW, rows_error: 0, cost_column_ignored: false, rows: PREVIEW.rows.slice(0, 2) });
    expect(within(preview).queryByRole('note')).toBeNull();
    await act(async () => { fireEvent.click(within(preview).getByRole('button', { name: 'Aplicar cambios' })); });
    const applied = send.mock.calls.find(([u]) => String(u).includes('/import/61/apply/'));
    expect(applied?.[1]?.method).toBe('POST');
    expect(await screen.findByRole('status')).toHaveTextContent('1 nuevas, 0 modificadas, 1 sin cambios');
  });
});

describe('proveedores', () => {
  it('lo pendiente de liquidar sólo con `costs.view`, y dice que es informativo', async () => {
    const { unmount } = render(<SuppliersManager canManage={false} canSeeCosts={false} companyId={null} />);
    await screen.findByText('Distribuidora Andina');
    expect(screen.queryByText('Pendiente de liquidar al proveedor')).toBeNull();
    unmount();
    render(<SuppliersManager canManage={false} canSeeCosts companyId={null} />);
    expect(await screen.findByText('Pendiente de liquidar al proveedor')).toBeInTheDocument();
    expect(screen.getByText(/Es informativo: no registra una deuda/)).toBeInTheDocument();
    expect(await screen.findByText(/1 unidad sin costo acordado/)).toBeInTheDocument();
  });
});

describe('modo de venta', () => {
  it('se cambia con `products.manage` y se envía al servidor', async () => {
    render(<ProductSaleModeControl productId={44} saleMode="stock" canManage />);
    fireEvent.change(screen.getByLabelText('Cómo se vende'), { target: { value: 'stock_or_order' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar modo' })); });
    expect(writes[0]).toMatchObject({ method: 'PATCH', path: '/admin/products/44/sale-mode/', body: { sale_mode: 'stock_or_order' } });
    expect(await screen.findByRole('status')).toHaveTextContent('Modo de venta guardado.');
  });

  it('sin permiso sólo se lee', () => {
    render(<ProductSaleModeControl productId={44} saleMode="stock_or_order" canManage={false} />);
    expect(screen.getByTestId('sale-mode-readonly')).toHaveTextContent('Stock o bajo pedido');
    expect(screen.queryByRole('combobox')).toBeNull();
  });
});

describe('abastecimiento de un pedido', () => {
  it('sin líneas de proveedor no se pinta', () => {
    const { container } = render(<OrderSupplyPanel order={orderWith({ supply_origin: 'own', supply_status: '' })} onChanged={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('sin permisos del servidor, se ve el estado y no hay acciones', () => {
    render(<OrderSupplyPanel order={orderWith({ supply_status: 'to_confirm' })} onChanged={jest.fn()} />);
    const line = screen.getByRole('listitem', { name: 'Abastecimiento de Equipo X 256 Blanco' });
    expect(line).toHaveTextContent('Por confirmar con el proveedor');
    expect(line).toHaveTextContent('Entrega en 7 días');
    expect(within(line).queryByRole('button')).toBeNull();
  });

  it('por confirmar + `can_manage_supply`: confirmar y «no se pudo abastecer»; recibir no', () => {
    render(<OrderSupplyPanel order={orderWith({ supply_status: 'to_confirm' }, { can_manage_supply: true })} onChanged={jest.fn()} />);
    expect(screen.getByRole('button', { name: 'Confirmar con proveedor' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'No se pudo abastecer' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Recibir del proveedor' })).toBeNull();
  });

  it('pedido + `can_receive_supply` sin `can_manage_supply`: sólo recibir', () => {
    render(<OrderSupplyPanel order={orderWith({ supply_status: 'pending' }, { can_receive_supply: true })} onChanged={jest.fn()} />);
    expect(screen.getByRole('button', { name: 'Recibir del proveedor' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirmar con proveedor' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'No se pudo abastecer' })).toBeNull();
  });

  it('confirmar puede elegir una oferta vigente del producto', async () => {
    offers = [offerRow({ id: 77, supplier_name: 'Otro proveedor', remaining: 4 })];
    const onChanged = jest.fn();
    render(<OrderSupplyPanel order={orderWith({ supply_status: 'to_confirm' }, { can_manage_supply: true })} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar con proveedor' }));
    const form = screen.getByRole('form', { name: /Confirmar con proveedor/ });
    await screen.findByRole('option', { name: /Otro proveedor/ });
    const lookedUp = send.mock.calls.map(([u]) => String(u)).find((u) => u.includes('/admin/supplier-offers/'));
    expect(lookedUp).toContain('product=44');
    expect(lookedUp).toContain('state=active');
    fireEvent.change(within(form).getByLabelText('Oferta (opcional)'), { target: { value: '77' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Confirmar' })); });
    expect(writes[0]).toMatchObject({ method: 'POST', path: '/admin/orders/900/supply/2/confirm/', body: { offer_id: 77 } });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('recibir un producto con serie pide la serie de cada unidad (la regla del servidor) y los envía', async () => {
    const onChanged = jest.fn();
    render(<OrderSupplyPanel
      order={orderWith({ supply_status: 'pending', product_is_serialized: true }, { can_receive_supply: true })}
      onChanged={onChanged}
    />);
    fireEvent.click(screen.getByRole('button', { name: 'Recibir del proveedor' }));
    const form = screen.getByRole('form', { name: /Recibir del proveedor/ });
    expect(within(form).getAllByRole('group')).toHaveLength(2);
    fireEvent.change(within(form).getByLabelText('IMEI (equipo 2, opcional)'), { target: { value: '356789012345680' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Registrar recepción' })); });
    // An IMEI alone is not enough: the server asks every device for its serial.
    expect(within(form).getByRole('alert')).toHaveTextContent('Revisa los equipos marcados.');
    expect(within(form).getAllByText('Falta el número de serie.')).toHaveLength(2);
    expect(writes).toEqual([]);
    fireEvent.change(within(form).getByLabelText('Serie (equipo 1)'), { target: { value: 'SER-A' } });
    fireEvent.change(within(form).getByLabelText('IMEI (equipo 1, opcional)'), { target: { value: '356789012345672' } });
    fireEvent.change(within(form).getByLabelText('Serie (equipo 2)'), { target: { value: 'SER-B' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Registrar recepción' })); });
    expect(writes[0]).toMatchObject({
      method: 'POST', path: '/admin/orders/900/supply/2/receive/',
      body: { units: [{ serial_number: 'SER-A', imei: '356789012345672' }, { serial_number: 'SER-B', imei: '356789012345680' }] },
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('recibir sin serie no pide equipos', async () => {
    render(<OrderSupplyPanel order={orderWith({ supply_status: 'pending' }, { can_receive_supply: true })} onChanged={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Recibir del proveedor' }));
    const form = screen.getByRole('form', { name: /Recibir del proveedor/ });
    expect(within(form).queryByRole('group')).toBeNull();
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Registrar recepción' })); });
    expect(writes[0]).toMatchObject({ path: '/admin/orders/900/supply/2/receive/', body: {} });
  });

  it('«no se pudo abastecer» exige el motivo y luego recuerda que la devolución va por el flujo habitual', async () => {
    render(<OrderSupplyPanel order={orderWith({ supply_status: 'pending' }, { can_manage_supply: true })} onChanged={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'No se pudo abastecer' }));
    const form = screen.getByRole('form', { name: /No se pudo abastecer/ });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Registrar' })); });
    expect(writes).toEqual([]);
    fireEvent.change(within(form).getByLabelText('Motivo'), { target: { value: 'El proveedor no tiene' } });
    await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Registrar' })); });
    expect(writes[0]).toMatchObject({ path: '/admin/orders/900/supply/2/fail/', body: { reason: 'El proveedor no tiene' } });
    expect(await screen.findByRole('status')).toHaveTextContent('flujo habitual de la orden');
  });
});

describe('lista de órdenes y menú', () => {
  it('«por confirmar» se destaca en la lista', () => {
    const row = { id: 1, customer_name: 'Ana', customer_email: 'a@x.pe', total: '10.00', status: 'paid', fulfillment_status: 'pending', created_at: '2026-10-10T10:00:00Z', has_stock_shortfall: false, supply_status: 'to_confirm' };
    render(<OrdersTable orders={[row as unknown as AdminOrder]} />);
    const badge = screen.getByTestId('order-supply-badge');
    expect(badge).toHaveTextContent('Bajo pedido · Por confirmar con el proveedor');
    expect(badge.className).toContain('text-warning');
  });

  it('«Proveedores» y «Ofertas de proveedores» están en el menú, con `products.view`', () => {
    expect(INTERNAL_MODULES.find((m) => m.href === '/admin/products/suppliers'))
      .toMatchObject({ group: 'products', label: 'Proveedores', requiredCapabilities: ['products.view'], status: 'implemented' });
    expect(INTERNAL_MODULES.find((m) => m.href === '/admin/products/suppliers/offers'))
      .toMatchObject({ group: 'products', label: 'Ofertas de proveedores', requiredCapabilities: ['products.view'], status: 'implemented' });
  });
});
