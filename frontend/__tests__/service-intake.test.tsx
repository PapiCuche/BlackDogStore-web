import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ServiceIntake } from '@/app/admin/service/components/ServiceIntake';
import { fetchWithAuth } from '@/app/lib/auth';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

it('receives an existing customer device and opens the created order', async () => {
  const opened = jest.fn();
  jest.mocked(fetchWithAuth).mockImplementation(async (input, init) => {
    const url = String(input);
    let body: unknown;
    if (url.includes('/customers/')) body = { results: [{ id: 5, display_name: 'Cliente Uno' }] };
    else if (url.includes('/devices/')) body = { results: [{ id: 9, display_name: 'Equipo Uno' }] };
    else {
      expect(JSON.parse(String(init?.body))).toMatchObject({ customer_id: 5, device_id: 9, branch_id: 2, reported_issue: 'No enciende' });
      body = { id: 42 };
    }
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  render(<ServiceIntake slug="taller" context={{ statuses: [], available_branches: [{ id: 2, name: 'Centro' }], device_types: [] }}
    may={() => true} onCreated={opened} />);
  await userEvent.click(screen.getByRole('button', { name: 'Buscar cliente' }));
  await userEvent.selectOptions(await screen.findByLabelText('Cliente'), '5');
  await userEvent.selectOptions(await screen.findByLabelText('Equipo'), '9');
  await userEvent.type(screen.getByLabelText('Falla reportada'), 'No enciende');
  await userEvent.click(screen.getByRole('button', { name: 'Registrar recepción' }));
  expect(await screen.findByText('Orden creada.')).toBeInTheDocument();
  expect(opened).toHaveBeenCalledWith(42);
});
