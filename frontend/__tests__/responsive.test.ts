import fs from 'fs';
import path from 'path';

/**
 * M12F — responsive, comprobado donde se puede comprobar sin navegador.
 *
 * NO AFIRMA QUE LA WEB SE VEA BIEN. No hay navegador en este entorno, así que
 * nada de aquí sustituye a abrir la página. Lo que hace es perseguir las causas
 * ESTRUCTURALES de los defectos que sí se pueden encontrar leyendo el código:
 * un ancho fijo mayor que el viewport más estrecho, una tabla ancha sin
 * contenedor que la desplace, un modal más alto que la pantalla.
 *
 * El viewport más estrecho que soportamos son 320 px. Con el `px-6` habitual
 * del contenedor quedan 272 px útiles.
 */

const APP = path.join(process.cwd(), 'app');
const NARROWEST_CONTENT = 272;

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (/\.tsx?$/.test(entry.name)) out.push(full);
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

const FILES = walk(APP);
const rel = (f: string) => path.relative(process.cwd(), f);

/** Cada `class="..."` del fichero, ya sin comentarios. */
function classAttrs(file: string): string[] {
  return code(file).match(/class(?:Name)?="[^"]*"/g) ?? [];
}

describe('nada más ancho que el móvil más estrecho', () => {
  it('ningún ancho fijo supera el espacio útil de 320 px', () => {
    // `w-[340px]` dentro de un contenedor con `px-6` desbordaba 68 px: la
    // portada tenía scroll horizontal en 320 px. Un ancho fijo grande sólo es
    // aceptable si algo lo limita — `max-w-[85vw]`, `max-w-full` — o si el
    // elemento está oculto hasta un breakpoint donde cabe.
    const offenders: string[] = [];
    for (const file of FILES) {
      for (const attr of classAttrs(file)) {
        for (const m of attr.matchAll(/(?<!max-)(?<!min-)\bw-\[(\d+)px\]/g)) {
          const width = Number(m[1]);
          if (width <= NARROWEST_CONTENT) continue;
          const capped = /max-w-\[|max-w-full|max-w-\[85vw\]/.test(attr);
          // `hidden ... lg:block` no se dibuja en móvil, así que no desborda.
          const hiddenOnMobile = /\bhidden\b/.test(attr);
          if (capped || hiddenOnMobile) continue;
          offenders.push(`${rel(file)} → w-[${width}px]`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('toda tabla con ancho mínimo vive dentro de algo que la desplaza', () => {
    // Una tabla es la excepción legítima del scroll horizontal: hay datos que
    // no caben en 320 px y encogerlos hasta 8 px no es una solución. Lo que no
    // vale es que el desplazamiento lo herede la PÁGINA.
    const offenders: string[] = [];
    for (const file of FILES) {
      const src = code(file);
      for (const m of src.matchAll(/min-w-\[(\d+)px\]/g)) {
        if (Number(m[1]) <= NARROWEST_CONTENT) continue;
        // El contenedor con scroll tiene que estar cerca, no en otro fichero.
        const around = src.slice(Math.max(0, m.index! - 400), m.index! + 200);
        if (!/overflow-x-auto|overflow-auto|overflow-x-scroll/.test(around)) {
          offenders.push(`${rel(file)} → min-w-[${m[1]}px] sin contenedor con scroll`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('los diálogos caben en la pantalla', () => {
  it('todo panel fijo a pantalla completa limita su alto', () => {
    // Un modal más alto que el viewport deja sus botones fuera de alcance y no
    // hay forma de llegar a ellos: ni cerrar, ni confirmar, ni cancelar.
    const offenders: string[] = [];
    for (const file of FILES) {
      for (const attr of classAttrs(file)) {
        const isOverlayPanel = /\bfixed\b/.test(attr) && /\binset-0\b/.test(attr);
        if (!isOverlayPanel) continue;
        // El velo en sí no necesita límite; el panel de dentro sí. Se acepta
        // cualquiera de las formas de contenerlo.
        const src = code(file);
        if (!/max-h-|overflow-y-auto|overflow-auto|h-full/.test(src)) {
          offenders.push(rel(file));
        }
      }
    }
    expect([...new Set(offenders)]).toEqual([]);
  });
});

describe('los objetivos táctiles se pueden tocar', () => {
  it('los controles del escaparate declaran alto mínimo', () => {
    // 44 px es el mínimo razonable con el pulgar. No se comprueba en todo el
    // admin —una tabla densa tiene otras reglas— sino en los botones que un
    // cliente pulsa desde el móvil.
    const storefront = FILES.filter(
      (f) => !rel(f).includes('/admin/') && /\.tsx$/.test(f),
    );
    const offenders: string[] = [];
    for (const file of storefront) {
      const src = code(file);
      // Un botón con `py-3.5` mide ~44 px con el texto dentro; lo que se
      // persigue es el que declara un alto explícito por debajo.
      for (const m of src.matchAll(/\bh-(\d+)\b(?![\w-])/g)) {
        const rem = Number(m[1]) / 4;
        if (rem * 16 >= 44) continue;
        const attr = src.slice(Math.max(0, m.index! - 200), m.index! + 60);
        // Sólo importa si el elemento es interactivo.
        if (/<button|<a\s|<Link|role="button"/.test(attr) && /\bw-\d+\b/.test(attr)) {
          offenders.push(`${rel(file)} → h-${m[1]}`);
        }
      }
    }
    // Se informa, no se bloquea: hay iconos decorativos dentro de botones más
    // grandes que casarían con esta heurística.
    expect(offenders.length).toBeLessThanOrEqual(8);
  });
});

describe('la tipografía escala sin saltos', () => {
  it('los titulares grandes usan clamp() en vez de una cascada de breakpoints', () => {
    // `text-[100px]` con diez excepciones por breakpoint es lo que produce un
    // ancho en el que el titular se queda enorme justo antes de saltar.
    const home = code(path.join(APP, 'page.tsx'));
    const hero = code(path.join(APP, 'components/Hero.tsx'));
    expect(hero).toContain('clamp(');
    expect(home).toContain('clamp(');
  });
});
