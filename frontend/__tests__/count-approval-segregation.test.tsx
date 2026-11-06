/**
 * DEC-INV-06 — quien realiza un recuento no lo aprueba.
 *
 * La regla vive en el servidor (403 `count_self_approval`); esta pantalla la explica:
 *   · quien aprueba y participó en el recuento (`self_approval.counted_by_me`) no ve «Aprobar y ajustar»: ve por
 *     qué y quién puede aprobarlo;
 *   · con «Aprobar recuentos propios» (`may_approve_own`, opción para equipos pequeños) ve «Aprobar mi propio
 *     recuento», que pide un motivo y lo envía (`self_approval_reason`); sin motivo no se envía nada;
 *   · un recuento aprobado por quien lo contó lo dice, con el motivo;
 *   · un servidor que no envía `self_approval` deja la pantalla como estaba.
 */
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import CountDetailPage from '@/app/admin/inventory/counts/[id]/page';
import { MAX_SELF_APPROVAL_REASON, approveOrdinaryCount } from '@/app/admin/inventory/counts/_components/ordinary-approval';
import * as api from '@/app/admin/lib/counts-api';
import { fetchWithAuth } from '@/app/lib/auth';
import type { CountDetail, CountLine } from '@/app/admin/lib/counts-api';

jest.mock('next/navigation', () => ({ useParams: () => ({ id: '9' }) }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ fetchAdminProducts: jest.fn() }));
jest.mock('@/app/lib/inventory', () => ({
  ...jest.requireActual('@/app/lib/inventory'),
  cancelCount: jest.fn(),
}));
jest.mock('@/app/admin/lib/counts-api', () => ({
  ...jest.requireActual('@/app/admin/lib/counts-api'),
  fetchCountDetail: jest.fn(),
  scanCount: jest.fn(),
  setCountLines: jest.fn(),
  recountLines: jest.fn(),
  submitCountForReview: jest.fn(),
  searchCountProducts: jest.fn(),
  reopenCount: jest.fn(),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

let capabilities: string[] = [];
jest.mock('@/app/admin/components/AccessGuard', () => ({
  AccessGuard: ({ children }: { children: (a: unknown) => React.ReactNode }) => (
    <>
      {children({
        user: { username: 'ana', role: 'customer' },
        dashboard: null,
        hasCompanyContext: true,
        isPlatformAdmin: false,
        isLegacyBridge: false,
        can: (cap: string) => capabilities.includes(cap),
      })}
    </>
  ),
}));

const mocked = api as jest.Mocked<typeof api>;
const actual = jest.requireActual('@/app/admin/lib/counts-api') as typeof api;
const mockFetch = fetchWithAuth as jest.Mock;

function line(over: Partial<CountLine> = {}): CountLine {
  return {
    id: 101, product: 5, product_name: 'Cargador USB-C 20W', product_slug: 'c',
    theoretical_at_start: 10, physical_quantity: 7, theoretical_at_approval: null,
    difference: null, is_counted: true, note: '', updated_at: '2026-11-05T10:00:00Z',
    product_code: '7750001', product_codes: ['7750001'], current_theoretical: 10,
    expected_difference: -3, review_state: 'counted',
    ...over,
  };
}

function detail(over: Partial<CountDetail> = {}): CountDetail {
  return {
    id: 9, company: 7, branch: 11, branch_name: 'Sucursal Centro',
    status: 'review', status_label: 'En revisión', reason: '', kind: 'regular',
    items: [line()], counted_items: 1, created_by: 1, created_by_username: 'ana',
    created_at: '2026-11-05T09:00:00Z', approved_at: null, cancelled_at: null,
    updated_at: '2026-11-05T09:00:00Z', version: 'v-review-1', review_version: 'v-review-1', last_reopen: null,
    self_approval: { counted_by_me: false, may_approve_own: false },
    ...over,
  };
}

async function mountPage() {
  await act(async () => { render(<CountDetailPage />); });
  await screen.findByRole('heading', { name: 'Recuento #9' });
}

beforeEach(() => {
  jest.clearAllMocks();
  capabilities = ['inventory.view', 'inventory.adjust', 'inventory.counts.approve'];
});

describe('quien participó en el recuento', () => {
  it('no ve «Aprobar y ajustar» y la pantalla le dice por qué y quién puede aprobarlo', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({ self_approval: { counted_by_me: true, may_approve_own: false } }));
    await mountPage();
    expect(screen.queryByRole('button', { name: 'Aprobar y ajustar' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Aprobar mi propio recuento' })).not.toBeInTheDocument();
    const note = screen.getByRole('note', { name: 'No puedes aprobar este recuento' });
    expect(note).toHaveTextContent(/Participaste en este recuento/);
    expect(note).toHaveTextContent(/otra persona con el permiso «Aprobar recuentos»/);
    // Lo demás que le toca a quien aprueba sigue ahí: devolverlo a edición o anularlo.
    expect(screen.getByRole('button', { name: 'Volver a edición' })).toBeEnabled();
  });

  it('también en un recuento ordinario que se aprueba directo desde «en conteo»', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({
      status: 'counting', status_label: 'En conteo', review_version: null, was_submitted: false,
      self_approval: { counted_by_me: true, may_approve_own: false },
    }));
    await mountPage();
    expect(screen.queryByRole('button', { name: 'Aprobar y ajustar' })).not.toBeInTheDocument();
    expect(screen.getByRole('note', { name: 'No puedes aprobar este recuento' })).toBeInTheDocument();
  });

  it('quien no participó aprueba como siempre', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail());
    await mountPage();
    expect(screen.getByRole('button', { name: 'Aprobar y ajustar' })).toBeEnabled();
    expect(screen.queryByRole('note', { name: 'No puedes aprobar este recuento' })).not.toBeInTheDocument();
  });

  it('un servidor sin `self_approval` deja la pantalla como estaba', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({ self_approval: undefined }));
    await mountPage();
    expect(screen.getByRole('button', { name: 'Aprobar y ajustar' })).toBeEnabled();
  });

  it('un 403 count_self_approval se muestra con el texto del servidor', async () => {
    jest.spyOn(window, 'confirm').mockReturnValue(true);
    mocked.fetchCountDetail.mockResolvedValue(detail());
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 403,
      json: async () => ({ code: 'count_self_approval', detail: 'Participaste en este recuento: no puedes aprobarlo tú.' }),
    });
    await mountPage();
    await userEvent.click(screen.getByRole('button', { name: 'Aprobar y ajustar' }));
    expect(await screen.findByText(/no puedes aprobarlo tú/)).toBeInTheDocument();
    expect(mocked.fetchCountDetail).toHaveBeenCalledTimes(2);
  });
});

