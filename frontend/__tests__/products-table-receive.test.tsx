/** INV-UX-A1 — «Recibir mercadería» on each product row. */
import { render, screen, within } from '@testing-library/react';

import { ProductsTable } from '@/app/admin/components/ProductsTable';
import type { AdminProduct } from '@/app/lib/admin';

function product(id: number, over: Partial<AdminProduct> = {}): AdminProduct {
  return {
    id, name: `Producto ${id}`, slug: `p-${id}`, description: '', price: '10.00', inventory: 3, image_url: '',
    category_id: 1, category_name: 'Fundas', is_active: true, is_published_online: true, publication_problems: [],
    is_serialized: false, created_at: '', updated_at: '', ...over,
  };
}
const rowOf = (name: string) => screen.getByText(name).closest('tr') as HTMLElement;

it('a quantity product goes to Movimientos with the product preloaded', () => {
  render(<ProductsTable products={[product(8)]} canManage={false} canReceive onChanged={() => {}} />);
  expect(within(rowOf('Producto 8')).getByRole('link', { name: 'Recibir mercadería de Producto 8' }))
    .toHaveAttribute('href', '/admin/inventory/movements?receive=1&product=8');
});

it('a serial-tracked device goes to Equipos › Registrar equipo', () => {
  render(<ProductsTable products={[product(41, { is_serialized: true })]} canManage canReceive onChanged={() => {}} />);
  expect(within(rowOf('Producto 41')).getByRole('link', { name: /Recibir mercadería de Producto 41/ }))
    .toHaveAttribute('href', '/admin/inventory/units?register=1&product=41');
});

it('without inventory.adjust the shortcut is not offered, and a read-only viewer has no actions column', () => {
  const { rerender } = render(<ProductsTable products={[product(8)]} canManage onChanged={() => {}} />);
  expect(screen.queryByRole('link', { name: /Recibir mercadería/ })).toBeNull();
  expect(screen.getByRole('link', { name: 'Editar' })).toBeInTheDocument();
  rerender(<ProductsTable products={[product(8)]} canManage={false} onChanged={() => {}} />);
  expect(screen.queryByRole('columnheader', { name: 'Acciones' })).toBeNull();
});
