import { fetchWithAuth, forgetInternalAccess, hasInternalAccess } from '@/app/lib/auth';

/**
 * `fetchWithAuth` — la sesión web por cookie, H4.1.1.
 *
 * TRES DEFECTOS QUE ESTAS PRUEBAS IMPIDEN QUE VUELVAN:
 *
 *   1. Forzaba `Content-Type: application/json` también sobre un `FormData`.
 *      Sin boundary, el servidor no encontraba el archivo y la subida de
 *      evidencias respondía 400 aunque la autenticación ya funcionara.
 *   2. Cada 401 hacía su propio refresh. Al abrir una pantalla salen varias
 *      peticiones a la vez, y cada refresh rota el token y deja otro en lista
 *      negra: la base de desarrollo acumuló 978 emitidos y 695 revocados.
 *   3. Cualquier 401 refrescaba, incluso con un `Authorization` explícito, que
 *      es otro canal al que la cookie de refresh no pertenece.
 *
 * Contra un `fetch` falso que registra cada llamada: lo que se afirma es QUÉ
 * salió hacia el servidor y cuántas veces.
 */

type Call = { url: string; init: RequestInit };
let calls: Call[] = [];

function reply(status: number, body: unknown = {}): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function install(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    calls.push({ url, init });
    return handler(url, init);
  }) as unknown as typeof fetch;
}

function header(call: Call, name: string): string | undefined {
  const headers = (call.init.headers ?? {}) as Record<string, string>;
  const key = Object.keys(headers).find((k) => k.toLowerCase() === name.toLowerCase());
  return key === undefined ? undefined : headers[key];
}

const refreshes = () => calls.filter((c) => c.url.includes('/auth/refresh/'));
const callsTo = (url: string) => calls.filter((c) => c.url === url);
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

beforeEach(() => {
  calls = [];
  document.cookie = 'csrftoken=csrf-de-prueba';
  window.localStorage.clear();
  forgetInternalAccess();
});

describe('fetchWithAuth · Content-Type', () => {
  it('manda JSON por defecto, con CSRF y cookies', async () => {
    install(() => reply(200));
    await fetchWithAuth('/api/x/', { method: 'POST', body: JSON.stringify({ a: 1 }) });

    expect(header(calls[0], 'Content-Type')).toBe('application/json');
    expect(header(calls[0], 'X-CSRFToken')).toBe('csrf-de-prueba');
    expect(calls[0].init.credentials).toBe('include');
  });

  it('NO fija Content-Type para FormData: el navegador pone el boundary', async () => {
    install(() => reply(201));
    const form = new FormData();
    form.append('stage', 'intake');

    await fetchWithAuth('/api/v1/internal/demo/service/orders/1/evidence/', {
      method: 'POST',
      body: form,
      headers: { 'Idempotency-Key': 'orden-1-foto' },
    });

    expect(header(calls[0], 'Content-Type')).toBeUndefined();
    expect(header(calls[0], 'X-CSRFToken')).toBe('csrf-de-prueba');
    expect(header(calls[0], 'Idempotency-Key')).toBe('orden-1-foto');
    expect(calls[0].init.body).toBe(form);
  });

  it('respeta un Content-Type explícito del llamador, sin duplicarlo', async () => {
    install(() => reply(200));
    await fetchWithAuth('/api/x/', {
      method: 'POST', body: 'hola', headers: { 'content-type': 'text/plain' },
    });

    const names = Object.keys(calls[0].init.headers as Record<string, string>);
    expect(names.filter((n) => n.toLowerCase() === 'content-type')).toHaveLength(1);
    expect(header(calls[0], 'Content-Type')).toBe('text/plain');
  });

  it('no manda CSRF en un GET', async () => {
    install(() => reply(200));
    await fetchWithAuth('/api/x/');
    expect(header(calls[0], 'X-CSRFToken')).toBeUndefined();
  });
});

