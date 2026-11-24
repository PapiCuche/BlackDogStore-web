/**
 * NAV-SALES-01 (revisión) — una entrada del menú que abre una pantalla en una parte suya (`?panel=equipo` en la caja,
 * `?seccion=colores` en Modelos) es una PETICIÓN:
 *
 *   - se atiende una vez y la dirección vuelve a la pantalla sin el parámetro, así que el segundo clic en la misma
 *     entrada es otra petición (antes la consulta no cambiaba y no pasaba nada);
 *   - lo atendido se cuenta junto a la petición, fuera de lo que se vuelve a montar: si la caja se monta de nuevo
 *     (cambio de empresa) el bloque no le quita el foco al escáner.
 */
import { act, render, screen } from '@testing-library/react';

import { AddressQuery } from '@/app/admin/components/AddressQuery';
import { useMenuRequest } from '@/app/admin/lib/use-menu-request';
import { readDevicePanel } from '@/app/admin/lib/pos-panel';
import { PosDevicePicker } from '@/app/admin/sales/pos/PosDevicePicker';

let mockQuery = '';
const mockReplace = jest.fn();
const mockRouter = { replace: mockReplace, push: jest.fn() };
jest.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(mockQuery),
  useRouter: () => mockRouter,
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  posModelSearch: jest.fn().mockResolvedValue([]),
  fetchPosModelConfigurations: jest.fn(),
  posReceiveDevice: jest.fn(),
}));

beforeAll(() => { Element.prototype.scrollIntoView = jest.fn(); });
afterAll(() => { delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView; });
beforeEach(() => { mockReplace.mockReset(); mockQuery = ''; });

/** The till as the page builds it: the request lives in the page, the block may be mounted again. */
function Till({ mount }: { mount: number }) {
  const device = useMenuRequest(readDevicePanel, '/admin/sales/pos');
  return (
    <>
      <AddressQuery onChange={device.onQuery} />
      <input aria-label="Escanear" />
      <PosDevicePicker
        key={mount}
        companyId={3}
        branch={8}
        onAddProduct={jest.fn()}
        onAddOnOrder={jest.fn()}
        onReceived={jest.fn()}
        focusRequested={device.pending !== null}
        onFocusHandled={device.done}
      />
    </>
  );
}

const model = () => screen.getByLabelText('Modelo');
const scan = () => screen.getByLabelText('Escanear');

it('el segundo clic en «Venta rápida de equipo» vuelve a llevar a la búsqueda del modelo', () => {
  mockQuery = 'panel=equipo';
  const view = render(<Till mount={1} />);
  expect(model()).toHaveFocus();
  expect(mockReplace).toHaveBeenCalledTimes(1);
  expect(mockReplace).toHaveBeenCalledWith('/admin/sales/pos', { scroll: false });

  // La dirección ya no lleva el parámetro; el vendedor vuelve al escáner.
  mockQuery = '';
  view.rerender(<Till mount={1} />);
  act(() => scan().focus());

  // Segundo clic en la misma entrada.
  mockQuery = 'panel=equipo';
  view.rerender(<Till mount={1} />);
  expect(model()).toHaveFocus();
  expect(mockReplace).toHaveBeenCalledTimes(2);
});

it('si la caja se monta de nuevo (cambio de empresa), el bloque no le quita el foco al escáner', () => {
  mockQuery = 'panel=equipo';
  const view = render(<Till mount={1} />);
  expect(model()).toHaveFocus();
  mockQuery = '';
  view.rerender(<Till mount={1} />);
  act(() => scan().focus());

  view.rerender(<Till mount={2} />);
  expect(scan()).toHaveFocus();
  expect(screen.getByRole('button', { name: /Vender equipo por modelo/ })).toHaveAttribute('aria-expanded', 'false');
  expect(mockReplace).toHaveBeenCalledTimes(1);
});

it('una consulta que no es una petición no hace nada', () => {
  mockQuery = 'panel=otro';
  render(<Till mount={1} />);
  expect(model()).not.toHaveFocus();
  expect(mockReplace).not.toHaveBeenCalled();
});
