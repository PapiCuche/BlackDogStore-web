/** INV-UX-A1 — «Siguiente paso» bar, last-branch memory and the receiving links. */
import { render, screen } from '@testing-library/react';

import { NextStepBar, extractLabelBatch } from '@/app/admin/components/NextStepBar';
import { forgetBranch, preferredBranch, rememberBranch } from '@/app/admin/lib/last-branch';
import { productIdFromSearch, receiveHref, wantsToReceive } from '@/app/admin/lib/receive-links';

const PRODUCT = { id: 7, name: 'Cable USB-C 1 m', slug: 'cable-usb-c-1m' };

describe('NextStepBar', () => {
  it('links to labels and publication, filtered to the product', () => {
    render(<NextStepBar product={PRODUCT} published={false} />);
    expect(screen.getByRole('link', { name: 'Etiquetas' })).toHaveAttribute('href', '/admin/inventory/labels');
    expect(screen.getByRole('link', { name: 'Publicación' }))
      .toHaveAttribute('href', '/admin/products/publication?search=Cable%20USB-C%201%20m');
    expect(screen.queryByRole('link', { name: 'Ver en la tienda' })).toBeNull();
  });

  it('offers the storefront only when it is published', () => {
    const { rerender } = render(<NextStepBar product={PRODUCT} published />);
    const link = screen.getByRole('link', { name: 'Ver en la tienda' });
    expect(link).toHaveAttribute('href', '/product/cable-usb-c-1m');
    expect(link).toHaveAttribute('target', '_blank');
    rerender(<NextStepBar product={PRODUCT} published={null} />);
    expect(screen.queryByRole('link', { name: 'Ver en la tienda' })).toBeNull();
    rerender(<NextStepBar product={PRODUCT} published showStorefront={false} />);
    expect(screen.queryByRole('link', { name: 'Ver en la tienda' })).toBeNull();
  });

  it('renders the screen\'s own actions', () => {
    render(<NextStepBar product={PRODUCT}><button type="button">Guardar y recibir más stock</button></NextStepBar>);
    expect(screen.getByRole('button', { name: 'Guardar y recibir más stock' })).toBeInTheDocument();
  });

  it('announces a label batch when the response has one', () => {
    render(<NextStepBar product={PRODUCT} labelBatch={{ count: 1, href: '/admin/inventory/labels' }} />);
    expect(screen.getByRole('status')).toHaveTextContent('Etiquetas listas para imprimir');
    expect(screen.getByRole('link', { name: 'Abrir las etiquetas' })).toHaveAttribute('href', '/admin/inventory/labels');
  });

  it('says nothing about labels without a batch', () => {
    render(<NextStepBar product={PRODUCT} labelBatch={null} />);
    expect(screen.queryByText(/listas para imprimir/)).toBeNull();
  });
});

describe('extractLabelBatch', () => {
  it('is null when the response has neither key (LABEL-A not deployed)', () => {
    expect(extractLabelBatch({ results: [] })).toBeNull();
    expect(extractLabelBatch(null)).toBeNull();
    expect(extractLabelBatch('x')).toBeNull();
    expect(extractLabelBatch({ label_batch: null })).toBeNull();
    expect(extractLabelBatch({ label_batches: [] })).toBeNull();
  });
  it('reads label_batch and label_batches', () => {
    expect(extractLabelBatch({ label_batch: { id: 3, count: 4 } })).toEqual({ count: 4, href: '/admin/inventory/labels' });
    expect(extractLabelBatch({ label_batches: [{ id: 1 }, { id: 2 }] })).toEqual({ count: 2, href: '/admin/inventory/labels' });
  });
  it('trusts only an internal admin url', () => {
    expect(extractLabelBatch({ label_batch: { url: '/admin/inventory/labels?batch=3' } })?.href).toBe('/admin/inventory/labels?batch=3');
    expect(extractLabelBatch({ label_batch: { url: 'https://evil.example/x' } })?.href).toBe('/admin/inventory/labels');
    expect(extractLabelBatch({ label_batch: { url: '//evil.example/x' } })?.href).toBe('/admin/inventory/labels');
  });
});

describe('last branch (memory of the page)', () => {
  beforeEach(() => forgetBranch());
  it('is used only while the caller still reaches it', () => {
    rememberBranch(2);
    expect(preferredBranch([{ id: 1 }, { id: 2 }], 1)).toBe(2);
    expect(preferredBranch([{ id: 1 }], 1)).toBe(1);
    forgetBranch();
    expect(preferredBranch([{ id: 1 }, { id: 2 }], null)).toBeNull();
  });
  it('writes nothing to the browser storage', () => {
    localStorage.clear(); sessionStorage.clear();
    rememberBranch(5);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe('receiving links', () => {
  it('goes to Equipos for a serial-tracked device and Movimientos otherwise', () => {
    expect(receiveHref({ id: 41, is_serialized: true })).toBe('/admin/inventory/units?register=1&product=41');
    expect(receiveHref({ id: 8, is_serialized: false })).toBe('/admin/inventory/movements?receive=1&product=8');
    expect(receiveHref({ id: 8 })).toBe('/admin/inventory/movements?receive=1&product=8');
  });
  it('reads ?product= only when it is a plain positive number', () => {
    expect(productIdFromSearch('?product=41&register=1')).toBe(41);
    expect(productIdFromSearch('product=7')).toBe(7);
    for (const bad of ['', '?product=', '?product=0', '?product=-3', '?product=4a', '?product=1e3', '?product=1234567890123']) {
      expect(productIdFromSearch(bad)).toBeNull();
    }
    expect(wantsToReceive('?receive=1', 'receive')).toBe(true);
    expect(wantsToReceive('?register=1', 'receive')).toBe(false);
  });
});
