/**
 * DEVICE-COMMERCE-01 · DC1 — «Valor al costo», junto al valor a precio de venta.
 *
 * El servidor sólo manda la cifra a quien tiene `costs.view`; la tarjeta no
 * aparece si no viene, y avisa de las unidades sin costo en vez de callarlas.
 */
import { render, screen } from '@testing-library/react';

import { InventoryCostCard } from '@/app/admin/components/InventoryUi';

describe('Valor al costo', () => {
  it('sin la cifra (sin `costs.view`) no hay tarjeta', () => {
    const { container } = render(<InventoryCostCard summary={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('con la cifra, la muestra y cuenta lo pendiente', () => {
    render(<InventoryCostCard summary={{ inventory_cost_value: '3066.00', inventory_cost_pending_units: 4 }} />);
    expect(screen.getByText('Valor al costo')).toBeInTheDocument();
    expect(screen.getByText(/3[.,]?066/)).toBeInTheDocument();
    expect(screen.getByText(/4 unidades sin costo/)).toBeInTheDocument();
  });

  it('sin pendientes lo dice así', () => {
    render(<InventoryCostCard summary={{ inventory_cost_value: '10.00', inventory_cost_pending_units: 0 }} />);
    expect(screen.getByText(/Promedio por sucursal y costo de cada equipo/)).toBeInTheDocument();
  });
});
