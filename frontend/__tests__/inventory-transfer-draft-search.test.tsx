/**
 * Decisión D (propietario) — búsqueda en el borrador de una transferencia.
 *
 * «Agregar búsqueda al borrador de transferencias respetando el comportamiento
 * existente. No alterar reglas de stock ni permisos. Preservar cantidades y
 * selecciones durante los filtros.»
 *
 * Se monta la página de verdad, con el control de acceso y el armazón
 * sustituidos, y se mira lo único que importa al final: qué líneas llegan a
 * `setTransferItems`. Filtrar es mirar; nunca decide qué se envía.
 */
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import TransferDetailPage from '@/app/admin/inventory/transfers/[id]/page';
import * as admin from '@/app/lib/admin';
import * as inventory from '@/app/lib/inventory';
import type { AdminProduct } from '@/app/lib/admin';
import type { StockTransfer } from '@/app/lib/inventory';

jest.mock('next/navigation', () => ({ useParams: () => ({ id: '4' }) }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ fetchAdminProducts: jest.fn() }));
jest.mock('@/app/lib/inventory', () => ({
  ...jest.requireActual('@/app/lib/inventory'),
  fetchTransfer: jest.fn(),
  setTransferItems: jest.fn(),
  dispatchTransfer: jest.fn(),
  receiveTransfer: jest.fn(),
  cancelTransfer: jest.fn(),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

let capabilities: string[] = ['inventory.view', 'inventory.adjust'];
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

const adm = admin as jest.Mocked<typeof admin>;
const inv = inventory as jest.Mocked<typeof inventory>;

function product(id: number, name: string, code = '', over: Partial<AdminProduct> = {}): AdminProduct {
  return {
    id, name, slug: `p-${id}`, description: '', price: '10.00', inventory: 9, image_url: '',
    category_id: null, category_name: null, is_active: true, is_published_online: true,
    is_serialized: false, primary_code: code, created_at: '', updated_at: '', ...over,
  };
}

const CATALOGUE = [
  product(1, 'Cable USB-C 1 m', 'BDS-0001'),
  product(2, 'Funda MagSafe iPhone 15', 'BDS-0042'),
  product(3, 'Cámara de seguridad Wi-Fi', 'CAM-77'),
  product(4, 'Cargador 20W', 'BDS-0100'),
];

function transfer(over: Partial<StockTransfer> = {}): StockTransfer {
  return {
    id: 4, company: 7, source_branch: 1, source_branch_name: 'Principal',
    destination_branch: 2, destination_branch_name: 'Sucursal Norte',
    status: 'draft', status_label: 'Borrador', reason: '', reference: '',
    items: [], total_units: 0, created_by: 3, created_by_username: 'ana',
    created_at: '2026-11-05T10:00:00Z', dispatched_at: null, received_at: null,
    cancelled_at: null, updated_at: '2026-11-05T10:00:00Z', ...over,
  };
}

function page(results: AdminProduct[], count = results.length, pageNo = 1) {
  return { count, page: pageNo, page_size: 100, results } as Awaited<ReturnType<typeof admin.fetchAdminProducts>>;
}

async function mount(data: StockTransfer = transfer(), catalogue: AdminProduct[] = CATALOGUE) {
  inv.fetchTransfer.mockResolvedValue(data);
  inv.setTransferItems.mockResolvedValue(data);
  adm.fetchAdminProducts.mockResolvedValue(page(catalogue));
  await act(async () => { render(<TransferDetailPage />); });
}

const search = () => screen.getByRole('searchbox', { name: 'Buscar producto' });
const qty = (name: string) => screen.getByRole('spinbutton', { name });

beforeEach(() => {
  capabilities = ['inventory.view', 'inventory.adjust'];
  adm.fetchAdminProducts.mockReset();
  inv.fetchTransfer.mockReset();
  inv.setTransferItems.mockReset();
});

describe('Borrador de transferencia · búsqueda', () => {
  it('muestra el buscador etiquetado y el conteo «Mostrando N de M»', async () => {
    await mount();
    expect(search()).toBeInTheDocument();
    expect(screen.getByText('Mostrando 4 de 4')).toBeInTheDocument();
    expect(screen.getAllByRole('spinbutton')).toHaveLength(4);
  });

  it('filtra por nombre, sin distinguir mayúsculas', async () => {
    const user = userEvent.setup();
    await mount();
    await user.type(search(), 'CABLE');
    expect(screen.getAllByRole('spinbutton')).toHaveLength(1);
    expect(qty('Cable USB-C 1 m')).toBeInTheDocument();
    expect(screen.getByText('Mostrando 1 de 4')).toBeInTheDocument();
  });

  it('filtra por código / SKU', async () => {
    const user = userEvent.setup();
    await mount();
    await user.type(search(), 'bds-0042');
    expect(screen.getAllByRole('spinbutton')).toHaveLength(1);
    expect(qty('Funda MagSafe iPhone 15')).toBeInTheDocument();
  });

  it('no distingue tildes, en ninguna de las dos direcciones', async () => {
    const user = userEvent.setup();
    await mount();
    await user.type(search(), 'camara');
    expect(screen.getAllByRole('spinbutton')).toHaveLength(1);
    expect(qty('Cámara de seguridad Wi-Fi')).toBeInTheDocument();

    await user.clear(search());
    await user.type(search(), 'cárgador');
    expect(screen.getAllByRole('spinbutton')).toHaveLength(1);
    expect(qty('Cargador 20W')).toBeInTheDocument();
  });

  it('busca por todas las palabras, en cualquier orden', async () => {
    const user = userEvent.setup();
    await mount();
    await user.type(search(), 'iphone funda');
    expect(screen.getAllByRole('spinbutton')).toHaveLength(1);
    expect(qty('Funda MagSafe iPhone 15')).toBeInTheDocument();
  });

  it('sin coincidencias dice qué se buscó y deja limpiar la búsqueda', async () => {
    const user = userEvent.setup();
    await mount();
    await user.type(search(), 'xyz');
    expect(screen.queryAllByRole('spinbutton')).toHaveLength(0);
    expect(screen.getByText('Ningún producto coincide con «xyz».')).toBeInTheDocument();
    expect(screen.getByText('Mostrando 0 de 4')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Limpiar búsqueda' }));
    expect(search()).toHaveValue('');
    expect(screen.getAllByRole('spinbutton')).toHaveLength(4);
  });

  it('conserva las cantidades al cambiar y limpiar la búsqueda', async () => {
    const user = userEvent.setup();
    await mount();
    await user.type(search(), 'cable');
    await user.type(qty('Cable USB-C 1 m'), '5');

    await user.clear(search());
    await user.type(search(), 'funda');
    expect(screen.queryByRole('spinbutton', { name: 'Cable USB-C 1 m' })).toBeNull();
    await user.type(qty('Funda MagSafe iPhone 15'), '2');

    await user.clear(search());
    expect(qty('Cable USB-C 1 m')).toHaveValue(5);
    expect(qty('Funda MagSafe iPhone 15')).toHaveValue(2);
    expect(qty('Cargador 20W')).toHaveValue(null);
  });

  it('«Guardar líneas» envía también las líneas ocultas por el filtro, y no las de 0', async () => {
    const user = userEvent.setup();
    // A line already saved (Cargador ×3) that the user sets to 0, and one kept (Cámara ×1).
    await mount(transfer({
      items: [
        { id: 90, product: 4, product_name: 'Cargador 20W', product_slug: 'p-4', quantity: 3 },
        { id: 91, product: 3, product_name: 'Cámara de seguridad Wi-Fi', product_slug: 'p-3', quantity: 1 },
      ],
      total_units: 4,
    }));
    expect(qty('Cargador 20W')).toHaveValue(3);

    await user.clear(qty('Cargador 20W'));
    await user.type(qty('Cargador 20W'), '0');
    await user.type(search(), 'cable');
    await user.type(qty('Cable USB-C 1 m'), '5');
    await user.clear(search());
    await user.type(search(), 'funda');
    await user.type(qty('Funda MagSafe iPhone 15'), '2');

    // Only «Funda» is on screen now.
    expect(screen.getAllByRole('spinbutton')).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Guardar líneas' }));

    expect(inv.setTransferItems).toHaveBeenCalledTimes(1);
    const [id, lines] = inv.setTransferItems.mock.calls[0];
    expect(id).toBe(4);
    expect([...lines].sort((a, b) => a.product - b.product)).toEqual([
      { product: 1, quantity: 5 },
      { product: 2, quantity: 2 },
      { product: 3, quantity: 1 },
    ]);
  });

  it('«Sólo con cantidad» muestra lo que se va a enviar, combinado con la búsqueda', async () => {
    const user = userEvent.setup();
    await mount();
    await user.type(qty('Cable USB-C 1 m'), '5');
    await user.type(qty('Funda MagSafe iPhone 15'), '2');
    await user.type(qty('Cargador 20W'), '0');

    const onlyWithQty = screen.getByRole('checkbox', { name: 'Sólo con cantidad' });
    expect(onlyWithQty).not.toBeChecked();
    await user.click(onlyWithQty);
    expect(onlyWithQty).toBeChecked();
    expect(screen.getAllByRole('spinbutton')).toHaveLength(2);
    expect(qty('Cable USB-C 1 m')).toHaveValue(5);
    expect(qty('Funda MagSafe iPhone 15')).toHaveValue(2);
    expect(screen.getByText('Mostrando 2 de 4')).toBeInTheDocument();

    await user.type(search(), 'funda');
    expect(screen.getAllByRole('spinbutton')).toHaveLength(1);
    await user.clear(search());

    // Correcting a quantity under the filter does not make the row vanish mid-edit…
    await user.clear(qty('Cable USB-C 1 m'));
    expect(qty('Cable USB-C 1 m')).toHaveValue(null);
    await user.type(qty('Cable USB-C 1 m'), '7');
    expect(qty('Cable USB-C 1 m')).toHaveValue(7);

    // …and a row left empty goes away the next time the filter is applied.
    await user.clear(qty('Cable USB-C 1 m'));
    await user.click(onlyWithQty);
    expect(screen.getAllByRole('spinbutton')).toHaveLength(4);
    await user.click(onlyWithQty);
    expect(screen.getAllByRole('spinbutton')).toHaveLength(1);
    expect(qty('Funda MagSafe iPhone 15')).toHaveValue(2);
  });

  it('«Sólo con cantidad» sin ninguna cantidad lo dice', async () => {
    const user = userEvent.setup();
    await mount();
    await user.click(screen.getByRole('checkbox', { name: 'Sólo con cantidad' }));
    expect(screen.queryAllByRole('spinbutton')).toHaveLength(0);
    expect(screen.getByText('Todavía no hay productos con cantidad.')).toBeInTheDocument();
  });
});

describe('Borrador de transferencia · qué productos se ofrecen', () => {
  it('pide sólo productos por cantidad: el servidor rechaza los de número de serie', async () => {
    await mount();
    expect(adm.fetchAdminProducts).toHaveBeenCalledWith(expect.objectContaining({ is_serialized: 'false', page_size: 100 }));
    expect(screen.getByText(/número de serie no se transfieren desde aquí/)).toBeInTheDocument();
  });

  it('recorre todas las páginas del catálogo, no sólo las primeras 100', async () => {
    const first = Array.from({ length: 100 }, (_, i) => product(1000 + i, `Producto ${i}`));
    const last = product(5000, 'Adaptador Lightning antiguo', 'OLD-1');
    inv.fetchTransfer.mockResolvedValue(transfer());
    adm.fetchAdminProducts
      .mockResolvedValueOnce(page(first, 101, 1))
      .mockResolvedValueOnce(page([last], 101, 2));
    const user = userEvent.setup();
    await act(async () => { render(<TransferDetailPage />); });

    expect(adm.fetchAdminProducts).toHaveBeenCalledTimes(2);
    expect(adm.fetchAdminProducts).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));
    expect(screen.getByText('Mostrando 101 de 101')).toBeInTheDocument();
    await user.type(search(), 'lightning');
    expect(qty('Adaptador Lightning antiguo')).toBeInTheDocument();
  });

  it('una línea guardada cuyo producto no está en la lista sigue a la vista y se envía', async () => {
    const user = userEvent.setup();
    await mount(transfer({
      items: [{ id: 92, product: 77, product_name: 'iPhone 13 128 GB', product_slug: 'p-77', quantity: 1 }],
      total_units: 1,
    }));
    expect(qty('iPhone 13 128 GB')).toHaveValue(1);
    expect(screen.getByText('Mostrando 5 de 5')).toBeInTheDocument();

    await user.type(qty('Cable USB-C 1 m'), '5');
    await user.click(screen.getByRole('button', { name: 'Guardar líneas' }));
    const [, lines] = inv.setTransferItems.mock.calls[0];
    expect([...lines].sort((a, b) => a.product - b.product)).toEqual([
      { product: 1, quantity: 5 },
      { product: 77, quantity: 1 },
    ]);
  });

  it('muestra el código junto al nombre sin cambiar el nombre accesible de la cantidad', async () => {
    await mount();
    const row = qty('Funda MagSafe iPhone 15').closest('li')!;
    expect(within(row).getByText('BDS-0042')).toBeInTheDocument();
  });

  it('quien sólo consulta ve la tabla de líneas, sin buscador ni cantidades', async () => {
    capabilities = ['inventory.view'];
    await mount(transfer({
      items: [{ id: 90, product: 4, product_name: 'Cargador 20W', product_slug: 'p-4', quantity: 3 }],
      total_units: 3,
    }));
    expect(screen.queryByRole('searchbox', { name: 'Buscar producto' })).toBeNull();
    expect(screen.queryAllByRole('spinbutton')).toHaveLength(0);
    expect(screen.getByRole('cell', { name: 'Cargador 20W' })).toBeInTheDocument();
  });
});
