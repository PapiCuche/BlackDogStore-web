/**
 * SALES-UX-02 — a new customer WITHOUT leaving the till. The basket, the scanned devices and the idempotency key
 * stay where they are; only the customer is added. A document that already identifies somebody offers that person.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { PosNewCustomerDialog } from '@/app/admin/sales/pos/PosNewCustomerDialog';
import * as api from '@/app/admin/lib/internal-api';

jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  createCustomer: jest.fn(),
}));
const createCustomer = api.createCustomer as jest.MockedFunction<typeof api.createCustomer>;

const row = (over: Partial<api.CustomerRow> = {}): api.CustomerRow => ({
  id: 7, display_name: 'Rosa Quispe', has_account: false, customer_type: 'person', document_type: 'dni',
  document_number: '45678912', phone: '', email: '', is_active: true, created_at: '', updated_at: '', ...over,
});

beforeEach(() => createCustomer.mockReset());

it('is a modal dialog named «Nuevo cliente» and closes with Escape or Cancelar', async () => {
  const onClose = jest.fn();
  render(<PosNewCustomerDialog companyId={3} onCreated={jest.fn()} onClose={onClose} />);
  expect(screen.getByRole('dialog', { name: 'Nuevo cliente' })).toHaveAttribute('aria-modal', 'true');
  await userEvent.keyboard('{Escape}');
  expect(onClose).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
  expect(onClose).toHaveBeenCalledTimes(2);
});

it('creates the customer in the company of the till and hands it to the sale', async () => {
  createCustomer.mockResolvedValue({ ...row(), first_name: 'Rosa', last_name: 'Quispe', possible_duplicates: [] } as never);
  const onCreated = jest.fn();
  render(<PosNewCustomerDialog companyId={3} onCreated={onCreated} onClose={jest.fn()} />);
  await userEvent.type(screen.getByLabelText(/^Nombres?/i), 'Rosa');
  await userEvent.type(screen.getByLabelText(/^Apellidos?/i), 'Quispe');
  await userEvent.click(screen.getByRole('button', { name: 'Crear cliente' }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  expect(createCustomer.mock.calls[0][0]).toBe(3);
  expect(onCreated.mock.calls[0][0]).toMatchObject({ id: 7, display_name: 'Rosa Quispe' });
});

it('offers the existing customer when the document already identifies somebody, and uses it on request', async () => {
  const existing = row({ id: 11, display_name: 'Rosa Quispe Ríos' });
  createCustomer.mockRejectedValue(Object.assign(new Error('Ya existe un cliente con ese documento.'), { conflict: existing }));
  const onCreated = jest.fn();
  render(<PosNewCustomerDialog companyId={3} onCreated={onCreated} onClose={jest.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Crear cliente' }));
  const use = await screen.findByRole('button', { name: 'Usar a Rosa Quispe Ríos' });
  expect(onCreated).not.toHaveBeenCalled();
  await userEvent.click(use);
  expect(onCreated).toHaveBeenCalledWith(existing);
});

it('keeps the dialog open and shows the server message when the save fails', async () => {
  createCustomer.mockRejectedValue(new Error('No se pudo crear el cliente.'));
  const onCreated = jest.fn();
  render(<PosNewCustomerDialog companyId={3} onCreated={onCreated} onClose={jest.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Crear cliente' }));
  expect(await screen.findByText('No se pudo crear el cliente.')).toBeInTheDocument();
  expect(onCreated).not.toHaveBeenCalled();
  expect(screen.getByRole('dialog', { name: 'Nuevo cliente' })).toBeInTheDocument();
});
