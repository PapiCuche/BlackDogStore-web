/**
 * INV-COM-01B — «Destino comercial»: dónde queda cada línea de la recepción en la tienda y qué falta.
 * Es una lectura del servidor (sin precios ni identificadores); una recepción no publica nada.
 */
import { render, screen } from '@testing-library/react';

import { ReceivingCommercial, destinationChain, destinationState } from '@/app/admin/components/ReceivingCommercial';
import type { Commercial, ReceiptPreview } from '@/app/admin/lib/inventory-receipts';

type Line = ReceiptPreview['lines'][number];

const commercial = (extra: Partial<Commercial> = {}): Commercial => ({
  placement: 'model_page', state: 'pending', published: false, problems: [], model: { id: 1, name: 'iPhone 13', published: true },
  has_dispatch_branch: true, in_dispatch_branch: true, units_priced: 2, units_unpriced: 0, priced_by: 'unit', ...extra,
});
const line = (extra: Partial<Line> = {}, c: Partial<Commercial> = {}): Line => ({
  index: 0, kind: 'units', product: { id: 5, name: 'iPhone 13 128 GB Azul Seminuevo', is_serialized: true, condition_line: 'used' },
  status: 'ok', errors: [], warnings: [], will_receive: 2, labels: 2, duplicates: [], unit_issues: [], commercial: commercial(c), ...extra,
});

describe('la cadena modelo → línea → variante → unidades', () => {
  it('un seminuevo con modelo', () => {
    expect(destinationChain(line(), commercial())).toEqual(['iPhone 13', 'Seminuevos', 'iPhone 13 128 GB Azul Seminuevo', '2 unidades identificadas']);
  });
  it('una variante nueva con modelo dice Nuevos y una unidad en singular', () => {
    const l = line({ will_receive: 1, product: { id: 6, name: 'iPhone 13 128 GB Azul', is_serialized: true, condition_line: 'new' } });
    expect(destinationChain(l, commercial({ placement: 'card' }))).toEqual(['iPhone 13', 'Nuevos', 'iPhone 13 128 GB Azul', '1 unidad identificada']);
  });
  it('un accesorio sin modelo: producto → unidades por cantidad', () => {
    const l = line({ kind: 'quantity', will_receive: 5, product: { id: 7, name: 'Cable USB-C', is_serialized: false } });
    expect(destinationChain(l, commercial({ model: null, placement: 'card', priced_by: 'product' }))).toEqual(['Cable USB-C', '5 unidades por cantidad']);
  });
});

describe('el estado, sin prometer lo que no sabe', () => {
  it('publicado no promete que el cliente lo vea', () => {
    expect(destinationState(commercial({ state: 'published', published: true }))).toMatch(/Publicado\. Que un cliente lo vea además depende de las existencias.*y de que cada equipo tenga su precio\./);
    expect(destinationState(commercial({ state: 'published', published: true, priced_by: 'product' }))).not.toMatch(/precio/);
  });
  it('preparado: es decisión del operador publicarlo', () => {
    expect(destinationState(commercial({ state: 'ready' }))).toMatch(/Preparado para publicar.*stock interno.*decisión aparte/);
  });
  it('pendiente dice qué falta, incluidos los equipos sin precio', () => {
    expect(destinationState(commercial({ problems: ['no_category', 'no_image'], units_unpriced: 1 })))
      .toBe('Pendiente de completar para publicar: falta una categoría, una imagen, el precio propio de 1 equipo. Mientras tanto es stock interno.');
    expect(destinationState(commercial({ problems: [], units_unpriced: 3 }))).toMatch(/el precio propio de 3 equipos/);
  });
});

