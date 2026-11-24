/**
 * NAV-SALES-01 — la misma pantalla con otro filtro: el camino no cambia y Next no vuelve a montar la página, así que
 * quien depende de la consulta (la caja con `?panel=equipo`, los pedidos con `?supply=`/`?status=`/`?receipt=`) la
 * escucha. `AddressQuery` avisa de cada consulta nueva, también la primera, una sola vez por cambio.
 */
import { render } from '@testing-library/react';

import { AddressQuery } from '@/app/admin/components/AddressQuery';
import { wantsDevicePanel } from '@/app/admin/lib/pos-panel';

let mockQuery = '';
jest.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(mockQuery),
}));

describe('AddressQuery', () => {
  it('avisa al montar y en cada cambio de la consulta, no en cada render', () => {
    const onChange = jest.fn();
    mockQuery = '';
    const view = render(<AddressQuery onChange={onChange} />);
    expect(onChange.mock.calls).toEqual([['']]);
    view.rerender(<AddressQuery onChange={onChange} />);
    expect(onChange).toHaveBeenCalledTimes(1);
    mockQuery = 'panel=equipo';
    view.rerender(<AddressQuery onChange={onChange} />);
    expect(onChange.mock.calls).toEqual([[''], ['panel=equipo']]);
  });

  it('usa siempre la última función que recibe', () => {
    const first = jest.fn();
    const second = jest.fn();
    mockQuery = 'supply=pending';
    const view = render(<AddressQuery onChange={first} />);
    view.rerender(<AddressQuery onChange={second} />);
    mockQuery = 'status=cancelled';
    view.rerender(<AddressQuery onChange={second} />);
    expect(first.mock.calls).toEqual([['supply=pending']]);
    expect(second.mock.calls).toEqual([['status=cancelled']]);
  });
});

describe('wantsDevicePanel', () => {
  it.each([
    ['panel=equipo', true],
    ['?panel=equipo', true],
    ['x=1&panel=equipo', true],
    ['', false],
    ['panel=servicio', false],
    ['panel=EQUIPO', false],
  ])('%s → %s', (query, expected) => {
    expect(wantsDevicePanel(query)).toBe(expected);
  });
});
