/**
 * INV-RECEIVE-PRICE-03 — el precio de venta de un seminuevo es suyo, y sólo lo fija quien gestiona productos.
 *
 *   · «Equipos seminuevos»: datos del equipo (`inventory.adjust`), precio (`products.manage` + `inventory.adjust`) y
 *     costo (`costs.view`) son tres autoridades; ningún campo editable aparece sin la suya y la pantalla dice por qué;
 *     el precio pide confirmación; un equipo vendido conserva el suyo; sin precio no hay «precio del producto».
 *   · La recepción separa «Datos del equipo» e «Información comercial» y avisa del precio pendiente.
 *   · El paso 4 dice, por línea, lo que falta y lleva a la pantalla que lo resuelve; nada publica.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { AvailabilityPanel } from '@/app/admin/components/AvailabilityPanel';
import { ReceivingFollowUp, onlineText } from '@/app/admin/components/ReceivingFollowUp';
import { ReceivingLinesEditor } from '@/app/admin/components/ReceivingLinesEditor';
import { UsedUnitsPanel, hasUsablePrice } from '@/app/admin/components/UsedUnitsPanel';
import { newLine, PENDING_PRICE_TEXT, type ReceiptFollowUp, type ReceiptResult } from '@/app/admin/lib/inventory-receipts';
import { usedUnitsAccess, usedUnitsHref, usedUnitsLinkFilters } from '@/app/admin/lib/used-units-access';
import { fetchWithAuth } from '@/app/lib/auth';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const reply = (body: unknown, status = 200) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) }) as Response;

type Call = { method: string; url: string; body: unknown };
let calls: Call[];
let patchReply: { status: number; body: unknown } | null;

const UNIT = {
  id: 7, product_id: 52, product_name: 'Equipo X seminuevo', branch_id: 1, branch_name: 'Central',
  serial_number: 'SER123', imei: '356789012345678', imei2: null, condition: 'used', condition_label: 'Usado',
  status: 'available', status_label: 'Disponible', received_at: '2026-10-01T10:00:00Z', sold_at: null, order_id: null,
  notes: '', public_id: 'abc', battery_health: 90, cosmetic_grade: 5, cosmetic_grade_label: 'Excelente', public_notes: '',
  sale_price: null as string | null, condition_line: 'used', photos: [] as unknown[], cost: '1200.00' as string | null | undefined,
  web: { offered: false, reasons: ['no_price'] },
};
let units: typeof UNIT[];

beforeEach(() => {
  calls = [];
  patchReply = null;
  units = [UNIT];
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    calls.push({ method, url, body: init.body ? JSON.parse(String(init.body)) : undefined });
    if (method === 'PATCH') {
      if (patchReply) return reply(patchReply.body, patchReply.status);
      return reply({ ...UNIT, ...(calls[calls.length - 1].body as object) });
    }
    if (url.includes('/admin/catalog/grades/')) return reply([{ id: 5, code: 'A', label: 'Excelente', description: '', sort_order: 0, is_active: true }]);
    if (url.includes('/admin/inventory/units/')) return reply({ results: units, count: units.length, page: 1, page_size: 25, statuses: [], conditions: [] });
    return reply({}, 404);
  });
});

const writes = () => calls.filter((c) => c.method !== 'GET');

async function open(props: Partial<React.ComponentProps<typeof UsedUnitsPanel>> = {}) {
  render(<UsedUnitsPanel branch="all" canEdit canSeeCost={false} {...props} />);
  fireEvent.click(await screen.findByRole('button', { name: /^(Editar|Ver) SER123$/ }));
  return screen.getByRole('dialog', { name: 'Equipo X seminuevo' });
}

describe('las tres autoridades de la pantalla', () => {
  const access = (...caps: string[]) => ({ can: (c: string) => caps.includes(c) });

  it('ajustar inventario no da el precio, gestionar productos solo no da la ficha, el costo va aparte', () => {
    expect(usedUnitsAccess(access('inventory.adjust'))).toEqual({ canEdit: true, canManagePrice: false, canSeeCost: false });
    expect(usedUnitsAccess(access('products.manage'))).toEqual({ canEdit: false, canManagePrice: false, canSeeCost: false });
    expect(usedUnitsAccess(access('inventory.adjust', 'products.manage'))).toEqual({ canEdit: true, canManagePrice: true, canSeeCost: false });
    expect(usedUnitsAccess(access('costs.view'))).toEqual({ canEdit: false, canManagePrice: false, canSeeCost: true });
  });

  it('el enlace de la recepción preselecciona artículo y «sin precio»; lo demás se ignora', () => {
    expect(usedUnitsLinkFilters('?product=52&precio=sin')).toEqual({ product: '52', priced: 'no' });
    expect(usedUnitsLinkFilters('?product=abc&precio=gratis')).toEqual({ product: '', priced: '' });
    expect(usedUnitsHref(52, true)).toBe('/admin/inventory/used?product=52&precio=sin');
    expect(usedUnitsHref(52)).toBe('/admin/inventory/used?product=52');
  });

  it('un precio cero no es un precio', () => {
    expect(hasUsablePrice('0.00')).toBe(false);
    expect(hasUsablePrice(null)).toBe(false);
    expect(hasUsablePrice('1.00')).toBe(true);
  });
});

describe('Equipos seminuevos · precio de venta', () => {
  it('con ajustar inventario y sin gestionar productos: el precio se lee, no se edita, y se dice por qué', async () => {
    const dialog = await open({ canEdit: true, canManagePrice: false });
    expect(within(dialog).queryByLabelText(/Precio de venta \(S\/\)/)).toBeNull();
    expect(within(dialog).getByText('Sólo quien gestiona productos puede fijar o cambiar el precio de venta.')).toBeInTheDocument();
    expect(within(dialog).getByRole('note')).toHaveTextContent(PENDING_PRICE_TEXT);
    // Lo que sí puede: la ficha, sin que viaje un precio que el servidor rechazaría (403).
    fireEvent.change(within(dialog).getByLabelText('Batería (%)'), { target: { value: '85' } });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    expect(writes()).toHaveLength(1);
    expect(writes()[0].body).toEqual({ battery_health: 85 });
    expect(writes()[0].body).not.toHaveProperty('sale_price');
  });

  it('sin cambios no se escribe nada', async () => {
    const dialog = await open({ canEdit: true, canManagePrice: true });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    expect(writes()).toEqual([]);
  });

  it('un precio cero, negativo o con tres decimales no se envía', async () => {
    const dialog = await open({ canManagePrice: true });
    for (const bad of ['0', '0.00', '-5', '1899.999', 'abc']) {
      fireEvent.change(within(dialog).getByLabelText(/Precio de venta \(S\/\)/), { target: { value: bad } });
      await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
      expect(within(dialog).getByRole('alert')).toHaveTextContent('mayor que cero');
    }
    expect(writes()).toEqual([]);
  });

  it('cambiar un precio pide confirmación con el importe anterior y el nuevo; «Revisar» no escribe', async () => {
    units = [{ ...UNIT, sale_price: '2100.00', web: { offered: true, reasons: [] } }];
    const dialog = await open({ canManagePrice: true });
    fireEvent.change(within(dialog).getByLabelText(/Precio de venta \(S\/\)/), { target: { value: '2300' } });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    const confirm = within(dialog).getByRole('alertdialog', { name: 'Confirmar el precio de venta' });
    expect(confirm).toHaveTextContent(/cambiar el precio de venta de S\/ 2,100\.00 a S\/ 2,300\.00/);
    expect(within(confirm).getByRole('button', { name: 'Sí, guardar este precio' })).toHaveFocus();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Revisar' }));
    expect(writes()).toEqual([]);
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Sí, guardar este precio' })); });
    expect(writes()).toEqual([{ method: 'PATCH', url: expect.stringContaining('/inventory/units/7/'), body: { sale_price: '2300' } }]);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Equipo guardado.'));
  });

  it('quitar el precio también se confirma y dice que deja de venderse', async () => {
    units = [{ ...UNIT, sale_price: '2100.00' }];
    const dialog = await open({ canManagePrice: true });
    fireEvent.change(within(dialog).getByLabelText(/Precio de venta \(S\/\)/), { target: { value: '' } });
    expect(within(dialog).getByRole('note')).toHaveTextContent(PENDING_PRICE_TEXT);
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    expect(within(dialog).getByRole('alertdialog')).toHaveTextContent(/dejará de venderse en caja y de ofrecerse en línea/);
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Sí, quitar el precio' })); });
    expect(writes()[0].body).toEqual({ sale_price: null });
  });

  it('un equipo vendido conserva su precio: no hay campo, y se explica', async () => {
    units = [{ ...UNIT, status: 'sold', status_label: 'Vendido', sale_price: '2100.00' }];
    const dialog = await open({ canManagePrice: true });
    expect(within(dialog).queryByLabelText(/Precio de venta \(S\/\)/)).toBeNull();
    expect(within(dialog).getByText(/ya se vendió: su precio es el de esa venta/)).toBeInTheDocument();
    expect(within(dialog).queryByRole('note')).toBeNull();
  });

  it('el rechazo del servidor se muestra y no cierra la ficha', async () => {
    patchReply = { status: 400, body: { detail: 'El equipo SER123 ya se vendió: su precio no se puede cambiar.', field: 'sale_price' } };
    const dialog = await open({ canManagePrice: true });
    fireEvent.change(within(dialog).getByLabelText(/Precio de venta \(S\/\)/), { target: { value: '1999' } });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar' })); });
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Sí, guardar este precio' })); });
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('ya se vendió');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('costo y precio son dos datos distintos; el costo sólo con costs.view', async () => {
    units = [{ ...UNIT, sale_price: '2100.00' }];
    let dialog = await open({ canManagePrice: true, canSeeCost: true });
    expect(within(dialog).getByText('Costo de adquisición (interno)')).toBeInTheDocument();
    expect(within(dialog).getByText('S/ 1,200.00')).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Precio de venta \(S\/\)/)).toHaveValue('2100.00');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancelar' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    document.body.innerHTML = '';
    units = [{ ...UNIT, sale_price: '2100.00', cost: undefined }];
    dialog = await open({ canManagePrice: true, canSeeCost: false });
    expect(within(dialog).queryByText(/Costo de adquisición/)).toBeNull();
    expect(within(dialog).queryByText('S/ 1,200.00')).toBeNull();
  });

  it('la tabla dice «Sin precio (pendiente)» también para un cero, y si la tienda lo ofrece', async () => {
    units = [
      { ...UNIT, id: 7, serial_number: 'SER123', sale_price: '0.00' },
      { ...UNIT, id: 8, serial_number: 'SER124', sale_price: '1999.00', web: { offered: true, reasons: [] } },
    ];
    render(<UsedUnitsPanel branch="all" canEdit canSeeCost={false} />);
    const first = (await screen.findByText('SER123')).closest('tr') as HTMLElement;
    expect(within(first).getByText('Sin precio (pendiente)')).toBeInTheDocument();
    expect(within(first).getByText(/No · no tiene precio de venta/)).toBeInTheDocument();
    const second = screen.getByText('SER124').closest('tr') as HTMLElement;
    expect(within(second).getByText('S/ 1,999.00')).toBeInTheDocument();
    expect(within(second).getByText('Se ofrece')).toBeInTheDocument();
    expect(screen.queryByText(/Precio del producto/)).toBeNull();
    expect(screen.getByText(/puede ver los precios, no cambiarlos/)).toBeInTheDocument();
  });

  it('el filtro «Sin precio» pregunta al servidor; un enlace llega con artículo y filtro puestos', async () => {
    render(<UsedUnitsPanel branch="all" canEdit canSeeCost={false} initialProduct="52" initialPriced="no" />);
    await screen.findByText('SER123');
    const first = calls.find((c) => c.url.includes('/inventory/units/'))!.url;
    expect(first).toContain('priced=no');
    expect(first).toContain('product=52');
    expect(screen.getByText(/Sólo los equipos de «Equipo X seminuevo»/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Precio de venta'), { target: { value: 'yes' } });
    await waitFor(() => expect(calls[calls.length - 1].url).toContain('priced=yes'));
    fireEvent.click(screen.getByRole('button', { name: 'Ver todos' }));
    await waitFor(() => expect(calls[calls.length - 1].url).not.toContain('product='));
  });
});

describe('la recepción: datos del equipo e información comercial', () => {
  const USED_PRODUCT = { id: 52, name: 'iPhone 13 128 GB Azul Seminuevo', is_serialized: true, requires_imei: true, condition_line: 'used' as const };
  const NEW_PRODUCT = { id: 53, name: 'iPhone 16 128 GB Negro', is_serialized: true, requires_imei: true, condition_line: 'new' as const };

  function editor(product: typeof USED_PRODUCT | typeof NEW_PRODUCT, { canManageProducts = false, canCosts = false, price = '' } = {}) {
    const line = newLine(product);
    line.units = line.units.map((u) => ({ ...u, condition: product.condition_line === 'used' ? 'used' : 'new', price }));
    render(
      <ReceivingLinesEditor
        lines={[line]} onLines={() => undefined} canCosts={canCosts} canManageProducts={canManageProducts}
        problems={null} focus={null} onCreateArticle={() => undefined}
      />,
    );
    return screen.getByRole('article', { name: product.name });
  }

  it('sin gestionar productos: dos grupos, ningún campo de precio y el aviso de precio pendiente', () => {
    const card = editor(USED_PRODUCT);
    const data = within(card).getByRole('group', { name: 'Datos del equipo' });
    const commercial = within(card).getByRole('group', { name: 'Información comercial' });
    expect(within(data).getByLabelText(/Número de serie/)).toBeInTheDocument();
    expect(within(data).getByLabelText(/Batería medida/)).toBeInTheDocument();
    expect(within(data).getByText(/Modelo y variante:/)).toHaveTextContent('iPhone 13 128 GB Azul Seminuevo');
    expect(within(commercial).queryByLabelText(/Precio de venta/)).toBeNull();
    expect(within(commercial).queryByLabelText(/Costo del equipo/)).toBeNull();
    expect(within(commercial).getByRole('note')).toHaveTextContent(PENDING_PRICE_TEXT);
    expect(within(commercial).getByText(/Tu usuario no puede fijar precios de venta/)).toBeInTheDocument();
    expect(within(commercial).getByText(/Poner un precio no publica nada/)).toBeInTheDocument();
  });

  it('con gestionar productos y sin costos: el precio está en «Información comercial» y el costo no', () => {
    const card = editor(USED_PRODUCT, { canManageProducts: true });
    const commercial = within(card).getByRole('group', { name: 'Información comercial' });
    expect(within(commercial).getByLabelText(/Precio de venta del equipo/)).toBeInTheDocument();
    expect(within(commercial).queryByLabelText(/Costo del equipo/)).toBeNull();
    expect(within(commercial).getByText(/El costo de adquisición lo registra quien tiene permiso para ver costos/)).toBeInTheDocument();
    expect(within(commercial).getByRole('note')).toHaveTextContent(PENDING_PRICE_TEXT);
  });

  it('con precio escrito el aviso desaparece; costo y precio son campos distintos', () => {
    const card = editor(USED_PRODUCT, { canManageProducts: true, canCosts: true, price: '1899' });
    const commercial = within(card).getByRole('group', { name: 'Información comercial' });
    expect(within(commercial).getByLabelText(/Costo del equipo: lo que pagó la tienda/)).toBeInTheDocument();
    expect(within(commercial).getByLabelText(/Precio de venta del equipo/)).toHaveValue('1899');
    expect(within(commercial).queryByRole('note')).toBeNull();
  });

  it('un equipo nuevo no tiene precio propio ni aviso: se vende al precio de su producto', () => {
    const card = editor(NEW_PRODUCT, { canManageProducts: true });
    expect(within(card).queryByRole('group', { name: 'Información comercial' })).toBeNull();
    expect(within(card).queryByLabelText(/Precio de venta del equipo/)).toBeNull();
    expect(within(card).queryByText(PENDING_PRICE_TEXT)).toBeNull();
  });
});

describe('el paso 4: seguimiento por artículo', () => {
  const info = (extra: Partial<ReceiptFollowUp> = {}): ReceiptFollowUp => ({
    received: 3, priced_by: 'unit', units_priced: 2, units_unpriced: 1, units_without_battery: 1, units_without_grade: 0,
    units_without_photos: 3, storage_branch: { id: 1, name: 'Miraflores' }, dispatch_branch: { id: 1, name: 'Miraflores' },
    in_dispatch_branch: true, state: 'ready', problems: [], model: { id: 9, name: 'iPhone 13', published: true },
    placement: 'model_page', requires_image: false, units_offered_online: 0, online: 'blocked', blocked_reasons: ['not_published'],
    ...extra,
  });
  const line = (follow: ReceiptFollowUp | null = info()): ReceiptResult['lines'][number] => ({
    index: 0, kind: 'units', product: { id: 52, name: 'iPhone 13 128 GB Azul Seminuevo' }, received: 3, movement_id: 4, units: 3,
    units_unpriced: 1, follow_up: follow, label_batch: null, label_error: false,
  });

  it('dice lo recibido, los precios pendientes, lo que falta, el despacho, la publicación y el estado en línea', () => {
    render(<ReceivingFollowUp line={line()} canManagePrices canViewProducts />);
    const card = screen.getByRole('article', { name: 'Seguimiento de iPhone 13 128 GB Azul Seminuevo' });
    expect(card).toHaveTextContent('Recibido: 3 equipos identificados en Miraflores.');
    expect(card).toHaveTextContent('Precio de venta: 2 equipos con precio y 1 pendiente. Un equipo pendiente no se vende en caja ni se ofrece en línea');
    expect(card).toHaveTextContent('1 equipo sin batería medida, 3 equipos sin fotos propias');
    expect(card).toHaveTextContent('Se despacha en línea desde Miraflores, la misma sucursal donde quedó.');
    expect(card).toHaveTextContent('Publicación: Preparado para publicar');
    expect(card).toHaveTextContent('La tienda en línea no lo ofrece todavía: no está publicado: hoy es stock interno.');
    expect(within(card).getByRole('link', { name: 'Fijar el precio de 1 equipo' })).toHaveAttribute('href', '/admin/inventory/used?product=52&precio=sin');
    expect(within(card).getByRole('link', { name: 'Completar datos y fotos' })).toHaveAttribute('href', '/admin/inventory/used?product=52');
    expect(within(card).getByRole('link', { name: 'Revisar la publicación' })).toHaveAttribute('href', expect.stringContaining('/admin/products/publication?search='));
    expect(card).toHaveTextContent('Poner un precio no publica nada');
  });

  it('sin gestionar productos no hay enlace para fijar precios: se dice a quién pedirlo', () => {
    render(<ReceivingFollowUp line={line()} canManagePrices={false} canViewProducts={false} />);
    const card = screen.getByRole('article');
    expect(within(card).queryByRole('link', { name: /Fijar el precio/ })).toBeNull();
    expect(within(card).queryByRole('link', { name: 'Revisar la publicación' })).toBeNull();
    expect(card).toHaveTextContent('pide a quien gestiona productos');
  });

  it('otra sucursal, sin despacho o publicado y ofrecido: cada caso con sus palabras', () => {
    const { rerender } = render(<ReceivingFollowUp line={line(info({ in_dispatch_branch: false, dispatch_branch: { id: 2, name: 'Centro' }, blocked_reasons: ['not_in_dispatch_branch'] }))} canManagePrices canViewProducts />);
    expect(screen.getByRole('article')).toHaveTextContent('La tienda en línea despacha desde Centro: lo recibido en Miraflores no se ofrece en línea hasta que esté allí.');
    rerender(<ReceivingFollowUp line={line(info({ dispatch_branch: null, in_dispatch_branch: false, blocked_reasons: ['no_dispatch_branch'] }))} canManagePrices canViewProducts />);
    expect(screen.getByRole('article')).toHaveTextContent('La empresa no tiene una sucursal de despacho definida');
    rerender(<ReceivingFollowUp line={line(info({ state: 'published', units_priced: 3, units_unpriced: 0, units_offered_online: 3, online: 'offered', blocked_reasons: [] }))} canManagePrices canViewProducts />);
    const card = screen.getByRole('article');
    expect(card).toHaveTextContent('La tienda en línea ofrece ahora 3 equipos de esta línea.');
    expect(card).toHaveTextContent('Precio de venta: los 3 equipos tienen su precio.');
    expect(within(card).queryByRole('link', { name: /Fijar el precio/ })).toBeNull();
    expect(within(card).queryByRole('link', { name: 'Revisar la publicación' })).toBeNull();
  });

  it('una línea por cantidad sigue a su producto y no habla de equipos sin precio', () => {
    expect(onlineText(info({ priced_by: 'product', units_offered_online: null, online: 'offered', blocked_reasons: [] }))).toBe('La tienda en línea lo ofrece ahora.');
    render(<ReceivingFollowUp line={{ ...line(info({ priced_by: 'product', units_unpriced: 0, units_priced: 0 })), kind: 'quantity' }} canManagePrices canViewProducts />);
    expect(screen.getByRole('article')).not.toHaveTextContent(/Precio de venta/);
    expect(screen.queryByRole('link', { name: 'Completar datos y fotos' })).toBeNull();
  });

  it('sin seguimiento del servidor no se dibuja nada', () => {
    const { container } = render(<ReceivingFollowUp line={line(null)} canManagePrices canViewProducts />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('ninguna pantalla muestra el precio del producto como precio de un seminuevo', () => {
  it('Consultar disponibilidad: un seminuevo sin precio dice «Sin precio», no el del producto', async () => {
    const counts = { available: 1, reserved_web: 0, reserved_staff: 0, sold: 0, written_off: 0 };
    const used = {
      id: 11, name: 'iPhone 15 seminuevo', barcode: '', model: { id: 6, name: 'iPhone 15' }, category: null, line: 'used',
      line_label: 'Seminuevo', options: [], is_serialized: true, price: '1.00', unit_price_range: null, sale_mode: 'stock',
      sells_on_order: false, is_published_online: true, supplier_offers: [],
      stock: { by_branch: [{ branch_id: 1, branch_name: 'Centro', ...counts }], total: counts },
    };
    const unit = (id: number, price: string | null) => ({
      id, branch_id: 1, branch_name: 'Centro', status: 'available', status_label: 'Disponible', hold_kind: null, hold_until: null,
      imei_last4: `000${id}`, serial_last4: '', condition: 'used', condition_label: 'Usado', battery_health: 90,
      cosmetic_grade_label: '', public_notes: '', sale_price: price, received_at: '2026-10-01T10:00:00Z', sold_at: null,
    });
    send.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('units=1')) return reply({ product: used, count: 2, truncated: false, results: [unit(1, null), unit(2, '2400.00')] });
      return reply({ count: 1, page: 1, page_size: 25, can_see_suppliers: false, scope: { branch: null, branches: [{ id: 1, name: 'Centro' }], is_aggregate: true }, results: [used], facets: { categories: [], models: [], attributes: [] } });
    });
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    render(<AvailabilityPanel pollMs={60_000} />);
    const row = await screen.findByTestId('availability-row-11');
    fireEvent.click(within(row).getByRole('button', { name: 'Ver equipos' }));
    const drawer = await screen.findByLabelText('Equipos de iPhone 15 seminuevo');
    const first = (await within(drawer).findByText('IMEI ••••0001')).closest('tr') as HTMLElement;
    expect(within(first).getByText('Sin precio')).toBeInTheDocument();
    expect(within(drawer).queryByText('S/ 1.00')).toBeNull();
    const second = within(drawer).getByText('IMEI ••••0002').closest('tr') as HTMLElement;
    expect(within(second).getByText(/2[,.]?400\.00/)).toBeInTheDocument();
  });
});
