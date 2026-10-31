import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PosReceiptSelector, isSelectable, type ReceiptOption } from '@/app/admin/sales/pos/PosReceiptSelector';

/**
 * El selector de comprobante de la caja (ERP-FISCAL-6 §20/§30).
 *
 * El backend manda cada opción con `enabled`, `branches` y una causa cuando no
 * se puede usar. Aquí sólo se comprueba que la pantalla la respeta: no ofrece lo
 * que el servidor rechazaría, muestra por qué, y no pierde la nota interna.
 */

const NOTE: ReceiptOption = {
  value: 'sales_note', label: 'Nota de venta interna', branches: [1, 2],
  enabled: true, disabled_code: '', disabled_reason: '',
};
const BOLETA: ReceiptOption = {
  value: 'boleta', label: 'Boleta electrónica · BETA', branches: [1, 2],
  enabled: true, disabled_code: '', disabled_reason: '',
};
const FACTURA_ONLY_BRANCH_2: ReceiptOption = {
  value: 'factura', label: 'Factura electrónica · BETA', branches: [2],
  enabled: true, disabled_code: '', disabled_reason: '',
};
const FACTURA_NO_SERIES: ReceiptOption = {
  value: 'factura', label: 'Factura electrónica · BETA', branches: [],
  enabled: false, disabled_code: 'NO_SERIES_FOR_BRANCH',
  disabled_reason: 'Falta una serie de factura activa para «Tienda principal».',
};

it('offers the internal note, the boleta and the factura when the backend enables them', () => {
  render(<PosReceiptSelector branch={2} value="" onChange={jest.fn()}
    options={[NOTE, BOLETA, FACTURA_ONLY_BRANCH_2]} />);
  expect(screen.getByRole('option', { name: 'Nota de venta interna' })).toBeEnabled();
  expect(screen.getByRole('option', { name: 'Boleta electrónica · BETA' })).toBeEnabled();
  expect(screen.getByRole('option', { name: 'Factura electrónica · BETA' })).toBeEnabled();
  // Lo que NO va en una venta nueva: notas correctivas y comunicaciones.
  expect(screen.queryByRole('option', { name: /cr[ée]dito|d[ée]bito|baja|resumen/i })).not.toBeInTheDocument();
});

it('lets the operator choose a document enabled for the selected branch and submits it', async () => {
  const changed = jest.fn();
  render(<PosReceiptSelector branch={1} value="sales_note" onChange={changed}
    options={[NOTE, FACTURA_ONLY_BRANCH_2, BOLETA]} />);
  await userEvent.selectOptions(screen.getByLabelText('Tipo de comprobante'), 'boleta');
  expect(changed).toHaveBeenCalledWith('boleta');
});

it('keeps a document configured only for another branch visible but not selectable', () => {
  render(<PosReceiptSelector branch={1} value="" onChange={jest.fn()}
    options={[NOTE, FACTURA_ONLY_BRANCH_2, BOLETA]} />);
  const factura = screen.getByRole('option', { name: /Factura electrónica · BETA — no disponible/ });
  expect(factura).toBeDisabled();
  expect(screen.getByText('No disponible en esta sucursal.')).toBeInTheDocument();
  expect(isSelectable(FACTURA_ONLY_BRANCH_2, 1)).toBe(false);
  expect(isSelectable(FACTURA_ONLY_BRANCH_2, 2)).toBe(true);
});

it('shows a disabled fiscal option with the reason the backend gave', () => {
  render(<PosReceiptSelector branch={1} value="" onChange={jest.fn()}
    options={[NOTE, FACTURA_NO_SERIES, BOLETA]} />);
  expect(screen.getByRole('option', { name: /Factura electrónica · BETA — no disponible/ })).toBeDisabled();
  expect(screen.getByRole('list', { name: 'Documentos no disponibles' }))
    .toHaveTextContent('Falta una serie de factura activa para «Tienda principal».');
  expect(screen.getByRole('option', { name: 'Boleta electrónica · BETA' })).toBeEnabled();
});

it('drops the chosen document when the branch changes to one that cannot issue it', () => {
  const changed = jest.fn();
  const { rerender } = render(<PosReceiptSelector branch={2} value="factura" onChange={changed}
    options={[NOTE, FACTURA_ONLY_BRANCH_2, BOLETA]} />);
  expect(changed).not.toHaveBeenCalled();
  rerender(<PosReceiptSelector branch={1} value="factura" onChange={changed}
    options={[NOTE, FACTURA_ONLY_BRANCH_2, BOLETA]} />);
  expect(changed).toHaveBeenCalledWith('');
});

