import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  ALL_BRANCHES,
  BranchSelector,
} from '@/app/admin/components/BranchSelector';
import type { BranchAccessInfo } from '@/app/lib/inventory';

function access(overrides: Partial<BranchAccessInfo> = {}): BranchAccessInfo {
  return {
    company: { id: 1, name: 'Empresa Demo' },
    results: [
      {
        id: 10,
        company: 1,
        company_name: 'Empresa Demo',
        name: 'Centro',
        address: '',
        phone: '',
        email: '',
        is_active: true,
        created_at: '',
        updated_at: '',
      },
      {
        id: 20,
        company: 1,
        company_name: 'Empresa Demo',
        name: 'Norte',
        address: '',
        phone: '',
        email: '',
        is_active: true,
        created_at: '',
        updated_at: '',
      },
    ],
    count: 2,
    default_branch: { id: 10, name: 'Centro' },
    access_mode: 'all',
    allows_aggregate: true,
    ...overrides,
  };
}

describe('BranchSelector', () => {
  it('renders one reachable branch as context instead of a fake choice', () => {
    const one = access({
      results: [access().results[0]],
      count: 1,
      allows_aggregate: false,
    });

    render(
      <BranchSelector
        access={one}
        value={10}
        allowAll={false}
        onChange={jest.fn()}
      />,
    );

    expect(screen.getByText('Centro')).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('offers the caller-visible aggregate and returns the selected branch', async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();

    render(
      <BranchSelector
        access={access()}
        value={ALL_BRANCHES}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole('button', { name: /todas las sucursales/i }));

    const listbox = screen.getByRole('listbox', { name: /seleccionar sucursal/i });
    expect(listbox).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Centro' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Norte' })).toBeInTheDocument();

    await user.click(screen.getByRole('option', { name: 'Norte' }));

    expect(onChange).toHaveBeenCalledWith(20);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('closes the options with Escape without changing branch', async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();

    render(
      <BranchSelector
        access={access()}
        value={10}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole('button', { name: /centro/i }));
    expect(screen.getByRole('listbox')).toBeInTheDocument();

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
