/**
 * @jest-environment node
 */
import fs from 'fs';
import path from 'path';

/**
 * ANALYTICS-MARKETING · where the providers are allowed to exist in the code.
 *
 * The shop says its own events; three adapter files translate them. This reads
 * the source and fails when that stops being true: a `fbq(...)` dropped into a
 * component is a tracker that ignores consent, and an ID in a `NEXT_PUBLIC_`
 * variable is one that needs a rebuild to change.
 */

const APP = path.join(process.cwd(), 'app');
const ADAPTERS = path.join('app', 'lib', 'analytics', 'adapters');

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sources(full);
    return /\.(ts|tsx)$/.test(entry.name) ? [full] : [];
  });
}

const FILES = sources(APP).map((file) => ({ file: path.relative(process.cwd(), file), text: fs.readFileSync(file, 'utf8') }));
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const outside = FILES.filter(({ file }) => !file.startsWith(ADAPTERS));

test('no page or component calls a provider: gtag, fbq and ttq exist only in the adapters', () => {
  const offenders = outside
    .filter(({ text }) => /\b(gtag|fbq)\s*\(|\bttq\s*\.|\bdataLayer\b|window\.(gtag|fbq|ttq)\b/.test(code(text)))
    .map(({ file }) => file);
  expect(offenders).toEqual([]);
});

test('no provider script address appears outside its adapter', () => {
  const hosts = /googletagmanager\.com|google-analytics\.com|connect\.facebook\.net|facebook\.com\/tr|analytics\.tiktok\.com/;
  expect(outside.filter(({ text }) => hosts.test(code(text))).map(({ file }) => file)).toEqual([]);
});

test('each adapter loads its script from one constant, official, https address', () => {
  const expected: Record<string, string> = {
    'ga4.ts': 'https://www.googletagmanager.com/gtag/js',
    'meta.ts': 'https://connect.facebook.net/en_US/fbevents.js',
    'tiktok.ts': 'https://analytics.tiktok.com/i18n/pixel/events.js',
  };
  for (const [name, address] of Object.entries(expected)) {
    const text = code(fs.readFileSync(path.join(process.cwd(), ADAPTERS, name), 'utf8'));
    const addresses = text.match(/https?:\/\/[^"'`\s)]+/g) ?? [];
    expect(addresses).toEqual([address]);
    expect(text).toContain(`const SCRIPT = "${address}";`);
  }
});

test('what a provider is called with never comes from an environment variable', () => {
  // A NEXT_PUBLIC_ value is compiled in: changing it means rebuilding the frontend.
  const analytics = FILES.filter(({ file }) => file.includes(path.join('lib', 'analytics')) || /AnalyticsProvider|ConsentBanner|consent\.ts/.test(file));
  expect(analytics.length).toBeGreaterThan(6);
  expect(analytics.filter(({ text }) => /process\.env/.test(code(text))).map(({ file }) => file)).toEqual([]);
  expect(FILES.filter(({ text }) => /NEXT_PUBLIC_(GA|GTM|GOOGLE_ANALYTICS|META|FB|FACEBOOK|PIXEL|TIKTOK)/.test(text)).map(({ file }) => file)).toEqual([]);
});

test('no adapter gives a provider anything about the person', () => {
  for (const name of ['ga4.ts', 'meta.ts', 'tiktok.ts']) {
    const text = code(fs.readFileSync(path.join(process.cwd(), ADAPTERS, name), 'utf8'));
    expect(text).not.toMatch(/\b(email|phone|em|ph|fn|ln|external_id|user_id|first_name|last_name|document|address|imei|serial)\b\s*:/i);
    expect(text).not.toMatch(/ttq\??\.identify|"identify"\s*\)|fbq\("init",\s*pixelId,/);
    expect(text).not.toMatch(/location\.href|location\.search|document\.URL/);
  }
});

test('the vocabulary of events has no field for a person, a device or a token', () => {
  const events = code(fs.readFileSync(path.join(APP, 'lib', 'analytics', 'events.ts'), 'utf8'));
  expect(events).not.toMatch(/\b(email|phone|document|address|imei|serial|token|password|notes?)\b\??\s*:/i);
});
