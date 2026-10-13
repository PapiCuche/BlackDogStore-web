/**
 * INVENTORY-UNIFIED-01 — imágenes en lote, en el panel.
 *
 * Se adjuntan muchas imágenes (o un ZIP) y cada una va a un producto por el
 * nombre del archivo. La pantalla dice la regla antes de adjuntar nada, enseña
 * qué va a pasar antes de que pase y, si se va a reemplazar una galería, pide
 * escribir la palabra. Qué archivo es de qué producto lo decide el servidor
 * (`test_image_import.py`); aquí se fija que se pida y se muestre bien.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { BulkImageImport } from '@/app/admin/components/BulkImageImport';
import { ProductsManager } from '@/app/admin/components/ProductsManager';
import * as api from '@/app/admin/lib/internal-api';
import * as admin from '@/app/lib/admin';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  previewImageImport: jest.fn(),
  applyImageImport: jest.fn(),
  fetchImageImportRules: jest.fn(),
}));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
  fetchAdminCategories: jest.fn(),
}));

const mocked = api as jest.Mocked<typeof api>;
const adminApi = admin as jest.Mocked<typeof admin>;

const RULES: api.ImageImportRules = {
  pattern: '<código>.<ext>', suffixes: ['_2', '-2', ' (2)'],
  examples: ['FUNDA.png', 'FUNDA_2.png', 'FUNDA-3.jpg', 'FUNDA (4).webp'],
  confirm_word: 'REEMPLAZAR', workbook: { sheet: 'Imágenes', headers: ['Código', 'Imagen principal', 'Imágenes'] },
  max_per_product: 12,
  image_rules: { separator: '|', formats: ['PNG', 'JPG', 'JPEG', 'WEBP'], max_per_product: 12, max_file_mb: 8, max_files: 200, max_total_mb: 100 },
};

const image = (name: string, existing = false, primary = false) => ({
  name, address: `/api/storefront/images/${name}`, sha256: name, primary, existing,
});

const productRow = (over: Record<string, unknown> = {}, extra: Partial<api.ImportRow> = {}): api.ImportRow => ({
  sheet: '', row: 1, action: 'update', match_key: 'COD-A1', errors: [], warnings: [],
  data: {
    product_id: 5, name: 'Teléfono', code: 'COD-A1', barcode: '',
    images: [image('COD-A1.png', false, true), image('COD-A1_2.png'), image('COD-A1_3.png', true)],
    gallery_count: 1, gallery_ids: [9], will_add: 2, will_remove: 0, main: 'COD-A1.png', ...over,
  },
  ...extra,
} as api.ImportRow);

const skipRow = (file: string, sentence: string): api.ImportRow => ({
  sheet: '', row: 9, action: 'skip', match_key: file, errors: [], warnings: [sentence],
  data: { file, reason: 'no_product' },
} as api.ImportRow);

const job = (rows: api.ImportRow[], over: Partial<api.ImportJob> = {}): api.ImportJob => ({
  id: 31, import_type: 'product_images', status: 'previewed', stock_mode: '', original_filename: '', file_sha256: '',
  mapping: {}, options: { mode: 'add', use_main: false, source: 'names' },
  counts: {
    total: rows.length, create: 0, update: rows.filter((r) => r.action === 'update').length,
    no_change: rows.filter((r) => r.action === 'no_change').length,
    skip: rows.filter((r) => r.action === 'skip').length, error: rows.filter((r) => r.action === 'error').length,
  },
  summary: summary(),
  created_at: '2026-10-13T15:00:00Z', applied_at: null, created_by: 'ana', applied_by: '', is_applicable: true,
  rows, ...over,
});

const png = (name: string) => new File(['x'], name, { type: 'image/png' });

function summary(over: Partial<api.ImportJob['summary']> = {}, ignoredNames: string[] = []): api.ImportJob['summary'] {
  return {
    products: 1, images_to_add: 2, images_to_remove: 0, unmatched: 0, confirm_word: '',
    media: {
      attached: 3, referenced: 3, valid: 3, invalid: 0, missing: 0, duplicates: 0, orphans: 0, orphan_names: [],
      ignored: ignoredNames.length, ignored_names: ignoredNames, already_present: 0, staged: 2, separator: '|',
    },
    ...over,
  };
}

/**
 * What the server answers to APPLY: the job, applied, WITHOUT its rows — only
 * the preview carries them (`_job_payload(job)` against `_job_payload(job, rows=True)`).
 * The mock used to hand the rows back, and hid that the result had no links.
 */
