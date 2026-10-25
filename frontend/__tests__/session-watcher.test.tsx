import { act, fireEvent, render, screen } from '@testing-library/react';
import { SessionWatcher, sessionNavigation } from '@/app/components/SessionWatcher';
import { __resetSessionStateForTests, markEnded, noteRemaining, sessionStarted } from '@/app/lib/session-state';

/**
 * AUTH-C — the warning, «Continuar sesión», and zero.
 *
 * The server is a fake that answers `/auth/session/` with what the test says;
 * time is Jest's. Each case is something the owner asked for: the warning with
 * its countdown, continuing only on a click, the 8-hour text, the redirect with
 * its reason, and nothing internal left on screen.
 */

type Status = { idle_remaining_s: number; absolute_remaining_s: number; idle_timeout_s: number; warn_before_s: number };
let status: Status | null;
let keepalives = 0;
let guest = false;
let down = false;
const navigated: string[] = [];

function reply(code: number, body: unknown, headers: Record<string, string> = {}) {
  const response = {
    status: code,
    ok: code >= 200 && code < 300,
    headers: { get: (name: string) => headers[name] ?? null },
    json: () => Promise.resolve(body),
    clone: () => response,
  };
  return response as unknown as Response;
}

function server() {
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if (url.includes('/auth/session/keepalive/')) {
      keepalives += 1;
      status = { ...(status as Status), idle_remaining_s: 1800 };
      return reply(200, status);
    }
    if (url.includes('/auth/session/')) {
      if (down) throw new TypeError('Failed to fetch');
      if (guest) return reply(401, { detail: 'Las credenciales de autenticación no se proveyeron.' });
      return status ? reply(200, status) : reply(401, { code: 'session_expired' });
    }
    if (url.includes('/auth/refresh/')) return guest ? reply(401, {}) : reply(401, { code: 'session_expired' });
    if (url.includes('/auth/logout/')) return reply(200, {});
    if (url.includes('/auth/csrf/')) return reply(200, {});
    void init;
    return reply(200, {});
  }) as unknown as typeof fetch;
}

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

async function seconds(n: number) {
  for (let i = 0; i < n; i++) {
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    await flush();
  }
}

