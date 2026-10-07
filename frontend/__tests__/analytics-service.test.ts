import { CONSENT_KEY, writeConsent } from '@/app/lib/consent';
import { EVENT_NAMES, type AnalyticsEvent, type MeasurementConfig } from '@/app/lib/analytics/events';
import {
  checkoutContext, configure, pageView, resetAnalyticsForTests, setConsent, setHardNavigationForTests, track,
} from '@/app/lib/analytics/service';

/**
 * ANALYTICS-MARKETING · the analytics service, in a browser with no network.
 *
 * The providers' scripts never arrive here: what is observed is what the shop
 * hands to them — the `<script>` tags it adds, and the calls it queues on
 * `gtag`, `fbq` and `ttq`. That is exactly the surface that must obey consent.
 */

const ALL: MeasurementConfig = { providers: {
  google_analytics: { measurement_id: 'G-NOESREAL01', purchase: 'browser' },
  meta: { pixel_id: '123456789012345', purchase: 'both' },
  tiktok: { pixel_code: 'C0NOESREAL0NOESREAL0', purchase: 'both' },
} };
const HOSTS = { ga: 'googletagmanager.com', meta: 'connect.facebook.net', tiktok: 'analytics.tiktok.com' };
const ITEM = { id: '42', name: 'iPhone 15 Pro', category: 'iPhone', price: 4999, quantity: 1 };
const PURCHASE: AnalyticsEvent = {
  name: 'PURCHASE', eventId: 'purchase.77', transactionId: '77', currency: 'PEN', value: 4999, tax: 762.56,
  items: [ITEM],
};

const scripts = () => Array.from(document.querySelectorAll('script[src]')).map((node) => node.getAttribute('src') ?? '');
const loadedFrom = (host: string) => scripts().some((src) => src.includes(host));
const w = window as unknown as Record<string, unknown>;

/** What was handed to gtag: `arguments` objects pushed on the data layer. */
const ga = () => ((window.dataLayer ?? []) as IArguments[]).map((entry) => Array.from(entry));
const gaEvents = () => ga().filter((call) => call[0] === 'event').map((call) => ({ name: call[1] as string, params: call[2] as Record<string, unknown> }));
const meta = () => ((window.fbq?.queue ?? []) as unknown[][]);
const metaEvents = () => meta().filter((call) => call[0] === 'track').map((call) => ({ name: call[1] as string, params: call[2] as Record<string, unknown>, options: call[3] as { eventID: string } }));
const tiktok = () => ((window.ttq ?? []) as unknown as unknown[][]);
const tiktokEvents = () => tiktok().filter((call) => call[0] === 'track').map((call) => ({ name: call[1] as string, params: call[2] as Record<string, unknown>, options: call[3] as { event_id: string } }));

/** As a fresh document would be at this address: no script has seen anything yet. */
function goTo(path: string) {
  nativePush({}, '', path);
}

// The History API as the browser gives it, kept before the service wraps it.
const nativePush = window.history.pushState.bind(window.history);
const nativeReplace = window.history.replaceState.bind(window.history);
let left: { url: string; how: string }[] = [];

beforeEach(() => {
  resetAnalyticsForTests();
  window.history.pushState = nativePush;
  window.history.replaceState = nativeReplace;
  left = [];
  setHardNavigationForTests((url, how) => { left.push({ url, how }); });
  window.localStorage.clear();
  document.head.querySelectorAll('script').forEach((node) => node.remove());
  document.cookie.split(';').forEach((entry) => { document.cookie = `${entry.split('=')[0].trim()}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`; });
  for (const name of ['dataLayer', 'gtag', 'fbq', '_fbq', 'ttq', 'TiktokAnalyticsObject']) delete w[name];
  goTo('/');
});

