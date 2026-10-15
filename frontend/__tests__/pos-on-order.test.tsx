import { fireEvent, render, screen } from '@testing-library/react';

import { PosOnOrderBadge, PosOnOrderOffer, PosProductHit, PosSupplyReason } from '@/app/admin/sales/pos/PosOnOrder';
import type { PosProduct } from '@/app/admin/lib/internal-api';
import {
  MIXED_SUPPLY_MESSAGE,
  SUPPLY_REASON_MAX,
  lineIsReady,
  lineToOnOrder,
  missingSupplyReasons,
  needsSupplyReason,
  pendingUnits,
  previewItems,
  saleItems,
  withOnOrderProduct,
  withProduct,
  withQuantity,
  withSupplyReason,
  withUnit,
} from '@/app/admin/lib/unit-entry';

/**
 * DEVICE-COMMERCE-01 · DC3 — «bajo pedido» en la caja.
 *
 * Sin estante (o sin bastante) y con una oferta vigente, el cajero añade la
 * línea bajo pedido A PROPÓSITO, al precio de la oferta y con su plazo. Una
 * línea es toda del estante o toda bajo pedido; la de bajo pedido viaja con
 * `on_order` y sin equipos, aunque el producto tenga serie.
 */

const PHONE: PosProduct = {
  id: 44, name: 'Equipo X 256', price: '4499.00', available: 0, barcode: '', is_serialized: true, requires_imei: true,
  on_order: { lead_time_days: 7, price: '4699.00' },
};

describe('la cesta', () => {
  it('una línea bajo pedido va al precio de la oferta, sin equipos y lista para cobrar', () => {
    const lines = withOnOrderProduct([], PHONE);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatchObject({ product: 44, price: '4699.00', quantity: 1, onOrder: true, leadTimeDays: 7, units: [] });
    expect(lineIsReady(lines[0])).toBe(true);
    expect(pendingUnits(lines)).toBe(0);
    expect(saleItems(lines)).toEqual([{ product: 44, quantity: 1, on_order: true }]);
    expect(previewItems(lines)).toEqual([{ product: 44, quantity: 1, on_order: true }]);
  });

  it('otra pulsación suma a la misma línea', () => {
    const lines = withOnOrderProduct(withOnOrderProduct([], PHONE), PHONE);
    expect(lines).toHaveLength(1);
    expect(lines[0].quantity).toBe(2);
  });

  it('nunca mezcla estante y bajo pedido de un mismo producto', () => {
    const shelf = withProduct([], { ...PHONE, available: 1 });
    expect(() => withOnOrderProduct(shelf, PHONE)).toThrow(MIXED_SUPPLY_MESSAGE);
    const onOrder = withOnOrderProduct([], PHONE);
    expect(() => withProduct(onOrder, { ...PHONE, available: 1 })).toThrow(MIXED_SUPPLY_MESSAGE);
    expect(() => withUnit(onOrder, { unit_id: 9, serial_number: 'S9', imei: '' }, PHONE, 'scanner')).toThrow(MIXED_SUPPLY_MESSAGE);
  });

  it('sin oferta no hay bajo pedido', () => {
    expect(() => withOnOrderProduct([], { ...PHONE, on_order: null })).toThrow('no se puede vender bajo pedido');
  });

  it('una línea del estante que no alcanza pasa ENTERA a bajo pedido y suelta sus equipos', () => {
    const shelf = withUnit([], { unit_id: 9, serial_number: 'S9', imei: '' }, { ...PHONE, available: 1 }, 'scanner');
    const moved = lineToOnOrder(shelf.map((l) => ({ ...l, quantity: 3 })), 44);
    expect(moved[0]).toMatchObject({ onOrder: true, price: '4699.00', units: [], quantity: 3, leadTimeDays: 7 });
    expect(saleItems(moved)).toEqual([{ product: 44, quantity: 3, on_order: true }]);
  });

  it('la venta normal no cambia', () => {
    const lines = withProduct([], { id: 3, name: 'Funda', price: '50.00', available: 5 });
    expect(saleItems(lines)).toEqual([{ product: 3, quantity: 1 }]);
    expect(previewItems(lines)).toEqual([{ product: 3, quantity: 1 }]);
  });
});

