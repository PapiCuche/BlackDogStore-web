import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { UnitPhotoManager, moved, unitPhotoLabel } from '@/app/admin/components/UnitPhotoManager';
import { CartItemCard, CartThumb } from '@/app/components/CartItemCard';
import { UsedUnitCardV4 } from '@/app/components/v4/UsedUnitCardV4';
import { fetchWithAuth } from '@/app/lib/auth';
import type { UsedUnit } from '@/app/lib/catalog-models';
import { publicPhotoCount, type UnitPhoto } from '@/app/lib/stock-units';

import { reply } from './support/device-commerce-fixtures';

/**
 * UNIT-PHOTO-02 — the real photos of each used device.
 *
 * The panel: each photo says which side it shows (a defect says what), one is the primary, they are reordered with
 * the keyboard, replaced in place and deleted; only an inspector confirms «esta foto es de este equipo exacto», and
 * only confirmed photos are «Se muestra en la tienda». The shop: a card, a cart line and its devices show THAT
 * device's photo, or «Sin fotos de este equipo» — never a generic picture.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown };
let writes: Call[];

const PHOTOS: UnitPhoto[] = [
  { id: 1, url: 'http://localhost/1.png', alt_text: '', view: 'front', view_label: 'Frontal', is_primary: true,
    attested: true, attested_by: 'Ana', public: true },
  { id: 2, url: 'http://localhost/2.png', alt_text: '', view: 'defect', view_label: 'Defecto: rayón', is_primary: false,
    attested: false, public: false },
];

beforeEach(() => {
  writes = [];
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    writes.push({
      method, path: String(input).replace(/^.*\/api/, ''),
      body: init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}')),
    });
    return reply({ id: 7, photos: PHOTOS });
  });
});

function manager(props: Partial<Parameters<typeof UnitPhotoManager>[0]> = {}) {
  const onChange = jest.fn();
  render(<UnitPhotoManager unitId={7} photos={PHOTOS} canEdit canAttest={false} onChange={onChange} {...props} />);
  return onChange;
}

describe('el gestor de fotos del panel', () => {
  it('dice el lado de cada foto, cuál es la principal y cuáles se muestran en la tienda', () => {
    manager();
    const items = screen.getAllByTestId('unit-photo');
    expect(items[0]).toHaveTextContent('1. FrontalPrincipal');
    expect(within(items[0]).getByTestId('unit-photo-state')).toHaveTextContent('Se muestra en la tienda');
    expect(within(items[1]).getByTestId('unit-photo-state')).toHaveTextContent('falta confirmarla');
    expect(screen.getByText(/\(1 de 2\)/)).toBeInTheDocument();
  });

  it('sube con su lado, y un defecto dice qué muestra', async () => {
    const onChange = manager();
    const file = new File(['x'], 'reverso.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('Nueva foto'), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText('Qué muestra'), { target: { value: 'defect' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir foto' })); });
    expect(writes).toEqual([]);
    expect(screen.getByRole('alert')).toHaveTextContent('qué defecto muestra');
    fireEvent.change(screen.getByLabelText('Qué defecto muestra (lo lee el comprador)'), { target: { value: 'Golpe en la esquina' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir foto' })); });
    expect(writes[0].path).toBe('/admin/inventory/units/7/photos/');
    const form = writes[0].body as FormData;
    expect([form.get('file'), form.get('view'), form.get('defect_note')]).toEqual([file, 'defect', 'Golpe en la esquina']);
    expect(onChange).toHaveBeenCalledWith({ id: 7, photos: PHOTOS });
    expect(screen.getByRole('status')).toHaveTextContent('hasta que se confirme');
  });

  it('se ordena con el teclado, se elige la principal, se reemplaza y se elimina', async () => {
    manager();
    expect(screen.getByRole('button', { name: 'Subir foto 1 (Frontal)' })).toBeDisabled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Subir foto 2 (Defecto: rayón)' })); });
    expect(writes[0]).toEqual({ method: 'POST', path: '/admin/inventory/units/7/photos/order/', body: { order: [2, 1] } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Hacer principal la foto 2 (Defecto: rayón)' })); });
    expect(writes[1]).toEqual({ method: 'PATCH', path: '/admin/inventory/units/7/photos/2/', body: { is_primary: true } });
    const file = new File(['y'], 'nueva.png', { type: 'image/png' });
    await act(async () => { fireEvent.change(screen.getByTestId('unit-photo-replace-2'), { target: { files: [file] } }); });
    expect(writes[2].path).toBe('/admin/inventory/units/7/photos/2/replace/');
    expect((writes[2].body as FormData).get('file')).toBe(file);
    expect(screen.getByRole('status')).toHaveTextContent('hay que confirmarla de nuevo');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Eliminar foto 1 (Frontal)' })); });
    expect(writes[3]).toMatchObject({ method: 'DELETE', path: '/admin/inventory/units/7/photos/1/' });
  });

  it('sólo quien inspecciona confirma que la foto es de este equipo exacto', async () => {
    manager({ canEdit: false });
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.getByText('Confirmada por Ana.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Eliminar/ })).toBeNull();
    expect(screen.queryByLabelText('Nueva foto')).toBeNull();
  });

  it('la confirmación se marca y se retira', async () => {
    manager({ canEdit: false, canAttest: true });
    const confirm = screen.getByRole('checkbox', { name: 'Confirmo que la foto 2 (Defecto: rayón) es de este equipo exacto' });
    await act(async () => { fireEvent.click(confirm); });
    expect(writes[0]).toEqual({ method: 'POST', path: '/admin/inventory/units/7/photos/2/attestation/', body: { attested: true } });
    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox', { name: 'Confirmo que la foto 1 (Frontal) es de este equipo exacto' }));
    });
    expect(writes[1].body).toEqual({ attested: false });
  });

  it('un error del servidor se dice', async () => {
    send.mockResolvedValueOnce(reply({ detail: 'Esa foto ya no está entre las de este equipo.' }, 404));
    manager();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Eliminar foto 2 (Defecto: rayón)' })); });
    expect(screen.getByRole('alert')).toHaveTextContent('Esa foto ya no está');
  });

  it('reglas puras', () => {
    expect(moved([1, 2, 3], 2, 0)).toEqual([3, 1, 2]);
    expect(moved([1, 2, 3], 0, 5)).toEqual([1, 2, 3]);
    expect(unitPhotoLabel({ view: 'defect', defect_note: 'Golpe' })).toBe('Defecto: Golpe');
    expect(unitPhotoLabel({})).toBe('Otra');
    expect(publicPhotoCount(PHOTOS)).toBe(1);
    expect(publicPhotoCount([{ id: 3, url: 'x', alt_text: '' }])).toBe(1);     // a server before UNIT-PHOTO-02
  });
});

describe('la tienda', () => {
  const UNIT: UsedUnit = {
    id: 'u1', product_id: 1, options: { Capacidad: '128 GB' }, battery_health: 90, cosmetic_grade: null,
    price: '1999.00', notes: '', imei_hint: '•••• 1234',
    photos: [
      { url: 'http://localhost/back.png', alt_text: '', width: 1, height: 1, view: 'back', view_label: 'Posterior', is_primary: false },
      { url: 'http://localhost/front.png', alt_text: '', width: 1, height: 1, view: 'front', view_label: 'Frontal', is_primary: true },
    ],
  };

  it('la tarjeta muestra la principal del equipo, o «Sin fotos de este equipo»', () => {
    const { container, rerender } = render(
      <UsedUnitCardV4 unit={UNIT} modelName="Equipo X" selected={false} onSelect={() => {}} />);
    expect(container.querySelector('img')?.getAttribute('src')).toContain('front.png');
    rerender(<UsedUnitCardV4 unit={{ ...UNIT, photos: [] }} modelName="Equipo X" selected={false} onSelect={() => {}} />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('Sin fotos de este equipo')).toBeInTheDocument();
  });

  it('la línea del carrito de un seminuevo sin foto lo dice, nunca otra imagen', () => {
    render(<CartThumb product={{ name: 'Equipo X', image_url: '', image_pending: false, image_scope: 'unit' }} />);
    expect(screen.getByTestId('cart-thumb-no-unit-photo')).toHaveTextContent('Sin fotos de este equipo');
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('cada equipo del carrito muestra su propia foto', () => {
    render(
      <CartItemCard
        id={1}
        quantity={2}
        sells_by_unit
        line_total="3998.00"
        product={{ id: 1, name: 'Equipo X', slug: 'equipo-x', image_url: 'http://localhost/front.png', image_scope: 'unit', condition_line: 'used' }}
        units={[
          { id: 'a', price: '1999.00', options: {}, battery_health: 90, cosmetic_grade: '', imei_hint: '', held_until: '',
            photo: { url: 'http://localhost/front.png', alt_text: '', width: 1, height: 1, view_label: 'Frontal' } },
          { id: 'b', price: '1999.00', options: {}, battery_health: 80, cosmetic_grade: '', imei_hint: '', held_until: '',
            photo: null },
        ]}
      />,
    );
    const [first, second] = screen.getAllByTestId('cart-unit-photo');
    expect(within(first).getByRole('img', { name: 'Foto de este equipo: Frontal' })).toBeInTheDocument();
    expect(second).toHaveTextContent('Sin fotos de este equipo');
  });
});
