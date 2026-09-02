import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  Confirm, ErrorNote, Panel, Pill, dateTime,
} from '@/app/admin/service/components/ServiceUi';
import { ServiceApiError, makeIdempotencyKey } from '@/app/lib/service-console';

/**
 * The pieces every service screen is assembled from.
 *
 * These are the primitives a payment panel will reuse, so the baseline covers
 * them first: a destructive action that asks before it fires, an error card
 * that shows the SERVER's words, and a key generator that has to be different
 * every time somebody means something different.
 */

describe('Confirm', () => {
  it('does not fire until the person confirms', async () => {
    const onConfirm = jest.fn();
    render(
      <Confirm label="Registrar entrega" question="¿Seguro?" onConfirm={onConfirm} />,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Registrar entrega' }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByText('¿Seguro?')).toBeInTheDocument();
  });

  it('fires once the person confirms', async () => {
    const onConfirm = jest.fn();
    render(<Confirm label="Entregar" question="¿Seguro?" onConfirm={onConfirm} />);

    await userEvent.click(screen.getByRole('button', { name: 'Entregar' }));
    await userEvent.click(screen.getByRole('button', { name: 'Sí' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('fires NOTHING when the person backs out', async () => {
    // Worth pinning for an action that moves money or hands a device over: the
    // escape has to actually escape, and it has to leave the button ready to
    // be pressed again rather than stuck open.
    const onConfirm = jest.fn();
    render(<Confirm label="Entregar" question="¿Seguro?" onConfirm={onConfirm} />);

    await userEvent.click(screen.getByRole('button', { name: 'Entregar' }));
    await userEvent.click(screen.getByRole('button', { name: 'No' }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Entregar' })).toBeInTheDocument();
  });

  it('cannot be opened at all while busy', async () => {
    const onConfirm = jest.fn();
    render(
      <Confirm label="Entregar" question="¿Seguro?" onConfirm={onConfirm} disabled />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Entregar' }));
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe('ErrorNote', () => {
  it("shows the SERVER's message rather than a generic one", () => {
    render(
      <ErrorNote
        error={new ServiceApiError('Saldo pendiente: S/ 200.00', 409, 'payment_required')}
      />,
    );
    expect(screen.getByText(/Saldo pendiente/)).toBeInTheDocument();
  });

  it('renders nothing when there is nothing wrong', () => {
    const { container } = render(<ErrorNote error={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('ServiceApiError', () => {
  it('carries the machine-readable code, so a screen never parses Spanish', () => {
    const err = new ServiceApiError('lo que sea', 409, 'idempotency_conflict');
    expect(err.code).toBe('idempotency_conflict');
    expect(err.status).toBe(409);
  });

  it('flags a 403 as forbidden, which is what triggers a context reload', () => {
    expect(new ServiceApiError('no', 403).isForbidden).toBe(true);
    expect(new ServiceApiError('no', 400).isForbidden).toBe(false);
  });
});

describe('makeIdempotencyKey', () => {
  it('gives two different intentions two different keys', () => {
    expect(makeIdempotencyKey('order-1:200.00')).not.toEqual(
      makeIdempotencyKey('order-1:300.00'),
    );
  });

  it('never exceeds what the server column accepts', () => {
    expect(makeIdempotencyKey('x'.repeat(500)).length).toBeLessThanOrEqual(64);
  });
});

describe('Panel and Pill', () => {
  it('renders its title, subtitle and children', () => {
    render(
      <Panel title="Pago del servicio" subtitle="No registra cobro todavía">
        <p>contenido</p>
      </Panel>,
    );
    expect(screen.getByText('Pago del servicio')).toBeInTheDocument();
    expect(screen.getByText('No registra cobro todavía')).toBeInTheDocument();
    expect(screen.getByText('contenido')).toBeInTheDocument();
  });

  it('shows the label it was given and invents none', () => {
    render(<Pill label="Entregado" tone="good" />);
    expect(screen.getByText('Entregado')).toBeInTheDocument();
  });
});

describe('dateTime', () => {
  it('answers a dash for a missing value rather than "Invalid Date"', () => {
    expect(dateTime(null)).toBe('—');
  });
});
