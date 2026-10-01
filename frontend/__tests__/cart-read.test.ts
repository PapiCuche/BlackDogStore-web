import { readCart } from '@/app/lib/cart';

/**
 * H4.1.2B — leer el carrito y no poder leerlo son dos respuestas distintas.
 *
 * EL DEFECTO QUE ESTAS PRUEBAS FIJAN. El checkout guardaba cualquier fallo de
 * lectura como lista vacía. Con eso, un 429 del limitador —60 lecturas por
 * minuto y por IP, que una oficina o la suite de navegador agotan sin mala
 * intención— se mostraba al comprador como «no pudimos leer tu carrito» y, peor,
 * apagaba la cotización: sin artículos no hay nada que cotizar, así que el
 * desglose tributario desaparecía de la pantalla sin que nadie dijera por qué.
 *
 * Lo que NO se prueba aquí: que el límite exista. Eso es del backend y tiene sus
 * propias pruebas. Aquí sólo se fija cómo se comporta el cliente cuando llega.
 */

function respuesta(init: {
  ok?: boolean;
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}): Response {
  const headers = new Headers(init.headers ?? {});
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    headers,
    json: async () => init.body,
    text: async () => JSON.stringify(init.body ?? ''),
  } as unknown as Response;
}

const UN_ARTICULO = [
  { id: 1, quantity: 2, product: { id: 3, name: 'iPhone', price: '100.00', slug: 'iphone' } },
];

describe('readCart', () => {
  it('devuelve los artículos cuando el servidor contesta', async () => {
    const fetchImpl = jest.fn(async () => respuesta({ body: UN_ARTICULO }));

    const read = await readCart('k', { fetchImpl: fetchImpl as unknown as typeof fetch });

    expect(read).toEqual({ status: 'ok', items: UN_ARTICULO });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('un carrito vacío es «ok», no un fallo', async () => {
    const fetchImpl = jest.fn(async () => respuesta({ body: [] }));

    // La distinción entera del arreglo: sin esto, la pantalla no puede decirle
    // al comprador «tu carrito está vacío» sin insinuar que algo se rompió.
    await expect(
      readCart('k', { fetchImpl: fetchImpl as unknown as typeof fetch }),
    ).resolves.toEqual({ status: 'ok', items: [] });
  });

  it('espera lo que el limitador pide y lo consigue al segundo intento', async () => {
    const esperas: number[] = [];
    const fetchImpl = jest
      .fn()
      .mockResolvedValueOnce(
        respuesta({
          ok: false,
          status: 429,
          body: { detail: 'Solicitud fue regulada (throttled). Se espera que esté disponible en 60 segundos.' },
        }),
      )
      .mockResolvedValueOnce(respuesta({ body: UN_ARTICULO }));

    const read = await readCart('k', {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      wait: async (ms) => { esperas.push(ms); },
    });

    expect(read).toEqual({ status: 'ok', items: UN_ARTICULO });
    // 61 s: lo que dijo el servidor más uno. Ni menos —volvería a chocar— ni
    // un número inventado aquí.
    expect(esperas).toEqual([61_000]);
  });

  it('prefiere «Retry-After» al texto del cuerpo, que depende del idioma', async () => {
    const esperas: number[] = [];
    const fetchImpl = jest
      .fn()
      .mockResolvedValueOnce(
        respuesta({
          ok: false,
          status: 429,
          headers: { 'Retry-After': '5' },
          body: { detail: 'Se espera que esté disponible en 60 segundos.' },
        }),
      )
      .mockResolvedValueOnce(respuesta({ body: [] }));

    await readCart('k', {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      wait: async (ms) => { esperas.push(ms); },
    });

    expect(esperas).toEqual([6_000]);
  });

  it('si el 429 insiste, lo dice: ilegible, nunca vacío', async () => {
    const fetchImpl = jest.fn(async () =>
      respuesta({
        ok: false,
        status: 429,
        headers: { 'Retry-After': '1' },
        body: { detail: 'Solicitud fue regulada (throttled).' },
      }),
    );

    const read = await readCart('k', {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      wait: async () => {},
    });

    expect(read).toEqual({ status: 'unreadable' });
    // Un reintento, no una tormenta contra un servidor que ya dijo que no.
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('un 500 no se reintenta ni se disfraza de carrito vacío', async () => {
    const fetchImpl = jest.fn(async () => respuesta({ ok: false, status: 500, body: {} }));

    const read = await readCart('k', { fetchImpl: fetchImpl as unknown as typeof fetch });

    expect(read).toEqual({ status: 'unreadable' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('sin red tampoco hay carrito vacío', async () => {
    const fetchImpl = jest.fn(async () => { throw new Error('offline'); });

    await expect(
      readCart('k', { fetchImpl: fetchImpl as unknown as typeof fetch }),
    ).resolves.toEqual({ status: 'unreadable' });
  });

  it('un tope impide que un «Retry-After» absurdo cuelgue la pantalla', async () => {
    const esperas: number[] = [];
    const fetchImpl = jest
      .fn()
      .mockResolvedValueOnce(
        respuesta({ ok: false, status: 429, headers: { 'Retry-After': '99999' }, body: {} }),
      )
      .mockResolvedValueOnce(respuesta({ body: [] }));

    await readCart('k', {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      wait: async (ms) => { esperas.push(ms); },
    });

    expect(esperas).toEqual([120_000]);
  });
});
