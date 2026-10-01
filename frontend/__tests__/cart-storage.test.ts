import {
  clearStoredCoupon,
  getSessionKey,
  readStoredCoupon,
  writeStoredCoupon,
} from '@/app/lib/cart';

describe('tenant-neutral cart storage migration', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('creates a generic guest session key and mirrors it for legacy tabs', () => {
    const key = getSessionKey();

    expect(key).toMatch(/^guest-/);
    expect(window.localStorage.getItem('storefront_session_key')).toBe(key);
    expect(window.localStorage.getItem('blackdog_session_key')).toBe(key);
  });

  it('migrates an existing legacy guest session without changing the cart identity', () => {
    window.localStorage.setItem('blackdog_session_key', 'guest-existing');

    expect(getSessionKey()).toBe('guest-existing');
    expect(window.localStorage.getItem('storefront_session_key')).toBe('guest-existing');
  });

  it('migrates a legacy coupon to the generic session key', () => {
    window.sessionStorage.setItem(
      'blackdog_coupon',
      JSON.stringify({ code: 'WELCOME10', discount_percent: 10 }),
    );

    expect(readStoredCoupon()).toEqual({
      code: 'WELCOME10',
      discount_percent: 10,
    });
    expect(window.sessionStorage.getItem('blackdog_coupon')).toBeNull();
    expect(JSON.parse(window.sessionStorage.getItem('storefront_coupon') ?? '{}')).toEqual({
      code: 'WELCOME10',
      discount_percent: 10,
    });
  });

  it('clears malformed coupon data instead of leaking it into checkout', () => {
    window.sessionStorage.setItem(
      'storefront_coupon',
      JSON.stringify({ code: 123, discount_percent: '10' }),
    );

    expect(readStoredCoupon()).toBeNull();
    expect(window.sessionStorage.getItem('storefront_coupon')).toBeNull();
    expect(window.sessionStorage.getItem('blackdog_coupon')).toBeNull();
  });

  it('writes and clears coupons only through the storage helper', () => {
    writeStoredCoupon({ code: 'SAVE15', discount_percent: 15 });

    expect(readStoredCoupon()).toEqual({ code: 'SAVE15', discount_percent: 15 });

    clearStoredCoupon();
    expect(window.sessionStorage.getItem('storefront_coupon')).toBeNull();
    expect(window.sessionStorage.getItem('blackdog_coupon')).toBeNull();
  });
});
