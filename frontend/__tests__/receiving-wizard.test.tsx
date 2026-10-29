/**
 * INV-OPS-01 — asistente «Recibir mercadería»: pasos, validación, revisión,
 * confirmación idempotente y pantalla final. Sin red: el contrato de recibos se
 * simula; el buscador de productos y los proveedores, también.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ReceivingWizard, type ReceivingAccess } from '@/app/admin/components/ReceivingWizard';
import * as receipts from '@/app/admin/lib/inventory-receipts';
import { forgetBranch, rememberBranch } from '@/app/admin/lib/last-branch';
import * as internalApi from '@/app/admin/lib/internal-api';
import * as admin from '@/app/lib/admin';

const mockRouter = { push: jest.fn(), replace: jest.fn(), refresh: jest.fn(), back: jest.fn(), forward: jest.fn(), prefetch: jest.fn() };
jest.mock('next/navigation', () => ({ usePathname: () => '/admin/inventory/receive', useRouter: () => mockRouter }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
  fetchAdminProductDetail: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchSuppliers: jest.fn(),
}));
jest.mock('@/app/admin/lib/inventory-receipts', () => {
  const actual = jest.requireActual('@/app/admin/lib/inventory-receipts');
  return { ...actual, previewReceipt: jest.fn(), confirmReceipt: jest.fn(), newReceiptKey: jest.fn() };
});

const search = admin.fetchAdminProducts as jest.Mock;
const detail = admin.fetchAdminProductDetail as jest.Mock;
const suppliersApi = internalApi.fetchSuppliers as jest.Mock;
const preview = receipts.previewReceipt as jest.Mock;
const confirm = receipts.confirmReceipt as jest.Mock;
const makeKey = receipts.newReceiptKey as jest.Mock;
const realKey = jest.requireActual('@/app/admin/lib/inventory-receipts').newReceiptKey;

const BRANCHES = [{ id: 1, name: 'Miraflores' }, { id: 2, name: 'Surco' }];
const ACCESS: ReceivingAccess = { costs: false, manageProducts: false, viewProducts: true, labels: true };
const SCOPE = { user: 1, company: 7 };

function product(id: number, name: string, over: Partial<admin.AdminProduct> = {}): admin.AdminProduct {
  return {
    id, name, slug: `p-${id}`, description: '', price: '10.00', inventory: 0, image_url: '', category_id: 1,
    category_name: 'x', is_active: true, is_published_online: false, publication_problems: [],
    is_serialized: false, requires_imei: false, sale_mode: 'stock', condition_line: 'new', product_model_id: null,
    primary_code: `SKU-${id}`, search_exact: true, created_at: '', updated_at: '', ...over,
  } as admin.AdminProduct;
}
const PHONE = product(5, 'iPhone 15 128GB', { is_serialized: true, requires_imei: true });
const CABLE = product(9, 'Cable USB-C 1m');
const page = (rows: admin.AdminProduct[]) => ({ count: rows.length, page: 1, page_size: 8, results: rows });

function previewFor(body: receipts.ReceiptBody, over: Partial<receipts.ReceiptPreview['lines'][number]>[] = []): receipts.ReceiptPreview {
  const lines = body.lines.map((l, index) => ({
    index, kind: l.kind,
    product: { id: l.product, name: l.product === 5 ? PHONE.name : CABLE.name, sku: `SKU-${l.product}`, is_serialized: l.kind === 'units' },
    status: 'ok' as const, errors: [], warnings: [],
    will_receive: l.kind === 'units' ? l.units.length : l.quantity,
    labels: l.kind === 'units' ? l.units.length : 1, duplicates: [], ...(over[index] ?? {}),
  }));
  return {
    ok: lines.every((l) => l.status === 'ok'),
    branch: { id: body.branch, name: BRANCHES.find((b) => b.id === body.branch)!.name },
    supplier: body.supplier ? { id: body.supplier, name: 'Importadora Sur' } : null,
    document_ref: body.document_ref,
    totals: {
      lines: lines.length,
      quantity_units: lines.filter((l) => l.kind === 'quantity').reduce((n, l) => n + l.will_receive, 0),
      devices: lines.filter((l) => l.kind === 'units').reduce((n, l) => n + l.will_receive, 0),
      labels: lines.reduce((n, l) => n + l.labels, 0),
    },
    lines,
  };
}

function resultFor(body: receipts.ReceiptBody, over: Partial<receipts.ReceiptResult> = {}): receipts.ReceiptResult {
  return {
    receipt_id: 44, branch: { id: body.branch, name: 'Miraflores' }, supplier: null, document_ref: body.document_ref,
    totals: { lines: body.lines.length, quantity_units: 3, devices: 1, labels: 4 },
    lines: body.lines.map((l, index) => ({
      index, kind: l.kind, product: { id: l.product, name: l.product === 5 ? PHONE.name : CABLE.name },
      received: l.kind === 'quantity' ? l.quantity : 0, movement_id: 1, units: l.kind === 'units' ? l.units.length : 0,
      label_batch: { id: 8, count: 4, url: '/admin/inventory/labels?batch=8' }, label_error: false,
    })),
    label_batches: [{ id: 8, count: 4, url: '/admin/inventory/labels?batch=8' }],
    label_error: false,
    ...over,
  };
}

const user = () => userEvent.setup();

async function renderWizard(access: Partial<ReceivingAccess> = {}, props: Partial<React.ComponentProps<typeof ReceivingWizard>> = {}) {
  const u = user();
  await act(async () => {
    render(<ReceivingWizard access={{ ...ACCESS, ...access }} scope={SCOPE} branches={BRANCHES} defaultBranch={1} {...props} />);
  });
  return u;
}

const next = (u: ReturnType<typeof user>, name: RegExp) => u.click(screen.getByRole('button', { name }));
const picker = () => screen.getByRole('combobox', { name: /Buscar el artículo/ });

async function addProduct(u: ReturnType<typeof user>, p: admin.AdminProduct) {
  search.mockResolvedValueOnce(page([p]));
  await u.type(picker(), `${p.name.slice(0, 4)}{Enter}`);
  await screen.findByRole('article', { name: p.name });
}

async function toArticles(u: ReturnType<typeof user>) {
  await next(u, /Siguiente: artículos/);
  await screen.findByRole('heading', { name: '¿Qué llegó?' });
}

async function fillPhone(u: ReturnType<typeof user>, serial = 'SER1', imei = '356789012345678') {
  const card = screen.getByRole('article', { name: PHONE.name });
  const serialBox = within(card).getAllByLabelText(/Número de serie/).slice(-1)[0];
  await u.type(serialBox, serial);
  await u.type(within(card).getAllByLabelText(/^IMEI \*/).slice(-1)[0], imei);
}

let storage: { get: jest.SpyInstance; set: jest.SpyInstance; remove: jest.SpyInstance };

beforeEach(() => {
  window.scrollTo = jest.fn();
  window.history.replaceState(null, ''); // each test starts a flow of its own
  forgetBranch();
  receipts.forgetStashedDraft();
  search.mockReset();
  detail.mockReset();
  preview.mockReset().mockImplementation(async (body) => previewFor(body));
  confirm.mockReset().mockImplementation(async (body) => resultFor(body));
  makeKey.mockReset().mockImplementation(realKey);
  suppliersApi.mockReset().mockResolvedValue({ count: 1, page: 1, page_size: 1, results: [{ id: 3, name: 'Importadora Sur' }] });
  storage = {
    get: jest.spyOn(Storage.prototype, 'getItem'),
    set: jest.spyOn(Storage.prototype, 'setItem'),
    remove: jest.spyOn(Storage.prototype, 'removeItem'),
  };
});
afterEach(() => jest.restoreAllMocks());

