import { fireEvent, render, screen } from '@testing-library/react';

import { InventoryModeSwitch, QuickInventoryTasks, readInventoryMode } from '@/app/admin/components/InventoryMode';

/**
 * INV-SWITCH-01 · modo rápido y modo avanzado del inventario: dos vistas sobre
 * las MISMAS pantallas y servicios. El rápido sólo lleva a las tareas del día;
 * el avanzado es el tablero de siempre. Se recuerda por navegador.
 */

beforeEach(() => window.localStorage.clear());

it('por defecto es el modo avanzado de siempre', () => {
  expect(readInventoryMode()).toBe('advanced');
});

it('cambiar de modo se recuerda y se anuncia', () => {
  const onChange = jest.fn();
  render(<InventoryModeSwitch mode="advanced" onChange={onChange} />);
  expect(screen.getByRole('button', { name: 'Modo avanzado' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'Modo rápido' }));
  expect(onChange).toHaveBeenCalledWith('quick');
  expect(readInventoryMode()).toBe('quick');
});

it('un almacenamiento ilegible no rompe nada', () => {
  window.localStorage.setItem('bds-inventory-mode', 'otra-cosa');
  expect(readInventoryMode()).toBe('advanced');
});

it('el modo rápido lleva a las pantallas existentes, sin inventario paralelo', () => {
  render(<QuickInventoryTasks canSeeSuppliers canRegister />);
  const links = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
  expect(links).toEqual([
    '/admin/inventory/stock', '/admin/inventory/bulk', '/admin/products/suppliers/offers',
    '/admin/inventory/units', '/admin/inventory/used', '/admin/products/register',
  ]);
});

it('sin permisos de proveedores o de registro, esas tareas no aparecen', () => {
  render(<QuickInventoryTasks canSeeSuppliers={false} canRegister={false} />);
  expect(screen.queryByRole('link', { name: /Ofertas de proveedores/ })).toBeNull();
  expect(screen.queryByRole('link', { name: /Registrar producto/ })).toBeNull();
  expect(screen.getByRole('link', { name: /Consultar stock y precios/ })).toBeInTheDocument();
});
