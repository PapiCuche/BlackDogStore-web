import { render, screen, waitFor } from '@testing-library/react';

import { dashboard, user } from './support/fixtures';

/**
 * The gate every internal screen sits behind.
 *
 * WHAT THESE PIN. The guard decides four things, and each one is a decision
 * somebody could break without noticing: who gets in without a membership, who
 * is refused, what the children are handed, and whether a failure is a dead end
 * or something a person can retry.
 */

const mockFetchDashboard = jest.fn();
const mockGetCurrentUser = jest.fn();
const mockIsStaffRole = jest.fn();

class MockNoInternalAccessError extends Error {}

jest.mock('@/app/admin/lib/internal-api', () => ({
  fetchInternalDashboard: (...a: unknown[]) => mockFetchDashboard(...a),
  NoInternalAccessError: MockNoInternalAccessError,
}));

jest.mock('@/app/lib/auth', () => ({
  getCurrentUser: () => mockGetCurrentUser(),
  isStaffRole: (...a: unknown[]) => mockIsStaffRole(...a),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { InternalControlGuard } = require('@/app/admin/components/InternalControlGuard');

type Ctx = {
  dashboard: ReturnType<typeof dashboard> | null;
  selectedCompanyId: number | null;
  reload: () => void;
  selectCompany: (id: number | null) => void;
};

function Probe({ ctx }: { ctx: Ctx }) {
  return (
    <div>
      <span data-testid="caps">
        {(ctx.dashboard?.access.capabilities ?? []).join(',') || 'none'}
      </span>
      <span data-testid="legacy">{ctx.dashboard === null ? 'legacy' : 'company'}</span>
    </div>
  );
}

function renderGuard() {
  return render(
    <InternalControlGuard>{(ctx: Ctx) => <Probe ctx={ctx} />}</InternalControlGuard>,
  );
}

beforeEach(() => {
  mockGetCurrentUser.mockResolvedValue(user());
  mockIsStaffRole.mockReturnValue(true);
  mockFetchDashboard.mockResolvedValue(dashboard(['service.orders.view']));
});

describe('InternalControlGuard', () => {
  it('hands the children the capabilities the SERVER sent', async () => {
    mockFetchDashboard.mockResolvedValue(
      dashboard(['service.orders.view', 'service.delivery.manage']),
    );
    renderGuard();
    await waitFor(() =>
      expect(screen.getByTestId('caps')).toHaveTextContent(
        'service.orders.view,service.delivery.manage',
      ),
    );
  });

  it('refuses an anonymous caller instead of rendering the panel', async () => {
    mockGetCurrentUser.mockResolvedValue(null);
    renderGuard();
    await waitFor(() => expect(screen.getByText('Inicia sesión')).toBeInTheDocument());
    expect(screen.queryByTestId('caps')).not.toBeInTheDocument();
  });

  it('lets a LEGACY staff account in with no company, as documented', async () => {
    // The deliberate fallback: an operator whose company has not adopted
    // memberships still has a job to do. Removing this locks out everybody who
    // uses the panel today.
    mockFetchDashboard.mockRejectedValue(new MockNoInternalAccessError('nope'));
    mockIsStaffRole.mockReturnValue(true);
    renderGuard();
    await waitFor(() =>
      expect(screen.getByTestId('legacy')).toHaveTextContent('legacy'),
    );
  });

  it('refuses a non-staff account with no company', async () => {
    mockFetchDashboard.mockRejectedValue(new MockNoInternalAccessError('nope'));
    mockIsStaffRole.mockReturnValue(false);
    renderGuard();
    await waitFor(() =>
      expect(screen.getByText('Sin acceso interno')).toBeInTheDocument(),
    );
  });

  it('offers a retry rather than a dead end when the request fails', async () => {
    mockFetchDashboard.mockRejectedValue(new Error('502'));
    renderGuard();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Reintentar' })).toBeInTheDocument(),
    );
  });

  it('asks the server again when the context is reloaded', async () => {
    // The seam a 403 is supposed to pull: capabilities are the server's, and a
    // revocation has to be able to reach the screen. A guard that cached them
    // for the life of the page would keep drawing buttons that no longer work.
    const { rerender } = renderGuard();
    await waitFor(() => expect(mockFetchDashboard).toHaveBeenCalledTimes(1));

    let captured: Ctx | null = null;
    rerender(
      <InternalControlGuard>
        {(ctx: Ctx) => {
          captured = ctx;
          return <Probe ctx={ctx} />;
        }}
      </InternalControlGuard>,
    );
    await waitFor(() => expect(captured).not.toBeNull());
    captured!.reload();
    await waitFor(() => expect(mockFetchDashboard).toHaveBeenCalledTimes(2));
  });

  it('refetches for the company the caller selected, not for a remembered one', async () => {
    const { rerender } = renderGuard();
    await waitFor(() => expect(mockFetchDashboard).toHaveBeenCalledWith(null));

    let captured: Ctx | null = null;
    rerender(
      <InternalControlGuard>
        {(ctx: Ctx) => {
          captured = ctx;
          return <Probe ctx={ctx} />;
        }}
      </InternalControlGuard>,
    );
    await waitFor(() => expect(captured).not.toBeNull());
    captured!.selectCompany(8);
    await waitFor(() => expect(mockFetchDashboard).toHaveBeenLastCalledWith(8));
  });
});
