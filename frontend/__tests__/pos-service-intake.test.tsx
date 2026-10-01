import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { PosServiceIntake } from '@/app/admin/sales/pos/PosServiceIntake';
import { PosModeSwitch, mayOpenServiceFromTill } from '@/app/admin/sales/pos/PosModeSwitch';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * POS-SVC-01. The till opens a service order and names the technician in ONE
 * request, from the candidates the SERVER returns for that branch.
 *
 * A service is not a sale: nothing here touches the basket, a product, a charge
 * or a receipt. The entry is a courtesy — the server checks every request.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

const ORDER = {
  id: 1, number: 'SRV-000001', status: 'repaired', status_label: 'Reparado',
  customer_name: 'Cliente Uno', device_summary: 'Teléfono X', branch_name: 'Centro',
  technician_name: '', reported_issue: 'No enciende', physical_condition: '',
  received_accessories: '', internal_notes: '', received_at: '2026-09-28T10:00:00Z',
  received_by_name: 'Recepción', available_transitions: [], customer_notifications: [],
};

const calls: { path: string; method: string; body: unknown }[] = [];

function mockApi(overrides: (path: string, method: string) => unknown | undefined = () => undefined) {
  calls.length = 0;
  jest.mocked(fetchWithAuth).mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    let body: unknown = overrides(path, method);
    if (body === undefined) {
      body = { count: 0, results: [] };
    }
    return { ok: true, status: 200, json: async () => body } as Response;
  });
}

describe('servicio técnico desde la caja', () => {
  const TILL = ['sales.pos.use', 'service.orders.view', 'service.orders.create',
    'service.customers.view', 'service.devices.view', 'service.orders.assign'];

  it('la entrada sólo aparece para quien puede recibir y asignar', () => {
    expect(mayOpenServiceFromTill(TILL)).toBe(true);
    expect(mayOpenServiceFromTill(TILL.filter((c) => c !== 'service.orders.assign'))).toBe(false);
    expect(mayOpenServiceFromTill([...TILL.filter((c) => c !== 'service.orders.assign'), 'service.orders.manage'])).toBe(true);
    expect(mayOpenServiceFromTill(TILL.filter((c) => c !== 'service.orders.create'))).toBe(false);

    const onChange = jest.fn();
    const { rerender } = render(<PosModeSwitch mode="products" onChange={onChange} capabilities={TILL} />);
    fireEvent.click(screen.getByRole('button', { name: 'Servicio técnico' }));
    expect(onChange).toHaveBeenCalledWith('service');
    rerender(<PosModeSwitch mode="products" onChange={onChange} capabilities={['sales.pos.use']} />);
    expect(screen.queryByRole('button', { name: 'Servicio técnico' })).not.toBeInTheDocument();
  });

  function tillApi() {
    mockApi((path, method) => {
      if (path.includes('/service/context')) return {
        statuses: [], available_branches: [{ id: 11, name: 'Centro' }], device_types: [],
      };
      if (path.includes('/service/customers/')) return { results: [{ id: 3, display_name: 'Ana Cliente' }] };
      if (path.includes('/devices/')) return { results: [{ id: 4, display_name: 'Teléfono X100' }] };
      if (path.includes('/service/technicians/')) return {
        candidates: [{ id: 9, name: 'Ana Técnica' }, { id: 10, name: 'Luis Taller' }],
      };
      if (path.endsWith('/service/orders/') && method === 'POST') return {
        ...ORDER, id: 77, number: 'SRV-000077', status: 'received', technician_name: 'Luis Taller',
      };
      return undefined;
    });
  }

  async function fillUntilTechnician() {
    tillApi();
    await act(async () => {
      render(<PosServiceIntake slug="taller" may={(cap) => TILL.includes(cap)} />);
    });
    await screen.findByText('Nueva orden de servicio');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Buscar cliente' })); });
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox', { name: 'Cliente' }), { target: { value: '3' } });
    });
    await act(async () => {
      fireEvent.change(await screen.findByRole('combobox', { name: 'Equipo' }), { target: { value: '4' } });
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Falla reportada' }), { target: { value: 'Pantalla rota' } });
  }

  it('sólo dice que no hay técnicos cuando el servidor lo ha dicho', async () => {
    const NOBODY = /Ningún técnico alcanza esta sucursal/;
    tillApi();
    const answer = jest.mocked(fetchWithAuth).getMockImplementation()!;
    let release: () => void = () => undefined;
    jest.mocked(fetchWithAuth).mockImplementation(async (input, init) => {
      if (String(input).includes('/service/technicians/')) {
        await new Promise<void>((resolve) => { release = resolve; });
        return { ok: true, status: 200, json: async () => ({ candidates: [] }) } as Response;
      }
      return answer(input, init);
    });
    await act(async () => {
      render(<PosServiceIntake slug="taller" may={(cap) => TILL.includes(cap)} />);
    });
    await screen.findByText('Nueva orden de servicio');
    // Todavía preguntando: «nadie» no es un hecho aún.
    expect(screen.queryByText(NOBODY)).not.toBeInTheDocument();
    await act(async () => { release(); });
    expect(await screen.findByText(NOBODY)).toBeInTheDocument();
  });

  it('ofrece por nombre los técnicos que el servidor devuelve para la sucursal', async () => {
    await fillUntilTechnician();
    const picker = await screen.findByRole('combobox', { name: 'Técnico asignado' });
    await waitFor(() => expect(within(picker).getAllByRole('option').map((o) => o.textContent))
      .toEqual(['Selecciona técnico', 'Ana Técnica', 'Luis Taller']));
    expect(calls.some((c) => c.path.includes('/service/technicians/?branch_id=11'))).toBe(true);
  });

  it('no deja registrar sin técnico, y lo envía en la misma petición', async () => {
    await fillUntilTechnician();
    const submit = screen.getByRole('button', { name: 'Crear servicio' });
    expect(submit).toBeDisabled();

    const picker = await screen.findByRole('combobox', { name: 'Técnico asignado' });
    await waitFor(() => expect(within(picker).getAllByRole('option')).toHaveLength(3));
    fireEvent.change(picker, { target: { value: '10' } });
    expect(submit).toBeEnabled();
    await act(async () => { fireEvent.click(submit); });

    const post = calls.find((c) => c.method === 'POST' && c.path.endsWith('/service/orders/'));
    expect(post?.body).toMatchObject({
      customer_id: 3, device_id: 4, branch_id: 11, reported_issue: 'Pantalla rota', technician_id: 10,
    });
    // Una sola petición crea y asigna: no hay un segundo POST de asignación.
    expect(calls.some((c) => c.method === 'POST' && c.path.endsWith('/assignment/'))).toBe(false);
  });

  it('muestra el número de la orden creada y un enlace para abrirla', async () => {
    await fillUntilTechnician();
    const picker = await screen.findByRole('combobox', { name: 'Técnico asignado' });
    await waitFor(() => expect(within(picker).getAllByRole('option')).toHaveLength(3));
    fireEvent.change(picker, { target: { value: '10' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Crear servicio' })); });

    expect(await screen.findByText(/SRV-000077/)).toBeInTheDocument();
    expect(screen.getByText(/Luis Taller/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Abrir orden' })).toHaveAttribute('href', '/admin/service/orders/77');
  });
});
