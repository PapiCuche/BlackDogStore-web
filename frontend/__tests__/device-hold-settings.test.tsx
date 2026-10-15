import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { DeviceHoldSettings } from '@/app/admin/components/DeviceHoldSettings';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * DEVICE-COMMERCE · how long a device stays set aside for a buyer, and whether
 * a web buyer may ask the supplier for it although the shelf has it.
 *
 * Saved on their own (only their four fields travel), read-only for whoever
 * the server does not let change them, and the server's field errors are said
 * under the field they belong to.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/api', () => ({ API_BASE: '/api' }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

let patches: { url: string; body: Record<string, unknown> }[];
let refusal: { status: number; body: unknown } | null;

beforeEach(() => {
  patches = [];
  refusal = null;
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const body = JSON.parse(String(init.body));
    patches.push({ url: String(input), body });
    if (refusal) return { ok: false, status: refusal.status, json: async () => refusal!.body } as Response;
    return { ok: true, status: 200, json: async () => ({ settings: body, can_manage_order_review: true }) } as Response;
  });
});

function shown(props: Partial<React.ComponentProps<typeof DeviceHoldSettings>> = {}) {
  const onSaved = jest.fn();
  render(
    <DeviceHoldSettings
      companyId={7}
      cartMinutes={15}
      checkoutMinutes={45}
      ceilingMinutes={60}
      overStock={false}
      canEdit
      onSaved={onSaved}
      {...props}
    />,
  );
  return onSaved;
}

const cart = () => screen.getByLabelText('Carrito (minutos)');
const checkout = () => screen.getByLabelText('Checkout / pago (minutos)');
const ceiling = () => screen.getByLabelText('Tope total (minutos)');
const overStock = () =>
  screen.getByRole('checkbox', { name: 'Permitir que el comprador web elija proveedor aunque haya stock' });
const save = () => fireEvent.click(screen.getByRole('button', { name: 'Guardar apartados y venta bajo pedido' }));

test('shows the current values, their bounds and what the clocks never release', () => {
  shown();
  expect(cart()).toHaveValue(15);
  expect(checkout()).toHaveValue(45);
  expect(ceiling()).toHaveValue(60);
  expect(overStock()).not.toBeChecked();
  expect(screen.getByText(/Entre 5 y 60/)).toBeInTheDocument();
  expect(screen.getByText(/Entre 30 y 120/)).toBeInTheDocument();
  expect(screen.getByText(/Entre 35 y 240/)).toBeInTheDocument();
  expect(screen.getByText(/pago aún lo está confirmando la pasarela/)).toBeInTheDocument();
  expect(screen.getByText(/Apagado por defecto/)).toBeInTheDocument();
  expect(screen.getByText(/se despacha desde el proveedor/)).toBeInTheDocument();
});

test('saving sends the four fields as numbers and a boolean, to this company', async () => {
  const onSaved = shown();
  fireEvent.change(cart(), { target: { value: '20' } });
  fireEvent.change(checkout(), { target: { value: '60' } });
  fireEvent.change(ceiling(), { target: { value: '90' } });
  fireEvent.click(overStock());
  save();

  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(patches).toEqual([{
    url: '/api/admin/company-settings/?company=7',
    body: {
      device_cart_hold_minutes: 20,
      device_checkout_hold_minutes: 60,
      device_hold_ceiling_minutes: 90,
      web_on_order_over_stock: true,
    },
  }]);
  expect(await screen.findByText('Cambios guardados.')).toBeInTheDocument();
  expect(overStock()).toBeChecked();
  expect(ceiling()).toHaveValue(90);
});

test('whoever may not change them reads them and is told what it takes', () => {
  shown({ canEdit: false, overStock: true });
  expect(cart()).toBeDisabled();
  expect(checkout()).toBeDisabled();
  expect(ceiling()).toBeDisabled();
  expect(overStock()).toBeDisabled();
  expect(overStock()).toBeChecked();
  expect(screen.queryByRole('button', { name: 'Guardar apartados y venta bajo pedido' })).not.toBeInTheDocument();
  expect(screen.getByText('settings.manage')).toBeInTheDocument();
});

test('field errors from the server are said under their field', async () => {
  refusal = {
    status: 400,
    body: {
      device_checkout_hold_minutes: ['Asegúrese de que este valor es mayor o igual a 30.'],
      device_hold_ceiling_minutes: ['El tope debe ser al menos el carrito más 30 minutos.'],
    },
  };
  const onSaved = shown();
  fireEvent.change(checkout(), { target: { value: '10' } });
  save();
  expect(await screen.findByText('Asegúrese de que este valor es mayor o igual a 30.')).toBeInTheDocument();
  expect(screen.getByText('El tope debe ser al menos el carrito más 30 minutos.')).toBeInTheDocument();
  expect(checkout()).toHaveAttribute('aria-invalid', 'true');
  expect(ceiling()).toHaveAttribute('aria-invalid', 'true');
  expect(cart()).toHaveAttribute('aria-invalid', 'false');
  expect(onSaved).not.toHaveBeenCalled();
});

test('a refusal for lack of permission shows the server\'s reason', async () => {
  refusal = { status: 403, body: { detail: 'No tienes permisos para cambiar estos ajustes.' } };
  const onSaved = shown();
  fireEvent.click(overStock());
  save();
  expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permisos para cambiar estos ajustes.');
  expect(onSaved).not.toHaveBeenCalled();
});

test('an empty minutes box is not sent as zero: it is refused before saving', async () => {
  shown();
  fireEvent.change(cart(), { target: { value: '' } });
  save();
  expect(await screen.findByText('Indica un número entero de minutos.')).toBeInTheDocument();
  expect(patches).toEqual([]);
});
