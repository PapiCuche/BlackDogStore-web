import { render } from '@testing-library/react';

/**
 * La foto de un producto es una dirección que escribe la tienda. Puede estar en
 * un host que el optimizador de imágenes no tiene permitido, y `next/image`
 * LANZA en ese caso: una sola foto así tumbaba la página entera.
 *
 * `ProductImage` optimiza lo que puede y, lo que no, lo muestra con un `<img>`
 * normal. Nunca rompe.
 */

jest.mock('next/image', () => ({
  __esModule: true,
  default: (props: { src: string; alt: string }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img data-optimised="yes" src={props.src} alt={props.alt} />
  ),
}));

const ENV = process.env.NEXT_PUBLIC_IMAGE_HOSTS;
afterEach(() => { process.env.NEXT_PUBLIC_IMAGE_HOSTS = ENV; jest.resetModules(); });

function load(hosts: string) {
  process.env.NEXT_PUBLIC_IMAGE_HOSTS = hosts;
  jest.resetModules();
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require('@/app/components/ProductImage') as typeof import('@/app/components/ProductImage');
}

it('optimiza una foto de un host permitido', () => {
  const { ProductImage } = load('cdn.tienda.example');
  const { container } = render(<ProductImage src="https://cdn.tienda.example/a.png" alt="A" sizes="64px" />);

  expect(container.querySelector('img')).toHaveAttribute('data-optimised', 'yes');
});

it('una foto de un host que no está permitido se muestra igual, sin optimizar y sin romper', () => {
  const { ProductImage } = load('cdn.tienda.example');
  const { container } = render(<ProductImage src="https://otro.example/a.png" alt="A" sizes="64px" />);

  const img = container.querySelector('img')!;
  expect(img).not.toHaveAttribute('data-optimised');
  expect(img).toHaveAttribute('src', 'https://otro.example/a.png');
  expect(img).toHaveAttribute('loading', 'lazy');
});

it('una imagen subida desde el panel se muestra tal cual y con su sombra por silueta', () => {
  const { ProductImage } = load('');
  const id = 'a'.repeat(32);
  for (const src of [`/api/storefront/images/${id}`, `https://tienda.example/api/storefront/images/${id}`]) {
    const { container, unmount } = render(<ProductImage src={src} alt="" sizes="64px" />);
    const img = container.querySelector('img')!;
    expect(img).not.toHaveAttribute('data-optimised');
    expect(img.style.filter).toBe('var(--cutout-shadow)');
    unmount();
  }
});

it('una foto externa no recibe la sombra de las imágenes de la tienda', () => {
  const { ProductImage } = load('cdn.tienda.example');
  for (const src of ['https://cdn.tienda.example/a.png', 'https://otro.example/a.png']) {
    const { container, unmount } = render(<ProductImage src={src} alt="" sizes="64px" />);
    expect(container.querySelector('img')!.style.filter).toBe('');
    unmount();
  }
});
