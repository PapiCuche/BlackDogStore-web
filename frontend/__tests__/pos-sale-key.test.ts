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
