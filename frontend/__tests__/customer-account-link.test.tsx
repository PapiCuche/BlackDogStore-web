import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { CustomerAccountLink } from '@/app/admin/components/CustomerAccountLink';
import { unlinkCustomerAccount } from '@/app/lib/service-console';

/**
 * CUSTOMER-UNLINK-UI · deshacer una vinculación cuenta–cliente desde la ficha.
 *
 * La API existía y no tenía pantalla: una cuenta vinculada por error a la ficha
 * de otro cliente sólo se podía soltar llamando a la API a mano, y mientras tanto
 * el cliente de verdad recibía «ya pertenece a otra cuenta».
 *
 * Desvincular quita acceso; no da ninguno. Volver a vincular sigue pidiendo el
 * enlace de una orden y el documento del cliente. La pantalla pide el motivo
 * porque el servidor lo exige y lo deja en el registro de auditoría.
 */

jest.mock('@/app/lib/service-console', () => ({ unlinkCustomerAccount: jest.fn() }));
const unlink = unlinkCustomerAccount as jest.MockedFunction<typeof unlinkCustomerAccount>;

function show(overrides: Partial<Parameters<typeof CustomerAccountLink>[0]> = {}) {
  const onChanged = jest.fn();
  render(
    <CustomerAccountLink slug="acme" customerId={7} hasAccount canUnlink onChanged={onChanged} {...overrides} />,
  );
  return onChanged;
}

beforeEach(() => {
  unlink.mockReset();
  unlink.mockResolvedValue({ id: 7, has_account: false });
});

it('una ficha sin cuenta lo dice y no ofrece nada que deshacer', () => {
  show({ hasAccount: false });
  expect(screen.getByText('Sin cuenta')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Desvincular cuenta' })).not.toBeInTheDocument();
});

it('quien no puede gestionar clientes ve el estado y ningún botón', () => {
  show({ canUnlink: false });
  expect(screen.getByText('Tiene cuenta en la plataforma')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Desvincular cuenta' })).not.toBeInTheDocument();
});

it('sin empresa elegida no hay a quién pedírselo', () => {
  show({ slug: null });
  expect(screen.queryByRole('button', { name: 'Desvincular cuenta' })).not.toBeInTheDocument();
});

it('pide el motivo antes de dejar desvincular, y dice qué va a pasar', () => {
  show();
  fireEvent.click(screen.getByRole('button', { name: 'Desvincular cuenta' }));

  expect(screen.getByText(/dejará de ver las reparaciones de este cliente/)).toBeInTheDocument();
  expect(screen.getByText(/No se borra la cuenta ni la ficha/)).toBeInTheDocument();
  const confirm = screen.getByRole('button', { name: 'Desvincular' });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: '   ' } });
  expect(confirm).toBeDisabled();
  expect(unlink).not.toHaveBeenCalled();
});

it('desvincula con el motivo escrito y avisa a la ficha para que se vuelva a leer', async () => {
  const onChanged = show();
  fireEvent.click(screen.getByRole('button', { name: 'Desvincular cuenta' }));
  fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: ' La cuenta era de otra persona ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Desvincular' }));

  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  expect(unlink).toHaveBeenCalledWith('acme', 7, 'La cuenta era de otra persona');
});

it('cancelar no llama al servidor', () => {
  show();
  fireEvent.click(screen.getByRole('button', { name: 'Desvincular cuenta' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));

  expect(unlink).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Desvincular cuenta' })).toBeInTheDocument();
});

it('si el servidor lo rechaza, lo dice y deja lo escrito', async () => {
  unlink.mockRejectedValue(new Error('No tienes permiso para esta acción.'));
  const onChanged = show();
  fireEvent.click(screen.getByRole('button', { name: 'Desvincular cuenta' }));
  fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: 'Cuenta equivocada' } });
  fireEvent.click(screen.getByRole('button', { name: 'Desvincular' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permiso para esta acción.');
  expect(onChanged).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Motivo')).toHaveValue('Cuenta equivocada');
});