describe('fetchWithAuth · refresh', () => {
  it('cinco 401 simultáneos comparten UN refresh, y cada petición reintenta UNA vez', async () => {
    let renewed = false;
    install(async (url) => {
      if (url.includes('/auth/refresh/')) {
        await wait(20);
        renewed = true;
        return reply(200);
      }
      return renewed ? reply(200) : reply(401);
    });
    const urls = [1, 2, 3, 4, 5].map((n) => `/api/v1/internal/demo/service/orders/${n}/`);

    const results = await Promise.all(urls.map((url) => fetchWithAuth(url)));

    expect(results.map((r) => r.status)).toEqual([200, 200, 200, 200, 200]);
    expect(refreshes()).toHaveLength(1);
    for (const url of urls) expect(callsTo(url)).toHaveLength(2);
  });

  it('una tanda POSTERIOR puede pedir su propio refresh', async () => {
    let renewed = false;
    install(async (url) => {
      if (url.includes('/auth/refresh/')) {
        await wait(5);
        renewed = true;
        return reply(200);
      }
      return renewed ? reply(200) : reply(401);
    });

    await fetchWithAuth('/api/a/');
    renewed = false; // la sesión vuelve a caducar
    await fetchWithAuth('/api/b/');

    expect(refreshes()).toHaveLength(2);
  });

  it('si el reintento vuelve a ser 401, devuelve ese 401 sin un segundo refresh', async () => {
    install((url) => (url.includes('/auth/refresh/') ? reply(200) : reply(401)));

    const res = await fetchWithAuth('/api/v1/internal/demo/service/context/');

    expect(res.status).toBe(401);
    expect(refreshes()).toHaveLength(1);
    expect(callsTo('/api/v1/internal/demo/service/context/')).toHaveLength(2);
  });

  it('si el refresh falla, no reintenta', async () => {
    install(() => reply(401));

    const res = await fetchWithAuth('/api/x/');

    expect(res.status).toBe(401);
    expect(refreshes()).toHaveLength(1);
    expect(callsTo('/api/x/')).toHaveLength(1);
  });

  it('una petición con Authorization explícito no usa el refresh de la cookie', async () => {
    install(() => reply(401));

    await fetchWithAuth('/api/x/', { headers: { Authorization: 'Bearer ajeno' } });

    expect(refreshes()).toHaveLength(0);
    expect(callsTo('/api/x/')).toHaveLength(1);
  });

  it.each([403, 404])('un %i no es una sesión caducada y no refresca', async (status) => {
    install(() => reply(status));

    const res = await fetchWithAuth('/api/x/');

    expect(res.status).toBe(status);
    expect(refreshes()).toHaveLength(0);
  });

  it('login, refresh y logout no se reintentan a sí mismos', async () => {
    install(() => reply(401));

    await fetchWithAuth('/api/auth/login/', { method: 'POST', body: '{}' });
    await fetchWithAuth('/api/auth/logout/', { method: 'POST', body: '{}' });

    expect(refreshes()).toHaveLength(0);
  });

  it('una mutación reintenta con el CSRF que dejó el refresh', async () => {
    let renewed = false;
    install((url) => {
      if (url.includes('/auth/refresh/')) {
        document.cookie = 'csrftoken=csrf-renovado';
        renewed = true;
        return reply(200);
      }
      return renewed ? reply(200) : reply(401);
    });

    await fetchWithAuth('/api/v1/internal/demo/notifications/read-all/', { method: 'POST' });

    const attempts = callsTo('/api/v1/internal/demo/notifications/read-all/');
    expect(attempts).toHaveLength(2);
    expect(header(attempts[1], 'X-CSRFToken')).toBe('csrf-renovado');
  });
});

describe('hasInternalAccess — lo decide el servidor, no el rol', () => {
  it.each([
    [{ count: 0, is_platform_admin: false }, false],
    [{ count: 1, is_platform_admin: false }, true],
    [{ count: 0, is_platform_admin: true }, true],
  ])('membresías %j → %s', async (body, expected) => {
    install((url) => (url.includes('/me/memberships/') ? reply(200, body) : reply(404)));
    expect(await hasInternalAccess()).toBe(expected);
  });

  it('pregunta UNA vez aunque lo pidan varios a la vez', async () => {
    install(async () => {
      await wait(10);
      return reply(200, { count: 1, is_platform_admin: false });
    });

    await Promise.all([hasInternalAccess(), hasInternalAccess(), hasInternalAccess()]);

    expect(calls.filter((c) => c.url.includes('/me/memberships/'))).toHaveLength(1);
  });

  it('no recuerda un fallo como si fuera un «no»', async () => {
    let first = true;
    install(() => {
      if (first) {
        first = false;
        return reply(500);
      }
      return reply(200, { count: 1, is_platform_admin: false });
    });

    expect(await hasInternalAccess()).toBe(false);
    expect(await hasInternalAccess()).toBe(true);
  });

  it('se olvida cuando la sesión cambia', async () => {
    install(() => reply(200, { count: 1, is_platform_admin: false }));

    await hasInternalAccess();
    forgetInternalAccess();
    await hasInternalAccess();

    expect(calls.filter((c) => c.url.includes('/me/memberships/'))).toHaveLength(2);
  });
});
