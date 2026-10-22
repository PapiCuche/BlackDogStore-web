import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { OrderSupplyPanel, unitRowErrors } from '@/app/admin/components/OrderSupplyPanel';
import { SupplyAlertsCard } from '@/app/admin/components/SupplyAlertsCard';
import { nextScanTarget, pastedCodes } from '@/app/admin/lib/scan-rows';
import type { AdminOrderDetail } from '@/app/lib/admin';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * SUPPLIER-DEVICE-FLOW-02 · B — recibir del proveedor.
 *
 *   · la regla de cada fila es la del servidor: serie siempre, IMEI si el
 *     producto lo exige; su rechazo vuelve a la fila y al campo que nombra;
 *   · antes de escribir se ve qué se recibe: pedido, producto, proveedor,
 *     sucursal, precio vendido, cantidad y —sólo con la clave del servidor— el
 *     costo pactado; el costo real sólo lo pide a quien ve costos;
 *   · con un lector: una fila por equipo, Enter al campo siguiente, pegar una
 *     lista llena las filas siguientes;
 *   · recibir dos veces no registra dos veces (409 `already_received`).
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

// 3567890123456 7X with their Luhn check digits.
const IMEI_A = '356789012345672';
const IMEI_B = '356789012345680';
const IMEI_C = '356789012345698';

type Call = { path: string; body: Record<string, unknown> };
let writes: Call[];
let receiveReply: Response;

function orderWith(item: Record<string, unknown> = {}, flags: Partial<AdminOrderDetail> = {}): AdminOrderDetail {
  return {
    id: 900, supply_status: 'pending', can_manage_supply: false, can_receive_supply: true,
    items: [{
      id: 2, product_id: 44, product_name: 'Equipo X 256 Blanco', quantity: 2, price: '4699.00', subtotal: '9398.00',
      supply_origin: 'supplier', supply_status: 'pending', supply_note: '', lead_time_days: 7, supplier_offer_id: 31,
      supplier_name: 'Distribuidora Andina', product_is_serialized: true, requires_imei: true,
      fulfillment_branch: { id: 3, name: 'Sucursal Centro' }, ...item,
    }],
    ...flags,
  } as unknown as AdminOrderDetail;
}

beforeEach(() => {
  writes = [];
  receiveReply = reply({ id: 2, supply_origin: 'supplier', supply_status: 'received' });
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if ((init.method ?? 'GET').toUpperCase() !== 'GET') {
      writes.push({ path: url.replace(/^.*\/api/, ''), body: JSON.parse(String(init.body ?? '{}')) });
      return receiveReply;
    }
    if (url.includes('/admin/orders/alerts/')) {
      return reply({ due_soon_days: 2, alerts: [
        { kind: 'to_confirm', label: 'Abastecimiento por confirmar', level: 'critical', count: 0, href: '/admin/orders?alert=to_confirm' },
        { kind: 'promise_due', label: 'Fecha prometida vencida o por vencer', level: 'critical', count: 3, overdue: 1, href: '/admin/orders?alert=promise_due' },
        { kind: 'units_pending', label: 'Equipos por identificar', level: 'warning', count: 2, href: '/admin/orders?alert=units_pending' },
      ] });
    }
    if (url.includes('/admin/orders/900/')) return reply(orderWith());
    return reply({}, 404);
  });
});

function openReceive(order = orderWith(), onChanged = jest.fn()) {
  render(<OrderSupplyPanel order={order} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole('button', { name: 'Recibir del proveedor' }));
  return screen.getByRole('form', { name: /Recibir del proveedor/ });
}

const type = (form: HTMLElement, label: string, value: string) =>
  fireEvent.change(within(form).getByLabelText(label), { target: { value } });

async function submit(form: HTMLElement) {
  await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Registrar recepción' })); });
}

