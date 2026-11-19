import { render, screen } from '@testing-library/react';
import { ReceiptCoverageNotice } from '@/app/admin/components/ReceiptCoverageNotice';
import { OrdersTable } from '@/app/admin/components/OrdersTable';
import { PosReceiptSelector, type ReceiptOption } from '@/app/admin/sales/pos/PosReceiptSelector';
import { buildSalesNoteQuery } from '@/app/lib/inventory';
import type { AdminOrder, ReceiptCoverage } from '@/app/lib/admin';

/**
 * NOTE-NOT-SUBSTITUTE — la nota de venta no reemplaza a la boleta o factura.
 *
 * Los avisos son SÓLO para el personal (la nota no lleva leyenda). El texto lo
 * decide el servidor; la pantalla no deduce «emitido» de nada y nunca dice
 * «aceptado» sin un comprobante de producción aceptado.
 */

const OFF_WARNING =
  'Esta venta no tiene comprobante electrónico emitido por el sistema: emite la boleta o factura por SUNAT ' +
  '(SOL u otro medio) al cobrar o entregar. La facturación electrónica del sistema está desactivada; la nota ' +
  'de venta no reemplaza al comprobante.';

const coverage = (over: Partial<ReceiptCoverage>): ReceiptCoverage => ({
  state: 'pending', integration_enabled: false, warning: OFF_WARNING, receipt: '', ...over,
});

describe('ReceiptCoverageNotice', () => {
  it('tells the staff to issue the receipt through SUNAT when the sale has none', () => {
    render(<ReceiptCoverageNotice coverage={coverage({})} />);
    const notice = screen.getByRole('alert');
    expect(notice).toHaveAttribute('data-state', 'pending');
    expect(notice).toHaveTextContent('Comprobante de pago pendiente');
    expect(notice).toHaveTextContent('emite la boleta o factura por SUNAT (SOL u otro medio) al cobrar o entregar');
    expect(notice).toHaveTextContent('la nota de venta no reemplaza al comprobante');
  });

  it('names the accepted receipt and warns nothing when the system holds it', () => {
    render(<ReceiptCoverageNotice coverage={coverage({ state: 'system', warning: '', receipt: 'F001-12' })} />);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByTestId('receipt-coverage')).toHaveTextContent('F001-12');
  });

  it('says nothing for an order that was not charged, or without coverage', () => {
    const { container, rerender } = render(
      <ReceiptCoverageNotice coverage={coverage({ state: 'not_due', warning: '' })} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ReceiptCoverageNotice coverage={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('never claims acceptance for a pending sale', () => {
    render(<ReceiptCoverageNotice coverage={coverage({})} />);
    expect(screen.getByRole('alert').textContent?.toLowerCase()).not.toContain('aceptad');
  });
});

describe('the order list marks the sales pending their receipt', () => {
  const order = (over: Partial<AdminOrder>): AdminOrder => ({
    id: 7, customer_name: 'Cliente', customer_email: 'c@x.pe', user_id: null, username: null, total: '118.00',
    status: 'paid', fulfillment_status: 'pending', paid: true, created_at: '2026-11-19T10:00:00Z',
    paid_at: '2026-11-19T10:00:00Z', item_count: 1, has_stock_shortfall: false, ...over,
  });

  it('shows the badge only where the server says so', () => {
    render(<OrdersTable orders={[order({ id: 1, receipt_pending: true }), order({ id: 2, receipt_pending: false })]} />);
    const badges = screen.getAllByTestId('order-receipt-pending-badge');
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent('Sin comprobante electrónico');
  });
});

describe('the sales note list can ask only for the sales pending their receipt', () => {
  it('sends ?receipt=pending', () => {
    expect(buildSalesNoteQuery({ receipt: 'pending' })).toBe('?receipt=pending');
    expect(buildSalesNoteQuery({ receipt: '' })).toBe('');
  });
});

describe('the till, choosing the sales note with the integration off', () => {
  const NOTE: ReceiptOption = { value: 'sales_note', label: 'Nota de venta interna', branches: [1], enabled: true };
  const BOLETA_OFF: ReceiptOption = {
    value: 'boleta', label: 'Boleta electrónica · BETA', branches: [1], enabled: false,
    disabled_code: 'FISCAL_DISABLED', disabled_reason: 'La emisión fiscal no está habilitada.',
  };

  it('tells the cashier to issue the boleta or factura through SUNAT', () => {
    render(<PosReceiptSelector options={[NOTE, BOLETA_OFF]} branch={1} value="sales_note" onChange={() => {}} />);
    expect(screen.getByTestId('pos-receipt-external'))
      .toHaveTextContent('emite la boleta o factura por SUNAT (SOL u otro medio)');
  });
});
