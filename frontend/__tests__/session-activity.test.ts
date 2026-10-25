import { fetchWithAuth, login } from '@/app/lib/auth';
import { fetcher } from '@/app/lib/api';
import {
  __resetSessionStateForTests, getSession, idleRemainingS, isEnded, noteTrustedGesture,
} from '@/app/lib/session-state';

/**
 * AUTH-C — what `fetchWithAuth` tells the server about a person's activity, and
 * what it does when the server says the session is over.
 *
 * THE RULE THESE TESTS PIN. `X-Session-Activity: 1` goes out only when (a) the
 * request is a write, or a read the caller marked `{ activity: true }`, AND (b)
 * a REAL key or pointer press happened in the last few seconds (or the browser
 * reports user activation). A timer, a poll, an effect on mount, a synthetic
 * event: never. And once the server says `session_expired`, nothing renews.
 */

type Call = { url: string; init: RequestInit };
let calls: Call[] = [];

function reply(status: number, body: unknown = {}, headers: Record<string, string> = {}): Response {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  const response = {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name: string) => lower[name.toLowerCase()] ?? null },
    json: () => Promise.resolve(body),
    clone: () => response,
  };
  return response as unknown as Response;
}

function install(handler: (url: string, init: RequestInit) => Response) {
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    calls.push({ url, init });
    return handler(url, init);
  }) as unknown as typeof fetch;
}

function activity(call: Call): string | undefined {
  const headers = (call.init.headers ?? {}) as Record<string, string>;
  const key = Object.keys(headers).find((k) => k.toLowerCase() === 'x-session-activity');
  return key === undefined ? undefined : headers[key];
}

function setActivation(active: boolean) {
  Object.defineProperty(navigator, 'userActivation', { value: { isActive: active }, configurable: true });
}

function personPresses() {
  noteTrustedGesture({ isTrusted: true } as Event);
}

beforeEach(() => {
  calls = [];
  document.cookie = 'csrftoken=csrf-de-prueba';
  window.localStorage.clear();
  __resetSessionStateForTests();
  setActivation(false);
  jest.useRealTimers();
});

describe('the activity mark', () => {
  it('a write with nobody at the keyboard carries no mark', async () => {
    install(() => reply(200));
    await fetchWithAuth('/api/x/', { method: 'POST', body: '{}' });
    expect(activity(calls[0])).toBeUndefined();
  });

  it('a write right after a real key press carries it', async () => {
    install(() => reply(200));
    personPresses();
    await fetchWithAuth('/api/x/', { method: 'POST', body: '{}' });
    expect(activity(calls[0])).toBe('1');
  });

  it('the browser saying «the user just acted» counts too', async () => {
    install(() => reply(200));
    setActivation(true);
    await fetchWithAuth('/api/x/', { method: 'PATCH', body: '{}' });
    expect(activity(calls[0])).toBe('1');
  });

  it('a read carries it only when the caller says a person asked for it', async () => {
    install(() => reply(200));
    personPresses();
    await fetchWithAuth('/api/x/');
    await fetchWithAuth('/api/x/', { activity: true });
    expect(activity(calls[0])).toBeUndefined();
    expect(activity(calls[1])).toBe('1');
  });

  it('`activity: false` wins even right after a key press', async () => {
    install(() => reply(200));
    personPresses();
    await fetchWithAuth('/api/x/', { method: 'POST', body: '{}', activity: false });
    expect(activity(calls[0])).toBeUndefined();
  });

  it('a synthetic event (a script, not a person) is not a gesture', async () => {
    install(() => reply(200));
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
    document.dispatchEvent(new Event('pointerdown'));
    await fetchWithAuth('/api/x/', { method: 'POST', body: '{}' });
    expect(activity(calls[0])).toBeUndefined();
  });

  it('the caller cannot put the mark there by hand', async () => {
    install(() => reply(200));
    await fetchWithAuth('/api/x/', { method: 'POST', body: '{}', headers: { 'X-Session-Activity': '1' } });
    expect(activity(calls[0])).toBeUndefined();
  });

  it('a timer that writes every minute never keeps the session alive', async () => {
    jest.useFakeTimers();
    install(() => reply(200));
    personPresses();
    jest.advanceTimersByTime(10_000); // the press is long gone
    const timer = setInterval(() => { void fetchWithAuth('/api/x/', { method: 'POST', body: '{}' }); }, 60_000);
    for (let i = 0; i < 5; i++) {
      jest.advanceTimersByTime(60_000);
      await Promise.resolve();
    }
    clearInterval(timer);
    jest.useRealTimers();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(calls.length).toBeGreaterThanOrEqual(5);
    expect(calls.every((call) => activity(call) === undefined)).toBe(true);
  });

  it('the mark expires a few seconds after the press', async () => {
    jest.useFakeTimers({ now: 0, doNotFake: ['performance'] });
    install(() => reply(200));
    const realNow = performance.now.bind(performance);
    let shift = 0;
    jest.spyOn(performance, 'now').mockImplementation(() => realNow() + shift);
    personPresses();
    shift = 6_000;
    jest.useRealTimers();
    await fetchWithAuth('/api/x/', { method: 'POST', body: '{}' });
    expect(activity(calls[0])).toBeUndefined();
    (performance.now as jest.Mock).mockRestore();
  });
});

