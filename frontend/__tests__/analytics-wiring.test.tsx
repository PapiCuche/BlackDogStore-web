import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import fs from 'fs';
import path from 'path';

import { AnalyticsProvider, contactChannel } from '@/app/components/AnalyticsProvider';
import { writeConsent } from '@/app/lib/consent';
import { resetAnalyticsForTests } from '@/app/lib/analytics/service';
import { rememberPaymentReference, takePaymentReference } from '@/app/lib/payment-reference';

/**
 * ANALYTICS-MARKETING · where the shop says its events, and what it keeps out of the address.
 */

let mockPath = '/';
jest.mock('next/navigation', () => ({ usePathname: () => mockPath }));
jest.mock('@/app/components/StorefrontProvider', () => ({
  useStorefront: () => ({ contact: { whatsapp_link: 'https://wa.me/51987654321' } }),
}));

const CONFIG = { providers: { meta: { pixel_id: '123456789012345', purchase: 'both' } } };
const REFERENCE = '12345678901234567890';
const meta = () => ((window.fbq?.queue ?? []) as unknown[][]).filter((call) => call[0] === 'track');

function answer(body: unknown, ok = true) {
  return Promise.resolve({ ok, status: ok ? 200 : 500, json: async () => body } as Response);
}

beforeEach(() => {
  resetAnalyticsForTests();
  window.localStorage.clear();
  window.sessionStorage.clear();
  document.head.querySelectorAll('script').forEach((node) => node.remove());
  delete (window as unknown as Record<string, unknown>).fbq;
  delete (window as unknown as Record<string, unknown>)._fbq;
  mockPath = '/';
  window.history.pushState({}, '', '/');
});

describe('the payment reference stays out of the address', () => {
  it('is kept for the tab and handed to the success page', () => {
    rememberPaymentReference(REFERENCE);
    expect(takePaymentReference()).toBe(REFERENCE);
    expect(window.location.search).toBe('');
  });

  it('an address that still carries one is honoured and cleaned at once', () => {
    window.history.pushState({}, '', `/checkout/success?reference=${REFERENCE}`);
    expect(takePaymentReference()).toBe(REFERENCE);
    expect(window.location.pathname + window.location.search).toBe('/checkout/success');
    expect(takePaymentReference()).toBe(REFERENCE);            // a reload still finds it
  });

  it('something that is not a reference is not kept', () => {
    for (const value of ['', 'a b', '<script>', 'x'.repeat(80)]) {
      rememberPaymentReference(value);
      window.history.pushState({}, '', `/checkout/success?reference=${encodeURIComponent(value)}`);
      expect(takePaymentReference()).toBeNull();
    }
  });

  it('the checkout sends the buyer on without it', () => {
    const checkout = fs.readFileSync(path.join(process.cwd(), 'app/checkout/page.tsx'), 'utf8');
    expect(checkout).toContain('router.push("/checkout/success")');
    expect(checkout).not.toMatch(/success\?reference=/);
  });
});

describe('the provider component', () => {
  it('asks the server what may be measured, without sending a cookie', async () => {
    const fetchMock = jest.fn(() => answer(CONFIG));
    global.fetch = fetchMock as unknown as typeof fetch;
    render(<AnalyticsProvider />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/measurement\/config\/$/);
    expect(init.credentials).toBe('omit');
  });

  it('a server that fails leaves the page unmeasured, not broken', async () => {
    writeConsent({ analytics: true, marketing: true });
    global.fetch = jest.fn(() => Promise.reject(new Error('sin red'))) as unknown as typeof fetch;
    expect(() => render(<AnalyticsProvider />)).not.toThrow();
    await act(async () => { await Promise.resolve(); });
    expect(document.querySelectorAll('script[src]')).toHaveLength(0);
  });

  it('counts a page view per route, and reaching out by WhatsApp, e-mail or phone as contact', async () => {
    writeConsent({ analytics: false, marketing: true });
    global.fetch = jest.fn(() => answer(CONFIG)) as unknown as typeof fetch;
    const { rerender } = render(
      <>
        <AnalyticsProvider />
        <a href="https://wa.me/51987654321?text=Hola">WhatsApp</a>
        <a href="mailto:tienda@example.pe">Correo</a>
        <a href="/product">Catálogo</a>
      </>,
    );
    await waitFor(() => expect(meta().map((call) => call[1])).toEqual(['PageView']));

    fireEvent.click(screen.getByText('WhatsApp'));
    fireEvent.click(screen.getByText('Correo'));
    fireEvent.click(screen.getByText('Catálogo'));
    expect(meta().map((call) => call[1])).toEqual(['PageView', 'Contact', 'Contact']);
    // The number in the link is the shop's, and still: nothing of the link is sent.
    expect(JSON.stringify(meta())).not.toMatch(/51987654321|tienda@example/);

    mockPath = '/product';
    window.history.pushState({}, '', '/product');
    rerender(<><AnalyticsProvider /></>);
    await waitFor(() => expect(meta().filter((call) => call[1] === 'PageView')).toHaveLength(2));
  });

  it('knows a contact link by its scheme and host only', () => {
    expect(contactChannel('https://wa.me/51987654321')).toBe('whatsapp');
    expect(contactChannel('https://api.whatsapp.com/send?phone=51987654321')).toBe('whatsapp');
    expect(contactChannel('mailto:a@example.pe')).toBe('email');
    expect(contactChannel('tel:+51987654321')).toBe('phone');
    for (const other of ['/contact', 'https://wa.me.evil.example/x', 'https://example.pe/?u=https://wa.me/1', 'javascript:alert(1)', '']) {
      expect(contactChannel(other)).toBeNull();
    }
  });
});

