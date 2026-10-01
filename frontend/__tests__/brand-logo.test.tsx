import { pickLogo, shouldRenderWordmark } from '@/app/lib/brand-logo';
import type { StorefrontLogos } from '@/app/lib/storefront';

/**
 * M12E — la elección de logotipo por contraste.
 *
 * Esto existe porque el defecto ya ocurrió: logo negro sobre cabecera negra,
 * invisible. Lo que se prueba no es que devuelva una cadena, sino que NUNCA
 * devuelva el contraste equivocado — y que cuando no puede acertar, prefiera no
 * dibujar nada antes que dibujar algo ilegible.
 */

const FULL: StorefrontLogos = {
  primary_on_light: '/v-negra.png',
  primary_on_dark: '/v-blanca.png',
  horizontal_on_light: '/h-negra.png',
  horizontal_on_dark: '/h-blanca.png',
  isotype_on_light: '/i-negra.png',
  isotype_on_dark: '/i-blanca.png',
};

const NONE: StorefrontLogos = {
  primary_on_light: '',
  primary_on_dark: '',
  horizontal_on_light: '',
  horizontal_on_dark: '',
  isotype_on_light: '',
  isotype_on_dark: '',
};

describe('el contraste manda', () => {
  it('sobre superficie oscura elige la variante blanca', () => {
    expect(pickLogo(FULL, '', 'header', 'dark')).toBe('/h-blanca.png');
    expect(pickLogo(FULL, '', 'hero', 'dark')).toBe('/v-blanca.png');
  });

  it('sobre superficie clara elige la variante negra', () => {
    expect(pickLogo(FULL, '', 'header', 'light')).toBe('/h-negra.png');
    expect(pickLogo(FULL, '', 'hero', 'light')).toBe('/v-negra.png');
  });

  it('nunca devuelve una variante del contraste contrario', () => {
    // La comprobación que cierra el defecto original.
    for (const placement of ['header', 'hero', 'footer'] as const) {
      expect(pickLogo(FULL, '', placement, 'dark')).toContain('blanca');
      expect(pickLogo(FULL, '', placement, 'light')).toContain('negra');
    }
  });
});

describe('la composición depende del sitio', () => {
  it('la cabecera prefiere el horizontal, como manda el manual', () => {
    expect(pickLogo(FULL, '', 'header', 'dark')).toBe('/h-blanca.png');
  });

  it('el hero prefiere el vertical', () => {
    expect(pickLogo(FULL, '', 'hero', 'dark')).toBe('/v-blanca.png');
  });

  it('si falta la composición ideal usa la otra, del MISMO contraste', () => {
    // Un logo de la forma equivocada se lee; uno del contraste equivocado, no.
    const sinHorizontal = { ...FULL, horizontal_on_dark: '' };
    expect(pickLogo(sinHorizontal, '', 'header', 'dark')).toBe('/v-blanca.png');
  });
});

describe('cuando no hay variante', () => {
  it('sobre claro cae al logo legado, que suele ser negro', () => {
    expect(pickLogo(NONE, '/legado.png', 'header', 'light')).toBe('/legado.png');
  });

  it('sobre oscuro NO dibuja el legado: prefiere nada', () => {
    // El legado puede tener cualquier contraste. Sobre oscuro, no arriesgar:
    // el consumidor cae al nombre de la empresa, que siempre se lee.
    expect(pickLogo(NONE, '/legado.png', 'header', 'dark')).toBe('');
  });

  it('sin nada de nada devuelve vacío en los dos', () => {
    expect(pickLogo(NONE, '', 'header', 'dark')).toBe('');
    expect(pickLogo(NONE, '', 'hero', 'light')).toBe('');
  });

  it('tolera un branding sin la clave `logos`', () => {
    // Un backend anterior a M12E no manda `logos`. La página tiene que
    // renderizar igual.
    expect(pickLogo(undefined, '/legado.png', 'header', 'light')).toBe('/legado.png');
    expect(pickLogo(undefined, '/legado.png', 'header', 'dark')).toBe('');
  });
});

describe('el nombre no se duplica', () => {
  it('con logo, no se escribe el nombre al lado', () => {
    // El lockup ya contiene «BLACK DOG STORE». Escribirlo otra vez duplica la
    // marca justo dentro de su área de protección.
    expect(shouldRenderWordmark('/h-blanca.png')).toBe(false);
  });

  it('sin logo, el nombre ES la identidad y se escribe', () => {
    expect(shouldRenderWordmark('')).toBe(true);
  });
});

describe('aislamiento entre tenants', () => {
  it('la elección sale sólo del branding recibido', () => {
    // No hay tabla de assets por slug ni condicional por empresa: si el tenant
    // no manda una variante, no aparece una de otro.
    const otro: StorefrontLogos = {
      ...NONE,
      horizontal_on_dark: '/otro-tenant.png',
    };
    expect(pickLogo(NONE, '', 'header', 'dark')).toBe('');
    expect(pickLogo(otro, '', 'header', 'dark')).toBe('/otro-tenant.png');
  });

  it('ningún archivo del módulo invierte colores', () => {
    const fs = jest.requireActual('fs') as typeof import('fs');
    const path = jest.requireActual('path') as typeof import('path');
    const source = fs.readFileSync(
      path.join(process.cwd(), 'app/lib/brand-logo.ts'), 'utf8',
    );
    // Sin comentarios: el módulo EXPLICA por qué no invierte, y un escaneo de
    // texto casaría con su propia explicación.
    const code = source
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    expect(code).not.toContain('invert');
    expect(code).not.toContain('black-dog');
    expect(code).not.toContain('slug');
  });
});
