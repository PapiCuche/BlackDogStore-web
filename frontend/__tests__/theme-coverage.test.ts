import fs from 'fs';
import path from 'path';

/**
 * M12F — que el tema llegue a TODA la web, y que se note cuando deja de llegar.
 *
 * NO ES UNA REGLA INGENUA. Prohibir cualquier hex sería prohibir el verde de
 * WhatsApp, el rojo de un error y las rutas de los assets — y una regla que
 * grita por cosas correctas se desactiva en una semana.
 *
 * Lo que persigue es UNA cosa: un color de superficie o de texto escrito como
 * literal, que por definición no puede cambiar con el tema. Todo lo demás lleva
 * su justificación abajo.
 */

const APP = path.join(process.cwd(), 'app');

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (/\.tsx?$/.test(entry.name)) out.push(full);
  }
  return out;
}

/** Sin comentarios: un fichero que EXPLICA por qué no usa algo casaría consigo mismo. */
function code(file: string): string {
  return fs
    .readFileSync(file, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '');
}

const FILES = walk(APP);
const rel = (f: string) => path.relative(process.cwd(), f);

/**
 * IDENTIDAD DE UN TERCERO — clase B.
 *
 * `#25D366` es el verde oficial de WhatsApp. No es una decisión de tema
 * nuestra: es la marca de otra empresa, y el botón se reconoce por ese color.
 * Convertirlo en `brand-accent` sería adoptar una marca ajena como propia.
 */
const THIRD_PARTY_COLOURS = ['#25D366', '#25d366'];

describe('ningún color de superficie queda fuera del tema', () => {
  it('no hay literales hex en utilidades de color', () => {
    // El defecto: `bg-[#080808]` es negro en el tema claro también. Eran 71 en
    // 20 ficheros, escritos con cuatro grafías del mismo negro porque no había
    // token que lo nombrara.
    const offenders: string[] = [];
    for (const file of FILES) {
      const matches = code(file).match(
        /\b(?:bg|text|border|ring|from|to|via|divide|placeholder|outline|shadow)-\[#[0-9a-fA-F]{3,8}\]/g,
      );
      for (const m of matches ?? []) {
        if (THIRD_PARTY_COLOURS.some((c) => m.toLowerCase().includes(c.toLowerCase()))) {
          continue;
        }
        offenders.push(`${rel(file)} → ${m}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('el verde de WhatsApp sigue ahí, y sigue siendo suyo', () => {
    // La contraparte del test anterior: si la allowlist se usara para colar
    // otros literales, este test no lo vería. Lo que comprueba es que la
    // excepción existe por un motivo concreto y sigue siendo ESE.
    const withGreen = FILES.filter((f) =>
      THIRD_PARTY_COLOURS.some((c) => code(f).includes(c)),
    );
    expect(withGreen.length).toBeGreaterThan(0);
    for (const file of withGreen) {
      // Acompaña siempre al botón de WhatsApp. Si un día apareciera en un
      // fichero que no tiene nada que ver, la excepción se estaría usando para
      // colar un literal cualquiera.
      expect(code(file).toLowerCase()).toMatch(/whatsapp|wa\.me/);
    }
  });
});

describe('el tema se aplica en el propio documento', () => {
  const globals = fs.readFileSync(path.join(APP, 'globals.css'), 'utf8');

  it('el fondo del body sale de un token, no de un literal', () => {
    // Sin esto la página toma el fondo del navegador y hereda el tema del
    // host: un artefacto claro dentro de un contenedor oscuro, o al revés.
    expect(globals).toMatch(/body\s*\{[^}]*background:\s*var\(--background\)/);
  });

  it('los dos temas definen las mismas variables', () => {
    // Una variable definida sólo en oscuro es un color que en claro cae al
    // valor de otra cosa — o a nada.
    const block = (sel: string) => {
      const i = globals.indexOf(sel);
      const open = globals.indexOf('{', i);
      const close = globals.indexOf('\n}', open);
      return new Set(
        (globals.slice(open, close).match(/--[\w-]+(?=\s*:)/g) ?? []).filter(
          (v) => !v.startsWith('--brand-'),
        ),
      );
    };
    const light = block(':root {');
    const dark = block(':root[data-theme="dark"]');
    // El oscuro redefine un subconjunto: las que NO redefine las hereda de
    // `:root`. Lo que no puede haber es una variable que sólo exista en oscuro.
    for (const name of dark) {
      expect(light.has(name)).toBe(true);
    }
  });
});

describe('el movimiento se puede parar', () => {
  it('hay una regla de movimiento reducido', () => {
    const globals = fs.readFileSync(path.join(APP, 'globals.css'), 'utf8');
    expect(globals).toContain('prefers-reduced-motion');
  });

  it('ninguna animación en bucle queda sin cubrir', () => {
    // Una animación `infinite` que no se detenga bajo movimiento reducido es
    // un mareo que el usuario no puede apagar.
    const globals = fs.readFileSync(path.join(APP, 'globals.css'), 'utf8');
    const reduced = globals.slice(globals.indexOf('prefers-reduced-motion'));
    expect(reduced).toMatch(/animation(-duration)?:\s*(none|0)/);
  });
});

describe('el contenido del tenant se pinta como texto', () => {
  it('sólo el script anti-parpadeo usa dangerouslySetInnerHTML', () => {
    // El contenido de las campañas lo escribe personal del tenant desde un
    // panel, no el equipo que revisa este código. Pintarlo como HTML sería
    // aceptar `<script>` de quien tenga acceso al admin.
    //
    // La única excepción es el script que resuelve el tema antes del primer
    // paint: tiene que ser inline para que corra antes de pintar, y su
    // contenido es una CONSTANTE del código — ni llega por la red ni lo
    // escribe una persona. Se comprueba abajo que sigue siendo eso.
    const offenders = FILES.filter((f) => code(f).includes('dangerouslySetInnerHTML'));
    expect(offenders.map(rel)).toEqual(['app/layout.tsx']);
  });

  it('ese script no interpola nada que venga de fuera', () => {
    // La excepción vale mientras el contenido sea la constante. En el momento
    // en que alguien meta ahí una plantilla con un valor del servidor, deja de
    // ser una excepción y pasa a ser una inyección.
    const layout = code(path.join(APP, 'layout.tsx'));
    const uses = layout.match(/dangerouslySetInnerHTML=\{\{[^}]*\}\}/g) ?? [];
    expect(uses).toHaveLength(1);
    expect(uses[0]).toContain('THEME_INIT_SCRIPT');
    // Sin plantillas ni concatenación: sólo el identificador.
    expect(uses[0]).not.toContain('`');
    expect(uses[0]).not.toContain('+');
  });

  it('no hay eval ni new Function', () => {
    const offenders = FILES.filter((f) => /\beval\(|new Function\(/.test(code(f)));
    expect(offenders.map(rel)).toEqual([]);
  });
});
