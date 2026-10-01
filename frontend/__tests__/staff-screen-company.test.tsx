import { render, screen, waitFor } from '@testing-library/react';

import { dashboard, user } from './support/fixtures';

/**
 * DE DÓNDE SALE LA EMPRESA EN LA PANTALLA DE PERSONAL.
 *
 * EL DEFECTO QUE ESTO IMPIDE. La pantalla leía `ctx.selectedCompanyId`, que es
 * el SELECTOR del master y vale `null` para quien pertenece a una sola empresa
 * —el caso normal—. Sin empresa no se pedía nada, el estado inicial nunca salía
 * de `null` y la pantalla giraba para siempre. Un smoke que sólo comprobaba que
 * la ruta renderizaba pasaba tan contento.
 *
 * Por eso estas pruebas afirman sobre CONTENIDO: los nombres de las personas,
 * sus áreas y sus roles. Y sobre lo que se pidió al servidor, que es donde se
 * ve si la empresa llegó o no.
 */

const mockFetchStaff = jest.fn();
const mockFetchInvitations = jest.fn();
const mockFetchAreas = jest.fn();
const mockFetchRoles = jest.fn();
const mockFetchBranches = jest.fn();

jest.mock('@/app/lib/staff', () => ({
  fetchStaff: (...a: unknown[]) => mockFetchStaff(...a),
  fetchInvitations: (...a: unknown[]) => mockFetchInvitations(...a),
  fetchAreas: (...a: unknown[]) => mockFetchAreas(...a),
  fetchRoles: (...a: unknown[]) => mockFetchRoles(...a),
  fetchBranches: (...a: unknown[]) => mockFetchBranches(...a),
  createInvitation: jest.fn(),
  resendInvitation: jest.fn(),
  revokeInvitation: jest.fn(),
  setStaffActive: jest.fn(),
}));

// El guard tiene sus propias pruebas. Aquí estorba: pide sesión y panel por
// red, y lo que se examina es la pantalla que va DENTRO.
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: unknown) => React.ReactNode }) =>
    children((globalThis as { __ctx?: unknown }).__ctx),
}));

jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const StaffPage = require('@/app/admin/staff/page').default;

function persona(overrides: Record<string, unknown> = {}) {
  return {
    id: 41,
    full_name: 'Ana Torres Pérez',
    first_name: 'Ana',
    last_name: 'Torres Pérez',
    email: 'ana@correo.test',
    is_active: true,
    is_self: false,
    areas: [{ id: 5, name: 'Servicio Técnico' }],
    roles: [{ id: 9, name: 'Técnico' }],
    branch_access_mode: 'all',
    branches: [],
    branch_scope_label: 'Todas las sucursales',
    ...overrides,
  };
}

function contexto(overrides: Record<string, unknown> = {}) {
  return {
    user: user(),
    dashboard: dashboard(['memberships.view', 'memberships.manage']),
    selectedCompanyId: null,
    reload: jest.fn(),
    selectCompany: jest.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockFetchStaff.mockResolvedValue([persona()]);
  mockFetchInvitations.mockResolvedValue([]);
  mockFetchAreas.mockResolvedValue([]);
  mockFetchRoles.mockResolvedValue([]);
  mockFetchBranches.mockResolvedValue([]);
});

describe('Personal · de dónde sale la empresa', () => {
  it('carga con una sola membresía y sin selector explícito del master', async () => {
    // EL CASO OBLIGATORIO: usuario normal, una empresa, `selectedCompanyId`
    // null. Es exactamente el estado en el que la pantalla se quedaba girando.
    (globalThis as { __ctx?: unknown }).__ctx = contexto({ selectedCompanyId: null });
    render(<StaffPage />);

    // Contenido real, no «la ruta renderizó».
    expect(await screen.findByText('Ana Torres Pérez')).toBeInTheDocument();
    expect(screen.getByText('ana@correo.test')).toBeInTheDocument();
    expect(screen.getByText('Servicio Técnico')).toBeInTheDocument();
    expect(screen.getByText('Técnico')).toBeInTheDocument();
    expect(screen.getByText('Todas las sucursales')).toBeInTheDocument();

    // Y nada de spinner perpetuo.
    expect(screen.queryByText(/Cargando personal/i)).not.toBeInTheDocument();

    // La empresa que viajó es la del panel, no el selector nulo.
    expect(mockFetchStaff).toHaveBeenCalledWith(7, expect.anything());
    expect(mockFetchInvitations).toHaveBeenCalledWith(7);
  });

  it('ignora el selector del master cuando el panel ya dice qué empresa es', async () => {
    /*
      El selector y el panel pueden discrepar un instante mientras el panel se
      recarga. Manda el panel: es la empresa que el SERVIDOR resolvió, y pedir
      personal de otra devolvería 404 y una pantalla en blanco.
    */
    (globalThis as { __ctx?: unknown }).__ctx = contexto({ selectedCompanyId: 999 });
    render(<StaffPage />);

    expect(await screen.findByText('Ana Torres Pérez')).toBeInTheDocument();
    expect(mockFetchStaff).toHaveBeenCalledWith(7, expect.anything());
    expect(mockFetchStaff).not.toHaveBeenCalledWith(999, expect.anything());
  });

  it('sin empresa lo dice, y no lo disfraza de cargando ni de lista vacía', async () => {
    // Pasa de verdad: un rol heredado entra al control interno sin empresa.
    (globalThis as { __ctx?: unknown }).__ctx = contexto({ dashboard: null });
    render(<StaffPage />);

    expect(
      await screen.findByText(/No hay ninguna empresa seleccionada/i),
    ).toBeInTheDocument();

    // Los tres estados son distintos y no se pisan.
    expect(screen.queryByText(/Cargando personal/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/No hay personal que coincida/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    // Y no se pide nada: sin empresa no hay nada que pedir.
    await waitFor(() => expect(mockFetchStaff).not.toHaveBeenCalled());

    // Tampoco se ofrece dar de alta a nadie: no hay dónde.
    expect(
      screen.queryByRole('button', { name: 'Añadir trabajador' }),
    ).not.toBeInTheDocument();
  });

  it('una empresa con la búsqueda sin resultados dice otra cosa distinta', async () => {
    mockFetchStaff.mockResolvedValue([]);
    (globalThis as { __ctx?: unknown }).__ctx = contexto();
    render(<StaffPage />);

    expect(
      await screen.findByText(/No hay personal que coincida/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/No hay ninguna empresa seleccionada/i),
    ).not.toBeInTheDocument();
  });

  it('la propia ficha no ofrece un botón que sólo puede fallar', async () => {
    mockFetchStaff.mockResolvedValue([
      persona({ id: 1, full_name: 'Yo Mismo', email: 'yo@correo.test', is_self: true }),
      persona({ id: 2, full_name: 'Otra Persona', email: 'otra@correo.test' }),
    ]);
    (globalThis as { __ctx?: unknown }).__ctx = contexto();
    render(<StaffPage />);

    expect(await screen.findByText('Yo Mismo')).toBeInTheDocument();
    // Uno solo: el de la otra persona.
    expect(screen.getAllByRole('button', { name: 'Desactivar acceso' })).toHaveLength(1);
    expect(screen.getByText(/Esta es tu cuenta/i)).toBeInTheDocument();
  });
});
