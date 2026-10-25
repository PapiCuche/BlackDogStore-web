import { readFileSync, readdirSync, statSync } from 'fs';
import { join, relative } from 'path';

/**
 * AUTH-C — two guards that read the source, because what they forbid is a
 * pattern anybody could add next month.
 *
 *   1. NO AUTOMATIC ACTIVITY (review item d). Anything that runs by itself —
 *      `setInterval`, a `setTimeout` loop, a focus or visibility listener — may
 *      not ask for activity (`activity: true`). The runtime rule already makes
 *      that harmless (the mark needs a trusted gesture: `session-activity.test`),
 *      this keeps the intent visible: a file with a timer that wants activity
 *      has to be listed here, with its reason.
 *
 *   2. NOTHING SENSITIVE IN THE BROWSER'S STORAGE. Every `localStorage` /
 *      `sessionStorage` write is listed with what it holds. Tokens, keys, fiscal
 *      credentials and a customer's private data (a service order's notes
 *      included) never go there: drafts that survive a closed session may only
 *      be the non-sensitive ones.
 */

const APP = join(__dirname, '..', 'app');

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

const FILES = sources(APP).map((path) => ({ path: relative(APP, path), text: readFileSync(path, 'utf8') }));

const AUTOMATIC = /setInterval\(|addEventListener\(\s*["'](focus|visibilitychange|online|pageshow)["']|setTimeout\(/;

/** Files that run something by themselves AND ask for activity — each with why it is not automatic. */
const AUTOMATIC_WITH_ACTIVITY: Record<string, string> = {
  // Its interval only draws the countdown; `activity: true` is on «Continuar sesión»
  // (a click) and on the heartbeat, which runs from a TRUSTED key/pointer press.
  'components/SessionWatcher.tsx': 'activity only from a click or a trusted gesture',
};

describe('no automatic source of activity', () => {
  it('nothing that runs by itself asks for activity', () => {
    const offenders = FILES
      .filter(({ text }) => AUTOMATIC.test(text) && /activity:\s*true/.test(text))
      .map(({ path }) => path)
      .filter((path) => !(path in AUTOMATIC_WITH_ACTIVITY));
    expect(offenders).toEqual([]);
  });

  it('the polls say outright that they are not activity', () => {
    const availability = FILES.find(({ path }) => path === 'lib/availability.ts')!.text;
    const bell = FILES.find(({ path }) => path === 'admin/components/NotificationBell.tsx')!.text;
    expect(availability).toMatch(/fetchWithAuth\([^)]*\{ activity: false \}\)/);
    expect(bell).toMatch(/unread-count\/`, \{ activity: false \}\)/);
  });

  it('nobody but fetchWithAuth writes the activity header', () => {
    const writers = FILES
      .filter(({ text }) => /["']X-Session-Activity["']/i.test(text))
      .map(({ path }) => path);
    expect(writers.sort()).toEqual(['lib/session-state.ts']);
  });

  it('no route change counts by itself: nothing outside the gesture tracker fakes a gesture', () => {
    const fakers = FILES
      .filter(({ path, text }) => path !== 'lib/session-state.ts' && /noteTrustedGesture\(/.test(text))
      .map(({ path }) => path);
    expect(fakers).toEqual([]);
  });
});

/** Every storage write, by file and key, with what it holds. Nothing else may write. */
const STORAGE_WRITES: Record<string, string> = {
  'admin/components/InternalControlGuard.tsx SELECTED_COMPANY_KEY': 'the company a master is looking at (an id)',
  'admin/components/InventoryMode.tsx KEY': 'a display preference',
  'admin/lib/pos-in-flight.ts STORAGE_KEY': 'a sale in flight: random key, basket hash, time, user and company ids',
  'auth/reset-password/page.tsx "bd.auth.username"': 'the username to prefill after a reset (no password)',
  'components/ThemeProvider.tsx THEME_STORAGE_KEY': 'light or dark',
  'lib/analytics/items.ts BEGUN': 'which measurement events already fired',
  'lib/analytics/service.ts PURCHASED': 'which purchases were already measured',
  'lib/cart.ts COUPON_KEY': 'the coupon typed in the cart',
  'lib/cart.ts LEGACY_SESSION_KEY': 'the cart key of this browser (random)',
  'lib/cart.ts SESSION_KEY': 'the cart key of this browser (random)',
  'lib/consent.ts CONSENT_KEY': 'the measurement consent answer',
  'lib/payment-reference.ts KEY': 'the payment reference of the success page',
};

describe('nothing sensitive in the browser', () => {
  const writes = FILES.flatMap(({ path, text }) =>
    [...text.matchAll(/(?:localStorage|sessionStorage)\.setItem\(\s*([A-Za-z_][A-Za-z_0-9.]*|"[^"]*"|'[^']*')/g)]
      .map((match) => `${path} ${match[1]}`),
  );

  it('every write is a known, non-sensitive one', () => {
    expect([...new Set(writes)].filter((write) => !(write in STORAGE_WRITES))).toEqual([]);
  });

  it('no key names a token, a secret or private data', () => {
    const forbidden = /token|jwt|secret|password|clave|sol|cert|note|nota|document|dni|ruc|phone|address|notes/i;
    expect(writes.filter((write) => forbidden.test(write.split(' ')[1]) && !/session_key|SESSION_KEY/.test(write)))
      .toEqual([]);
  });

  it('the session lives in HttpOnly cookies only: no code reads them', () => {
    const readers = FILES
      .filter(({ text }) => /blackdog_(access|refresh)/.test(text))
      .map(({ path }) => path);
    expect(readers).toEqual([]);
  });
});
