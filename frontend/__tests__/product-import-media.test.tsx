import { fireEvent, render, screen, within } from '@testing-library/react';

import { ImportImagesField, MediaSummary, rowImagesLabel } from '@/app/admin/components/ImportWizard';
import { previewProductImport, type ImportJob, type ImportMediaSummary } from '@/app/admin/lib/internal-api';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * BULK-MEDIA · la carga masiva con imágenes, en el panel.
 *
 * El servidor casa cada fila con sus archivos y dice qué falta. El panel envía
 * los archivos junto al libro y enseña ese resultado sin maquillarlo.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const file = (name: string, type = 'image/png', bytes = 10) =>
  new File([new Uint8Array(bytes)], name, { type });

const media = (extra: Partial<ImportMediaSummary> = {}): ImportMediaSummary => ({
  attached: 0, referenced: 0, valid: 0, invalid: 0, missing: 0, duplicates: 0, orphans: 0,
  orphan_names: [], ignored: 0, ignored_names: [], already_present: 0, staged: 0, separator: '|',
  ...extra,
});

describe('la petición de previsualización', () => {
  it('lleva el libro, cada imagen y el ZIP en la misma petición', async () => {
    send.mockResolvedValueOnce({ ok: true, status: 201, json: async () => ({ id: 1 }) } as Response);
    const book = file('productos.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
    const zip = file('fotos.zip', 'application/zip');

    await previewProductImport(4, book, {
      sheetName: 'Productos', mapping: { name: 1 },
      images: [file('a.png'), file('b.webp', 'image/webp')], imagesZip: zip,
    });

    const [url, init] = send.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/products\/import\/preview\/\?company=4$/);
    const form = init!.body as FormData;
    expect((form.get('file') as File).name).toBe('productos.xlsx');
    expect(form.getAll('images').map((f) => (f as File).name)).toEqual(['a.png', 'b.webp']);
    expect((form.get('images_zip') as File).name).toBe('fotos.zip');
    // Sin Content-Type: lo escribe el navegador, con el boundary.
    expect(init!.headers).toBeUndefined();
  });

  it('sin imágenes, la petición es la de siempre', async () => {
    send.mockResolvedValueOnce({ ok: true, status: 201, json: async () => ({ id: 1 }) } as Response);
    await previewProductImport(null, file('p.xlsx'), {});
    const form = send.mock.calls.at(-1)![1]!.body as FormData;
    expect(form.getAll('images')).toEqual([]);
    expect(form.get('images_zip')).toBeNull();
  });

  it('un lote demasiado grande se explica', async () => {
    send.mockResolvedValueOnce({ ok: false, status: 413, json: async () => { throw new Error('html'); } } as unknown as Response);
    await expect(previewProductImport(null, file('p.xlsx'), { images: [file('a.png')] }))
      .rejects.toThrow(/demasiado/i);
  });
});

