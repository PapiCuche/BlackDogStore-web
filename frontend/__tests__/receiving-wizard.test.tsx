/**
 * INV-OPS-01 — asistente «Recibir mercadería»: pasos, validación, revisión,
 * confirmación idempotente y pantalla final. Sin red: el contrato de recibos se
 * simula; el buscador de productos y los proveedores, también.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ReceivingWizard, type ReceivingAccess } from '@/app/admin/components/ReceivingWizard';
import * as receipts from '@/app/admin/lib/inventory-receipts';
import { forgetBranch, rememberBranch } from '@/app/admin/lib/last-branch';
import * as internalApi from '@/app/admin/lib/internal-api';
import * as admin from '@/app/lib/admin';

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
const ACCESS: ReceivingAccess = { costs: false, manageProducts: false, viewProducts: true };

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
    render(<ReceivingWizard access={{ ...ACCESS, ...access }} branches={BRANCHES} defaultBranch={1} {...props} />);
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
    const held = receipts.takeStashedDraft();
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
    );
    detail.mockResolvedValue(product(5, PHONE.name, { is_serialized: true, requires_imei: true }));
    await renderWizard({}, { initialProductId: 5 });
    expect(await screen.findByRole('article', { name: PHONE.name })).toBeInTheDocument();
    expect(screen.getByRole('article', { name: CABLE.name })).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: CABLE.name })).getByLabelText('Cantidad')).toHaveValue('4');
    expect(detail).toHaveBeenCalledWith(5);
    // …and the draft is given back only once.
    expect(receipts.takeStashedDraft()).toBeNull();
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

    // Changing the content: back, edit, review again → another attempt, another key.
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
    await act(async () => { render(<ReceivingWizard access={ACCESS} branches={BRANCHES} defaultBranch={1} />); });
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
