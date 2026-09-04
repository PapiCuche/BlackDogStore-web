import fs from 'fs';
import path from 'path';

/**
 * Auditoría de frontend — las tres reglas de superficie que se rompieron.
 *
 * Ninguna de éstas la habría encontrado un test de componente: son propiedades
 * de cómo encaja la aplicación entera, y se descubrieron abriéndola.
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

/** Sin comentarios: un fichero que EXPLICA una regla casaría consigo mismo. */
function code(file: string): string {
  return fs
    .readFileSync(file, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '');
}

const CSS = fs.readFileSync(path.join(APP, 'globals.css'), 'utf8');
const FILES = walk(APP);
const rel = (f: string) => path.relative(process.cwd(), f);

describe('el panel no lleva puesta la ropa de la tienda', () => {
  it('la cabecera y el pie del escaparate se deciden por ruta', () => {
    // El layout raíz los pintaba en TODAS las rutas: el control interno salía
    // con «Catálogo · Servicios · Carrito» encima y el pie de marketing
    // completo debajo. Quien gestiona inventario no necesita un carrito.
    const layout = code(path.join(APP, 'layout.tsx'));
    expect(layout).toContain('StorefrontHeader');
    expect(layout).toContain('StorefrontFooter');
    // Ya no se montan directamente, que es lo que hacía imposible excluirlos.
    expect(layout).not.toMatch(/<Header\s*\/>/);
    expect(layout).not.toMatch(/<Footer\s*\/>/);
  });

  it('el armazón sabe qué rutas NO son escaparate', () => {
    const chrome = code(path.join(APP, 'components/StorefrontChrome.tsx'));
    expect(chrome).toContain('/admin');
    // Por RUTA, no por sesión: un administrador que visita la tienda ve la
    // tienda, y un anónimo que llega al panel ve la puerta del panel.
    expect(chrome).toContain('usePathname');
    expect(chrome).not.toContain('is_superuser');
  });

  it('el botón de WhatsApp no aparece en el panel', () => {
    const chrome = code(path.join(APP, 'components/StorefrontChrome.tsx'));
    const fn = chrome.slice(chrome.indexOf('export function WhatsAppButton'));
    expect(fn).toContain('isInternalSurface');
  });
});

describe('los colores de estado distinguen texto de relleno', () => {
  it('existen las dos familias', () => {
    // Confundirlas costó dos defectos seguidos: un relleno sólido convertido
    // en tinte de superficie (1.24:1) y luego el mismo relleno girando con el
    // tema mientras su texto no (1.77:1).
    for (const role of ['danger', 'warning', 'success', 'info']) {
      expect(CSS).toContain(`--${role}:`);
      expect(CSS).toContain(`--${role}-solid:`);
      expect(CSS).toContain(`--${role}-surface:`);
    }
  });

  it('los rellenos sólidos NO cambian con el tema', () => {
    // Un badge rojo es rojo en los dos temas: su fondo no lo pinta el tema, así
    // que su texto tampoco puede cambiar. Si el sólido girara, el texto blanco
    // encima dejaría de contrastar en uno de los dos.
    const darkBlock = CSS.slice(CSS.indexOf(':root[data-theme="dark"]'));
    const end = darkBlock.indexOf('\n}');
    const dark = darkBlock.slice(0, end);
    for (const role of ['danger', 'warning', 'success', 'info']) {
      expect(dark).not.toContain(`--${role}-solid:`);
    }
  });

  it('nadie escribe un estado con el extremo claro de una escala', () => {
    // `text-amber-300` y compañía venían de cuando sólo había tema oscuro. En
    // claro rendían 1.02:1 — el aviso de configuración incompleta del panel era
    // literalmente invisible.
    const offenders: string[] = [];
    for (const file of FILES) {
      const found = code(file).match(
        /\btext-(?:red|rose|amber|yellow|orange|emerald|green|sky|blue)-(?:50|100|200|300|400)\b/g,
      );
      if (found) offenders.push(`${rel(file)} → ${[...new Set(found)].join(', ')}`);
    }
    expect(offenders).toEqual([]);
  });

  it('un relleno con texto fijo usa el token que no gira', () => {
    // `text-on-status` es blanco en los dos temas. Sólo puede ir sobre un fondo
    // que tampoco gire.
    const offenders: string[] = [];
    for (const file of FILES) {
      for (const attr of code(file).match(/class(?:Name)?="[^"]*"/g) ?? []) {
        if (!attr.includes('text-on-status')) continue;
        if (/\bbg-(danger|warning|success|info)\b(?!-solid)/.test(attr)) {
          offenders.push(`${rel(file)} → ${attr.slice(0, 70)}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('el texto de un relleno invertido no usa el token de la página', () => {
  it('ningún `bg-foreground` lleva `text-muted`', () => {
    // Sobre un relleno pintado con el color del texto, `muted` —calculado para
    // la superficie normal— rinde 2:1. Uno de los siete casos era «Continuar al
    // pago», el CTA más importante del sitio.
    const offenders: string[] = [];
    for (const file of FILES) {
      for (const attr of code(file).match(/class(?:Name)?="[^"]*"/g) ?? []) {
        if (/bg-foreground/.test(attr) && /\btext-muted\b/.test(attr)) {
          offenders.push(`${rel(file)} → ${attr.slice(0, 70)}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('cada etiqueta nombra a su campo', () => {
  it('no quedan etiquetas huérfanas', () => {
    // 28 etiquetas en 11 ficheros no tenían `htmlFor` y sus controles no tenían
    // `id`. El lector de pantalla anunciaba el campo sin nombre y pulsar la
    // etiqueta no lo enfocaba — en móvil, media área táctil perdida.
    //
    // Una etiqueta que ENVUELVE su control ya está asociada y no necesita nada.
    const offenders: string[] = [];
    for (const file of FILES) {
      const src = code(file);
      for (const m of src.matchAll(/<label(?<attrs>[^>]*)>(?<body>[\s\S]*?)<\/label>/g)) {
        const { attrs = '', body = '' } = m.groups ?? {};
        if (attrs.includes('htmlFor')) continue;
        if (/<(input|select|textarea)(?![\w-])/.test(body)) continue;
        const text = body.replace(/\{[^}]*\}/g, '').replace(/<[^>]+>/g, '').trim();
        if (!text) continue;
        offenders.push(`${rel(file)} → «${text.slice(0, 34)}»`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('ningún `htmlFor` apunta a un id que no existe', () => {
    // Una asociación equivocada es peor que ninguna: nombraría el campo con la
    // etiqueta de otro. El codemod que las generó cometió exactamente ese error
    // una vez, y así se encontró.
    const offenders: string[] = [];
    for (const file of FILES) {
      const src = code(file);
      const fors = [...src.matchAll(/<label[^>]*htmlFor="([^"]+)"/g)].map((m) => m[1]);
      const ids = new Set([...src.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
      for (const f of fors) if (!ids.has(f)) offenders.push(`${rel(file)} → ${f}`);
      const dup = fors.filter((f, i) => fors.indexOf(f) !== i);
      for (const d of dup) offenders.push(`${rel(file)} → duplicado ${d}`);
    }
    expect(offenders).toEqual([]);
  });
});
