import { render, screen } from '@testing-library/react';

import { AppChrome } from '@/app/components/AppChrome';

let pathname = '/';

jest.mock('next/navigation', () => ({
  usePathname: () => pathname,
}));

jest.mock('@/app/components/Header', () => ({
  Header: () => <div data-testid="storefront-header">Header</div>,
}));

jest.mock('@/app/components/Footer', () => ({
  Footer: () => <div data-testid="storefront-footer">Footer</div>,
}));

describe('AppChrome', () => {
  it('renders storefront chrome on public routes', () => {
    pathname = '/product';
    render(
      <AppChrome whatsappLink="https://example.test/contact">
        <p>contenido</p>
      </AppChrome>,
    );

    expect(screen.getByTestId('storefront-header')).toBeInTheDocument();
    expect(screen.getByTestId('storefront-footer')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Contactar por WhatsApp' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Saltar al contenido' })).toHaveAttribute('href', '#main-content');
    expect(screen.getByText('contenido').parentElement).not.toHaveClass('internal-ui-fonts');
  });

  it('removes storefront chrome from internal routes', () => {
    pathname = '/admin/inventory';
    render(
      <AppChrome whatsappLink="https://example.test/contact">
        <p>contenido interno</p>
      </AppChrome>,
    );

    expect(screen.queryByTestId('storefront-header')).not.toBeInTheDocument();
    expect(screen.queryByTestId('storefront-footer')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Contactar por WhatsApp' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Saltar al contenido' })).toHaveAttribute('href', '#admin-main-content');
    expect(screen.getByText('contenido interno').parentElement).toHaveClass('internal-ui-fonts');
  });
});
