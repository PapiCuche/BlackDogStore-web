import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { fetchWithAuth } from '@/app/lib/auth';
import { DispatchPanel } from '@/app/admin/components/DispatchPanel';

/**
 * ORDER-FULFILLMENT-SAFETY-01 — el panel «Estado de despacho» del detalle de
 * un pedido. Lo que se fija:
 *
 *  - el recorrido es el de la modalidad que dice el servidor: un recojo no
 *    pasa por «Enviado» y un envío no pasa por «Listo para retiro»;
 *  - sin permiso se ve todo (pasos e historial) y no hay ningún botón;
 *  - un paso a la vez, el que dice el servidor; «Entregado» se confirma antes;
 *    volver atrás exige el motivo; lo que falta deshabilita el paso y se lista;
 *  - el motivo de un rechazo es el del servidor, tal cual;
 *  - el historial marca lo corregido; la corrección de un marcado de salida
 *    exige motivo y la declaración, y sólo la ve quien puede corregir.
 *
 * Se simula la red (`fetchWithAuth`), no las funciones del API: así también se
 * comprueba lo que se manda al servidor.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  SELECTED_COMPANY_KEY: 'internal-selected-company',
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

const PICKUP_STEPS = [
  { status: 'pending', label: 'Pendiente', state: 'done', at: null, actor: '' },
  { status: 'preparing', label: 'En preparación', state: 'current', at: '2026-10-16T15:00:00Z', actor: 'ana.ventas' },
  { status: 'ready_for_pickup', label: 'Listo para retiro', state: 'todo', at: null, actor: '' },
  { status: 'delivered', label: 'Entregado', state: 'todo', at: null, actor: '' },
];

const SHIPPING_STEPS = [
  { status: 'pending', label: 'Pendiente', state: 'done', at: null, actor: '' },
  { status: 'preparing', label: 'En preparación', state: 'current', at: '2026-10-16T15:00:00Z', actor: 'ana.ventas' },
  { status: 'shipped', label: 'Enviado', state: 'todo', at: null, actor: '' },
  { status: 'delivered', label: 'Entregado', state: 'todo', at: null, actor: '' },
];

const HISTORY_PREPARING = {
  id: 11, at: '2026-10-16T15:00:00Z', action: 'order_fulfillment_status_changed', actor: 'ana.ventas',
  old_status: 'pending', new_status: 'preparing', old_label: 'Pendiente', new_label: 'En preparación',
  note: 'Se empezó a armar', direction: 'forward', corrected: false, corrects_id: null,
};

function dispatch(overrides: Record<string, unknown> = {}) {
  return {
    kind: 'pickup',
    mode: 'pickup_store',
    current: 'preparing',
    current_label: 'En preparación',
    steps: PICKUP_STEPS,
    forward: 'ready_for_pickup',
    forward_label: 'Listo para retiro',
    back: 'pending',
    back_label: 'Pendiente',
    reason_min: 5,
    blockers: [],
    may_manage: true,
    notice: '',
    ever_left: false,
    history: [HISTORY_PREPARING],
    left_markings: [],
    may_correct: false,
    ...overrides,
  };
}

function order(dispatchOverrides: Record<string, unknown> = {}, overrides: Record<string, unknown> = {}) {
  return {
    id: 42,
    status: 'paid',
    paid: true,
    fulfillment_status: 'preparing',
    available_fulfillment_transitions: ['pending', 'ready_for_pickup'],
    dispatch: dispatch(dispatchOverrides),
    ...overrides,
  };
}

function renderPanel(o: ReturnType<typeof order>) {
  const onChanged = jest.fn();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  render(<DispatchPanel order={o as any} onChanged={onChanged} />);
  return { onChanged, panel: screen.getByTestId('dispatch-panel') };
}

function sentBody(call = 0) {
  return JSON.parse(String(send.mock.calls[call][1]?.body));
}

beforeEach(() => send.mockReset());

describe('el recorrido de la modalidad', () => {
  it('un recojo en tienda no muestra «Enviado»', () => {
    const { panel } = renderPanel(order());
    const steps = within(panel).getByRole('list', { name: 'Pasos del despacho' });
    expect(within(steps).getAllByRole('listitem').map((li) => li.getAttribute('data-testid'))).toEqual([
      'dispatch-step-pending', 'dispatch-step-preparing', 'dispatch-step-ready_for_pickup', 'dispatch-step-delivered',
    ]);
    expect(steps).toHaveTextContent('Listo para retiro');
    expect(panel).not.toHaveTextContent('Enviado');
    expect(screen.getByTestId('dispatch-kind')).toHaveTextContent('Recojo en tienda');
  });

  it('un envío no muestra «Listo para retiro»', () => {
    const { panel } = renderPanel(order({
      kind: 'shipping', mode: 'national_address', steps: SHIPPING_STEPS,
      forward: 'shipped', forward_label: 'Enviado',
    }));
    const steps = within(panel).getByRole('list', { name: 'Pasos del despacho' });
    expect(steps).toHaveTextContent('Enviado');
    expect(panel).not.toHaveTextContent('Listo para retiro');
    expect(screen.getByTestId('dispatch-kind')).toHaveTextContent('Envío');
    expect(screen.getByTestId('dispatch-kind')).toHaveTextContent('Envío nacional a domicilio');
  });

  it('cada paso dice su estado, su fecha y quién lo marcó', () => {
    renderPanel(order());
    const current = screen.getByTestId('dispatch-step-preparing');
    expect(current).toHaveAttribute('data-state', 'current');
    expect(current).toHaveAttribute('aria-current', 'step');
    expect(current).toHaveTextContent('ana.ventas');
    expect(current).toHaveTextContent('16/10/2026');
    expect(screen.getByTestId('dispatch-step-pending')).toHaveAttribute('data-state', 'done');
    expect(screen.getByTestId('dispatch-step-delivered')).toHaveAttribute('data-state', 'todo');
    expect(screen.getByTestId('dispatch-current')).toHaveTextContent('En preparación');
  });

  it('el aviso del servidor se enseña tal cual', () => {
    renderPanel(order({ notice: 'Pedido cancelado: su despacho ya no cambia.' }));
    expect(screen.getByTestId('dispatch-notice')).toHaveTextContent('Pedido cancelado: su despacho ya no cambia.');
  });
});

describe('sólo lectura', () => {
  it('sin permiso se ven los pasos y el historial, y ningún botón', () => {
    const { panel } = renderPanel(order({ may_manage: false, forward: null, forward_label: '', back: null, back_label: '' }));
    expect(within(panel).getByRole('list', { name: 'Pasos del despacho' })).toBeInTheDocument();
    expect(screen.getByTestId('dispatch-history')).toHaveTextContent('Pendiente → En preparación');
    expect(screen.getByTestId('dispatch-read-only')).toBeInTheDocument();
    expect(within(panel).queryAllByRole('button')).toHaveLength(0);
  });

  it('aunque el pedido traiga pasos, sin permiso no se ofrecen', () => {
    const { panel } = renderPanel(order({ may_manage: false }));
    expect(within(panel).queryAllByRole('button')).toHaveLength(0);
  });

  it('sin `dispatch` no se pinta nada', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const { container } = render(<DispatchPanel order={{ id: 1, dispatch: null } as any} onChanged={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('avanzar y volver', () => {
  it('el botón marca el paso siguiente y entrega el pedido que devuelve el servidor', async () => {
    const saved = order({ current: 'ready_for_pickup' }, { fulfillment_status: 'ready_for_pickup' });
    send.mockResolvedValue(reply(200, saved));
    const { onChanged } = renderPanel(order());
    fireEvent.change(screen.getByLabelText('Nota (opcional)'), { target: { value: 'Listo en caja' } });
    fireEvent.click(screen.getByRole('button', { name: 'Marcar como Listo para retiro' }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(saved));
    expect(String(send.mock.calls[0][0])).toContain('/admin/orders/42/fulfillment-status/');
    expect(send.mock.calls[0][1]?.method).toBe('PATCH');
    expect(sentBody()).toEqual({ fulfillment_status: 'ready_for_pickup', note: 'Listo en caja' });
  });

  it('«Entregado» pide confirmar la entrega antes de mandarse', async () => {
    send.mockResolvedValue(reply(200, order()));
    renderPanel(order({
      current: 'ready_for_pickup', forward: 'delivered', forward_label: 'Entregado',
      back: 'preparing', back_label: 'En preparación',
    }));
    fireEvent.click(screen.getByRole('button', { name: 'Marcar como Entregado' }));
    expect(send).not.toHaveBeenCalled();
    const confirm = screen.getByTestId('dispatch-confirm-delivered');
    expect(confirm).toHaveTextContent('el producto se entregó al cliente o a quien lo recibe');

    fireEvent.click(within(confirm).getByRole('button', { name: 'Sí, se entregó' }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(sentBody()).toEqual({ fulfillment_status: 'delivered', note: '' });
  });

  it('desde la confirmación se puede volver sin mandar nada', () => {
    renderPanel(order({ forward: 'delivered', forward_label: 'Entregado' }));
    fireEvent.click(screen.getByRole('button', { name: 'Marcar como Entregado' }));
    fireEvent.click(within(screen.getByTestId('dispatch-confirm-delivered')).getByRole('button', { name: 'Volver' }));
    expect(screen.queryByTestId('dispatch-confirm-delivered')).toBeNull();
    expect(send).not.toHaveBeenCalled();
  });

  it('volver atrás exige el motivo, y lo manda como nota', async () => {
    send.mockResolvedValue(reply(200, order()));
    renderPanel(order());
    fireEvent.click(screen.getByRole('button', { name: 'Volver a Pendiente' }));
    const field = screen.getByLabelText('Motivo para volver a «Pendiente»');
    const submit = screen.getByRole('button', { name: 'Volver a Pendiente' });
    expect(submit).toBeDisabled();
    fireEvent.change(field, { target: { value: '  ab   c ' } });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(send).not.toHaveBeenCalled();

    fireEvent.change(field, { target: { value: 'El cliente   cambió de equipo' } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(sentBody()).toEqual({ fulfillment_status: 'pending', note: 'El cliente cambió de equipo' });
  });

  it('el mínimo del motivo es el que dice el servidor', () => {
    renderPanel(order({ reason_min: 12 }));
    fireEvent.click(screen.getByRole('button', { name: 'Volver a Pendiente' }));
    fireEvent.change(screen.getByLabelText('Motivo para volver a «Pendiente»'), { target: { value: 'Diez letra' } });
    expect(screen.getByRole('button', { name: 'Volver a Pendiente' })).toBeDisabled();
  });

  it('el 400 del servidor sobre el motivo se enseña tal cual', async () => {
    send.mockResolvedValue(reply(400, {
      detail: 'Volver al paso anterior exige un motivo (al menos 5 caracteres).', code: 'reason_required',
    }));
    renderPanel(order());
    fireEvent.click(screen.getByRole('button', { name: 'Volver a Pendiente' }));
    fireEvent.change(screen.getByLabelText('Motivo para volver a «Pendiente»'), { target: { value: 'Motivo largo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Volver a Pendiente' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Volver al paso anterior exige un motivo (al menos 5 caracteres).');
  });

  it('lo que falta deshabilita el paso siguiente y se lista', () => {
    renderPanel(order({
      kind: 'shipping', steps: SHIPPING_STEPS, forward: 'shipped', forward_label: 'Enviado',
      blockers: [
        { code: 'units_pending', detail: 'Falta identificar 1 equipo(s) (IMEI o serie).' },
        { code: 'logistics_missing', detail: 'Para marcarlo como enviado falta el transportista en la tarjeta «Entrega».' },
      ],
    }));
    const button = screen.getByRole('button', { name: 'Marcar como Enviado' });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(send).not.toHaveBeenCalled();
    const list = screen.getByTestId('dispatch-blockers');
    expect(list).toHaveTextContent('Falta identificar 1 equipo(s) (IMEI o serie).');
    expect(list).toHaveTextContent('falta el transportista');
  });

  it('el 409 del servidor se enseña tal cual y el pedido no cambia', async () => {
    send.mockResolvedValue(reply(409, {
      detail: 'Hay productos bajo pedido que aún no se recibieron del proveedor.', code: 'supply_pending',
    }));
    const { onChanged } = renderPanel(order());
    fireEvent.click(screen.getByRole('button', { name: 'Marcar como Listo para retiro' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Hay productos bajo pedido que aún no se recibieron del proveedor.');
    expect(onChanged).not.toHaveBeenCalled();
  });
});

describe('historial de despacho', () => {
  it('lista cada entrada con su fecha, quién, el cambio y la nota; lo corregido lleva su marca', () => {
    renderPanel(order({
      history: [
        HISTORY_PREPARING,
        { ...HISTORY_PREPARING, id: 12, at: '2026-10-16T15:10:00Z', old_status: 'preparing', new_status: 'shipped',
          old_label: 'En preparación', new_label: 'Enviado', note: '', corrected: true },
        { ...HISTORY_PREPARING, id: 13, at: '2026-10-16T16:00:00Z', action: 'order_left_marking_corrected',
          actor: 'jefe', old_status: '', new_status: 'preparing', old_label: '', new_label: 'En preparación',
          note: 'Se marcó por error', direction: '', corrects_id: 12 },
        { ...HISTORY_PREPARING, id: 14, action: 'order_delivery_updated', old_label: '', new_label: '', note: '' },
        { ...HISTORY_PREPARING, id: 15, action: 'order_cancelled', old_label: '', new_label: '', note: 'No pagó' },
      ],
    }));
    const first = screen.getByTestId('dispatch-history-11');
    expect(first).toHaveTextContent('Pendiente → En preparación');
    expect(first).toHaveTextContent('ana.ventas');
    expect(first).toHaveTextContent('Nota: Se empezó a armar');
    expect(first).not.toHaveTextContent('Corregido');

    // Un recojo «enviado»: incoherente, pero se enseña como se escribió.
    const corrected = screen.getByTestId('dispatch-history-12');
    expect(corrected).toHaveTextContent('En preparación → Enviado');
    expect(within(corrected).getByText('Corregido')).toBeInTheDocument();

    const correction = screen.getByTestId('dispatch-history-13');
    expect(correction).toHaveTextContent('Marcado de salida corregido');
    expect(correction).toHaveTextContent('En preparación → Enviado');
    expect(correction).toHaveTextContent('Motivo: Se marcó por error');
    expect(screen.getByTestId('dispatch-history-14')).toHaveTextContent('Datos de entrega actualizados');
    expect(screen.getByTestId('dispatch-history-15')).toHaveTextContent('Pedido cancelado');
    expect(screen.getByTestId('dispatch-history-15')).toHaveTextContent('Motivo: No pagó');

    const order_ = within(screen.getByTestId('dispatch-history')).getAllByRole('listitem')
      .map((li) => li.getAttribute('data-testid'));
    expect(order_).toEqual(['dispatch-history-11', 'dispatch-history-12', 'dispatch-history-13',
      'dispatch-history-14', 'dispatch-history-15']);
  });
});

describe('corregir un marcado de salida', () => {
  const MARKING = {
    audit_id: 12, at: '2026-10-16T15:10:00Z', old_status: 'preparing', new_status: 'shipped', actor: 'ana.ventas',
    corrected: false, correction: null, can_correct: true, blockers: [],
  };

  it('exige el motivo y la declaración, y manda never_left: true', async () => {
    const saved = order({ left_markings: [{ ...MARKING, corrected: true }] });
    send.mockResolvedValue(reply(201, { ...saved, correction_created: true }));
    const { onChanged } = renderPanel(order({ may_correct: true, left_markings: [MARKING] }));
    const section = screen.getByTestId('left-marking-corrections');
    expect(section).toHaveTextContent('El registro original se conserva');
    expect(section).toHaveTextContent('NO cancela el pedido, no reembolsa ni mueve stock');

    fireEvent.click(within(section).getByRole('button', { name: 'Corregir este registro' }));
    const submit = within(section).getByRole('button', { name: 'Corregir registro' });
    expect(submit).toBeDisabled();

    fireEvent.change(within(section).getByLabelText('Motivo de la corrección'), { target: { value: 'Clic por error' } });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(send).not.toHaveBeenCalled();

    const declaration = within(section).getByLabelText('Confirmo que el producto nunca salió de la tienda');
    fireEvent.click(declaration);
    expect(submit).toBeEnabled();
    fireEvent.click(declaration);
    expect(submit).toBeDisabled();

    fireEvent.change(within(section).getByLabelText('Motivo de la corrección'), { target: { value: 'abc' } });
    fireEvent.click(declaration);
    expect(submit).toBeDisabled();

    fireEvent.change(within(section).getByLabelText('Motivo de la corrección'), { target: { value: 'Clic por error' } });
    fireEvent.click(submit);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(String(send.mock.calls[0][0])).toContain('/admin/orders/42/left-markings/12/correct/');
    expect(send.mock.calls[0][1]?.method).toBe('POST');
    expect(sentBody()).toEqual({ reason: 'Clic por error', never_left: true });
  });

  it('el rechazo del servidor se enseña tal cual', async () => {
    send.mockResolvedValue(reply(409, {
      detail: 'No se puede corregir: La tarjeta «Entrega» tiene una guía o un código de seguimiento.',
      code: 'contradicting_evidence',
    }));
    renderPanel(order({ may_correct: true, left_markings: [MARKING] }));
    const section = screen.getByTestId('left-marking-corrections');
    fireEvent.click(within(section).getByRole('button', { name: 'Corregir este registro' }));
    fireEvent.change(within(section).getByLabelText('Motivo de la corrección'), { target: { value: 'Clic por error' } });
    fireEvent.click(within(section).getByLabelText('Confirmo que el producto nunca salió de la tienda'));
    fireEvent.click(within(section).getByRole('button', { name: 'Corregir registro' }));
    expect(await within(section).findByRole('alert')).toHaveTextContent(
      'No se puede corregir: La tarjeta «Entrega» tiene una guía o un código de seguimiento.');
  });

  it('un registro que no se puede corregir dice por qué y no ofrece el botón', () => {
    renderPanel(order({
      may_correct: true,
      left_markings: [{
        ...MARKING, can_correct: false,
        blockers: [{ code: 'unit_left', detail: 'Un equipo identificado de este pedido figura como salido de la tienda.' }],
      }],
    }));
    const marking = screen.getByTestId('left-marking-12');
    expect(marking).toHaveTextContent('No se puede corregir');
    expect(marking).toHaveTextContent('Un equipo identificado de este pedido figura como salido de la tienda.');
    expect(within(marking).queryByRole('button')).toBeNull();
  });

  it('un registro ya corregido dice cuándo, quién y por qué', () => {
    renderPanel(order({
      may_correct: true,
      left_markings: [{
        ...MARKING, corrected: true, can_correct: false,
        correction: { at: '2026-10-16T16:00:00Z', actor: 'jefe', reason: 'Se marcó por error' },
      }],
    }));
    const marking = screen.getByTestId('left-marking-12');
    expect(marking).toHaveTextContent(/Corregido el 16\/10\/2026.* por jefe: Se marcó por error/);
    expect(within(marking).queryByRole('button')).toBeNull();
  });

  it('sin may_correct no aparece', () => {
    renderPanel(order({ may_correct: false, left_markings: [MARKING] }));
    expect(screen.queryByTestId('left-marking-corrections')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Corregir este registro' })).toBeNull();
    expect(screen.queryByText('Corregir marcado de salida')).toBeNull();
  });
});