describe('paso 1 · Procedencia', () => {
  it('muestra los cuatro pasos y marca el actual', async () => {
    await renderWizard();
    const steps = within(screen.getByRole('navigation', { name: 'Pasos de la recepción' })).getAllByRole('listitem');
    expect(steps.map((s) => s.textContent)).toEqual([
      expect.stringContaining('Procedencia'), expect.stringContaining('Artículos'),
      expect.stringContaining('Revisar y confirmar'), expect.stringContaining('Terminado'),
    ]);
    expect(steps[0]).toHaveAttribute('aria-current', 'step');
    expect(steps[1]).not.toHaveAttribute('aria-current');
  });

  it('abre en la última sucursal usada, o en la predeterminada', async () => {
    rememberBranch(2);
    await renderWizard();
    expect(screen.getByLabelText(/Sucursal que recibe/)).toHaveValue('2');
  });

  it('con una sola sucursal la deja elegida; sin ninguna no deja avanzar', async () => {
    await renderWizard({}, { branches: [{ id: 9, name: 'Única' }], defaultBranch: null });
    expect(screen.getByLabelText(/Sucursal que recibe/)).toHaveValue('9');
  });

  it('sin sucursal elegida «Siguiente» está desactivado', async () => {
    await renderWizard({}, { defaultBranch: null });
    expect(screen.getByLabelText(/Sucursal que recibe/)).toHaveValue('');
    expect(screen.getByRole('button', { name: /Siguiente: artículos/ })).toBeDisabled();
  });

  it('carga proveedores y limita documento (60) y observaciones (300)', async () => {
    await renderWizard();
    expect(await screen.findByRole('option', { name: 'Importadora Sur' })).toBeInTheDocument();
    expect(screen.getByLabelText(/Referencia del documento/)).toHaveAttribute('maxlength', '60');
    expect(screen.getByLabelText(/Observaciones/)).toHaveAttribute('maxlength', '300');
  });

  it('si los proveedores no cargan, avisa y deja seguir', async () => {
    suppliersApi.mockRejectedValue(new Error('x'));
    await renderWizard();
    expect(await screen.findByText(/No se pudo cargar la lista de proveedores/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Siguiente: artículos/ })).toBeEnabled();
  });

  it('sin permiso de ver productos no se pide el proveedor', async () => {
    await renderWizard({ viewProducts: false });
    expect(screen.queryByLabelText(/Proveedor/)).not.toBeInTheDocument();
    expect(suppliersApi).not.toHaveBeenCalled();
  });
});

describe('paso 2 · Artículos', () => {
  it('un código escaneado más Enter agrega el producto exacto; uno con serie abre el editor de equipos', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, PHONE);
    const card = screen.getByRole('article', { name: PHONE.name });
    expect(within(card).getByText('Equipos con serie')).toBeInTheDocument();
    expect(within(card).getByLabelText(/Número de serie/)).toBeInTheDocument();
    expect(within(card).getByLabelText(/^IMEI \*/)).toBeInTheDocument();
    expect(within(card).getByLabelText(/IMEI 2/)).toBeInTheDocument();
    expect(within(card).getByLabelText(/Estado del equipo/)).toHaveValue('new');
  });

  it('un producto por cantidad abre un contador con botones grandes', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, CABLE);
    const card = screen.getByRole('article', { name: CABLE.name });
    const qty = within(card).getByLabelText('Cantidad');
    expect(qty).toHaveValue('1');
    await u.click(within(card).getByRole('button', { name: 'Una unidad más' }));
    await u.click(within(card).getByRole('button', { name: 'Una unidad más' }));
    expect(qty).toHaveValue('3');
    await u.click(within(card).getByRole('button', { name: 'Una unidad menos' }));
    expect(qty).toHaveValue('2');
    await u.clear(qty);
    await u.type(qty, '1a2');
    expect(qty).toHaveValue('12');
  });

  it('el costo sólo se pide con costs.view', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, CABLE);
    expect(screen.queryByLabelText(/Costo/)).not.toBeInTheDocument();
    await addProduct(u, PHONE);
    expect(screen.queryByLabelText(/Costo/)).not.toBeInTheDocument();
  });

  it('con costs.view aparece el costo por unidad y el de cada equipo', async () => {
    const u = await renderWizard({ costs: true });
    await toArticles(u);
    await addProduct(u, CABLE);
    await addProduct(u, PHONE);
    expect(screen.getByLabelText(/Costo por unidad/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Costo del equipo/)).toBeInTheDocument();
  });

  it('Enter lleva de la serie al IMEI y del IMEI a una fila nueva lista para escanear', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, PHONE);
    const card = screen.getByRole('article', { name: PHONE.name });
    await u.type(within(card).getByLabelText(/Número de serie/), 'A1{Enter}');
    expect(within(card).getByLabelText(/^IMEI \*/)).toHaveFocus();
    await u.keyboard('356789012345678{Enter}');
    const serials = within(card).getAllByLabelText(/Número de serie/);
    expect(serials).toHaveLength(2);
    expect(serials[1]).toHaveFocus();
    // An empty row does not stack more rows.
    await u.keyboard('{Enter}');
    expect(within(card).getAllByLabelText(/Número de serie/)).toHaveLength(2);
  });

  it('«Agregar otro equipo» y «Quitar este equipo»', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, PHONE);
    const card = screen.getByRole('article', { name: PHONE.name });
    expect(within(card).queryByRole('button', { name: /Quitar el equipo/ })).not.toBeInTheDocument();
    await u.click(within(card).getByRole('button', { name: 'Agregar otro equipo' }));
    expect(within(card).getAllByLabelText(/Número de serie/)).toHaveLength(2);
    await u.click(within(card).getByRole('button', { name: /Quitar el equipo 1/ }));
    expect(within(card).getAllByLabelText(/Número de serie/)).toHaveLength(1);
  });

  it('muestra la lista en curso con totales, y se puede quitar una línea', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, CABLE);
    await addProduct(u, PHONE);
    await fillPhone(u);
    expect(screen.getByText(/2 artículos · 1 unidad · 1 equipo/)).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: `Quitar ${CABLE.name} de la lista` }));
    expect(screen.queryByRole('article', { name: CABLE.name })).not.toBeInTheDocument();
  });

  it('el mismo producto dos veces no duplica la línea: avisa y sigue en la misma', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, CABLE);
    search.mockResolvedValueOnce(page([CABLE]));
    await u.type(picker(), 'Cabl{Enter}');
    expect(await screen.findByText(/ya está en la lista/)).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(1);
  });

  it('«Siguiente» con errores marca fila por fila y no avanza', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, PHONE);
    await u.type(within(screen.getByRole('article', { name: PHONE.name })).getByLabelText(/Número de serie/), 'A1');
    await next(u, /Siguiente: revisar/);
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
    expect(screen.getByText(/Falta el IMEI: este equipo lo necesita/)).toBeInTheDocument();
    expect(preview).not.toHaveBeenCalled();
  });

  it('series repetidas se señalan en cada fila', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, PHONE);
    await fillPhone(u, 'DUP', '111111111111111');
    await u.click(screen.getByRole('button', { name: 'Agregar otro equipo' }));
    await fillPhone(u, 'dup', '222222222222222');
    await next(u, /Siguiente: revisar/);
    expect(screen.getAllByText(/número de serie está repetido/)).toHaveLength(2);
  });

  it('sin artículos pide agregar uno', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await next(u, /Siguiente: revisar/);
    expect(screen.getByText('Agrega al menos un artículo para continuar.')).toBeInTheDocument();
  });

  it('crear artículo sólo se ofrece con products.manage; sin él, un mensaje claro', async () => {
    const u = await renderWizard({ manageProducts: false });
    await toArticles(u);
    expect(screen.queryByRole('link', { name: /crear artículo/ })).not.toBeInTheDocument();
    expect(screen.getByText(/no puede crear artículos/)).toBeInTheDocument();
  });

  it('con products.manage el enlace lleva al registro y guarda la lista en memoria', async () => {
    const u = await renderWizard({ manageProducts: true });
    await toArticles(u);
    await addProduct(u, CABLE);
    const link = screen.getByRole('link', { name: /No existe: crear artículo/ });
    expect(link).toHaveAttribute('href', '/admin/products/register?from=receive');
    link.addEventListener('click', (e) => e.preventDefault());
    await u.click(link);
    const held = receipts.takeStashedDraft(SCOPE);
    expect(held?.lines.map((l) => l.product.id)).toEqual([9]);
    expect(held?.origin.branch).toBe(1);
  });

  it('ofrece cargar una lista pegada sin reimplementarla', async () => {
    const u = await renderWizard();
    await toArticles(u);
    expect(screen.getByRole('link', { name: /Cargar desde una lista pegada/ })).toHaveAttribute('href', '/admin/inventory/bulk');
  });

  it('de vuelta de «crear artículo»: recupera la lista y preselecciona el producto de ?product=', async () => {
    receipts.stashDraft(
      { branch: 2, supplier: null, document_ref: 'F1', notes: '' },
      [{ ...receipts.newLine({ id: 9, name: CABLE.name, is_serialized: false, requires_imei: false }), quantity: '4' }],
      SCOPE,
    );
    detail.mockResolvedValue(product(5, PHONE.name, { is_serialized: true, requires_imei: true }));
    await renderWizard({}, { initialProductId: 5 });
    expect(await screen.findByRole('article', { name: PHONE.name })).toBeInTheDocument();
    expect(screen.getByRole('article', { name: CABLE.name })).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: CABLE.name })).getByLabelText('Cantidad')).toHaveValue('4');
    expect(detail).toHaveBeenCalledWith(5);
    // …and the draft is given back only once.
    expect(receipts.takeStashedDraft(SCOPE)).toBeNull();
  });

  it('?product= que no carga avisa en lenguaje humano', async () => {
    detail.mockRejectedValue(new Error('Producto no encontrado.'));
    await renderWizard({}, { initialProductId: 77 });
    expect(await screen.findByText('Producto no encontrado.')).toBeInTheDocument();
  });

  it('Atrás y Siguiente no pierden lo escrito', async () => {
    const u = await renderWizard();
    await u.type(screen.getByLabelText(/Referencia del documento/), 'F001-1');
    await toArticles(u);
    await addProduct(u, CABLE);
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    expect(screen.getByLabelText(/Referencia del documento/)).toHaveValue('F001-1');
    await toArticles(u);
    expect(screen.getByRole('article', { name: CABLE.name })).toBeInTheDocument();
  });
});

