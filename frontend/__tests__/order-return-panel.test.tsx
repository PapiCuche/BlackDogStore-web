import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { fetchWithAuth } from '@/app/lib/auth';
import { OrderReturnPanel } from '@/app/admin/components/OrderReturnPanel';
import { DispatchPanel } from '@/app/admin/components/DispatchPanel';

/**
 * POST-DELIVERY-RETURN (fase 1) — el panel «Devolución» del detalle de un
 * pedido. Lo que se fija:
 *
 *  - no aparece si el pedido no la admite y no tiene ninguna;
 *  - registrar pide tipo y motivo (mínimo), avisa cuando corresponde a un
 *    superior y enseña por qué no se puede;
 *  - recibir pide la sucursal, el IMEI o la serie de CADA equipo —que tiene
 *    que ser uno de los que salieron— y su estado; una cantidad vuelve entera
 *    y no admite «A revisión»; lo que se manda es lo que el servidor espera;
 *  - cerrar calcula lo que se devuelve, exige el motivo de un descuento, se
 *    confirma antes y manda el importe;
 *  - retirar pide motivo; sin ningún `can_*` sólo se lee;
 *  - el motivo de un rechazo es el del servidor, tal cual;
 *  - el historial de despacho enseña el nombre que da el servidor (`label`).
 *
 * Se simula la red (`fetchWithAuth`), no las funciones del API.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  SELECTED_COMPANY_KEY: 'internal-selected-company',
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

const IMEI = '356938035643809';

const ITEMS = [
  {
    id: 7, product_id: 70, product_name: 'iPhone 15 128 GB', quantity: 1, price: '120.00', subtotal: '120.00',
    is_serialized: true, requires_imei: true,
    units: [{
      id: 501, serial_number: 'F2LXK1ABC', imei: IMEI, imei2: '', method: 'scan', method_label: 'Escaneo',
      identified_at: '2026-10-10T10:00:00Z', identified_by: 'ana.ventas', can_remove: false,
    }],
  },
  {
    id: 8, product_id: 80, product_name: 'Cable USB-C', quantity: 2, price: '13.50', subtotal: '27.00',
    is_serialized: false, units: [],
  },
];

function theReturn(overrides: Record<string, unknown> = {}) {
  return {
    id: 3, status: 'requested', status_label: 'Devolución registrada', kind: 'defectuoso',
    kind_label: 'Producto defectuoso', reason: 'No enciende al llegar',
    requested_at: '2026-10-18T15:00:00Z', requested_by: 'ana.ventas',
    received_at: null, received_by: '', receiving_branch: null,
    closed_at: null, closed_by: '', withdrawn_at: null, withdrawn_by: '', withdrawn_reason: '',
    paid_amount: null, deduction_amount: null, deduction_reason: '', refund_amount: null,
    refund_method: '', refund_method_label: '', cancellation_id: null, lines: [],
    ...overrides,
  };
}

function block(overrides: Record<string, unknown> = {}) {
  return {
    eligible: true, can_request: false, can_receive: false, can_close: false, can_withdraw: false,
    superior_needed: false, superior_conditions: [], blockers: [], window_days: 7, paid_amount: '147.00',
    receive_branches: [], return: null,
    ...overrides,
  };
}

function order(blockOverrides: Record<string, unknown> | null = {}) {
  return {
    id: 42, status: 'paid', paid: true, fulfillment_status: 'delivered', total: '147.00', items: ITEMS,
    return_block: blockOverrides === null ? null : block(blockOverrides),
  };
}

function renderPanel(o: ReturnType<typeof order>) {
  const onChanged = jest.fn();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const view = render(<OrderReturnPanel order={o as any} onChanged={onChanged} />);
  return { onChanged, ...view };
}

function sentBody(call = 0) {
  return JSON.parse(String(send.mock.calls[call][1]?.body));
}

function sentUrl(call = 0) {
  return String(send.mock.calls[call][0]);
}

function optionValues(select: HTMLElement) {
  return within(select).getAllByRole('option').map((option) => (option as HTMLOptionElement).value);
}

beforeEach(() => send.mockReset());

describe('cuándo aparece', () => {
  it('no aparece si el pedido no la admite y no tiene devolución', () => {
    renderPanel(order({ eligible: false }));
    expect(screen.queryByTestId('order-return-panel')).not.toBeInTheDocument();
  });

  it('no aparece sin `return_block` (servidor anterior)', () => {
    renderPanel(order(null));
    expect(screen.queryByTestId('order-return-panel')).not.toBeInTheDocument();
  });

  it('una devolución cerrada se sigue viendo, con sus pasos, fechas y responsables', () => {
    renderPanel(order({
      eligible: false,
      return: theReturn({
        status: 'closed', status_label: 'Devolución cerrada',
        received_at: '2026-10-18T17:00:00Z', received_by: 'luis.almacen',
        receiving_branch: { id: 1, name: 'Miraflores' },
        closed_at: '2026-10-19T09:00:00Z', closed_by: 'carla.admin',
        paid_amount: '147.00', deduction_amount: '10.00', deduction_reason: 'Flete de vuelta',
        refund_amount: '137.00', refund_method: 'cash', refund_method_label: 'Efectivo',
        lines: [{
          id: 1, order_item: 7, product_id: 70, product_name: 'iPhone 15 128 GB', quantity: 1, unit_id: 501,
          serial_number: '•••••1ABC', imei: '•••••••••••3809', condition: 'review', condition_label: 'A revisión',
        }],
      }),
    }));
    const steps = within(screen.getByRole('list', { name: 'Pasos de la devolución' })).getAllByRole('listitem');
    expect(steps.map((li) => li.getAttribute('data-testid'))).toEqual([
      'order-return-step-requested', 'order-return-step-received', 'order-return-step-closed',
    ]);
    expect(steps.every((li) => li.getAttribute('data-state') === 'done')).toBe(true);
    expect(screen.getByTestId('order-return-step-received')).toHaveTextContent('luis.almacen');
    expect(screen.getByTestId('order-return-step-closed')).toHaveTextContent('carla.admin');
    expect(screen.getByTestId('order-return-step-closed')).toHaveTextContent('19/10/2026');
    expect(screen.getByTestId('order-return-refund')).toHaveTextContent('S/ 137.00 · Efectivo');
    expect(screen.getByTestId('order-return-lines')).toHaveTextContent('•••••••••••3809');
    expect(screen.getByTestId('order-return-lines')).toHaveTextContent('A revisión');
  });

  it('una devolución retirada se lee «Solicitada → Retirada», con el motivo', () => {
    renderPanel(order({
      return: theReturn({
        status: 'withdrawn', status_label: 'Devolución retirada', withdrawn_at: '2026-10-18T16:00:00Z',
        withdrawn_by: 'ana.ventas', withdrawn_reason: 'El cliente se lo queda',
      }),
    }));
    const steps = within(screen.getByRole('list', { name: 'Pasos de la devolución' })).getAllByRole('listitem');
    expect(steps.map((li) => li.getAttribute('data-testid'))).toEqual([
      'order-return-step-requested', 'order-return-step-withdrawn',
    ]);
    expect(screen.getByTestId('order-return-panel')).toHaveTextContent('El cliente se lo queda');
  });
});

describe('registrar', () => {
  it('pide tipo y motivo (mínimo 5) y manda {kind, reason}', async () => {
    const updated = order({ return: theReturn() });
    send.mockResolvedValueOnce(reply(201, updated));
    const { onChanged } = renderPanel(order({ can_request: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Registrar devolución' }));
    const form = screen.getByTestId('order-return-request');
    const submit = within(form).getByRole('button', { name: 'Registrar devolución' });

    fireEvent.change(within(form).getByLabelText('Motivo'), { target: { value: '  No   enciende ' } });
    expect(submit).toBeDisabled();                       // sin tipo
    fireEvent.change(within(form).getByLabelText('Tipo de devolución'), { target: { value: 'defectuoso' } });
    fireEvent.change(within(form).getByLabelText('Motivo'), { target: { value: 'Mal' } });
    expect(submit).toBeDisabled();                       // motivo corto
    fireEvent.change(within(form).getByLabelText('Motivo'), { target: { value: '  No   enciende ' } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(updated));
    expect(sentUrl()).toMatch(/\/admin\/orders\/42\/return\/$/);
    expect(send.mock.calls[0][1]?.method).toBe('POST');
    expect(sentBody()).toEqual({ kind: 'defectuoso', reason: 'No enciende' });
  });

  it('avisa cuando corresponde a un superior, con las condiciones del servidor', () => {
    renderPanel(order({
      can_request: true, superior_needed: true,
      superior_conditions: [{ code: 'older_than_window', detail: 'La entrega fue hace más de 7 días.' }],
    }));
    fireEvent.click(screen.getByRole('button', { name: 'Registrar devolución' }));
    expect(screen.getByTestId('order-return-superior')).toHaveTextContent('Requiere autorización superior');
    expect(screen.getByTestId('order-return-superior')).toHaveTextContent('La entrega fue hace más de 7 días.');
  });

  it('sin plazo vencido no hay aviso de superior', () => {
    renderPanel(order({ can_request: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Registrar devolución' }));
    expect(screen.queryByTestId('order-return-superior')).not.toBeInTheDocument();
  });

  it('enseña por qué no se puede registrar, sin botón', () => {
    renderPanel(order({
      blockers: [{ code: 'superior_required', detail: 'Registrar esta devolución corresponde a un superior.' }],
    }));
    expect(screen.getByTestId('order-return-blockers'))
      .toHaveTextContent('Registrar esta devolución corresponde a un superior.');
    expect(screen.queryByRole('button', { name: 'Registrar devolución' })).not.toBeInTheDocument();
  });
});

const RECEIVING = {
  can_receive: true,
  receive_branches: [{ id: 1, name: 'Miraflores' }, { id: 2, name: 'San Isidro' }],
  return: theReturn(),
};

describe('recibir', () => {
  it('cada equipo por su IMEI o serie, que tiene que ser uno de los que salieron; manda lo que el servidor espera', async () => {
    const updated = order({ return: theReturn({ status: 'received' }) });
    send.mockResolvedValueOnce(reply(200, updated));
    const { onChanged } = renderPanel(order(RECEIVING));
    const form = screen.getByTestId('order-return-receive');
    const device = screen.getByTestId('order-return-device-7-0');
    const quantity = screen.getByTestId('order-return-quantity-8');
    const receive = within(form).getByRole('button', { name: 'Recibir mercadería' });

    fireEvent.change(within(form).getByLabelText('Sucursal que recibe'), { target: { value: '2' } });
    fireEvent.change(within(device).getByLabelText('Estado en que llega'), { target: { value: 'review' } });
    fireEvent.change(within(quantity).getByLabelText('Estado en que llega'), { target: { value: 'damaged' } });
    expect(receive).toBeDisabled();                      // falta el IMEI

    fireEvent.change(within(device).getByLabelText('IMEI o serie del equipo que vuelve'), {
      target: { value: '356938035643800' },
    });
    expect(device).toHaveTextContent('No es uno de los equipos que salieron con este pedido.');
    expect(receive).toBeDisabled();

    fireEvent.change(within(device).getByLabelText('IMEI o serie del equipo que vuelve'), {
      target: { value: ' 35-6938-0356-43809 ' },
    });
    expect(device).toHaveTextContent('Coincide con un equipo que salió con este pedido.');
    expect(within(quantity).getByLabelText('Cantidad que vuelve')).toHaveValue(2);
    expect(within(quantity).getByLabelText('Cantidad que vuelve')).toHaveAttribute('readonly');
    expect(receive).toBeEnabled();

    fireEvent.click(receive);
    expect(send).not.toHaveBeenCalled();                 // primero se confirma
    const confirm = screen.getByTestId('order-return-receive-confirm');
    expect(confirm).toHaveTextContent('San Isidro');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Sí, recibir' }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(updated));
    expect(sentUrl()).toMatch(/\/admin\/orders\/42\/return\/receive\/$/);
    expect(sentBody()).toEqual({
      branch: 2,
      lines: [
        { order_item: 7, quantity: 1, condition: 'review', code: '35-6938-0356-43809' },
        { order_item: 8, quantity: 2, condition: 'damaged' },
      ],
    });
  });

  it('una cantidad devuelta a mano: un superior la recibe sin mover stock y se manda `already_in_stock`', async () => {
    send.mockResolvedValueOnce(reply(200, order({ return: theReturn({ status: 'received' }) })));
    renderPanel(order({ ...RECEIVING, manual_return_lines: [8], can_receive_by_hand: true }));
    const form = screen.getByTestId('order-return-receive');
    const device = screen.getByTestId('order-return-device-7-0');
    const byHand = screen.getByTestId('order-return-by-hand-8');
    const receive = within(form).getByRole('button', { name: 'Recibir mercadería' });
    fireEvent.change(within(form).getByLabelText('Sucursal que recibe'), { target: { value: '1' } });
    fireEvent.change(within(device).getByLabelText('Estado en que llega'), { target: { value: 'sellable' } });
    fireEvent.change(within(device).getByLabelText('IMEI o serie del equipo que vuelve'), { target: { value: IMEI } });
    expect(receive).toBeDisabled();                      // la línea marcada pide la casilla
    fireEvent.click(within(byHand).getByRole('checkbox', { name: /Ya ingresó a mano/ }));
    expect(within(screen.getByTestId('order-return-quantity-8')).getByLabelText('Estado en que llega')).toBeDisabled();
    fireEvent.click(receive);
    fireEvent.click(within(screen.getByTestId('order-return-receive-confirm')).getByRole('button', { name: 'Sí, recibir' }));
    await waitFor(() => expect(send).toHaveBeenCalled());
    expect(sentBody().lines[1]).toEqual({ order_item: 8, quantity: 2, condition: 'sellable', already_in_stock: true });
  });

  it('sin ser superior, la línea devuelta a mano no se puede recibir', () => {
    renderPanel(order({ ...RECEIVING, manual_return_lines: [8], can_receive_by_hand: false }));
    const byHand = screen.getByTestId('order-return-by-hand-8');
    expect(within(byHand).queryByRole('checkbox')).toBeNull();
    expect(byHand).toHaveTextContent('corresponde a un superior');
    const quantity = screen.getByTestId('order-return-quantity-8');
    fireEvent.change(within(quantity).getByLabelText('Estado en que llega'), { target: { value: 'sellable' } });
    expect(within(screen.getByTestId('order-return-receive')).getByRole('button', { name: 'Recibir mercadería' })).toBeDisabled();
  });

  it('también vale la serie, sin importar mayúsculas', () => {
    renderPanel(order(RECEIVING));
    const device = screen.getByTestId('order-return-device-7-0');
    fireEvent.change(within(device).getByLabelText('IMEI o serie del equipo que vuelve'), {
      target: { value: 'f2lxk1abc' },
    });
    expect(device).toHaveTextContent('Coincide con un equipo que salió con este pedido.');
  });

  it('una cantidad no ofrece «A revisión»; un equipo sí', () => {
    renderPanel(order(RECEIVING));
    const quantity = within(screen.getByTestId('order-return-quantity-8')).getByLabelText('Estado en que llega');
    const device = within(screen.getByTestId('order-return-device-7-0')).getByLabelText('Estado en que llega');
    expect(optionValues(quantity)).toEqual(['', 'sellable', 'damaged']);
    expect(optionValues(device)).toEqual(['', 'sellable', 'review', 'damaged']);
  });

  it('explica a qué costo entra el stock y qué pasa con «A revisión»', () => {
    renderPanel(order(RECEIVING));
    const form = screen.getByTestId('order-return-receive');
    expect(form).toHaveTextContent('al costo con que salió en la venta');
    expect(form).toHaveTextContent('«A revisión» entra como «Apartado» hasta que alguien lo libere');
  });

  it('el rechazo del servidor (409) se enseña tal cual', async () => {
    send.mockResolvedValueOnce(reply(409, {
      detail: 'Ese equipo no es uno de los que salieron con este pedido.', code: 'unit_not_of_order',
    }));
    const { onChanged } = renderPanel(order({ ...RECEIVING, receive_branches: [{ id: 1, name: 'Miraflores' }] }));
    const device = screen.getByTestId('order-return-device-7-0');
    fireEvent.change(within(device).getByLabelText('IMEI o serie del equipo que vuelve'), { target: { value: IMEI } });
    fireEvent.change(within(device).getByLabelText('Estado en que llega'), { target: { value: 'sellable' } });
    fireEvent.change(within(screen.getByTestId('order-return-quantity-8')).getByLabelText('Estado en que llega'), {
      target: { value: 'sellable' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Recibir mercadería' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sí, recibir' }));
    expect(await screen.findByRole('alert'))
      .toHaveTextContent('Ese equipo no es uno de los que salieron con este pedido.');
    expect(onChanged).not.toHaveBeenCalled();
    expect(sentBody().branch).toBe(1);                   // una sola sucursal: elegida de antemano
  });
});

const CLOSING = { can_close: true, return: theReturn({ status: 'received', received_at: '2026-10-18T17:00:00Z' }) };

describe('cerrar', () => {
  it('calcula lo que se devuelve, exige el motivo del descuento, se confirma y manda el importe', async () => {
    const updated = order({ eligible: false, return: theReturn({ status: 'closed' }) });
    send.mockResolvedValueOnce(reply(200, updated));
    const { onChanged } = renderPanel(order(CLOSING));
    const form = screen.getByTestId('order-return-close');
    const close = within(form).getByRole('button', { name: 'Cerrar devolución' });
    expect(screen.getByTestId('order-return-paid')).toHaveTextContent('S/ 147.00');
    expect(screen.getByTestId('order-return-computed-refund')).toHaveTextContent('S/ 147.00');

    fireEvent.change(within(form).getByLabelText('Medio del reembolso'), { target: { value: 'cash' } });
    fireEvent.change(within(form).getByLabelText('Descuento (opcional)'), { target: { value: '10,5' } });
    expect(screen.getByTestId('order-return-computed-refund')).toHaveTextContent('S/ 136.50');
    expect(close).toBeDisabled();                        // falta el motivo del descuento
    fireEvent.change(within(form).getByLabelText('Motivo del descuento'), { target: { value: 'Flete  de vuelta' } });
    expect(close).toBeEnabled();

    fireEvent.click(close);
    expect(send).not.toHaveBeenCalled();
    const confirm = screen.getByTestId('order-return-close-confirm');
    expect(confirm).toHaveTextContent('La venta se cancela');
    expect(confirm).toHaveTextContent('S/ 136.50');
    expect(confirm).toHaveTextContent('PENDIENTE');
    expect(confirm).toHaveTextContent('nota de venta interna');
    expect(confirm).toHaveTextContent('nota de crédito');
    expect(confirm).toHaveTextContent('comisión');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Sí, cerrar devolución' }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(updated));
    expect(sentUrl()).toMatch(/\/admin\/orders\/42\/return\/close\/$/);
    expect(sentBody()).toEqual({
      refund_amount: '136.50', refund_method: 'cash', deduction_amount: '10.50', deduction_reason: 'Flete de vuelta',
    });
  });

  it('sin descuento manda sólo el importe y el medio', async () => {
    send.mockResolvedValueOnce(reply(200, order({ eligible: false })));
    renderPanel(order(CLOSING));
    const form = screen.getByTestId('order-return-close');
    expect(within(form).getByRole('button', { name: 'Cerrar devolución' })).toBeDisabled();   // falta el medio
    fireEvent.change(within(form).getByLabelText('Medio del reembolso'), { target: { value: 'transfer' } });
    expect(within(form).queryByLabelText('Motivo del descuento')).not.toBeInTheDocument();
    fireEvent.click(within(form).getByRole('button', { name: 'Cerrar devolución' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sí, cerrar devolución' }));
    await waitFor(() => expect(send).toHaveBeenCalled());
    expect(sentBody()).toEqual({ refund_amount: '147.00', refund_method: 'transfer' });
  });

  it('un descuento mayor que lo cobrado no se puede cerrar', () => {
    renderPanel(order(CLOSING));
    const form = screen.getByTestId('order-return-close');
    fireEvent.change(within(form).getByLabelText('Medio del reembolso'), { target: { value: 'cash' } });
    fireEvent.change(within(form).getByLabelText('Descuento (opcional)'), { target: { value: '200' } });
    fireEvent.change(within(form).getByLabelText('Motivo del descuento'), { target: { value: 'Flete de vuelta' } });
    expect(form).toHaveTextContent('El descuento no puede pasar de lo cobrado (S/ 147.00).');
    expect(within(form).getByRole('button', { name: 'Cerrar devolución' })).toBeDisabled();
  });

  it('el rechazo del servidor (409) se enseña tal cual', async () => {
    send.mockResolvedValueOnce(reply(409, { detail: 'Esta devolución ya se cerró.', code: 'already_closed' }));
    const { onChanged } = renderPanel(order(CLOSING));
    fireEvent.change(screen.getByLabelText('Medio del reembolso'), { target: { value: 'cash' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar devolución' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sí, cerrar devolución' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Esta devolución ya se cerró.');
    expect(onChanged).not.toHaveBeenCalled();
  });
});

describe('retirar', () => {
  it('pide el motivo y manda {reason}', async () => {
    const updated = order({ can_request: true, return: theReturn({ status: 'withdrawn' }) });
    send.mockResolvedValueOnce(reply(200, updated));
    const { onChanged } = renderPanel(order({ can_withdraw: true, return: theReturn() }));
    fireEvent.click(screen.getByRole('button', { name: 'Retirar devolución' }));
    const group = screen.getByTestId('order-return-withdraw');
    const confirm = within(group).getByRole('button', { name: 'Sí, retirar devolución' });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(group).getByLabelText('Motivo para retirar la devolución'), {
      target: { value: 'El cliente se lo queda' },
    });
    fireEvent.click(confirm);
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(updated));
    expect(sentUrl()).toMatch(/\/admin\/orders\/42\/return\/withdraw\/$/);
    expect(sentBody()).toEqual({ reason: 'El cliente se lo queda' });
  });

  it('un 403 del servidor se enseña con sus palabras', async () => {
    send.mockResolvedValueOnce(reply(403, {
      detail: 'Retira la devolución quien la registró, o un superior.', code: 'not_the_requester',
    }));
    renderPanel(order({ can_withdraw: true, return: theReturn() }));
    fireEvent.click(screen.getByRole('button', { name: 'Retirar devolución' }));
    fireEvent.change(screen.getByLabelText('Motivo para retirar la devolución'), { target: { value: 'Ya no vuelve' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sí, retirar devolución' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Retira la devolución quien la registró, o un superior.');
  });
});

describe('sólo lectura', () => {
  it('sin ningún `can_*` se ve la devolución y ningún botón', () => {
    renderPanel(order({ return: theReturn() }));
    const panel = screen.getByTestId('order-return-panel');
    expect(panel).toHaveTextContent('No enciende al llegar');
    expect(screen.getByTestId('order-return-read-only')).toHaveTextContent('Sólo lectura');
    expect(within(panel).queryAllByRole('button')).toHaveLength(0);
    expect(within(panel).queryAllByRole('textbox')).toHaveLength(0);
  });
});

describe('historial de despacho', () => {
  const DISPATCH = {
    kind: 'shipping', mode: 'national_address', current: 'delivered', current_label: 'Entregado', steps: [],
    forward: null, forward_label: '', back: null, back_label: '', reason_min: 5, blockers: [], may_manage: false,
    notice: '', ever_left: true, left_markings: [], may_correct: false,
  };
  const ENTRY = {
    old_status: '', new_status: '', old_label: '', new_label: '', note: '', direction: '', corrected: false,
    corrects_id: null, actor: 'luis.almacen', at: '2026-10-18T17:00:00Z',
  };

  it('enseña el nombre que da el servidor, y el motivo de una devolución como motivo', () => {
    const o = {
      id: 42,
      dispatch: {
        ...DISPATCH,
        history: [
          { ...ENTRY, id: 1, action: 'order_return_requested', label: 'Devolución registrada', note: 'No enciende' },
          { ...ENTRY, id: 2, action: 'order_return_received', label: 'Devolución recibida' },
          { ...ENTRY, id: 3, action: 'order_something_new' },
        ],
      },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<DispatchPanel order={o as any} onChanged={jest.fn()} />);
    expect(screen.getByTestId('dispatch-history-1')).toHaveTextContent('Devolución registrada');
    expect(screen.getByTestId('dispatch-history-1')).toHaveTextContent('Motivo: No enciende');
    expect(screen.getByTestId('dispatch-history-2')).toHaveTextContent('Devolución recibida');
    expect(screen.getByTestId('dispatch-history-2')).not.toHaveTextContent('order_return_received');
    expect(screen.getByTestId('dispatch-history-3')).toHaveTextContent('order_something_new');
  });
});
