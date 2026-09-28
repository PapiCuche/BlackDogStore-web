import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PosReceiptSelector } from '@/app/admin/sales/pos/PosReceiptSelector';

it('offers only documents configured for the selected branch and submits the choice', async () => {
  const changed = jest.fn();
  render(<PosReceiptSelector branch={1} value="sales_note" onChange={changed} options={[
    { value: 'sales_note', label: 'Nota de venta interna', branches: [1, 2] },
    { value: 'factura', label: 'Factura · BETA', branches: [2] },
    { value: 'boleta', label: 'Boleta · BETA', branches: [1] },
  ]} />);
  expect(screen.queryByRole('option', { name: 'Factura · BETA' })).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Tipo de comprobante'), 'boleta');
  expect(changed).toHaveBeenCalledWith('boleta');
});