describe('la pantalla', () => {
  it('el resultado ofrece «Bajo pedido (N días)» al precio de la oferta, como acción aparte', () => {
    const onAdd = jest.fn();
    const onAddOnOrder = jest.fn();
    render(<PosProductHit product={PHONE} onAdd={onAdd} onAddOnOrder={onAddOnOrder} />);
    const button = screen.getByRole('button', { name: 'Agregar Equipo X 256 bajo pedido (7 días)' });
    expect(button).toHaveTextContent('Bajo pedido (7 días)');
    expect(button.textContent).toMatch(/4[,.]?699\.00/);
    fireEvent.click(button);
    expect(onAddOnOrder).toHaveBeenCalledWith(PHONE);
    expect(onAdd).not.toHaveBeenCalled();
  });

  it('sin oferta no hay botón de bajo pedido', () => {
    render(<PosProductHit product={{ ...PHONE, on_order: null }} onAdd={jest.fn()} onAddOnOrder={jest.fn()} />);
    expect(screen.queryByRole('button', { name: /bajo pedido/ })).toBeNull();
  });

  it('tras escanear sin estante, la opción se ofrece y se acepta a mano', () => {
    const onAdd = jest.fn();
    render(<PosOnOrderOffer product={PHONE} onAdd={onAdd} onDismiss={jest.fn()} />);
    expect(screen.getByText(/entrega en 7 días/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Agregar bajo pedido' }));
    expect(onAdd).toHaveBeenCalledWith(PHONE);
  });

  it('la línea dice su plazo', () => {
    render(<PosOnOrderBadge days={1} />);
    expect(screen.getByTestId('pos-line-on-order')).toHaveTextContent('Bajo pedido (1 día)');
  });
});

/**
 * Owner's decision — whoever has `sales.orders.manage` (`can_order_over_stock`)
 * may sell «bajo pedido» although the branch shelf covers the quantity, but
 * only saying why. The reason travels as `supply_reason` on that line, in the
 * preview and in the sale; without the permission nothing changes and the
 * server's `stock_available` message is what the till shows.
 */
describe('bajo pedido aunque haya stock', () => {
  const STOCKED: PosProduct = { ...PHONE, available: 3 };

  it('una línea bajo pedido que el estante cubre pide motivo; una que no, no', () => {
    const covered = withOnOrderProduct([], STOCKED);
    expect(needsSupplyReason(covered[0])).toBe(true);
    expect(missingSupplyReasons(covered)).toBe(1);
    const uncovered = withOnOrderProduct([], PHONE);
    expect(needsSupplyReason(uncovered[0])).toBe(false);
    expect(missingSupplyReasons(uncovered)).toBe(0);
    // Más de lo que hay: el estante ya no la cubre.
    expect(needsSupplyReason(withQuantity(covered, 44, 4)[0])).toBe(false);
    // Del estante no es bajo pedido: nunca pide motivo.
    expect(needsSupplyReason(withProduct([], STOCKED)[0])).toBe(false);
  });

  it('el motivo viaja recortado como `supply_reason` en la vista previa y en la venta', () => {
    const lines = withSupplyReason(withOnOrderProduct([], STOCKED), 44, '  Cliente pide sellado de fábrica  ');
    expect(missingSupplyReasons(lines)).toBe(0);
    const expected = [{ product: 44, quantity: 1, on_order: true, supply_reason: 'Cliente pide sellado de fábrica' }];
    expect(saleItems(lines)).toEqual(expected);
    expect(previewItems(lines)).toEqual(expected);
  });

  it('un motivo en blanco no cuenta y no viaja', () => {
    const lines = withSupplyReason(withOnOrderProduct([], STOCKED), 44, '   ');
    expect(missingSupplyReasons(lines)).toBe(1);
    expect(saleItems(lines)).toEqual([{ product: 44, quantity: 1, on_order: true }]);
    expect(previewItems(lines)).toEqual([{ product: 44, quantity: 1, on_order: true }]);
  });

  it('el motivo no pasa de 200 caracteres', () => {
    expect(SUPPLY_REASON_MAX).toBe(200);
    const lines = withSupplyReason(withOnOrderProduct([], STOCKED), 44, 'x'.repeat(250));
    expect(lines[0].supplyReason).toHaveLength(200);
    expect((saleItems(lines)[0] as { supply_reason?: string }).supply_reason).toHaveLength(200);
  });

  it('en la línea: el motivo es obligatorio, se escribe ahí y se ve', () => {
    const onChange = jest.fn();
    const line = withOnOrderProduct([], STOCKED)[0];
    const { rerender } = render(<PosSupplyReason line={line} onChange={onChange} />);
    const input = screen.getByLabelText('Motivo para pedirlo al proveedor');
    expect(input).toBeRequired();
    expect(input).toHaveAttribute('maxLength', '200');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText(/Hay 3 en esta sucursal/)).toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'Pidió color de proveedor' } });
    expect(onChange).toHaveBeenCalledWith('Pidió color de proveedor');

    rerender(<PosSupplyReason line={{ ...line, supplyReason: 'Pidió color de proveedor' }} onChange={onChange} />);
    expect(screen.getByLabelText('Motivo para pedirlo al proveedor')).toHaveValue('Pidió color de proveedor');
    expect(screen.getByLabelText('Motivo para pedirlo al proveedor')).toHaveAttribute('aria-invalid', 'false');
  });

  it('una línea que no lo necesita no muestra el campo', () => {
    render(<PosSupplyReason line={withOnOrderProduct([], PHONE)[0]} onChange={jest.fn()} />);
    expect(screen.queryByLabelText('Motivo para pedirlo al proveedor')).toBeNull();
  });
});