function appliedAnswer(staged: api.ImportJob, applied: Record<string, number>): api.ImportJob {
  const { rows: _onlyInThePreview, rows_truncated: _neither, ...rest } = staged;
  void _onlyInThePreview; void _neither;
  return { ...rest, status: 'applied', is_applicable: false, summary: { ...staged.summary, applied } };
}

async function open() {
  await act(async () => { render(<BulkImageImport companyId={7} />); });
}
const attach = (...files: File[]) => fireEvent.change(screen.getByLabelText('Elegir imágenes'), { target: { files } });
const previewButton = () => screen.getByRole('button', { name: 'Previsualizar' });
const preview = async () => { await act(async () => { fireEvent.click(previewButton()); }); };
const lastPreview = () => mocked.previewImageImport.mock.calls[mocked.previewImageImport.mock.calls.length - 1];

async function previewed(result: api.ImportJob, ...files: File[]) {
  mocked.previewImageImport.mockResolvedValueOnce(result);
  await open();
  attach(...(files.length ? files : [png('COD-A1.png')]));
  await preview();
}

beforeEach(() => {
  mocked.previewImageImport.mockReset();
  mocked.applyImageImport.mockReset();
  mocked.fetchImageImportRules.mockReset().mockResolvedValue(RULES);
});

describe('antes de adjuntar nada', () => {
  it('dice cómo se nombra un archivo, con un ejemplo, y qué decide la principal', async () => {
    await open();
    const rule = screen.getByRole('note', { name: 'Cómo nombrar las imágenes' });
    expect(rule).toHaveTextContent(/código del producto/);
    for (const example of ['FUNDA.png', 'FUNDA_2.png', 'FUNDA-3.jpg', 'FUNDA (4).webp']) {
      expect(rule).toHaveTextContent(example);
    }
    expect(rule).toHaveTextContent(/número más bajo.*principal/i);
    expect(rule).toHaveTextContent(/código interno o su código de barras, tal cual/);
    expect(rule).toHaveTextContent(/No se busca por el nombre del producto/);
  });

  it('enseña los límites que aplica el servidor', async () => {
    await open();
    expect(mocked.fetchImageImportRules).toHaveBeenCalledWith(7);
    const rule = screen.getByRole('note', { name: 'Cómo nombrar las imágenes' });
    expect(rule).toHaveTextContent('Hasta 12 imágenes por producto, 8 MB cada una y 200 archivos por carga.');
  });

  it('si los límites no se pueden leer, la regla sigue ahí', async () => {
    mocked.fetchImageImportRules.mockRejectedValueOnce(new Error('x'));
    await open();
    expect(screen.getByRole('note', { name: 'Cómo nombrar las imágenes' })).toHaveTextContent('FUNDA_2.png');
    expect(screen.queryByText(/archivos por carga/)).toBeNull();
  });

  it('sin imágenes no hay nada que previsualizar', async () => {
    await open();
    expect(previewButton()).toBeDisabled();
    attach(png('COD-A1.png'));
    expect(previewButton()).toBeEnabled();
  });
});

