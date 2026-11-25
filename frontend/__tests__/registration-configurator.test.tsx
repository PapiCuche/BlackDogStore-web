/**
 * SELECTOR-UX-01 · PR B — «Registrar artículo» elige la configuración con EL configurador (modo único).
 *
 * Con un modelo que tiene paleta, los `<select>` de atributos (todas las opciones de la empresa) se sustituyen por los
 * chips de la caja sobre la paleta real del modelo menos sus exclusiones: sólo lo que existe, una opción por paso, lo
 * que no existe deshabilitado con su motivo, y dice si esa configuración ya es una variante (de la línea del tipo:
 * nuevo o seminuevo) o si se creará. Sin paleta, siguen los `<select>`. «Registrar otro igual» conserva lo común y
 * vacía los identificadores, con una clave nueva.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ArticleRegistrationWizard } from '@/app/admin/components/ArticleRegistrationWizard';
import * as registration from '@/app/lib/registration';
import * as admin from '@/app/lib/admin';
import * as api from '@/app/admin/lib/internal-api';
import { BLACK, CHIP, COL, ESIM, G128, GREEN, JA, MODEL, selectorPalette } from './support/selector-catalog';

jest.mock('@/app/lib/registration', () => ({
  ...jest.requireActual('@/app/lib/registration'),
  submitRegistration: jest.fn(),
  bulkSetModelVisibility: jest.fn(),
}));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminCategories: jest.fn(),
  uploadProductImage: jest.fn(),
  bulkSetProductVisibility: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchAdminCatalogModels: jest.fn(),
  fetchCatalogAttributes: jest.fn(),
  fetchCosmeticGrades: jest.fn(),
  fetchSuppliers: jest.fn(),
  fetchModelPalette: jest.fn(),
}));

const mReg = registration as jest.Mocked<typeof registration>;
const mAdmin = admin as jest.Mocked<typeof admin>;
const mApi = api as jest.Mocked<typeof api>;
const GOOD_IMEI = '356938035640011';
const GOOD_IMEI_2 = '356938035640029';

function result(over: Partial<registration.RegistrationResult> = {}): registration.RegistrationResult {
  return {
    dry_run: false, replayed: false, kind: 'new_device',
    product: { id: 900, existing_id: null, created: false, name: 'Equipo Z 128 GB Negro eSIM J/A', slug: 'equipo-z', is_active: true,
      is_published_online: false, is_serialized: true, sale_mode: 'stock', condition_line: 'new' },
    model: null, units: [], movements: [7], supplier: null, offer: null,
    publication: { requested: false, published: false, problems: [], model_hidden: false },
    warnings: [], links: { product: '/admin/products/900', units: null, storefront: null },
    ...over,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mAdmin.fetchAdminCategories.mockResolvedValue([{ id: 6, name: 'Celulares', slug: 'celulares' }] as unknown as admin.AdminCategory[]);
  mApi.fetchAdminCatalogModels.mockResolvedValue([
    { id: MODEL, name: 'Equipo Z', slug: 'equipo-z', category_id: 6, category_name: 'Celulares', image_url: '',
      description: '', is_published_online: false, sort_order: 0, variant_count: 1, used_unit_count: 0 },
    { id: 777, name: 'Sin Paleta', slug: 'sin-paleta', category_id: 6, category_name: 'Celulares', image_url: '',
      description: '', is_published_online: false, sort_order: 1, variant_count: 0, used_unit_count: 0 },
  ]);
  mApi.fetchCatalogAttributes.mockResolvedValue([
    { id: 1, name: 'Capacidad', kind: 'text', sort_order: 0, options: [{ id: G128, label: '128 GB', swatch_hex: '', sort_order: 0 }] },
    { id: 2, name: 'Color', kind: 'color', sort_order: 1, options: [{ id: BLACK, label: 'Negro', swatch_hex: '#111111', sort_order: 0 }] },
  ]);
  mApi.fetchCosmeticGrades.mockResolvedValue([]);
  mApi.fetchSuppliers.mockResolvedValue({ count: 0, page: 1, page_size: 1, results: [] } as unknown as Awaited<ReturnType<typeof api.fetchSuppliers>>);
  mApi.fetchModelPalette.mockImplementation(async (id: number) => (id === MODEL ? selectorPalette() : selectorPalette({ has_palette: false, model_id: 777 })));
  mReg.submitRegistration.mockImplementation(async (body) => (body.dry_run ? result({ dry_run: true }) : result()));
});

async function startDevice(kind = /Equipo nuevo/) {
  render(<ArticleRegistrationWizard access={{ manage: true, adjust: true, costs: true }} companyId={7} branches={[{ id: 1, name: 'Centro' }]} />);
  await waitFor(() => expect(mAdmin.fetchAdminCategories).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('radio', { name: kind }));
  fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
  await screen.findByLabelText('Modelo');
}

async function chooseModel(id: number) {
  fireEvent.change(screen.getByLabelText('Modelo'), { target: { value: String(id) } });
  await waitFor(() => expect(mApi.fetchModelPalette).toHaveBeenCalledWith(id, 7));
}

const configurator = () => screen.getByRole('group', { name: 'Configuración del equipo' });
const radio = (group: string, name: RegExp) => within(within(configurator()).getByRole('radiogroup', { name: group })).getByRole('radio', { name });

describe('con un modelo que tiene paleta', () => {
  it('los chips de la caja sustituyen a los <select>: sólo la paleta, lo excluido deshabilitado', async () => {
    await startDevice();
    await chooseModel(MODEL);
    expect(await screen.findByRole('group', { name: 'Configuración del equipo' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Capacidad', { selector: 'select' })).toBeNull();
    expect(within(configurator()).getAllByRole('radiogroup').map((g) => g.getAttribute('aria-label'))).toEqual(['Capacidad', 'Color', 'Tipo de SIM']);
    expect(within(configurator()).queryByRole('radio', { name: /Rosa/ })).toBeNull();
    fireEvent.click(radio('Capacidad', /128 GB/));
    fireEvent.click(radio('Color', /Verde/));
    // Verde · Chip is a pair the model excludes: Chip is closed and eSIM, the only way out, is chosen by itself.
    expect(radio('Tipo de SIM', /Chip/)).toBeDisabled();
    expect(radio('Tipo de SIM', /eSIM/)).toBeChecked();
    expect(configurator()).toHaveTextContent('Configuración nueva: la variante se crea al registrar');
  });

  it('dice si la configuración ya es una variante, y manda sus opciones (también la región que no se pregunta)', async () => {
    await startDevice();
    await chooseModel(MODEL);
    await screen.findByRole('group', { name: 'Configuración del equipo' });
    fireEvent.click(radio('Capacidad', /128 GB/));
    fireEvent.click(radio('Color', /Negro/));
    fireEvent.click(radio('Tipo de SIM', /eSIM/));
    expect(configurator()).toHaveTextContent('Ya existe: Equipo Z 128 GB Negro eSIM J/A. Se le agregan los equipos.');
    fireEvent.change(screen.getByLabelText(/^Nombre/), { target: { value: 'Equipo Z' } });
    fireEvent.change(screen.getByLabelText(/^Precio de venta/), { target: { value: '3000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.change(await screen.findByLabelText(/^IMEI 1$/), { target: { value: GOOD_IMEI } });
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    await waitFor(() => expect(mReg.submitRegistration).toHaveBeenCalled());
    const body = mReg.submitRegistration.mock.calls[0][0] as { options: number[] };
    expect([...body.options].sort()).toEqual([G128, BLACK, ESIM, JA].sort());
  });

  it('un seminuevo mira las variantes de su línea (seminuevos), no las de nuevos', async () => {
    await startDevice(/Equipo seminuevo/);
    await chooseModel(MODEL);
    await screen.findByRole('group', { name: 'Configuración del equipo' });
    fireEvent.click(radio('Capacidad', /128 GB/));
    fireEvent.click(radio('Color', /Negro/));
    fireEvent.click(radio('Tipo de SIM', /eSIM/));
    expect(configurator()).toHaveTextContent('Configuración nueva');
  });

  it('cambiar de modelo olvida la configuración elegida', async () => {
    await startDevice();
    await chooseModel(MODEL);
    await screen.findByRole('group', { name: 'Configuración del equipo' });
    fireEvent.click(radio('Color', /Negro/));
    await chooseModel(777);
    // Without a palette: the selects of always, nothing chosen.
    expect(await screen.findByLabelText('Capacidad')).toHaveValue('');
    expect(screen.queryByRole('group', { name: 'Configuración del equipo' })).toBeNull();
  });
});

describe('«Registrar otro igual»', () => {
  it('conserva lo común, vacía los IMEI y usa otra clave', async () => {
    await startDevice();
    await chooseModel(MODEL);
    await screen.findByRole('group', { name: 'Configuración del equipo' });
    fireEvent.click(radio('Capacidad', /128 GB/));
    fireEvent.click(radio('Color', /Negro/));
    fireEvent.click(radio('Tipo de SIM', /eSIM/));
    fireEvent.change(screen.getByLabelText(/^Nombre/), { target: { value: 'Equipo Z' } });
    fireEvent.change(screen.getByLabelText(/^Precio de venta/), { target: { value: '3000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.change(await screen.findByLabelText(/^IMEI 1$/), { target: { value: GOOD_IMEI } });
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Registrar artículo' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Registrar otro igual' }));
    // Back at the devices: the same configuration, an empty IMEI.
    const imei = await screen.findByLabelText(/^IMEI 1$/);
    expect(imei).toHaveValue('');
    fireEvent.change(imei, { target: { value: GOOD_IMEI_2 } });
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Registrar artículo' }));
    await screen.findByRole('button', { name: 'Registrar otro igual' });
    const real = mReg.submitRegistration.mock.calls.filter(([body]) => !body.dry_run);
    expect(real).toHaveLength(2);
    expect(real[1][0]).toEqual(expect.objectContaining({ options: real[0][0].options }));
    expect(JSON.stringify(real[1][0])).toContain(GOOD_IMEI_2);
    expect(JSON.stringify(real[1][0])).not.toContain(GOOD_IMEI);
    expect(real[1][1]?.key).toBeTruthy();
    expect(real[1][1]?.key).not.toBe(real[0][1]?.key);
  });
});

void COL; void CHIP; void GREEN;
