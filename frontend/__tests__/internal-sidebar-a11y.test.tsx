import { fireEvent, render, screen, within } from '@testing-library/react';

import { MobileSidebar } from '@/app/admin/components/InternalSidebar';

const access = {
  capabilities: [],
  legacyRole: 'admin',
  isPlatformAdmin: false,
  hasCompanyContext: true,
};

describe('MobileSidebar accessibility', () => {
  it('moves focus into the drawer, traps it, restores it and closes with Escape', () => {
    const onClose = jest.fn();
    const previousOverflow = document.body.style.overflow;

    const { rerender, unmount } = render(
      <>
        <button type="button">Abrir panel</button>
        <MobileSidebar
          access={access}
          companyName="Empresa demo"
          open={false}
          onClose={onClose}
        />
      </>,
    );

    const trigger = screen.getByRole('button', { name: 'Abrir panel' });
    trigger.focus();

    rerender(
      <>
        <button type="button">Abrir panel</button>
        <MobileSidebar
          access={access}
          companyName="Empresa demo"
          open
          onClose={onClose}
        />
      </>,
    );

    const dialog = screen.getByRole('dialog', {
      name: 'Navegación del control interno',
    });
    const panel = dialog.querySelector('div[tabindex="-1"]') as HTMLElement | null;

    expect(panel).not.toBeNull();
    expect(panel).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(screen.getByRole('link', { name: /Volver a la tienda/ })).toHaveFocus();

    fireEvent.keyDown(window, { key: 'Tab' });
    expect(within(panel!).getByRole('button', { name: 'Cerrar menú' })).toHaveFocus();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);

    rerender(
      <>
        <button type="button">Abrir panel</button>
        <MobileSidebar
          access={access}
          companyName="Empresa demo"
          open={false}
          onClose={onClose}
        />
      </>,
    );

    expect(document.body.style.overflow).toBe(previousOverflow);
    expect(screen.getByRole('button', { name: 'Abrir panel' })).toHaveFocus();

    unmount();
  });
});
