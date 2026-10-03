import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { InventoryAdjustForm } from '@/app/admin/components/InventoryAdjustForm';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * DRIFT-02 — el ajuste de inventario dice en qué sucursal se aplica.
 *
 * El servidor acepta `branch` en el ajuste y, si no llega, usa la sucursal por
 * defecto de quien ajusta. La pantalla nunca lo enviaba: en una empresa con
 * varias sucursales el ajuste caía siempre en la sucursal por defecto, sin
 * decirlo y sin poder elegir otra.
 *
 * Se prueba hasta la petición: lo que importa es qué sucursal viaja.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

const mockFetch = fetchWithAuth as jest.Mock;

const CENTRO = { id: 11, name: 'Sucursal Centro' };
const NORTE = { id: 12, name: 'Sucursal Norte' };

function sentBody() {
  expect(mockFetch).toHaveBeenCalledTimes(1);
  const [url, init] = mockFetch.mock.calls[0];
  expect(String(url)).toContain('/admin/products/5/inventory-adjust/');
  return JSON.parse(init.body);
}

async function fillAndSubmit() {
  await userEvent.type(screen.getByLabelText(/Delta/), '4');
  await userEvent.type(screen.getByLabelText('Motivo'), 'Ingreso de proveedor');
  await userEvent.click(screen.getByRole('button', { name: 'Aplicar ajuste' }));
}

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: 5, inventory: 14 }) });
});

describe('ajuste de inventario · sucursal', () => {
  it('con varias sucursales deja elegir, parte de la sucursal por defecto y la envía', async () => {
    render(
      <InventoryAdjustForm
        productId={5}
        currentInventory={10}
        branches={[CENTRO, NORTE]}
        defaultBranchId={NORTE.id}
        onAdjusted={jest.fn()}
      />,
    );

    const select = screen.getByLabelText('Sucursal') as HTMLSelectElement;
    expect(select.value).toBe(String(NORTE.id));

    await fillAndSubmit();
    expect(sentBody()).toEqual({ delta: 4, reason: 'Ingreso de proveedor', branch: NORTE.id });
  });

  it('envía la sucursal que la persona elige', async () => {
    render(
      <InventoryAdjustForm
        productId={5}
        currentInventory={10}
        branches={[CENTRO, NORTE]}
        defaultBranchId={NORTE.id}
        onAdjusted={jest.fn()}
      />,
    );

    await userEvent.selectOptions(screen.getByLabelText('Sucursal'), String(CENTRO.id));
    await fillAndSubmit();
    expect(sentBody().branch).toBe(CENTRO.id);
  });

  it('sin sucursal por defecto no elige por la persona: pide escogerla', async () => {
    render(
      <InventoryAdjustForm
        productId={5}
        currentInventory={10}
        branches={[CENTRO, NORTE]}
        defaultBranchId={null}
        onAdjusted={jest.fn()}
      />,
    );

    await fillAndSubmit();
    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/sucursal/i);
  });

  it('con una sola sucursal no pregunta y no cambia lo que se envía', async () => {
    render(
      <InventoryAdjustForm
        productId={5}
        currentInventory={10}
        branches={[CENTRO]}
        defaultBranchId={CENTRO.id}
        onAdjusted={jest.fn()}
      />,
    );

    expect(screen.queryByLabelText('Sucursal')).not.toBeInTheDocument();
    await fillAndSubmit();
    expect(sentBody()).toEqual({ delta: 4, reason: 'Ingreso de proveedor' });
  });

  it('sin datos de sucursales se comporta como antes', async () => {
    render(<InventoryAdjustForm productId={5} currentInventory={10} onAdjusted={jest.fn()} />);

    expect(screen.queryByLabelText('Sucursal')).not.toBeInTheDocument();
    await fillAndSubmit();
    expect(sentBody()).toEqual({ delta: 4, reason: 'Ingreso de proveedor' });
  });
});
