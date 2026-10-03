import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ImageUploadField } from '@/app/admin/components/ImageUploadField';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * El hueco de imagen del panel: subir un archivo y colocar su dirección.
 *
 * Hasta ahora una imagen de la tienda era una dirección escrita a mano. El
 * dueño de una tienda no aloja archivos: tiene la foto en su equipo. Aquí se
 * comprueba hasta la petición —qué archivo viaja y a dónde— y qué recibe el
 * formulario que contiene el hueco.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const mockFetch = fetchWithAuth as jest.Mock;

const URL_ = '/api/storefront/images/' + 'a'.repeat(32) + '/';

function png(name = 'recorte.png') {
  return new File([new Uint8Array([137, 80, 78, 71])], name, { type: 'image/png' });
}

function reply(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function setup(props: Partial<React.ComponentProps<typeof ImageUploadField>> = {}) {
  const onChange = jest.fn();
  render(
    <ImageUploadField
      label="Imagen del hero" name="hero_image_url" value="" companyId={7}
      onChange={onChange} {...props}
    />,
  );
  return { onChange, input: screen.getByLabelText('Subir Imagen del hero') as HTMLInputElement };
}

beforeEach(() => mockFetch.mockReset());

describe('hueco de imagen', () => {
  it('sube el archivo elegido a la empresa del panel y entrega su dirección', async () => {
    mockFetch.mockResolvedValue(reply(201, { url: URL_, has_alpha: true, width: 800, height: 600 }));
    const { onChange, input } = setup();

    await userEvent.upload(input, png());

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('hero_image_url', URL_));
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain('/admin/storefront/images/?company=7');
    expect(init.method).toBe('POST');
    expect((init.body as FormData).get('file')).toBeInstanceOf(File);
    expect(((init.body as FormData).get('file') as File).name).toBe('recorte.png');
  });

  it('sólo ofrece los formatos que el servidor acepta', () => {
    const { input } = setup();
    expect(input.accept).toBe('image/png,image/jpeg,image/webp');
  });

  it('dice el motivo cuando el servidor rechaza el archivo y no cambia el valor', async () => {
    mockFetch.mockResolvedValue(reply(400, { detail: 'La imagen pesa más de 8 MB.' }));
    const { onChange, input } = setup({ value: '/antes.png' });

    await userEvent.upload(input, png());

    expect(await screen.findByRole('alert')).toHaveTextContent('La imagen pesa más de 8 MB.');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('muestra la imagen colocada sobre un fondo que deja ver la transparencia', () => {
    setup({ value: URL_ });

    const preview = screen.getByRole('img', { name: 'Imagen del hero' });
    expect(preview).toHaveAttribute('src', URL_);
    expect(preview.closest('[data-transparency-grid]')).not.toBeNull();
  });

  it('sin imagen muestra el hueco, no una imagen rota', () => {
    setup();

    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.getByText('Sin imagen')).toBeInTheDocument();
  });

  it('quitar la imagen deja el hueco vacío', async () => {
    const { onChange } = setup({ value: URL_ });

    await userEvent.click(screen.getByRole('button', { name: 'Quitar Imagen del hero' }));

    expect(onChange).toHaveBeenCalledWith('hero_image_url', '');
  });

  it('en sólo lectura no ofrece subir ni quitar', () => {
    render(
      <ImageUploadField
        label="Imagen del hero" name="hero_image_url" value={URL_} companyId={7}
        onChange={jest.fn()} readOnly
      />,
    );

    expect(screen.queryByLabelText('Subir Imagen del hero')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Quitar Imagen del hero' })).toBeNull();
  });
});