it('does not touch a selection that is still valid', () => {
  const changed = jest.fn();
  render(<PosReceiptSelector branch={1} value="sales_note" onChange={changed}
    options={[NOTE, BOLETA]} />);
  expect(changed).not.toHaveBeenCalled();
  expect(screen.getByRole('note')).toHaveTextContent('Documento interno sin validez tributaria.');
});

it('still works with a context that predates the enabled/reason contract', () => {
  const legacy: ReceiptOption[] = [
    { value: 'sales_note', label: 'Nota de venta interna', branches: [1] },
    { value: 'boleta', label: 'Boleta electrónica · BETA', branches: [1] },
  ];
  render(<PosReceiptSelector branch={1} value="" onChange={jest.fn()} options={legacy} />);
  expect(screen.getByRole('option', { name: 'Nota de venta interna' })).toBeEnabled();
  expect(screen.getByRole('option', { name: 'Boleta electrónica · BETA' })).toBeEnabled();
  expect(screen.queryByRole('list', { name: 'Documentos no disponibles' })).not.toBeInTheDocument();
});

it('asks for a branch before offering anything', () => {
  render(<PosReceiptSelector branch={null} value="" onChange={jest.fn()} options={[NOTE, BOLETA]} />);
  expect(screen.getByRole('option', { name: /Nota de venta interna — no disponible/ })).toBeDisabled();
  expect(screen.getAllByText('Selecciona una sucursal.').length).toBe(2);
});

describe('SALES-UX-02 · the kind of document is said out loud', () => {
  const FISCAL_OFF = (value: string, label: string): ReceiptOption => ({
    value, label, branches: [], enabled: false, disabled_code: 'FISCAL_DISABLED',
    disabled_reason: 'La emisión electrónica no está habilitada en este servidor.',
  });

  it('marks the internal note as internal and NOT a fiscal receipt', () => {
    render(<PosReceiptSelector branch={1} value="sales_note" onChange={jest.fn()} options={[NOTE, BOLETA]} />);
    const note = screen.getByRole('note');
    expect(note).toHaveAttribute('data-document-kind', 'internal');
    expect(note).toHaveTextContent('Interno · no fiscal');
    expect(note).toHaveTextContent('No es un comprobante de pago ante SUNAT');
    expect(note).not.toHaveTextContent('Comprobante electrónico');
  });

  it('marks a boleta or factura as an electronic receipt that is still BETA', () => {
    render(<PosReceiptSelector branch={1} value="boleta" onChange={jest.fn()} options={[NOTE, BOLETA]} />);
    const note = screen.getByRole('note');
    expect(note).toHaveAttribute('data-document-kind', 'fiscal');
    expect(note).toHaveTextContent('Comprobante electrónico · BETA');
    expect(note).not.toHaveTextContent('Interno · no fiscal');
  });

  it('before choosing, tells the three documents apart', () => {
    render(<PosReceiptSelector branch={1} value="" onChange={jest.fn()} options={[NOTE, BOLETA]} />);
    const note = screen.getByRole('note');
    expect(note).toHaveAttribute('data-document-kind', 'none');
    expect(note).toHaveTextContent('Nota de venta interna');
    expect(note).toHaveTextContent('Boleta y factura electrónicas');
  });

  it('says plainly that the electronic receipts are not operative when the server has them off', () => {
    render(<PosReceiptSelector branch={1} value="" onChange={jest.fn()}
      options={[NOTE, FISCAL_OFF('boleta', 'Boleta electrónica · BETA'), FISCAL_OFF('factura', 'Factura electrónica · BETA')]} />);
    expect(screen.getByRole('note')).toHaveTextContent('Boleta y factura electrónicas: no operativas');
  });

  it('does not claim the electronic receipts are off when they are simply absent for this operator', () => {
    render(<PosReceiptSelector branch={1} value="" onChange={jest.fn()} options={[NOTE]} />);
    expect(screen.getByRole('note')).not.toHaveTextContent('no operativas');
    expect(screen.getByRole('note')).toHaveTextContent('no disponibles para tu cuenta');
    expect(screen.getByRole('note')).not.toHaveTextContent('hoy en BETA');
  });
});
