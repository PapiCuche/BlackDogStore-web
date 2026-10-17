import { render, screen, within } from '@testing-library/react';

import { CommissionClosureSummary } from '@/app/admin/components/CommissionClosureSummary';

/**
 * Cierre financiero (propietario, 2026-10-16) · las comisiones de una venta
 * anulada: devengada, liquidada, anulada y el saldo a recuperar (el ajuste
 * compensatorio de una comisión ya liquidada), cada uno por su nombre.
 */

const REPORT = {
  window_days: 7, today: '2026-10-16', branches: [], total_commission: '5.50', note: '', results: [],
  totals: { accrued: '3.00', settled: '6.50', voided: '1.00', recoverable: '-4.00' },
  adjustments: [
    { id: 1, order_id: 77, commission_id: 9, seller_name: 'Vendedora', amount: '-4.00', kind: 'cancellation',
      label: 'Ajuste por anulación (saldo a recuperar)', created_at: '2026-10-16T15:00:00Z' },
  ],
};

it('separa devengada, liquidada, anulada y saldo a recuperar', () => {
  render(<CommissionClosureSummary report={REPORT} />);
  const summary = screen.getByRole('region', { name: 'Estado de las comisiones' });
  expect(within(summary).getByTestId('commission-accrued')).toHaveTextContent('3.00');
  expect(within(summary).getByTestId('commission-settled')).toHaveTextContent('6.50');
  expect(within(summary).getByTestId('commission-voided')).toHaveTextContent('1.00');
  expect(within(summary).getByTestId('commission-recoverable')).toHaveTextContent(/-\s*S\/\s*4\.00/);
  const table = screen.getByRole('table', { name: 'Ajustes por anulación' });
  expect(table).toHaveTextContent('#77');
  expect(table).toHaveTextContent('Vendedora');
  expect(table).toHaveTextContent('Ajuste por anulación (saldo a recuperar)');
});

it('sin ajustes no hay tabla, y un informe antiguo sin totales no enseña nada', () => {
  const { rerender, container } = render(<CommissionClosureSummary report={{ ...REPORT, adjustments: [] }} />);
  expect(screen.queryByRole('table', { name: 'Ajustes por anulación' })).toBeNull();
  rerender(<CommissionClosureSummary report={{ ...REPORT, totals: undefined, adjustments: undefined }} />);
  expect(container).toBeEmptyDOMElement();
});
