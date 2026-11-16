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
    ...FAILED_JOB, uncertain: true, last_error: 'INCIERTO: el agente se interrumpió…',
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

  await waitFor(() => expect(mockApi.retryPrintJob).toHaveBeenCalledWith(7, 9, true));
  expect(await within(row).findByText('En cola')).toBeInTheDocument();
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
