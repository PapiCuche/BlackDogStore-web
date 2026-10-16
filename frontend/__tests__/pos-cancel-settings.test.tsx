import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { PosCancelSettings } from '@/app/admin/components/PosCancelSettings';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * Cierre financiero (propietario, 2026-10-16) · cuántos días puede anular una
 * venta POS quien cancela pedidos; pasado ese plazo la anula un superior.
 * Se guarda sola (sólo su campo viaja), 0–365, y quien no puede cambiarla la ve.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/api', () => ({ API_BASE: '/api' }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

let patches: Record<string, unknown>[];
let refusal: { status: number; body: unknown } | null;

beforeEach(() => {
  patches = [];
  refusal = null;
  send.mockReset();
  send.mockImplementation(async (_input: RequestInfo | URL, init: RequestInit = {}) => {
    const body = JSON.parse(String(init.body));
    patches.push(body);
    if (refusal) return { ok: false, status: refusal.status, json: async () => refusal!.body } as Response;
    return { ok: true, status: 200, json: async () => ({ settings: body }) } as Response;
  });
});

function shown(props: Partial<React.ComponentProps<typeof PosCancelSettings>> = {}) {
  const onSaved = jest.fn();
  render(<PosCancelSettings companyId={7} days={7} canEdit onSaved={onSaved} {...props} />);
  return onSaved;
}

it('guarda sólo su campo, como número entero', async () => {
  const onSaved = shown();
  const field = screen.getByLabelText('Anulación simple de ventas POS (días)');
  expect(field).toHaveValue(7);
  fireEvent.change(field, { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar plazo de anulación' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(patches).toEqual([{ pos_cancel_simple_days: 0 }]);
  expect(screen.getByRole('status')).toHaveTextContent('Cambios guardados.');
});

it('fuera de 0–365 no se envía', () => {
  shown();
  const field = screen.getByLabelText('Anulación simple de ventas POS (días)');
  for (const value of ['366', '-1', '2.5', '']) {
    fireEvent.change(field, { target: { value } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar plazo de anulación' }));
  }
  expect(patches).toEqual([]);
  expect(screen.getByText('Indica un número entero de días entre 0 y 365.')).toBeInTheDocument();
});

it('el error del servidor se dice bajo el campo', async () => {
  refusal = { status: 400, body: { pos_cancel_simple_days: ['Debe estar entre 0 y 365 días.'] } };
  shown();
  fireEvent.click(screen.getByRole('button', { name: 'Guardar plazo de anulación' }));
  expect(await screen.findByText('Debe estar entre 0 y 365 días.')).toBeInTheDocument();
});

it('sin autoridad se ve pero no se cambia', () => {
  shown({ canEdit: false });
  expect(screen.getByLabelText('Anulación simple de ventas POS (días)')).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Guardar plazo de anulación' })).toBeNull();
  expect(screen.getByText(/settings\.manage/)).toBeInTheDocument();
});
