import { fireEvent, render, screen, waitFor } from '@testing-library/react';

/**
 * El selector de estado de despacho — H4.1.2 (RBAC-LEGACY-01).
 *
 * EL DEFECTO. El componente decidía las opciones con `user.role === "inventory"`:
 * una segunda copia de la regla, guardada en el navegador y atada al rol GLOBAL.
 * Dentro de una empresa manda la capability, así que alguien con perfil
 * «inventory» a quien su empresa dio `sales.orders.manage` veía cuatro estados
 * donde el servidor le permitía siete.
 *
 * Lo que se fija: las opciones son EXACTAMENTE las que dice el servidor, y el
 * componente ya no recibe al usuario — no tiene de dónde leer un rol.
 */

const mockUpdate = jest.fn();
jest.mock('@/app/lib/admin', () => {
  const actual = jest.requireActual('@/app/lib/admin');
  return {
    ...actual,
    updateOrderFulfillment: (...args: unknown[]) => mockUpdate(...args),
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { FulfillmentStatusSelect } = require('@/app/admin/components/FulfillmentStatusSelect');

const ALL = ['pending', 'confirmed', 'preparing', 'ready_for_pickup', 'shipped', 'delivered', 'cancelled'];

function optionsOf(container: HTMLElement) {
  return Array.from(container.querySelectorAll('option')).map((o) => ({
    value: o.value,
    disabled: o.disabled,
  }));
}

beforeEach(() => {
  mockUpdate.mockReset();
  mockUpdate.mockResolvedValue({});
});

describe('FulfillmentStatusSelect · las opciones las decide el servidor', () => {
  it('con los siete estados del servidor, ofrece los siete', () => {
    const { container } = render(
      <FulfillmentStatusSelect orderId={1} current="pending" allowed={ALL} onChanged={jest.fn()} />,
    );

    expect(optionsOf(container).map((o) => o.value)).toEqual(ALL);
    expect(optionsOf(container).every((o) => !o.disabled)).toBe(true);
  });

  it('con la regla legacy de inventario, ofrece esos cuatro y el actual queda visible pero no elegible', () => {
    const legacyInventory = ['preparing', 'ready_for_pickup', 'shipped', 'delivered'];
    const { container } = render(
      <FulfillmentStatusSelect orderId={1} current="pending" allowed={legacyInventory} onChanged={jest.fn()} />,
    );

    expect(optionsOf(container)).toEqual([
      { value: 'pending', disabled: true },
      { value: 'preparing', disabled: false },
      { value: 'ready_for_pickup', disabled: false },
      { value: 'shipped', disabled: false },
      { value: 'delivered', disabled: false },
    ]);
    expect(optionsOf(container).some((o) => o.value === 'cancelled')).toBe(false);
  });

  it('guarda el estado elegido contra el pedido', async () => {
    const onChanged = jest.fn();
    render(
      <FulfillmentStatusSelect orderId={42} current="pending" allowed={ALL} onChanged={onChanged} />,
    );

    fireEvent.change(screen.getByLabelText('Estado de despacho'), { target: { value: 'cancelled' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledWith('cancelled'));
    expect(mockUpdate).toHaveBeenCalledWith(42, 'cancelled', '');
  });
});
