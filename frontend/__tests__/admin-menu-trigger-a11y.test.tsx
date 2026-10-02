import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { AdminShell } from '@/app/admin/components/AdminShell';
import { dashboard, user } from './support/fixtures';

/**
 * El botón que abre el menú del panel en un teléfono dice qué abre y si está
 * abierto.
 *
 * El cajón ya era un diálogo con el foco atrapado, pero el botón que lo abre no
 * decía nada: un lector de pantalla anunciaba «Abrir menú de módulos, botón» y
 * nada cambiaba al pulsarlo. `aria-expanded` y `aria-controls` son lo que une
 * el botón con lo que aparece.
 */

jest.mock('@/app/admin/lib/internal-api', () => ({
  fetchInternalDashboard: jest.fn().mockResolvedValue(null),
  NoInternalAccessError: class extends Error {},
}));

function renderShell() {
  render(
    <AdminShell user={user()} dashboard={dashboard(['roles.manage'])} onSelectCompany={jest.fn()}>
      <p>contenido</p>
    </AdminShell>,
  );
  return screen.getByRole('button', { name: 'Abrir menú de módulos' });
}

describe('botón del menú móvil del panel', () => {
  it('cerrado, anuncia que abre un diálogo y que está plegado', () => {
    const trigger = renderShell();

    expect(trigger).toHaveAttribute('aria-haspopup', 'dialog');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).not.toHaveAttribute('aria-controls');
  });

  it('abierto, apunta al diálogo que abrió', async () => {
    const trigger = renderShell();
    await userEvent.click(trigger);

    const dialog = screen.getByRole('dialog', { name: 'Navegación del control interno' });
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(dialog.id).not.toBe('');
    expect(trigger).toHaveAttribute('aria-controls', dialog.id);
  });

  it('al cerrar con Escape vuelve a decir que está plegado', async () => {
    const trigger = renderShell();
    await userEvent.click(trigger);
    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('dialog', { name: 'Navegación del control interno' })).toBeNull();
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).not.toHaveAttribute('aria-controls');
  });
});