async function toReview(u: ReturnType<typeof user>, p: admin.AdminProduct = CABLE) {
  await toArticles(u);
  await addProduct(u, p);
  if (p.is_serialized) await fillPhone(u);
  await next(u, /Siguiente: revisar/);
  await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
}

describe('paso 3 · Revisar y confirmar', () => {
  it('pide la revisión al servidor con el cuerpo del contrato y la cuenta en lenguaje claro', async () => {
    const u = await renderWizard();
    await u.type(screen.getByLabelText(/Referencia del documento/), '  F001-9 ');
    await u.selectOptions(await screen.findByLabelText(/Proveedor/), '3');
    await toReview(u, PHONE);
    expect(preview).toHaveBeenCalledTimes(1);
    expect(preview.mock.calls[0][0]).toEqual({
      branch: 1, supplier: 3, document_ref: 'F001-9', notes: '',
      lines: [{ kind: 'units', product: 5, units: [{ serial: 'SER1', imei: '356789012345678', condition: 'new' }] }],
    });
    expect(await screen.findByText(/Entrarán 0 unidades por cantidad y 1 equipo identificado\. Se prepararán 1 etiqueta\./)).toBeInTheDocument();
    expect(screen.getByText('Importadora Sur')).toBeInTheDocument();
    expect(screen.getByText('F001-9')).toBeInTheDocument();
    expect(screen.getByText(/Se usa el artículo que ya existe \(SKU-5\)\. Entran 1 equipo identificado/)).toBeInTheDocument();
  });

  it('errores y duplicados por línea, con «Corregir» que vuelve a esa línea', async () => {
    preview.mockImplementation(async (body) => previewFor(body, [{
      status: 'error', errors: ['El IMEI no es válido.'], duplicates: ['356789012345678'],
    }]));
    const u = await renderWizard();
    await toReview(u, PHONE);
    expect(await screen.findByText('El IMEI no es válido.')).toBeInTheDocument();
    expect(screen.getByText('356789012345678')).toBeInTheDocument();
    expect(screen.getByText('Duplicados:')).toBeInTheDocument();
    expect(screen.getByText(/mientras tanto no se puede confirmar/)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirmar recepción' })).toBeDisabled();
    await u.click(screen.getByRole('button', { name: `Corregir ${PHONE.name}` }));
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
    const card = screen.getByRole('article', { name: PHONE.name });
    await waitFor(() => expect(within(card).getAllByLabelText(/Número de serie/)[0]).toHaveFocus());
    expect(within(card).getAllByLabelText(/Número de serie/)[0]).toHaveValue('SER1');
  });

  it('las advertencias se muestran pero no impiden confirmar', async () => {
    preview.mockImplementation(async (body) => previewFor(body, [{ warnings: ['El costo es mucho menor al habitual.'] }]));
    const u = await renderWizard();
    await toReview(u);
    expect(await screen.findByText('El costo es mucho menor al habitual.')).toBeInTheDocument();
    await u.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('button', { name: 'Confirmar recepción' })).toBeEnabled();
  });

  it('si la revisión falla, lo dice, deja reintentar y volver', async () => {
    preview.mockRejectedValueOnce(new receipts.ReceiptApiError('La sucursal ya no existe.', 409));
    const u = await renderWizard();
    await toReview(u);
    expect(await screen.findByText('La sucursal ya no existe.')).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByRole('checkbox')).toBeInTheDocument();
    expect(preview).toHaveBeenCalledTimes(2);
  });

  it('confirmar queda desactivado hasta aceptar explícitamente', async () => {
    const u = await renderWizard();
    await toReview(u);
    const button = await screen.findByRole('button', { name: 'Confirmar recepción' });
    expect(button).toBeDisabled();
    await u.click(screen.getByRole('checkbox', { name: /Revisé los datos/ }));
    expect(button).toBeEnabled();
  });

  it('un doble clic confirma una sola vez', async () => {
    let release: (value: receipts.ReceiptResult) => void = () => undefined;
    confirm.mockImplementation((body) => new Promise((resolve) => { release = (r) => resolve(r ?? resultFor(body)); }));
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    const button = screen.getByRole('button', { name: 'Confirmar recepción' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('button', { name: 'Registrando…' })).toBeDisabled();
    await act(async () => { release(undefined as never); });
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it('manda Idempotency-Key estable: se reutiliza al reintentar y cambia sólo si cambia el contenido', async () => {
    confirm.mockRejectedValueOnce(new receipts.ReceiptApiError('No llegó la respuesta del servidor.', 0, true));
    confirm.mockRejectedValueOnce(new receipts.ReceiptApiError('No llegó la respuesta del servidor.', 0, true));
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    expect(await screen.findByText(/No llegó la respuesta/)).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2));
    const [firstKey, secondKey] = confirm.mock.calls.map((c) => c[1]);
    expect(firstKey).toMatch(/^rcv-/);
    expect(secondKey).toBe(firstKey);

    // Changing the content needs the explicit acknowledgement; then: back, edit, review again → another key.
    expect(screen.getByRole('button', { name: 'Atrás' })).toBeDisabled();
    await u.click(screen.getByRole('button', { name: /Revisé el historial y no se registró nada, quiero editar/ }));
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    await u.click(within(screen.getByRole('article', { name: CABLE.name })).getByRole('button', { name: 'Una unidad más' }));
    await next(u, /Siguiente: revisar/);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    const thirdKey = confirm.mock.calls[2][1];
    expect(thirdKey).toMatch(/^rcv-/);
    expect(thirdKey).not.toBe(firstKey);
  });

  it('sin fuente segura de claves no confirma y lo explica', async () => {
    makeKey.mockReturnValue(null);
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    expect(await screen.findByText(/no puede generar la clave de seguridad/)).toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('editar tras revisar invalida la revisión', async () => {
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    await u.click(within(screen.getByRole('article', { name: CABLE.name })).getByRole('button', { name: 'Una unidad más' }));
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    await u.click(screen.getByRole('button', { name: /Siguiente: artículos/ }));
    await next(u, /Siguiente: revisar/);
    expect(preview).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole('checkbox')).not.toBeChecked();
  });
});

describe('foco, textos y totales (hallazgos del E2E)', () => {
  it('en cada cambio de paso el foco va al título del paso y se anuncia', async () => {
    const u = await renderWizard();
    await toArticles(u);
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toHaveFocus();
    expect(screen.getByText('Paso 2 de 4: Artículos')).toBeInTheDocument();
    await addProduct(u, CABLE);
    await next(u, /Siguiente: revisar/);
    const review = await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
    expect(review).toHaveFocus();
    expect(review).toHaveAttribute('tabindex', '-1');
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    expect(await screen.findByRole('heading', { name: 'Recepción registrada' })).toHaveFocus();
    expect(screen.getByText('Paso 4 de 4: Terminado')).toBeInTheDocument();
  });

  it('«Registrar otra recepción» lleva el foco al título del paso 2', async () => {
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    await u.click(screen.getByRole('button', { name: 'Registrar otra recepción' }));
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toHaveFocus();
  });

  it('con errores no muestra totales en cero: dice cuántas líneas corregir', async () => {
    preview.mockImplementation(async (body) => ({ ...previewFor(body, [{ status: 'error', errors: ['Falta algo.'] }]), totals: { lines: 0, quantity_units: 0, devices: 0, labels: 0 } }));
    const u = await renderWizard();
    await toReview(u, PHONE);
    expect(await screen.findByText('No se puede confirmar todavía: corrige 1 línea.')).toBeInTheDocument();
    expect(screen.queryByText(/Entrarán/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Se prepararán/)).not.toBeInTheDocument();
  });

  it('una línea con errores nunca dice «Entran 0 …»: pide corregirla', async () => {
    preview.mockImplementation(async (body) => previewFor(body, [{ status: 'error', errors: ['Falta algo.'], will_receive: 0, labels: 0 }, { status: 'error', errors: ['Otra cosa.'], will_receive: 0, labels: 0 }]));
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, PHONE);
    await fillPhone(u);
    await addProduct(u, CABLE);
    await next(u, /Siguiente: revisar/);
    await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
    const lines = await screen.findAllByText(/Esta línea tiene errores; corrígela para poder confirmar\./);
    expect(lines).toHaveLength(2);
    expect(screen.queryByText(/Entran \d/)).not.toBeInTheDocument();
    expect(screen.queryByText(/etiqueta/)).not.toBeInTheDocument();
  });

  it('el título de los duplicados distingue «repetidos en esta recepción» de «ya registrados»', async () => {
    preview.mockImplementation(async (body) => previewFor(body, [{
      status: 'error',
      duplicates: [
        'Equipo 2: este IMEI está repetido en la misma recepción.',
        'Equipo 1: ese número de serie ya está registrado para este producto.',
        'Equipo 3: algo raro con el valor.',
      ],
    }]));
    const u = await renderWizard();
    await toReview(u, PHONE);
    const line = (await screen.findAllByRole('listitem')).find((li) => li.textContent?.includes(PHONE.name) && li.textContent?.includes('Equipo 2'))!;
    expect(within(line).getByText('Repetidos en esta recepción:')).toBeInTheDocument();
    expect(within(line).getByText('Ya registrados en el sistema:')).toBeInTheDocument();
    expect(within(line).getByText('Otros duplicados:')).toBeInTheDocument();
    expect(screen.queryByText('Ya existen en el sistema:')).not.toBeInTheDocument();
  });

  it('409 busy: mensaje claro, mismo contenido y misma clave, reintento inmediato', async () => {
    confirm.mockRejectedValueOnce(new receipts.ReceiptApiError('Otra recepción está en curso; vuelve a intentarlo.', 409, false, { code: 'busy' }));
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    expect(await screen.findByText('Otra recepción está en curso; vuelve a intentarlo en unos segundos.')).toBeInTheDocument();
    expect(screen.getByRole('checkbox')).toBeChecked();
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    expect(confirm.mock.calls[1][1]).toBe(confirm.mock.calls[0][1]);
  });
});

