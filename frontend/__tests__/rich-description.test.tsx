import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';

import { DescriptionEditor } from '@/app/admin/components/DescriptionEditor';
import { RichText } from '@/app/components/RichText';
import { DESCRIPTION_MAX_LENGTH, markdownToPlainText, parseMarkdown, safeHref } from '@/app/lib/markdown-lite';
import { htmlToMarkdown, markdownForPaste } from '@/app/lib/paste-to-markdown';

/**
 * The fastest of a few runs, in ms. A wall-clock budget measured ONCE also measures whatever else the machine is doing:
 * under the full suite (one Jest worker per core) a 20–60 ms parse was seen at 214–572 ms — a GC pause or a descheduled
 * worker, not the parser. Noise only ever ADDS time, so the minimum is the honest estimate; a parser that is really
 * slow (quadratic on these inputs means seconds) is slow on every run and still fails.
 */
function fastestOf(work: () => void, runs = 5): number {
  let best = Infinity;
  for (let i = 0; i < runs; i++) {
    const started = performance.now();
    work();
    best = Math.min(best, performance.now() - started);
  }
  return best;
}

/**
 * CATALOG-RICH-DESC-01 — descripciones de productos y modelos con formato.
 *
 * Se guardan como Markdown (el mismo campo de texto) y la tienda las pinta como elementos de React, nunca como
 * HTML. Aquí se prueba el subconjunto de Markdown, lo que no debe convertirse en formato, la seguridad (HTML y
 * enlaces maliciosos), el pegado desde ChatGPT y el editor con su barra, vista previa y deshacer.
 */

const SAMPLE = `**Apple iPhone 13 Pro Max**

El iPhone 13 Pro Max combina potencia, elegancia y fotografía profesional.

## Características principales

- Pantalla Super Retina XDR OLED.
- Tecnología ProMotion de 120 Hz.
- Procesador A15 Bionic.
- Triple cámara de 12 MP.
- Face ID.
- MagSafe.

## Capacidades

1. 128 GB
2. 256 GB
3. 512 GB
4. 1 TB`;

const draw = (source: string, props: Partial<Parameters<typeof RichText>[0]> = {}) =>
  render(<RichText source={source} testId="rt" {...props} />).getByTestId('rt');

