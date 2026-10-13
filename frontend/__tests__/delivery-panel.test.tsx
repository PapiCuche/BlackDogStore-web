import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CarrierSettings } from '@/app/admin/components/CarrierSettings';
import { OrderDeliveryCard } from '@/app/admin/components/OrderDeliveryCard';
import { safeTrackingUrl, type OrderDelivery } from '@/app/lib/delivery';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * DELIVERY-01 · the panel: a company's carriers, and the «Entrega» card of an order.
 *
 * Who may change what is the server's answer. The card writes the delivery
 * record and nothing of the order; the freight is information.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn(), SELECTED_COMPANY_KEY: 'internal-selected-company' }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const AGENCY = { id: 7, name: 'Agencia Uno', kind: 'agency', kind_label: 'Recojo en agencia', tracking_url_template: 'https://rastreo.ejemplo.test/{guia}', is_active: true };
const COURIER = { id: 8, name: 'A domicilio', kind: 'courier', kind_label: 'Entrega a domicilio', tracking_url_template: '', is_active: false };

let carriers: (typeof AGENCY)[];
let canManage: boolean;
let refusal: { status: number; body: unknown } | null;
let calls: { method: string; path: string; body: unknown }[];
let saved: Partial<OrderDelivery>;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function delivery(overrides: Partial<OrderDelivery> = {}): OrderDelivery {
  return {
    exists: true, can_edit: true, mode: 'national_address', mode_label: 'Envío nacional a domicilio',
    allowed_modes: ['national_address', 'agency_pickup'],
    recipient_name: 'Ana Torres', recipient_document_type: 'dni', recipient_document_number: '70000001', recipient_phone: '987654321',
    address_line: 'Av. Siempre Viva 123', district: 'Juliaca', city: 'Puno', department: '', reference: '',
    carrier: null, carrier_name: '', agency_name: '', agency_locality: '', freight_amount: null, freight_paid_by: '',
    waybill_number: '', tracking_code: '', tracking_url: '', tracking_url_typed: false, shipped_at: null, updated_by: null, updated_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  carriers = [AGENCY, COURIER];
  canManage = true;
  refusal = null;
  calls = [];
  saved = {};
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = init.method ?? 'GET';
    const path = String(input).replace(/^.*\/admin\//, '/');
    if (method === 'GET') return reply(200, { results: carriers, can_manage: canManage });
    const body = JSON.parse(String(init.body));
    calls.push({ method, path, body });
    if (refusal) return reply(refusal.status, refusal.body);
    if (path.includes('/delivery/')) return reply(200, delivery({ ...saved, ...body, carrier_name: body.carrier === 7 ? 'Agencia Uno' : '' }));
    if (method === 'POST') { carriers = [...carriers, { ...AGENCY, ...body, id: 9, kind_label: 'Recojo en agencia' }]; return reply(201, carriers[2]); }
    carriers = carriers.map((c) => (path === `/carriers/${c.id}/` ? { ...c, ...body } : c));
    return reply(200, carriers[0]);
  });
});