describe('contrato real: unit_issues, invalid_lines, conflicto de clave', () => {
  it('pone el mensaje del servidor junto al equipo exacto y «Corregir» enfoca esa fila', async () => {
    preview.mockImplementation(async (body) => previewFor(body, [{
      status: 'error', errors: ['Equipo 2: ese IMEI ya está registrado en otro equipo de la empresa.'],
      duplicates: [],
      unit_issues: [{ unit: 1, field: 'imei', kind: 'duplicate', message: 'Equipo 2: ese IMEI ya está registrado en otro equipo de la empresa.' }],
    }]));
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, PHONE);
    await fillPhone(u, 'S-1', '111111111111111');
    await u.click(screen.getByRole('button', { name: 'Agregar otro equipo' }));
    await fillPhone(u, 'S-2', '222222222222222');
    await next(u, /Siguiente: revisar/);
    await u.click(await screen.findByRole('button', { name: `Corregir ${PHONE.name}` }));
    const card = screen.getByRole('article', { name: PHONE.name });
    const imeis = within(card).getAllByLabelText(/^IMEI \*/);
    await waitFor(() => expect(imeis[1]).toHaveFocus());
    expect(imeis[1]).toHaveAttribute('aria-invalid', 'true');
    expect(imeis[0]).not.toHaveAttribute('aria-invalid');
    expect(within(card).getByText(/ese IMEI ya está registrado/)).toBeInTheDocument();
  });

  it('un 400 invalid_lines al confirmar muestra la revisión con errores y no registra nada', async () => {
    confirm.mockImplementation(async (body) => {
      throw new receipts.ReceiptApiError('Hay líneas que no se pueden recibir. No se recibió nada.', 400, false, {
        code: 'invalid_lines', preview: { ...previewFor(body, [{ status: 'error', errors: ['El producto está desactivado.'] }]), ok: false },
      });
    });
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    expect(await screen.findByText(/No se registró nada/)).toBeInTheDocument();
    expect(screen.getByText('El producto está desactivado.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: `Corregir ${CABLE.name}` })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirmar recepción' })).toBeDisabled();
    expect(screen.queryByRole('heading', { name: 'Recepción registrada' })).not.toBeInTheDocument();
  });

  it('un 409 idempotency_conflict avisa del cambio y el siguiente intento usa clave nueva', async () => {
    confirm.mockRejectedValueOnce(new receipts.ReceiptApiError('Esa clave ya confirmó otra recepción.', 409, false, { code: 'idempotency_conflict' }));
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    expect(await screen.findByText(/cambió respecto al intento anterior/)).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    const [first, second] = confirm.mock.calls.map((c) => c[1]);
    expect(second).not.toBe(first);
  });

  it('tras un 4xx definitivo con el mismo contenido se reutiliza la misma clave', async () => {
    confirm.mockRejectedValueOnce(new receipts.ReceiptApiError('Sucursal no encontrada o sin acceso.', 404, false, { code: 'not_found' }));
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    expect(await screen.findByText('Sucursal no encontrada o sin acceso.')).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    expect(confirm.mock.calls[1][1]).toBe(confirm.mock.calls[0][1]);
  });

  it('un error 400 con línea ofrece ir a ese artículo', async () => {
    confirm.mockRejectedValueOnce(new receipts.ReceiptApiError('La cantidad es un número entero entre 1 y 100000.', 400, false, { code: 'invalid', line: 0 }));
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await u.click(await screen.findByRole('button', { name: 'Ir a ese artículo' }));
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
  });

  it('con más de 50 artículos no avanza y lo explica', async () => {
    receipts.stashDraft(
      { branch: 1, supplier: null, document_ref: '', notes: '' },
      Array.from({ length: 51 }, (_, i) => ({ ...receipts.newLine({ id: 100 + i, name: `Art ${i}`, is_serialized: false, requires_imei: false }), quantity: '1' })),
      SCOPE,
    );
    const u = await renderWizard();
    await next(u, /Siguiente: revisar/);
    expect(screen.getByText(/hasta 50 artículos por vez/)).toBeInTheDocument();
    expect(preview).not.toHaveBeenCalled();
  });
});

