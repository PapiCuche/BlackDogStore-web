import {
  basketHash, forgetSaleInFlight, isDefinitiveRefusal, readSaleInFlight, rememberSaleInFlight,
} from '@/app/admin/lib/pos-in-flight';
import { PosConflictError } from '@/app/admin/lib/internal-api';

/**
 * AUTH-C (review P2-6) — the mark a till keeps while a sale is in flight.
 *
 * What it may hold (a random key, a hash, a time, two ids), whose it is (this
 * cashier in this company), and when it is forgotten (a definitive answer).
 */

beforeEach(() => window.sessionStorage.clear());

describe('the in-flight mark', () => {
  it('holds a key, a hash of the lines and a time — nothing about the customer', () => {
    rememberSaleInFlight({ key: 'k-1', basket: basketHash([{ product: 1, quantity: 2 }]), user: 7, company: 3 });

    const raw = JSON.parse(window.sessionStorage.getItem('pos-sale-in-flight')!);
    expect(Object.keys(raw).sort()).toEqual(['at', 'basket', 'company', 'key', 'user']);
    expect(raw.basket).toMatch(/^[0-9a-f]{8}$/);
  });

  it('two baskets hash differently; the same basket the same', () => {
    expect(basketHash([{ product: 1, quantity: 2 }])).toBe(basketHash([{ product: 1, quantity: 2 }]));
    expect(basketHash([{ product: 1, quantity: 2 }])).not.toBe(basketHash([{ product: 1, quantity: 3 }]));
  });

  it('is only read back by the same cashier in the same company', () => {
    rememberSaleInFlight({ key: 'k-1', basket: 'abc', user: 7, company: 3 });

    expect(readSaleInFlight(7, 3)?.key).toBe('k-1');
    expect(readSaleInFlight(8, 3)).toBeNull();
    expect(readSaleInFlight(7, 4)).toBeNull();
  });

  it('a mark older than a day is dropped', () => {
    window.sessionStorage.setItem('pos-sale-in-flight', JSON.stringify({
      key: 'k', basket: 'b', user: 7, company: 3, at: Date.now() - 25 * 3600 * 1000,
    }));
    expect(readSaleInFlight(7, 3)).toBeNull();
    expect(window.sessionStorage.getItem('pos-sale-in-flight')).toBeNull();
  });

  it('is forgotten on purpose', () => {
    rememberSaleInFlight({ key: 'k-1', basket: 'abc', user: 7, company: 3 });
    forgetSaleInFlight();
    expect(readSaleInFlight(7, 3)).toBeNull();
  });

  it('a 4xx refusal is an answer; a 5xx, a closed session or a lost answer is not', () => {
    const withStatus = (status: number) => Object.assign(new Error('x'), { status });
    expect(isDefinitiveRefusal(withStatus(400))).toBe(true);
    expect(isDefinitiveRefusal(withStatus(403))).toBe(true);
    expect(isDefinitiveRefusal(withStatus(401))).toBe(false);
    expect(isDefinitiveRefusal(withStatus(502))).toBe(false);
    expect(isDefinitiveRefusal(new TypeError('Failed to fetch'))).toBe(false);
    expect(new PosConflictError('ya usada', 12)).toBeInstanceOf(Error);
  });
});
