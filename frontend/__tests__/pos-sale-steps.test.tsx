/**
 * SALES-UX-02 — the path of a sale: Productos → Cliente → Forma de pago → Comprobante → Revisar → Cobrar.
 *
 * The steps only READ the state of the till and say what is missing; they never decide a price, a total or a
 * permission (the server does). They are a guide, so a step that is not required says «Opcional», and one that
 * blocks says why.
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { saleSteps, type StepInput } from '@/app/admin/lib/pos-steps';
import { PosSaleSteps } from '@/app/admin/sales/pos/PosSaleSteps';
import { PosReviewPanel } from '@/app/admin/sales/pos/PosReviewPanel';
import type { PosLine } from '@/app/admin/lib/unit-entry';
import type { PosPreview } from '@/app/admin/lib/internal-api';

const READY: StepInput = {
  hasLines: true, unitsMissing: 0, unpriced: 0, customerChosen: false, receiptType: 'sales_note',
  customerIsRuc: false, isCash: true, received: '2000', total: 1800, paymentChosen: true, previewReady: true, terms: true,
  supplyReasonsMissing: 0, previewError: null, sendingBlocked: false, branchChosen: true, receiptSelectable: true,
};
const by = (input: Partial<StepInput>) => Object.fromEntries(saleSteps({ ...READY, ...input }).map((s) => [s.id, s]));

describe('saleSteps', () => {
  it('lists the five steps in the order of the sale', () => {
    expect(saleSteps(READY).map((s) => s.id)).toEqual(['products', 'customer', 'payment', 'receipt', 'review']);
  });

  it('a complete sale has every required step done and the customer optional', () => {
    const steps = by({});
    expect(steps.products.status).toBe('done');
    expect(steps.payment.status).toBe('done');
    expect(steps.receipt.status).toBe('done');
    expect(steps.customer.status).toBe('optional');
    expect(steps.review.status).toBe('done');
  });

  it('an empty basket is the current step and nothing else is ready', () => {
    const steps = by({ hasLines: false });
    expect(steps.products.status).toBe('current');
    expect(steps.review.status).not.toBe('done');
  });

  it('devices still to identify, or without a price, block the products step and say why', () => {
    expect(by({ unitsMissing: 2 }).products).toMatchObject({ status: 'blocked' });
    expect(by({ unitsMissing: 2 }).products.note).toMatch(/identifica 2 equipos/i);
    expect(by({ unpriced: 1 }).products.note).toMatch(/sin precio/i);
  });

  it('cash short of the total blocks the payment step', () => {
    const steps = by({ received: '1000' });
    expect(steps.payment.status).toBe('blocked');
    expect(steps.payment.note).toMatch(/no alcanza/i);
    expect(by({ received: '' }).payment.status).toBe('current');
  });

  it('a card sale needs no cash amount', () => {
    expect(by({ isCash: false, received: '' }).payment.status).toBe('done');
  });

  it('no document chosen leaves the receipt step current', () => {
    expect(by({ receiptType: '' }).receipt.status).toBe('current');
  });

  it('a factura without a customer with RUC blocks the customer step', () => {
    const steps = by({ receiptType: 'factura', customerChosen: false });
    expect(steps.customer.status).toBe('blocked');
    expect(steps.customer.note).toMatch(/RUC/);
    expect(by({ receiptType: 'factura', customerChosen: true, customerIsRuc: true }).customer.status).toBe('done');
    expect(by({ receiptType: 'factura', customerChosen: true, customerIsRuc: false }).customer.status).toBe('blocked');
  });

  it('the review waits for the server total and the confirmation of the conditions', () => {
    expect(by({ previewReady: false }).review.status).not.toBe('done');
    expect(by({ terms: false }).review.status).not.toBe('done');
  });

  it('exactly one step is current at a time', () => {
    const steps = saleSteps({ ...READY, receiptType: '', received: '' });
    expect(steps.filter((s) => s.status === 'current')).toHaveLength(1);
  });
});

describe('saleSteps · everything that disables Cobrar is told', () => {
  it('a missing supply reason blocks the products step and the review', () => {
    const steps = by({ supplyReasonsMissing: 1 });
    expect(steps.products.status).toBe('blocked');
    expect(steps.products.note).toMatch(/motivo/i);
    expect(steps.review.status).not.toBe('done');
  });

  it('a pricing error is shown in the review step instead of «calculando»', () => {
    const steps = by({ previewError: 'Indica el motivo del descuento.', previewReady: false });
    expect(steps.review.note).toContain('Indica el motivo del descuento.');
    expect(steps.review.status).toBe('blocked');
  });

  it('a previous sale still to be checked blocks the review', () => {
    expect(by({ sendingBlocked: true }).review.status).not.toBe('done');
    expect(by({ sendingBlocked: true }).review.note).toMatch(/venta anterior/i);
  });

  it('without a branch nothing is ready', () => {
    expect(by({ branchChosen: false }).products.status).toBe('blocked');
    expect(by({ branchChosen: false }).review.status).not.toBe('done');
  });

  it('a document the branch cannot issue is not a chosen document', () => {
    expect(by({ receiptType: 'factura', receiptSelectable: false, customerChosen: true, customerIsRuc: true }).receipt.status).toBe('current');
  });
});

describe('PosSaleSteps', () => {
  it('writes the note of the current or blocked step where a touch user can read it', () => {
    render(<PosSaleSteps steps={saleSteps({ ...READY, unitsMissing: 2 })} onGo={jest.fn()} />);
    expect(screen.getByRole('status')).toHaveTextContent(/identifica 2 equipos/i);
  });

  it('renders an ordered list, marks the current step and reports clicks', async () => {
    const onGo = jest.fn();
    render(<PosSaleSteps steps={saleSteps({ ...READY, receiptType: '' })} onGo={onGo} />);
    const list = screen.getByRole('list', { name: 'Pasos de la venta' });
    const items = within(list).getAllByRole('listitem');
    expect(items).toHaveLength(5);
    expect(screen.getByRole('button', { name: /4\s*Comprobante/ })).toHaveAttribute('aria-current', 'step');
    await userEvent.click(screen.getByRole('button', { name: /2\s*Cliente/ }));
    expect(onGo).toHaveBeenCalledWith('customer');
  });

  it('spells out the state of each step for assistive technology, not only by colour', () => {
    render(<PosSaleSteps steps={saleSteps({ ...READY, unitsMissing: 1 })} onGo={jest.fn()} />);
    expect(screen.getByRole('button', { name: /1\s*Productos/ })).toHaveAccessibleName(/requiere atención/i);
    expect(screen.getByRole('button', { name: /3\s*Forma de pago/ })).toHaveAccessibleName(/listo/i);
  });
});

const unitLine: PosLine = {
  product: 5, name: 'iPhone 15 128 GB Seminuevo', price: '0.00', quantity: 2, available: 3, isSerialized: true,
  requiresImei: true, pricedBy: 'unit',
  units: [
    { id: 1, method: 'scanner', serial_number: 'F2LXK1AAA1', imei: '356938035643809', price: '1800.00', battery_health: 88, cosmetic_grade: 'A' },
    { id: 2, method: 'scanner', serial_number: 'F2LXK1BBB2', imei: '356938035643817', price: '2100.00', battery_health: null, cosmetic_grade: null },
  ],
};
const caseLine: PosLine = {
  product: 8, name: 'Funda MagSafe', price: '49.90', quantity: 2, available: 10, isSerialized: false, requiresImei: false, units: [],
};
const preview: PosPreview = {
  subtotal: '3999.80', discount: '100.00', discount_source: 'manual', promotions: [], coupon_code: '', total: '3899.80',
  tax: { base_label: 'Op. gravada', taxable_amount: '3304.91', tax_label: 'IGV (18%)', tax_amount: '594.89', tax_treatment: 'taxed' } as PosPreview['tax'],
  seller: { id: 1, name: 'Ana' }, customer: null, commission: null,
  lines: [
    { product: 5, name: 'iPhone 15 128 GB Seminuevo', quantity: 1, price: '1800.00', unit: 1 },
    { product: 5, name: 'iPhone 15 128 GB Seminuevo', quantity: 1, price: '2100.00', unit: 2 },
    { product: 8, name: 'Funda MagSafe', quantity: 2, price: '49.90' },
  ],
};

describe('PosReviewPanel', () => {
  const base = {
    lines: [unitLine, caseLine], preview, customerName: 'Rosa Quispe', sellerName: 'Ana', paymentLabel: 'Efectivo',
    received: '4000', document: { value: 'sales_note', label: 'Nota de venta interna' },
  };

  it('shows each device with its own price, its state and a masked identifier', () => {
    render(<PosReviewPanel {...base} />);
    const region = screen.getByRole('region', { name: 'Revisar la venta' });
    expect(region).toHaveTextContent('iPhone 15 128 GB Seminuevo');
    expect(region).toHaveTextContent('S/ 1,800.00');
    expect(region).toHaveTextContent('S/ 2,100.00');
    expect(region).toHaveTextContent('Batería 88 %');
    expect(region).toHaveTextContent('IMEI ••••3809');
    expect(region).toHaveTextContent('IMEI ••••3817');
    expect(region).not.toHaveTextContent('356938035643809');
    expect(region).not.toHaveTextContent('F2LXK1AAA1');
  });

  it('shows accessories as quantity × unit price', () => {
    render(<PosReviewPanel {...base} />);
    expect(screen.getByRole('region', { name: 'Revisar la venta' })).toHaveTextContent('Funda MagSafe');
    expect(screen.getByRole('region', { name: 'Revisar la venta' })).toHaveTextContent('2 × S/ 49.90');
  });

  it('shows the discount, the subtotal, the taxes the server computed and the total', () => {
    render(<PosReviewPanel {...base} />);
    const region = screen.getByRole('region', { name: 'Revisar la venta' });
    expect(region).toHaveTextContent('Subtotal');
    expect(region).toHaveTextContent('S/ 3,999.80');
    expect(within(region).getByText('Descuento', { selector: 'dt' })).toBeInTheDocument();
    expect(region).toHaveTextContent('−S/ 100.00');
    expect(region).toHaveTextContent('IGV (18%)');
    expect(region).toHaveTextContent('S/ 594.89');
    expect(within(region).getByText('Total', { selector: 'dt' })).toBeInTheDocument();
    expect(region).toHaveTextContent('S/ 3,899.80');
  });

  it('shows the customer, the seller, the method, the cash and change, and the document kind', () => {
    render(<PosReviewPanel {...base} />);
    const region = screen.getByRole('region', { name: 'Revisar la venta' });
    expect(region).toHaveTextContent('Rosa Quispe');
    expect(region).toHaveTextContent('Ana');
    expect(region).toHaveTextContent('Efectivo');
    expect(region).toHaveTextContent('Vuelto');
    expect(region).toHaveTextContent('S/ 100.20');
    expect(region).toHaveTextContent('Nota de venta interna');
    expect(region).toHaveTextContent('Interno · no fiscal');
  });

  it('says «venta al público» when there is no customer and marks an electronic document as such', () => {
    render(<PosReviewPanel {...base} customerName={null} document={{ value: 'boleta', label: 'Boleta electrónica · BETA' }} />);
    const region = screen.getByRole('region', { name: 'Revisar la venta' });
    expect(region).toHaveTextContent('Venta al público');
    expect(region).toHaveTextContent('Comprobante electrónico · BETA');
    expect(region).not.toHaveTextContent('Interno · no fiscal');
  });

  it('shows the pricing error instead of waiting forever', () => {
    render(<PosReviewPanel {...base} preview={null} error="Indica el motivo del descuento." />);
    expect(screen.getByRole('alert')).toHaveTextContent('Indica el motivo del descuento.');
    expect(screen.getByRole('region', { name: 'Revisar la venta' })).not.toHaveTextContent('Calculando');
  });

  it('never multiplies money in the browser: a line shows the server price and the quantity', () => {
    const odd = { ...preview, lines: [{ product: 8, name: 'Funda MagSafe', quantity: 3, price: '33.333' }] };
    render(<PosReviewPanel {...base} preview={odd} />);
    const region = screen.getByRole('region', { name: 'Revisar la venta' });
    expect(region).toHaveTextContent('3 × S/ 33.33');
    expect(region).not.toHaveTextContent(/S\/\s?99\.99/);          // 3 × 33.333, never multiplied here
  });

  it('does not invent figures while the server has not answered', () => {
    render(<PosReviewPanel {...base} preview={null} />);
    const region = screen.getByRole('region', { name: 'Revisar la venta' });
    expect(region).toHaveTextContent('Calculando el total');
    expect(region).not.toHaveTextContent('S/ 3,899.80');
  });
});
