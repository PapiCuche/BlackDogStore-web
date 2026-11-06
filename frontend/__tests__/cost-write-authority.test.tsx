/**
 * COST-WRITE-AUTHORITY (decisión C del propietario) — en pantalla.
 *
 * El servidor rechaza un costo de quien no tiene «Ver costos y márgenes»
 * (400 `costs_forbidden`; en un Excel, la fila queda con error). Aquí se fija lo
 * que ve esa persona: «Registrar equipo» no le pide un costo ni lo envía, y si el
 * servidor rechaza uno lo dice arriba (no hay campo junto al cual decirlo); las
 * tres cargas de Excel le avisan, antes de adjuntar nada, que la columna de costo
 * va vacía. Quien sí tiene el permiso lo ve todo como siempre.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { StockUnitForm } from '@/app/admin/components/StockUnitForm';
import { StockUnitImport } from '@/app/admin/components/StockUnitImport';
import StockImportPage from '@/app/admin/inventory/import/page';
import ProductImportPage from '@/app/admin/products/import/page';
import { forgetBranch } from '@/app/admin/lib/last-branch';
import * as api from '@/app/admin/lib/internal-api';
import { fetchWithAuth } from '@/app/lib/auth';

jest.mock('next/navigation', () => ({ usePathname: () => '/admin', useRouter: () => ({ push: jest.fn() }) }));
jest.mock('@/app/lib/auth', () => ({ ...jest.requireActual('@/app/lib/auth'), fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ ...jest.requireActual('@/app/lib/admin'), fetchAdminCategories: jest.fn(async () => []) }));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchImportHistory: jest.fn(async () => []),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
jest.mock('@/app/admin/components/InventoryExportPanel', () => ({ InventoryExports: () => null }));
let ctx: Record<string, unknown>;
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (value: unknown) => React.ReactNode }) => <>{children(ctx)}</>,
}));

const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const mocked = api as jest.Mocked<typeof api>;
const IMEI_A = '356938035643809';
const REFUSAL = 'Necesitas el permiso «Ver costos y márgenes» para indicar un costo. Registra los equipos sin costo: quedará pendiente de completar.';
const IN_FILE = 'Este archivo trae costos y tu usuario no puede registrarlos: quita la columna de costo (o déjala vacía) o pide a un administrador el permiso «Ver costos y márgenes».';

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function member(capabilities: string[]) {
  ctx = {
    user: { id: 3, username: 'ana', role: 'customer' },
    dashboard: { company: { id: 7, name: 'Tienda A' }, access: { capabilities, is_platform_admin: false } },
    selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn(),
  };
}

const PHONE = {
  id: 10, name: 'iPhone 16', slug: 'p-10', description: '', price: '4500.00', inventory: 0, image_url: '',
  category_id: null, category_name: null, is_active: true, is_published_online: true, is_serialized: true,
  requires_imei: true, can_change_tracking: false, primary_code: '', created_at: '', updated_at: '',
};

let posted: Record<string, unknown>[];
let receiveReply: { status: number; body: unknown };

beforeEach(() => {
  forgetBranch();
  posted = [];
  receiveReply = { status: 201, body: { results: [{ id: 501 }] } };
  mocked.fetchImportHistory.mockClear();
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const url = String(input);
    if (method === 'GET' && url.includes('/admin/products/')) {
      return /\/admin\/products\/\d+\/$/.test(new URL(url, 'http://x').pathname)
        ? reply(200, PHONE) : reply(200, { count: 1, page: 1, page_size: 8, results: [PHONE] });
    }
    if (method === 'GET') return reply(200, { results: [] });
    if (typeof init.body === 'string') posted.push(JSON.parse(init.body));
    return reply(receiveReply.status, receiveReply.body);
  });
});

function mountForm(canSeeCost?: boolean) {
  const onSaved = jest.fn();
  render(
    <StockUnitForm
      branch={1} branches={[{ id: 1, name: 'Centro' }]} canChangeTracking initialProductId={10}
      conditions={[{ value: 'new', label: 'Nuevo' }]} onSaved={onSaved} onClose={jest.fn()}
      {...(canSeeCost === undefined ? {} : { canSeeCost })}
    />,
  );
  return onSaved;
}

async function fillOneDevice() {
  await screen.findByRole('button', { name: 'Cambiar producto' });
  fireEvent.change(screen.getByLabelText(/^Número de serie/), { target: { value: 'SERIE0001' } });
  fireEvent.change(screen.getByLabelText(/^IMEI \*/), { target: { value: IMEI_A } });
  fireEvent.change(screen.getByLabelText(/^Motivo o documento de ingreso/), { target: { value: 'Factura F001-1' } });
}