describe('lo que se pide al servidor', () => {
  it('por defecto: añadir, sin tocar la principal, sin libro', async () => {
    const file = png('COD-A1.png');
    await previewed(job([productRow()]), file);
    expect(lastPreview()).toEqual([7, { images: [file], imagesZip: null, book: null, mode: 'add', useMain: false }]);
  });

  it('con «usar como principal», un ZIP y el libro «Imágenes»', async () => {
    mocked.previewImageImport.mockResolvedValueOnce(job([productRow()]));
    await open();
    const zip = new File(['z'], 'fotos.zip', { type: 'application/zip' });
    const book = new File(['b'], 'imagenes.xlsx');
    fireEvent.change(screen.getByLabelText(/Elegir un ZIP/), { target: { files: [zip] } });
    fireEvent.change(screen.getByLabelText(/Libro «Imágenes» \(opcional\)/), { target: { files: [book] } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Usar como principal la primera imagen/ }));
    await preview();

    expect(lastPreview()).toEqual([7, { images: [], imagesZip: zip, book, mode: 'add', useMain: true }]);
    expect(screen.getByText('imagenes.xlsx')).toBeInTheDocument();
  });

  it('reemplazar se elige a propósito, y ahí la principal es siempre la primera', async () => {
    mocked.previewImageImport.mockResolvedValueOnce(job([productRow()]));
    await open();
    const modes = screen.getByRole('radiogroup', { name: 'Qué hacer con la galería' });
    expect(within(modes).getByRole('radio', { name: /Añadir a la galería/ })).toBeChecked();
    fireEvent.click(within(modes).getByRole('radio', { name: /Reemplazar la galería/ }));
    expect(modes).toHaveTextContent(/quita las imágenes que el producto tenía/i);
    expect(screen.queryByRole('checkbox', { name: /Usar como principal/ })).toBeNull();

    attach(png('COD-A1.png'));
    await preview();
    expect(lastPreview()[1]).toMatchObject({ mode: 'replace', useMain: false });
  });

  it('mientras sube lo dice y no se puede pedir dos veces', async () => {
    let finish: (value: api.ImportJob) => void = () => {};
    mocked.previewImageImport.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await open();
    attach(png('COD-A1.png'));
    await preview();

    expect(screen.getByRole('button', { name: /Subiendo y revisando/ })).toBeDisabled();
    await act(async () => { finish(job([productRow()])); });
    expect(mocked.previewImageImport).toHaveBeenCalledTimes(1);
  });

  it('lo que el servidor rechaza se muestra', async () => {
    mocked.previewImageImport.mockRejectedValueOnce(new Error('El ZIP contiene enlaces simbólicos. No se usa ningún archivo de él.'));
    await open();
    attach(png('COD-A1.png'));
    await preview();
    expect(screen.getByRole('alert')).toHaveTextContent('enlaces simbólicos');
    expect(screen.queryByRole('table')).toBeNull();
  });
});

describe('la vista previa', () => {
  it('por producto: qué se añade, cuál será la principal, qué ya estaba, y su galería', async () => {
    await previewed(job([productRow()]));

    expect(screen.getByText('2 imágenes para 1 producto. Todavía no se ha cambiado nada.')).toBeInTheDocument();
    const row = within(screen.getByRole('table')).getAllByRole('row')[1];
    expect(row).toHaveTextContent('Teléfono');
    expect(row).toHaveTextContent('COD-A1');
    expect(within(row).getByRole('link', { name: 'Ver galería de Teléfono' })).toHaveAttribute('href', '/admin/products/5');
    // Miniaturas de lo que se va a añadir; lo que ya estaba no se vuelve a enseñar.
    expect(within(row).getAllByRole('img').map((img) => [img.getAttribute('alt'), img.getAttribute('src')])).toEqual([
      ['COD-A1.png', '/api/storefront/images/COD-A1.png'], ['COD-A1_2.png', '/api/storefront/images/COD-A1_2.png'],
    ]);
    expect(row).toHaveTextContent('Principal: COD-A1.png');
    expect(row).toHaveTextContent('1 ya estaba en la galería');
  });

  it('si la principal no cambia, lo dice', async () => {
    await previewed(job([productRow({ main: '' })]));
    expect(within(screen.getByRole('table')).getAllByRole('row')[1]).toHaveTextContent('La principal no cambia');
  });

  it('los archivos sin producto se listan con su motivo y no impiden lo demás', async () => {
    await previewed(job([
      productRow(),
      skipRow('NADIE.png', '«NADIE.png»: ningún producto de esta empresa tiene ese código. No se carga.'),
    ]));
    const orphans = screen.getByRole('region', { name: 'Archivos que no se cargarán' });
    expect(within(orphans).getAllByRole('listitem').map((item) => item.textContent))
      .toEqual(['«NADIE.png»: ningún producto de esta empresa tiene ese código. No se carga.']);
    expect(screen.getByRole('button', { name: 'Añadir 2 imágenes' })).toBeEnabled();
  });

  it('lo que el lote ignoró por no ser una imagen también se dice', async () => {
    await previewed(job([productRow()], {
      summary: summary({}, ['notas.txt']),
    }));
    expect(screen.getByText(/No son imágenes y se ignoraron: notas\.txt/)).toBeInTheDocument();
  });

  it('con errores no se puede aplicar: se dicen por producto y se pueden descargar', async () => {
    await previewed(job([
      productRow({ will_add: 0 }, { action: 'error', errors: ['Imagen «COD-A1.png»: el archivo no es una imagen válida.'] }),
    ], { is_applicable: false }));

    expect(within(screen.getByRole('table')).getAllByRole('row')[1]).toHaveTextContent('el archivo no es una imagen válida');
    expect(screen.queryByRole('button', { name: /Añadir/ })).toBeNull();
    expect(screen.getByRole('alert')).toHaveTextContent(/1 producto con error/);
    expect(screen.getByRole('link', { name: 'Descargar errores' }).getAttribute('href'))
      .toMatch(/\/admin\/imports\/31\/errors\.csv\?company=7$/);
  });

  it('si todo estaba ya, lo dice y no ofrece aplicar', async () => {
    await previewed(job([productRow({ will_add: 0, main: '' }, { action: 'no_change' })], {
      summary: summary({ images_to_add: 0 }),
    }));
    expect(screen.getByText(/No hay nada que añadir/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Añadir|Aplicar|Reemplazar/ })).toBeNull();
  });
});

