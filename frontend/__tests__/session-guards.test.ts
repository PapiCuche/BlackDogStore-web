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

/** Code without its comments: what a guard about references must read. */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

/** The one place allowed to name the storage objects, and the one inline script that cannot import it. */
const STORAGE_DOOR = 'lib/browser-storage.ts';
const THEME_INIT = 'components/ThemeProvider.tsx';

describe('nothing sensitive in the browser', () => {
  it('nobody but browser-storage touches localStorage or sessionStorage — aliases included', () => {
    const offenders = FILES.flatMap(({ path, text }) => {
      if (path === STORAGE_DOOR) return [];
      let body = code(text);
      // The theme script runs before React and cannot import: its single read is pinned here.
      if (path === THEME_INIT) body = body.replace('var c=localStorage.getItem(k);', '');
      return /localStorage|sessionStorage/.test(body) ? [path] : [];
    });
    expect(offenders).toEqual([]);
  });

  it('every key the browser may keep is listed with what it holds — and none is sensitive', () => {
    const { STORAGE_KEYS } = jest.requireActual('@/app/lib/browser-storage') as {
      STORAGE_KEYS: Record<string, { area: string; holds: string }>;
    };
    const forbidden = /token|jwt|secret|password|contraseña|clave|sunat|cert|note|nota|document|dni|ruc|phone|teléfono|address|dirección/i;
    for (const [key, entry] of Object.entries(STORAGE_KEYS)) {
      expect(['local', 'session']).toContain(entry.area);
      expect(entry.holds.length).toBeGreaterThan(5);
      expect(`${key} ${entry.holds}`.replace(/session_key|cart key/g, '')).not.toMatch(forbidden);
    }
  });

  it('a key that is not listed is refused', () => {
    const { storageSet } = jest.requireActual('@/app/lib/browser-storage') as {
      storageSet: (area: 'local' | 'session', key: string, value: string) => void;
    };
    expect(() => storageSet('local', 'blackdog_access', 'x')).toThrow(/not a listed/);
    expect(() => storageSet('session', 'ui-theme', 'dark')).toThrow(/not a listed/); // wrong area
  });

  it('the session lives in HttpOnly cookies only: no code reads them', () => {
    const readers = FILES
      .filter(({ text }) => /blackdog_(access|refresh)/.test(text))
      .map(({ path }) => path);
    expect(readers).toEqual([]);
  });
});

describe('the preferences people already have keep working (owner, point 7)', () => {
  const { STORAGE_KEYS, storageGet, storageSet, storageRemove } = jest.requireActual('@/app/lib/browser-storage') as {
    STORAGE_KEYS: Record<string, { area: 'local' | 'session'; prefix?: boolean }>;
    storageGet: (a: 'local' | 'session', k: string) => string | null;
    storageSet: (a: 'local' | 'session', k: string, v: string) => void;
    storageRemove: (a: 'local' | 'session', k: string) => void;
  };

  it('every key the app used before keeps its name and its storage', () => {
    expect(Object.fromEntries(Object.entries(STORAGE_KEYS).map(([key, entry]) => [key, entry.area]))).toEqual({
      'internal-selected-company': 'local',
      'bds-inventory-mode': 'local',
      'ui-theme': 'local',
      'bd.consent.v2': 'local',
      'storefront_session_key': 'local',
      'blackdog_session_key': 'local',
      'bd.purchase.': 'local',
      'bd.session.generation': 'local',
      'bd.auth.username': 'session',
      'pos-sale-in-flight': 'session',
      'bd.checkout.reference': 'session',
      'storefront_coupon': 'session',
      'blackdog_coupon': 'session',
      'bd.checkout.begun': 'session',
    });
  });

  it('a value written before the wrapper existed is read by it, and back', () => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    for (const [key, entry] of Object.entries(STORAGE_KEYS)) {
      const name = entry.prefix ? `${key}evento-1` : key;
      const raw = entry.area === 'local' ? window.localStorage : window.sessionStorage;
      raw.setItem(name, 'antes');
      expect(storageGet(entry.area, name)).toBe('antes');
      storageSet(entry.area, name, 'después');
      expect(raw.getItem(name)).toBe('después');
      storageRemove(entry.area, name);
      expect(raw.getItem(name)).toBeNull();
    }
  });

  it('every call names a listed key in the right storage', () => {
    const offenders: string[] = [];
    let seen = 0;
    for (const { path, text } of FILES) {
      if (path === STORAGE_DOOR) continue;
      const constants = Object.fromEntries(
        [...text.matchAll(/const\s+([A-Z_]+)\s*=\s*"([^"]+)"/g)].map((m) => [m[1], m[2]]),
      );
      for (const call of text.matchAll(/storage(?:Get|Set|Remove)\(\s*"(local|session)",\s*([A-Za-z_]+|"[^"]+")/g)) {
        const [, area, ref] = call;
        let key = ref.startsWith('"') ? ref.slice(1, -1) : constants[ref];
        if (key === undefined && ref === 'SELECTED_COMPANY_KEY') key = 'internal-selected-company';
        if (key === undefined && ref === 'THEME_STORAGE_KEY') key = 'ui-theme';
        if (key === undefined && ref === 'CONSENT_KEY') key = 'bd.consent.v2';
        const entry = key === undefined ? undefined
          : STORAGE_KEYS[key] ?? Object.entries(STORAGE_KEYS).find(([name, e]) => e.prefix && key!.startsWith(name))?.[1];
        seen += 1;
        if (!entry || entry.area !== area) offenders.push(`${path}: ${area} ${ref}`);
      }
    }
    expect(seen).toBeGreaterThanOrEqual(30); // every call site was read, not none
    expect(offenders).toEqual([]);
  });
});
