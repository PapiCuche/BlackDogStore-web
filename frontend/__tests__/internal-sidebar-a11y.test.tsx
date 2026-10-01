import { fireEvent, render, screen } from '@testing-library/react';

import { MobileSidebar } from '@/app/admin/components/InternalSidebar';

const access = {
  capabilities: [],
  legacyRole: 'admin',
  isPlatformAdmin: false,
  hasCompanyContext: true,
};

describe('MobileSidebar accessibility', () => {
  it('moves focus into the drawer, locks background scroll and closes with Escape', () => {
    const onClose = jest.fn();
    const previousOverflow = document.body.style.overflow;

    const { unmount } = render(
      <MobileSidebar
        access={access}
        companyName="Empresa demo"
        open
        onClose={onClose}
      />,
    );

    const dialog = screen.getByRole('dialog', {
      name: 'Navegación del control interno',
    });
    const panel = dialog.querySelector('[tabindex="-1"]') as HTMLElement | null;

    expect(panel).not.toBeNull();
    expect(panel).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);

    unmount();
    expect(document.body.style.overflow).toBe(previousOverflow);
  });
});
