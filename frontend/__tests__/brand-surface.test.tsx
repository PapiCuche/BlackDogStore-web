import fs from 'fs';
import path from 'path';
import { render, screen } from '@testing-library/react';

import { BrandLogo } from '@/app/components/BrandLogo';
import { ThemeProvider, THEME_STORAGE_KEY } from '@/app/components/ThemeProvider';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';

/**
 * M12F.1 — el defecto que este fichero existe para cerrar.
 *
 * M12F hizo global el tema traduciendo `bg-[#080808]` a `bg-background`. Tres
 * bloques —el hero y dos de autenticación— pasaron así a SEGUIR AL TEMA, y su
 * `surface="dark"` se quedó donde estaba. Resultado: en tema claro pedían la
 * variante blanca del logotipo y la pintaban sobre fondo crema. Invisible.
 *
 * Es el mismo defecto que abrió M12E, reintroducido por el propio cambio que
 * hacía global el tema — y sobrevivió a una suite de 215 pruebas porque
 * ninguna miraba la relación entre la superficie declarada y la real.
 */

const CONFIG: StorefrontConfig = {
  ...NEUTRAL_CONFIG,
  company: { ...NEUTRAL_CONFIG.company, name: 'Taller Prueba' },
  branding: {
    ...NEUTRAL_CONFIG.branding,
    logos: {
      primary_on_light: '/v-negra.png',
      primary_on_dark: '/v-blanca.png',
      horizontal_on_light: '/h-negra.png',
      horizontal_on_dark: '/h-blanca.png',
      isotype_on_light: '/i-negra.png',
      isotype_on_dark: '/i-blanca.png',
    },
  },
};

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => ({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  });
});

function renderIn(theme: 'light' | 'dark', ui: React.ReactElement) {
  localStorage.setItem(THEME_STORAGE_KEY, theme);
  return render(
    <ThemeProvider>
      <StorefrontProvider config={CONFIG}>{ui}</StorefrontProvider>
    </ThemeProvider>,
  );
}

describe('una superficie que sigue al tema pide el logotipo del tema', () => {
  it('en claro pide la variante para fondo claro', () => {
    renderIn('light', <BrandLogo placement="hero" surface="theme" />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/v-negra.png');
  });

  it('en oscuro pide la variante para fondo oscuro', () => {
    renderIn('dark', <BrandLogo placement="hero" surface="theme" />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/v-blanca.png');
  });

  it('la cabecera prefiere el horizontal, y sigue al tema igual', () => {
    renderIn('light', <BrandLogo placement="header" surface="theme" />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/h-negra.png');
  });
});

describe('una superficie fija NO sigue al tema', () => {
  it('un bloque declarado oscuro pide la variante blanca aunque el tema sea claro', () => {
    // `surface` sigue siendo una afirmación sobre la SUPERFICIE. Un panel que
    // de verdad es negro en los dos temas tiene que poder decirlo.
    renderIn('light', <BrandLogo placement="hero" surface="dark" />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/v-blanca.png');
  });
});

describe('una banda invertida pide el contraste contrario', () => {
  it('en tema oscuro la banda es clara, así que el logotipo es el negro', () => {
    // La banda del pie se pinta con `bg-white`, que tras la traducción de
    // paleta ES el color del texto: su contraste es el contrario al de la
    // página.
    renderIn('dark', <BrandLogo placement="hero" surface="inverse" />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/v-negra.png');
  });

  it('en tema claro la banda es oscura, así que el logotipo es el blanco', () => {
    renderIn('light', <BrandLogo placement="hero" surface="inverse" />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/v-blanca.png');
  });
});

describe('el compacto prefiere el isotipo', () => {
  it('en móvil usa la pieza que el manual diseñó para ese tamaño', () => {
    renderIn('dark', <BrandLogo placement="compact" surface="theme" />);
    expect(screen.getByRole('img')).toHaveAttribute('src', '/i-blanca.png');
  });
});

// ---------------------------------------------------------------------------
// La defensa estructural
// ---------------------------------------------------------------------------

const APP = path.join(process.cwd(), 'app');

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(full));
    else if (/\.tsx$/.test(e.name)) out.push(full);
  }
  return out;
}

function code(file: string): string {
  return fs
    .readFileSync(file, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '');
}

describe('ningún bloque declara una superficie que no tiene', () => {
  it('un componente con superficies del tema no pide un contraste fijo', () => {
    /**
     * NO ES UN PARSER DE CSS, y no pretende serlo. La heurística es la que
     * habría bastado para encontrar el defecto real: si un fichero pinta con
     * `bg-background` o `bg-surface` —tokens que cambian con el tema— y además
     * declara `surface="dark"` o `surface="light"`, alguien está afirmando un
     * contraste que su propia superficie no garantiza.
     *
     * Un bloque legítimamente fijo existe: se pinta con un color que no depende
     * del tema, y entonces este test no lo ve porque no usa esos tokens.
     */
    const offenders: string[] = [];
    for (const file of walk(APP)) {
      const src = code(file);
      const fixed = src.match(/surface="(dark|light)"/g);
      if (!fixed) continue;
      const themed = /\bbg-(background|surface|surface-2)\b/.test(src);
      if (themed) {
        offenders.push(
          `${path.relative(process.cwd(), file)} → ${fixed.join(', ')} junto a superficies del tema`,
        );
      }
    }
    expect(offenders).toEqual([]);
  });

  it('nadie vuelve a leer `logo_url` a pelo para dibujar un logotipo', () => {
    // El campo legado sigue existiendo y `pickLogo` lo usa como último recurso,
    // con la regla de contraste puesta. Leerlo directamente se salta las seis
    // variantes y devuelve el defecto original por otra puerta.
    const offenders: string[] = [];
    for (const file of walk(APP)) {
      if (file.endsWith('BrandLogo.tsx')) continue;
      const src = code(file);
      if (/branding\.logo_url/.test(src) && /<img/.test(src)) {
        offenders.push(path.relative(process.cwd(), file));
      }
    }
    expect(offenders).toEqual([]);
  });
});