describe('el bloque en la revisión', () => {
  it('se dibuja con nombre, cadena y regla de ubicación, y el enlace sale a otra pestaña', () => {
    render(<ReceivingCommercial line={line()} canViewProducts />);
    const box = screen.getByRole('region', { name: 'Destino comercial de iPhone 13 128 GB Azul Seminuevo' });
    expect(box).toHaveTextContent('iPhone 13 → Seminuevos → iPhone 13 128 GB Azul Seminuevo → 2 unidades identificadas');
    expect(box).toHaveTextContent('Cada equipo se ofrece, uno por uno, en la página de su modelo; no tiene una tarjeta pública propia.');
    const link = screen.getByRole('link', { name: /Abrir Publicación e-commerce/ });
    expect(link).toHaveAttribute('href', '/admin/products/publication');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('data-receiving-leave', 'ok');
  });

  it('sin permiso de ver productos no hay enlace; ya publicado tampoco', () => {
    const { rerender } = render(<ReceivingCommercial line={line()} canViewProducts={false} />);
    expect(screen.queryByRole('link')).toBeNull();
    rerender(<ReceivingCommercial line={line({}, { state: 'published', published: true })} canViewProducts />);
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('una variante con tarjeta propia lo dice', () => {
    render(<ReceivingCommercial line={line({}, { placement: 'card', model: null })} canViewProducts />);
    expect(screen.getByText(/Tiene su tarjeta propia en la tienda cuando se publica/)).toBeInTheDocument();
  });

  it('avisa si la sucursal no es la de despacho o si la empresa no tiene una', () => {
    const { rerender } = render(<ReceivingCommercial line={line({}, { in_dispatch_branch: false })} canViewProducts />);
    expect(screen.getByText(/no es la de despacho de la tienda en línea/)).toBeInTheDocument();
    rerender(<ReceivingCommercial line={line({}, { has_dispatch_branch: false, in_dispatch_branch: false })} canViewProducts />);
    expect(screen.getByText(/no tiene una sucursal de despacho definida/)).toBeInTheDocument();
    rerender(<ReceivingCommercial line={line()} canViewProducts />);
    expect(screen.queryByText(/despacho/)).toBeNull();
  });

  it('avisa si el modelo está oculto aunque la variante esté publicada', () => {
    render(<ReceivingCommercial line={line({}, { state: 'published', published: true, model: { id: 1, name: 'iPhone 13', published: false } })} canViewProducts />);
    expect(screen.getByText(/El modelo «iPhone 13» está oculto/)).toBeInTheDocument();
  });

  it('sin datos comerciales del servidor no se dibuja nada', () => {
    const { container } = render(<ReceivingCommercial line={line({ commercial: undefined })} canViewProducts />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('lo que dice el servidor de un precio o de los detalles, junto a su campo', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { serverProblems } = require('@/app/admin/components/ReceivingWizard') as typeof import('@/app/admin/components/ReceivingWizard');

  it('un problema de sale_price o public_notes cae en su campo, no en la fila genérica', () => {
    const data = {
      ok: false, branch: { id: 1, name: 'x' }, supplier: null, document_ref: '', totals: { lines: 1, quantity_units: 0, devices: 0, labels: 0 },
      lines: [{
        index: 0, kind: 'units', product: { id: 1, name: 'x', is_serialized: true }, status: 'error', errors: ['Equipo 1: el precio propio sólo se indica en equipos seminuevos.'],
        warnings: [], will_receive: 0, labels: 0, duplicates: [],
        unit_issues: [
          { unit: 0, field: 'sale_price', kind: 'error', message: 'Equipo 1: el precio propio sólo se indica en equipos seminuevos.' },
          { unit: 0, field: 'public_notes', kind: 'error', message: 'Equipo 1: los detalles para el comprador sólo se indican en equipos seminuevos.' },
          { unit: 0, field: 'battery_health', kind: 'error', message: 'Equipo 1: otra cosa.' },
        ],
      }],
    } as never;
    const out = serverProblems(data, ['L1'], [['U1']], (_i: number, m: string) => m);
    expect(out.L1.units.U1.price).toMatch(/precio propio/);
    expect(out.L1.units.U1.publicNotes).toMatch(/detalles para el comprador/);
    expect(out.L1.units.U1.row).toMatch(/otra cosa/);
  });
});
