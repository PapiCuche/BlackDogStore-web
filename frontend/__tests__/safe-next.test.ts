import { safeInternalNextPath } from '@/app/lib/safe-next';

/**
 * `?next=` — H4.1.1.
 *
 * El login lleva a donde diga el enlace, y un enlace lo escribe cualquiera. Lo
 * que estas pruebas fijan es que sólo se vuelve a rutas LOCALES, incluidas las
 * variantes que un navegador normaliza hacia fuera del sitio.
 *
 * Los caracteres de control y la barra invertida se construyen con su código
 * para que la prueba diga exactamente qué carácter examina.
 */
const TAB = String.fromCharCode(9);
const NEWLINE = String.fromCharCode(10);
const BACKSLASH = String.fromCharCode(92);

describe('safeInternalNextPath', () => {
  it.each([
    ['/admin', '/admin'],
    ['/admin/service', '/admin/service'],
    ['/orders', '/orders'],
    ['/invitacion?token=AbC-12_xyz', '/invitacion?token=AbC-12_xyz'],
    ['/admin/service/orders/25#evidencias', '/admin/service/orders/25#evidencias'],
  ])('acepta la ruta local %s', (raw, expected) => {
    expect(safeInternalNextPath(raw)).toBe(expected);
  });

  it.each([
    // Otro origen, o código.
    'https://evil.example',
    'http://evil.example',
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    'data:text/html,hola',
    // Protocolo relativo y sus disfraces.
    '//evil.example',
    `/${BACKSLASH}evil.example`,
    `${BACKSLASH}${BACKSLASH}evil.example`,
    '/%2F%2Fevil.example',
    '/%2f%2fevil.example',
    '/%5Cevil.example',
    '%2F%2Fevil.example',
    '/%252F%252Fevil.example',
    // Caracteres de control, crudos o codificados.
    `/${TAB}evil.example`,
    `/${NEWLINE}evil.example`,
    '/%09/evil.example',
    // No es una ruta.
    'admin',
    '',
    ' /admin',
    // Volver al login después del login.
    '/auth',
    '/auth?next=/admin',
  ])('rechaza %j', (raw) => {
    expect(safeInternalNextPath(raw)).toBeNull();
  });

  it('rechaza lo que no es texto', () => {
    expect(safeInternalNextPath(null)).toBeNull();
    expect(safeInternalNextPath(undefined)).toBeNull();
  });
});
