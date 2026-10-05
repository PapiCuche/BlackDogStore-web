import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { EvidenceGallery, type Evidence } from '@/app/admin/service/components/EvidenceGallery';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * SERVICE-EVIDENCE-CONTEXT · el trabajo del técnico con las fotos.
 *
 * Abre la orden, elige la etapa, toma o elige varias fotos, les pone una nota y
 * las sube de una vez. Después las encuentra agrupadas por etapa, las abre en
 * grande y las recorre. Lo que se fija es ESE recorrido y lo que viaja al
 * servidor en cada paso.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const row = (id: number, extra: Partial<Evidence> = {}): Evidence => ({
  id, stage: 'intake', caption: '', visibility: 'internal', mime_type: 'image/webp',
  byte_size: 120_000, width: 1600, height: 1200, created_at: '2026-09-03T10:00:00Z',
  uploaded_by: 'tecnico', voided_at: null, void_reason: null, ...extra,
});

const photo = (name: string, type = 'image/jpeg', bytes = 10) =>
  new File([new Uint8Array(bytes)], name, { type });

let gallery: Evidence[];
type Sent = { method: string; path: string; form: FormData | null; json: unknown; key: string | null };
let sent: Sent[];
let failNextUpload: string | null;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  gallery = [];
  sent = [];
  failNextUpload = null;
  let nextId = 100;
  URL.createObjectURL = jest.fn((file: File) => `blob:${file.name}`);
  URL.revokeObjectURL = jest.fn();
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const path = String(input).replace(/^.*\/evidence/, '');
    const form = init.body instanceof FormData ? init.body : null;
    const headers = new Headers(init.headers);
    if (method !== 'GET') {
      sent.push({
        method, path, form, key: headers.get('Idempotency-Key'),
        json: typeof init.body === 'string' ? JSON.parse(init.body) : null,
      });
    }
    if (method === 'GET') {
      const counts: Record<string, number> = {};
      for (const item of gallery) if (!item.voided_at) counts[item.stage] = (counts[item.stage] ?? 0) + 1;
      return reply(200, { count: gallery.length, stage_counts: counts, results: gallery });
    }
    if (method === 'POST' && path === '/') {
      const name = (form!.get('image') as File).name;
      if (failNextUpload === name) {
        failNextUpload = null;
        return reply(400, { detail: 'La imagen está incompleta o dañada.' });
      }
      const created = row(nextId++, { stage: String(form!.get('stage')), caption: String(form!.get('caption') ?? '') });
      gallery = [...gallery, created];
      return reply(201, created);
    }
    if (method === 'PATCH') {
      const id = Number(path.split('/')[1]);
      gallery = gallery.map((item) => (item.id === id ? { ...item, caption: (JSON.parse(String(init.body)) as { caption: string }).caption } : item));
      return reply(200, gallery.find((item) => item.id === id));
    }
    return reply(200, {});
  });
});

const ALL = () => true;

async function mount(may: (capability: string) => boolean = ALL) {
  await act(async () => {
    render(<EvidenceGallery slug="tienda" orderId={9} may={may} />);
  });
}

function choose(files: File[]) {
  fireEvent.change(screen.getByLabelText('Elegir fotos'), { target: { files } });
}