describe('when the server says the session is over', () => {
  it('a 401 session_expired is final: no renewal, no retry', async () => {
    install(() => reply(401, { detail: 'Credenciales inválidas.', code: 'session_expired' }));
    const res = await fetchWithAuth('/api/x/', { method: 'POST', body: '{}' });
    expect(res.status).toBe(401);
    expect(calls.filter((c) => c.url.includes('/auth/refresh/'))).toHaveLength(0);
    expect(calls).toHaveLength(1);
    expect(isEnded()).toBe(true);
  });

  it('after that, no 401 renews anything until somebody signs in', async () => {
    install(() => reply(401, { code: 'session_expired' }));
    await fetchWithAuth('/api/x/');
    calls = [];
    install(() => reply(401, {}));
    await fetchWithAuth('/api/y/');
    expect(calls.filter((c) => c.url.includes('/auth/refresh/'))).toHaveLength(0);

    install((url) => (url.includes('/auth/login/') ? reply(200, { user: {} }) : reply(401, {})));
    await login('ana', 'clave');
    expect(isEnded()).toBe(false);
    calls = [];
    await fetchWithAuth('/api/z/');
    expect(calls.filter((c) => c.url.includes('/auth/refresh/'))).toHaveLength(1);
  });

  it('a renewal answered session_expired ends it too', async () => {
    install((url) => (url.includes('/auth/refresh/') ? reply(401, { code: 'session_expired' }) : reply(401, {})));
    await fetchWithAuth('/api/x/');
    expect(isEnded()).toBe(true);
  });

  it('X-Session-Expired on a GUEST answer (a 200) ends it: the only readable sign', async () => {
    install(() => reply(200, [], { 'X-Session-Expired': '1' }));
    await fetchWithAuth('/api/cart/?session_key=x');
    expect(isEnded()).toBe(true);
  });

  it('the plain storefront fetcher reads it too', async () => {
    install(() => reply(200, [], { 'X-Session-Expired': '1' }));
    await fetcher('/api/products/');
    expect(isEnded()).toBe(true);
  });

  it('X-Session-Idle-Remaining is how long is left, in seconds of THIS tab', async () => {
    install(() => reply(200, {}, { 'X-Session-Idle-Remaining': '1200' }));
    await fetchWithAuth('/api/x/');
    expect(idleRemainingS()).toBeGreaterThan(1195);
    expect(idleRemainingS()).toBeLessThanOrEqual(1200);
    expect(getSession().ended).toBeNull();
  });
});
