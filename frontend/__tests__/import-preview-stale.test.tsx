/**
 * IMPORT-STALE-01 · una vista previa caducada o desfasada no se aplica: la pantalla lo dice en palabras y ofrece
 * «Subir el archivo otra vez». El servidor responde 409 con `code` (`preview_expired` / `preview_stale`).
 */
import { act, fireEvent, render, screen } from '@testing-library/react';

import StockImportPage from '@/app/admin/inventory/import/page';
import ProductImportPage from '@/app/admin/products/import/page';
import * as auth from '@/app/lib/auth';
import * as api from '@/app/admin/lib/internal-api';
import { ImportApplyError, type ImportJob } from '@/app/admin/lib/internal-api';

jest.mock('next/navigation', () => ({ usePathname: () => '/admin/inventory/import', useRouter: () => ({ push: jest.fn() }) }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ ...jest.requireActual('@/app/lib/admin'), fetchAdminCategories: jest.fn(async () => []) }));
jest.mock('@/app/admin/components/AdminShell', () => ({ AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
jest.mock('@/app/admin/components/InventoryExportPanel', () => ({ InventoryExports: () => null }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: unknown) => React.ReactNode }) => (
    <>{children({
      user: { id: 1, username: 'ana', role: 'customer' },
      dashboard: { company: { id: 7 }, access: { capabilities: ['inventory.adjust', 'products.manage'] } },
      selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn(),
    })}</>
  ),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchImportHistory: jest.fn(async () => []),
  inspectImportFile: jest.fn(),
  previewStockImport: jest.fn(),
  previewProductImport: jest.fn(),
  applyImport: jest.fn(),
}));

const mocked = api as jest.Mocked<typeof api>;
const fetchWithAuth = auth.fetchWithAuth as jest.Mock;

const EXPIRED = 'Esta vista previa caducó: tiene más de 60 minutos y el inventario o el catálogo pueden haber cambiado desde entonces. No se aplicó nada. Sube el archivo otra vez para revisarlo con los datos de ahora.';
const STALE = 'El stock cambió desde la vista previa en 1 producto(s): «Cable Lightning». No se aplicó nada. Sube el archivo otra vez para revisarlo con los datos de ahora.';

function job(importType: 'stock' | 'products'): ImportJob {
  return {
    id: 40, import_type: importType, status: 'previewed', stock_mode: importType === 'stock' ? 'reconcile_target' : '',
    original_filename: 'carga.xlsx', file_sha256: 'x', mapping: {}, options: {}, summary: {}, created_by: 'ana',
    applied_by: '', created_at: '2026-11-05T10:00:00Z', applied_at: null, is_applicable: true, rows: [],
    rows_truncated: false, counts: { total: 1, create: importType === 'products' ? 1 : 0, update: importType === 'stock' ? 1 : 0, no_change: 0, skip: 0, error: 0 },
  } as unknown as ImportJob;
}

const STOCK_SHEET = {
  name: 'Worksheet', header_row: 1, headers: ['ID', 'CODIGO', 'CODIGO EAN', 'NOMBRE', 'ALMACEN 1'], sample_rows: 1,
  detected: 'stock', preset: '', mapping: { code: 1 }, warehouse_columns: [{ index: 4, header: 'ALMACEN 1' }],
  notes: [], signature: 's', profile: null, help: false,
};
const PRODUCT_SHEET = {
  name: 'Productos', header_row: 1, headers: ['Nombre'], sample_rows: 1, detected: 'products', preset: '',
  mapping: { name: 0 }, warehouse_columns: [], notes: [], signature: 's', profile: null, help: false,
};

async function stockUntilConfirmation() {
  await act(async () => { render(<StockImportPage />); });
  await act(async () => {
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(['x'], 'inv.xlsx')] } });
  });
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
  fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
}

beforeEach(() => {
  mocked.inspectImportFile.mockReset().mockImplementation(async (_company, _file, kind) => (kind === 'stock'
    ? { sheets: [STOCK_SHEET], branches: [{ id: 3, name: 'Principal' }] }
    : { sheets: [PRODUCT_SHEET], fields: { name: { label: 'Nombre', required: true } } }) as never);
  mocked.previewStockImport.mockReset().mockResolvedValue(job('stock'));
  mocked.previewProductImport.mockReset().mockResolvedValue(job('products'));
  mocked.applyImport.mockReset();
});

describe('carga de stock', () => {
  it('una vista previa caducada no se aplica: lo dice y «Subir el archivo otra vez» vuelve al archivo', async () => {
    mocked.applyImport.mockRejectedValue(new ImportApplyError(EXPIRED, 'preview_expired'));
    await stockUntilConfirmation();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Generar movimientos/ })); });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('La vista previa caducó');
    expect(alert).toHaveTextContent(EXPIRED);
    expect(screen.getByRole('button', { name: /Generar movimientos/ })).toBeDisabled();

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir el archivo otra vez' })); });
    expect(screen.getByText('1 · Elige el archivo')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('si el stock cambió desde la vista previa, lo dice y no muestra el error genérico', async () => {
    mocked.applyImport.mockRejectedValue(new ImportApplyError(STALE, 'preview_stale'));
    await stockUntilConfirmation();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Generar movimientos/ })); });

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('El inventario cambió desde la vista previa');
    expect(alert).toHaveTextContent('«Cable Lightning»');
    expect(screen.getByRole('button', { name: 'Subir el archivo otra vez' })).toBeInTheDocument();
  });

  it('la confirmación ya no promete recalcular: avisa de que un cambio de stock la anula', async () => {
    await stockUntilConfirmation();
    expect(screen.queryByText(/se vuelve a calcular en el momento de aplicar/)).not.toBeInTheDocument();
    expect(screen.getByText(/si el stock cambia mientras revisas esta pantalla/i)).toBeInTheDocument();
  });

  it('cualquier otro rechazo sigue siendo el mensaje del servidor, sin la acción de volver a subir', async () => {
    mocked.applyImport.mockRejectedValue(new Error('La sucursal Principal se desactivó.'));
    await stockUntilConfirmation();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Generar movimientos/ })); });
    expect(screen.getByText('La sucursal Principal se desactivó.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Subir el archivo otra vez' })).not.toBeInTheDocument();
  });
});

describe('carga de productos', () => {
  it('un catálogo editado desde la vista previa no se aplica y ofrece subir el archivo otra vez', async () => {
    mocked.applyImport.mockRejectedValue(new ImportApplyError('El catálogo cambió desde la vista previa en 1 producto(s): «Cable».', 'preview_stale'));
    await act(async () => { render(<ProductImportPage />); });
    await act(async () => {
      fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(['x'], 'catalogo.xlsx')] } });
    });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Continuar' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Aplicar a/ })); });

    expect(screen.getByRole('alert')).toHaveTextContent('El catálogo cambió desde la vista previa · no se aplicó nada');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir el archivo otra vez' })); });
    expect(screen.getByText('1 · Elige el archivo')).toBeInTheDocument();
  });
});

describe('applyImport', () => {
  it('lleva el código del 409 en el error', async () => {
    const real = jest.requireActual('@/app/admin/lib/internal-api') as typeof api;
    fetchWithAuth.mockResolvedValueOnce({
      ok: false, status: 409,
      json: async () => ({ detail: STALE, code: 'preview_stale', affected: 1, products: ['Cable Lightning'] }),
    });
    const caught = await real.applyImport(7, job('stock')).catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(ImportApplyError);
    expect((caught as ImportApplyError).code).toBe('preview_stale');
    expect((caught as ImportApplyError).message).toBe(STALE);
  });
});