describe('INV-ID-01: el dígito que falta', () => {
  it('con 14 dígitos ofrece el decimoquinto y sólo lo pone si se pide', () => {
    const form = openReceive();
    type(form, 'IMEI (equipo 1)', IMEI_A.slice(0, 14));
    expect(within(form).getByLabelText('IMEI (equipo 1)')).toHaveValue(IMEI_A.slice(0, 14));
    fireEvent.click(within(form).getByRole('button', { name: `Completar con ${IMEI_A.slice(-1)}` }));
    expect(within(form).getByLabelText('IMEI (equipo 1)')).toHaveValue(IMEI_A);
    expect(within(form).queryByRole('button', { name: /Completar con/ })).toBeNull();
  });
});

describe('la regla de cada equipo es la del servidor', () => {
  it('un producto con IMEI pide serie e IMEI en cada fila, sin escribir nada', async () => {
    const form = openReceive();
    type(form, 'Serie (equipo 1)', 'SER-A');
    type(form, 'IMEI (equipo 2)', IMEI_B);
    await submit(form);
    expect(writes).toEqual([]);
    expect(within(form).getByLabelText('IMEI (equipo 1)')).toHaveAttribute('aria-invalid', 'true');
    expect(within(form).getByText('Este producto lleva IMEI: indícalo.')).toBeInTheDocument();
    expect(within(form).getByLabelText('Serie (equipo 2)')).toHaveAttribute('aria-invalid', 'true');
  });

  it('el rechazo del servidor vuelve a la fila y al campo que nombra', async () => {
    receiveReply = reply({ detail: 'Ese IMEI ya está registrado en otro equipo de la empresa.', code: 'invalid',
      field: 'units', line: 2, unit_field: 'imei' }, 400);
    const form = openReceive();
    type(form, 'IMEI (equipo 1)', IMEI_A);
    type(form, 'Serie (equipo 1)', 'SER-A');
    type(form, 'IMEI (equipo 2)', IMEI_B);
    type(form, 'Serie (equipo 2)', 'SER-B');
    await submit(form);
    expect(within(form).getByLabelText('IMEI (equipo 2)')).toHaveAttribute('aria-invalid', 'true');
    expect(within(form).getByRole('alert')).toHaveTextContent('Equipo 2: Ese IMEI ya está registrado');
    expect(within(form).getByLabelText('IMEI (equipo 1)')).not.toHaveAttribute('aria-invalid');
  });

  it('cada fila lleva su nota y su segundo IMEI', async () => {
    const onChanged = jest.fn();
    const form = openReceive(orderWith({ quantity: 1 }), onChanged);
    type(form, 'IMEI (equipo 1)', IMEI_A);
    type(form, 'IMEI 2 (equipo 1, opcional)', IMEI_B);
    type(form, 'Serie (equipo 1)', 'SER-A');
    type(form, 'Nota (equipo 1, opcional)', 'Caja abierta');
    await submit(form);
    expect(writes[0]).toEqual({ path: '/admin/orders/900/supply/2/receive/', body: {
      units: [{ serial_number: 'SER-A', imei: IMEI_A, imei2: IMEI_B, notes: 'Caja abierta' }],
    } });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('una línea ya recibida no se registra otra vez: se avisa y se vuelve a leer la orden', async () => {
    receiveReply = reply({ detail: 'Esta línea ya se recibió del proveedor.', code: 'already_received',
      units: [{ serial_number: 'SER-A', imei: IMEI_A, imei2: '' }] }, 409);
    const onChanged = jest.fn();
    const form = openReceive(orderWith({ quantity: 1 }), onChanged);
    type(form, 'IMEI (equipo 1)', IMEI_A);
    type(form, 'Serie (equipo 1)', 'SER-A');
    await submit(form);
    expect(await screen.findByRole('status')).toHaveTextContent('ya estaba recibida');
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('las reglas de las filas, sin pantalla', () => {
    const row = (serial: string, imei = '', imei2 = '') => ({ serial_number: serial, imei, imei2, notes: '', cost: '' });
    expect(unitRowErrors([row('A', IMEI_A), row('B', IMEI_B)], true)).toEqual({});
    expect(unitRowErrors([row('A')], false)).toEqual({});
    expect(unitRowErrors([row('A')], true)).toHaveProperty(['0.imei']);
    expect(unitRowErrors([row('A', '', IMEI_B)], false)).toHaveProperty(['0.imei2']);
    expect(unitRowErrors([row('A', IMEI_A), row('a', IMEI_A)], false)).toEqual({
      '1.serial_number': 'Esta serie ya está en otra fila.', '1.imei': 'Este IMEI ya está en otra fila.',
    });
    expect(unitRowErrors([row('A', '356789012345671')], false)).toHaveProperty(['0.imei']);
  });
});

describe('qué se recibe, y el costo sólo con la clave del servidor', () => {
  it('pedido, producto, proveedor, sucursal, precio y cantidad; sin costos sin la clave', () => {
    const form = openReceive();
    const facts = within(form).getByLabelText('Datos de la línea');
    for (const text of ['#900', 'Equipo X 256 Blanco', 'Distribuidora Andina', 'Sucursal Centro', 'S/ 4699.00', '2']) {
      expect(facts).toHaveTextContent(text);
    }
    expect(facts).not.toHaveTextContent('Costo pactado');
    expect(within(form).queryByLabelText(/Costo real/)).toBeNull();
    expect(screen.queryByLabelText('Costos de la línea')).toBeNull();
  });

  it('con `costs.view` se ve el pactado y se puede decir el real, por equipo o para todos', async () => {
    const form = openReceive(orderWith({ quantity: 1, agreed_unit_cost: '2500.0000', received_unit_cost: null }));
    expect(within(form).getByLabelText('Datos de la línea')).toHaveTextContent('Costo pactado');
    type(form, 'IMEI (equipo 1)', IMEI_A);
    type(form, 'Serie (equipo 1)', 'SER-A');
    type(form, 'Costo real (equipo 1, opcional)', '2450.00');
    type(form, 'Costo real por equipo (opcional, para los que no lo dicen)', '2400.00');
    await submit(form);
    expect(writes[0].body).toEqual({
      units: [{ serial_number: 'SER-A', imei: IMEI_A, cost: '2450.00' }], unit_cost: '2400.00',
    });
  });

  it('una línea recibida muestra el pactado, el real y la diferencia', () => {
    render(<OrderSupplyPanel order={orderWith({ supply_status: 'received', agreed_unit_cost: '70.0000',
      received_unit_cost: '65.5000', unit_cost_difference: '-4.5000' }, { supply_status: 'received' })} onChanged={jest.fn()} />);
    expect(screen.getByLabelText('Costos de la línea')).toHaveTextContent(
      'Costo pactado S/ 70.00 · Costo real S/ 65.50 · Diferencia -4.50 por unidad');
  });

  it('la línea dice su sucursal y el precio vendido aunque no se esté recibiendo', () => {
    render(<OrderSupplyPanel order={orderWith({}, { can_receive_supply: false })} onChanged={jest.fn()} />);
    const line = screen.getByRole('listitem', { name: 'Abastecimiento de Equipo X 256 Blanco' });
    expect(line).toHaveTextContent('Sucursal: Sucursal Centro');
    expect(line).toHaveTextContent('Vendido a S/ 4699.00 c/u');
  });
});

describe('con un lector', () => {
  it('abre en el primer campo; Enter: IMEI → IMEI 2 → serie → siguiente fila → registrar, sin enviar', () => {
    const form = openReceive();
    const field = (label: string) => within(form).getByLabelText(label);
    expect(field('IMEI (equipo 1)')).toHaveFocus();
    expect(field('IMEI (equipo 1)')).toHaveAttribute('autocomplete', 'off');
    expect(field('IMEI (equipo 1)')).toHaveAttribute('inputmode', 'numeric');
    fireEvent.keyDown(field('IMEI (equipo 1)'), { key: 'Enter' });
    expect(field('IMEI 2 (equipo 1, opcional)')).toHaveFocus();
    fireEvent.keyDown(field('IMEI 2 (equipo 1, opcional)'), { key: 'Enter' });
    expect(field('Serie (equipo 1)')).toHaveFocus();
    fireEvent.keyDown(field('Serie (equipo 1)'), { key: 'Enter' });
    expect(field('IMEI (equipo 2)')).toHaveFocus();
    fireEvent.keyDown(field('IMEI (equipo 2)'), { key: 'Enter' });
    fireEvent.keyDown(field('IMEI 2 (equipo 2, opcional)'), { key: 'Enter' });
    fireEvent.keyDown(field('Serie (equipo 2)'), { key: 'Enter' });
    expect(within(form).getByRole('button', { name: 'Registrar recepción' })).toHaveFocus();
    expect(writes).toEqual([]);
  });

  it('sin IMEI la cadena es sólo la serie', () => {
    const form = openReceive(orderWith({ requires_imei: false }));
    expect(within(form).getByLabelText('Serie (equipo 1)')).toHaveFocus();
    fireEvent.keyDown(within(form).getByLabelText('Serie (equipo 1)'), { key: 'Enter' });
    expect(within(form).getByLabelText('Serie (equipo 2)')).toHaveFocus();
  });

  it('pegar una lista llena ese campo en las filas siguientes', () => {
    const form = openReceive(orderWith({ quantity: 3 }));
    fireEvent.paste(within(form).getByLabelText('IMEI (equipo 1)'), {
      clipboardData: { getData: () => `${IMEI_A}\n${IMEI_B}\r\n\n${IMEI_C}\n` },
    });
    expect(within(form).getByLabelText('IMEI (equipo 1)')).toHaveValue(IMEI_A);
    expect(within(form).getByLabelText('IMEI (equipo 2)')).toHaveValue(IMEI_B);
    expect(within(form).getByLabelText('IMEI (equipo 3)')).toHaveValue(IMEI_C);
  });

  it('las piezas, sin pantalla', () => {
    expect(pastedCodes('uno')).toEqual([]);
    expect(pastedCodes(' a \n\n b\r\nc ')).toEqual(['a', 'b', 'c']);
    expect(nextScanTarget(0, 0, 3, 2)).toEqual({ row: 0, field: 1 });
    expect(nextScanTarget(0, 2, 3, 2)).toEqual({ row: 1, field: 0 });
    expect(nextScanTarget(1, 2, 3, 2)).toBe('end');
  });
});

describe('alertas operativas', () => {
  it('muestra las que tienen pedidos, con su enlace al listado filtrado', async () => {
    render(<SupplyAlertsCard />);
    const due = await screen.findByTestId('supply-alert-promise_due');
    expect(due).toHaveAttribute('href', '/admin/orders?alert=promise_due');
    expect(due).toHaveTextContent('3');
    expect(due).toHaveTextContent('1 ya vencido');
    expect(screen.getByTestId('supply-alert-units_pending')).toHaveAttribute('href', '/admin/orders?alert=units_pending');
    expect(screen.queryByTestId('supply-alert-to_confirm')).toBeNull();
  });

  it('sin nada esperando lo dice', async () => {
    send.mockImplementation(async () => reply({ due_soon_days: 2, alerts: [
      { kind: 'to_confirm', label: 'x', level: 'critical', count: 0, href: '/admin/orders?alert=to_confirm' },
    ] }));
    render(<SupplyAlertsCard />);
    expect(await screen.findByText(/Nada esperando/)).toBeInTheDocument();
  });

  it('un 403 se dice, sin romper el panel', async () => {
    send.mockImplementation(async () => reply({ detail: 'x' }, 403));
    render(<SupplyAlertsCard />);
    expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permisos para ver órdenes.');
  });
});