describe('paso 4 · Terminado', () => {
  it('un lote fallido ({id:null,status:error}) no cuenta como etiquetas listas; los buenos sí', async () => {
    confirm.mockImplementation(async (body) => resultFor(body, {
      label_error: true,
      label_batches: [{ id: 8, count: 3, status: 'open' }, { id: null, status: 'error' }],
    }));
    const u = await renderWizard();
    await toReview(u);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    expect(screen.getByRole('alert')).toHaveTextContent('El stock sí se registró');
    expect(screen.getByText(/Etiquetas listas para imprimir/)).toBeInTheDocument();
    expect(screen.queryByText(/\(2 lotes\)/)).not.toBeInTheDocument();
  });

  async function finish(u: ReturnType<typeof user>, p = PHONE) {
    await toReview(u, p);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
  }

  it('cuenta lo registrado, ofrece etiquetas listas y la ficha comercial', async () => {
    const u = await renderWizard();
    await finish(u);
    expect(screen.getByText(/Entraron 3 unidades por cantidad y 1 equipo identificado en Miraflores/)).toBeInTheDocument();
    expect(screen.getByText(/Etiquetas listas para imprimir/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Imprimir o ver etiquetas' })).toHaveAttribute('href', '/admin/inventory/labels?batch=8');
    expect(screen.getByRole('link', { name: `Completar ficha comercial de ${PHONE.name}` }))
      .toHaveAttribute('href', `/admin/products/publication?search=${encodeURIComponent(PHONE.name)}`);
    expect(screen.queryByText(/no se pudieron preparar las etiquetas/)).not.toBeInTheDocument();
  });

  it('si las etiquetas fallaron, avisa que el stock SÍ se registró y no ofrece imprimir', async () => {
    confirm.mockImplementation(async (body) => resultFor(body, { label_error: true, label_batches: [] }));
    const u = await renderWizard();
    await finish(u);
    const warning = screen.getByRole('alert');
    expect(warning).toHaveTextContent('El stock sí se registró, pero no se pudieron preparar las etiquetas.');
    expect(warning).toHaveTextContent(/No repitas la recepción/);
    expect(screen.queryByRole('link', { name: 'Imprimir o ver etiquetas' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Etiquetas listas para imprimir/)).not.toBeInTheDocument();
  });

  it('si el servidor responde que ya estaba registrada, lo dice', async () => {
    confirm.mockImplementation(async (body) => resultFor(body, { replayed: true }));
    const u = await renderWizard();
    await finish(u);
    expect(screen.getByText(/ya estaba registrada: no se repitió/)).toBeInTheDocument();
  });

  it('«Registrar otra recepción» conserva sucursal y proveedor y vacía las líneas', async () => {
    const u = await renderWizard();
    await u.selectOptions(screen.getByLabelText(/Sucursal que recibe/), '2');
    await u.selectOptions(await screen.findByLabelText(/Proveedor/), '3');
    await u.type(screen.getByLabelText(/Referencia del documento/), 'F-7');
    await finish(u, CABLE);
    await u.click(screen.getByRole('button', { name: 'Registrar otra recepción' }));
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    expect(screen.getByLabelText(/Sucursal que recibe/)).toHaveValue('2');
    expect(screen.getByLabelText(/Proveedor/)).toHaveValue('3');
    expect(screen.getByLabelText(/Referencia del documento/)).toHaveValue('');
  });

  it('recuerda la sucursal sólo en memoria de la página', async () => {
    const u = await renderWizard();
    await u.selectOptions(screen.getByLabelText(/Sucursal que recibe/), '2');
    await finish(u, CABLE);
    // A new visit to the page in the same session opens on the branch just used.
    await act(async () => { render(<ReceivingWizard access={ACCESS} scope={SCOPE} branches={BRANCHES} defaultBranch={1} />); });
    expect(screen.getByLabelText(/Sucursal que recibe/)).toHaveValue('2');
  });
});

describe('privacidad', () => {
  it('no escribe ni lee nada del almacenamiento del navegador durante todo el flujo', async () => {
    const u = await renderWizard({ costs: true });
    await u.type(screen.getByLabelText(/Observaciones/), 'caja golpeada');
    await toArticles(u);
    await addProduct(u, PHONE);
    await fillPhone(u, 'S-SECRETA', '356789012345678');
    await u.type(screen.getByLabelText(/Costo del equipo/), '2500');
    await next(u, /Siguiente: revisar/);
    await u.click(await screen.findByRole('checkbox'));
    await u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    expect(storage.set).not.toHaveBeenCalled();
    expect(storage.remove).not.toHaveBeenCalled();
    expect(storage.get).not.toHaveBeenCalled();
    expect(JSON.stringify(Object.entries(localStorage))).not.toMatch(/S-SECRETA|356789012345678/);
    expect(JSON.stringify(Object.entries(sessionStorage))).not.toMatch(/S-SECRETA|356789012345678/);
  });

  it('el costo no viaja si no se tiene costs.view', async () => {
    const u = await renderWizard({ costs: false });
    await toArticles(u);
    await addProduct(u, CABLE);
    await next(u, /Siguiente: revisar/);
    await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
    expect(JSON.stringify(preview.mock.calls[0][0])).not.toMatch(/cost/);
  });
});

// ───────────────────────────── revisión independiente (#204) ─────────────────────────────

const lost = () => new receipts.ReceiptApiError('No llegó la respuesta del servidor, así que no sabemos si la recepción se guardó.', 0, true);

async function toConfirm(u: ReturnType<typeof user>, p: admin.AdminProduct = CABLE) {
  await toReview(u, p);
  await u.click(await screen.findByRole('checkbox'));
}
const clickConfirm = (u: ReturnType<typeof user>) => u.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
const pop = (rcv: number, pos = rcv) => act(() => { window.dispatchEvent(new PopStateEvent('popstate', { state: { rcv, pos } })); });

describe('P2-A · una confirmación sin respuesta bloquea la edición', () => {
  it('muestra el panel, bloquea «Atrás» y reintenta con la MISMA clave sin duplicar la recepción', async () => {
    confirm.mockRejectedValueOnce(lost());
    confirm.mockImplementationOnce(async (body) => resultFor(body, { replayed: true }));
    const u = await renderWizard();
    await toConfirm(u);
    await clickConfirm(u);
    expect(await screen.findByText(/Puede que la recepción ya se haya registrado/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Atrás' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /Corregir/ })).not.toBeInTheDocument();
    const history = screen.getByRole('link', { name: /historial de movimientos/i });
    expect(history).toHaveAttribute('href', '/admin/inventory/movements');
    expect(history).toHaveAttribute('target', '_blank');
    expect(history.getAttribute('rel')).toMatch(/noopener/);
    await u.click(screen.getByRole('button', { name: 'Reintentar confirmación' }));
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    expect(screen.getByText(/ya estaba registrada: no se repitió/)).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(confirm.mock.calls[1][1]).toBe(confirm.mock.calls[0][1]);
    expect(confirm.mock.calls[1][0]).toEqual(confirm.mock.calls[0][0]);
  });

  it('el navegador tampoco puede retroceder mientras hay un intento sin respuesta', async () => {
    confirm.mockRejectedValueOnce(lost());
    const u = await renderWizard();
    await toConfirm(u);
    await clickConfirm(u);
    await screen.findByText(/Puede que la recepción ya se haya registrado/);
    pop(2);
    expect(screen.getByRole('heading', { name: 'Revisa antes de registrar' })).toBeInTheDocument();
  });

  it('«Revisé el historial…» desbloquea; la edición siguiente confirma con clave NUEVA', async () => {
    confirm.mockRejectedValueOnce(lost());
    const u = await renderWizard();
    await toConfirm(u);
    await clickConfirm(u);
    await u.click(await screen.findByRole('button', { name: /Revisé el historial y no se registró nada, quiero editar/ }));
    expect(screen.queryByText(/Puede que la recepción ya se haya registrado/)).not.toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    await u.click(within(screen.getByRole('article', { name: CABLE.name })).getByRole('button', { name: 'Una unidad más' }));
    await next(u, /Siguiente: revisar/);
    await u.click(await screen.findByRole('checkbox'));
    await clickConfirm(u);
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    expect(confirm.mock.calls[1][1]).not.toBe(confirm.mock.calls[0][1]);
    expect(confirm.mock.calls[1][0]).not.toEqual(confirm.mock.calls[0][0]);
  });

  it('un 200 sin forma de recepción es ambiguo: el panel, nunca un paso 4 vacío', async () => {
    confirm.mockRejectedValueOnce(new receipts.ReceiptApiError('No llegó la respuesta del servidor, así que no sabemos si la recepción se guardó.', 200, true));
    const u = await renderWizard();
    await toConfirm(u);
    await clickConfirm(u);
    expect(await screen.findByText(/Puede que la recepción ya se haya registrado/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Recepción registrada' })).not.toBeInTheDocument();
  });

  it('un error definitivo (4xx) no bloquea la edición', async () => {
    confirm.mockRejectedValueOnce(new receipts.ReceiptApiError('Sucursal no encontrada o sin acceso.', 404, false, { code: 'not_found' }));
    const u = await renderWizard();
    await toConfirm(u);
    await clickConfirm(u);
    await screen.findByText('Sucursal no encontrada o sin acceso.');
    expect(screen.getByRole('button', { name: 'Atrás' })).toBeEnabled();
    expect(screen.queryByText(/Puede que la recepción ya se haya registrado/)).not.toBeInTheDocument();
  });
});

describe('P2-A2 · tras un reintento refusado entero se desbloquea', () => {
  it('un 400 invalid_lines al reintentar prueba que no se guardó: se muestra la revisión y se puede corregir', async () => {
    confirm.mockRejectedValueOnce(lost());
    confirm.mockImplementationOnce(async (body) => {
      throw new receipts.ReceiptApiError('Hay líneas que no se pueden recibir. No se recibió nada.', 400, false, {
        code: 'invalid_lines', preview: { ...previewFor(body, [{ status: 'error', errors: ['El producto está desactivado.'] }]), ok: false },
      });
    });
    const u = await renderWizard();
    await toConfirm(u);
    await clickConfirm(u);
    await u.click(await screen.findByRole('button', { name: 'Reintentar confirmación' }));
    expect(await screen.findByText('El producto está desactivado.')).toBeInTheDocument();
    expect(screen.queryByText(/Puede que la recepción ya se haya registrado/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Atrás' })).toBeEnabled();
    expect(screen.getByRole('button', { name: `Corregir ${CABLE.name}` })).toBeEnabled();
  });
});

describe('P3 D1 · los avisos no se duplican en StrictMode', () => {
  it('un proveedor que ya no existe se avisa UNA vez aunque React doble-invoque', async () => {
    receipts.stashDraft({ branch: 1, supplier: 999, document_ref: '', notes: '' },
      [receipts.newLine({ id: 9, name: CABLE.name, is_serialized: false, requires_imei: false })], SCOPE);
    await act(async () => {
      render(<React.StrictMode><ReceivingWizard access={ACCESS} scope={SCOPE} branches={BRANCHES} defaultBranch={1} /></React.StrictMode>);
    });
    expect(await screen.findAllByText(/El proveedor de tu lista ya no está disponible/)).toHaveLength(1);
  });
});

describe('P2-B · historial por paso y aviso al salir', () => {
  beforeEach(() => { mockRouter.push.mockReset(); });

  async function withOutsideLink() {
    const u = user();
    await act(async () => {
      render(
        <>
          <a href="/admin/inventory">Inventario (menú)</a>
          <a href="#seccion">Mismo lugar</a>
          <ReceivingWizard access={ACCESS} scope={SCOPE} branches={BRANCHES} defaultBranch={1} />
        </>,
      );
    });
    return u;
  }

  it('cada paso es una entrada del historial y «atrás» del navegador vuelve al paso anterior sin perder las líneas', async () => {
    const u = await renderWizard();
    expect((window.history.state as { rcv: number }).rcv).toBe(1);
    await toArticles(u);
    expect((window.history.state as { rcv: number }).rcv).toBe(2);
    await addProduct(u, CABLE);
    pop(1);
    expect(screen.getByRole('heading', { name: '¿De dónde viene y adónde llega?' })).toBeInTheDocument();
    await toArticles(u);
    expect(screen.getByRole('article', { name: CABLE.name })).toBeInTheDocument();
  });

  it('un enlace del menú con líneas escritas pide confirmación; «Seguir aquí» se queda y «Salir» navega', async () => {
    const u = await withOutsideLink();
    await toArticles(u);
    await addProduct(u, CABLE);
    await u.click(screen.getByRole('link', { name: 'Inventario (menú)' }));
    const dialog = await screen.findByRole('alertdialog', { name: /Tienes una recepción sin confirmar/ });
    expect(dialog).toHaveTextContent('Si sales se pierde lo que escribiste.');
    expect(mockRouter.push).not.toHaveBeenCalled();
    await u.click(within(dialog).getByRole('button', { name: 'Seguir aquí' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByRole('article', { name: CABLE.name })).toBeInTheDocument();
    await u.click(screen.getByRole('link', { name: 'Inventario (menú)' }));
    await u.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Salir' }));
    expect(mockRouter.push).toHaveBeenCalledWith('/admin/inventory');
  });

  it('Escape cierra el aviso y el foco entra y vuelve', async () => {
    const u = await withOutsideLink();
    await toArticles(u);
    await addProduct(u, CABLE);
    await u.click(screen.getByRole('link', { name: 'Inventario (menú)' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByRole('button', { name: 'Seguir aquí' })).toHaveFocus();
    await u.keyboard('{Escape}');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('sin nada escrito el enlace navega sin preguntar; el mismo lugar nunca pregunta', async () => {
    const u = await withOutsideLink();
    const link = screen.getByRole('link', { name: 'Inventario (menú)' });
    const seen = jest.fn((e: Event) => e.preventDefault());
    link.addEventListener('click', seen);
    await u.click(link);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(seen).toHaveBeenCalled(); // reached the link: the guard did not swallow it
    await toArticles(u);
    await addProduct(u, CABLE);
    const same = screen.getByRole('link', { name: 'Mismo lugar' });
    same.addEventListener('click', (e) => e.preventDefault());
    await u.click(same);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('«crear artículo» conserva la lista por su cuenta: no pregunta', async () => {
    const u = await renderWizard({ manageProducts: true });
    await toArticles(u);
    await addProduct(u, CABLE);
    const link = screen.getByRole('link', { name: /No existe: crear artículo/ });
    link.addEventListener('click', (e) => e.preventDefault());
    await u.click(link);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('«atrás» más allá del primer paso con líneas pregunta; sin líneas deja salir', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, CABLE);
    pop(0);
    expect(await screen.findByRole('alertdialog')).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Seguir aquí' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByRole('article', { name: CABLE.name })).toBeInTheDocument();

    const back = jest.spyOn(window.history, 'back').mockImplementation(() => undefined);
    await u.click(screen.getByRole('button', { name: `Quitar ${CABLE.name} de la lista` }));
    pop(0);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(back).toHaveBeenCalled();
    back.mockRestore();
  });

  it('con una confirmación en vuelo el aviso es el fuerte', async () => {
    confirm.mockImplementation(() => new Promise(() => undefined));
    const u = await withOutsideLink();
    await toConfirm(u);
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar recepción' }));
    await u.click(screen.getByRole('link', { name: 'Inventario (menú)' }));
    expect(await screen.findByRole('alertdialog')).toHaveTextContent(/Puede que la recepción ya se haya registrado/);
  });

  it('con una confirmación sin respuesta el aviso es el fuerte aunque no haya líneas editables', async () => {
    confirm.mockRejectedValueOnce(lost());
    const u = await withOutsideLink();
    await toConfirm(u);
    await clickConfirm(u);
    await screen.findByText(/Puede que la recepción ya se haya registrado/);
    await u.click(screen.getByRole('link', { name: 'Inventario (menú)' }));
    expect(await screen.findByRole('alertdialog')).toHaveTextContent(/no sabrás si se guardó/);
  });

  it('terminada la recepción ya no se pregunta; «atrás» NO reabre el flujo y SÍ sale del asistente', async () => {
    const go = jest.spyOn(window.history, 'go').mockImplementation(() => undefined);
    const u = await withOutsideLink();
    await toConfirm(u);
    await clickConfirm(u);
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    const link = screen.getByRole('link', { name: 'Inventario (menú)' });
    link.addEventListener('click', (e) => e.preventDefault());
    await u.click(link);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    // The browser's «atrás» lands on the entry of step 2 (position 2 of the flow): leave the whole flow, in one jump.
    pop(2, 2);
    expect(screen.getByRole('heading', { name: 'Recepción registrada' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '¿Qué llegó?' })).not.toBeInTheDocument();
    expect(go).toHaveBeenCalledWith(-3);
    go.mockRestore();
  });

  it('tras terminar, repetir «atrás» no acumula entradas fantasma en el historial', async () => {
    const go = jest.spyOn(window.history, 'go').mockImplementation(() => undefined);
    const u = await renderWizard();
    await toConfirm(u);
    await clickConfirm(u);
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    const length = window.history.length;
    pop(2, 2); pop(1, 1); pop(0, 0);
    expect(window.history.length).toBe(length);
    expect(screen.getByRole('heading', { name: 'Recepción registrada' })).toBeInTheDocument();
    go.mockRestore();
  });

  it('«Registrar otra recepción» deja el historial como un flujo nuevo, sin pasos fantasma', async () => {
    const u = await renderWizard();
    await toConfirm(u);
    await clickConfirm(u);
    await screen.findByRole('heading', { name: 'Recepción registrada' });
    await u.click(screen.getByRole('button', { name: 'Registrar otra recepción' }));
    await waitFor(() => expect(window.history.state).toMatchObject({ rcv: 2, pos: 2 }));
    expect(screen.getByRole('heading', { name: '¿Qué llegó?' })).toBeInTheDocument();
    window.history.back(); // «atrás» del navegador: el paso 1 del flujo nuevo
    await waitFor(() => expect(screen.getByRole('heading', { name: '¿De dónde viene y adónde llega?' })).toBeInTheDocument());
    expect(window.history.state).toMatchObject({ rcv: 1, pos: 1 });
  });

  it('el cambio de empresa pasa por el mismo aviso: Salir continúa, Seguir aquí lo cancela', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, CABLE);
    const proceed = jest.fn();
    let blocked = false;
    act(() => { blocked = receipts.askBeforeLeaving(proceed); });
    expect(blocked).toBe(true);
    const dialog = await screen.findByRole('alertdialog');
    await u.click(within(dialog).getByRole('button', { name: 'Seguir aquí' }));
    expect(proceed).not.toHaveBeenCalled();
    expect(screen.getByRole('article', { name: CABLE.name })).toBeInTheDocument();
    act(() => { receipts.askBeforeLeaving(proceed); });
    await u.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Salir' }));
    expect(proceed).toHaveBeenCalledTimes(1);
  });

  it('el cambio de empresa con una confirmación sin respuesta avisa con el texto fuerte; sin nada que perder no pregunta', async () => {
    const u = await renderWizard();
    expect(receipts.askBeforeLeaving(jest.fn())).toBe(false);
    confirm.mockRejectedValueOnce(lost());
    await toConfirm(u);
    await clickConfirm(u);
    await screen.findByText(/Puede que la recepción ya se haya registrado/);
    act(() => { receipts.askBeforeLeaving(jest.fn()); });
    expect(await screen.findByRole('alertdialog')).toHaveTextContent(/no sabrás si se guardó/);
  });

  it('el «atrás» propio queda alineado con el historial', async () => {
    const u = await renderWizard();
    await toArticles(u);
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    expect(screen.getByRole('heading', { name: '¿De dónde viene y adónde llega?' })).toBeInTheDocument();
    await waitFor(() => expect((window.history.state as { rcv: number }).rcv).toBe(1));
  });

  it('si se abre directo en el paso 2 (borrador recuperado), «Atrás» llega al paso 1 y no pregunta por salir', async () => {
    receipts.stashDraft({ branch: 1, supplier: null, document_ref: '', notes: '' },
      [receipts.newLine({ id: 9, name: CABLE.name, is_serialized: false, requires_imei: false })], SCOPE);
    const u = await renderWizard();
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '¿De dónde viene y adónde llega?' })).toBeInTheDocument();
  });

  it('editar y volver «adelante» del navegador no deja confirmar una revisión vieja', async () => {
    const u = await renderWizard();
    await toConfirm(u);
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    await u.click(within(screen.getByRole('article', { name: CABLE.name })).getByRole('button', { name: 'Una unidad más' }));
    pop(3); // the browser's «adelante»
    expect(await screen.findByRole('button', { name: 'Revisar de nuevo' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirmar recepción' })).toBeDisabled();
  });
});

describe('P2-D · el borrador en memoria es de una persona y de una empresa', () => {
  it('avisa que recuperó la lista', async () => {
    receipts.stashDraft({ branch: 1, supplier: null, document_ref: '', notes: '' },
      [{ ...receipts.newLine({ id: 9, name: CABLE.name, is_serialized: false, requires_imei: false }), quantity: '2' }], SCOPE);
    await renderWizard();
    expect(await screen.findByText('Recuperamos tu lista de la recepción.')).toBeInTheDocument();
  });

  it('el borrador de otra persona o de otra empresa no se restaura', async () => {
    receipts.stashDraft({ branch: 1, supplier: null, document_ref: '', notes: '' },
      [receipts.newLine({ id: 9, name: CABLE.name, is_serialized: false, requires_imei: false })], { user: 99, company: 7 });
    await renderWizard();
    expect(screen.queryByText(/Recuperamos tu lista/)).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '¿De dónde viene y adónde llega?' })).toBeInTheDocument();
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
  });

  it('un proveedor del borrador que ya no está en la lista se quita a la vista', async () => {
    receipts.stashDraft({ branch: 1, supplier: 999, document_ref: '', notes: '' },
      [receipts.newLine({ id: 9, name: CABLE.name, is_serialized: false, requires_imei: false })], SCOPE);
    const u = await renderWizard();
    expect(await screen.findByText(/El proveedor de tu lista ya no está disponible/)).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    expect(screen.getByLabelText(/Proveedor/)).toHaveValue('');
  });

  it('una sucursal del borrador que no alcanzas se cambia a la vista', async () => {
    receipts.stashDraft({ branch: 77, supplier: null, document_ref: '', notes: '' },
      [receipts.newLine({ id: 9, name: CABLE.name, is_serialized: false, requires_imei: false })], SCOPE);
    await renderWizard();
    expect(await screen.findByText(/La sucursal de tu lista ya no está disponible/)).toBeInTheDocument();
  });

  it('«crear artículo» guarda la lista atada a esta persona y empresa', async () => {
    const u = await renderWizard({ manageProducts: true });
    await toArticles(u);
    await addProduct(u, CABLE);
    const link = screen.getByRole('link', { name: /No existe: crear artículo/ });
    link.addEventListener('click', (e) => e.preventDefault());
    await u.click(link);
    expect(receipts.takeStashedDraft({ user: 2, company: 7 })).toBeNull();
  });
});

describe('P2-H · todos los proveedores', () => {
  const rows = (from: number, n: number) => Array.from({ length: n }, (_, i) => ({ id: from + i, name: `Proveedor ${from + i}` }));

  it('con 60 proveedores se cargan todas las páginas y se puede elegir cualquiera', async () => {
    suppliersApi.mockImplementation(async ({ page = 1 }: { page?: number }) => ({
      count: 60, page, page_size: 25, results: page === 1 ? rows(1, 25) : page === 2 ? rows(26, 25) : page === 3 ? rows(51, 10) : [],
    }));
    const u = await renderWizard();
    await screen.findByRole('option', { name: 'Proveedor 60' });
    expect(screen.getAllByRole('option').filter((o) => /^Proveedor \d+$/.test(o.textContent ?? ''))).toHaveLength(60);
    await u.selectOptions(screen.getByLabelText(/Proveedor/), '60');
    expect(screen.getByLabelText(/Proveedor/)).toHaveValue('60');
    expect(suppliersApi).toHaveBeenCalledTimes(3);
    expect(screen.queryByText(/Se muestran los primeros/)).not.toBeInTheDocument();
  });

  it('si se llega al tope de páginas lo dice y propone seguir sin proveedor', async () => {
    suppliersApi.mockImplementation(async ({ page = 1 }: { page?: number }) => ({ count: 5000, page, page_size: 25, results: rows((page - 1) * 25 + 1, 25) }));
    await renderWizard();
    expect(await screen.findByText(/Se muestran los primeros 1000 de 5000 proveedores/)).toBeInTheDocument();
    expect(suppliersApi).toHaveBeenCalledTimes(40);
  });
});

describe('P3 · textos y filas', () => {
  it('E · el «Equipo N» del servidor se muestra con el número de la fila que ve la persona', async () => {
    preview.mockImplementation(async (body) => previewFor(body, [{
      status: 'error', errors: [], duplicates: ['Equipo 2: ese IMEI ya está registrado en otro equipo de la empresa.'],
      unit_issues: [{ unit: 1, field: 'imei', kind: 'duplicate', message: 'Equipo 2: ese IMEI ya está registrado en otro equipo de la empresa.' }],
    }]));
    const u = await renderWizard();
    await toArticles(u);
    await addProduct(u, PHONE);
    await fillPhone(u, 'S-1', '111111111111111');
    await u.click(screen.getByRole('button', { name: 'Agregar otro equipo' })); // row 2: left EMPTY (not sent)
    await u.click(screen.getByRole('button', { name: 'Agregar otro equipo' })); // row 3
    const card = screen.getByRole('article', { name: PHONE.name });
    const serials = within(card).getAllByLabelText(/Número de serie/);
    await u.type(serials[2], 'S-3');
    await u.type(within(card).getAllByLabelText(/^IMEI \*/)[2], '333333333333333');
    await next(u, /Siguiente: revisar/);
    // The server's «Equipo 2» is the 2nd device SENT = the 3rd row on screen.
    expect(await screen.findAllByText(/Equipo 3: ese IMEI ya está registrado/)).not.toHaveLength(0);
    expect(screen.queryByText(/Equipo 2: ese IMEI ya está registrado/)).not.toBeInTheDocument();
  });

  it('F · una fila con sólo costo o estado cambiado avisa que no se enviará', async () => {
    const u = await renderWizard({ costs: true });
    await toArticles(u);
    await addProduct(u, PHONE);
    const card = screen.getByRole('article', { name: PHONE.name });
    expect(within(card).queryByText(/no tiene serie ni IMEI y no se enviará/)).not.toBeInTheDocument();
    await u.type(within(card).getByLabelText(/Costo del equipo/), '2500');
    expect(await within(card).findByText('Esta fila no tiene serie ni IMEI y no se enviará.')).toBeInTheDocument();
    await u.type(within(card).getByLabelText(/Número de serie/), 'S1');
    expect(within(card).queryByText(/no tiene serie ni IMEI y no se enviará/)).not.toBeInTheDocument();
  });
});

describe('P2-I y J · permisos en el resultado', () => {
  async function finishTwo(access: Partial<ReceivingAccess> = {}, over: Partial<receipts.ReceiptResult> = {}) {
    confirm.mockImplementation(async (body) => resultFor(body, {
      label_batches: [{ id: 8, count: 3, status: 'open' }, { id: 9, count: 2, status: 'open' }], ...over,
    }));
    const u = await renderWizard(access);
    await toArticles(u);
    await addProduct(u, CABLE);
    await addProduct(u, PHONE);
    await fillPhone(u);
    await next(u, /Siguiente: revisar/);
    await u.click(await screen.findByRole('checkbox'));
    await clickConfirm(u);
    await screen.findByRole('heading', { name: 'Recepción registrada' });
  }

  it('J · con varios artículos nombra a todos, enlaza el conjunto de lotes y la ficha de cada uno', async () => {
    await finishTwo();
    const next = screen.getByRole('region', { name: 'Siguiente paso' });
    expect(next).toHaveTextContent(CABLE.name);
    expect(next).toHaveTextContent(PHONE.name);
    expect(within(next).getByText(/2 lotes/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: `Completar ficha comercial de ${CABLE.name}` })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: `Completar ficha comercial de ${PHONE.name}` })).toBeInTheDocument();
  });

  it('J · sin permiso de etiquetas no se ofrecen enlaces a etiquetas y se explica', async () => {
    await finishTwo({ labels: false });
    expect(screen.queryByRole('link', { name: /Etiquetas|etiquetas/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Pide a alguien con permiso de etiquetas/)).toBeInTheDocument();
  });

  it('J · sin products.view no se ofrece ninguna ficha comercial', async () => {
    await finishTwo({ viewProducts: false });
    expect(screen.queryByRole('link', { name: /ficha comercial/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Publicación' })).not.toBeInTheDocument();
  });
});

describe('calidad de pruebas', () => {
  it('(5) editar tras revisar invalida la revisión Y la casilla: sin la comprobación no se confirmaría lo viejo', async () => {
    const u = await renderWizard();
    await toConfirm(u);
    expect(screen.getByRole('checkbox')).toBeChecked();
    await u.click(screen.getByRole('button', { name: 'Atrás' }));
    await u.click(within(screen.getByRole('article', { name: CABLE.name })).getByRole('button', { name: 'Una unidad más' }));
    pop(3);
    expect(await screen.findByRole('button', { name: 'Revisar de nuevo' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
    await u.click(screen.getByRole('button', { name: 'Revisar de nuevo' }));
    expect(await screen.findByRole('checkbox')).not.toBeChecked();
    expect(preview.mock.calls[1][0].lines[0].quantity).toBe(2);
  });

  it('(6) un costo tecleado nunca viaja si la persona deja de tener costs.view', async () => {
    const u = userEvent.setup();
    let view: ReturnType<typeof render>;
    await act(async () => { view = render(<ReceivingWizard access={{ ...ACCESS, costs: true }} scope={SCOPE} branches={BRANCHES} defaultBranch={1} />); });
    await toArticles(u);
    await addProduct(u, CABLE);
    await u.type(screen.getByLabelText(/Costo por unidad/), '12.50');
    await act(async () => { view!.rerender(<ReceivingWizard access={{ ...ACCESS, costs: false }} scope={SCOPE} branches={BRANCHES} defaultBranch={1} />); });
    expect(screen.queryByLabelText(/Costo por unidad/)).not.toBeInTheDocument();
    await next(u, /Siguiente: revisar/);
    await screen.findByRole('heading', { name: 'Revisa antes de registrar' });
    expect(JSON.stringify(preview.mock.calls[0][0])).not.toMatch(/cost/);
  });
});
