import { register } from '@/app/lib/auth';

/**
 * AUTH-REGISTER-CSRF — registrarse con una sesión abierta en otra pestaña.
 *
 * Con la cookie de acceso presente, el servidor exige el token CSRF en toda
 * escritura. `register` salía con `fetch` a secas y respondía «CSRF Failed» a
 * quien tuviera una sesión abierta. Lleva el token, como el resto de `auth.ts`.
 */

const DATA = { username: 'ana', email: 'ana@example.com', password: 'Clave123!', password_confirm: 'Clave123!' };

afterEach(() => {
  document.cookie = 'csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
});

it('envía el token CSRF que ya tiene el navegador', async () => {
  document.cookie = 'csrftoken=abc123; path=/';
  const calls: Array<{ url: string; init: RequestInit }> = [];
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ url: String(input), init });
    return { ok: true, status: 201, json: async () => ({ detail: 'ok', requires_verification: true }) } as Response;
  }) as unknown as typeof fetch;

  await register(DATA);

  const post = calls.find((call) => call.url.endsWith('/auth/register/'))!;
  expect(post.init.method).toBe('POST');
  expect(new Headers(post.init.headers).get('X-CSRFToken')).toBe('abc123');
  expect(post.init.credentials).toBe('include');
});

it('sin token, lo pide antes de registrar', async () => {
  const order: string[] = [];
  global.fetch = jest.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    order.push(url.replace(/^.*\/api/, ''));
    if (url.endsWith('/auth/csrf/')) document.cookie = 'csrftoken=nuevo; path=/';
    return { ok: true, status: 200, json: async () => ({ detail: 'ok', requires_verification: true }) } as Response;
  }) as unknown as typeof fetch;

  await register(DATA);

  expect(order).toEqual(['/auth/csrf/', '/auth/register/']);
});