describe('«Transportistas»', () => {
  async function opened() {
    render(<CarrierSettings />);
    await screen.findAllByTestId('carrier-row');
  }

  it('lists the company\'s own carriers, the switched-off ones too', async () => {
    await opened();
    const [agency, courier] = screen.getAllByTestId('carrier-row');
    expect(within(agency).getByText('Agencia Uno')).toBeInTheDocument();
    expect(within(agency).getByText('Recojo en agencia · con seguimiento')).toBeInTheDocument();
    expect(within(courier).getByText('Inactivo')).toBeInTheDocument();
  });

  it('nothing is there until somebody adds it', async () => {
    carriers = [];
    render(<CarrierSettings />);
    expect(await screen.findByText('Todavía no registraste ningún transportista.')).toBeInTheDocument();
    expect(send).toHaveBeenCalledTimes(1);                      // read once; nothing was created by looking
  });

  it('a suggestion only fills the name: nothing is saved until the form is', async () => {
    await opened();
    fireEvent.click(screen.getByRole('button', { name: 'Shalom' }));
    expect(screen.getByLabelText('Nombre')).toHaveValue('Shalom');
    expect(calls).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Olva Courier' }));
    expect(screen.getByLabelText('Nombre')).toHaveValue('Olva Courier');

    fireEvent.change(screen.getByLabelText('Dirección de seguimiento (opcional)'), { target: { value: ' https://rastreo.ejemplo.test/{guia} ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Agregar transportista' }));
    await waitFor(() => expect(screen.getAllByTestId('carrier-row')).toHaveLength(3));
    expect(calls).toEqual([{ method: 'POST', path: '/carriers/', body: {
      name: 'Olva Courier', kind: 'agency', tracking_url_template: 'https://rastreo.ejemplo.test/{guia}',
    } }]);
    expect(screen.getByLabelText('Nombre')).toHaveValue('');
  });

  it('a carrier is switched off and on, never deleted', async () => {
    await opened();
    expect(screen.queryByRole('button', { name: /Eliminar|Borrar/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Desactivar Agencia Uno' }));
    await waitFor(() => expect(calls).toEqual([{ method: 'PATCH', path: '/carriers/7/', body: { is_active: false } }]));
    fireEvent.click(await screen.findByRole('button', { name: 'Activar A domicilio' }));
    await waitFor(() => expect(calls[1]).toEqual({ method: 'PATCH', path: '/carriers/8/', body: { is_active: true } }));
  });

  it('what the server refuses is said under its field', async () => {
    refusal = { status: 400, body: { tracking_url_template: ['Escribe una dirección que empiece por https://.'] } };
    await opened();
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Otra' } });
    fireEvent.change(screen.getByLabelText('Dirección de seguimiento (opcional)'), { target: { value: 'javascript:alert(1)' } });
    fireEvent.click(screen.getByRole('button', { name: 'Agregar transportista' }));
    expect(await screen.findByText('Escribe una dirección que empiece por https://.')).toBeInTheDocument();
    expect(screen.getByLabelText('Dirección de seguimiento (opcional)')).toHaveAttribute('aria-invalid', 'true');
  });

  it('whoever may not change the list reads it and is told what it takes', async () => {
    canManage = false;
    await opened();
    expect(screen.queryByRole('form', { name: 'Transportista' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Desactivar Agencia Uno' })).not.toBeInTheDocument();
    expect(screen.getByText('settings.manage')).toBeInTheDocument();
  });
});

describe('«Entrega» of an order', () => {
  function shown(d: OrderDelivery | null, onSaved = jest.fn()) {
    render(<OrderDeliveryCard orderId={42} delivery={d} onSaved={onSaved} />);
    return onSaved;
  }

  it('shows nothing to whoever may not see orders, nor for a till sale', () => {
    const { container } = render(<OrderDeliveryCard orderId={42} delivery={null} onSaved={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
    const till = render(<OrderDeliveryCard orderId={42} delivery={delivery({ exists: false, can_edit: false, mode: '', allowed_modes: [] })} onSaved={jest.fn()} />);
    expect(till.container).toBeEmptyDOMElement();
  });

  it('shows the record: who receives, where it goes, the carrier and the waybill', () => {
    shown(delivery({
      can_edit: false, mode: 'agency_pickup', mode_label: 'Recojo en agencia', carrier: 7, carrier_name: 'Agencia Uno',
      agency_name: 'Agencia Centro', agency_locality: 'Juliaca', freight_amount: '15.50', freight_paid_by: 'customer_at_destination',
      waybill_number: 'G-100', tracking_url: 'https://rastreo.ejemplo.test/G-100', updated_by: 'Gema Gestora',
    }));
    expect(screen.getByRole('heading', { name: 'Entrega' })).toBeInTheDocument();
    expect(screen.getByText('Recojo en agencia')).toBeInTheDocument();
    expect(screen.getByText('Agencia Centro · Juliaca')).toBeInTheDocument();
    expect(screen.getByText('S/ 15.50 · paga: El cliente, al recibir')).toBeInTheDocument();
    expect(screen.getByText('Flete (informativo)')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Abrir seguimiento' });
    expect(link).toHaveAttribute('href', 'https://rastreo.ejemplo.test/G-100');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.queryByRole('button', { name: 'Editar entrega' })).not.toBeInTheDocument();     // the server said no
  });

  it('never makes a link of an address that is not https', () => {
    for (const url of ['http://rastreo.ejemplo.test/G-100', 'javascript:alert(1)', '//rastreo.ejemplo.test', ' https://x.test']) {
      expect(safeTrackingUrl(url)).toBe('');
    }
    expect(safeTrackingUrl('https://rastreo.ejemplo.test/G-100')).toBe('https://rastreo.ejemplo.test/G-100');
    shown(delivery({ tracking_url: 'javascript:alert(1)', waybill_number: 'G-100' }));
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('saving sends only what changed, to this order, and says the freight is information', async () => {
    const onSaved = shown(delivery());
    fireEvent.click(screen.getByRole('button', { name: 'Editar entrega' }));
    expect(screen.getByText(/no cambia el total del pedido ni su estado de despacho/)).toBeInTheDocument();
    // Only the modes the server allows, and only carriers that can be chosen today.
    const modes = Array.from(screen.getByLabelText('Modalidad').querySelectorAll('option')).map((o) => o.getAttribute('value'));
    expect(modes).toEqual(['national_address', 'agency_pickup']);
    await waitFor(() => expect(screen.getByLabelText('Transportista').querySelectorAll('option')).toHaveLength(2));

    fireEvent.change(screen.getByLabelText('Modalidad'), { target: { value: 'agency_pickup' } });
    fireEvent.change(screen.getByLabelText('Transportista'), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText('Número de guía'), { target: { value: ' G-100 ' } });
    fireEvent.change(screen.getByLabelText('Flete (S/, informativo)'), { target: { value: '15,50' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar entrega' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(calls).toEqual([{ method: 'PATCH', path: '/orders/42/delivery/', body: {
      mode: 'agency_pickup', carrier: 7, waybill_number: 'G-100', freight_amount: '15.50',
    } }]);
    expect(onSaved.mock.calls[0][0].carrier_name).toBe('Agencia Uno');
    expect(await screen.findByText('Entrega guardada.')).toBeInTheDocument();
  });

  it('an order that can only stay as it is does not offer another mode', () => {
    shown(delivery({ mode: 'pickup_store', mode_label: 'Recojo en tienda', allowed_modes: ['pickup_store'] }));
    fireEvent.click(screen.getByRole('button', { name: 'Editar entrega' }));
    expect(screen.getByLabelText('Modalidad')).toBeDisabled();
  });

  it('saving with nothing changed asks the server for nothing', () => {
    const onSaved = shown(delivery());
    fireEvent.click(screen.getByRole('button', { name: 'Editar entrega' }));
    fireEvent.click(screen.getByRole('button', { name: 'Guardar entrega' }));
    expect(calls).toEqual([]);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('what the server refuses is said next to its field and nothing is called saved', async () => {
    refusal = { status: 400, body: { carrier: ['Para un recojo en agencia elige un transportista con agencias.'] } };
    const onSaved = shown(delivery());
    fireEvent.click(screen.getByRole('button', { name: 'Editar entrega' }));
    fireEvent.change(screen.getByLabelText('Modalidad'), { target: { value: 'agency_pickup' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar entrega' }));
    expect(await screen.findByText('Para un recojo en agencia elige un transportista con agencias.')).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.queryByText('Entrega guardada.')).not.toBeInTheDocument();
  });

  it('an online order without a record offers to make one', () => {
    shown(delivery({ exists: false, mode: '', mode_label: '', recipient_name: '', address_line: '', district: '', city: '',
                     recipient_phone: '', recipient_document_type: '', recipient_document_number: '' }));
    expect(screen.getByText('Este pedido todavía no tiene un registro de entrega.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Registrar entrega' })).toBeInTheDocument();
  });
});
