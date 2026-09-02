import { useRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  PAYMENT_METHODS, ServiceApiError, makeIdempotencyKey,
  type ServicePaymentSummary,
} from '@/app/lib/service-console';

/**
 * The payment console. M12B, on the H3 runner.
 *
 * WHAT THESE ARE FOR. This is the first screen in the product that moves money,
 * and the failure modes are specific: arithmetic done twice in two places, a
 * key that changes between a click and its retry, a null balance drawn as zero,
 * and a button offered to somebody the server will refuse.
 */

const summary = (over: Partial<ServicePaymentSummary> = {}): ServicePaymentSummary => ({
  currency: 'PEN',
  quoted_total: '500.00',
  confirmed_paid: '200.00',
  outstanding: '300.00',
  credit: '0.00',
  payment_status: 'partial',
  requires_payment_before_delivery: false,
  ...over,
});

describe('the payment method list', () => {
  it('offers exactly the four the server accepts', () => {
    expect(PAYMENT_METHODS.map((m) => m.value))
      .toEqual(['cash', 'card', 'transfer', 'other']);
  });

  it('does NOT offer online', () => {
    // It names a gateway flow nobody built. The server refuses it in the
    // service layer AND in a database constraint, so a screen that showed it
    // would only be promising a 400.
    expect(PAYMENT_METHODS.map((m) => m.value)).not.toContain('online');
  });
});

describe('the summary contract', () => {
  it('carries every figure as a STRING, never a number', () => {
    // Parsing them would create a second answer to "how much is owed" that can
    // disagree with the server's — and the one that disagrees is always the one
    // a customer is looking at.
    const s = summary();
    for (const value of [s.quoted_total, s.confirmed_paid, s.outstanding, s.credit]) {
      expect(typeof value).toBe('string');
    }
  });

  it('allows a NULL total and a NULL balance, which are not zero', () => {
    const s = summary({ quoted_total: null, outstanding: null, payment_status: 'no_quote' });
    expect(s.quoted_total).toBeNull();
    expect(s.outstanding).toBeNull();
    expect(s.payment_status).toBe('no_quote');
  });

  it('reports the tenant policy so a screen can explain a refusal', () => {
    expect(summary({ requires_payment_before_delivery: true })
      .requires_payment_before_delivery).toBe(true);
  });
});

describe('ServiceApiError distinguishes the two 409s', () => {
  it('tells payment_required from idempotency_conflict', () => {
    // A counter must draw "saldo pendiente" rather than a generic failure, and
    // must not parse Spanish to tell them apart.
    const blocked = new ServiceApiError('Saldo pendiente: 300.00 PEN.', 409, 'payment_required');
    const conflict = new ServiceApiError('Clave repetida.', 409, 'idempotency_conflict');
    expect(blocked.code).toBe('payment_required');
    expect(conflict.code).toBe('idempotency_conflict');
    expect(blocked.status).toBe(conflict.status);
    expect(blocked.code).not.toEqual(conflict.code);
  });
});

describe('idempotency keys for money', () => {
  it('differ when the amount differs', () => {
    expect(makeIdempotencyKey('7:200.00:cash:')).not.toEqual(
      makeIdempotencyKey('7:300.00:cash:'),
    );
  });

  it('differ when the method differs', () => {
    expect(makeIdempotencyKey('7:200.00:cash:')).not.toEqual(
      makeIdempotencyKey('7:200.00:card:'),
    );
  });

  it('fit the column the server declares', () => {
    expect(makeIdempotencyKey('7:200.00:cash:' + 'x'.repeat(400)).length)
      .toBeLessThanOrEqual(64);
  });
});

/**
 * A minimal stand-in for the panel's key handling, exercised the way a real
 * double-click exercises it. The panel itself holds the map outside render
 * state; this asserts the PROPERTY that makes that necessary.
 */
function KeyProbe({ onSubmit }: { onSubmit: (key: string) => void }) {
  // A REF, not a local. A Map created during render is a new Map on every
  // render, so the key would change between a click and its retry — which is
  // exactly the bug this probe exists to rule out, and the reason the real
  // panel holds its map outside render state too.
  const keys = useRef(new Map<string, string>());
  const keyFor = (shape: string) => {
    const existing = keys.current.get(shape);
    if (existing) return existing;
    const minted = makeIdempotencyKey(shape);
    keys.current.set(shape, minted);
    return minted;
  };
  return (
    <button type="button" onClick={() => onSubmit(keyFor('7:200.00:cash:'))}>
      Registrar pago
    </button>
  );
}

describe('a double-click sends one intention', () => {
  it('reuses the key for the same amount, method and reference', async () => {
    const sent: string[] = [];
    render(<KeyProbe onSubmit={(k) => sent.push(k)} />);
    const button = screen.getByRole('button', { name: 'Registrar pago' });
    await userEvent.click(button);
    await userEvent.click(button);
    // Two requests, ONE key. The server answers the second from the first.
    expect(sent).toHaveLength(2);
    expect(new Set(sent).size).toBe(1);
  });
});

describe('the confirmation before money moves', () => {
  it('is required, and backing out sends nothing', async () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { Confirm } = require('@/app/admin/service/components/ServiceUi');
    const onConfirm = jest.fn();
    render(
      <Confirm
        label="Registrar pago"
        question="¿Registrar PEN 200.00? No se puede editar después."
        onConfirm={onConfirm}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Registrar pago' }));
    expect(screen.getByText(/No se puede editar después/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'No' }));
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('says a reversal returns no money', async () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { Confirm } = require('@/app/admin/service/components/ServiceUi');
    render(
      <Confirm
        label="Reversar"
        question="¿Marcar este pago como registrado por error? No devuelve dinero."
        onConfirm={jest.fn()}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Reversar' }));
    expect(screen.getByText(/No devuelve dinero/)).toBeInTheDocument();
  });
});

describe('the panel source itself', () => {
  type FS = { readFileSync(p: string, e: 'utf8'): string };
  const fs = jest.requireActual('fs') as FS;
  const src = fs.readFileSync('app/admin/service/orders/[id]/page.tsx', 'utf8');
  const panel = src.slice(
    src.indexOf('function PaymentSection'),
    src.indexOf('function DeliverySection'),
  );

  it('does no arithmetic on money', () => {
    // Every figure is a string the server computed. Adding, subtracting or
    // parsing one here would be a second answer to the same question.
    for (const forbidden of ['parseFloat', 'Number(', 'toFixed', ' + amount', 'amount -']) {
      expect(panel).not.toContain(forbidden);
    }
  });

  it('gates on the capability and never on a role name', () => {
    expect(panel).toContain('CAP_PAYMENTS_MANAGE');
    for (const forbidden of ['role ===', 'isAdmin', 'isTechnician', 'isMaster']) {
      expect(panel).not.toContain(forbidden);
    }
  });

  it('mints the idempotency key outside render state', () => {
    expect(panel).toContain('useState<Map<string, string>>(() => new Map())');
  });

  it('sends no currency, clock or cashier', () => {
    for (const forbidden of ['currency:', 'received_at', 'received_by']) {
      expect(panel).not.toContain(`${forbidden} `);
    }
  });

  it('offers no way to edit or delete a payment', () => {
    for (const forbidden of ['editPayment', 'deletePayment', 'updatePayment']) {
      expect(panel).not.toContain(forbidden);
    }
  });
});