describe('adjuntar imágenes', () => {
  function Harness({ onChange }: { onChange: (images: File[], zip: File | null) => void }) {
    return <ImportImagesField images={[]} zip={null} onChange={onChange} />;
  }

  it('añade las imágenes elegidas y deja fuera lo que no lo es', () => {
    const onChange = jest.fn();
    render(<Harness onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(/Elegir imágenes/), {
      target: { files: [file('a.png'), file('notas.txt', 'text/plain'), file('b.jpg', 'image/jpeg')] },
    });

    expect(onChange).toHaveBeenCalledWith([expect.objectContaining({ name: 'a.png' }), expect.objectContaining({ name: 'b.jpg' })], null);
    expect(screen.getByRole('status')).toHaveTextContent('notas.txt');
  });

  it('acepta un ZIP y sólo un ZIP en su campo', () => {
    const onChange = jest.fn();
    render(<Harness onChange={onChange} />);

    fireEvent.change(screen.getByLabelText(/Elegir un ZIP/), { target: { files: [file('fotos.zip', 'application/zip')] } });

    expect(onChange).toHaveBeenCalledWith([], expect.objectContaining({ name: 'fotos.zip' }));
  });

  it('muestra lo adjunto y permite quitarlo', () => {
    const onChange = jest.fn();
    render(<ImportImagesField images={[file('a.png'), file('b.png')]} zip={file('fotos.zip', 'application/zip')} onChange={onChange} />);

    expect(screen.getByText(/2 imágenes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Quitar a.png' }));
    expect(onChange).toHaveBeenLastCalledWith([expect.objectContaining({ name: 'b.png' })], expect.objectContaining({ name: 'fotos.zip' }));

    fireEvent.click(screen.getByRole('button', { name: 'Quitar fotos.zip' }));
    expect(onChange).toHaveBeenLastCalledWith(expect.any(Array), null);
  });

  it('un archivo con el mismo nombre SUSTITUYE al anterior, y lo dice', () => {
    // Quien corrige una imagen inválida vuelve a adjuntarla con su mismo
    // nombre: tiene que viajar la nueva, no la que ya estaba mal.
    const onChange = jest.fn();
    const old = file('a.png', 'image/png', 10);
    render(<ImportImagesField images={[old, file('b.png')]} zip={null} onChange={onChange} />);

    const fixed = file('A.PNG', 'image/png', 99);
    fireEvent.change(screen.getByLabelText(/Elegir imágenes/), { target: { files: [fixed, file('c.png')] } });

    const next = onChange.mock.calls[0][0] as File[];
    expect(next.map((f) => f.name)).toEqual(['A.PNG', 'b.png', 'c.png']);
    expect(next[0]).toBe(fixed);
    expect(screen.getByRole('status')).toHaveTextContent('Se reemplazó');
    expect(screen.getByRole('status')).toHaveTextContent('A.PNG');
  });
});

describe('resumen de imágenes de la previsualización', () => {
  const job = (summary: ImportMediaSummary | undefined) => ({ summary: { media: summary } }) as unknown as ImportJob;

  it('no aparece si el archivo no habla de imágenes', () => {
    const { container } = render(<MediaSummary job={job(media())} />);
    expect(container).toBeEmptyDOMElement();
    const legacy = render(<MediaSummary job={job(undefined)} />);
    expect(legacy.container).toBeEmptyDOMElement();
  });

  it('dice cuántas se usarán y nombra lo que falta o sobra', () => {
    render(<MediaSummary job={job(media({
      attached: 5, referenced: 4, valid: 2, invalid: 1, missing: 1, duplicates: 1, orphans: 2,
      orphan_names: ['sobra.png', 'otra.png'], ignored: 1, ignored_names: ['leeme.txt'], already_present: 1,
    }))} />);

    const box = screen.getByRole('region', { name: 'Imágenes de la importación' });
    const value = (label: string) => within(box).getByText(label).parentElement!.textContent;
    expect(value('Adjuntas')).toContain('5');
    expect(value('Válidas')).toContain('2');
    expect(value('Faltantes')).toContain('1');
    expect(value('Inválidas')).toContain('1');
    expect(value('Duplicadas')).toContain('1');
    expect(value('Sin usar')).toContain('2');
    expect(box).toHaveTextContent('sobra.png');
    expect(box).toHaveTextContent('leeme.txt');
    expect(box).toHaveTextContent('1 ya estaba en la galería');
  });

  it('avisa cuando las filas citan imágenes y no se adjuntó ninguna', () => {
    render(<MediaSummary job={job(media({ referenced: 3, missing: 3 }))} />);
    expect(screen.getByRole('region', { name: 'Imágenes de la importación' }))
      .toHaveTextContent('Adjunta las imágenes');
  });
});

describe('las imágenes de una fila', () => {
  it('nombra la principal primero y cuenta las demás', () => {
    expect(rowImagesLabel({ data: { images: [
      { name: 'b.png', primary: false }, { name: 'a.png', primary: true }, { name: 'c.png', primary: false },
    ] } })).toBe('★ a.png + 2');
    expect(rowImagesLabel({ data: { images: [{ name: 'a.png', primary: false }] } })).toBe('a.png');
    expect(rowImagesLabel({ data: {} })).toBe('');
  });
});
