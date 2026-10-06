/**
 * @jest-environment node
 */

import nextConfig from '@/next.config';

/**
 * FE-AUTH-04 — las páginas salen con cabeceras de seguridad.
 *
 * El frontend no enviaba ninguna. Quedaban abiertas dos cosas concretas: la
 * tienda y el panel se podían incrustar en un marco de otro sitio
 * (clickjacking sobre un panel con sesión), y las páginas que reciben un token
 * de un solo uso en la URL —verificar correo, restablecer contraseña, aceptar
 * una invitación— lo entregaban en `Referer` a cualquier recurso externo.
 *
 * En la topología de producción Caddy añade las suyas; éstas valen también
 * cuando Next se publica sin un proxy delante.
 */

type Rule = { source: string; headers: { key: string; value: string }[] };

async function rules(): Promise<Rule[]> {
  expect(typeof nextConfig.headers).toBe('function');
  return (await nextConfig.headers!()) as Rule[];
}

function valueFor(all: Rule[], source: string, key: string) {
  const rule = all.find((r) => r.source === source);
  return rule?.headers.find((h) => h.key.toLowerCase() === key.toLowerCase())?.value;
}

describe('cabeceras de seguridad del frontend', () => {
  it('todas las rutas prohíben incrustarse en otro sitio', async () => {
    const all = await rules();

    expect(valueFor(all, '/:path*', 'X-Frame-Options')).toBe('DENY');
    expect(valueFor(all, '/:path*', 'Content-Security-Policy')).toContain("frame-ancestors 'none'");
  });

  it('todas las rutas fijan tipo de contenido y política de referente', async () => {
    const all = await rules();

    expect(valueFor(all, '/:path*', 'X-Content-Type-Options')).toBe('nosniff');
    expect(valueFor(all, '/:path*', 'Referrer-Policy')).toBe('strict-origin-when-cross-origin');
  });

  it('todas las rutas renuncian a la cámara, el micrófono y la ubicación', async () => {
    // La aplicación no usa ninguna de las tres. Renunciar a ellas impide que
    // un script de terceros —el SDK de pago, por ejemplo— las pida en nombre
    // de la tienda. Las fotos de evidencia se eligen con un campo de archivo,
    // que no depende de este permiso.
    const policy = valueFor(await rules(), '/:path*', 'Permissions-Policy') ?? '';
    const features = policy.split(',').map((f) => f.trim());

    expect(features).toEqual(expect.arrayContaining(['camera=()', 'microphone=()', 'geolocation=()']));
    // El pago no se toca: el SDK de la pasarela puede necesitarlo.
    expect(policy).not.toMatch(/payment/);
  });

  it('la política de contenido no rompe la aplicación: sólo restringe marcos y base', async () => {
    const csp = valueFor(await rules(), '/:path*', 'Content-Security-Policy') ?? '';
    const directives = csp.split(';').map((d) => d.trim().split(' ')[0]).filter(Boolean).sort();

    // Sin `object-src`: el ticket de caja se imprime como PDF en un marco
    // creado por la página, que hereda esta política.
    expect(directives).toEqual(['base-uri', 'frame-ancestors']);
  });

  // `/seguimiento/<código>`: el código ES la credencial de la reparación. La
  // página ya lo pide con una etiqueta `meta`; la cabecera vale antes de que
  // el navegador haya leído el documento.
  it.each(['/auth/verify-email', '/auth/reset-password', '/invitacion', '/seguimiento/:token'])(
    '%s no entrega su token en el referente',
    async (source) => {
      const all = await rules();
      const general = all.findIndex((r) => r.source === '/:path*');
      const specific = all.findIndex((r) => r.source === source);

      expect(valueFor(all, source, 'Referrer-Policy')).toBe('no-referrer');
      // En Next, si dos reglas fijan la misma cabecera gana la última.
      expect(specific).toBeGreaterThan(general);
    },
  );
});
