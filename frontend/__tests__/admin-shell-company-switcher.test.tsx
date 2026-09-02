import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { AdminShell, buildAccessContext } from '@/app/admin/components/AdminShell';
import { dashboard, user } from './support/fixtures';

/**
 * The switcher, and the bug that made it a decoration.
 *
 * `/admin/roles` and `/admin/users` shipped telling a platform master to choose
 * a company and passing NEITHER `dashboard` nor `onSelectCompany` to the shell.
 * `AdminShell` calls `onSelectCompany?.(id)`; on an undefined prop that is a
 * no-op, so the dropdown opened, closed, and did nothing. The screens were
 * unusable for exactly the caller their own copy addressed.
 *
 * A test that renders the shell both ways is the cheapest thing that would have
 * caught it, and it is the first reason this baseline exists.
 */

jest.mock('@/app/admin/lib/internal-api', () => ({
  fetchInternalDashboard: jest.fn().mockResolvedValue(null),
  NoInternalAccessError: class extends Error {},
}));

describe('AdminShell company switcher', () => {
  it('calls back with the company the person picked', async () => {
    const onSelectCompany = jest.fn();
    render(
      <AdminShell
        user={user()}
        dashboard={dashboard(['roles.manage'])}
        onSelectCompany={onSelectCompany}
      >
        <p>contenido</p>
      </AdminShell>,
    );

    await userEvent.click(screen.getByRole('button', { expanded: false }));
    // The `role="option"` is the <li>; the handler lives on the button inside
    // it. Clicking the wrapper looks right and does nothing — the same shape of
    // mistake as the bug this test exists for.
    const option = screen.getByRole('option', { name: /Otra/ });
    await userEvent.click(within(option).getByRole('button'));

    expect(onSelectCompany).toHaveBeenCalledWith(8);
  });

  it('still renders the page body while a company is being chosen', () => {
    render(
      <AdminShell
        user={user()}
        dashboard={dashboard(['roles.manage'], { company: null })}
        onSelectCompany={jest.fn()}
      >
        <p>contenido</p>
      </AdminShell>,
    );
    expect(screen.getByText('contenido')).toBeInTheDocument();
  });
});

describe('buildAccessContext', () => {
  it('takes the capabilities from the SERVER, never from the session role', () => {
    const ctx = buildAccessContext(
      user({ role: 'admin' }),
      dashboard(['service.orders.view']),
    );
    expect(ctx.capabilities).toEqual(['service.orders.view']);
    expect(ctx.isPlatformAdmin).toBe(false);
  });

  it('falls back to the session role for navigation ONLY, with no capabilities', () => {
    // The documented legacy path: an operator whose company has not adopted
    // memberships keeps their sidebar and gains no authority from it.
    const ctx = buildAccessContext(user({ role: 'technician' }), null);
    expect(ctx.capabilities).toEqual([]);
    expect(ctx.legacyRole).toBe('technician');
    expect(ctx.hasCompanyContext).toBe(false);
  });
});