describe('aplicar', () => {
  it('añadir: se aplica ese trabajo y se dice lo que pasó, con enlace a cada galería', async () => {
    const staged = job([productRow()]);
    await previewed(staged);
    mocked.applyImageImport.mockResolvedValueOnce(
      appliedAnswer(staged, { products: 1, images_added: 2, images_removed: 0, made_main: 0 }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Añadir 2 imágenes' })); });

    expect(mocked.applyImageImport).toHaveBeenCalledWith(7, 31, undefined);
    const result = screen.getByRole('status');
    expect(result).toHaveTextContent('Se añadieron 2 imágenes a 1 producto.');
    expect(within(result).getByRole('link', { name: 'Ver galería de Teléfono' })).toHaveAttribute('href', '/admin/products/5');
    expect(result).toHaveTextContent(/orden.*texto alternativo/i);
    expect(screen.queryByRole('button', { name: /Añadir 2/ })).toBeNull();
  });

  it('reemplazar: dice cuántas se quitan y no deja aplicar sin escribir la palabra', async () => {
    const staged = job([productRow({ will_add: 1, will_remove: 2 })], {
      options: { mode: 'replace', use_main: false, source: 'names' },
      summary: summary({ images_to_add: 1, images_to_remove: 2, confirm_word: 'REEMPLAZAR' }),
    });
    await previewed(staged);

    expect(screen.getByRole('alert')).toHaveTextContent('Se quitarán 2 imágenes de 1 producto');
    expect(within(screen.getByRole('table')).getAllByRole('row')[1]).toHaveTextContent('Se quitan 2');
    const button = screen.getByRole('button', { name: 'Reemplazar galerías' });
    const word = screen.getByLabelText('Escribe REEMPLAZAR para confirmar');
    expect(button).toBeDisabled();
    fireEvent.change(word, { target: { value: 'reemplazar' } });
    expect(button).toBeDisabled();
    fireEvent.change(word, { target: { value: 'REEMPLAZAR' } });
    expect(button).toBeEnabled();

    mocked.applyImageImport.mockResolvedValueOnce(
      appliedAnswer(staged, { products: 1, images_added: 1, images_removed: 2, made_main: 0 }));
    await act(async () => { fireEvent.click(button); });

    expect(mocked.applyImageImport).toHaveBeenCalledWith(7, 31, 'REEMPLAZAR');
    expect(screen.getByRole('status')).toHaveTextContent('Se quitaron 2 imágenes y se añadió 1 imagen en 1 producto.');
    expect(within(screen.getByRole('status')).getByRole('link', { name: 'Ver galería de Teléfono' })).toBeInTheDocument();
  });

  it('añadir no pide ninguna palabra', async () => {
    await previewed(job([productRow()]));
    expect(screen.queryByLabelText(/Escribe REEMPLAZAR/)).toBeNull();
  });

  it('si el servidor no aplica, lo dice y la vista previa sigue ahí', async () => {
    await previewed(job([productRow()]));
    mocked.applyImageImport.mockRejectedValueOnce(new Error('La galería de «Teléfono» cambió desde la previsualización.'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Añadir 2 imágenes' })); });

    expect(screen.getByRole('alert')).toHaveTextContent('cambió desde la previsualización');
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('«Empezar otra carga» deja la pantalla como al principio', async () => {
    const staged = job([productRow()]);
    await previewed(staged);
    mocked.applyImageImport.mockResolvedValueOnce(
      appliedAnswer(staged, { products: 1, images_added: 2, images_removed: 0, made_main: 0 }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Añadir 2 imágenes' })); });
    fireEvent.click(screen.getByRole('button', { name: 'Empezar otra carga' }));

    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('table')).toBeNull();
    expect(previewButton()).toBeDisabled();
  });
});

describe('las peticiones', () => {
  const real = jest.requireActual('@/app/admin/lib/internal-api') as typeof api;
  /* eslint-disable-next-line @typescript-eslint/no-require-imports */
  const { fetchWithAuth } = require('@/app/lib/auth') as { fetchWithAuth: jest.Mock };
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

  beforeEach(() => fetchWithAuth.mockReset());

  it('la vista previa va por la carga de productos, diciendo que son sólo imágenes', async () => {
    fetchWithAuth.mockResolvedValueOnce(ok(job([])));
    const [a, b] = [png('COD-A1.png'), png('COD-A1_2.png')];
    const zip = new File(['z'], 'fotos.zip');
    const book = new File(['b'], 'imagenes.xlsx');
    await real.previewImageImport(7, { images: [a, b], imagesZip: zip, book, mode: 'replace', useMain: true });

    const [url, init] = fetchWithAuth.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/products\/import\/preview\/\?company=7$/);
    const form = init.body as FormData;
    expect(form.get('kind')).toBe('images');
    expect(form.getAll('images')).toEqual([a, b]);
    expect(form.get('images_zip')).toBe(zip);
    expect(form.get('file')).toBe(book);
    expect([form.get('mode'), form.get('use_main')]).toEqual(['replace', 'true']);
    expect(init.headers).toBeUndefined();
  });

  it('sin ZIP, sin libro y sin marcar, no manda esos campos', async () => {
    fetchWithAuth.mockResolvedValueOnce(ok(job([])));
    await real.previewImageImport(null, { images: [png('A.png')], mode: 'add', useMain: false });
    const form = fetchWithAuth.mock.calls[0][1].body as FormData;
    expect([form.has('images_zip'), form.has('file'), form.has('use_main')]).toEqual([false, false, false]);
    expect(form.get('mode')).toBe('add');
  });

  it('aplicar va a su propia dirección, sin archivos, con la palabra si la hay', async () => {
    fetchWithAuth.mockResolvedValue(ok(job([])));
    await real.applyImageImport(7, 31, 'REEMPLAZAR');
    await real.applyImageImport(null, 32);

    const [url, init] = fetchWithAuth.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/products\/images\/import\/31\/apply\/\?company=7$/);
    expect([init.method, JSON.parse(init.body)]).toEqual(['POST', { confirm: 'REEMPLAZAR' }]);
    expect(JSON.parse(fetchWithAuth.mock.calls[1][1].body)).toEqual({});
  });

  it('lo que el servidor explica al no aplicar se conserva', async () => {
    fetchWithAuth.mockResolvedValueOnce({ ok: false, status: 400, json: async () => ({ detail: 'Para reemplazar las galerías escribe REEMPLAZAR.' }) });
    await expect(real.applyImageImport(7, 31)).rejects.toThrow('Para reemplazar las galerías escribe REEMPLAZAR.');
  });

  it('las reglas se piden para la empresa elegida', async () => {
    fetchWithAuth.mockResolvedValueOnce(ok(RULES));
    expect(await real.fetchImageImportRules(7)).toEqual(RULES);
    expect(String(fetchWithAuth.mock.calls[0][0])).toMatch(/\/admin\/products\/images\/import\/rules\/\?company=7$/);
  });
});

describe('desde la lista de productos', () => {
  beforeEach(() => {
    adminApi.fetchAdminProducts.mockReset().mockResolvedValue({ count: 0, page: 1, page_size: 20, results: [] });
    adminApi.fetchAdminCategories.mockReset().mockResolvedValue([]);
  });

  it('quien gestiona el catálogo tiene el enlace a las imágenes en lote', async () => {
    await act(async () => { render(<ProductsManager canManage />); });
    expect(screen.getByRole('link', { name: 'Añadir imágenes en lote' })).toHaveAttribute('href', '/admin/inventory/bulk#imagenes');
  });

  it('quien sólo consulta, no', async () => {
    await act(async () => { render(<ProductsManager canManage={false} />); });
    expect(screen.queryByRole('link', { name: 'Añadir imágenes en lote' })).toBeNull();
  });
});
