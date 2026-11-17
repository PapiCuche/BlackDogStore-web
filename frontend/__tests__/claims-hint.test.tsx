import { readFileSync } from 'fs';
import path from 'path';

import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';

import { ClaimsHint, CLAIMS_CHECK_DELAY_MS } from '@/app/admin/components/ClaimsHint';
import { DescriptionEditor } from '@/app/admin/components/DescriptionEditor';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * P2 · F5c — avisos del control de afirmaciones (Manual V4 §21) bajo los textos que publica la tienda.
 *
 * Con la API simulada (las reglas las prueba Django):
 *   - el texto se revisa tras una pausa al escribir, con la empresa del contexto, y nunca vacío ni demasiado largo;
 *   - cada frase señalada sale con su consejo, en una región viva y accesible, en español;
 *   - un error de la API (permiso, límite, red) no muestra nada y no rompe el formulario: el aviso NUNCA bloquea;
 *   - una respuesta vieja no pisa la del texto actual;
 *   - los editores de la tienda lo muestran.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const BATTERY = {
  phrase: 'batería al 100 %', rule: 'condition',
  advice: 'sólo si el equipo inspeccionado lo respalda (Manual V4 §21)',
};
const OFFICIAL = {
  phrase: 'distribuidor oficial', rule: 'official',
  advice: 'sólo con una acreditación verificable del fabricante (Manual V4 §21)',
};

async function settle() {
  await act(async () => {
    jest.advanceTimersByTime(CLAIMS_CHECK_DELAY_MS + 10);
  });
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  send.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('ClaimsHint', () => {
  it('checks the text after a pause and lists each phrase with its advice in a live region', async () => {
    send.mockResolvedValue(reply({ warnings: [BATTERY, OFFICIAL] }));
    render(<ClaimsHint text="Distribuidor oficial, batería al 100 %" companyId={7} />);
    expect(send).not.toHaveBeenCalled();
    await settle();

    expect(send).toHaveBeenCalledTimes(1);
    const [url, init] = send.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/claims\/check\/\?company=7$/);
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ text: 'Distribuidor oficial, batería al 100 %' });

    const region = screen.getByRole('status');
    expect(region).toHaveAttribute('aria-live', 'polite');
    expect(region).toHaveTextContent(
      'Revisa: "batería al 100 %" sólo si el equipo inspeccionado lo respalda (Manual V4 §21)',
    );
    expect(region).toHaveTextContent('Revisa: "distribuidor oficial" sólo con una acreditación verificable');
    expect(region).toHaveTextContent('No impide guardar');
  });

  it('shows nothing for a clean text', async () => {
    send.mockResolvedValue(reply({ warnings: [] }));
    render(<ClaimsHint text="Equipo revisado." />);
    await settle();
    expect(send).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it('never asks for an empty or an over-long text', async () => {
    const { rerender } = render(<ClaimsHint text="   " />);
    await settle();
    rerender(<ClaimsHint text={'a'.repeat(20001)} />);
    await settle();
    expect(send).not.toHaveBeenCalled();
  });

  it.each([
    ['a refusal', () => send.mockResolvedValue(reply({ detail: 'No.' }, 403))],
    ['the rate limit', () => send.mockResolvedValue(reply({ detail: 'Espera.' }, 429))],
    ['a network error', () => send.mockRejectedValue(new TypeError('Failed to fetch'))],
  ])('shows nothing and does not break on %s', async (_label, arrange) => {
    arrange();
    render(<ClaimsHint text="impecable" />);
    await settle();
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it('typing again waits for the pause, and an old answer never replaces the current one', async () => {
    let resolveFirst: (r: Response) => void = () => {};
    send.mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveFirst = resolve; }));
    send.mockResolvedValueOnce(reply({ warnings: [] }));
    const { rerender } = render(<ClaimsHint text="impecable" />);
    await settle();
    rerender(<ClaimsHint text="Equipo revisado" />);
    await settle();
    await act(async () => {
      resolveFirst(reply({ warnings: [{ phrase: 'impecable', rule: 'condition', advice: BATTERY.advice }] }));
      await Promise.resolve();
    });
    expect(send).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });

  it('clears the warnings as soon as the text is emptied', async () => {
    send.mockResolvedValue(reply({ warnings: [BATTERY] }));
    const { rerender } = render(<ClaimsHint text="batería al 100 %" />);
    await settle();
    expect(screen.getByRole('status')).not.toBeEmptyDOMElement();
    rerender(<ClaimsHint text="" />);
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  });
});

function DescriptionHarness() {
  const [value, setValue] = useState('');
  return <DescriptionEditor id="d" label="Descripción" value={value} onChange={setValue} />;
}

describe('editors show the claims hint', () => {
  it('the description editor of products and models warns while typing, and saving stays possible', async () => {
    send.mockResolvedValue(reply({ warnings: [BATTERY] }));
    render(<DescriptionHarness />);
    fireEvent.change(screen.getByLabelText('Descripción'), { target: { value: 'Batería al 100 %' } });
    await settle();
    expect(screen.getByRole('status')).toHaveTextContent('Revisa: "batería al 100 %"');
    // Advice, not an error: the field is not marked invalid.
    expect(screen.getByLabelText('Descripción')).not.toHaveAttribute('aria-invalid');
  });

  it.each([
    ['app/admin/components/CatalogModelsManager.tsx', 'the cosmetic grade editor'],
    ['app/admin/components/UsedUnitsPanel.tsx', 'the used-device notes'],
    ['app/admin/settings/storefront/page.tsx', 'the home page, services hero and campaign editors'],
    ['app/admin/settings/storefront/ListContentEditor.tsx', 'the service rows'],
  ])('%s renders ClaimsHint (%s)', (file) => {
    const source = readFileSync(path.join(__dirname, '..', file), 'utf8');
    expect(source).toMatch(/<ClaimsHint\b/);
  });
});
