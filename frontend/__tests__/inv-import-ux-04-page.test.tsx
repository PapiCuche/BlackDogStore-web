/**
 * INV-IMPORT-UX-04 · la pantalla de carga de productos, de la vista previa con errores al archivo corregido.
 * Revisión independiente (P2-3/P2-4): «Subir el archivo corregido» conserva el modo y las imágenes, vuelve al paso
 * del archivo con un aviso veraz, el historial se relee tras la vista previa, y «Empezar de nuevo» borra el aviso.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';

import ProductImportPage from '@/app/admin/products/import/page';
import * as api from '@/app/admin/lib/internal-api';
import type { ImportJob } from '@/app/admin/lib/internal-api';

jest.mock('next/navigation', () => ({ usePathname: () => '/admin/products/import', useRouter: () => ({ push: jest.fn() }) }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ ...jest.requireActual('@/app/lib/admin'), fetchAdminCategories: jest.fn(async () => []) }));
jest.mock('@/app/admin/components/AdminShell', () => ({ AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: unknown) => React.ReactNode }) => (
    <>{children({ user: { id: 1 }, dashboard: { company: { id: 7 } }, selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn() })}</>
  ),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchImportHistory: jest.fn(async () => []),
  inspectImportFile: jest.fn(),
  previewProductImport: jest.fn(),
}));

const mocked = api as jest.Mocked<typeof api>;
const SHEET = {
  name: 'Productos', header_row: 1, headers: ['Nombre'], sample_rows: 3, detected: 'products', preset: '', mapping: { name: 0 },
  warehouse_columns: [], notes: [], signature: 's', profile: null, help: false,
};
const JOB = {
  id: 12, import_type: 'products', status: 'previewed', stock_mode: '', original_filename: 'catalogo.xlsx', file_sha256: 'x',
  mapping: {}, options: {}, summary: {}, created_by: 'ana', applied_by: null, created_at: '2026-11-04T10:00:00Z', applied_at: null,
  is_applicable: false, rows: [], rows_truncated: false, counts: { total: 667, create: 664, update: 0, no_change: 0, skip: 0, error: 3 },
} as unknown as ImportJob;

async function previewWithErrors() {
  await act(async () => { render(<ProductImportPage />); });
  await act(async () => {
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(['x'], 'catalogo.xlsx')] } });
  });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Continuar' })); });
  fireEvent.click(screen.getByRole('checkbox', { name: /Sólo crear|solo crear|crear nuevos/i }));
  await act(async () => {
    fireEvent.change(screen.getByLabelText(/Elegir imágenes/), { target: { files: [new File([new Uint8Array(10)], 'foto.png', { type: 'image/png' })] } });
  });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
}

beforeEach(() => {
  mocked.fetchImportHistory.mockClear();
  mocked.inspectImportFile.mockReset().mockResolvedValue({ sheets: [SHEET], fields: { name: { label: 'Nombre', required: true } } } as never);
  mocked.previewProductImport.mockReset().mockResolvedValue(JOB);
});

it('vista previa con errores → «Subir el archivo corregido» conserva el modo y las imágenes y avisa; volver a empezar lo borra', async () => {
  await previewWithErrors();
  expect(screen.getByRole('region', { name: 'Estado de la importación' })).toHaveTextContent('Vista previa con errores · no se importó nada');
  // La vista previa también es un trabajo: el historial se relee.
  expect(mocked.fetchImportHistory.mock.calls.length).toBeGreaterThanOrEqual(2);

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir el archivo corregido' })); });
  expect(screen.getByText('1 · Elige el archivo')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent(/Se conservan el modo, las categorías y las imágenes adjuntas/);

  // El mismo modo viaja con el archivo corregido.
  await act(async () => {
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(['y'], 'catalogo-corregido.xlsx')] } });
  });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Continuar' })); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
  const options = mocked.previewProductImport.mock.calls.map(([, , opts]) => (opts as { options: { mode: string } }).options.mode);
  expect(options).toEqual(['create_only', 'create_only']);
  const images = mocked.previewProductImport.mock.calls.map(([, , opts]) => ((opts as { images: File[] }).images ?? []).map((f) => f.name));
  expect(images).toEqual([['foto.png'], ['foto.png']]);

  // «Cambiar archivo» (reset, como «Empezar de nuevo») lo borra todo, aviso incluido.
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir el archivo corregido' })); });
  await act(async () => {
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(['z'], 'otro.xlsx')] } });
  });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Cambiar archivo' })); });
  expect(screen.queryByText(/Se conservan el modo/)).not.toBeInTheDocument();
});
