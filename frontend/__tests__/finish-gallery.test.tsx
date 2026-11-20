import { fireEvent, render, screen, within } from '@testing-library/react';

import ModelDetail from '@/app/components/ModelDetail';
import { ModelConfiguratorV4 } from '@/app/components/v4/ModelConfiguratorV4';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { IMAGE_PENDING_LABEL } from '@/app/components/ImagePending';
import { REFERENCE_IMAGE_LABEL, photoLabel, variantGallery } from '@/app/lib/model-configurator';
import type { CatalogModel, Variant, VariantImage } from '@/app/lib/catalog-models';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * CATALOG-REFERENCE-01 · PR C (D-C-1) — the gallery of a REAL FINISH.
 *
 * A variant shows its own pictures; else the pictures of its colour (the finish: model + colour); else the neutral
 * «Imagen pendiente» marker. Never another colour's photo and never the model's cover (`model.image_url`, which is
 * one colour's photo and stays for lists).
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));

const MANAGED = (n: number) => `/api/storefront/images/${String(n).padStart(32, 'a')}`;
const picture = (url: string, extra: Partial<VariantImage> = {}): VariantImage => (
  { url, alt_text: '', is_primary: true, width: 800, height: 800, ...extra });

/** The fixture as a server with roles and finishes sends it: own pictures resolved into `gallery`. */
function finishModel(): CatalogModel {
  const model = modelFixture() as unknown as CatalogModel;
  model.attributes = model.attributes.map((attribute) => ({
    ...attribute, role: attribute.kind === 'color' ? 'color' : 'capacity',
  }));
  // Every variant without pictures of its own: what it shows is its finish's, or nothing.
  model.variants = model.variants.map((variant) => ({ ...variant, image_url: '', images: [] }));
  model.finish_images = {
    '21': [picture(MANAGED(21), { alt_text: 'Equipo X Azul' }), picture(MANAGED(210), { is_primary: false })],
    '23': [picture(MANAGED(23), { is_demo: true })],
  };
  const finishes = model.finish_images;
  model.variants = model.variants.map((variant) => {
    const gallery = finishes[String(variant.options['4'])] ?? [];
    return { ...variant, gallery, image_pending: gallery.length === 0 };
  });
  return model;
}

const variantOf = (model: CatalogModel, id: number) => model.variants.find((v) => v.product_id === id) as Variant;

describe('variantGallery: the rule', () => {
  it('uses what the server resolved for the variant', () => {
    const model = finishModel();
    expect(variantGallery(model, variantOf(model, 41)).map((image) => image.url)).toEqual([MANAGED(21), MANAGED(210)]);
    expect(variantGallery(model, variantOf(model, 45)).map((image) => image.url)).toEqual([MANAGED(23)]);
  });

  it('a colour switch changes the gallery: each colour shows its own finish', () => {
    const model = finishModel();
    const blue = variantGallery(model, variantOf(model, 41));
    const red = variantGallery(model, variantOf(model, 45));
    expect(blue.map((image) => image.url)).not.toEqual(red.map((image) => image.url));
  });

  it('a finish without pictures is empty — never another colour, never the cover', () => {
    const model = finishModel();
    expect(model.image_url).toBe('http://localhost/modelo.png');
    expect(variantGallery(model, variantOf(model, 42))).toEqual([]);
    expect(variantGallery(model, null)).toEqual([]);
  });

  it('a server without `gallery`: own pictures, own address, its colour\'s finish, else nothing', () => {
    const model = finishModel();
    const strip = (variant: Variant): Variant => {
      const rest: Variant = { ...variant };
      delete rest.gallery;
      delete rest.image_pending;
      return rest;
    };
    const own = { ...strip(variantOf(model, 41)), images: [picture('http://localhost/propia.png')] };
    expect(variantGallery(model, own).map((image) => image.url)).toEqual(['http://localhost/propia.png']);
    const address = { ...strip(variantOf(model, 41)), image_url: 'http://localhost/sola.png' };
    expect(variantGallery(model, address).map((image) => image.url)).toEqual(['http://localhost/sola.png']);
    expect(variantGallery(model, strip(variantOf(model, 43))).map((image) => image.url))
      .toEqual([MANAGED(21), MANAGED(210)]);
    expect(variantGallery(model, strip(variantOf(model, 42)))).toEqual([]);
    // Without finishes at all (an older server), still never the model's cover.
    const older = { ...model, finish_images: undefined };
    expect(variantGallery(older, strip(variantOf(model, 41)))).toEqual([]);
  });

  it('a demo resource is labelled «Imagen referencial», even when the shop uploaded it', () => {
    expect(photoLabel(MANAGED(23), 'catalog', true)).toBe(REFERENCE_IMAGE_LABEL);
    expect(photoLabel(MANAGED(23), 'catalog')).toBeNull();
  });
});

let model: CatalogModel;

