import { render, screen } from '@testing-library/react';

let mockPathname = '/';
jest.mock('next/navigation', () => ({ usePathname: () => mockPathname }));
jest.mock('@/app/components/Header', () => ({ Header: () => null }));
jest.mock('@/app/components/Footer', () => ({ Footer: () => null }));
jest.mock('@/app/components/StorefrontProvider', () => ({
  useStorefront: () => ({ contact: {} }),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { SkipLink, StorefrontContent } = require('@/app/components/StorefrontChrome');

/**
 * «Saltar al contenido» — lo primero que encuentra el teclado.
 *
 * Sin él, quien navega con teclado o con lector de pantalla recorre la cabecera
 * entera —logotipo, catálogo, categorías, carrito, tema— en cada página antes
 * de llegar a lo que vino a leer. El enlace existió y se perdió al reconciliar
 * la interfaz; el panel conservó el destino (`#admin-main-content`) sin nadie
 * que apuntara a él.
 */
describe('enlace de salto', () => {
  it('en la tienda apunta al contenido de la página', () => {
    mockPathname = '/product';
    render(
      <>
        <SkipLink />
        <StorefrontContent><p>contenido</p></StorefrontContent>
      </>,
    );

    const link = screen.getByRole('link', { name: 'Saltar al contenido' });
    const target = document.querySelector(link.getAttribute('href')!);

    expect(target).not.toBeNull();
    expect(target).toHaveTextContent('contenido');
    // Enfocable por programa: sin esto el salto mueve la vista y no el foco.
    expect(target).toHaveAttribute('tabindex', '-1');
  });

  it('en el panel apunta al área principal del panel', () => {
    mockPathname = '/admin/inventory';
    render(<SkipLink />);

    expect(screen.getByRole('link', { name: 'Saltar al contenido' }))
      .toHaveAttribute('href', '#admin-main-content');
  });

  it('en el panel el envoltorio no duplica el destino', () => {
    mockPathname = '/admin';
    const { container } = render(<StorefrontContent><p>x</p></StorefrontContent>);

    expect(container.querySelector('#contenido')).toBeNull();
  });
});
