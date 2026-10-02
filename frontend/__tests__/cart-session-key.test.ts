import { getSessionKey } from '@/app/lib/cart';

/**
 * FE-AUTH-02 — la clave del carrito anónimo no se puede adivinar.
 *
 * Esa clave es lo único que elige un carrito de invitado en el servidor: quien
 * la conoce lo lee y lo cambia. Se fabricaba con la hora y seis caracteres de
 * `Math.random()`, que no es una fuente segura y deja unos 31 bits que
 * adivinar, con la hora de creación a la vista.
 *
 * Una clave que ya existe no se toca: cambiarla dejaría al visitante sin su
 * carrito.
 */

const UUID = /^guest-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function freshKey() {
  window.localStorage.clear();
  return getSessionKey();
}

describe('clave del carrito anónimo', () => {
  afterEach(() => jest.restoreAllMocks());

  it('no sale de Math.random ni lleva la hora', () => {
    const random = jest.spyOn(Math, 'random').mockReturnValue(0.123456789);
    jest.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000);

    const key = freshKey();

    expect(random).not.toHaveBeenCalled();
    expect(key).not.toContain('1700000000000');
    expect(key).toMatch(UUID);
  });

  it('dos visitantes en el mismo instante reciben claves distintas', () => {
    jest.spyOn(Math, 'random').mockReturnValue(0.5);
    jest.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000);

    expect(freshKey()).not.toBe(freshKey());
  });

  it('funciona donde no hay randomUUID, con getRandomValues', () => {
    const original = globalThis.crypto.randomUUID;
    Object.defineProperty(globalThis.crypto, 'randomUUID', { value: undefined, configurable: true });
    try {
      const a = freshKey();
      const b = freshKey();
      expect(a).toMatch(UUID);
      expect(a).not.toBe(b);
    } finally {
      Object.defineProperty(globalThis.crypto, 'randomUUID', { value: original, configurable: true });
    }
  });

  it('conserva la clave que el visitante ya tenía, aunque sea del formato antiguo', () => {
    window.localStorage.clear();
    window.localStorage.setItem('storefront_session_key', 'guest-1700000000000-abc123');

    expect(getSessionKey()).toBe('guest-1700000000000-abc123');
  });
});
