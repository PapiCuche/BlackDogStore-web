import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { OrderReviewSettings } from '@/app/admin/components/OrderReviewSettings';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * CLIENT-SEC-01 · the two order-review options of a company.
 *
 * Both start off. They are saved on their own — only their two fields travel —
 * so the rest of the configuration screen stays exactly what it was, and
 * whether this person may change them is the server's answer.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/api', () => ({ API_BASE: '/api' }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

let patches: { url: string; body: Record<string, unknown> }[];
let refusal: { status: number; body: unknown } | null;

function config(amount: string | null, hold: boolean) {
  return { settings: { risk_review_amount: amount, hold_orders_pending_review: hold }, can_manage_order_review: true };
}

beforeEach(() => {
  patches = [];
  refusal = null;
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const body = JSON.parse(String(init.body));
    patches.push({ url: String(input), body });
    if (refusal) return { ok: false, status: refusal.status, json: async () => refusal!.body } as Response;
    return {
      ok: true, status: 200,
      json: async () => config(body.risk_review_amount === null ? null : '1500.00', body.hold_orders_pending_review),
    } as Response;
  });
});

function shown(props: Partial<React.ComponentProps<typeof OrderReviewSettings>> = {}) {
  const onSaved = jest.fn();
  render(<OrderReviewSettings companyId={7} amount={null} hold={false} canEdit onSaved={onSaved} {...props} />);
  return onSaved;
}

const amount = () => screen.getByLabelText('Monto de revisión (S/)');
const hold = () => screen.getByRole('checkbox', { name: 'Retener los pedidos por revisar' });
const save = () => fireEvent.click(screen.getByRole('button', { name: 'Guardar revisión de pedidos' }));

test('both options start off, and the screen says what that means', () => {
  shown();
  expect(amount()).toHaveValue('');
  expect(hold()).not.toBeChecked();
  expect(screen.getByText(/funcionan como siempre/)).toBeInTheDocument();
  expect(screen.getByText(/no cancela pedidos ni toca pagos/)).toBeInTheDocument();
  expect(screen.getByText(/Apagado, la revisión es sólo informativa/)).toBeInTheDocument();
});

test('the hold names the three states it holds back', () => {
  // The server holds «listo para retiro» too: that state is what tells the buyer to come.
  shown();
  expect(screen.getByText(/no se puede marcar como listo para retiro, enviado o entregado hasta/)).toBeInTheDocument();
});

test('saving sends these two options and nothing else, to this company', async () => {
  const onSaved = shown();
  fireEvent.change(amount(), { target: { value: ' 1500,00 ' } });
  fireEvent.click(hold());
  save();

  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(patches).toEqual([{
    url: '/api/admin/company-settings/?company=7',
    body: { risk_review_amount: '1500.00', hold_orders_pending_review: true },
  }]);
  expect(await screen.findByText('Cambios guardados.')).toBeInTheDocument();
  expect(amount()).toHaveValue('1500.00');
  expect(hold()).toBeChecked();
});

test('an empty amount is «no amount», never a zero', async () => {
  const onSaved = shown({ amount: '900.00', hold: true });
  expect(amount()).toHaveValue('900.00');
  fireEvent.change(amount(), { target: { value: '  ' } });
  save();
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(patches[0].body).toEqual({ risk_review_amount: null, hold_orders_pending_review: true });
});

test('whoever may not change them reads them and is told what it takes', () => {
  shown({ amount: '900.00', hold: true, canEdit: false });
  expect(amount()).toBeDisabled();
  expect(amount()).toHaveValue('900.00');
  expect(hold()).toBeDisabled();
  expect(hold()).toBeChecked();
  expect(screen.queryByRole('button', { name: 'Guardar revisión de pedidos' })).not.toBeInTheDocument();
  expect(screen.getByText('settings.manage')).toBeInTheDocument();
});

test('an amount the server refuses is said under the amount', async () => {
  refusal = { status: 400, body: { risk_review_amount: ['Asegúrese de que este valor es mayor o igual a 0.01.'] } };
  const onSaved = shown();
  fireEvent.change(amount(), { target: { value: '0' } });
  save();
  expect(await screen.findByText('Asegúrese de que este valor es mayor o igual a 0.01.')).toBeInTheDocument();
  expect(amount()).toHaveAttribute('aria-invalid', 'true');
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.queryByText('Cambios guardados.')).not.toBeInTheDocument();
});

test('a refusal for lack of permission shows the server\'s reason', async () => {
  refusal = { status: 403, body: { detail: 'No tienes permisos para cambiar la revisión de pedidos de esta empresa.' } };
  const onSaved = shown();
  fireEvent.click(hold());
  save();
  expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permisos para cambiar la revisión de pedidos');
  expect(onSaved).not.toHaveBeenCalled();
});

test('while a save is on its way it cannot be sent again', async () => {
  let answer: (value: Response) => void = () => {};
  send.mockReturnValue(new Promise<Response>((resolve) => { answer = resolve; }));
  const onSaved = shown();
  fireEvent.click(hold());
  save();

  expect(await screen.findByRole('button', { name: 'Guardando…' })).toBeDisabled();
  expect(hold()).toBeDisabled();
  answer({ ok: true, status: 200, json: async () => config(null, true) } as Response);
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  expect(send).toHaveBeenCalledTimes(1);
});
