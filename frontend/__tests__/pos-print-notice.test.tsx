import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { PosPrintNotice } from '@/app/admin/sales/pos/PosPrintNotice';

/**
 * Lo que la caja dice sobre el ticket después de cobrar.
 *
 * Si el local tiene una impresora propia, el ticket ya va de camino: quien cobra
 * desde un teléfono no tiene que abrir ningún diálogo de impresión. Si no la
 * tiene, no se dice nada y la caja ofrece imprimir como siempre.
 */

it('sin impresora del local no dice nada', () => {
  const { container } = render(<PosPrintNotice job={null} />);
  expect(container).toBeEmptyDOMElement();
});

it('dice a qué impresora del local se envió el ticket', () => {
  render(<PosPrintNotice job={{ id: 4, status: 'pending', printer: 'Caja' }} />);

  const notice = screen.getByRole('status');
  expect(notice).toHaveTextContent('Ticket enviado a la impresora «Caja»');
  expect(notice).toHaveTextContent('No hace falta imprimir desde este equipo');
});

it('sin agente conectado no dice que se envió: dice que espera y que se puede imprimir aquí', () => {
  // «En cola» no es «enviado». Si nadie en el local está recogiendo tickets,
  // decir «no hace falta imprimir» dejaría al cliente sin su ticket.
  render(<PosPrintNotice job={{ id: 4, status: 'pending', printer: 'Caja', agent_online: false }} />);

  const notice = screen.getByRole('status');
  expect(notice).toHaveTextContent('El ticket está en cola para «Caja»');
  expect(notice).toHaveTextContent('el agente de impresión de este local no está conectado');
  expect(notice).not.toHaveTextContent('No hace falta imprimir');
});

it('un ticket ya impreso se dice como impreso', () => {
  render(<PosPrintNotice job={{ id: 4, status: 'printed', printer: 'Caja' }} />);
  expect(screen.getByRole('status')).toHaveTextContent('Ticket impreso en «Caja»');
});

it('si la impresora falló lo avisa y deja reenviarlo', async () => {
  const retry = jest.fn();
  render(<PosPrintNotice job={{ id: 4, status: 'failed', printer: 'Caja' }} onRetry={retry} />);

  expect(screen.getByRole('alert')).toHaveTextContent('La impresora «Caja» no pudo imprimir el ticket');
  await userEvent.click(screen.getByRole('button', { name: 'Reenviar a la impresora' }));
  expect(retry).toHaveBeenCalledWith(4);
});
