import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { OrderRiskCard } from '@/app/admin/components/OrderRiskCard';
import { OrdersTable, orderRiskBadge } from '@/app/admin/components/OrdersTable';
import { fetchAdminOrders, reviewOrderRisk, type AdminOrder, type OrderRisk } from '@/app/lib/admin';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * CLIENT-SEC-01 · the review of an order, in the panel.
 *
 * The list marks only what asks for attention; the card shows the server's
 * level and reasons, and offers the decision only to whoever the SERVER says
 * may decide. The decision is advice and is sent once.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  SELECTED_COMPANY_KEY: 'internal-selected-company',
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function order(overrides: Partial<AdminOrder>): AdminOrder {
  return {
    id: 1, customer_name: 'Cliente', customer_email: 'c@example.com', user_id: null, username: null,
    total: '100.00', status: 'paid', fulfillment_status: 'pending', paid: true,
    created_at: '2026-01-01T00:00:00Z', paid_at: null, item_count: 1, has_stock_shortfall: false,
    ...overrides,
  };
}

function risk(overrides: Partial<OrderRisk> = {}): OrderRisk {
  return {
    level: 'high', level_label: 'Alto', score: 70,
    signals: [
      { code: 'guest_checkout', label: 'Compra sin cuenta.' },
      { code: 'velocity_ip', label: 'Varios pedidos desde el mismo origen en la última hora.' },
    ],
    review_status: 'pending', review_status_label: 'Por revisar',
    reviewed_by: null, reviewed_at: null, review_note: '',
    assessed_at: '2026-10-12T15:00:00Z', can_review: true,
    ...overrides,
  };
}

beforeEach(() => {
  send.mockReset();
});

describe('the orders list', () => {
  it.each([
    [{ risk_level: null, review_status: null }, null],
    [{}, null],                                                      // a server that does not say: nothing
    [{ risk_level: 'low', review_status: 'not_required' }, null],
    [{ risk_level: 'medium', review_status: 'not_required' }, 'Riesgo medio'],
    [{ risk_level: 'high', review_status: 'approved' }, 'Riesgo alto'],
    [{ risk_level: 'high', review_status: 'pending' }, 'Por revisar · riesgo alto'],
    [{ risk_level: 'low', review_status: 'pending' }, 'Por revisar'],
  ] as const)('%j says %j', (fields, text) => {
    expect(orderRiskBadge(fields as Pick<AdminOrder, 'risk_level' | 'review_status'>)).toBe(text);
  });

  it('marks only the orders that ask for attention, in words', () => {
    render(
      <OrdersTable
        orders={[
          order({ id: 1, risk_level: 'low', review_status: 'not_required' }),
          order({ id: 2, risk_level: 'high', review_status: 'pending' }),
          order({ id: 3 }),
          order({ id: 4, risk_level: 'medium', review_status: 'not_required' }),
        ]}
      />,
    );
    const badges = screen.getAllByTestId('order-risk-badge');
    expect(badges.map((badge) => badge.textContent)).toEqual(['Por revisar · riesgo alto', 'Riesgo medio']);
  });

  it('the «Por revisar» filter is asked of the server', async () => {
    send.mockResolvedValue(reply(200, { count: 0, page: 1, page_size: 25, results: [] }));
    await fetchAdminOrders({ review: 'pending', page: 1 });
    expect(String(send.mock.calls[0][0])).toContain('review=pending');

    await fetchAdminOrders({ page: 1 });
    expect(String(send.mock.calls[1][0])).not.toContain('review=');
  });
});