describe('tomar y subir varias fotos', () => {
  it('ofrece la cámara del teléfono sin impedir elegir fotos ya tomadas', async () => {
    await mount();
    const picker = screen.getByLabelText('Elegir fotos') as HTMLInputElement;
    const camera = screen.getByLabelText('Tomar foto') as HTMLInputElement;

    expect(picker.multiple).toBe(true);
    expect(picker).not.toHaveAttribute('capture');
    expect(camera).toHaveAttribute('capture', 'environment');
    expect(camera.accept).toBe('image/*');
  });

  it('las fotos elegidas esperan con su vista previa y su nota; nada viaja todavía', async () => {
    await mount();

    choose([photo('frontal.jpg'), photo('trasera.jpg')]);

    const queue = screen.getByRole('list', { name: 'Fotos por subir' });
    expect(within(queue).getAllByRole('listitem')).toHaveLength(2);
    expect(within(queue).getByAltText('Vista previa de frontal.jpg')).toHaveAttribute('src', 'blob:frontal.jpg');
    expect(within(queue).getByLabelText('Nota de frontal.jpg')).toBeInTheDocument();
    expect(sent).toEqual([]);
  });

  it('sube todas de una vez, cada una con su etapa, su nota y su propia clave', async () => {
    await mount();
    fireEvent.change(screen.getByLabelText('Etapa de las fotos'), { target: { value: 'diagnosis' } });
    choose([photo('placa.jpg'), photo('conector.jpg')]);
    fireEvent.change(screen.getByLabelText('Nota de placa.jpg'), { target: { value: 'Corrosión junto al conector' } });

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir 2 fotos' })); });

    await waitFor(() => expect(sent.filter((call) => call.method === 'POST')).toHaveLength(2));
    const [first, second] = sent;
    expect(first.form!.get('stage')).toBe('diagnosis');
    expect((first.form!.get('image') as File).name).toBe('placa.jpg');
    expect(first.form!.get('caption')).toBe('Corrosión junto al conector');
    expect(second.form!.get('caption')).toBe('');
    expect(first.key).toBeTruthy();
    expect(second.key).toBeTruthy();
    expect(first.key).not.toBe(second.key);
    // Subidas: la cola queda vacía y la galería las muestra en su etapa.
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Fotos por subir' })).not.toBeInTheDocument());
    expect(await screen.findByRole('heading', { name: /Diagnóstico/ })).toBeInTheDocument();
    expect(screen.getByText('Corrosión junto al conector')).toBeInTheDocument();
  });

  it('una foto se puede quitar antes de subirla', async () => {
    await mount();
    choose([photo('a.jpg'), photo('b.jpg')]);
    fireEvent.click(screen.getByRole('button', { name: 'Quitar a.jpg' }));
    expect(screen.queryByLabelText('Nota de a.jpg')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Subir 1 foto' })).toBeEnabled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:a.jpg');
  });

  it('la que falla se queda con su motivo y se reintenta con la MISMA clave', async () => {
    await mount();
    choose([photo('rota.jpg'), photo('buena.jpg')]);
    failNextUpload = 'rota.jpg';

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir 2 fotos' })); });
    await waitFor(() => expect(sent).toHaveLength(2));

    expect(await screen.findByText('La imagen está incompleta o dañada.')).toBeInTheDocument();
    expect(screen.getByLabelText('Nota de rota.jpg')).toBeInTheDocument();
    expect(screen.queryByLabelText('Nota de buena.jpg')).not.toBeInTheDocument();

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir 1 foto' })); });
    await waitFor(() => expect(sent).toHaveLength(3));
    expect(sent[2].key).toBe(sent[0].key);
  });

  it('lo que no es una foto, o pesa demasiado, no se pone en cola', async () => {
    await mount();
    choose([photo('manual.pdf', 'application/pdf'), photo('enorme.jpg', 'image/jpeg', 26 * 1024 * 1024), photo('bien.heic', 'image/heic')]);

    expect(screen.getByRole('alert')).toHaveTextContent('manual.pdf');
    expect(screen.getByRole('alert')).toHaveTextContent('enorme.jpg');
    expect(screen.getByRole('alert')).toHaveTextContent('25 MB');
    expect(screen.getByLabelText('Nota de bien.heic')).toBeInTheDocument();
  });

  it('sin autoridad sobre la etapa elegida no deja subir, y lo dice', async () => {
    await mount((capability) => capability !== 'service.delivery.manage');
    fireEvent.change(screen.getByLabelText('Etapa de las fotos'), { target: { value: 'delivery' } });
    choose([photo('a.jpg')]);

    expect(screen.getByRole('button', { name: 'Subir 1 foto' })).toBeDisabled();
    expect(screen.getByText(/No tienes autoridad sobre la etapa «Entrega»/)).toBeInTheDocument();
  });
});

