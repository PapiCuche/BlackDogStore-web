/**
 * COMMERCE-ADMIN-UX-01 — «Registrar artículo».
 *
 * El asistente no escribe nada por su cuenta: arma UN cuerpo y lo manda a
 * `POST /api/admin/products/register/`, que llama a los servicios de siempre en
 * una transacción. Aquí se fija lo que decide la pantalla:
 *
 *   · qué tipos y pasos ofrece, y por qué no cuando no (los permisos de las
 *     acciones de debajo);
 *   · que sin `costs.view` no se ve ni viaja ningún costo, ni el margen;
 *   · que «Revisar» pregunta al servidor sin escribir (`dry_run`, sin clave) y
 *     «Registrar» manda una clave que se reutiliza si se reintenta lo mismo;
 *   · que las fotos y la publicación que dependen de ellas van DESPUÉS, por los
 *     endpoints de siempre.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ArticleRegistrationWizard } from '@/app/admin/components/ArticleRegistrationWizard';
import * as registration from '@/app/lib/registration';
import * as admin from '@/app/lib/admin';
import * as api from '@/app/admin/lib/internal-api';
import * as stockUnits from '@/app/lib/stock-units';

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
}));
jest.mock('@/app/lib/stock-units', () => ({
  ...jest.requireActual('@/app/lib/stock-units'),
  uploadUnitPhoto: jest.fn(),
}));

const mReg = registration as jest.Mocked<typeof registration>;
const mAdmin = admin as jest.Mocked<typeof admin>;
const mApi = api as jest.Mocked<typeof api>;
const mUnits = stockUnits as jest.Mocked<typeof stockUnits>;

const ALL = { manage: true, adjust: true, costs: true };
const NO_COSTS = { manage: true, adjust: true, costs: false };
const CATALOGUE_ONLY = { manage: true, adjust: false, costs: false };
const BRANCHES = [{ id: 1, name: 'Centro' }, { id: 2, name: 'Norte' }];
// 3569380356400 1X / 2X with their Luhn check digits.
const GOOD_IMEI = '356938035640011';
const GOOD_IMEI_2 = '356938035640029';
const BAD_IMEI = '356938035640018';

function result(over: Partial<registration.RegistrationResult> = {}): registration.RegistrationResult {
  return {
    dry_run: false, replayed: false, kind: 'accessory',
    product: { id: 41, existing_id: null, created: true, name: 'Funda MagSafe', slug: 'funda-magsafe', is_active: true,
      is_published_online: true, is_serialized: false, sale_mode: 'stock', condition_line: 'new' },
    model: null, units: [], movements: [7, 8], supplier: null, offer: null,
    publication: { requested: true, published: true, problems: [], model_hidden: false },
    warnings: [],
    links: { product: '/admin/products/41', units: null, storefront: '/product/funda-magsafe' },
    ...over,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mAdmin.fetchAdminCategories.mockResolvedValue([
    { id: 5, name: 'Fundas', slug: 'fundas' }, { id: 6, name: 'Celulares', slug: 'celulares' },
  ] as unknown as admin.AdminCategory[]);
  mApi.fetchAdminCatalogModels.mockResolvedValue([
    { id: 9, name: 'iPhone 15', slug: 'iphone-15', category_id: 6, category_name: 'Celulares', image_url: '',
      description: '', is_published_online: true, sort_order: 0, variant_count: 1, used_unit_count: 0 },
  ]);
  mApi.fetchCatalogAttributes.mockResolvedValue([
    { id: 1, name: 'Capacidad', kind: 'text', sort_order: 0, options: [{ id: 11, label: '128 GB', swatch_hex: '', sort_order: 0 }] },
    { id: 2, name: 'Color', kind: 'color', sort_order: 1, options: [{ id: 21, label: 'Negro', swatch_hex: '#111111', sort_order: 0 }] },
  ]);
  mApi.fetchCosmeticGrades.mockResolvedValue([
    { id: 3, code: 'excelente', label: 'Excelente', description: '', sort_order: 0, is_active: true },
  ]);
  mApi.fetchSuppliers.mockResolvedValue({ count: 1, page: 1, page_size: 1, results: [
    { id: 4, name: 'Mayorista Lima', document_type: '', document_number: '', contact_name: '', phone: '', email: '',
      notes: '', is_active: true, offer_count: 0 },
  ] } as unknown as Awaited<ReturnType<typeof api.fetchSuppliers>>);
  mReg.submitRegistration.mockImplementation(async (body) => (
    body.dry_run ? result({ dry_run: true, product: { ...result().product, id: null } }) : result()
  ));
});

function wizard(access = ALL) {
  render(<ArticleRegistrationWizard access={access} companyId={7} branches={BRANCHES} />);
}

const kindButton = (name: RegExp) => screen.getByRole('radio', { name });

async function choose(kind: RegExp) {
  await waitFor(() => expect(mAdmin.fetchAdminCategories).toHaveBeenCalled());
  fireEvent.click(kindButton(kind));
  fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
}

function type(label: RegExp | string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function fillProduct({ name = 'Funda MagSafe', category = '5', price = '90' } = {}) {
  await screen.findByLabelText(/^Nombre/);
  type(/^Nombre/, name);
  fireEvent.change(screen.getByLabelText(/^Categoría/), { target: { value: category } });
  type(/^Precio de venta/, price);
}

const sent = (call = 0) => mReg.submitRegistration.mock.calls[call][0] as Record<string, unknown>;
const options = (call = 0) => mReg.submitRegistration.mock.calls[call][1] as { key?: string } | undefined;

describe('qué se puede registrar', () => {
  it('los cuatro tipos, como opciones', async () => {
    wizard();
    await waitFor(() => expect(mAdmin.fetchAdminCategories).toHaveBeenCalledWith(7));
    for (const name of [/Equipo nuevo/, /Equipo seminuevo/, /Accesorio o repuesto/, /Disponible por proveedor/]) {
      expect(kindButton(name)).toBeEnabled();
    }
  });

  it('sin «Ajustar inventario» los equipos se ven deshabilitados, con el motivo', async () => {
    wizard(CATALOGUE_ONLY);
    await waitFor(() => expect(mAdmin.fetchAdminCategories).toHaveBeenCalled());
    expect(kindButton(/Equipo nuevo/)).toBeDisabled();
    expect(kindButton(/Equipo seminuevo/)).toBeDisabled();
    expect(kindButton(/Accesorio o repuesto/)).toBeEnabled();
    expect(screen.getAllByText(registration.ADJUST_REASON).length).toBeGreaterThan(0);
  });

  it('sin «Gestionar productos» no se registra nada', async () => {
    wizard({ manage: false, adjust: true, costs: true });
    await waitFor(() => expect(mAdmin.fetchAdminCategories).toHaveBeenCalled());
    for (const name of [/Equipo nuevo/, /Accesorio o repuesto/, /Disponible por proveedor/]) {
      expect(kindButton(name)).toBeDisabled();
    }
    expect(screen.getAllByText(registration.MANAGE_REASON).length).toBeGreaterThan(0);
  });
});

describe('los costos', () => {
  it('con «Ver costos» se pide el costo y se ve el margen estimado', async () => {
    wizard(ALL);
    await choose(/Accesorio o repuesto/);
    await fillProduct({ price: '90' });
    type(/^Costo de referencia/, '60');
    expect(screen.getByText(/Margen estimado/).textContent).toContain('S/ 30.00');
    expect(screen.getByText(/Margen estimado/).textContent).toContain('33.3 %');
  });

  it('sin «Ver costos» no hay campo de costo, ni margen, ni costo en lo que se envía', async () => {
    wizard(NO_COSTS);
    await choose(/Accesorio o repuesto/);
    await fillProduct();
    expect(screen.queryByLabelText(/Costo de referencia/)).toBeNull();
    expect(screen.queryByText(/Margen estimado/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Agregar sucursal' }));
    expect(screen.queryByLabelText(/Costo unitario/)).toBeNull();
    fireEvent.change(screen.getByLabelText('Cantidad 1'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    await waitFor(() => expect(mReg.submitRegistration).toHaveBeenCalled());
    expect(JSON.stringify(sent())).not.toMatch(/cost/);
  });

  it('el cuerpo nunca lleva un costo sin el permiso, aunque el borrador lo tenga', () => {
    const draft = { ...registration.emptyDraft(), kind: 'accessory' as const, name: 'X', price: '1', reference_cost: '9',
      stock: [{ branch: '1', quantity: '2', unit_cost: '3' }] };
    expect(JSON.stringify(registration.buildRegistrationBody(draft, NO_COSTS))).not.toMatch(/cost/);
    expect(registration.buildRegistrationBody(draft, ALL)).toMatchObject({
      product: { reference_cost: '9' }, stock: [{ branch: 1, quantity: 2, unit_cost: '3' }],
    });
    expect(registration.estimatedMargin('90', '60', { costs: false })).toBeNull();
  });
});

describe('un accesorio, de punta a punta', () => {
  it('revisa sin escribir, registra con su clave y deja los enlaces', async () => {
    wizard(ALL);
    await choose(/Accesorio o repuesto/);
    await fillProduct();
    type(/Código \/ SKU/, 'FUNDA-01');
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Agregar sucursal' }));
    fireEvent.click(screen.getByRole('button', { name: 'Agregar sucursal' }));
    fireEvent.change(screen.getByLabelText('Sucursal 2'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('Cantidad 1'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Costo unitario 1'), { target: { value: '20' } });
    fireEvent.change(screen.getByLabelText('Cantidad 2'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));

    await screen.findByRole('heading', { name: 'Revisar y registrar' });
    expect(sent(0)).toMatchObject({
      kind: 'accessory', dry_run: true,
      product: { name: 'Funda MagSafe', category: 5, price: '90', sku: 'FUNDA-01', is_active: true, show_online: true },
      stock: [{ branch: 1, quantity: 5, unit_cost: '20' }, { branch: 2, quantity: 3 }],
    });
    expect(options(0)?.key).toBeUndefined();

    fireEvent.click(screen.getByRole('button', { name: 'Registrar artículo' }));
    await screen.findByRole('heading', { name: 'Artículo registrado' });
    expect(sent(1).dry_run).toBeUndefined();
    expect(options(1)?.key).toMatch(/^reg-/);
    expect(screen.getByRole('link', { name: 'Abrir el producto' })).toHaveAttribute('href', '/admin/products/41');
    expect(screen.getByRole('link', { name: /Ver en la tienda/ })).toHaveAttribute('href', '/product/funda-magsafe');
  });

  it('sin «Ajustar inventario» el paso de stock dice por qué y se registra sin stock', async () => {
    wizard(CATALOGUE_ONLY);
    await choose(/Accesorio o repuesto/);
    await fillProduct();
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    expect(await screen.findByText(/El producto se registra sin stock/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Agregar sucursal' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    await waitFor(() => expect(mReg.submitRegistration).toHaveBeenCalled());
    expect(sent().stock).toBeUndefined();
  });
});

describe('un equipo nuevo', () => {
  async function toStock() {
    wizard(ALL);
    await choose(/Equipo nuevo/);
    await fillProduct({ name: 'iPhone 15 128 GB Negro', category: '6', price: '3999' });
    fireEvent.change(screen.getByLabelText(/^Modelo/), { target: { value: '9' } });
    fireEvent.change(screen.getByLabelText('Capacidad'), { target: { value: '11' } });
    fireEvent.change(screen.getByLabelText('Color'), { target: { value: '21' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    await screen.findByLabelText('Número de serie 1');
  }

  it('un IMEI mal escrito no llega a revisarse', async () => {
    await toStock();
    type('Número de serie 1', 'SN1');
    type('IMEI 1', BAD_IMEI);
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    expect(await screen.findByText(/Equipo 1: Ese IMEI no es válido/)).toBeInTheDocument();
    expect(mReg.submitRegistration).not.toHaveBeenCalled();
  });

  it('manda el modelo, sus opciones, la sucursal y cada equipo', async () => {
    await toStock();
    type('Número de serie 1', 'SN1');
    type('IMEI 1', GOOD_IMEI);
    fireEvent.click(screen.getByRole('button', { name: 'Agregar equipo' }));
    type('Número de serie 2', 'SN2');
    type('IMEI 2', GOOD_IMEI_2);
    fireEvent.change(screen.getByLabelText(/^Sucursal de ingreso/), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    await waitFor(() => expect(mReg.submitRegistration).toHaveBeenCalled());
    expect(sent()).toMatchObject({
      kind: 'new_device', model: { id: 9 }, options: [11, 21], branch: 2, requires_imei: true,
      units: [{ serial_number: 'SN1', imei: GOOD_IMEI }, { serial_number: 'SN2', imei: GOOD_IMEI_2 }],
    });
  });
});

describe('un seminuevo', () => {
  it('sube sus fotos reales al equipo creado, por el endpoint de fotos', async () => {
    mReg.submitRegistration.mockImplementation(async (body) => result({
      kind: 'used_device', dry_run: Boolean(body.dry_run), units: [{ id: body.dry_run ? null : 77 }],
      links: { product: '/admin/products/41', units: '/admin/inventory/units?product=41', storefront: null },
    }));
    mUnits.uploadUnitPhoto.mockResolvedValue({ id: 1, url: '/x.jpg', alt_text: '' });
    wizard(ALL);
    await choose(/Equipo seminuevo/);
    await fillProduct({ name: 'iPhone 15 seminuevo', category: '6', price: '2500' });
    fireEvent.change(screen.getByLabelText(/^Modelo/), { target: { value: '9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    await screen.findByLabelText('Número de serie');
    type('Número de serie', 'SNU');
    type('IMEI', GOOD_IMEI);
    type(/^Batería/, '88');
    fireEvent.change(screen.getByLabelText(/^Estado estético/), { target: { value: 'excelente' } });
    type(/^Precio de este equipo/, '2399');
    type(/^Costo de este equipo/, '1800');
    const photo = new File(['x'], 'frente.jpg', { type: 'image/jpeg' });
    fireEvent.change(screen.getByLabelText(/Fotos reales/), { target: { files: [photo] } });
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    await screen.findByRole('heading', { name: 'Revisar y registrar' });
    expect(sent(0)).toMatchObject({
      kind: 'used_device', unit: { serial_number: 'SNU', imei: GOOD_IMEI, battery_health: 88,
        cosmetic_grade: 'excelente', sale_price: '2399', cost: '1800' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Registrar artículo' }));
    await screen.findByRole('heading', { name: 'Artículo registrado' });
    await waitFor(() => expect(mUnits.uploadUnitPhoto).toHaveBeenCalledWith(77, photo));
    expect(screen.getByRole('link', { name: 'Ver los equipos' })).toHaveAttribute('href', '/admin/inventory/units?product=41');
  });
});

describe('disponible por proveedor', () => {
  it('no ofrece stock propio; manda proveedor y oferta', async () => {
    wizard(ALL);
    await choose(/Disponible por proveedor/);
    await fillProduct({ name: 'iPad Pro', category: '6', price: '6999' });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    expect(await screen.findByText(/no entra en tu stock/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Proveedor/), { target: { value: '4' } });
    type(/^Precio comercial/, '7199');
    type(/^Costo pactado/, '6000');
    type(/^Cantidad confirmada/, '10');
    type(/^Plazo de entrega/, '3');
    fireEvent.click(screen.getByRole('button', { name: 'Revisar' }));
    await waitFor(() => expect(mReg.submitRegistration).toHaveBeenCalled());
    expect(sent()).toMatchObject({
      kind: 'supplier', supplier: { id: 4 },
      offer: { commercial_price: '7199', agreed_cost: '6000', confirmed_quantity: 10, lead_time_days: 3 },
    });
    expect(sent().stock).toBeUndefined();
    expect(sent().units).toBeUndefined();
  });
});

describe('la revisión y el envío', () => {
  it('lo que el servidor rechaza al revisar se dice, con el paso al que volver', async () => {
    mReg.submitRegistration.mockRejectedValueOnce(new registration.RegistrationApiError(
      'Ese IMEI ya está registrado en otro equipo de la empresa.', 400,
      { step: 'stock', field: 'imei', line: 2 }));
    wizard(ALL);
    await choose(/Accesorio o repuesto/);
    await fillProduct();
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Revisar' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Ese IMEI ya está registrado');
    expect(alert.textContent).toContain('línea 2');
    fireEvent.click(within(alert).getByRole('button', { name: /Volver a Stock/ }));
    expect(await screen.findByRole('button', { name: 'Revisar' })).toBeInTheDocument();
  });

  it('dice por qué no se publicará en e-commerce', async () => {
    mReg.submitRegistration.mockResolvedValue(result({
      dry_run: true, publication: { requested: true, published: false, problems: ['no_category'] },
      warnings: ['«X» ya es esa variante'] }));
    wizard(ALL);
    await choose(/Accesorio o repuesto/);
    await fillProduct({ category: '' });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Revisar' }));
    expect(await screen.findByText(/no tiene categoría/)).toBeInTheDocument();
    expect(screen.getByText(/ya es esa variante/)).toBeInTheDocument();
  });

  it('reintentar lo mismo reutiliza la clave; no se registra dos veces', async () => {
    let call = 0;
    mReg.submitRegistration.mockImplementation(async (body) => {
      call += 1;
      if (body.dry_run) return result({ dry_run: true });
      if (call === 2) throw new TypeError('Failed to fetch');
      return result();
    });
    wizard(ALL);
    await choose(/Accesorio o repuesto/);
    await fillProduct();
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Revisar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Registrar artículo' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Registrar artículo' }));
    await screen.findByRole('heading', { name: 'Artículo registrado' });
    expect(options(1)?.key).toBeTruthy();
    expect(options(2)?.key).toBe(options(1)?.key);
  });

  it('pide publicar después de subir imágenes cuando sólo faltaba la imagen', async () => {
    mReg.submitRegistration.mockImplementation(async (body) => result({
      dry_run: Boolean(body.dry_run), publication: { requested: true, published: false, problems: ['no_image'] },
      links: { product: '/admin/products/41', units: null, storefront: null } }));
    mAdmin.uploadProductImage.mockResolvedValue({} as never);
    mAdmin.bulkSetProductVisibility.mockResolvedValue({ action: 'publish', changed: 1, unchanged: 0, refused: [], total: 1 });
    wizard(ALL);
    await choose(/Accesorio o repuesto/);
    await fillProduct();
    const image = new File(['x'], 'funda.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText(/Imágenes del producto/), { target: { files: [image] } });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Revisar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Registrar artículo' }));
    await screen.findByRole('heading', { name: 'Artículo registrado' });
    await waitFor(() => expect(mAdmin.uploadProductImage).toHaveBeenCalledWith(41, image));
    await waitFor(() => expect(mAdmin.bulkSetProductVisibility).toHaveBeenCalledWith('publish', { ids: [41] }, 7));
    expect(await screen.findByText(/Publicado en e-commerce/)).toBeInTheDocument();
  });

  const NEW_MODEL = { id: 9, created: true, name: 'Galaxy S25', slug: 'galaxy-s25' };

  it('dice que el modelo nuevo se crea oculto cuando su primera variante no se puede publicar', async () => {
    mReg.submitRegistration.mockImplementation(async (body) => result({
      dry_run: Boolean(body.dry_run), model: body.dry_run ? { ...NEW_MODEL, id: null } : NEW_MODEL,
      publication: { requested: true, published: false, problems: ['no_category'], model_hidden: true },
      links: { product: '/admin/products/41', units: null, storefront: null } }));
    wizard(ALL);
    await choose(/Accesorio o repuesto/);
    await fillProduct({ category: '' });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Revisar' }));
    expect(await screen.findByText(/El modelo nuevo «Galaxy S25» se creará oculto en e-commerce/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Registrar artículo' }));
    await screen.findByRole('heading', { name: 'Artículo registrado' });
    expect(screen.getByText(/El modelo nuevo «Galaxy S25» quedó oculto en e-commerce/)).toBeInTheDocument();
    expect(mReg.bulkSetModelVisibility).not.toHaveBeenCalled();
  });

  it('si la variante se publica tras subir sus imágenes, publica también su modelo nuevo', async () => {
    mReg.submitRegistration.mockImplementation(async (body) => result({
      dry_run: Boolean(body.dry_run), model: NEW_MODEL,
      publication: { requested: true, published: false, problems: ['no_image'], model_hidden: true },
      links: { product: '/admin/products/41', units: null, storefront: null } }));
    mAdmin.uploadProductImage.mockResolvedValue({} as never);
    mAdmin.bulkSetProductVisibility.mockResolvedValue({ action: 'publish', changed: 1, unchanged: 0, refused: [], total: 1 });
    mReg.bulkSetModelVisibility.mockResolvedValue({ action: 'publish', changed: 1, unchanged: 0, total: 1 });
    wizard(ALL);
    await choose(/Accesorio o repuesto/);
    await fillProduct();
    const image = new File(['x'], 'funda.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText(/Imágenes del producto/), { target: { files: [image] } });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Revisar' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Registrar artículo' }));
    await screen.findByRole('heading', { name: 'Artículo registrado' });
    await waitFor(() => expect(mReg.bulkSetModelVisibility).toHaveBeenCalledWith('publish', [9], 7));
    expect(await screen.findByText(/Publicado en e-commerce/)).toBeInTheDocument();
    expect(screen.queryByText(/quedó oculto/)).toBeNull();
    expect(screen.getByRole('link', { name: /Ver en la tienda/ })).toHaveAttribute('href', '/modelo/galaxy-s25');
  });
});
