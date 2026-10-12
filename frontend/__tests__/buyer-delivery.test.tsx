import fs from 'fs';
import path from 'path';

import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { OrderDeliveryInfo } from '@/app/components/OrderDeliveryInfo';
import { SavedAddressSelect } from '@/app/components/SavedAddressSelect';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * DELIVERY-01 · what the buyer sees of a delivery, and the saved-address
 * select of the checkout.
 *
 * A tracking address is a link only when it is https. And choosing a saved
 * address only fills the form in the browser: nothing new is sent to the server.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const DELIVERY = {
  carrier_name: 'Agencia Uno', agency_name: 'Agencia Centro', agency_locality: 'Juliaca, Puno',
  waybill_number: 'G-100', tracking_url: 'https://rastreo.ejemplo.test/g/G-100',
};

describe('the delivery of my order', () => {
  it('says the carrier, the agency, the waybill and where to follow it', () => {
    render(<OrderDeliveryInfo delivery={DELIVERY} />);
    expect(screen.getByText('Agencia Uno')).toBeInTheDocument();
    expect(screen.getByText('Agencia Centro · Juliaca, Puno')).toBeInTheDocument();
    expect(screen.getByText('G-100')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Seguir mi envío' });
    expect(link).toHaveAttribute('href', 'https://rastreo.ejemplo.test/g/G-100');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it.each(['http://rastreo.ejemplo.test/G-100', 'javascript:alert(1)', '//rastreo.ejemplo.test/G-100', ' https://x.test', 'data:text/html,x', ''])(
    'never makes a link of %j', (url) => {
      render(<OrderDeliveryInfo delivery={{ ...DELIVERY, tracking_url: url }} />);
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
      expect(screen.getByText('G-100')).toBeInTheDocument();          // the rest is still said
    },
  );

  it('says nothing when there is nothing to say', () => {
    const none = render(<OrderDeliveryInfo delivery={null} />);
    expect(none.container).toBeEmptyDOMElement();
    const blank = render(<OrderDeliveryInfo delivery={{ carrier_name: '', agency_name: '', agency_locality: '', waybill_number: '', tracking_url: '' }} />);
    expect(blank.container).toBeEmptyDOMElement();
  });

  it('is shown on the page of my orders', () => {
    const source = fs.readFileSync(path.join(process.cwd(), 'app/orders/page.tsx'), 'utf8');
    expect(source).toMatch(/<OrderDeliveryInfo delivery=\{order\.delivery\} \/>/);
  });
});

describe('«Usar una dirección guardada» at checkout', () => {
  const HOME = {
    id: 1, label: 'Casa', kind: 'address', recipient_name: 'Ana Torres', recipient_document_type: '', recipient_document_number: '',
    recipient_phone: '987654321', address_line: 'Av. Siempre Viva 123', district: 'Cercado', city: 'Arequipa', department: '',
    reference: 'Frente al parque', carrier: null, carrier_name: '', agency_name: '', agency_locality: '', is_default: false,
  };
  const WORK = { ...HOME, id: 2, label: '', address_line: 'Calle Dos 45', district: 'Yanahuara', reference: '', is_default: true };
  const OFFICE = { ...HOME, id: 3, label: 'Agencia', kind: 'agency', address_line: '', district: '', city: '', carrier: 7, carrier_name: 'Agencia Uno', agency_name: 'Centro', agency_locality: 'Puno' };

  let results: unknown[];

  beforeEach(() => {
    results = [HOME, WORK, OFFICE];
    send.mockReset();
    send.mockImplementation(async () => ({ ok: true, status: 200, json: async () => ({ results, limit: 10, agency_carriers: [] }) }) as Response);
  });

  it('a guest has no book: nothing is asked and nothing is shown', () => {
    const { container } = render(<SavedAddressSelect enabled={false} isEmpty onUse={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
    expect(send).not.toHaveBeenCalled();
  });

  it('lists the doors of the book, not the agencies', async () => {
    render(<SavedAddressSelect enabled isEmpty={false} onUse={jest.fn()} />);
    const select = await screen.findByLabelText('Usar una dirección guardada');
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    expect(options).toEqual(['Escribir otra dirección', 'Casa — Av. Siempre Viva 123, Cercado, Arequipa', 'Calle Dos 45, Yanahuara, Arequipa']);
  });

  it('into an empty form, the default one is preselected and fills it', async () => {
    const onUse = jest.fn();
    render(<SavedAddressSelect enabled isEmpty onUse={onUse} />);
    await waitFor(() => expect(onUse).toHaveBeenCalledTimes(1));
    expect(onUse.mock.calls[0][0]).toMatchObject({ id: 2, address_line: 'Calle Dos 45', district: 'Yanahuara' });
    expect(screen.getByLabelText('Usar una dirección guardada')).toHaveValue('2');
  });

  it('a form somebody already typed in is left alone', async () => {
    const onUse = jest.fn();
    render(<SavedAddressSelect enabled isEmpty={false} onUse={onUse} />);
    expect(await screen.findByLabelText('Usar una dirección guardada')).toHaveValue('');
    expect(onUse).not.toHaveBeenCalled();
  });

  it('choosing one fills the form with it; choosing «another» changes nothing', async () => {
    const onUse = jest.fn();
    render(<SavedAddressSelect enabled isEmpty={false} onUse={onUse} />);
    const select = await screen.findByLabelText('Usar una dirección guardada');
    fireEvent.change(select, { target: { value: '1' } });
    expect(onUse).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1, address_line: 'Av. Siempre Viva 123', reference: 'Frente al parque' }));
    fireEvent.change(select, { target: { value: '' } });
    expect(onUse).toHaveBeenCalledTimes(1);
  });

  it('a book with no doors, or one that cannot be read, never gets in the way', async () => {
    results = [OFFICE];
    const first = render(<SavedAddressSelect enabled isEmpty onUse={jest.fn()} />);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(first.container).toBeEmptyDOMElement();

    send.mockRejectedValue(new Error('sin red'));
    const second = render(<SavedAddressSelect enabled isEmpty onUse={jest.fn()} />);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(second.container).toBeEmptyDOMElement();
  });

  it('only fills fields the checkout form already had: what is sent to the server is unchanged', () => {
    const source = fs.readFileSync(path.join(process.cwd(), 'app/checkout/page.tsx'), 'utf8');
    const wiring = source.slice(source.indexOf('<SavedAddressSelect'), source.indexOf('/>', source.indexOf('<SavedAddressSelect')));
    expect(wiring.match(/field: "(\w+)"/g)).toEqual(['field: "address_line"', 'field: "district"', 'field: "city"', 'field: "reference"']);
    // No address id, no carrier and no recipient reach the request the page builds.
    expect(source).not.toMatch(/address_id|source_address|saved_address|carrier|recipient_/);
  });
});