beforeEach(() => {
  jest.useFakeTimers({ doNotFake: ['performance', 'queueMicrotask'] });
  __resetSessionStateForTests();
  status = { idle_remaining_s: 1800, absolute_remaining_s: 28_800, idle_timeout_s: 1800, warn_before_s: 300 };
  keepalives = 0;
  guest = false;
  down = false;
  navigated.length = 0;
  jest.spyOn(sessionNavigation, 'replace').mockImplementation((url: string) => { navigated.push(url); });
  document.cookie = 'csrftoken=csrf';
  server();
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

function nowRemaining(idle: number, absolute = 28_800) {
  status = { ...(status as Status), idle_remaining_s: idle, absolute_remaining_s: absolute };
  act(() => noteRemaining(idle, absolute, 300));
}

describe('SessionWatcher · panel', () => {
  it('shows nothing while there is time', async () => {
    render(<SessionWatcher mode="internal"><p>Pantalla interna</p></SessionWatcher>);
    await flush();
    await seconds(2);
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.getByText('Pantalla interna')).toBeInTheDocument();
  });

  it('warns 5 minutes before, with a countdown, and asks the server first', async () => {
    render(<SessionWatcher mode="internal"><p>Pantalla interna</p></SessionWatcher>);
    await flush();
    nowRemaining(299);
    await seconds(2);
    const dialog = screen.getByRole('alertdialog');
    expect(dialog).toHaveTextContent('Tu sesión está por cerrarse');
    expect(dialog).toHaveTextContent('30 minutos sin actividad');
    expect(dialog).toHaveTextContent(/4:5\d/);
    expect(screen.getByRole('button', { name: 'Continuar sesión' })).toHaveFocus();
  });

  it('opening the warning is not activity; «Continuar sesión» is, once pressed', async () => {
    render(<SessionWatcher mode="internal"><p>Pantalla interna</p></SessionWatcher>);
    await flush();
    nowRemaining(299);
    await seconds(2);
    expect(keepalives).toBe(0);

    fireEvent.click(screen.getByRole('button', { name: 'Continuar sesión' }));
    await flush();

    expect(keepalives).toBe(1);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('a warning another tab answered goes away by itself', async () => {
    render(<SessionWatcher mode="internal"><p>Pantalla interna</p></SessionWatcher>);
    await flush();
    nowRemaining(299);
    await seconds(2);
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();

    nowRemaining(1800); // what the other tab's «Continuar» told everybody
    await seconds(1);

    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('at zero it hides the screen and goes to the login page with the reason', async () => {
    render(<SessionWatcher mode="internal"><p>Pantalla interna</p></SessionWatcher>);
    await flush();
    nowRemaining(2);
    status = null; // the server: over
    await seconds(4);
    expect(navigated[0]).toMatch(/^\/auth\?reason=idle&next=/);
    expect(screen.getByText('Comprobando tu sesión…')).toBeInTheDocument();
  });

  it('without an answer from the server it stays covered and asks again — silence is not «over»', async () => {
    render(<SessionWatcher mode="internal"><p>Pantalla interna</p></SessionWatcher>);
    await flush();
    down = true;
    nowRemaining(0);
    await seconds(4);
    expect(navigated).toHaveLength(0);
    expect(screen.getByText('Comprobando tu sesión…')).toBeInTheDocument();

    down = false;
    status = null; // the server answers now: over
    await seconds(10);
    expect(navigated[0]).toMatch(/^\/auth\?reason=idle/);
  });

  it('the 8-hour limit says so, and offers no «Continuar»', async () => {
    render(<SessionWatcher mode="internal"><p>Pantalla interna</p></SessionWatcher>);
    await flush();
    nowRemaining(1500, 200);
    await seconds(2);
    expect(screen.getByRole('alertdialog')).toHaveTextContent('8 horas');
    expect(screen.queryByRole('button', { name: 'Continuar sesión' })).toBeNull();

    status = null;
    nowRemaining(1500, 1);
    status = null;
    await seconds(3);
    expect(navigated[0]).toMatch(/^\/auth\?reason=absolute/);
  });

  it('a page restored from the back-forward cache is hidden until the server answers', async () => {
    render(<SessionWatcher mode="internal"><p>Pantalla interna</p></SessionWatcher>);
    await flush();
    status = null;
    await act(async () => {
      const event = new Event('pageshow') as PageTransitionEvent;
      Object.defineProperty(event, 'persisted', { value: true });
      window.dispatchEvent(event);
    });
    expect(screen.getByText('Comprobando tu sesión…')).toBeInTheDocument();
    await flush();
    expect(navigated[0]).toMatch(/^\/auth\?reason=/);
  });

  it('«Cerrar sesión» signs out and leaves', async () => {
    render(<SessionWatcher mode="internal"><p>Pantalla interna</p></SessionWatcher>);
    await flush();
    nowRemaining(299);
    await seconds(2);
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }));
    await flush();
    expect(navigated).toContain('/auth');
  });
});

describe('SessionWatcher · storefront', () => {
  it('a guest (no session) is never warned', async () => {
    guest = true;
    render(<SessionWatcher mode="storefront" />);
    await flush();
    await seconds(3);
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(navigated).toHaveLength(0);
  });

  it('a guest is not told a session closed: there was none', async () => {
    guest = true;
    render(<SessionWatcher mode="storefront" />);
    await flush();
    act(() => markEnded('idle')); // e.g. X-Session-Expired on some answer
    await seconds(1);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('at zero the buyer stays, as a guest, and is told so', async () => {
    render(<SessionWatcher mode="storefront" />);
    await flush();
    nowRemaining(2);
    status = null;
    await seconds(4);
    expect(navigated).toHaveLength(0);
    expect(screen.getByRole('alertdialog')).toHaveTextContent('Tu carrito sigue aquí');
    fireEvent.click(screen.getByRole('button', { name: 'Seguir como invitado' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('a tab told the session ended recovers when somebody signs in (here or in another tab)', async () => {
    render(<SessionWatcher mode="storefront" />);
    await flush();
    nowRemaining(2);
    status = null;
    await seconds(4);
    expect(screen.getByRole('alertdialog')).toHaveTextContent('Tu sesión se cerró');

    status = { idle_remaining_s: 1800, absolute_remaining_s: 600000, idle_timeout_s: 1800, warn_before_s: 300 };
    act(() => sessionStarted());
    await flush();

    expect(screen.queryByRole('alertdialog')).toBeNull();
  });
});
