import { fireEvent, render, screen } from '@testing-library/react';
import { PrivacyNotice } from '@/app/components/PrivacyNotice';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';
import { onOpenConsentPreferences } from '@/app/lib/consent';

test('privacy uses the resolved company and offers a way to withdraw consent', () => {
  const config = { ...NEUTRAL_CONFIG, company: { ...NEUTRAL_CONFIG.company, name: 'Otra tienda', legal_name: 'Otra empresa' }, contact: { ...NEUTRAL_CONFIG.contact, email: 'privacidad@example.org' } };
  render(<StorefrontProvider config={config}><PrivacyNotice /></StorefrontProvider>);
  expect(screen.getByText('Otra empresa')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'privacidad@example.org' })).toHaveAttribute('href', 'mailto:privacidad@example.org');
  expect(screen.getByText(/identificadores de navegador y sesión/)).toBeInTheDocument();
  expect(screen.getByText(/servidor cuando se confirma el pago/)).toBeInTheDocument();
  const opened = jest.fn(); const stop = onOpenConsentPreferences(opened);
  fireEvent.click(screen.getByRole('button', { name: 'Cambiar preferencias de cookies' }));
  expect(opened).toHaveBeenCalledTimes(1); stop();
  expect(document.body.textContent).not.toContain('CMAU');
});

test('missing tenant details are disclosed instead of inventing an identity', () => {
  render(<PrivacyNotice />);
  expect(screen.getByText(/no ha publicado su identidad legal/)).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /@/ })).toBeNull();
});
