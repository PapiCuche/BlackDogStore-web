import { useEffect } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { IN_FLIGHT_WINDOW_MS, PosInFlightNotice, usePosInFlight } from '@/app/admin/sales/pos/PosInFlight';

/**
 * AUTH-C (owner, 2026-10-25, points 4 and 5) — after signing in again, a sale
 * that was in flight is RESOLVED before anything else is charged.
 *
 *   · «Volver a comprobar» is ONLY a read: no charge is re-sent, no order made.
 *   · «not found» while the sale may still be arriving is UNCERTAIN, not «no».
 *   · An uncertain sale is resolved by a successful lookup or by the cashier's
 *     decision, RECORDED by the server (which refuses it if the sale exists).
 */

type Call = { url: string; method: string };
let calls: Call[] = [];
let lookup: () => Response;
let decision: () => Response;

function reply(status: number, body: unknown) {
  return { status, ok: status < 400, headers: { get: () => null }, json: () => Promise.resolve(body), clone() { return this; } } as unknown as Response;
}

beforeEach(() => {
  calls = [];
  window.sessionStorage.clear();
  document.cookie = 'csrftoken=csrf';
  lookup = () => reply(200, { found: false });
  decision = () => reply(200, { recorded: true });
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    calls.push({ url, method });
    if (url.includes('/sales/by-key/decision/')) return decision();
    if (url.includes('/sales/by-key/')) return lookup();
    return reply(200, {});
  }) as unknown as typeof fetch;
});

function mark(ageMs: number) {
  window.sessionStorage.setItem('pos-sale-in-flight', JSON.stringify({
    key: 'k-123', basket: 'abcd1234', at: Date.now() - ageMs, user: 7, company: 3,
  }));
}

const probe: { blocking: () => boolean } = { blocking: () => false };
const blocking = () => probe.blocking();

function Till() {
  const inFlight = usePosInFlight(3, 7);
  const { isBlocking } = inFlight;
  useEffect(() => {
    probe.blocking = isBlocking;
  });
  if (!inFlight.mark || !inFlight.state) return <p>Caja libre</p>;
  return (
    <PosInFlightNotice
      mark={inFlight.mark}
      state={inFlight.state}
      onRecheck={() => void inFlight.recheck()}
      onDecideNotCharged={() => void inFlight.decideNotCharged()}
      onAcknowledge={inFlight.acknowledge}
    />
  );
}

async function open() {
  await act(async () => { render(<Till />); });
  await act(async () => { await Promise.resolve(); });
}

const writes = () => calls.filter((c) => c.method !== 'GET');

it('a sale the server has: «sí se registró», and only then is the till free', async () => {
  lookup = () => reply(200, { found: true, order_id: 55, total: '120.00', paid_at: null, payment_method: 'cash', branch: 'Centro' });
  mark(5_000);
  await open();

  expect(screen.getByRole('alert')).toHaveTextContent('sí se registró: pedido #55 por S/ 120.00 en Centro');
  expect(blocking()).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Entendido' }));
  expect(screen.getByText('Caja libre')).toBeInTheDocument();
  expect(window.sessionStorage.getItem('pos-sale-in-flight')).toBeNull();
  expect(blocking()).toBe(false);
});

it('«not found» while it may still be arriving is uncertain, and the decision waits', async () => {
  mark(10_000);
  await open();

  expect(screen.getByRole('alert')).toHaveTextContent('puede estar llegando');
  expect(screen.getByRole('button', { name: /Confirmo que no se cobró/ })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Entendido' })).toBeNull();
  expect(blocking()).toBe(true);
});

it('«Volver a comprobar» only asks: no charge re-sent, no order made', async () => {
  mark(10_000);
  await open();

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Volver a comprobar' })); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Volver a comprobar' })); });

  expect(writes()).toEqual([]);
  expect(calls.every((c) => c.url.includes('/admin/pos/sales/by-key/?'))).toBe(true);
  expect(calls.length).toBe(3);
});

it('«not found» once the sale had time to arrive is «no se registró»', async () => {
  mark(IN_FLIGHT_WINDOW_MS + 5_000);
  await open();

  expect(screen.getByRole('alert')).toHaveTextContent('La venta anterior no se registró');
  fireEvent.click(screen.getByRole('button', { name: 'Entendido' }));
  expect(window.sessionStorage.getItem('pos-sale-in-flight')).toBeNull();
});

it('a failed lookup is uncertain; after the window the cashier decides and the server records it', async () => {
  lookup = () => reply(503, { detail: 'no' });
  mark(IN_FLIGHT_WINDOW_MS + 5_000);
  await open();
  expect(screen.getByRole('alert')).toHaveTextContent('No se pudo comprobar');
  expect(screen.queryByRole('button', { name: 'Entendido' })).toBeNull();

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirmo que no se cobró' })); });

  expect(writes()).toEqual([expect.objectContaining({ method: 'POST', url: expect.stringContaining('/sales/by-key/decision/') })]);
  expect(calls.some((c) => c.method === 'POST' && /\/pos\/sales\/(\?|$)/.test(c.url))).toBe(false);
  expect(screen.getByRole('alert')).toHaveTextContent('Quedó registrado que no se cobró');
});

it('the decision is refused when the sale exists after all, and says so', async () => {
  lookup = () => reply(503, { detail: 'no' });
  decision = () => reply(409, { found: true, order_id: 77, total: '50.00' });
  mark(IN_FLIGHT_WINDOW_MS + 5_000);
  await open();

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirmo que no se cobró' })); });

  expect(screen.getByRole('alert')).toHaveTextContent('sí se registró: pedido #77');
});

it('nothing is charged while the first answer is on its way', async () => {
  let release: (r: Response) => void = () => {};
  lookup = () => new Promise<Response>((resolve) => { release = resolve; }) as unknown as Response;
  mark(5_000);
  await act(async () => { render(<Till />); });

  expect(blocking()).toBe(true);
  await act(async () => { release(reply(200, { found: false })); });
});