describe('consent decides', () => {
  it('before an answer nothing is loaded and nothing is sent', () => {
    configure(ALL);
    pageView();
    track({ name: 'VIEW_ITEM', item: ITEM, currency: 'PEN', value: 4999 });
    expect(scripts()).toEqual([]);
    expect([window.gtag, window.fbq, window.ttq]).toEqual([undefined, undefined, undefined]);
  });

  it('a refusal is the same as no answer', () => {
    writeConsent({ analytics: false, marketing: false });
    configure(ALL);
    pageView();
    expect(scripts()).toEqual([]);
  });

  it('analytics alone brings Google and nobody else', () => {
    writeConsent({ analytics: true, marketing: false });
    configure(ALL);
    pageView();
    expect([loadedFrom(HOSTS.ga), loadedFrom(HOSTS.meta), loadedFrom(HOSTS.tiktok)]).toEqual([true, false, false]);
    expect(gaEvents().map((event) => event.name)).toEqual(['page_view']);
    expect(window.fbq).toBeUndefined();
    expect(window.ttq).toBeUndefined();
  });

  it('marketing alone brings Meta and TikTok and not Google', () => {
    writeConsent({ analytics: false, marketing: true });
    configure(ALL);
    pageView();
    expect([loadedFrom(HOSTS.ga), loadedFrom(HOSTS.meta), loadedFrom(HOSTS.tiktok)]).toEqual([false, true, true]);
    expect(metaEvents().map((event) => event.name)).toEqual(['PageView']);
    expect(tiktok().filter((call) => call[0] === 'page')).toHaveLength(1);
    expect(window.gtag).toBeUndefined();
  });

  it('accepting later loads then, and counts the page the visitor is on — nothing earlier', () => {
    configure(ALL);
    pageView();
    track({ name: 'SEARCH', term: 'funda' });
    setConsent({ analytics: true, marketing: true });
    expect(scripts()).toHaveLength(3);
    expect(gaEvents().map((event) => event.name)).toEqual(['page_view']);        // not the search from before
    expect(metaEvents().map((event) => event.name)).toEqual(['PageView']);
  });

  it('withdrawing stops the next event, and tells each provider', () => {
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
    pageView();
    setConsent({ analytics: false, marketing: false });
    track({ name: 'ADD_TO_CART', item: ITEM, currency: 'PEN', value: 4999 });

    expect(gaEvents().map((event) => event.name)).toEqual(['page_view']);
    expect(metaEvents().map((event) => event.name)).toEqual(['PageView']);
    expect(tiktokEvents()).toEqual([]);
    expect(w['ga-disable-G-NOESREAL01']).toBe(true);
    expect(ga().filter((call) => call[0] === 'consent').pop()).toEqual(['consent', 'update', {
      analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' }]);
    expect(meta().filter((call) => call[0] === 'consent').pop()).toEqual(['consent', 'revoke']);
    expect(tiktok().filter((call) => call[0] === 'disableCookie')).toHaveLength(1);
  });

  it('Google is told «denied» first and only then what was accepted', () => {
    writeConsent({ analytics: true, marketing: false });
    configure(ALL);
    const calls = ga();
    expect(calls[0]).toEqual(['consent', 'default', {
      analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' }]);
    expect(calls[1]).toEqual(['consent', 'update', {
      analytics_storage: 'granted', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' }]);
    expect(calls.findIndex((call) => call[0] === 'config')).toBeGreaterThan(1);
  });
});

describe('what exists comes from the server, at run time', () => {
  it('a provider the console did not activate is never loaded, whatever was accepted', () => {
    writeConsent({ analytics: true, marketing: true });
    configure({ providers: { meta: { pixel_id: '123456789012345', purchase: 'browser' } } });
    pageView();
    expect([loadedFrom(HOSTS.ga), loadedFrom(HOSTS.meta), loadedFrom(HOSTS.tiktok)]).toEqual([false, true, false]);
  });

  it('an id that is not what its provider issues is not put in a script address', () => {
    writeConsent({ analytics: true, marketing: true });
    configure({ providers: {
      google_analytics: { measurement_id: 'G-X"></script><script>alert(1)', purchase: 'browser' },
      meta: { pixel_id: "1');alert(1);//", purchase: 'browser' },
      tiktok: { pixel_code: 'https://evil.example/x.js', purchase: 'browser' },
    } });
    pageView();
    expect(scripts()).toEqual([]);
  });

  it('scripts come only from the three official hosts, over https', () => {
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
    expect(scripts().sort()).toEqual([
      'https://analytics.tiktok.com/i18n/pixel/events.js?sdkid=C0NOESREAL0NOESREAL0&lib=ttq',
      'https://connect.facebook.net/en_US/fbevents.js',
      'https://www.googletagmanager.com/gtag/js?id=G-NOESREAL01',
    ]);
  });

  it('events said before the server answered wait for it, and still obey consent', () => {
    writeConsent({ analytics: true, marketing: false });
    pageView();
    track({ name: 'VIEW_ITEM', item: ITEM, currency: 'PEN', value: 4999 });
    expect(scripts()).toEqual([]);
    configure(ALL);
    expect(gaEvents().map((event) => event.name)).toEqual(['page_view', 'view_item']);
    expect(window.fbq).toBeUndefined();
  });

  it('a server that does not answer leaves the shop unmeasured and working', () => {
    writeConsent({ analytics: true, marketing: true });
    configure(undefined as unknown as MeasurementConfig);
    expect(() => { pageView(); track(PURCHASE); }).not.toThrow();
    expect(scripts()).toEqual([]);
  });
});

describe('a page is viewed once and a sale is bought once', () => {
  beforeEach(() => {
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
  });

  it('re-rendering the same route is not a second page view', () => {
    pageView(); pageView(); pageView();
    goTo('/product');
    pageView(); pageView();
    expect(gaEvents().map((event) => event.params.page_path)).toEqual(['/', '/product']);
    expect(metaEvents().filter((event) => event.name === 'PageView')).toHaveLength(2);
  });

  it('a purchase is emitted once per order, even after a reload', () => {
    track(PURCHASE);
    track(PURCHASE);
    resetAnalyticsForTests();                    // a reload: the module starts again, the browser remembers
    configure(ALL);
    track(PURCHASE);
    expect(metaEvents().filter((event) => event.name === 'Purchase')).toHaveLength(1);
    expect(gaEvents().filter((event) => event.name === 'purchase')).toHaveLength(1);
  });

  it('the browser copy carries the id the server uses, so the provider keeps one of the two', () => {
    track(PURCHASE);
    expect(metaEvents().find((event) => event.name === 'Purchase')?.options).toEqual({ eventID: 'purchase.77' });
    expect(tiktokEvents().find((event) => event.name === 'Purchase')?.options).toEqual({ event_id: 'purchase.77' });
    expect(gaEvents().find((event) => event.name === 'purchase')?.params.transaction_id).toBe('77');
  });

  it('where the server sends the purchase alone, the browser does not', () => {
    resetAnalyticsForTests();
    window.localStorage.removeItem('bd.purchase.purchase.77');
    for (const name of ['dataLayer', 'gtag']) delete w[name];
    configure({ providers: { google_analytics: { measurement_id: 'G-NOESREAL01', purchase: 'server' } } });
    track(PURCHASE);
    track({ name: 'ADD_TO_CART', item: ITEM, currency: 'PEN', value: 4999 });
    expect(gaEvents().map((event) => event.name)).toEqual(['add_to_cart']);
  });

  it('every other event of a provider gets its own id', () => {
    track({ name: 'ADD_TO_CART', item: ITEM, currency: 'PEN', value: 4999 });
    track({ name: 'ADD_TO_CART', item: ITEM, currency: 'PEN', value: 4999 });
    const ids = metaEvents().filter((event) => event.name === 'AddToCart').map((event) => event.options.eventID);
    expect(new Set(ids).size).toBe(2);
    expect(tiktokEvents().filter((event) => event.name === 'AddToCart')[0].options.event_id).toBe(ids[0]);
  });
});

describe('no provider on a private address', () => {
  const PRIVATE = [
    '/seguimiento/AbCdEf0123456789AbCdEf0123456789', '/auth/reset-password?token=AbCdEf0123456789AbCdEf0123456789',
    '/auth/verify-email?token=abc', '/invitacion?token=abc', '/admin', '/admin/settings/integrations', '/orders', '/repairs',
    '/product?search=ana%40example.com', '/product?search=490154203237518', '/checkout/success?reference=12345678901234567890',
  ];

  it.each(PRIVATE)('%s: no script is loaded and no event is sent', (address) => {
    writeConsent({ analytics: true, marketing: true });
    goTo(address);
    configure(ALL);
    pageView();
    track({ name: 'CONTACT', channel: 'whatsapp' });
    expect(scripts()).toEqual([]);
    expect([window.gtag, window.fbq, window.ttq]).toEqual([undefined, undefined, undefined]);
  });

  it('arriving on one and moving to the shop loads them there, with a clean address', () => {
    writeConsent({ analytics: true, marketing: true });
    goTo('/seguimiento/AbCdEf0123456789AbCdEf0123456789');
    configure(ALL);
    pageView();
    goTo('/product');
    pageView();
    expect(scripts()).toHaveLength(3);
    expect(JSON.stringify([ga(), meta(), tiktok()])).not.toContain('AbCdEf0123456789');
  });

  it('with the scripts already there, an event on a private address is still not sent', () => {
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
    pageView();
    goTo('/admin/orders');
    pageView();
    track({ name: 'LOGIN', method: 'password' });
    expect(gaEvents().map((event) => event.params.page_path)).toEqual(['/']);
  });

  it('what Google is told about where is a path: no query string, no fragment', () => {
    writeConsent({ analytics: true, marketing: false });
    goTo('/product?category=iphone&utm_source=x#resenas');
    configure(ALL);
    pageView();
    const [view] = gaEvents();
    expect(view.params.page_location).toBe('http://localhost/product');
    expect(view.params.page_path).toBe('/product');
  });

  it('a search that looks like a person or a device is not repeated', () => {
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
    for (const term of ['ana@example.com', '490154203237518', 'F2LXK1ABC9']) track({ name: 'SEARCH', term });
    track({ name: 'SEARCH', term: 'funda iphone 15' });
    expect(gaEvents().filter((event) => event.name === 'search').map((event) => event.params.search_term))
      .toEqual(['[REDACTED]', '[REDACTED]', '[REDACTED]', 'funda iphone 15']);
    expect(JSON.stringify([meta(), tiktok()])).not.toMatch(/ana@example|490154203237518|F2LXK1ABC9/);
  });
});

describe('each provider is told what it has a name for', () => {
  const EVENTS: AnalyticsEvent[] = [
    { name: 'VIEW_ITEM_LIST', listName: 'Catálogo', items: [ITEM] },
    { name: 'SELECT_ITEM', listName: 'Catálogo', item: ITEM },
    { name: 'VIEW_ITEM', item: ITEM, currency: 'PEN', value: 4999 },
    { name: 'SEARCH', term: 'funda' },
    { name: 'ADD_TO_CART', item: ITEM, currency: 'PEN', value: 4999 },
    { name: 'REMOVE_FROM_CART', item: ITEM, currency: 'PEN', value: 4999 },
    { name: 'VIEW_CART', items: [ITEM], currency: 'PEN', value: 4999 },
    { name: 'BEGIN_CHECKOUT', items: [ITEM], currency: 'PEN', value: 4999, coupon: 'BIENVENIDA' },
    { name: 'ADD_SHIPPING_INFO', items: [ITEM], currency: 'PEN', value: 4999, shippingTier: 'pickup_store' },
    { name: 'ADD_PAYMENT_INFO', items: [ITEM], currency: 'PEN', value: 4999, paymentType: 'card' },
    PURCHASE,
    { name: 'SIGN_UP', method: 'password' },
    { name: 'LOGIN', method: 'google' },
    { name: 'LEAD', source: 'servicio' },
    { name: 'CONTACT', channel: 'whatsapp' },
  ];

  beforeEach(() => {
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
    pageView();
    EVENTS.forEach(track);
  });

  it('the list above is every event the shop can say', () => {
    expect(['PAGE_VIEW', ...EVENTS.map((event) => event.name)].sort()).toEqual([...EVENT_NAMES].sort());
  });

  it('Google Analytics: its recommended events, by their own names', () => {
    expect(gaEvents().map((event) => event.name)).toEqual([
      'page_view', 'view_item_list', 'select_item', 'view_item', 'search', 'add_to_cart', 'remove_from_cart', 'view_cart',
      'begin_checkout', 'add_shipping_info', 'add_payment_info', 'purchase', 'sign_up', 'login', 'generate_lead', 'generate_lead',
    ]);
    const purchase = gaEvents().find((event) => event.name === 'purchase')!.params;
    expect(purchase).toMatchObject({
      transaction_id: '77', currency: 'PEN', value: 4999, tax: 762.56,
      items: [{ item_id: '42', item_name: 'iPhone 15 Pro', item_category: 'iPhone', price: 4999, quantity: 1 }],
    });
    expect(gaEvents().find((event) => event.name === 'begin_checkout')!.params.coupon).toBe('BIENVENIDA');
    expect(gaEvents().find((event) => event.name === 'add_shipping_info')!.params.shipping_tier).toBe('pickup_store');
  });

  it('Meta: its standard events, and nothing invented for the rest', () => {
    expect(metaEvents().map((event) => event.name)).toEqual([
      'PageView', 'ViewContent', 'Search', 'AddToCart', 'InitiateCheckout', 'AddPaymentInfo', 'Purchase',
      'CompleteRegistration', 'Lead', 'Contact',
    ]);
    expect(metaEvents().find((event) => event.name === 'Purchase')!.params).toMatchObject({
      currency: 'PEN', value: 4999, order_id: '77', content_type: 'product', content_ids: ['42'],
      contents: [{ id: '42', quantity: 1, item_price: 4999 }],
    });
  });

  it('TikTok: its standard events, and the purchase is called Purchase', () => {
    expect(tiktokEvents().map((event) => event.name)).toEqual([
      'ViewContent', 'Search', 'AddToCart', 'InitiateCheckout', 'AddPaymentInfo', 'Purchase',
      'CompleteRegistration', 'Lead', 'Contact',
    ]);
    expect(tiktokEvents().find((event) => event.name === 'Purchase')!.params).toMatchObject({
      currency: 'PEN', value: 4999, order_id: '77',
      contents: [{ content_id: '42', content_name: 'iPhone 15 Pro', price: 4999, quantity: 1 }],
    });
  });

  it('Meta is initialised with the pixel and nothing about the person, and does not read the page by itself', () => {
    expect(meta().filter((call) => call[0] === 'init')).toEqual([['init', '123456789012345']]);
    expect(meta().filter((call) => call[0] === 'set')).toEqual([['set', 'autoConfig', false, '123456789012345']]);
    expect(window.fbq?.disablePushState).toBe(true);
    expect(tiktok().filter((call) => call[0] === 'identify')).toEqual([]);
  });
});

describe('what travels with a checkout', () => {
  it('the answer itself, and the identifiers only of what was accepted', () => {
    document.cookie = '_ga=GA1.1.1234567890.1700000000; path=/';
    document.cookie = '_ga_NOESREAL01=GS1.1.1700000123.1.0.1700000123.0.0.0; path=/';
    document.cookie = '_fbp=fb.1.1700000000000.1234567890; path=/';
    document.cookie = '_ttp=aBcDeFgHiJkLmNoPqRsT; path=/';

    configure(ALL);
    expect(checkoutContext()).toEqual({ consent: { analytics: false, marketing: false } });

    setConsent({ analytics: true, marketing: false });
    expect(checkoutContext()).toEqual({
      consent: { analytics: true, marketing: false }, ga_client_id: '1234567890.1700000000', ga_session_id: '1700000123' });

    setConsent({ analytics: true, marketing: true });
    expect(checkoutContext()).toMatchObject({
      consent: { analytics: true, marketing: true }, fbp: 'fb.1.1700000000000.1234567890', ttp: 'aBcDeFgHiJkLmNoPqRsT' });
  });

  it('never carries anything else of the browser', () => {
    document.cookie = 'csrftoken=NoEsReal; path=/';
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
    const keys = Object.keys(checkoutContext());
    expect(keys.every((key) => ['consent', 'ga_client_id', 'ga_session_id', 'fbp', 'fbc', 'ttp', 'ttclid'].includes(key))).toBe(true);
  });
});

describe('it cannot break the shop', () => {
  it('a provider whose script throws costs a measurement and nothing else', () => {
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
    window.gtag = () => { throw new Error('bloqueado'); };
    (window.fbq as unknown) = () => { throw new Error('bloqueado'); };
    expect(() => { pageView(); track(PURCHASE); setConsent({ analytics: false, marketing: false }); }).not.toThrow();
    expect(tiktokEvents().map((event) => event.name)).toEqual(['Purchase']);
  });

  it('a stored answer somebody edited by hand is not a consent', () => {
    window.localStorage.setItem(CONSENT_KEY, JSON.stringify({ version: 1, analytics: 'yes', marketing: 1 }));
    configure(ALL);
    pageView();
    expect(scripts()).toEqual([]);
  });
});

/**
 * REVIEW (P1). A provider's script that is already on the page keeps working when
 * the application changes route without loading a new document: it reads the
 * address by itself, and Google's sends its own page views on history changes.
 * Not sending OUR events there protects nothing.
 *
 * So the rule is about documents: while a provider's script is loaded, a
 * navigation to an address it may not see is not done inside this document. The
 * script is told to stop, and the browser is sent there with a full load — into
 * a document that has no such script.
 */
describe('a loaded script never sees a private address', () => {
  const TOKEN = '/seguimiento/AbCdEf0123456789AbCdEf0123456789';

  beforeEach(() => {
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
    pageView();
    expect(scripts()).toHaveLength(3);
  });

  it.each([
    ['the panel', '/admin/orders'], ['a customer\'s orders', '/orders'], ['a tracking link', TOKEN],
    ['a password reset', '/auth/reset-password?token=AbCdEf0123456789AbCdEf0123456789'],
    ['a search for an IMEI', '/product?search=490154203237518'],
  ])('going to %s inside the application becomes a full navigation, and the address never enters this document', (_what, address) => {
    window.history.pushState({}, '', address);                  // what the router does on a <Link> or router.push
    expect(left).toEqual([{ url: `http://localhost${address}`, how: 'assign' }]);
    expect(window.location.pathname).toBe('/');                 // the History API was never told
  });

  it('replacing the address with a private one is a full navigation too', () => {
    window.history.replaceState({}, '', '/product?search=ana%40example.com');
    expect(left).toEqual([{ url: 'http://localhost/product?search=ana%40example.com', how: 'replace' }]);
    expect(window.location.search).toBe('');
  });

  it('before leaving, every provider is told to stop', () => {
    window.history.pushState({}, '', TOKEN);
    expect(w['ga-disable-G-NOESREAL01']).toBe(true);
    expect(meta().filter((call) => call[0] === 'consent').pop()).toEqual(['consent', 'revoke']);
    expect(tiktok().filter((call) => call[0] === 'revokeConsent')).toHaveLength(1);
  });

  it('the Back button into a private address reloads, and no later listener hears of it', () => {
    goTo(TOKEN);                                                // the browser has already moved the address
    const later = jest.fn();
    window.addEventListener('popstate', later);                 // registered after ours, as a provider's would be
    window.dispatchEvent(new PopStateEvent('popstate'));
    window.removeEventListener('popstate', later);
    expect(left).toEqual([{ url: `http://localhost${TOKEN}`, how: 'reload' }]);
    expect(later).not.toHaveBeenCalled();
    expect(w['ga-disable-G-NOESREAL01']).toBe(true);
  });

  it('ordinary navigation in the shop stays inside the document', () => {
    window.history.pushState({}, '', '/product?category=iphone');
    window.history.replaceState({}, '', '/product?category=iphone&search=funda');
    expect(left).toEqual([]);
    expect(window.location.pathname + window.location.search).toBe('/product?category=iphone&search=funda');
  });

  it('with no script loaded there is nothing to protect: the application navigates as usual', () => {
    resetAnalyticsForTests();
    window.history.pushState = nativePush;
    window.localStorage.clear();
    configure(ALL);                                             // nobody accepted anything
    window.history.pushState({}, '', '/orders');
    expect(left).toEqual([]);
    expect(window.location.pathname).toBe('/orders');
  });

  it('Google is also told, for whatever it sends by itself, an address without parameters', () => {
    goTo('/product?category=iphone&utm_source=x');
    pageView();
    const set = ga().filter((call) => call[0] === 'set').pop() as unknown[];
    expect(set[1]).toMatchObject({ page_location: 'http://localhost/product' });
  });
});

/**
 * REVIEW (P2). Meta and TikTok can be told, from their own dashboards, to read
 * the e-mail and phone a visitor types into a form («automatic advanced
 * matching»). Nothing in this code can switch that off — so their scripts are
 * not on the pages that have such a form. Google's is: it does not read fields.
 */
describe('marketing pixels are not on the pages with a personal-data form', () => {
  it.each(['/checkout', '/auth', '/auth?next=%2Fproduct'])('%s: Google only', (address) => {
    writeConsent({ analytics: true, marketing: true });
    goTo(address);
    configure(ALL);
    pageView();
    expect([loadedFrom(HOSTS.ga), loadedFrom(HOSTS.meta), loadedFrom(HOSTS.tiktok)]).toEqual([true, false, false]);
    expect([window.fbq, window.ttq]).toEqual([undefined, undefined]);
  });

  it('with them loaded, going to the checkout leaves the document; Google alone would have stayed', () => {
    writeConsent({ analytics: true, marketing: true });
    configure(ALL);
    pageView();
    window.history.pushState({}, '', '/checkout');
    expect(left).toEqual([{ url: 'http://localhost/checkout', how: 'assign' }]);

    resetAnalyticsForTests();
    window.history.pushState = nativePush;
    left = [];
    setHardNavigationForTests((url, how) => { left.push({ url, how }); });
    writeConsent({ analytics: true, marketing: false });
    configure(ALL);
    window.history.pushState({}, '', '/checkout');
    expect(left).toEqual([]);
  });

  it('the page after paying has no form: the purchase reaches them there', () => {
    writeConsent({ analytics: true, marketing: true });
    goTo('/checkout/success');
    configure(ALL);
    pageView();
    track(PURCHASE);
    expect(metaEvents().map((event) => event.name)).toEqual(['PageView', 'Purchase']);
    expect(tiktokEvents().map((event) => event.name)).toEqual(['Purchase']);
  });
});

describe('TikTok is told about consent with its own consent calls', () => {
  it('granted on load, revoked on withdrawal', () => {
    writeConsent({ analytics: false, marketing: true });
    configure(ALL);
    expect(tiktok().filter((call) => call[0] === 'grantConsent')).toHaveLength(1);
    setConsent({ analytics: false, marketing: false });
    expect(tiktok().filter((call) => call[0] === 'revokeConsent')).toHaveLength(1);
    expect(tiktok().filter((call) => call[0] === 'disableCookie')).toHaveLength(1);
  });
});
