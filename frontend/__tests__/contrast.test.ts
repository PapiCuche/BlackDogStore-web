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

    it.each(['--danger', '--warning', '--success', '--info'])('el estado %s se lee sobre el fondo y sobre su propia superficie', (token) => {
      // El aviso #a15c07 daba 4,15:1 sobre su superficie con el blanco cálido del piloto (revisión de P2-F1).
      const scope: Record<string, string> = {
        ...tokensIn(':root {'), ...brand, ...(theme === 'dark' ? tokensIn(':root[data-theme="dark"]') : {}),
      };
      const at = (t: string) => resolve(scope[t], scope);
      expect(contrast(at(token), p.background)).toBeGreaterThanOrEqual(AA_NORMAL);
      expect(contrast(at(token), at(`${token}-surface`))).toBeGreaterThanOrEqual(AA_NORMAL);
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
// M12F.2 — superficies que NO siguen al tema
// ---------------------------------------------------------------------------

/**
 * Aquí vivían 44 comprobaciones sobre una paleta secuestrada: M12F redefinió
 * `--color-white` y toda la escala `zinc` para que cambiaran de significado
 * según el tema. Medían bien lo que había, y lo que había estaba mal.
 *
 * El precio fue que `white` dejó de ser blanco, y con él se convirtió en crema
 * el hero —que era una losa negra deliberada— y la marca desapareció de su
 * propia portada. Los nombres de color estándar vuelven a significar su color;
 * lo que cambia con el tema usa tokens, y lo que no debe cambiar usa éstos.
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

describe('los nombres de color estándar significan su color', () => {
  it('la hoja no redefine la paleta de Tailwind', () => {
    // La regla que M12F.2 estableció: `white` es WHITE. Redefinirla convierte
    // cualquier `bg-white` del código en otra cosa según el tema, y eso
    // alcanza a 2.779 utilidades a la vez sin que nadie lo vea venir.
    const code = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
    for (const stolen of [
      '--color-white:', '--color-black:',
      '--color-zinc-', '--color-neutral-', '--color-slate-', '--color-stone-',
      '--color-gray-',
    ]) {
      expect(code).not.toContain(stolen);
    }
  });
});

describe.each(SCENARIOS)('la losa de marca — %s', (_name, brand) => {
  it.each(['light', 'dark'] as const)('es oscura también en tema %s', (theme) => {
    // EL PUNTO ENTERO DE LA FASE. Una página clara con un hero negro no es una
    // inconsistencia: es la decisión de marca. Si la losa siguiera al tema,
    // volveríamos al hero crema que borró la identidad.
    const scope = themeScope(brand, theme);
    const slab = resolve(scope['--slab'], scope);
    expect(luminance(slab)).toBeLessThan(0.2);
  });

  it.each(['light', 'dark'] as const)('su texto se lee encima en tema %s', (theme) => {
    const scope = themeScope(brand, theme);
    const slab = resolve(scope['--slab'], scope);
    const surface = resolve(scope['--slab-surface'], scope);
    for (const token of ['--slab-foreground', '--slab-muted']) {
      const colour = resolve(scope[token], scope);
      expect(contrast(colour, slab)).toBeGreaterThanOrEqual(AA_NORMAL);
      expect(contrast(colour, surface)).toBeGreaterThanOrEqual(AA_NORMAL);
    }
  });

  it('no depende del tema: los dos temas dan la misma losa', () => {
    // Si divergieran, «la losa» serían dos losas y el logotipo elegido para
    // una se pintaría sobre la otra.
    const light = themeScope(brand, 'light');
    const dark = themeScope(brand, 'dark');
    expect(resolve(light['--slab'], light)).toEqual(resolve(dark['--slab'], dark));
    expect(resolve(light['--slab-foreground'], light))
      .toEqual(resolve(dark['--slab-foreground'], dark));
  });
});

describe.each(SCENARIOS)('la superficie inversa — %s', (_name, brand) => {
  it.each(['light', 'dark'] as const)('invierte de verdad en tema %s', (theme) => {
    // Una banda pintada CON el color del texto. En oscuro sale clara; en claro,
    // oscura. Es lo contrario del tema, y por eso su texto NO puede usar
    // `muted`: la llamada del pie rendía 2.02:1 justamente por eso.
    const scope = themeScope(brand, theme);
    const page = resolve(scope['--background'], scope);
    const inverse = resolve(scope['--inverse'], scope);
    const flipped = luminance(page) < 0.5
      ? luminance(inverse) > 0.5
      : luminance(inverse) < 0.5;
    expect(flipped).toBe(true);
  });

  it.each(['light', 'dark'] as const)('su texto se lee encima en tema %s', (theme) => {
    const scope = themeScope(brand, theme);
    const inverse = resolve(scope['--inverse'], scope);
    for (const token of ['--inverse-foreground', '--inverse-muted']) {
      const colour = resolve(scope[token], scope);
      expect(contrast(colour, inverse)).toBeGreaterThanOrEqual(AA_NORMAL);
    }
  });
});

describe('la excepción de los rellenos saturados', () => {
  it('el texto sobre un badge de color es blanco en los dos temas', () => {
    for (const theme of ['light', 'dark'] as const) {
      const scope = themeScope(PILOT, theme);
      expect(resolve(scope['--color-on-status'], scope)).toEqual(parseHex('#ffffff'));
    }
  });
});

// ---------------------------------------------------------------------------
// P2 · F1 — los tokens de la vista previa V4 (`app/v4/v4.css`)
// ---------------------------------------------------------------------------

/**
 * La V4 es clara siempre (blanco cálido de la marca y texto negro), así que se
 * mide sobre la paleta clara de cada tenant: el piloto, que lleva la paleta del
 * Manual V4, y uno sin configurar, que sólo tiene los respaldos neutros.
 *
 * El anillo de foco necesita 3:1 (WCAG 1.4.11). El dorado del piloto sobre su
 * blanco cálido rinde 2,12:1, así que el foco NO puede ser el acento.
 */
const V4_CSS = fs.readFileSync(path.join(process.cwd(), 'app/v4/v4.css'), 'utf8');

function v4Tokens(): Record<string, string> {
  const start = V4_CSS.indexOf('[data-design="v4"] {');
  if (start < 0) throw new Error('no encuentro el bloque [data-design="v4"]');
  const open = V4_CSS.indexOf('{', start);
  const close = V4_CSS.indexOf('\n}', open);
  const body = V4_CSS.slice(open + 1, close).replace(/\/\*[\s\S]*?\*\//g, '');
  const out: Record<string, string> = {};
  for (const line of body.split(';')) {
    const m = line.match(/(--[\w-]+)\s*:\s*([\s\S]+)/);
    if (m) out[m[1]] = m[2].trim().replace(/\s+/g, ' ');
  }
  return out;
}

const UI_MIN = 3;

function v4Palette(brand: Record<string, string>, theme: 'light' | 'dark') {
  const scope: Record<string, string> = {
    ...tokensIn(':root {'),
    ...brand,
    ...(theme === 'dark' ? tokensIn(':root[data-theme="dark"]') : {}),
    ...v4Tokens(),
  };
  return (token: string) => resolve(scope[token], scope);
}

describe.each(SCENARIOS)('contraste V4 — %s', (_name, brand) => {
  // El tema del visitante no cambia la V4: se mide en los dos.
  describe.each(['light', 'dark'] as const)('tema %s', (theme) => {
    const at = v4Palette(brand, theme);
    const grounds = [['fondo', at('--v4-canvas')], ['superficie', at('--v4-surface')]] as const;

    it.each(grounds)('el texto principal y el secundario se leen sobre %s', (_l, bg) => {
      expect(contrast(at('--v4-ink'), bg as RGB)).toBeGreaterThanOrEqual(AA_NORMAL);
      expect(contrast(at('--v4-charcoal'), bg as RGB)).toBeGreaterThanOrEqual(AA_NORMAL);
      expect(contrast(at('--v4-muted'), bg as RGB)).toBeGreaterThanOrEqual(AA_NORMAL);
    });

    it.each(grounds)('el anillo de foco alcanza 3:1 sobre %s', (_l, bg) => {
      expect(contrast(at('--v4-focus'), bg as RGB)).toBeGreaterThanOrEqual(UI_MIN);
    });

    it.each(grounds)('el borde de un selector alcanza 3:1 sobre %s', (_l, bg) => {
      expect(contrast(at('--v4-line-strong'), bg as RGB)).toBeGreaterThanOrEqual(UI_MIN);
    });

    it('el texto del botón principal se lee sobre él', () => {
      expect(contrast(at('--v4-on-ink'), at('--v4-ink'))).toBeGreaterThanOrEqual(AA_NORMAL);
    });

    it('los estados (error, aviso, éxito) se leen sobre el fondo', () => {
      for (const token of ['--danger', '--warning', '--success']) {
        expect(contrast(at(token), at('--v4-canvas'))).toBeGreaterThanOrEqual(AA_NORMAL);
        expect(contrast(at(token), at(`${token}-surface`))).toBeGreaterThanOrEqual(AA_NORMAL);
      }
    });

    it('la V4 no sigue al tema oscuro: su fondo es el claro de la marca', () => {
      expect(luminance(at('--v4-canvas'))).toBeGreaterThan(0.5);
    });
  });
});

describe('V4: los colores salen de la marca', () => {
  it('el piloto se ve con su paleta (blanco cálido y dorado como acento)', () => {
    const at = v4Palette(PILOT, 'light');
    expect(at('--v4-canvas')).toEqual(parseHex('#F5F3EE'));
    expect(at('--v4-accent')).toEqual(parseHex('#C8A45D'));
  });

  it('un tenant sin configurar no recibe el dorado del piloto', () => {
    const at = v4Palette(UNCONFIGURED, 'light');
    expect(at('--v4-accent')).not.toEqual(parseHex('#C8A45D'));
    expect(at('--v4-canvas')).toEqual(parseHex('#ffffff'));
  });

  it('el dorado no está escrito en la hoja V4', () => {
    expect(V4_CSS.toLowerCase()).not.toContain('#c8a45d');
  });

  it('el foco no es el acento: el dorado sobre blanco cálido no llega a 3:1', () => {
    const at = v4Palette(PILOT, 'light');
    expect(contrast(parseHex('#C8A45D'), at('--v4-canvas'))).toBeLessThan(UI_MIN);
    expect(at('--v4-focus')).not.toEqual(at('--v4-accent'));
  });
});

// ---------------------------------------------------------------------------
// CATALOG-REFERENCE-01 · PR D — el bloque del configurador en la ficha pública
// ---------------------------------------------------------------------------

/**
 * En `/modelo/<slug>` el configurador lleva los tokens del Manual V4 sólo dentro de su bloque
 * (`[data-design="v4"][data-v4-scope="block"]`), y el bloque SIGUE el tema del visitante, porque la cabecera y el
 * pie de la V3 lo siguen: en oscuro, los neutros del manual (negro, carbón y el blanco cálido de la marca) salen de
 * los colores oscuros de cada empresa. Se mide igual que la V4 clara.
 */
const BLOCK_DARK = '[data-design="v4"][data-v4-scope="block"]:where([data-theme="dark"] *) {';

function v4BlockDarkTokens(): Record<string, string> {
  const start = V4_CSS.indexOf(BLOCK_DARK);
  if (start < 0) throw new Error('no encuentro el bloque oscuro del configurador');
  const open = V4_CSS.indexOf('{', start);
  const close = V4_CSS.indexOf('\n}', open);
  const out: Record<string, string> = {};
  for (const line of V4_CSS.slice(open + 1, close).replace(/\/\*[\s\S]*?\*\//g, '').split(';')) {
    const m = line.match(/(--[\w-]+)\s*:\s*([\s\S]+)/);
    if (m) out[m[1]] = m[2].trim().replace(/\s+/g, ' ');
  }
  return out;
}

describe.each(SCENARIOS)('contraste del configurador en la ficha pública, tema oscuro — %s', (_name, brand) => {
  const scope: Record<string, string> = {
    ...tokensIn(':root {'), ...brand, ...tokensIn(':root[data-theme="dark"]'), ...v4Tokens(), ...v4BlockDarkTokens(),
  };
  const at = (token: string) => resolve(scope[token], scope);
  const grounds = [['fondo', '--v4-canvas'], ['superficie', '--v4-surface']] as const;

  it('el fondo es oscuro: sigue al tema como la cabecera y el pie', () => {
    expect(luminance(at('--v4-canvas'))).toBeLessThan(0.2);
  });

  it.each(grounds)('el texto principal y el secundario se leen sobre %s', (_l, bg) => {
    expect(contrast(at('--v4-ink'), at(bg))).toBeGreaterThanOrEqual(AA_NORMAL);
    expect(contrast(at('--v4-muted'), at(bg))).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it.each(grounds)('el foco y el borde de un selector alcanzan 3:1 sobre %s', (_l, bg) => {
    expect(contrast(at('--v4-focus'), at(bg))).toBeGreaterThanOrEqual(UI_MIN);
    expect(contrast(at('--v4-line-strong'), at(bg))).toBeGreaterThanOrEqual(UI_MIN);
  });

  it('el botón principal y su estado al pasar se leen', () => {
    expect(contrast(at('--v4-on-ink'), at('--v4-ink'))).toBeGreaterThanOrEqual(AA_NORMAL);
    expect(contrast(at('--v4-on-ink'), at('--v4-charcoal'))).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('los estados (error, aviso, éxito) se leen sobre el fondo y su superficie', () => {
    for (const token of ['--danger', '--warning', '--success']) {
      expect(contrast(at(token), at('--v4-canvas'))).toBeGreaterThanOrEqual(AA_NORMAL);
      expect(contrast(at(token), at(`${token}-surface`))).toBeGreaterThanOrEqual(AA_NORMAL);
    }
  });

  it('el dorado sigue siendo sólo acento: ni texto ni foco', () => {
    expect(at('--v4-focus')).not.toEqual(at('--v4-accent'));
    expect(at('--v4-ink')).not.toEqual(at('--v4-accent'));
  });
});
