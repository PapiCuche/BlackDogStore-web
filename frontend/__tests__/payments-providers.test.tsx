import { render, screen } from '@testing-library/react';

import KryptonPaymentForm from '@/app/components/KryptonPaymentForm';
import {
  KRYPTON_ASSETS,
  KRYPTON_FORM_IS_OVER,
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

  /** El guion oficial termina de cargar y publica `KR`; devuelve lo que la página le suscribió. */
  function gatewayClient() {
    const heard: { submit?: (answer: unknown) => unknown; error?: (error: unknown) => unknown } = {};
    (window as unknown as { KR: unknown }).KR = {
      onSubmit: (callback: (answer: unknown) => unknown) => { heard.submit = callback; return Promise.resolve(); },
      onError: (callback: (error: unknown) => unknown) => { heard.error = callback; return Promise.resolve(); },
    };
    return heard;
  }

  test('a form that can no longer charge is said, and a refused card is left to the form', async () => {
    const over = jest.fn();
    const mounted = mountKryptonForm(MCW, () => {}, over);
    const heard = gatewayClient();
    scripts()[0].dispatchEvent(new Event('load'));
    await Promise.resolve();
    await Promise.resolve();
    scripts().find((s) => s.getAttribute('src') === KRYPTON_ASSETS.theme)?.dispatchEvent(new Event('load'));
    await mounted;

    // Lo que el propio formulario muestra y deja reintentar: tarjeta mal escrita, pago rechazado.
    for (const errorCode of ['CLIENT_301', 'CLIENT_001', 'ACQ_001', 'PSP_526']) heard.error!({ errorCode });
    heard.error!(null);
    heard.error!({});
    expect(over).not.toHaveBeenCalled();

    // Token que no vale, clave que no vale, formulario caducado, demasiados intentos.
    expect([...KRYPTON_FORM_IS_OVER].sort()).toEqual(['CLIENT_004', 'CLIENT_100', 'PSP_099', 'PSP_108']);
    for (const errorCode of KRYPTON_FORM_IS_OVER) heard.error!({ errorCode, errorMessage: 'texto de la pasarela' });
    expect(over).toHaveBeenCalledTimes(4);
    expect(over).toHaveBeenCalledWith();              // ni el código ni el texto de la pasarela salen de aquí
  });

  test('the page listens for errors before anything else of the gateway loads', async () => {
    const over = jest.fn();
    void mountKryptonForm(MCW, () => {}, over);
    const heard = gatewayClient();
    scripts()[0].dispatchEvent(new Event('load'));
    await Promise.resolve();
    await Promise.resolve();

    // El tema aún no ha cargado, y el cliente ya puede decir que el token no vale.
    expect(scripts().find((s) => s.getAttribute('src') === KRYPTON_ASSETS.theme)?.dataset.loaded).toBeUndefined();
    expect(heard.error).toBeDefined();
    heard.error!({ errorCode: 'CLIENT_100' });
    expect(over).toHaveBeenCalledTimes(1);
  });

  test('the component tells the page in words a buyer can act on', async () => {
    const failed = jest.fn();
    render(<KryptonPaymentForm session={MCW} onSettled={() => {}} onError={failed} />);
    const heard = gatewayClient();
    scripts()[0].dispatchEvent(new Event('load'));
    await Promise.resolve();
    await Promise.resolve();

    heard.error!({ errorCode: 'PSP_108', errorMessage: 'The payment form has expired' });

    expect(failed).toHaveBeenCalledTimes(1);
    const [text] = failed.mock.calls[0];
    expect(text).toMatch(/formulario de pago ya no es válido/i);
    expect(text).toMatch(/no se cobró/i);
    expect(text).not.toMatch(/PSP_108|expired/);
  });

  test('a second form in the same tab that the gateway refuses is said the same way, once', async () => {
    // El cliente ya está en la pestaña, de un intento anterior. Con un token que
    // no acepta, rechaza `setFormConfig` con el mismo objeto que da a `onError`.
    const refusal = { errorCode: 'CLIENT_100', errorMessage: 'Invalid formToken' };
    let heard: ((error: unknown) => unknown) | undefined;
    (window as unknown as { KR: unknown }).KR = {
      onSubmit: () => Promise.resolve(),
      onError: (callback: (error: unknown) => unknown) => { heard = callback; return Promise.resolve(); },
      setFormConfig: () => { heard?.(refusal); return Promise.reject(refusal); },
      renderElements: () => Promise.resolve(),
      removeForms: () => Promise.resolve(),
    };
    const failed = jest.fn();
    render(<KryptonPaymentForm session={MCW} onSettled={() => {}} onError={failed} />);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(failed).toHaveBeenCalledTimes(1);
    expect(failed.mock.calls[0][0]).toMatch(/formulario de pago ya no es válido/i);
  });

  test('a refusal that only comes as a rejection is said in the same words', async () => {
    // Así lo hace el cliente real en un segundo intento: no avisa a quien
    // escucha, sólo rechaza.
    (window as unknown as { KR: unknown }).KR = {
      onSubmit: () => Promise.resolve(),
      onError: () => Promise.resolve(),
      setFormConfig: () => Promise.reject({ errorCode: 'CLIENT_100', errorMessage: 'Invalid formToken' }),
    };
    const failed = jest.fn();
    render(<KryptonPaymentForm session={MCW} onSettled={() => {}} onError={failed} />);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(failed).toHaveBeenCalledTimes(1);
    expect(failed.mock.calls[0][0]).toMatch(/formulario de pago ya no es válido/i);
  });

  test('a second form that fails for another reason keeps its own words', async () => {
    (window as unknown as { KR: unknown }).KR = {
      onSubmit: () => Promise.resolve(),
      onError: () => Promise.resolve(),
      setFormConfig: () => Promise.reject({ errorCode: 'CLIENT_999' }),
    };
    const failed = jest.fn();
    render(<KryptonPaymentForm session={MCW} onSettled={() => {}} onError={failed} />);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(failed).toHaveBeenCalledTimes(1);
    expect(failed.mock.calls[0][0]).toBe('No se pudo abrir el pago.');
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
