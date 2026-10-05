import { render, screen } from '@testing-library/react';

import KryptonPaymentForm from '@/app/components/KryptonPaymentForm';
import {
  KRYPTON_ASSETS,
  isPaymentSession,
  mountKryptonForm,
  type MiCuentaWebSession,
} from '@/app/lib/payments';

/**
 * PAGOS · un proveedor por instalación, y ninguno decide desde el navegador.
 *
 * Izipay vende dos productos («SDK web / Checkout» y «Mi Cuenta Web»). El
 * servidor dice cuál abrió el pago; el navegador dibuja ESE formulario, con un
 * guion cuya dirección es una constante de este código, y cuando el formulario
 * termina sólo cambia de pantalla. Que el pedido esté pagado lo dice el
 * servidor.
 */

const IZIPAY = {
  order_id: 7, provider: 'izipay', environment: 'sandbox', transaction_id: '12345678901234567890',
  authorization: 'token-de-sesion', merchant_code: '4001834', public_key: 'clave-publica', config: { action: 'pay' },
};
const MCW: MiCuentaWebSession = {
  order_id: 8, provider: 'micuentaweb', environment: 'test', transaction_id: '09876543210987654321',
  form_token: 'token-del-formulario', public_key: '69876357:testpublickey_NoEsReal',
};

afterEach(() => {
  document.head.innerHTML = '';
  document.body.innerHTML = '';
  delete (window as unknown as { KR?: unknown }).KR;
});

describe('what the browser accepts as a payment session', () => {
  test('one shape per provider', () => {
    expect(isPaymentSession(IZIPAY)).toBe(true);
    expect(isPaymentSession(MCW)).toBe(true);
    expect(isPaymentSession({ ...MCW, environment: 'production' })).toBe(true);
  });

  test('an unknown provider, a foreign environment or a missing token opens nothing', () => {
    expect(isPaymentSession({ ...IZIPAY, provider: 'otro' })).toBe(false);
    expect(isPaymentSession({ ...IZIPAY, environment: 'test' })).toBe(false);
    expect(isPaymentSession({ ...IZIPAY, authorization: '' })).toBe(false);
    expect(isPaymentSession({ ...MCW, environment: 'sandbox' })).toBe(false);
    expect(isPaymentSession({ ...MCW, form_token: '' })).toBe(false);
    expect(isPaymentSession({ ...MCW, public_key: '' })).toBe(false);
    expect(isPaymentSession(null)).toBe(false);
    expect(isPaymentSession('micuentaweb')).toBe(false);
  });
});

describe('Mi Cuenta Web · the Krypton form', () => {
  function scripts() {
    return Array.from(document.head.querySelectorAll('script'));
  }

  test('the script address is a constant of the code, never data from the response', () => {
    const tampered = {
      ...MCW,
      script_url: 'https://evil.example/kr.js',
      config: { src: 'https://evil.example/kr.js' },
    } as unknown as MiCuentaWebSession;

    void mountKryptonForm(tampered, () => {});

    const [main] = scripts();
    expect(main.getAttribute('src')).toBe(
      'https://static.micuentaweb.pe/static/js/krypton-client/V4.0/stable/kr-payment-form.min.js');
    expect(main.getAttribute('src')).toBe(KRYPTON_ASSETS.script);
    expect(main.getAttribute('kr-public-key')).toBe(MCW.public_key);
    expect(document.documentElement.innerHTML).not.toContain('evil.example');
    expect(document.head.querySelector('link[rel="stylesheet"]')?.getAttribute('href')).toBe(KRYPTON_ASSETS.stylesheet);
  });

  test('when the form says it finished, the page only moves on', async () => {
    const settled = jest.fn();
    const mounted = mountKryptonForm(MCW, settled);

    // El guion oficial termina de cargar y publica `KR`.
    let handler: ((answer: unknown) => unknown) | null = null;
    (window as unknown as { KR: unknown }).KR = {
      onSubmit: (callback: (answer: unknown) => unknown) => { handler = callback; return Promise.resolve(); },
    };
    scripts()[0].dispatchEvent(new Event('load'));
    await Promise.resolve();
    scripts().find((s) => s.getAttribute('src') === KRYPTON_ASSETS.theme)?.dispatchEvent(new Event('load'));
    await mounted;

    expect(handler).not.toBeNull();
    // Lo que el navegador reciba —aquí, un «PAID» inventado— no se lee.
    const result = handler!({ clientAnswer: { orderStatus: 'PAID' }, hash: 'x' });
    expect(result).toBe(false);                       // sin POST del navegador a ninguna parte
    expect(settled).toHaveBeenCalledTimes(1);
    expect(settled).toHaveBeenCalledWith();           // ni un dato de la respuesta sale de aquí
  });

  test('a script that does not load is an error the page can show', async () => {
    const mounted = mountKryptonForm(MCW, () => {});
    scripts()[0].dispatchEvent(new Event('error'));
    await expect(mounted).rejects.toThrow('No se pudo cargar el formulario de pago.');
  });

  test('the form container carries the token and the app draws no card field', () => {
    render(<KryptonPaymentForm session={MCW} onSettled={() => {}} onError={() => {}} />);

    const region = screen.getByRole('region', { name: 'Pago con tarjeta' });
    const holder = region.querySelector('.kr-smart-form');
    expect(holder).not.toBeNull();
    expect(holder?.getAttribute('kr-form-token')).toBe('token-del-formulario');
    expect(region.querySelectorAll('input')).toHaveLength(0);
  });
});