beforeEach(() => {
  model = finishModel();
  global.fetch = jest.fn((input: RequestInfo | URL) => Promise.resolve(
    String(input).includes('/catalog/models/equipo-x') ? reply(model) : reply({}, 404),
  )) as unknown as typeof fetch;
});

const colour = () => screen.getByRole('radiogroup', { name: /^Color/ });

describe('V4 configurator', () => {
  async function renderV4() {
    render(
      <StorefrontProvider config={NEUTRAL_CONFIG}>
        <ModelConfiguratorV4 slug="equipo-x" />
      </StorefrontProvider>,
    );
    await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
    return screen.getByTestId('v4-gallery');
  }

  it('the colour chosen shows its finish, and a finish without pictures the neutral marker', async () => {
    const gallery = await renderV4();
    expect(within(gallery).getAllByRole('img')[0].getAttribute('src')).toContain(encodeURIComponent(MANAGED(21)));
    fireEvent.click(within(colour()).getByRole('radio', { name: /^Blanco/ }));
    const marker = within(gallery).getByTestId('image-pending');
    expect(marker).toHaveAccessibleName(new RegExp(IMAGE_PENDING_LABEL));
    expect(within(gallery).getByText(IMAGE_PENDING_LABEL)).toBeInTheDocument();
    // No photo at all: neither the blue one nor the model's cover.
    expect(gallery.querySelector('img')).toBeNull();
    expect(gallery.innerHTML).not.toContain('modelo.png');
  });

  it('a demo picture carries «Imagen referencial»', async () => {
    const gallery = await renderV4();
    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Capacidad' })).getByRole('radio', { name: '512 GB' }));
    expect(within(gallery).getByText(REFERENCE_IMAGE_LABEL)).toBeInTheDocument();
  });
});

describe('V3 model page', () => {
  it('a finish without pictures shows the neutral marker in the photo box', async () => {
    render(<ModelDetail slug="equipo-x" />);
    await screen.findByRole('heading', { level: 1, name: 'Equipo X' });
    expect(screen.getByTestId('model-main-image').querySelector('img')).not.toBeNull();
    fireEvent.click(within(colour()).getByRole('radio', { name: /^Blanco/ }));
    const box = screen.getByTestId('model-main-image');
    expect(within(box).getByTestId('image-pending')).toBeInTheDocument();
    expect(box.querySelector('img')).toBeNull();
  });
});

describe('variantGallery: the colour dimension (D-PRC-8), on a server without `gallery`', () => {
  const COVER = 'http://localhost/modelo.png';
  const bare = (variant: Variant): Variant => {
    const rest: Variant = { ...variant, image_url: '', images: [] };
    delete rest.gallery;
    delete rest.image_pending;
    return rest;
  };

  it('a colourless model (no variant has a colour) shows its cover', () => {
    const model = finishModel();
    model.attributes = model.attributes.filter((attribute) => attribute.kind !== 'color');
    model.variants = model.variants.map((variant) => bare({ ...variant, options: { '3': variant.options['3'] } }));
    model.finish_images = {};
    expect(variantGallery(model, model.variants[0]).map((image) => image.url)).toEqual([COVER]);
  });

  it('without the colour role, the colour kind is the dimension: its finish, never the cover', () => {
    const model = finishModel();
    model.attributes = model.attributes.map((attribute) => (
      attribute.kind === 'color' ? { ...attribute, role: 'other' as const } : attribute));
    model.variants = model.variants.map(bare);
    expect(variantGallery(model, variantOf(model, 41)).map((image) => image.url)).toEqual([MANAGED(21), MANAGED(210)]);
    expect(variantGallery(model, variantOf(model, 42))).toEqual([]);
  });

  it('a model on a legacy colour-kind attribute never shows the cover, even when another attribute has the role', () => {
    const model = finishModel();
    model.attributes = [
      ...model.attributes.map((attribute) => (
        attribute.kind === 'color' ? { ...attribute, role: 'other' as const } : attribute)),
      { id: 5, name: 'Tono', kind: 'color', role: 'color', sort_order: 2,
        options: [{ id: 51, label: 'Cálido', swatch_hex: '#AA5500', sort_order: 0 }] },
    ];
    model.variants = model.variants.map(bare);
    expect(variantGallery(model, variantOf(model, 41))).toEqual([]);
  });

  it('with the colour role present, an attribute of the colour kind lends no finish pictures', () => {
    const model = finishModel();
    model.attributes = [...model.attributes, {
      id: 5, name: 'Tono', kind: 'color', role: 'other', sort_order: 2,
      options: [{ id: 51, label: 'Cálido', swatch_hex: '#AA5500', sort_order: 0 }],
    }];
    model.variants = model.variants.map((variant) => bare({ ...variant, options: { '3': variant.options['3'], '5': 51 } }));
    model.finish_images = { '51': [picture(MANAGED(51))] };
    // «Tono» is not the colour dimension, so it lends no finish pictures; and being of the colour kind, the model
    // is not colourless either: no cover.
    expect(variantGallery(model, model.variants[0])).toEqual([]);
  });
});
