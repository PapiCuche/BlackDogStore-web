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

const TITLES = [
  'Recibir mercadería', 'Registrar un equipo rápidamente', 'Recuento físico', 'Etiquetas pendientes', 'Consultar stock',
];

describe('tarjetas', () => {
  it('con todos los permisos hay cinco enlaces a sus pantallas', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'inventory.adjust', 'inventory.labels.print'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    const links = within(list).getAllByRole('link');
    expect(links).toHaveLength(5);
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/admin/inventory/receive', '/admin/inventory/units?register=1', '/admin/inventory/counts',
      '/admin/inventory/labels', '/admin/inventory/stock',
    ]);
    for (const title of TITLES) expect(within(list).getByText(title)).toBeInTheDocument();
  });

  it('quien sólo ve el inventario: lo demás se muestra desactivado, con el motivo', () => {
    render(<InventoryOperationsHub access={access(['inventory.view'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    expect(within(list).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual(['/admin/inventory/stock']);
    const receive = within(list).getByText('Recibir mercadería').closest('[aria-disabled="true"]') as HTMLElement;
    expect(receive).toBeInTheDocument();
    expect(receive).toHaveTextContent(/no puede registrar ingresos de stock/);
    expect(receive).toHaveTextContent(/Pídelo a quien administra/);
    expect(within(list).getByText('Etiquetas pendientes').closest('[aria-disabled="true"]')).toBeInTheDocument();
  });

  it('etiquetas se abren con su propia capacidad, aunque no se pueda ajustar stock', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'inventory.labels.print'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    expect(within(list).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual(['/admin/inventory/labels', '/admin/inventory/stock']);
  });

  it('cada tarjeta explica en una frase qué hace', () => {
    render(<InventoryOperationsHub access={access(['inventory.view', 'inventory.adjust', 'inventory.labels.print'])} />);
    const list = screen.getByRole('list', { name: 'Tareas del inventario' });
    for (const item of within(list).getAllByRole('listitem')) {
      expect(item.textContent!.length).toBeGreaterThan(40);
    }
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