describe('the review card', () => {
  it('shows nothing for an order that was not assessed', () => {
    const { container } = render(<OrderRiskCard orderId={7} risk={null} onReviewed={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the level and the reasons in the server\'s words', () => {
    render(<OrderRiskCard orderId={7} risk={risk()} onReviewed={jest.fn()} />);
    expect(screen.getByRole('heading', { name: 'Revisión del pedido' })).toBeInTheDocument();
    expect(screen.getByText('Alto')).toBeInTheDocument();
    expect(screen.getByText('Por revisar')).toBeInTheDocument();
    expect(screen.getByText('Compra sin cuenta.')).toBeInTheDocument();
    expect(screen.getByText('Varios pedidos desde el mismo origen en la última hora.')).toBeInTheDocument();
    expect(screen.queryByText('guest_checkout')).not.toBeInTheDocument();     // words, not codes
  });

  it('offers the decision only to whoever the server says may decide', () => {
    render(<OrderRiskCard orderId={7} risk={risk({ can_review: false })} onReviewed={jest.fn()} />);
    expect(screen.queryByRole('button', { name: 'Aprobar' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Rechazar' })).not.toBeInTheDocument();
    expect(screen.getByText(/Espera la decisión/)).toBeInTheDocument();
  });

  it('says that the decision is advice', () => {
    render(<OrderRiskCard orderId={7} risk={risk()} onReviewed={jest.fn()} />);
    expect(screen.getByText(/no cancela el pedido, no devuelve el pago/)).toBeInTheDocument();
  });

  it.each([['Aprobar', 'approve'], ['Rechazar', 'reject']] as const)('%s sends that decision with the note, once', async (button, decision) => {
    const decided = risk({ review_status: decision === 'approve' ? 'approved' : 'rejected', can_review: false });
    send.mockResolvedValue(reply(200, { risk: decided }));
    const onReviewed = jest.fn();
    render(<OrderRiskCard orderId={7} risk={risk()} onReviewed={onReviewed} />);

    fireEvent.change(screen.getByLabelText('Nota (opcional)'), { target: { value: '  La clienta confirmó.  ' } });
    fireEvent.click(screen.getByRole('button', { name: button }));
    for (const again of screen.getAllByRole('button')) fireEvent.click(again);             // a second tap, on either

    await waitFor(() => expect(onReviewed).toHaveBeenCalledWith(decided));
    expect(send).toHaveBeenCalledTimes(1);
    const [url, init] = send.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/orders\/7\/risk-review\/$/);
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ decision, note: 'La clienta confirmó.' });
  });

  it('while a decision is on its way neither button can be pressed again', async () => {
    let answer: (value: Response) => void = () => {};
    send.mockReturnValue(new Promise<Response>((resolve) => { answer = resolve; }));
    const onReviewed = jest.fn();
    render(<OrderRiskCard orderId={7} risk={risk()} onReviewed={onReviewed} />);
    fireEvent.click(screen.getByRole('button', { name: 'Rechazar' }));

    expect(await screen.findByRole('button', { name: 'Guardando…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Aprobar' })).toBeDisabled();
    answer(reply(200, { risk: risk({ review_status: 'rejected', can_review: false }) }));
    await waitFor(() => expect(onReviewed).toHaveBeenCalledTimes(1));
  });

  it('a review that was already decided shows who, when and why, and no buttons', () => {
    render(
      <OrderRiskCard
        orderId={7}
        risk={risk({
          review_status: 'rejected', review_status_label: 'Rechazado', reviewed_by: 'Gema Gestora',
          reviewed_at: '2026-10-12T16:00:00Z', review_note: 'No contestó.', can_review: false,
        })}
        onReviewed={jest.fn()}
      />,
    );
    expect(screen.getByText('Rechazado')).toBeInTheDocument();
    expect(screen.getByText('Gema Gestora')).toBeInTheDocument();
    expect(screen.getByText('No contestó.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Aprobar' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Espera la decisión/)).not.toBeInTheDocument();
  });

  it('when the server refuses, its reason is shown and nothing is called decided', async () => {
    send.mockResolvedValue(reply(409, { detail: 'La revisión de este pedido no está pendiente: ya se decidió o no hacía falta.', code: 'review_not_pending' }));
    const onReviewed = jest.fn();
    render(<OrderRiskCard orderId={7} risk={risk()} onReviewed={onReviewed} />);
    fireEvent.click(screen.getByRole('button', { name: 'Aprobar' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('ya se decidió');
    expect(onReviewed).not.toHaveBeenCalled();
  });

  it('an order with nothing noticed says so', () => {
    render(<OrderRiskCard orderId={7} risk={risk({ level: 'low', level_label: 'Bajo', signals: [], review_status: 'not_required', review_status_label: 'No requiere revisión', can_review: false })} onReviewed={jest.fn()} />);
    expect(screen.getByText('No se observó nada fuera de lo habitual.')).toBeInTheDocument();
    expect(screen.queryByText(/Espera la decisión/)).not.toBeInTheDocument();
  });
});

describe('the client of the review', () => {
  it('answers with the assessment the server sent', async () => {
    const decided = risk({ review_status: 'approved', can_review: false });
    send.mockResolvedValue(reply(200, { risk: decided }));
    await expect(reviewOrderRisk(9, 'approve', '')).resolves.toEqual(decided);
  });

  it.each([[403, 'No tienes permisos'], [404, 'Orden no encontrada'], [500, 'No se pudo registrar']])(
    'a %i is an error with words', async (status, words) => {
      send.mockResolvedValue(reply(status, {}));
      await expect(reviewOrderRisk(9, 'reject', '')).rejects.toThrow(words);
    },
  );
});
