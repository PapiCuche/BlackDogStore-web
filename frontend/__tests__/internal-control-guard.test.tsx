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

/**
 * Copia fiel del error real, incluida la señal que trae del SERVIDOR.
 *
 * H4.1.2B: un 403 del panel no dice quién es legacy. Lo reciben igual el
 * operador pre-SaaS y alguien con la membresía revocada, así que el backend
 * afirma explícitamente cuál de los dos es. Sin esa afirmación: no hay puente.
 */
class MockNoInternalAccessError extends Error {
  readonly legacyBridge: boolean;

  constructor(message: string, legacyBridge = false) {
    super(message);
    this.legacyBridge = legacyBridge;
  }
}

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

  it('lets a LEGACY staff account in when the SERVER confirms the bridge', async () => {
    // The deliberate fallback: an operator whose company has not adopted
    // memberships still has a job to do. Removing this locks out everybody who
    // uses the panel today.
    //
    // H4.1.2B: la confirmación la da el backend en el cuerpo del 403. Antes
    // bastaba el 403 a secas, y eso es lo que hacía entrar a una cuenta
    // revocada.
    mockFetchDashboard.mockRejectedValue(new MockNoInternalAccessError('nope', true));
    mockIsStaffRole.mockReturnValue(true);
    renderGuard();
    await waitFor(() =>
      expect(screen.getByTestId('legacy')).toHaveTextContent('legacy'),
    );
  });

  it('refuses a REVOKED staff account, even with the same 403 and the same role', async () => {
    // EL DEFECTO DE H4.1.2B, fijado. Revocar la membresía deja a esta persona
    // recibiendo exactamente el mismo 403 que el operador legacy; su rol global
    // sigue diciendo «admin». Sin la afirmación del servidor, no entra.
    mockFetchDashboard.mockRejectedValue(new MockNoInternalAccessError('revocada', false));
    mockIsStaffRole.mockReturnValue(true);
    renderGuard();
    await waitFor(() =>
      expect(screen.getByText('Sin acceso interno')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('legacy')).not.toBeInTheDocument();
  });

  it('refuses a non-staff account with no company', async () => {
    mockFetchDashboard.mockRejectedValue(new MockNoInternalAccessError('nope', true));
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

/**
 * H4.1.1 — el técnico entra por su MEMBRESÍA, nunca por su rol legacy.
 *
 * `isStaffRole` es compatibilidad: refleja las tuplas de permisos legacy del
 * backend (store/permissions.py), y ninguna admite a un técnico. Añadirlo
 * abriría trece páginas legacy cuyo backend responde 403. Estas pruebas fijan
 * las dos mitades de esa decisión.
 */
describe('InternalControlGuard · el técnico (H4.1.1)', () => {
  const actual = jest.requireActual('@/app/lib/auth') as {
    isStaffRole: (u: unknown) => boolean;
  };

  it('isStaffRole NO incluye al técnico', () => {
    expect(actual.isStaffRole(user({ role: 'technician' }))).toBe(false);
  });

  it('un técnico SIN membresía no obtiene el panel por el fallback legacy', async () => {
    mockGetCurrentUser.mockResolvedValue(user({ role: 'technician' }));
    mockFetchDashboard.mockRejectedValue(new MockNoInternalAccessError('sin membresía'));
    mockIsStaffRole.mockImplementation((u: unknown) => actual.isStaffRole(u));
    renderGuard();
    await waitFor(() => expect(screen.getByText('Sin acceso interno')).toBeInTheDocument());
    expect(screen.queryByTestId('caps')).not.toBeInTheDocument();
  });

  it('un técnico CON membresía entra por el servidor, sin consultar su rol legacy', async () => {
    mockGetCurrentUser.mockResolvedValue(user({ role: 'technician' }));
    mockFetchDashboard.mockResolvedValue(
      dashboard(['service.orders.view', 'service.repair.manage']),
    );
    mockIsStaffRole.mockReset();
    mockIsStaffRole.mockReturnValue(false);
    renderGuard();
    await waitFor(() =>
      expect(screen.getByTestId('caps')).toHaveTextContent(
        'service.orders.view,service.repair.manage',
      ),
    );
    expect(mockIsStaffRole).not.toHaveBeenCalled();
  });
});