describe('Markdown de una descripción', () => {
  it('pinta la descripción de ejemplo: negrita, títulos, viñetas y lista numerada, sin marcas a la vista', () => {
    const box = draw(SAMPLE);
    expect(within(box).getByText('Apple iPhone 13 Pro Max').tagName).toBe('STRONG');
    expect(within(box).getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['Características principales', 'Capacidades']);
    const [bullets, numbered] = within(box).getAllByRole('list');
    expect(bullets.tagName).toBe('UL');
    expect(within(bullets).getAllByRole('listitem')).toHaveLength(6);
    expect(numbered.tagName).toBe('OL');
    expect(within(numbered).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['128 GB', '256 GB', '512 GB', '1 TB']);
    expect(box.textContent).not.toMatch(/\*\*|##|^- /m);
    expect(box.querySelectorAll('p')).toHaveLength(2);
  });

  it('*cursiva*, _cursiva_, __negrita__ y `código`', () => {
    const box = draw('Uno *dos* tres _cuatro_ __cinco__ `seis`');
    expect(within(box).getByText('dos').tagName).toBe('EM');
    expect(within(box).getByText('cuatro').tagName).toBe('EM');
    expect(within(box).getByText('cinco').tagName).toBe('STRONG');
    expect(within(box).getByText('seis').tagName).toBe('CODE');
  });

  it('# y ## son subtítulos h2 (el h1 es el nombre del producto); ### en adelante, h3', () => {
    const box = draw('# Uno\n\n## Dos\n\n### Tres\n\n#### Cuatro');
    expect(within(box).getAllByRole('heading', { level: 2 })).toHaveLength(2);
    expect(within(box).getAllByRole('heading', { level: 3 })).toHaveLength(2);
    expect(within(box).queryByRole('heading', { level: 1 })).toBeNull();
  });

  it('párrafos separados por una línea en blanco; un salto simple dentro del párrafo se conserva', () => {
    const box = draw('Primero\nsigue igual\n\nSegundo');
    const ps = box.querySelectorAll('p');
    expect(ps).toHaveLength(2);
    expect(ps[0].querySelector('br')).not.toBeNull();
  });

  it('una descripción antigua sin Markdown se ve como antes, con sus saltos de línea', () => {
    const legacy = 'Equipo revisado.\nGarantía de 6 meses.\n\nIncluye cargador.';
    const box = draw(legacy);
    expect(box.querySelectorAll('p')).toHaveLength(2);
    expect(box.textContent).toBe('Equipo revisado.Garantía de 6 meses.Incluye cargador.');
  });

  it('viñetas con -, *, + y •; listas anidadas por sangría; la numeración respeta el número de inicio', () => {
    const blocks = parseMarkdown('- uno\n  - uno.a\n* dos\n+ tres\n• cuatro\n\n3. tres\n4. cuatro');
    expect(blocks[0]).toMatchObject({ t: 'ul' });
    const ul = blocks[0] as Extract<(typeof blocks)[number], { t: 'ul' }>;
    expect(ul.items).toHaveLength(4);
    expect(ul.items[0].children[0]).toMatchObject({ t: 'ul' });
    expect(blocks[1]).toMatchObject({ t: 'ol', start: 3 });
  });

  it('Markdown mal formado queda como texto: nada se pierde ni se inventa formato', () => {
    const box = draw('**sin cerrar y *otra\n\n2 * 3 = 6\n\nsnake_case_name y precio_total\n\n** solo');
    expect(box.querySelector('strong, em')).toBeNull();
    expect(box.textContent).toContain('**sin cerrar y *otra');
    expect(box.textContent).toContain('2 * 3 = 6');
    expect(box.textContent).toContain('snake_case_name y precio_total');
  });

  it('el texto normal no se convierte en lista ni en título por accidente', () => {
    const blocks = parseMarkdown('El año 2024. fue bueno\nPrecio -15% hoy\n#hashtag sin espacio');
    expect(blocks).toHaveLength(1);
    expect(blocks[0].t).toBe('p');
  });

  it('caracteres especiales, emojis y escapes', () => {
    const box = draw('Ñandú «comillas» — 100 % 😎 \\*no es cursiva\\*');
    expect(box.textContent).toBe('Ñandú «comillas» — 100 % 😎 *no es cursiva*');
  });

  it('separadores y citas', () => {
    const box = draw('Arriba\n\n---\n\n> Cita');
    expect(box.querySelector('hr')).not.toBeNull();
    expect(box.querySelector('blockquote')?.textContent).toBe('Cita');
  });

  it('una descripción de miles de caracteres se procesa entera y rápido', () => {
    const long = Array.from({ length: 400 }, (_, i) => `## Sección ${i}\n\nTexto con **negrita** y *cursiva* número ${i}.\n\n- a\n- b`).join('\n\n');
    expect(long.length).toBeGreaterThan(19000);
    let blocks: ReturnType<typeof parseMarkdown> = [];
    expect(fastestOf(() => { blocks = parseMarkdown(long); })).toBeLessThan(1000);
    expect(blocks.filter((b) => b.t === 'h')).toHaveLength(400);
  });
});

describe('Enlaces y seguridad', () => {
  it('sólo http(s), mailto, tel, rutas del sitio y anclas', () => {
    expect(safeHref('https://apple.com/iphone')).toBe('https://apple.com/iphone');
    expect(safeHref('mailto:hola@tienda.pe')).toBe('mailto:hola@tienda.pe');
    expect(safeHref('/modelo/iphone-13')).toBe('/modelo/iphone-13');
    for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'java\tscript:alert(1)', ' javascript:x',
      'data:text/html;base64,PHNjcmlwdD4=', 'vbscript:msgbox', '//evil.example', '/\\evil.example', 'file:///etc/passwd']) {
      expect(safeHref(bad)).toBeNull();
    }
  });

  it('un enlace permitido abre fuera con rel seguro; uno peligroso queda como texto sin <a>', () => {
    const box = draw('[Apple](https://apple.com) y [malo](javascript:alert(1)) y [interno](/servicios)');
    const links = within(box).getAllByRole('link');
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://apple.com', '/servicios']);
    expect(links[0]).toHaveAttribute('rel', 'nofollow noopener noreferrer ugc');
    expect(links[0]).toHaveAttribute('target', '_blank');
    expect(links[1]).not.toHaveAttribute('target');
    expect(box.textContent).toContain('malo');
    expect(box.innerHTML).not.toContain('javascript:');
  });

  it('HTML dentro de la descripción se muestra como texto: no hay script, iframe, img ni eventos', () => {
    const box = draw('<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">\n\n<iframe src="https://evil.example"></iframe> **ok**');
    expect(box.querySelector('script, iframe, img')).toBeNull();
    // Escaped as text (&lt;script&gt;…): no element and no attribute came from the description.
    expect(box.innerHTML).not.toMatch(/<script|<iframe|<img/);
    const attributes = Array.from(box.querySelectorAll('*')).flatMap((el) => Array.from(el.attributes).map((a) => a.name));
    expect(attributes.filter((name) => name.startsWith('on') || name === 'src')).toEqual([]);
    expect(box.textContent).toContain('<script>alert(1)</script>');
    expect(within(box).getByText('ok').tagName).toBe('STRONG');
  });

  it('el texto plano para buscadores y tarjetas no lleva marcas y se corta en una palabra', () => {
    const text = markdownToPlainText(SAMPLE, 160);
    expect(text).not.toMatch(/[*#]/);
    expect(text.startsWith('Apple iPhone 13 Pro Max. El iPhone 13 Pro Max combina')).toBe(true);
    expect(text.length).toBeLessThanOrEqual(160);
    expect(markdownToPlainText('')).toBe('');
    expect(markdownToPlainText('<b>hola</b> **x**')).toBe('<b>hola</b> x');
    expect(markdownToPlainText('Funda de silicona')).toBe('Funda de silicona');
  });
});

describe('Leer más', () => {
  it('una descripción larga se pliega y se despliega; una corta no muestra el botón', () => {
    const long = `${'Párrafo largo de prueba con bastante texto. '.repeat(30)}`;
    render(<RichText source={long} collapsible testId="largo" />);
    const button = screen.getByRole('button', { name: 'Leer más' });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(button);
    expect(screen.getByRole('button', { name: 'Leer menos' })).toHaveAttribute('aria-expanded', 'true');
    render(<RichText source="Corta." collapsible />);
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });
});

describe('Pegar desde ChatGPT y otros', () => {
  const CHATGPT_HTML = '<h2>Características</h2><p>El <strong>iPhone 13</strong> es <em>rápido</em>.</p>'
    + '<ul><li><p>Pantalla OLED</p></li><li>Face ID<ul><li>Seguro</li></ul></li></ul><ol><li>128 GB</li><li>256 GB</li></ol>'
    + '<p><a href="https://apple.com">Apple</a> y <a href="javascript:alert(1)">malo</a></p>'
    + '<script>alert(1)</script><style>p{}</style><img src=x onerror=alert(1)><iframe src="https://x"></iframe><hr>';

  it('el HTML de una respuesta copiada se convierte en el mismo Markdown, sin nada peligroso', () => {
    expect(htmlToMarkdown(CHATGPT_HTML)).toBe([
      '## Características',
      'El **iPhone 13** es *rápido*.',
      '- Pantalla OLED\n- Face ID\n   - Seguro',
      '1. 128 GB\n2. 256 GB',
      '[Apple](https://apple.com) y malo',
      '---',
    ].join('\n\n'));
  });

  it('el Markdown copiado con el botón «Copiar» (texto plano) se pega tal cual', () => {
    expect(markdownForPaste('', SAMPLE)).toBeNull();
    expect(markdownForPaste(null, '**hola**\n\n- uno')).toBeNull();
  });

  it('HTML sin estructura (un editor de código, un chat) no reemplaza el texto plano', () => {
    expect(markdownForPaste('<div><span style="color:red">**hola**</span></div>', '**hola**')).toBeNull();
  });

  it('Google Docs envuelve todo en <b style="font-weight:normal">: no es negrita; un span en 700 sí', () => {
    const md = htmlToMarkdown('<b style="font-weight:normal" id="docs-internal-guid-1"><p><span>Normal</span> <span style="font-weight:700">fuerte</span></p></b>');
    expect(md).toBe('Normal **fuerte**');
  });

  it('el texto pegado que parece Markdown pero era texto se escapa: no se duplican asteriscos ni se inventa formato', () => {
    const md = htmlToMarkdown('<p>Precio *especial* 2*3 y # 1 en ventas</p><p>- no es lista</p>');
    expect(md).toBe('Precio \\*especial\\* 2\\*3 y # 1 en ventas\n\n\\- no es lista');
    const box = draw(md);
    expect(box.querySelector('em, ul')).toBeNull();
    expect(box.textContent).toContain('Precio *especial* 2*3');
  });

  it('los saltos de Windows se normalizan', () => {
    expect(markdownForPaste('', 'uno\r\ndos')).toBe('uno\ndos');
  });
});

function Harness({ initial = '', max }: { initial?: string; max?: number }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <DescriptionEditor id="d" label="Descripción" value={value} onChange={setValue} maxLength={max} />
      <output data-testid="value">{value}</output>
    </>
  );
}

