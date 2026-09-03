import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { NotificationBell, targetHref } from '@/app/admin/components/NotificationBell';

/**
 * The badge, and the two things it must never do — M12B.
 *
 * A badge is a number somebody trusts at a glance, so the interesting cases
 * are not "does it render 3". They are: what does it show before it knows, and
 * what does a notification's link actually grant.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
}));

const { fetchWithAuth } = jest.requireMock('@/app/lib/auth') as {
  fetchWithAuth: jest.Mock;
};

function reply(body: unknown, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  fetchWithAuth.mockReset();
});

describe('targetHref', () => {
  it('builds a route from structured fields, never from a stored URL', () => {
    expect(targetHref({ target_type: 'repair_order', target_id: 25 }))
      .toBe('/admin/service/orders/25');
  });

  it('returns null for a target it does not know how to reach', () => {
    // An unknown type must not become a guessed path. The row still renders;
    // it simply is not a link.
    expect(targetHref({ target_type: 'wallet_movement', target_id: 7 })).toBeNull();
    expect(targetHref({ target_type: 'repair_order', target_id: null })).toBeNull();
  });
});

describe('NotificationBell', () => {
  it('renders nothing at all without a company', () => {
    const { container } = render(<NotificationBell slug={null} />);
    expect(container).toBeEmptyDOMElement();
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it('shows no badge while the count is still unknown', async () => {
    // NOT a zero. "We have not asked yet" and "you have none" are different
    // facts, and showing 0 for the first states something the client does not
    // know.
    let resolve: (v: unknown) => void = () => {};
    fetchWithAuth.mockReturnValue(new Promise((r) => { resolve = r; }));

    render(<NotificationBell slug="taller" />);
    expect(screen.queryByText('0')).not.toBeInTheDocument();

    resolve({ ok: true, json: () => Promise.resolve({ unread: 0 }) });
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalled());
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('shows the unread count once it knows', async () => {
    fetchWithAuth.mockReturnValue(reply({ unread: 3 }));
    render(<NotificationBell slug="taller" />);
    expect(await screen.findByText('3')).toBeInTheDocument();
  });

  it('caps an implausible count instead of breaking the layout', async () => {
    fetchWithAuth.mockReturnValue(reply({ unread: 5000 }));
    render(<NotificationBell slug="taller" />);
    expect(await screen.findByText('99+')).toBeInTheDocument();
  });

  it('says so when there is nothing, rather than showing an empty box', async () => {
    fetchWithAuth.mockImplementation((url: string) =>
      url.includes('unread-count') ? reply({ unread: 0 }) : reply({ results: [] }));

    render(<NotificationBell slug="taller" />);
    await userEvent.click(screen.getByLabelText('Notificaciones'));
    expect(await screen.findByText('No tienes notificaciones.')).toBeInTheDocument();
  });

  it('lists the preview and asks the server for this company only', async () => {
    fetchWithAuth.mockImplementation((url: string) =>
      url.includes('unread-count')
        ? reply({ unread: 1 })
        : reply({
            results: [{
              id: 1, title: 'Nueva reparación asignada', body: 'Orden REP-1',
              priority: 'action', target_type: 'repair_order', target_id: 9,
              read_at: null, created_at: '2026-09-02T10:00:00Z',
            }],
          }));

    render(<NotificationBell slug="taller" />);
    await userEvent.click(screen.getByLabelText('Notificaciones'));

    expect(await screen.findByText('Nueva reparación asignada')).toBeInTheDocument();
    for (const call of fetchWithAuth.mock.calls) {
      expect(String(call[0])).toContain('/v1/internal/taller/notifications');
    }
  });

  it('survives a failed count without claiming zero', async () => {
    fetchWithAuth.mockRejectedValue(new Error('red caída'));
    render(<NotificationBell slug="taller" />);
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalled());
    expect(screen.queryByText('0')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Notificaciones')).toBeInTheDocument();
  });
});
