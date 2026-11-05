/**
 * INV-OPS-01 — Centro de operaciones: cinco tarjetas, cada una según su capacidad,
 * y todas las pantallas avanzadas a un clic.
 */
import { render, screen, within } from '@testing-library/react';

import { InventoryOperationsHub } from '@/app/admin/components/InventoryOperationsHub';
import { buildInternalAccess } from '@/app/admin/lib/internal-access';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

function access(capabilities: string[]) {
  return buildInternalAccess(
    { username: 'ana', role: 'customer' } as never,
    { company: { id: 7, name: 'Tienda' }, access: { capabilities, is_platform_admin: false } } as never,
  );
}

// INV-OPS-UX-04: the tasks of the day, in the order a shop does them, then the two quick ones.
const TITLES = [
  'Recibir mercadería', 'Recuento físico', 'Consultar stock', 'Ver movimientos', 'Transferir entre sucursales',
  'Importar archivos', 'Registrar un equipo rápidamente', 'Etiquetas pendientes',
];

describe('tarjetas', () => {
  it('con todos los permisos hay ocho enlaces a sus pantallas, en el orden del trabajo', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'inventory.adjust', 'inventory.labels.print', 'products.view'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    const links = within(list).getAllByRole('link');
    expect(links).toHaveLength(8);
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/admin/inventory/receive', '/admin/inventory/counts', '/admin/inventory/stock', '/admin/inventory/movements',
      '/admin/inventory/transfers', '/admin/inventory/bulk', '/admin/inventory/units?register=1', '/admin/inventory/labels',
    ]);
    for (const title of TITLES) expect(within(list).getByText(title)).toBeInTheDocument();
  });

  it('quien sólo ve el inventario: lo demás se muestra desactivado, con el motivo', () => {
    render(<InventoryOperationsHub access={access(['inventory.view'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    expect(within(list).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([
      '/admin/inventory/stock', '/admin/inventory/movements', '/admin/inventory/transfers',
    ]);
    // Importing writes: without inventory.adjust or products.manage it is shown, disabled, with the reason.
    expect(within(list).getByText('Importar archivos').closest('[aria-disabled="true"]')).toHaveTextContent(/no puede importar/);
    const receive = within(list).getByText('Recibir mercadería').closest('[aria-disabled="true"]') as HTMLElement;
    expect(receive).toBeInTheDocument();
    expect(receive).toHaveTextContent(/no puede registrar ingresos de stock/);
    expect(receive).toHaveTextContent(/Pídelo a quien administra/);
    expect(within(list).getByText('Etiquetas pendientes').closest('[aria-disabled="true"]')).toBeInTheDocument();
  });

  it('etiquetas se abren con su propia capacidad, aunque no se pueda ajustar stock', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'inventory.labels.print'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    expect(within(list).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([
      '/admin/inventory/stock', '/admin/inventory/movements', '/admin/inventory/transfers', '/admin/inventory/labels',
    ]);
  });

  it('cada tarjeta explica en una frase qué hace', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'inventory.adjust', 'inventory.labels.print', 'products.view'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    for (const item of within(list).getAllByRole('listitem')) {
      expect(item.textContent!.length).toBeGreaterThan(40);
    }
  });
});

describe('P2-I · recibir necesita ver productos', () => {
  it('con inventory.adjust pero sin products.view la tarjeta de recibir sale desactivada con el motivo', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'inventory.adjust'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    const card = within(list).getByText('Recibir mercadería').closest('[aria-disabled="true"]') as HTMLElement;
    expect(card).toBeInTheDocument();
    expect(card).toHaveTextContent('Para recibir necesitas poder ver productos');
    // The other adjust-only tasks still work.
    expect(within(list).getByRole('link', { name: /Registrar un equipo rápidamente/ })).toBeInTheDocument();
  });
});

describe('Modo avanzado', () => {
  it('lista las pantallas detalladas según la capacidad y explica el Kardex', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'inventory.adjust'])} />);
    const advanced = screen.getByRole('region', { name: 'Modo avanzado' });
    const hrefs = within(advanced).getAllByRole('link').map((a) => a.getAttribute('href'));
    expect(hrefs).toEqual(expect.arrayContaining([
      '/admin/inventory', '/admin/inventory/movements', '/admin/inventory/transfers', '/admin/inventory/counts',
      '/admin/inventory/units?line=new', '/admin/inventory/import',
    ]));
    expect(hrefs).not.toContain('/admin/inventory/costs');
    expect(within(advanced).getByText(/Kardex: el historial de cada entrada/)).toBeInTheDocument();
  });

  it('costos aparecen sólo con costs.view', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'costs.view'])} />);
    const advanced = screen.getByRole('region', { name: 'Modo avanzado' });
    expect(within(advanced).getByRole('link', { name: /Costos y precios/ })).toHaveAttribute('href', '/admin/inventory/costs');
  });
});

describe('INV-OPS-UX-04 · las tareas dicen lo que la persona hace', () => {
  it('recibir menciona la lista de WhatsApp y el precio; el recuento, la sucursal y la aprobación', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'inventory.adjust', 'products.view'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    expect(within(list).getByRole('link', { name: /Recibir mercadería/ })).toHaveTextContent(/WhatsApp/);
    expect(within(list).getByRole('link', { name: /Recibir mercadería/ })).toHaveTextContent(/precio/);
    expect(within(list).getByRole('link', { name: /Recuento físico/ })).toHaveTextContent(/sucursal/);
    expect(within(list).getByRole('link', { name: /Importar archivos/ })).toHaveTextContent(/Excel/);
  });

  it('quien sólo administra productos puede importar el catálogo', () => {
    render(<InventoryOperationsHub access={access(['products.manage', 'products.view'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    expect(within(list).getByRole('link', { name: /Importar archivos/ })).toHaveAttribute('href', '/admin/inventory/bulk');
  });
});
