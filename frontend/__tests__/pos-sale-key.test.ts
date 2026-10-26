import { newSaleKey } from '@/app/admin/lib/pos-in-flight';

/**
 * AUTH-C (review of #184) — the key that makes a counter sale safe to retry
 * comes from a SECURE source or not at all. `Math.random` could repeat, and a
 * repeated key answers a new basket with somebody else's sale.
 */

const realCrypto = global.crypto;

afterEach(() => {
  Object.defineProperty(global, 'crypto', { value: realCrypto, configurable: true });
});

function withCrypto(value: unknown) {
  Object.defineProperty(global, 'crypto', { value, configurable: true });
}

it('uses randomUUID when there is one', () => {
  withCrypto({ randomUUID: () => '11111111-2222-4333-8444-555555555555' });
  expect(newSaleKey()).toBe('11111111-2222-4333-8444-555555555555');
});

it('falls back to getRandomValues: 128 random bits', () => {
  withCrypto({ getRandomValues: (bytes: Uint8Array) => bytes.fill(0xab) });
  expect(newSaleKey()).toBe('pos-' + 'ab'.repeat(16));
});

it('with neither, there is no key — never Math.random', () => {
  const random = jest.spyOn(Math, 'random');
  withCrypto(undefined);
  expect(newSaleKey()).toBeNull();
  expect(random).not.toHaveBeenCalled();
});

describe('a key spent on «no se cobró» is never sent again (duplicated tab)', () => {
  it('after 409 key_reserved the next attempt carries a different key', async () => {
    const { posSale } = jest.requireActual('@/app/admin/lib/internal-api') as typeof import('@/app/admin/lib/internal-api');
    const { keyAfterFailure } = jest.requireActual('@/app/admin/lib/pos-in-flight') as typeof import('@/app/admin/lib/pos-in-flight');
    withCrypto({ randomUUID: () => 'nueva-clave-segura-0001' });
    document.cookie = 'csrftoken=csrf';
    const sent: string[] = [];
    global.fetch = jest.fn(async (_url: unknown, init: RequestInit = {}) => {
      sent.push(JSON.parse(String(init.body)).idempotency_key);
      return sent.length === 1
        ? { status: 409, ok: false, headers: { get: () => null }, clone() { return this; },
            json: () => Promise.resolve({ detail: 'Esta venta quedó registrada como «no cobrada».', code: 'key_reserved' }) }
        : { status: 201, ok: true, headers: { get: () => null }, clone() { return this; }, json: () => Promise.resolve({ order_id: 9 }) };
    }) as unknown as typeof fetch;

    let key: string | null = 'clave-reservada-en-otra-pestana';
    const failure = await posSale(3, { idempotency_key: key } as never).catch((e: unknown) => e);
    key = keyAfterFailure(failure, key);
    await posSale(3, { idempotency_key: key } as never);

    expect(sent[0]).toBe('clave-reservada-en-otra-pestana');
    expect(sent[1]).toBe('nueva-clave-segura-0001');
  });

  it('with no secure source, there is no next key: the till refuses', async () => {
    const { keyAfterFailure } = jest.requireActual('@/app/admin/lib/pos-in-flight') as typeof import('@/app/admin/lib/pos-in-flight');
    withCrypto(undefined);
    const reserved = Object.assign(new Error('x'), { status: 409, code: 'key_reserved' });
    expect(keyAfterFailure(reserved, 'vieja')).toBeNull();
  });

  it('any other failure keeps the key: a retry must stay idempotent', () => {
    const { keyAfterFailure } = jest.requireActual('@/app/admin/lib/pos-in-flight') as typeof import('@/app/admin/lib/pos-in-flight');
    withCrypto({ randomUUID: () => 'otra' });
    expect(keyAfterFailure(new TypeError('Failed to fetch'), 'vieja')).toBe('vieja');
    expect(keyAfterFailure(Object.assign(new Error('x'), { status: 502 }), 'vieja')).toBe('vieja');
  });
});

describe('only key_reserved rotates the key (pinned after the #184 review)', () => {
  // Rotating on any of these would turn a retry of a sale that MAY have been
  // charged into a brand-new sale: the customer pays twice.
  const keep = (error: unknown) => {
    const { keyAfterFailure } = jest.requireActual('@/app/admin/lib/pos-in-flight') as typeof import('@/app/admin/lib/pos-in-flight');
    withCrypto({ randomUUID: () => 'una-clave-nueva' });
    return keyAfterFailure(error, 'vieja');
  };
  const api = () => jest.requireActual('@/app/admin/lib/internal-api') as typeof import('@/app/admin/lib/internal-api');

  it('keeps it when the key already names an order (PosConflictError)', () => {
    expect(keep(new (api().PosConflictError)('Esa venta ya existe.', 12))).toBe('vieja');
    expect(keep(new (api().PosConflictError)('Conflicto.', null))).toBe('vieja');
  });

  it('keeps it when stock ran out (PosStockError)', () => {
    expect(keep(new (api().PosStockError)('Sin stock.', []))).toBe('vieja');
  });

  it('keeps it on a 400 and on a 409 without code', () => {
    expect(keep(Object.assign(new Error('x'), { status: 400 }))).toBe('vieja');
    expect(keep(Object.assign(new Error('x'), { status: 409 }))).toBe('vieja');
    expect(keep({ status: 409 })).toBe('vieja');
  });

  it('keeps it on a network failure and on an abort', () => {
    expect(keep(new TypeError('Failed to fetch'))).toBe('vieja');
    expect(keep(new DOMException('The operation was aborted.', 'AbortError'))).toBe('vieja');
  });
});

it('the till applies it on every failed charge', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { readFileSync } = require('fs') as typeof import('fs');
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { join } = require('path') as typeof import('path');
  const page = readFileSync(join(__dirname, '..', 'app', 'admin', 'sales', 'pos', 'page.tsx'), 'utf8');
  const charge = page.indexOf('const result = await posSale(companyId, {');
  expect(charge).toBeGreaterThan(0);
  const start = page.indexOf('} catch (err) {', charge);
  const failure = page.slice(start, page.indexOf('} finally {', start));
  expect(failure).toContain('keyRef.current = keyAfterFailure(err, keyRef.current);');
});
