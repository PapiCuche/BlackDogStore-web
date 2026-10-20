/**
 * SUPPLIER-DEVICE-FLOW-02 · B — los equipos del registro con un lector.
 *
 * En las filas de un equipo nuevo, Enter lleva serie → IMEI → segundo IMEI →
 * la fila siguiente; en la última abre una fila nueva (si la actual tiene
 * algo). Pegar una lista llena ese campo hacia abajo, agregando filas. El
 * seminuevo admite su segundo IMEI, que el servidor ya recibe.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { ArticleRegistrationWizard } from '@/app/admin/components/ArticleRegistrationWizard';
import * as registration from '@/app/lib/registration';
import * as admin from '@/app/lib/admin';
import * as api from '@/app/admin/lib/internal-api';

jest.mock('@/app/lib/registration', () => ({
  ...jest.requireActual('@/app/lib/registration'),
  submitRegistration: jest.fn(),
}));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminCategories: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchAdminCatalogModels: jest.fn(),
  fetchCatalogAttributes: jest.fn(),
  fetchCosmeticGrades: jest.fn(),
  fetchSuppliers: jest.fn(),
}));

const mReg = registration as jest.Mocked<typeof registration>;
const mAdmin = admin as jest.Mocked<typeof admin>;
const mApi = api as jest.Mocked<typeof api>;

const GOOD_IMEI = '356938035640011';
const GOOD_IMEI_2 = '356938035640029';

beforeEach(() => {
  jest.clearAllMocks();
  mAdmin.fetchAdminCategories.mockResolvedValue([{ id: 6, name: 'Celulares', slug: 'celulares' }] as unknown as admin.AdminCategory[]);
  mApi.fetchAdminCatalogModels.mockResolvedValue([
    { id: 9, name: 'iPhone 15', slug: 'iphone-15', category_id: 6, category_name: 'Celulares', image_url: '',
      description: '', is_published_online: true, sort_order: 0, variant_count: 1, used_unit_count: 0 },
  ]);
  mApi.fetchCatalogAttributes.mockResolvedValue([]);
  mApi.fetchCosmeticGrades.mockResolvedValue([]);
  mApi.fetchSuppliers.mockResolvedValue({ count: 0, page: 1, page_size: 1, results: [] } as unknown as Awaited<ReturnType<typeof api.fetchSuppliers>>);
  mReg.submitRegistration.mockRejectedValue(new Error('sin servidor en esta prueba'));
});

const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const enter = (label: string) => fireEvent.keyDown(screen.getByLabelText(label), { key: 'Enter' });

async function toStock(kind: RegExp) {
  render(<ArticleRegistrationWizard access={{ manage: true, adjust: true, costs: false }} companyId={7}
    branches={[{ id: 1, name: 'Centro' }]} />);
  await waitFor(() => expect(mAdmin.fetchAdminCategories).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('radio', { name: kind }));
  fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
  await screen.findByLabelText(/^Nombre/);
  type(/^Nombre/, 'iPhone 15');
  fireEvent.change(screen.getByLabelText(/^Categoría/), { target: { value: '6' } });
  type(/^Precio de venta/, '3999');
  fireEvent.change(screen.getByLabelText(/^Modelo/), { target: { value: '9' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
}

describe('equipos nuevos con un lector', () => {
  it('Enter recorre la fila, pasa a la siguiente y en la última abre otra', async () => {
    await toStock(/Equipo nuevo/);
    await screen.findByLabelText('Número de serie 1');
    expect(screen.getByLabelText('IMEI 1')).toHaveAttribute('autocomplete', 'off');
    type('Número de serie 1', 'SN1');
    enter('Número de serie 1');
    expect(screen.getByLabelText('IMEI 1')).toHaveFocus();
    enter('IMEI 1');
    expect(screen.getByLabelText('Segundo IMEI 1 (opcional)')).toHaveFocus();
    enter('Segundo IMEI 1 (opcional)');
    // The last row: a new one opens and takes the focus.
    await waitFor(() => expect(screen.getByLabelText('Número de serie 2')).toHaveFocus());
    // An empty last row does not open another.
    enter('Número de serie 2');
    enter('IMEI 2');
    enter('Segundo IMEI 2 (opcional)');
    expect(screen.queryByLabelText('Número de serie 3')).toBeNull();
    expect(mReg.submitRegistration).not.toHaveBeenCalled();
  });

  it('pegar una lista de IMEI llena las filas, agregando las que faltan', async () => {
    await toStock(/Equipo nuevo/);
    await screen.findByLabelText('Número de serie 1');
    fireEvent.paste(screen.getByLabelText('IMEI 1'), { clipboardData: { getData: () => `${GOOD_IMEI}\n${GOOD_IMEI_2}\n` } });
    expect(screen.getByLabelText('IMEI 1')).toHaveValue(GOOD_IMEI);
    expect(screen.getByLabelText('IMEI 2')).toHaveValue(GOOD_IMEI_2);
  });
});

describe('un seminuevo', () => {
  it('manda su segundo IMEI y lo valida', async () => {
    await toStock(/Equipo seminuevo/);
    await screen.findByLabelText('Número de serie');
    type('Número de serie', 'SNU');
    type('IMEI', GOOD_IMEI);
    type('Segundo IMEI (opcional)', '356938035640018');
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    expect(await screen.findByText(/Segundo IMEI: Ese IMEI no es válido/)).toBeInTheDocument();
    expect(mReg.submitRegistration).not.toHaveBeenCalled();
    type('Segundo IMEI (opcional)', GOOD_IMEI_2);
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    await waitFor(() => expect(mReg.submitRegistration).toHaveBeenCalled());
    expect(mReg.submitRegistration.mock.calls[0][0]).toMatchObject({
      kind: 'used_device', unit: { serial_number: 'SNU', imei: GOOD_IMEI, imei2: GOOD_IMEI_2 },
    });
  });
});