const textarea = () => screen.getByLabelText('Descripción') as HTMLTextAreaElement;
const value = () => screen.getByTestId('value').textContent;
const select = (start: number, end: number) => { textarea().focus(); textarea().setSelectionRange(start, end); };

describe('Editor de descripciones', () => {
  beforeEach(() => {
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => { cb(0); return 0; });
  });
  afterEach(() => jest.restoreAllMocks());

  it('la barra aplica negrita, cursiva, títulos, listas, enlace y separador sobre lo seleccionado', () => {
    render(<Harness initial={'Hola mundo\nuno\ndos'} />);
    select(0, 4);
    fireEvent.click(screen.getByRole('button', { name: 'Negrita' }));
    expect(value()).toBe('**Hola** mundo\nuno\ndos');
    // Toggle off.
    select(2, 6);
    fireEvent.click(screen.getByRole('button', { name: 'Negrita' }));
    expect(value()).toBe('Hola mundo\nuno\ndos');
    select(5, 10);
    fireEvent.click(screen.getByRole('button', { name: 'Cursiva' }));
    expect(value()).toBe('Hola *mundo*\nuno\ndos');
    select(0, 0);
    fireEvent.click(screen.getByRole('button', { name: 'Título' }));
    expect(value()).toBe('## Hola *mundo*\nuno\ndos');
    select(0, 0);
    fireEvent.click(screen.getByRole('button', { name: 'Subtítulo' }));
    expect(value()).toBe('### Hola *mundo*\nuno\ndos');
    const at = value()!.indexOf('uno');
    select(at, value()!.length);
    fireEvent.click(screen.getByRole('button', { name: 'Lista numerada' }));
    expect(value()).toBe('### Hola *mundo*\n1. uno\n2. dos');
    select(at, value()!.length);
    fireEvent.click(screen.getByRole('button', { name: 'Lista con viñetas' }));
    expect(value()).toBe('### Hola *mundo*\n- uno\n- dos');
    select(value()!.length, value()!.length);
    fireEvent.click(screen.getByRole('button', { name: 'Separador' }));
    expect(value()).toBe('### Hola *mundo*\n- uno\n- dos\n\n---\n\n');
    fireEvent.click(screen.getByRole('button', { name: 'Enlace' }));
    expect(value()).toContain('[texto del enlace](https://)');
  });

  it('deshacer y rehacer recorren los cambios de la barra y lo escrito', () => {
    render(<Harness initial="Hola" />);
    select(0, 4);
    fireEvent.click(screen.getByRole('button', { name: 'Negrita' }));
    fireEvent.change(textarea(), { target: { value: '**Hola** y más' } });
    expect(value()).toBe('**Hola** y más');
    fireEvent.click(screen.getByRole('button', { name: 'Deshacer' }));
    expect(value()).toBe('**Hola**');
    fireEvent.click(screen.getByRole('button', { name: 'Deshacer' }));
    expect(value()).toBe('Hola');
    expect(screen.getByRole('button', { name: 'Deshacer' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Rehacer' }));
    expect(value()).toBe('**Hola**');
    fireEvent.keyDown(textarea(), { key: 'z', ctrlKey: true });
    expect(value()).toBe('Hola');
    fireEvent.keyDown(textarea(), { key: 'z', ctrlKey: true, shiftKey: true });
    expect(value()).toBe('**Hola**');
  });

  it('pegar una respuesta de ChatGPT con formato inserta su Markdown en el cursor', () => {
    render(<Harness initial="Intro: " />);
    select(7, 7);
    const data = { getData: (type: string) => (type === 'text/html' ? '<h2>Título</h2><ul><li><strong>Uno</strong></li></ul>' : 'Título Uno') };
    fireEvent.paste(textarea(), { clipboardData: data });
    // Bloques pegados tras un texto empiezan en su propia línea.
    expect(value()).toBe('Intro: \n\n## Título\n\n- **Uno**');
  });

  it('pegar texto plano no lo toca (el navegador lo inserta tal cual)', () => {
    render(<Harness initial="" />);
    const data = { getData: (type: string) => (type === 'text/plain' ? '**ya es Markdown**' : '') };
    const prevented = !fireEvent.paste(textarea(), { clipboardData: data });
    expect(prevented).toBe(false);
  });

  it('la vista previa muestra lo que verá la tienda', () => {
    render(<Harness initial={'## Capacidades\n\n1. 128 GB'} />);
    fireEvent.click(screen.getByRole('tab', { name: 'Vista previa' }));
    const panel = screen.getByRole('tabpanel', { name: 'Vista previa de la descripción' });
    expect(within(panel).getByRole('heading', { level: 2, name: 'Capacidades' })).toBeInTheDocument();
    expect(within(panel).getByRole('listitem')).toHaveTextContent('128 GB');
    expect(screen.getByRole('button', { name: 'Negrita' })).toBeDisabled();
    fireEvent.click(screen.getByRole('tab', { name: 'Escribir' }));
    expect(textarea()).toBeVisible();
  });

  it('cuenta los caracteres y avisa si la descripción supera el máximo', () => {
    render(<Harness initial="12345" max={4} />);
    expect(screen.getByText(/5 \/ 4 caracteres · demasiado larga/)).toBeInTheDocument();
    expect(textarea()).toHaveAttribute('aria-invalid', 'true');
    expect(DESCRIPTION_MAX_LENGTH).toBe(20000);
  });

  it('la barra está etiquetada para lectores de pantalla', async () => {
    render(<Harness />);
    const bar = screen.getByRole('toolbar', { name: 'Formato de la descripción' });
    expect(within(bar).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Negrita', 'Cursiva', 'Título', 'Subtítulo', 'Lista con viñetas', 'Lista numerada', 'Enlace', 'Separador', 'Deshacer', 'Rehacer',
    ]);
    await act(async () => undefined);
  });
});

describe('Auditoría independiente: regresiones', () => {
  beforeEach(() => {
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => { cb(0); return 0; });
  });
  afterEach(() => jest.restoreAllMocks());

  it.each([
    ['título con 20.000 espacios', `# a${' '.repeat(20000)}x`],
    ['título dentro de una lista', `- a\n  # b${' '.repeat(20000)}x`],
    ['separador con espacios', `-${' '.repeat(20000)}- x`],
    ['cursivas sin cerrar (*)', '*a '.repeat(6666)],
    ['cursivas sin cerrar (_)', '_a '.repeat(6666)],
    ['negritas sin cerrar', '**a '.repeat(5000)],
    ['negritas anidadas', `${'**a '.repeat(2500)}${'a** '.repeat(2500)}`],
    ['cursivas anidadas', `${'*a '.repeat(3333)}${'a* '.repeat(3333)}`],
    ['corchetes', '[a'.repeat(10000)],
    ['citas anidadas', '>'.repeat(20000)],
  ])('entrada hostil de 20.000 caracteres se procesa en menos de 200 ms: %s', (_name, input) => {
    expect(fastestOf(() => { parseMarkdown(input); markdownToPlainText(input, 160); })).toBeLessThan(200);
  });

  it('***negrita y cursiva*** y sus mezclas, sin asteriscos sueltos', () => {
    const box = draw('***Oferta*** · **negrita *cursiva*** · *a **b***');
    expect(box.textContent).toBe('Oferta · negrita cursiva · a b');
    const first = within(box).getByText('Oferta');
    expect(first.tagName).toBe('EM');
    expect(first.parentElement?.tagName).toBe('STRONG');
    expect(within(box).getByText('cursiva').tagName).toBe('EM');
    expect(within(box).getByText('b').tagName).toBe('STRONG');
  });

  it('un año al inicio de línea es una frase, no una lista; «1)» sigue siendo lista', () => {
    expect(parseMarkdown('2024. El mejor año para la tienda')[0].t).toBe('p');
    expect(parseMarkdown('1) Garantía')[0].t).toBe('ol');
  });

  it('pegar párrafos que empiezan con número, «~~~» o «---» no deja barras ni inventa bloques', () => {
    const md = htmlToMarkdown('<p>2024. Año</p><p>~~~</p><p>---</p><p>Fin</p>');
    const box = draw(md);
    expect(box.textContent).toBe('2024. Año~~~---Fin');
    expect(box.querySelector('ol, pre, hr')).toBeNull();
  });

  it('pegar cursivas contiguas, enlaces con «]» o con salto de línea se lee como se pegó', () => {
    expect(htmlToMarkdown('<p><em>x</em><em>y</em></p>')).toBe('*xy*');
    const box = draw(htmlToMarkdown('<p><a href="https://a.com">li]nk</a> y <a href="https://b.com">uno<br>dos</a></p>'));
    expect(within(box).getAllByRole('link').map((a) => a.textContent)).toEqual(['li]nk', 'uno dos']);
  });

  it('una cita con lista pegada conserva la lista', () => {
    const box = draw(htmlToMarkdown('<blockquote><ul><li>z</li></ul></blockquote>'));
    expect(box.querySelector('blockquote ul li')?.textContent).toBe('z');
  });

  it('pegar bloques en medio de una línea los separa en líneas propias', () => {
    render(<Harness initial="ab" />);
    select(1, 1);
    const data = { getData: (type: string) => (type === 'text/html' ? '<h2>T</h2><p><b>x</b></p>' : 'T x') };
    fireEvent.paste(textarea(), { clipboardData: data });
    expect(value()).toBe('a\n\n## T\n\n**x**\n\nb');
  });

  it('Cursiva sobre un texto en negrita lo deja en negrita y cursiva, y se puede quitar', () => {
    render(<Harness initial="**fuerte**" />);
    select(2, 8);
    fireEvent.click(screen.getByRole('button', { name: 'Cursiva' }));
    expect(value()).toBe('***fuerte***');
    select(3, 9);
    fireEvent.click(screen.getByRole('button', { name: 'Cursiva' }));
    expect(value()).toBe('**fuerte**');
  });

  it('escribir justo después de un cambio externo empieza un paso nuevo del historial', () => {
    function Reset() {
      const [v, setV] = useState('uno');
      return (
        <>
          <button type="button" onClick={() => setV('RESET')}>reset</button>
          <DescriptionEditor id="d" label="Descripción" value={v} onChange={setV} />
          <output data-testid="value">{v}</output>
        </>
      );
    }
    render(<Reset />);
    fireEvent.change(textarea(), { target: { value: 'uno dos' } });
    fireEvent.click(screen.getByRole('button', { name: 'reset' }));
    fireEvent.change(textarea(), { target: { value: 'RESETz' } });
    fireEvent.click(screen.getByRole('button', { name: 'Deshacer' }));
    expect(value()).toBe('RESET');
  });
});
