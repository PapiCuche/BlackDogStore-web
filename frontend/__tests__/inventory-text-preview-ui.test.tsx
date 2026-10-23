import { fireEvent, render, screen, within } from '@testing-library/react';

import { InventoryTextPreview } from '@/app/admin/components/InventoryTextPreview';

/** INV-TEXT-02 · la vista previa dice qué variante leyó y qué líneas no reconoció, con su motivo. */

function analyze(text: string, canSeeCosts = true) {
  render(<InventoryTextPreview canSeeCosts={canSeeCosts} />);
  fireEvent.change(screen.getByLabelText('Línea declarada'), { target: { value: 'new' } });
  fireEvent.change(screen.getByLabelText('Texto para reconocer'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: /Analizar/ }));
}

it('muestra tipo, variante y costo de cada candidato, corregibles', () => {
  analyze('IPH 17 pro chip 256gb LLA 4370 blanco\nAirPods Pro 2 850 blanco');
  expect(screen.getByLabelText('Línea del registro 1')).toHaveValue('new');
  expect(screen.getByLabelText('Modelo del registro 1')).toHaveValue('iPhone 17 Pro');
  expect(screen.getByLabelText('Capacidad del registro 1')).toHaveValue('256');
  expect(screen.getByLabelText('Color del registro 1')).toHaveValue('blanco');
  expect(screen.getAllByRole('row')[1]).toHaveTextContent('Chip · LL/A');
  expect(screen.getByLabelText('Costo del registro 1')).toHaveValue('4370');
  expect(screen.getByLabelText('Línea del registro 2')).toHaveValue('accessory');
});

it('las líneas no reconocidas se listan con su motivo', () => {
  analyze('Buenos días 👋\nNokia 3310 150\nIPH 15 128 2300 negro');
  const list = screen.getByRole('list', { name: 'Líneas no reconocidas' });
  expect(within(list).getByText(/Nokia 3310 150/).closest('li')).toHaveTextContent('Modelo no reconocido');
  expect(within(list).getByText(/Buenos días/).closest('li')).toHaveTextContent('Texto sin producto');
});

it('sin permiso de costos, el costo no aparece', () => {
  analyze('IPH 15 128 2300 negro', false);
  expect(screen.queryByText('2300')).toBeNull();
});

it('revisión pre-fusión: una línea no reconocida con un IMEI se muestra enmascarada', () => {
  analyze('IPH 15 128 2300 negro\n\nIMEI: 356789012345678\n356789012345678');
  const list = screen.getByRole('list', { name: 'Líneas no reconocidas' });
  expect(list).not.toHaveTextContent('356789012345678');
  expect(list).not.toHaveTextContent('35678901');
});

it('revisión pre-fusión: sin permiso de costos, los avisos no muestran importes', () => {
  analyze('IPH 15 128 2300 2500 negro\nIPH 14 128 80 azul', false);
  // The notices (the person's own pasted text stays available, masked, under «Ver texto original»).
  const notices = screen.getAllByText(/Costos ambiguos|demasiado bajo/).map(node => node.textContent ?? '');
  expect(notices).toEqual(['Costos ambiguos: indicar cuál es el costo.',
    'Importe demasiado bajo para un equipo: revisar si es el costo.']);
});
