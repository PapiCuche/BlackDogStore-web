/**
 * COUNT-REVIEW-FREEZE (decisión B del propietario) — un recuento enviado a revisión no se modifica.
 *
 * La regla vive en el servidor (409 `count_in_review`); esta pantalla la refleja:
 *   · en revisión el panel de conteo queda cerrado, con la explicación, para todos los tipos de recuento;
 *   · quien aprueba ve «Volver a edición» (pide un motivo) y «Aprobar y ajustar» (envía la versión que ve);
 *   · un 409 se muestra con el texto del servidor y el recuento se vuelve a leer;
 *   · tras volver a edición, quien cuenta ve quién lo devolvió y por qué.
 */
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import CountDetailPage from '@/app/admin/inventory/counts/[id]/page';
import { CountWorkspace } from '@/app/admin/inventory/counts/_components/CountWorkspace';
import { approveOrdinaryCount } from '@/app/admin/inventory/counts/_components/ordinary-approval';
import * as api from '@/app/admin/lib/counts-api';
import { fetchWithAuth } from '@/app/lib/auth';
import type { CountDetail, CountLine } from '@/app/admin/lib/counts-api';

jest.mock('next/navigation', () => ({ useParams: () => ({ id: '9' }) }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ fetchAdminProducts: jest.fn() }));
jest.mock('@/app/lib/inventory', () => ({
  ...jest.requireActual('@/app/lib/inventory'),
  approveCount: jest.fn(),
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
const { CountApiError } = actual;
const mockFetch = fetchWithAuth as jest.Mock;

function line(over: Partial<CountLine> = {}): CountLine {
  return {
    id: 101, product: 5, product_name: 'Cargador USB-C 20W', product_slug: 'c',
    theoretical_at_start: 10, physical_quantity: 10, theoretical_at_approval: null,
    difference: null, is_counted: true, note: '', updated_at: '2026-11-05T10:00:00Z',
    product_code: '7750001', product_codes: ['7750001'], current_theoretical: 10,
    expected_difference: 0, review_state: 'counted',
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
    ...over,
  };
}

function Harness({ initial }: { initial: CountDetail }) {
  const [c, setC] = useState(initial);
  return <CountWorkspace count={c} onCountChange={setC} canCount canSeeCosts={false} />;
}

const scanField = () => screen.getByLabelText(/Código de barras o búsqueda|Buscar producto/);

async function mountPage() {
  await act(async () => { render(<CountDetailPage />); });
  await screen.findByRole('heading', { name: 'Recuento #9' });
}

beforeEach(() => {
  jest.clearAllMocks();
  capabilities = ['inventory.view', 'inventory.adjust', 'inventory.counts.approve'];
});

describe('panel de conteo en revisión', () => {
  it('un recuento ORDINARIO en revisión también queda cerrado y lo explica', () => {
    render(<Harness initial={detail()} />);
    expect(scanField()).toBeDisabled();
    expect(screen.queryByRole('button', { name: /Recontar Cargador/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Corregir/ })).not.toBeInTheDocument();
    expect(screen.getAllByText('Pendiente de aprobación').length).toBeGreaterThan(0);
    expect(screen.getByText(/nadie puede cambiar cantidades/)).toBeInTheDocument();
    expect(screen.getByText(/quien aprueba lo devuelve a edición/)).toBeInTheDocument();
  });

  it('un 409 count_in_review al guardar se muestra y relee el recuento, que queda cerrado', async () => {
    mocked.setCountLines.mockRejectedValueOnce(new CountApiError(
      'Este recuento está en revisión (pendiente de aprobación): ya no admite cambios.', 409, 'count_in_review',
    ));
    mocked.fetchCountDetail.mockResolvedValueOnce(detail());
    render(<Harness initial={detail({ status: 'counting', status_label: 'En conteo', review_version: null })} />);
    await userEvent.click(within(screen.getByRole('row', { name: /Cargador/ })).getByRole('button', { name: /^Corregir/ }));
    await userEvent.type(screen.getByLabelText('Cantidad contada'), '2');
    await userEvent.click(screen.getByRole('button', { name: 'Guardar total' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/está en revisión/);
    expect(mocked.fetchCountDetail).toHaveBeenCalledWith(9);
    expect(scanField()).toBeDisabled();
  });

  it('tras volver a edición, quien cuenta ve quién lo devolvió y por qué', () => {
    render(<Harness initial={detail({
      status: 'counting', status_label: 'En conteo', review_version: null,
      last_reopen: { at: '2026-11-05T11:00:00Z', by: 'jefa', reason: 'Faltó la vitrina 2' },
    })} />);
    expect(scanField()).toBeEnabled();
    const note = screen.getByRole('note', { name: /Devuelto a edición/ });
    expect(note).toHaveTextContent('jefa');
    expect(note).toHaveTextContent('Faltó la vitrina 2');
  });
});

describe('página del recuento en revisión', () => {
  it('quien aprueba ve «Volver a edición» y «Aprobar y ajustar»; quien cuenta, ninguno ni «Anular»', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail());
    await mountPage();
    expect(screen.getByRole('button', { name: 'Volver a edición' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Aprobar y ajustar' })).toBeEnabled();
  });

  it('quien sólo cuenta no puede aprobar, devolver ni anular un recuento en revisión', async () => {
    capabilities = ['inventory.view', 'inventory.adjust'];
    mocked.fetchCountDetail.mockResolvedValue(detail());
    await mountPage();
    expect(screen.queryByRole('button', { name: 'Volver a edición' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Aprobar y ajustar' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Anular recuento' })).not.toBeInTheDocument();
  });

  it('«Volver a edición» pide el motivo y lo envía; sin motivo no se envía nada', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail());
    mocked.reopenCount.mockResolvedValueOnce(detail({ status: 'counting', status_label: 'En conteo' }));
    const prompt = jest.spyOn(window, 'prompt').mockReturnValueOnce('   ').mockReturnValueOnce('Faltó la vitrina 2');
    await mountPage();
    await userEvent.click(screen.getByRole('button', { name: 'Volver a edición' }));
    expect(mocked.reopenCount).not.toHaveBeenCalled();
    expect(screen.getByText(/Indica el motivo/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Volver a edición' }));
    expect(prompt).toHaveBeenCalledTimes(2);
    expect(mocked.reopenCount).toHaveBeenCalledWith(9, 'Faltó la vitrina 2');
  });

  it('un recuento de migración en revisión también puede volver a edición (su aprobación es otra pantalla)', async () => {
    mocked.fetchCountDetail.mockResolvedValue(detail({ kind: 'migration' }));
    await mountPage();
    expect(screen.getByRole('button', { name: 'Volver a edición' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Aprobar y ajustar' })).not.toBeInTheDocument();
  });
});

describe('aprobar lo que se ve', () => {
  it('la aprobación envía la versión que muestra la pantalla', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: 9, movements: [] }) });
    await approveOrdinaryCount(9, 'v-review-1');
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/inventory\/counts\/9\/approve\/$/);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ version: 'v-review-1' });
  });

  it('un 409 count_stale se muestra con el texto del servidor y el recuento se relee', async () => {
    jest.spyOn(window, 'confirm').mockReturnValue(true);
    mocked.fetchCountDetail.mockResolvedValue(detail());
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 409,
      json: async () => ({ code: 'count_stale', detail: 'El recuento cambió desde que lo abriste: no se aprobó nada.' }),
    });
    await mountPage();
    await userEvent.click(screen.getByRole('button', { name: 'Aprobar y ajustar' }));
    expect(await screen.findByText(/cambió desde que lo abriste/)).toBeInTheDocument();
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ version: 'v-review-1' });
    expect(mocked.fetchCountDetail).toHaveBeenCalledTimes(2);
  });

  it('los códigos nuevos se leen como tales', async () => {
    for (const code of ['count_in_review', 'count_stale', 'count_changed'] as const) {
      const err = await actual.readCountError(
        { ok: false, status: 409, json: async () => ({ code, detail: 'x' }) } as unknown as Response, 'f',
      );
      expect(err.code).toBe(code);
    }
  });

  it('volver a edición viaja con su motivo', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => detail({ status: 'counting' }) });
    await actual.reopenCount(9, 'Faltó la vitrina 2');
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/inventory\/counts\/9\/reopen\/$/);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ reason: 'Faltó la vitrina 2' });
  });
});