describe('«Registrar equipo» sin «Ver costos y márgenes»', () => {
  test('no pide un costo y registra el equipo sin enviar ninguno', async () => {
    const onSaved = mountForm();
    await fillOneDevice();
    expect(screen.queryByLabelText(/^Costo/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const [body] = posted;
    expect(body.units).toEqual([{ serial_number: 'SERIE0001', imei: IMEI_A, imei2: '', condition: 'new' }]);
    expect(JSON.stringify(body)).not.toContain('cost');
  });

  test('si el servidor rechaza un costo, lo dice arriba y conserva lo escrito', async () => {
    receiveReply = { status: 400, body: { detail: REFUSAL, code: 'costs_forbidden', field: 'cost' } };
    const onSaved = mountForm(false);
    await fillOneDevice();
    fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));
    expect(await screen.findByText(REFUSAL)).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
    expect((screen.getByLabelText(/^Número de serie/) as HTMLInputElement).value).toBe('SERIE0001');
  });

  test('quien tiene el permiso ve el campo y envía el costo como siempre', async () => {
    const onSaved = mountForm(true);
    await fillOneDevice();
    fireEvent.change(screen.getByLabelText(/^Costo/), { target: { value: '3800' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect((posted[0].units as Record<string, unknown>[])[0].cost).toBe('3800');
  });
});

describe('Cargas de Excel', () => {
  const NOTE = { name: 'Costos en el archivo' };

  test('la carga de equipos avisa antes de adjuntar nada, y enseña la fila rechazada sin ofrecer registrar', async () => {
    send.mockImplementation(async () => reply(201, {
      id: 31, import_type: 'units', status: 'previewed', original_filename: 'equipos.xlsx',
      counts: { total: 1, create: 0, update: 0, no_change: 0, skip: 0, error: 1 },
      summary: { units: 0 }, is_applicable: false, rows_truncated: false,
      rows: [{ sheet: 'Equipos', row: 2, action: 'error', match_key: 'SERIE0001', errors: [IN_FILE], warnings: [],
        data: { name: 'iPhone 16', branch: 'Centro', serial_number: 'SERIE0001', imei: IMEI_A, imei2: '', condition: 'new' } }],
    }));
    render(<StockUnitImport branches={[{ id: 1, name: 'Centro' }]} />);
    expect(screen.getByRole('note', NOTE)).toHaveTextContent('deja vacía (o quita) la columna «Costo»');

    fireEvent.change(screen.getByLabelText('Archivo de equipos (.xlsx)'), {
      target: { files: [new File(['x'], 'equipos.xlsx')] },
    });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
    expect(await screen.findByText(IN_FILE)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Registrar/ })).toBeNull();
  });

  test('con el permiso, la carga de equipos no avisa', () => {
    render(<StockUnitImport branches={[{ id: 1, name: 'Centro' }]} canSeeCost />);
    expect(screen.queryByRole('note', NOTE)).toBeNull();
  });

  test('la carga de stock avisa de «Costo unitario» sólo a quien no tiene el permiso', async () => {
    member(['inventory.view', 'inventory.adjust']);
    const first = render(<StockImportPage />);
    await act(async () => {});
    expect(screen.getByRole('note', NOTE)).toHaveTextContent('«Costo unitario»');
    first.unmount();

    member(['inventory.view', 'inventory.adjust', 'costs.view']);
    render(<StockImportPage />);
    await act(async () => {});
    expect(screen.queryByRole('note', NOTE)).toBeNull();
  });

  test('la carga de productos avisa de «Costo de referencia» sólo a quien no tiene el permiso', async () => {
    member(['products.view', 'products.manage']);
    const first = render(<ProductImportPage />);
    await act(async () => {});
    expect(screen.getByRole('note', NOTE)).toHaveTextContent('«Costo de referencia»');
    first.unmount();

    member(['products.view', 'products.manage', 'costs.view']);
    render(<ProductImportPage />);
    await act(async () => {});
    expect(screen.queryByRole('note', NOTE)).toBeNull();
  });
});
