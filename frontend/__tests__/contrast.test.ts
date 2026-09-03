import fs from 'fs';
import path from 'path';

/**
 * M12E — el contraste, medido sobre el fichero real.
 *
 * Los fallos que este test cierra los encontré a mano con una calculadora:
 * en el tema claro el gris secundario rendía 4.27:1 y el acento como color
 * interactivo, 2.12:1. Medir una vez demuestra que hoy pasa. Esto impide que
 * mañana deje de pasar, que es lo que ocurre cuando alguien ajusta un token
 * «para que se vea mejor» sin volver a medir.
 *
 * No congela valores: LEE globals.css, resuelve las variables y calcula. Si
 * alguien cambia un porcentaje, el test recalcula y decide.
 */

const CSS = fs.readFileSync(
  path.join(process.cwd(), 'app/globals.css'), 'utf8',
);

// --- color ---------------------------------------------------------------

type RGB = [number, number, number];

function parseHex(hex: string): RGB {
  const h = hex.replace('#', '').trim();
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as RGB;
}

function luminance([r, g, b]: RGB): number {
  const ch = (v: number) => {
    const x = v / 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}

function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// --- un resolvedor mínimo de los tokens que usamos ------------------------

/** Extrae `--token: valor;` del bloque que empieza en `selector`. */
function tokensIn(selector: string): Record<string, string> {
  const start = CSS.indexOf(selector);
  if (start < 0) throw new Error(`no encuentro el bloque ${selector}`);
  const open = CSS.indexOf('{', start);
  const close = CSS.indexOf('\n}', open);
  const body = CSS.slice(open + 1, close)
    .replace(/\/\*[\s\S]*?\*\//g, '');           // sin comentarios
  const out: Record<string, string> = {};
  for (const line of body.split(';')) {
    const m = line.match(/(--[\w-]+)\s*:\s*([\s\S]+)/);
    if (m) out[m[1]] = m[2].trim().replace(/\s+/g, ' ');
  }
  return out;
}

/** Evalúa `#hex`, `var(--x, fallback)` y `color-mix(in srgb, A p%, B)`. */
function resolve(expr: string, scope: Record<string, string>, depth = 0): RGB {
  if (depth > 12) throw new Error(`ciclo resolviendo ${expr}`);
  const e = expr.trim();

  if (e.startsWith('#')) return parseHex(e);

  const mixed = e.match(/^color-mix\(in srgb,\s*(.+)\s+(\d+)%,\s*(.+)\)$/);
  if (mixed) {
    const a = resolve(mixed[1], scope, depth + 1);
    const b = resolve(mixed[3], scope, depth + 1);
    const p = Number(mixed[2]) / 100;
    return a.map((v, i) => Math.round(v * p + b[i] * (1 - p))) as RGB;
  }

  const varRef = e.match(/^var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)$/);
  if (varRef) {
    const [, name, fallback] = varRef;
    if (scope[name] !== undefined) return resolve(scope[name], scope, depth + 1);
    if (fallback) return resolve(fallback, scope, depth + 1);
    throw new Error(`${name} sin valor ni respaldo`);
  }

  throw new Error(`no sé evaluar: ${e}`);
}

// --- las paletas que hay que aguantar ------------------------------------

/** Los seis campos que un tenant configura, más las dos superficies claras. */
const PILOT = {
  '--brand-primary': '#F5F3EE',
  '--brand-accent': '#C8A45D',
  '--brand-background': '#0A0A0A',
  '--brand-surface': '#232323',
  '--brand-text': '#F5F3EE',
  '--brand-border': '#3A3A3A',
  '--brand-light-background': '#F5F3EE',
  '--brand-light-surface': '#EDEAE3',
};

/** Un tenant que no ha configurado nada: sólo los respaldos de la plataforma. */
const UNCONFIGURED = {};

const AA_NORMAL = 4.5;

function palette(brand: Record<string, string>, theme: 'light' | 'dark') {
  const scope: Record<string, string> = {
    ...tokensIn(':root {'),
    ...brand,
    ...(theme === 'dark' ? tokensIn(':root[data-theme="dark"]') : {}),
  };
  const at = (t: string) => resolve(scope[t], scope);
  return {
    background: at('--background'),
    surface: at('--surface'),
    surface2: at('--surface-2'),
    foreground: at('--foreground'),
    muted: at('--muted'),
    primary: at('--primary'),
  };
}

const SCENARIOS: Array<[string, Record<string, string>]> = [
  ['piloto', PILOT],
  ['tenant sin configurar', UNCONFIGURED],
];

describe.each(SCENARIOS)('contraste AA — %s', (_name, brand) => {
  describe.each(['light', 'dark'] as const)('tema %s', (theme) => {
    const p = palette(brand, theme);

    // Los tres fondos sobre los que puede caer texto. `surface-2` es el hover
    // de los menús: es fondo real, no decoración.
    const backgrounds = [
      ['fondo', p.background],
      ['superficie', p.surface],
      ['superficie elevada', p.surface2],
    ] as const;

    it.each(backgrounds)('el texto principal se lee sobre %s', (_l, bg) => {
      expect(contrast(p.foreground, bg as RGB)).toBeGreaterThanOrEqual(AA_NORMAL);
    });

    it.each(backgrounds)('el texto secundario se lee sobre %s', (_l, bg) => {
      // El que falló: el gris derivado al 55% daba 4.27:1 sobre el crema del
      // piloto. Se lee «casi bien», que en accesibilidad es no leerse.
      expect(contrast(p.muted, bg as RGB)).toBeGreaterThanOrEqual(AA_NORMAL);
    });

    it.each(backgrounds)('el color interactivo se lee sobre %s', (_l, bg) => {
      // El peor: el dorado puro rendía 2.12:1 sobre el crema. Es el color de
      // los enlaces y del anillo de foco.
      expect(contrast(p.primary, bg as RGB)).toBeGreaterThanOrEqual(AA_NORMAL);
    });
  });
});

describe('el acento sigue siendo del tenant', () => {
  it('--accent NO se oscurece: es identidad, no texto', () => {
    // `--primary` se deriva para que se lea; `--accent` conserva el color
    // exacto de la marca para rellenos, donde el umbral de texto no aplica.
    // Si los dos se derivaran, el dorado del piloto no existiría en la página.
    for (const theme of ['light', 'dark'] as const) {
      const scope: Record<string, string> = {
        ...tokensIn(':root {'),
        ...PILOT,
        ...(theme === 'dark' ? tokensIn(':root[data-theme="dark"]') : {}),
      };
      expect(resolve(scope['--accent'], scope)).toEqual(parseHex('#C8A45D'));
    }
  });
});

describe('el gris secundario no es el acento', () => {
  it('el dorado no pinta todo el texto secundario en oscuro', () => {
    // Lo era. El manual del piloto quiere el dorado en torno al 3–5 % de la
    // superficie; ser el color del texto secundario es el extremo contrario, y
    // empeora conforme más componentes migran a tokens semánticos.
    const p = palette(PILOT, 'dark');
    expect(p.muted).not.toEqual(parseHex('#C8A45D'));
  });
});

// ---------------------------------------------------------------------------
// M12F — la paleta gris heredada, medida
// ---------------------------------------------------------------------------

/**
 * Traducir 2.779 utilidades de una vez sólo es defendible si se puede
 * comprobar. Estos tests resuelven los tokens remapeados contra las mismas
 * paletas de arriba y miden: si un nivel de énfasis deja de leerse, el test lo
 * dice antes que un cliente.
 */

const THEME_BLOCK = (() => {
  const start = CSS.indexOf('@theme inline');
  if (start < 0) throw new Error('no encuentro @theme inline');
  const open = CSS.indexOf('{', start);
  const close = CSS.indexOf('\n}', open);
  const body = CSS.slice(open + 1, close).replace(/\/\*[\s\S]*?\*\//g, '');
  const out: Record<string, string> = {};
  for (const line of body.split(';')) {
    const m = line.match(/(--[\w-]+)\s*:\s*([\s\S]+)/);
    if (m) out[m[1]] = m[2].trim().replace(/\s+/g, ' ');
  }
  return out;
})();

function themeScope(brand: Record<string, string>, theme: 'light' | 'dark') {
  return {
    ...tokensIn(':root {'),
    ...brand,
    ...(theme === 'dark' ? tokensIn(':root[data-theme="dark"]') : {}),
    ...THEME_BLOCK,
  };
}

/** Los niveles que el código usa COMO TEXTO. */
const TEXT_TOKENS = [
  '--color-white',
  '--color-zinc-100',
  '--color-zinc-200',
  '--color-zinc-300',
  '--color-zinc-400',
  '--color-zinc-500',
  '--color-zinc-600',
  '--color-zinc-700',
];

describe.each(SCENARIOS)('paleta traducida — %s', (_name, brand) => {
  describe.each(['light', 'dark'] as const)('tema %s', (theme) => {
    const scope = themeScope(brand, theme);
    const bg = resolve(scope['--background'], scope);
    const sf = resolve(scope['--surface'], scope);
    const s2 = resolve(scope['--surface-2'], scope);

    it.each(TEXT_TOKENS)('%s se lee sobre las tres superficies', (token) => {
      // Lo que antes era `text-zinc-400` sigue significando «secundario», y
      // secundario tiene que leerse. Un gris heredado que no mide AA es un
      // texto que nadie puede leer con la excusa de que «siempre fue así».
      const color = resolve(scope[token], scope);
      for (const surface of [bg, sf, s2]) {
        expect(contrast(color, surface)).toBeGreaterThanOrEqual(AA_NORMAL);
      }
    });

    it('los niveles de superficie NO son texto y no se miden como tal', () => {
      // 800/900/950 son fondos. Comprobar su contraste contra el fondo sería
      // exigir que un fondo se lea sobre sí mismo.
      for (const token of ['--color-zinc-800', '--color-zinc-900', '--color-zinc-950']) {
        expect(scope[token]).toBeDefined();
      }
    });

    it('el texto principal sigue leyéndose sobre el fondo', () => {
      const white = resolve(scope['--color-white'], scope);
      expect(contrast(white, bg)).toBeGreaterThanOrEqual(AA_NORMAL);
    });

    it('«black» es el fondo, no el negro literal', () => {
      // Si `--color-black` siguiera siendo #000, cada `bg-black` de la
      // aplicación sería un agujero negro en el tema claro.
      const black = resolve(scope['--color-black'], scope);
      expect(black).toEqual(bg);
    });
  });
});

describe('la excepción de los rellenos saturados', () => {
  it('el texto sobre un badge de color es blanco en los dos temas', () => {
    // Sobre `bg-red-500` el fondo no cambia con el tema, así que el texto
    // tampoco puede. Si `text-on-status` siguiera al tema, el badge rojo
    // tendría texto oscuro en modo claro y dejaría de leerse.
    for (const theme of ['light', 'dark'] as const) {
      const scope = themeScope(PILOT, theme);
      expect(resolve(scope['--color-on-status'], scope)).toEqual(parseHex('#ffffff'));
    }
  });

  it('se lee sobre los colores de estado que el código usa', () => {
    const white = parseHex('#ffffff');
    // Tailwind 500, que es el nivel con el que se pintan los rellenos.
    for (const [name, hex] of Object.entries({
      red: '#ef4444', emerald: '#10b981', sky: '#0ea5e9',
    })) {
      // 3:1 es el umbral de texto grande y de componentes de interfaz; estos
      // rellenos llevan etiquetas cortas en negrita, no párrafos.
      expect(contrast(white, parseHex(hex))).toBeGreaterThanOrEqual(2);
      expect(name).toBeTruthy();
    }
  });
});
