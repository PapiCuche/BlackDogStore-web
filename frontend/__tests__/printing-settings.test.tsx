import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

/**
 * Impresoras del local: darlas de alta, conectar el agente y ver la cola.
 *
 *  · una impresora es de UNA sucursal y vive en su red: se da su dirección;
 *  · el token del agente se enseña UNA vez, al crearlo, y nunca en la lista;
 *  · un trabajo fallido se puede reenviar.
 */

const mockApi = {
  fetchPrinters: jest.fn(),
  createPrinter: jest.fn(),
  updatePrinter: jest.fn(),
  deactivatePrinter: jest.fn(),
  fetchPrintAgents: jest.fn(),
  createPrintAgent: jest.fn(),
  revokePrintAgent: jest.fn(),
  fetchPrintJobs: jest.fn(),
  retryPrintJob: jest.fn(),
};
jest.mock('@/app/admin/lib/printing-api', () => ({
  ...jest.requireActual('@/app/admin/lib/printing-api'),
  fetchPrinters: (...a: unknown[]) => mockApi.fetchPrinters(...a),
  createPrinter: (...a: unknown[]) => mockApi.createPrinter(...a),
  updatePrinter: (...a: unknown[]) => mockApi.updatePrinter(...a),
  deactivatePrinter: (...a: unknown[]) => mockApi.deactivatePrinter(...a),
  fetchPrintAgents: (...a: unknown[]) => mockApi.fetchPrintAgents(...a),
  createPrintAgent: (...a: unknown[]) => mockApi.createPrintAgent(...a),
  revokePrintAgent: (...a: unknown[]) => mockApi.revokePrintAgent(...a),
  fetchPrintJobs: (...a: unknown[]) => mockApi.fetchPrintJobs(...a),
  retryPrintJob: (...a: unknown[]) => mockApi.retryPrintJob(...a),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const { PrintingSettings } = require('@/app/admin/settings/printing/PrintingSettings');
const { PrintingFieldError } = require('@/app/admin/lib/printing-api');
/* eslint-enable @typescript-eslint/no-require-imports */

const BRANCHES = [{ id: 1, name: 'Tienda principal' }, { id: 2, name: 'Sucursal Sur' }];
const PRINTER = {
  id: 5, branch: 1, branch_name: 'Tienda principal', name: 'Caja', host: '192.168.1.50',
  port: 9100, paper_width_mm: 80, encoding: 'cp858', auto_print: true, is_active: true,
};
const AGENT = {
  id: 3, branch: 1, branch_name: 'Tienda principal', name: 'Mostrador',
  token_hint: 'bdpa_abcde', is_active: true, last_seen_at: null,
};
const FAILED_JOB = {
  id: 9, kind: 'fiscal_ticket', reason: 'auto', status: 'failed', order: 41, branch: 1,
  printer: 5, printer_name: 'Caja', attempts: 5, last_error: 'Connection refused',
  created_at: '2026-10-04T10:00:00Z', printed_at: null,
};

beforeEach(() => {
  Object.values(mockApi).forEach((fn) => fn.mockReset());
  mockApi.fetchPrinters.mockResolvedValue([PRINTER]);
  mockApi.fetchPrintAgents.mockResolvedValue([AGENT]);
  mockApi.fetchPrintJobs.mockResolvedValue([FAILED_JOB]);
});

function mount() {
  return render(<PrintingSettings companyId={7} branches={BRANCHES} canManage />);
}

it('lista las impresoras con su sucursal y su dirección en la red del local', async () => {
  mount();

  const row = (await screen.findByText('Caja')).closest('li') as HTMLElement;
  expect(within(row).getByText(/Tienda principal/)).toBeInTheDocument();
  expect(within(row).getByText(/192\.168\.1\.50:9100/)).toBeInTheDocument();
  expect(within(row).getByText(/Automática/)).toBeInTheDocument();
  expect(mockApi.fetchPrinters).toHaveBeenCalledWith(7);
});

it('da de alta una impresora en la sucursal elegida', async () => {
  mockApi.createPrinter.mockResolvedValue({ ...PRINTER, id: 6, name: 'Sur', branch: 2, host: '10.0.0.9' });
  mount();
  await screen.findByText('Caja');

  await userEvent.selectOptions(screen.getByLabelText('Sucursal de la impresora'), '2');
  await userEvent.type(screen.getByLabelText('Nombre de la impresora'), 'Sur');
  await userEvent.type(screen.getByLabelText('Dirección en la red del local'), '10.0.0.9');
  await userEvent.click(screen.getByRole('button', { name: 'Añadir impresora' }));

  await waitFor(() => expect(mockApi.createPrinter).toHaveBeenCalledWith(7, {
    branch: 2, name: 'Sur', host: '10.0.0.9', auto_print: true,
  }));
  expect(await screen.findByText('Sur')).toBeInTheDocument();
});

it('enseña bajo el campo lo que el servidor no aceptó', async () => {
  mockApi.createPrinter.mockRejectedValue(new PrintingFieldError('Datos inválidos.', {
    host: ['Escribe la dirección de la impresora en la red del local (por ejemplo 192.168.1.50).'],
  }));
  mount();
  await screen.findByText('Caja');

  await userEvent.type(screen.getByLabelText('Nombre de la impresora'), 'Fuera');
  await userEvent.type(screen.getByLabelText('Dirección en la red del local'), '8.8.8.8');
  await userEvent.click(screen.getByRole('button', { name: 'Añadir impresora' }));

  expect(await screen.findByText(/en la red del local \(por ejemplo/)).toBeInTheDocument();
});

it('el token del agente se enseña una vez, al crearlo, y no en la lista', async () => {
  mockApi.createPrintAgent.mockResolvedValue({
    ...AGENT, id: 4, name: 'Trastienda', token: 'bdpa_secreto-de-una-sola-vez',
  });
  mount();
  await screen.findByText('Mostrador');
  expect(screen.queryByText(/bdpa_secreto/)).toBeNull();

  await userEvent.type(screen.getByLabelText('Nombre del agente'), 'Trastienda');
  await userEvent.click(screen.getByRole('button', { name: 'Crear agente' }));

  const once = await screen.findByRole('status');
  expect(once).toHaveTextContent('bdpa_secreto-de-una-sola-vez');
  expect(once).toHaveTextContent('No se volverá a mostrar');
  // En la lista, sólo la pista.
  const row = screen.getByText('Trastienda').closest('li') as HTMLElement;
  expect(within(row).queryByText(/secreto/)).toBeNull();
});

it('un trabajo fallido dice por qué y se puede reenviar', async () => {
  mockApi.retryPrintJob.mockResolvedValue({ ...FAILED_JOB, status: 'pending', attempts: 0, last_error: '' });
  mount();

  const row = (await screen.findByText(/Pedido #41/)).closest('li') as HTMLElement;
  expect(within(row).getByText('Falló')).toBeInTheDocument();
  expect(within(row).getByText('Connection refused')).toBeInTheDocument();

  await userEvent.click(within(row).getByRole('button', { name: 'Reenviar' }));

  await waitFor(() => expect(mockApi.retryPrintJob).toHaveBeenCalledWith(7, 9));
  expect(await within(row).findByText('En cola')).toBeInTheDocument();
});

// -- WIN-003C: impresoras del equipo del agente (USB) ------------------------

it('da de alta una impresora USB del equipo del agente sin pedir ninguna dirección', async () => {
  mockApi.createPrinter.mockResolvedValue({
    ...PRINTER, id: 12, name: 'USB caja', host: '', connection: 'local_queue', agent_online: false,
  });
  mount();
  await screen.findByText('Caja');

  await userEvent.selectOptions(screen.getByLabelText('Conexión'), 'local_queue');
  expect(screen.queryByLabelText('Dirección en la red del local')).toBeNull();
  await userEvent.type(screen.getByLabelText('Nombre de la impresora'), 'USB caja');
  await userEvent.click(screen.getByRole('button', { name: 'Añadir impresora' }));

  await waitFor(() => expect(mockApi.createPrinter).toHaveBeenCalledWith(7, {
    branch: 1, name: 'USB caja', auto_print: true, connection: 'local_queue',
  }));
  const row = (await screen.findByText('USB caja')).closest('li') as HTMLElement;
  expect(within(row).getByText(/Equipo del agente \(USB\) · id 12/)).toBeInTheDocument();
  expect(within(row).queryByText(/:9100/)).toBeNull();
  expect(within(row).getByText(/Ningún agente que pueda imprimir en ella está conectado/)).toBeInTheDocument();
});

it('dice qué agente está en línea ahora y si atiende impresoras USB', async () => {
  mockApi.fetchPrintAgents.mockResolvedValue([
    { ...AGENT, online: true, last_seen_at: '2026-11-16T10:00:00Z', connections: ['local_queue'] },
    { ...AGENT, id: 4, name: 'Viejo', online: false, last_seen_at: '2026-11-01T10:00:00Z' },
  ]);
  mount();

  const online = (await screen.findByText('Mostrador')).closest('li') as HTMLElement;
  expect(within(online).getByText(/En línea · Equipo del agente \(USB\)/)).toBeInTheDocument();
  const old = screen.getByText('Viejo').closest('li') as HTMLElement;
  expect(within(old).getByText(/Desconectado/)).toBeInTheDocument();
});

it('un ticket incierto sólo se reenvía tras confirmar que no salió', async () => {
  const uncertain = {
    ...FAILED_JOB, uncertain: true, uid: 'ab12', last_error: 'INCIERTO: el agente se interrumpió…',
  };
  mockApi.fetchPrintJobs.mockResolvedValue([uncertain]);
  mockApi.retryPrintJob.mockResolvedValue({ ...uncertain, status: 'pending', uncertain: false, last_error: '' });
  mount();

  const row = (await screen.findByText(/Pedido #41/)).closest('li') as HTMLElement;
  expect(within(row).getByText('Incierto: revisa el mostrador')).toBeInTheDocument();
  expect(within(row).queryByRole('button', { name: 'Reenviar' })).toBeNull();

  await userEvent.click(within(row).getByRole('button', { name: 'No salió: reenviar…' }));
  expect(mockApi.retryPrintJob).not.toHaveBeenCalled();
  await userEvent.click(within(row).getByRole('button', { name: 'Confirmo que no salió: reenviar' }));

  await waitFor(() => expect(mockApi.retryPrintJob).toHaveBeenCalledWith(7, 9, true, 'ab12'));
  expect(await within(row).findByText('En cola')).toBeInTheDocument();
});

it('la confirmación lleva el foco y lo devuelve al cancelar; un doble clic no reenvía dos veces', async () => {
  const uncertain = { ...FAILED_JOB, uncertain: true, last_error: 'INCIERTO: …' };
  mockApi.fetchPrintJobs.mockResolvedValue([uncertain]);
  let release: (v: unknown) => void = () => {};
  mockApi.retryPrintJob.mockImplementation(() => new Promise((r) => { release = r; }));
  mount();

  const row = (await screen.findByText(/Pedido #41/)).closest('li') as HTMLElement;
  const trigger = within(row).getByRole('button', { name: 'No salió: reenviar…' });
  await userEvent.click(trigger);
  const confirm = within(row).getByRole('button', { name: 'Confirmo que no salió: reenviar' });
  expect(confirm).toHaveFocus();

  await userEvent.click(within(row).getByRole('button', { name: 'Cancelar' }));
  expect(within(row).getByRole('button', { name: 'No salió: reenviar…' })).toHaveFocus();

  await userEvent.click(within(row).getByRole('button', { name: 'No salió: reenviar…' }));
  const again = within(row).getByRole('button', { name: 'Confirmo que no salió: reenviar' });
  await userEvent.click(again);
  await userEvent.click(again);
  expect(mockApi.retryPrintJob).toHaveBeenCalledTimes(1);
  expect(again).toBeDisabled();
  release({ ...uncertain, status: 'pending', uncertain: false, last_error: '' });
  expect(await within(row).findByText('En cola')).toBeInTheDocument();
});

it('si la confirmación llega tarde (409), se cierra la pregunta y se relee la cola', async () => {
  const uncertain = { ...FAILED_JOB, uncertain: true, uid: 'viejo', last_error: 'INCIERTO: …' };
  mockApi.fetchPrintJobs.mockResolvedValueOnce([uncertain])
    .mockResolvedValueOnce([{ ...uncertain, status: 'pending', uncertain: false, uid: 'nuevo' }]);
  mockApi.retryPrintJob.mockRejectedValue(new Error('Este ticket cambió desde que lo viste.'));
  mount();

  const row = (await screen.findByText(/Pedido #41/)).closest('li') as HTMLElement;
  await userEvent.click(within(row).getByRole('button', { name: 'No salió: reenviar…' }));
  await userEvent.click(within(row).getByRole('button', { name: 'Confirmo que no salió: reenviar' }));

  expect(await screen.findByText('Este ticket cambió desde que lo viste.')).toBeInTheDocument();
  expect(await within(row).findByText('En cola')).toBeInTheDocument();
  expect(within(row).queryByRole('button', { name: 'Confirmo que no salió: reenviar' })).toBeNull();
  expect(mockApi.retryPrintJob).toHaveBeenCalledWith(7, 9, true, 'viejo');
  expect(mockApi.fetchPrintJobs).toHaveBeenCalledTimes(2);
});

it('una impresora de un servidor anterior (sin conexión) se trata como de red', async () => {
  mount();

  const row = (await screen.findByText('Caja')).closest('li') as HTMLElement;
  expect(within(row).getByText(/192\.168\.1\.50:9100/)).toBeInTheDocument();
  expect(within(row).queryByText(/Ningún agente/)).toBeNull();
});

it('quien no puede configurar ve la cola pero no los formularios', async () => {
  render(<PrintingSettings companyId={7} branches={BRANCHES} canManage={false} />);

  expect(await screen.findByText(/Pedido #41/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Añadir impresora' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Crear agente' })).toBeNull();
  expect(mockApi.fetchPrintAgents).not.toHaveBeenCalled();
});

// -- THERMAL-512: el ancho que imprime cada impresora -----------------------

it('THERMAL-512: cada impresora dice su rollo y sus columnas', async () => {
  mockApi.fetchPrinters.mockResolvedValue([
    { ...PRINTER, print_width_dots: 512, printable_dots: 512, columns: 42 },
    { ...PRINTER, id: 6, name: 'Antigua' },                       // un servidor anterior a THERMAL-512
  ]);
  mount();

  const row = (await screen.findByText('Caja')).closest('li') as HTMLElement;
  expect(within(row).getByText(/80 mm, 42 columnas/)).toBeInTheDocument();
  const old = screen.getByText('Antigua').closest('li') as HTMLElement;
  expect(within(old).getByText(/80 mm, 48 columnas/)).toBeInTheDocument();
});

it('THERMAL-512: se da de alta una impresora que imprime 512 puntos (42 columnas)', async () => {
  mockApi.createPrinter.mockResolvedValue({
    ...PRINTER, id: 6, name: 'Angosta', host: '10.0.0.9', print_width_dots: 512, printable_dots: 512, columns: 42,
  });
  mount();
  await screen.findByText('Caja');

  await userEvent.type(screen.getByLabelText('Nombre de la impresora'), 'Angosta');
  await userEvent.type(screen.getByLabelText('Dirección en la red del local'), '10.0.0.9');
  await userEvent.selectOptions(screen.getByLabelText('Ancho que imprime'), '80-512');
  await userEvent.click(screen.getByRole('button', { name: 'Añadir impresora' }));

  await waitFor(() => expect(mockApi.createPrinter).toHaveBeenCalledWith(7, {
    branch: 1, name: 'Angosta', host: '10.0.0.9', auto_print: true, paper_width_mm: 80, print_width_dots: 512,
  }));
});

it('THERMAL-512: el ancho de una impresora ya dada de alta se cambia en su fila', async () => {
  mockApi.updatePrinter.mockResolvedValue({
    ...PRINTER, paper_width_mm: 58, print_width_dots: 0, printable_dots: 384, columns: 32,
  });
  mount();

  const row = (await screen.findByText('Caja')).closest('li') as HTMLElement;
  const select = within(row).getByLabelText('Ancho que imprime Caja');
  expect(select).toHaveValue('80-576');
  await userEvent.selectOptions(select, '58-384');

  await waitFor(() => expect(mockApi.updatePrinter).toHaveBeenCalledWith(7, 5, {
    paper_width_mm: 58, print_width_dots: 0,
  }));
  expect(await within(row).findByText(/58 mm, 32 columnas/)).toBeInTheDocument();
});

it('THERMAL-512: quien sólo opera la caja ve el ancho pero no lo cambia', async () => {
  render(<PrintingSettings companyId={7} branches={BRANCHES} canManage={false} />);

  const row = (await screen.findByText('Caja')).closest('li') as HTMLElement;
  expect(within(row).getByText(/80 mm, 48 columnas/)).toBeInTheDocument();
  expect(within(row).queryByRole('combobox')).toBeNull();
});

it('THERMAL-512: en un teléfono de 320 px el ancho de la fila cabe: etiquetas cortas y sin ancho fijo', async () => {
  mount();

  const row = (await screen.findByText('Caja')).closest('li') as HTMLElement;
  const select = within(row).getByLabelText('Ancho que imprime Caja');
  expect(select).toHaveClass('w-full', 'min-w-0', 'max-w-full', 'sm:w-auto');
  expect(select.parentElement).toHaveClass('w-full', 'min-w-0', 'max-w-full', 'sm:w-auto');
  // La más larga, a unos 7 px por carácter y con la flecha, en los ~246 px que deja una pantalla de 320.
  for (const option of within(select).getAllByRole('option')) {
    expect((option.textContent ?? '').length).toBeLessThanOrEqual(27);
  }
});

// -- LABEL-04: impresoras de etiquetas ------------------------------------------

it('da de alta una impresora de etiquetas con su rollo y sin ancho de ticket', async () => {
  mockApi.createPrinter.mockResolvedValue({
    ...PRINTER, id: 15, name: 'Stickers', host: '', connection: 'local_queue', role: 'label', label_roll: '30x20',
  });
  mount();
  await screen.findByText('Caja');

  await userEvent.selectOptions(screen.getByLabelText('Qué imprime'), 'label');
  expect(screen.queryByLabelText('Ancho que imprime')).toBeNull();
  await userEvent.selectOptions(screen.getByLabelText('Conexión'), 'local_queue');
  await userEvent.type(screen.getByLabelText('Nombre de la impresora'), 'Stickers');
  expect(screen.getByLabelText(/Imprimir solas las etiquetas de cada ingreso/)).toBeChecked();
  await userEvent.click(screen.getByRole('button', { name: 'Añadir impresora' }));

  await waitFor(() => expect(mockApi.createPrinter).toHaveBeenCalledWith(7, {
    branch: 1, name: 'Stickers', auto_print: true, connection: 'local_queue', role: 'label', label_roll: '30x20',
  }));
  const row = (await screen.findByText('Stickers')).closest('li') as HTMLElement;
  expect(within(row).getByText(/Etiquetas \(stickers\), rollo 30x20 mm/)).toBeInTheDocument();
});

it('cambia el rollo cargado de una impresora de etiquetas', async () => {
  mockApi.fetchPrinters.mockResolvedValue([
    { ...PRINTER, id: 15, name: 'Stickers', host: '', connection: 'local_queue', role: 'label', label_roll: '30x20' },
  ]);
  mockApi.updatePrinter.mockResolvedValue({ label_roll: '50x25' });
  mount();
  const row = (await screen.findByText('Stickers')).closest('li') as HTMLElement;

  await userEvent.selectOptions(within(row).getByLabelText('Rollo cargado en Stickers'), '50x25');

  await waitFor(() => expect(mockApi.updatePrinter).toHaveBeenCalledWith(7, 15, { label_roll: '50x25' }));
  expect(await within(row).findByText(/rollo 50x25 mm/)).toBeInTheDocument();
});

it('un lote de etiquetas en la cola dice su lote y su rollo, no un pedido', async () => {
  mockApi.fetchPrintJobs.mockResolvedValue([
    { ...FAILED_JOB, id: 30, kind: 'label_batch', order: null, label_batch: 8, label_size: '30x20', status: 'printed' },
  ]);
  mount();

  expect(await screen.findByText(/Lote de etiquetas #8 \(30x20 mm\)/)).toBeInTheDocument();
  expect(screen.queryByText(/Pedido #null/)).toBeNull();
});
