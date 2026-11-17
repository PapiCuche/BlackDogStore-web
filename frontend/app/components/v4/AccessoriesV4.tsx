"use client";

/**
 * P2 · F3 — «Compatible con {modelo}» in the V4 model page.
 *
 * ONLY WHAT THE API SAYS. The block is built from `model.accessories`
 * (`accessory_compat.for_model_public`): the shop chose which accessories fit
 * the model and in what order. With none, nothing is drawn — no heading, no
 * empty state.
 *
 * Each card says what a variant says, with the same words: the price (the
 * offer's when it is «bajo pedido»), the availability (`availabilityLabel`)
 * and a picture captioned «Imagen referencial» when the shop did not upload it
 * (`photoLabel`). Never a quantity, never a cost. The card's link goes to the
 * product page of today's shop (`/product/<slug>`, V3) until V4 has its own.
 *
 * Owner's decision 4 of `docs/p2-tienda-v4-plan.md` (whether sold-out
 * accessories are shown) is still open. Default until it is taken: they are
 * shown, said «Agotado», and their button adds nothing — as a sold-out variant.
 */

import Link from "next/link";
import { ProductImage } from "../ProductImage";
import { OnOrderOfferPrompt } from "../OnOrderOffer";
import { formatMoney } from "../../lib/format";
import { SOLD_OUT_LABEL, availabilityLabel, type ModelAccessory } from "../../lib/catalog-models";
import {
  accessoryPicture,
  photoLabel,
  useAccessoryCart,
  type ConfiguratorStatus,
} from "../../lib/model-configurator";

const CARD_SIZES = "(min-width: 1024px) 280px, (min-width: 640px) 45vw, 100vw";

function productHref(slug: string): string {
  return `/product/${encodeURIComponent(slug)}`;
}

function CardStatus({ status }: { status: ConfiguratorStatus }) {
  if (!status) return null;
  return (
    <p
      role={status.kind === "error" ? "alert" : "status"}
      className={`mt-3 text-sm ${status.kind === "error" ? "text-danger" : "text-foreground"}`}
    >
      {status.message}
      {status.kind === "success" ? (
        <>
          {" "}
          <Link href="/cart" className="v4-link">Ver carrito</Link>
        </>
      ) : null}
    </p>
  );
}

function AccessoryCardV4({ accessory }: { accessory: ModelAccessory }) {
  const cart = useAccessoryCart(accessory);
  const picture = accessoryPicture(accessory);
  const caption = picture ? photoLabel(picture.url, "catalog") : null;
  const soldOut = !cart.purchasable;
  const href = productHref(accessory.slug);

  return (
    <article aria-label={accessory.name} className="v4-card">
      {/* The picture is not a second link: the name below is the one way to the product page. */}
      <div className="v4-card-stage relative aspect-square">
        {picture ? (
          <ProductImage
            src={picture.url}
            alt={picture.alt_text || accessory.name}
            sizes={CARD_SIZES}
            className={`object-contain p-6 ${soldOut ? "opacity-50" : ""}`}
          />
        ) : (
          <span aria-hidden="true" className="flex h-full items-center justify-center text-4xl font-bold text-[color:var(--v4-line-strong)]">
            {accessory.name.trim().charAt(0).toUpperCase() || "·"}
          </span>
        )}
        {caption ? <span className="v4-image-label">{caption}</span> : null}
      </div>
      <div className="flex flex-1 flex-col p-5">
        <h3 className="v4-data leading-snug">
          <Link href={href} className="hover:underline">{accessory.name}</Link>
        </h3>
        <p className={`mt-1 ${soldOut ? "v4-sold-out" : "v4-small"}`}>{availabilityLabel(accessory)}</p>
        <p className="v4-price mt-3">S/ {formatMoney(cart.price)}</p>
        <div className="mt-auto pt-4">
          <button
            type="button"
            onClick={() => void cart.add(cart.onOrder !== null)}
            disabled={cart.busy || soldOut}
            aria-label={`${cart.busy ? "Agregando" : cart.onOrder ? "Pedir bajo pedido" : soldOut ? SOLD_OUT_LABEL : "Agregar al carrito"}: ${accessory.name}`}
            className="v4-cta"
          >
            {cart.busy ? "Agregando…" : cart.onOrder ? "Pedir bajo pedido" : soldOut ? SOLD_OUT_LABEL : "Agregar al carrito"}
          </button>
          {cart.offer ? (
            <OnOrderOfferPrompt offer={cart.offer} busy={cart.busy} onAccept={() => void cart.add(true)} />
          ) : null}
          <CardStatus status={cart.status} />
        </div>
      </div>
    </article>
  );
}

export function AccessoriesV4({ modelName, accessories }: { modelName: string; accessories?: ModelAccessory[] }) {
  const shown = accessories ?? [];
  if (shown.length === 0) return null;
  const title = `Compatible con ${modelName}`;
  return (
    <section data-testid="v4-accessories" aria-label={title} className="mt-20 border-t border-bd-border pt-12">
      <h2 className="v4-heading">{title}</h2>
      <ul className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {shown.map((accessory) => (
          <li key={accessory.id} className="min-w-0">
            <AccessoryCardV4 accessory={accessory} />
          </li>
        ))}
      </ul>
    </section>
  );
}

export default AccessoriesV4;