describe('la galería de la orden', () => {
  beforeEach(() => {
    gallery = [
      row(1, { caption: 'Golpe en la esquina', uploaded_by: 'ana' }),
      row(2, { caption: '' }),
      row(3, { stage: 'repair_before', caption: 'Pantalla rota' }),
      row(4, { stage: 'repair_after', caption: 'Pantalla nueva' }),
      row(5, { stage: 'quality', voided_at: '2026-09-04T10:00:00Z', void_reason: 'Duplicada' }),
    ];
  });

  it('cuenta las fotos en vigor por etapa', async () => {
    await mount();
    const summary = await screen.findByRole('list', { name: 'Evidencias por etapa' });
    expect(within(summary).getByText('Ingreso · 2 fotos')).toBeInTheDocument();
    expect(within(summary).getByText('Antes de reparar · 1 foto')).toBeInTheDocument();
    // La anulada no cuenta, pero sigue en la galería.
    expect(within(summary).queryByText(/Control de calidad/)).not.toBeInTheDocument();
    expect(screen.getByText('Anulada')).toBeInTheDocument();
  });

  it('cada foto dice su nota, cuándo y quién', async () => {
    await mount();
    const tile = (await screen.findByText('Golpe en la esquina')).closest('figure')!;
    expect(within(tile).getByText(/ana/)).toBeInTheDocument();
    expect(within(tile).getByRole('img')).toHaveAttribute('src', expect.stringContaining('/evidence/1/content/'));
    expect(within(tile).getByRole('img')).toHaveAttribute('alt', 'Golpe en la esquina');
  });

  it('abre la foto en grande y se recorre con el teclado', async () => {
    await mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Ver en grande: Golpe en la esquina' }));

    const viewer = screen.getByRole('dialog', { name: 'Evidencia' });
    expect(within(viewer).getByRole('img')).toHaveAttribute('src', expect.stringContaining('/evidence/1/content/'));
    expect(viewer).toHaveTextContent('Golpe en la esquina');
    expect(viewer).toHaveTextContent('1 de 5');

    fireEvent.keyDown(window, { key: 'ArrowRight' });
    expect(within(viewer).getByRole('img')).toHaveAttribute('src', expect.stringContaining('/evidence/2/content/'));
    fireEvent.click(within(viewer).getByRole('button', { name: 'Foto anterior' }));
    expect(viewer).toHaveTextContent('1 de 5');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Evidencia' })).not.toBeInTheDocument();
  });

  it('pone el antes y el después de la reparación uno junto al otro', async () => {
    await mount();
    const compare = await screen.findByRole('region', { name: 'Antes y después de la reparación' });
    const images = within(compare).getAllByRole('img');
    expect(images[0]).toHaveAttribute('src', expect.stringContaining('/evidence/3/content/'));
    expect(images[1]).toHaveAttribute('src', expect.stringContaining('/evidence/4/content/'));
  });

  it('la nota se corrige sin tocar la foto', async () => {
    await mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Editar nota: Golpe en la esquina' }));
    const field = screen.getByLabelText('Nota de la evidencia');
    fireEvent.change(field, { target: { value: 'Golpe en la esquina inferior' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar nota' })); });

    expect(sent).toEqual([expect.objectContaining({ method: 'PATCH', path: '/1/', json: { caption: 'Golpe en la esquina inferior' } })]);
    expect(await screen.findByText('Golpe en la esquina inferior')).toBeInTheDocument();
  });

  it('compartir con el cliente avisa de que también verá la nota', async () => {
    await mount();
    const tile = (await screen.findByText('Golpe en la esquina')).closest('figure')!;
    fireEvent.click(within(tile).getByRole('button', { name: 'Compartir con cliente' }));

    expect(sent).toEqual([]);
    expect(within(tile).getByText(/verá la foto y su nota/)).toBeInTheDocument();
    await act(async () => { fireEvent.click(within(tile).getByRole('button', { name: 'Sí, compartir' })); });
    expect(sent).toEqual([expect.objectContaining({ method: 'POST', path: '/1/publish-to-customer/' })]);
  });

  it('sin autoridad sobre su etapa, una foto no ofrece acciones', async () => {
    await mount((capability) => capability !== 'service.orders.create');
    const tile = (await screen.findByText('Golpe en la esquina')).closest('figure')!;
    expect(within(tile).queryByRole('button', { name: 'Compartir con cliente' })).not.toBeInTheDocument();
    expect(within(tile).queryByRole('button', { name: /Anular/ })).not.toBeInTheDocument();
    expect(within(tile).getByRole('button', { name: /Ver en grande/ })).toBeInTheDocument();
  });
});