describe("the purchase is the server's word, not the page's", () => {
  /* eslint-disable @typescript-eslint/no-require-imports */
  const SuccessPage = require('@/app/checkout/success/page').default;
  const PAID = {
    order_id: 77, status: 'paid', paid: true, total: '4999.00', message: 'Pago confirmado',
    measurement: { event_id: 'purchase.77', transaction_id: '77', value: 4999, currency: 'PEN', tax: 762.56, coupon: '',
                   items: [{ id: '42', name: 'iPhone 15 Pro', price: 4999, quantity: 1 }] },
  };

  function open(status: unknown) {
    writeConsent({ analytics: false, marketing: true });
    global.fetch = jest.fn((url: string) => answer(String(url).includes('/measurement/config/') ? CONFIG : status)) as unknown as typeof fetch;
    rememberPaymentReference(REFERENCE);
    window.history.pushState({}, '', '/checkout/success');
    mockPath = '/checkout/success';
    return render(<><AnalyticsProvider /><SuccessPage /></>);
  }

  it('opening the page with a payment that has not landed measures no purchase', async () => {
    open({ order_id: 77, status: 'failed', paid: false, total: '4999.00', message: 'El pago no pudo procesarse' });
    await waitFor(() => expect(meta().map((call) => call[1])).toEqual(['PageView']));
    await act(async () => { await Promise.resolve(); });
    expect(meta().filter((call) => call[1] === 'Purchase')).toEqual([]);
  });

  it("a paid order without the server's measurement block measures no purchase either", async () => {
    open({ order_id: 77, status: 'paid', paid: true, total: '4999.00', message: 'Pago confirmado' });
    expect(await screen.findByText(/registrada como pagada/)).toBeInTheDocument();
    expect(meta().filter((call) => call[1] === 'Purchase')).toEqual([]);
  });

  it("once the server says it is paid, the purchase is measured once, with the server's id", async () => {
    const first = open(PAID);
    await waitFor(() => expect(meta().filter((call) => call[1] === 'Purchase')).toHaveLength(1));
    const [, , params, options] = meta().find((call) => call[1] === 'Purchase') as [string, string, Record<string, unknown>, { eventID: string }];
    expect(options).toEqual({ eventID: 'purchase.77' });
    expect(params).toMatchObject({ value: 4999, currency: 'PEN', order_id: '77', content_ids: ['42'] });
    // The reference never reached the address, so it never reached the pixel.
    expect(window.location.search).toBe('');

    first.unmount();
    resetAnalyticsForTests();                                   // the buyer reloads the page
    open(PAID);
    await screen.findByText(/registrada como pagada/);
    expect(meta().filter((call) => call[1] === 'Purchase')).toHaveLength(1);
  });
});

describe('links into private addresses are real navigations', () => {
  it('the repairs page opens a tracking link in a new document, where no measurement script is loaded', () => {
    const repairs = fs.readFileSync(path.join(process.cwd(), 'app/repairs/page.tsx'), 'utf8');
    expect(repairs).toMatch(/<a\s+href=\{repair\.tracking_path\}/);
    expect(repairs).not.toMatch(/<Link\s+href=\{repair\.tracking_path\}/);
  });
});
