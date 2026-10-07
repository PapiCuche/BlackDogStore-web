import { act, fireEvent, render, screen } from '@testing-library/react';

import { ConsentBanner } from '@/app/components/ConsentBanner';
import { CONSENT_KEY, currentConsent, openConsentPreferences, readConsent, subscribeConsent } from '@/app/lib/consent';

/**
 * CONSENT · the cookie notice.
 *
 * Refusing costs one click, like accepting. Until one of the buttons is pressed
 * nothing optional is on; and the preferences can be reopened later, where a
 * change takes effect at once.
 */

let mockPath = '/';
jest.mock('next/navigation', () => ({ usePathname: () => mockPath }));

beforeEach(() => {
  mockPath = '/';
  window.localStorage.clear();
});

const stored = () => JSON.parse(window.localStorage.getItem(CONSENT_KEY) ?? 'null');

test('a first visit is asked, with three answers of the same weight', () => {
  render(<ConsentBanner />);
  const notice = screen.getByRole('dialog', { name: 'Cookies en esta tienda' });
  expect(notice).toBeInTheDocument();
  for (const name of ['Aceptar todas', 'Rechazar opcionales', 'Configurar']) {
    expect(screen.getByRole('button', { name })).toBeEnabled();
  }
  // Until an answer, nothing optional is on and nothing was written.
  expect(currentConsent()).toEqual({ analytics: false, marketing: false });
  expect(stored()).toBeNull();
});

test('«Aceptar todas» turns both on and the notice goes away', () => {
  render(<ConsentBanner />);
  fireEvent.click(screen.getByRole('button', { name: 'Aceptar todas' }));
  expect(stored()).toMatchObject({ version: 2, analytics: true, marketing: true });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('«Rechazar opcionales» is one click and leaves both off', () => {
  render(<ConsentBanner />);
  fireEvent.click(screen.getByRole('button', { name: 'Rechazar opcionales' }));
  expect(stored()).toMatchObject({ version: 2, analytics: false, marketing: false });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('«Configurar» starts with everything optional off, and the necessary ones cannot be switched', () => {
  render(<ConsentBanner />);
  fireEvent.click(screen.getByRole('button', { name: 'Configurar' }));

  expect(screen.getByRole('switch', { name: 'Necesarias' })).toBeChecked();
  expect(screen.getByRole('switch', { name: 'Necesarias' })).toBeDisabled();
  expect(screen.getByRole('switch', { name: 'Analítica' })).not.toBeChecked();
  expect(screen.getByRole('switch', { name: 'Marketing' })).not.toBeChecked();

  fireEvent.click(screen.getByRole('switch', { name: 'Analítica' }));
  fireEvent.click(screen.getByRole('button', { name: 'Guardar preferencias' }));
  expect(stored()).toMatchObject({ analytics: true, marketing: false });
});

test('somebody who already answered is not asked again', () => {
  window.localStorage.setItem(CONSENT_KEY, JSON.stringify({ version: 2, analytics: false, marketing: false, decidedAt: 'x' }));
  render(<ConsentBanner />);
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('the preferences reopen from the footer link, showing the answer on file, and a change is announced', () => {
  window.localStorage.setItem(CONSENT_KEY, JSON.stringify({ version: 2, analytics: true, marketing: true, decidedAt: 'x' }));
  const heard: unknown[] = [];
  const stop = subscribeConsent((consent) => heard.push(consent));
  render(<ConsentBanner />);

  act(() => openConsentPreferences());
  expect(screen.getByRole('dialog', { name: 'Preferencias de cookies' })).toBeInTheDocument();
  expect(screen.getByRole('switch', { name: 'Marketing' })).toBeChecked();

  fireEvent.click(screen.getByRole('switch', { name: 'Marketing' }));
  fireEvent.click(screen.getByRole('button', { name: 'Guardar preferencias' }));
  expect(readConsent()).toMatchObject({ analytics: true, marketing: false });
  expect(heard).toEqual([{ analytics: true, marketing: false }]);
  stop();
});

test('the panel does not ask: staff are not measured', () => {
  mockPath = '/admin/orders';
  render(<ConsentBanner />);
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('an answer that is not one is no answer', () => {
  for (const value of ['"yes"', '{"version":1,"analytics":"true","marketing":1}', '{"version":3,"analytics":true,"marketing":true}', 'null', '{']) {
    window.localStorage.setItem(CONSENT_KEY, value);
    expect(readConsent()).toBeNull();
    expect(currentConsent()).toEqual({ analytics: false, marketing: false });
  }
});


test('the notice links to privacy information before asking for consent', () => {
  render(<ConsentBanner />);
  expect(screen.getByRole('link', { name: 'Privacidad y cookies' })).toHaveAttribute('href', '/privacy');
});

test('the earlier notice does not authorize measurement under the new notice', () => {
  window.localStorage.setItem('bd.consent.v1', JSON.stringify({ version: 1, analytics: true, marketing: true, decidedAt: 'x' }));
  render(<ConsentBanner />);
  expect(screen.getByRole('dialog', { name: 'Cookies en esta tienda' })).toBeInTheDocument();
  expect(currentConsent()).toEqual({ analytics: false, marketing: false });
});