describe('con «Aprobar recuentos propios» (equipos pequeños)', () => {
  const own = { counted_by_me: true, may_approve_own: true };

  it('ve «Aprobar mi propio recuento», que pide el motivo; sin motivo no se envía nada', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({ self_approval: own }));
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: 9, movements: [] }) });
    const prompt = jest.spyOn(window, 'prompt').mockReturnValueOnce('  ').mockReturnValueOnce('Sólo yo en la tienda hoy');
    await mountPage();
    expect(screen.queryByRole('button', { name: 'Aprobar y ajustar' })).not.toBeInTheDocument();
    expect(screen.getByRole('note', { name: 'Aprobación de tu propio recuento' })).toHaveTextContent(/queda marcada en la auditoría/);

    await userEvent.click(screen.getByRole('button', { name: 'Aprobar mi propio recuento' }));
    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.getByText(/Indica el motivo para aprobar tu propio recuento/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Aprobar mi propio recuento' }));
    expect(prompt).toHaveBeenCalledTimes(2);
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({
      version: 'v-review-1', self_approval_reason: 'Sólo yo en la tienda hoy',
    });
  });

  it('el motivo tiene un límite de 250 caracteres: la pantalla lo anuncia y no recorta en silencio', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({ self_approval: own }));
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: 9, movements: [] }) });
    const long = 'x'.repeat(251);
    const exact = 'y'.repeat(MAX_SELF_APPROVAL_REASON);
    const prompt = jest.spyOn(window, 'prompt').mockReturnValueOnce(long).mockReturnValueOnce(exact);
    await mountPage();
    await userEvent.click(screen.getByRole('button', { name: 'Aprobar mi propio recuento' }));
    expect(MAX_SELF_APPROVAL_REASON).toBe(250);
    expect(prompt.mock.calls[0][0]).toMatch(/hasta 250 caracteres/);
    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.getByText(/El motivo admite hasta 250 caracteres \(tiene 251\)/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Aprobar mi propio recuento' }));
    expect(JSON.parse(mockFetch.mock.calls[0][1].body).self_approval_reason).toBe(exact);
  });

  it('cancelar el motivo no envía nada', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({ self_approval: own }));
    jest.spyOn(window, 'prompt').mockReturnValueOnce(null);
    await mountPage();
    await userEvent.click(screen.getByRole('button', { name: 'Aprobar mi propio recuento' }));
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('un recuento de otra persona se aprueba sin motivo, como siempre', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({ self_approval: { counted_by_me: false, may_approve_own: true } }));
    await mountPage();
    expect(screen.getByRole('button', { name: 'Aprobar y ajustar' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Aprobar mi propio recuento' })).not.toBeInTheDocument();
  });
});

describe('historia', () => {
  it('un recuento aprobado por quien lo contó lo dice, con el motivo', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({
      status: 'approved', status_label: 'Aprobado', approved_at: '2026-11-05T12:00:00Z',
      approved_by_username: 'jefa',
      self_approved: { at: '2026-11-05T12:00:00Z', by: 'jefa', reason: 'Sólo yo en la tienda hoy' },
    }));
    await mountPage();
    const note = screen.getByRole('note', { name: 'Aprobado por quien lo contó' });
    expect(note).toHaveTextContent('jefa');
    expect(note).toHaveTextContent('Sólo yo en la tienda hoy');
  });
});

describe('API', () => {
  it('el motivo viaja con la versión', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: 9, movements: [] }) });
    await approveOrdinaryCount(9, 'v-1', 'Solo hoy');
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ version: 'v-1', self_approval_reason: 'Solo hoy' });
  });

  it('sin motivo, la aprobación es la de siempre', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: 9, movements: [] }) });
    await approveOrdinaryCount(9, 'v-1');
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ version: 'v-1' });
  });

  it('los códigos nuevos se leen como tales', async () => {
    for (const [status, code] of [[403, 'count_self_approval'], [400, 'self_approval_reason_required']] as const) {
      const err = await actual.readCountError(
        { ok: false, status, json: async () => ({ code, detail: 'x' }) } as unknown as Response, 'f',
      );
      expect(err.code).toBe(code);
    }
  });
});
