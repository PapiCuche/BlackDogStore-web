"use client";

import { useEffect, useState } from "react";
import { DEFAULT_CURRENCY, toItem } from "../lib/analytics/items";
import { track } from "../lib/analytics/service";
import { ProductImage } from "./ProductImage";
import Link from "next/link";
import { API_BASE } from "../lib/api";
import { fetchWithAuth } from "../lib/auth";
import { getSessionKey, emitCartChange } from "../lib/cart";
import { formatMoney } from "../lib/format";
import { ProductCard } from "./ProductCard";

type Category = { id: number; name: string; slug: string };

type Product = {
  id: number;
  slug: string;
  name: string;
  description?: string;
  price: number | string;
  inventory: number;
  image_url?: string;
  /** La galería que la tienda subió. `image_url` es su principal. */
  images?: { url: string; alt_text: string; is_primary: boolean; width: number; height: number }[];
  average_rating?: number | null;
  review_count?: number;
  category?: Category;
};

type Review = {
  id: number;
  author_name: string;
  rating: number;
  comment: string;
  created_at: string;
};

function StarDisplay({ rating, size = "sm" }: { rating: number; size?: "sm" | "md" | "lg" }) {
  const sizeClass = size === "lg" ? "text-xl" : size === "md" ? "text-base" : "text-sm";
  return (
    <span className={`${sizeClass} tracking-tight`} aria-label={`${rating} de 5 estrellas`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span key={n} className={n <= rating ? "text-foreground" : "text-muted/35"} aria-hidden="true">★</span>
      ))}
    </span>
  );
}

function StarPicker({ rating, onChange, labelledBy }: { rating: number; onChange: (rating: number) => void; labelledBy?: string }) {
  const [hover, setHover] = useState(0);
  return (
    <div className="flex gap-1" role="group" aria-label={labelledBy ? undefined : "Calificación"} aria-labelledby={labelledBy}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          onMouseEnter={() => setHover(n)}
          onMouseLeave={() => setHover(0)}
          onFocus={() => setHover(n)}
          onBlur={() => setHover(0)}
          onClick={() => onChange(n)}
          className="rounded-md px-0.5 text-2xl leading-none transition"
          aria-label={`${n} estrella${n === 1 ? "" : "s"}`}
          aria-pressed={rating === n}
        >
          <span className={(hover || rating) >= n ? "text-foreground" : "text-muted/35"} aria-hidden="true">★</span>
        </button>
      ))}
    </div>
  );
}

export default function ProductDetail({ product }: { product: Product }) {
  const [quantity, setQuantity] = useState(1);
  // La galería que la tienda subió; sin ella, la dirección de siempre.
  const gallery = product.images?.length
    ? product.images
    : product.image_url
      ? [{ url: product.image_url, alt_text: "", is_primary: true, width: 0, height: 0 }]
      : [];
  const [selected, setSelected] = useState(() => Math.max(0, gallery.findIndex((image) => image.is_primary)));
  const shown = gallery[selected] ?? gallery[0] ?? null;
  const [status, setStatus] = useState<string | null>(null);
  const [statusType, setStatusType] = useState<"success" | "error">("success");
  const [loading, setLoading] = useState(false);

  const [reviews, setReviews] = useState<Review[]>([]);
  const [reviewRating, setReviewRating] = useState(5);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewComment, setReviewComment] = useState("");
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewSuccess, setReviewSuccess] = useState(false);

  const [relatedProducts, setRelatedProducts] = useState<Product[]>([]);

  useEffect(() => {
    void fetchReviews();
    if (product.category) {
      fetch(`${API_BASE}/products/?category=${product.category.slug}`)
        .then((response) => response.json())
        .then((data) => {
          if (Array.isArray(data)) {
            setRelatedProducts(
              data.filter((candidate: Product) => candidate.id !== product.id).slice(0, 3),
            );
          }
        })
        .catch(() => {
          // Related products are optional; the main product remains usable.
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [product.id, product.category?.slug]);

  useEffect(() => {
    // Once per product shown — keyed on its id, so a re-render is not a second view.
    track({ name: "VIEW_ITEM", item: toItem(product), currency: DEFAULT_CURRENCY, value: Number(product.price) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [product.id]);

  async function fetchReviews() {
    try {
      const res = await fetch(`${API_BASE}/reviews/?product=${product.id}`);
      if (res.ok) setReviews(await res.json());
    } catch {
      // Reviews are secondary content; product purchase remains available.
    }
  }

  async function handleAddToCart() {
    setStatus(null);
    setLoading(true);
    try {
      const sessionKey = getSessionKey();
      // Con `fetchWithAuth`: el carrito es de la sesión del navegador, pero la
      // cookie de acceso viaja sola y con ella el servidor exige el token CSRF.
      const res = await fetchWithAuth(`${API_BASE}/cart/add/`, {
        method: "POST",
        body: JSON.stringify({ session_key: sessionKey, product: product.id, quantity }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || "No se pudo agregar al carrito.");
      }
      setStatusType("success");
      setStatus("Producto agregado al carrito.");
      emitCartChange();
      track({
        name: "ADD_TO_CART", item: toItem(product, quantity), currency: DEFAULT_CURRENCY,
        value: Math.round(Number(product.price) * quantity * 100) / 100,
      });
    } catch (error: unknown) {
      setStatusType("error");
      setStatus(error instanceof Error ? error.message : "Error al agregar.");
    } finally {
      setLoading(false);
    }
  }

  async function handleReviewSubmit(event: React.FormEvent) {
    event.preventDefault();
    setReviewSubmitting(true);
    setReviewError(null);
    try {
      const res = await fetchWithAuth(`${API_BASE}/reviews/`, {
        method: "POST",
        body: JSON.stringify({
          product: product.id,
          rating: reviewRating,
          comment: reviewComment,
        }),
      });
      if (res.ok) {
        setReviewSuccess(true);
        setReviewComment("");
        setReviewRating(5);
        await fetchReviews();
      } else if (res.status === 401 || res.status === 403) {
        setReviewError("Inicia sesión para publicar una reseña.");
      } else {
        setReviewError("No se pudo publicar la reseña.");
      }
    } catch {
      setReviewError("No se pudo publicar la reseña.");
    } finally {
      setReviewSubmitting(false);
    }
  }

  const inStock = product.inventory > 0;
  const lowStock = product.inventory > 0 && product.inventory <= 3;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-7xl px-6 py-10 lg:px-8 lg:py-14">
        <nav className="mb-8 flex flex-wrap items-center gap-2 text-xs font-medium text-muted" aria-label="Ruta del producto">
          <Link href="/" className="transition hover:text-foreground">Inicio</Link>
          <span aria-hidden="true">/</span>
          <Link href="/product" className="transition hover:text-foreground">Catálogo</Link>
          {product.category ? (
            <>
              <span aria-hidden="true">/</span>
              <Link href={`/product?category=${product.category.slug}`} className="transition hover:text-foreground">
                {product.category.name}
              </Link>
            </>
          ) : null}
          <span aria-hidden="true">/</span>
          <span className="text-foreground/80">{product.name}</span>
        </nav>

        <div className="grid gap-10 lg:grid-cols-[1.15fr_0.85fr] lg:gap-14">
          <div className="min-w-0">
          <div className="relative overflow-hidden rounded-[1.75rem] border border-bd-border bg-surface">
            {shown ? (
              <div data-testid="product-main-image" className="relative aspect-[4/3] min-h-80 lg:min-h-[520px]">
                <ProductImage
                  key={shown.url}
                  src={shown.url}
                  alt={shown.alt_text || product.name}
                  className="object-contain p-6 sm:p-10"
                  sizes="(max-width: 1024px) 100vw, 58vw"
                  priority
                />
              </div>
            ) : (
              <div className="flex aspect-[4/3] min-h-80 items-center justify-center lg:min-h-[520px]">
                <div className="flex h-28 w-28 items-center justify-center rounded-full border border-bd-border text-5xl font-black text-foreground/[0.10]" aria-hidden="true">
                  {product.name.trim().charAt(0).toUpperCase() || "·"}
                </div>
              </div>
            )}
          </div>
          {gallery.length > 1 ? (
            <div role="group" aria-label="Imágenes del producto" className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {gallery.map((image, index) => (
                <button
                  key={image.url}
                  type="button"
                  aria-label={`Ver imagen ${index + 1} de ${gallery.length}`}
                  aria-pressed={index === selected}
                  onClick={() => setSelected(index)}
                  className={`relative h-20 w-20 shrink-0 overflow-hidden rounded-xl border bg-surface transition-colors ${
                    index === selected ? "border-foreground" : "border-bd-border hover:border-foreground/40"
                  }`}
                >
                  <ProductImage src={image.url} alt="" sizes="80px" className="object-contain p-1.5" />
                </button>
              ))}
            </div>
          ) : null}
          </div>

          <div className="flex flex-col">
            {product.category ? (
              <Link
                href={`/product?category=${product.category.slug}`}
                className="w-fit text-[10px] font-bold uppercase tracking-[0.16em] text-muted transition hover:text-foreground"
              >
                {product.category.name}
              </Link>
            ) : null}

            <h1 className="mt-3 font-display text-4xl font-semibold leading-[1.08] tracking-tight text-foreground sm:text-5xl">
              {product.name}
            </h1>

            {product.average_rating !== null && product.average_rating !== undefined ? (
              <div className="mt-5 flex items-center gap-3">
                <StarDisplay rating={Math.round(product.average_rating)} size="md" />
                <span className="text-sm text-muted">
                  {product.average_rating.toFixed(1)} · {product.review_count ?? 0} reseña{product.review_count !== 1 ? "s" : ""}
                </span>
              </div>
            ) : null}

            <p className="mt-6 text-sm leading-7 text-muted sm:text-base">
              {product.description || "Consulta el detalle, precio y disponibilidad de este producto."}
            </p>

            <div className="mt-6 flex items-center gap-2 text-sm">
              <span
                className={`h-2 w-2 rounded-full ${inStock ? "bg-emerald-400" : "bg-muted/40"}`}
                aria-hidden="true"
              />
              <span className={inStock ? "text-foreground" : "text-muted"}>
                {!inStock
                  ? "Sin stock"
                  : lowStock
                    ? `Últimas ${product.inventory} unidades`
                    : "En stock"}
              </span>
            </div>

            <div className="mt-8 rounded-2xl border border-bd-border bg-surface p-5 sm:p-6">
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted">Precio</span>
              <p className="mt-1 font-display text-4xl font-extrabold tabular-nums tracking-[-0.04em] text-foreground sm:text-5xl">
                S/ {formatMoney(product.price)}
              </p>

              <div className="mt-6 flex items-center justify-between gap-4 border-t border-bd-border pt-5">
                <span id="qty-label" className="text-sm font-semibold text-muted">Cantidad</span>
                <div className="flex overflow-hidden rounded-xl border border-bd-border bg-background" role="group" aria-labelledby="qty-label">
                  <button
                    type="button"
                    onClick={() => setQuantity(Math.max(1, quantity - 1))}
                    disabled={!inStock || quantity <= 1}
                    className="flex h-11 w-11 items-center justify-center text-foreground transition hover:bg-foreground/[0.05] disabled:opacity-30"
                    aria-label="Reducir cantidad"
                  >
                    −
                  </button>
                  <span className="flex h-11 w-12 items-center justify-center border-x border-bd-border text-sm font-bold tabular-nums text-foreground">
                    {quantity}
                  </span>
                  <button
                    type="button"
                    onClick={() => setQuantity(Math.min(product.inventory, quantity + 1))}
                    disabled={!inStock || quantity >= product.inventory}
                    className="flex h-11 w-11 items-center justify-center text-foreground transition hover:bg-foreground/[0.05] disabled:opacity-30"
                    aria-label="Aumentar cantidad"
                  >
                    +
                  </button>
                </div>
              </div>

              <button
                type="button"
                onClick={handleAddToCart}
                disabled={loading || !inStock}
                className="mt-5 w-full rounded-full bg-foreground px-4 py-3.5 text-sm font-extrabold uppercase tracking-[0.08em] text-background transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {loading ? "Agregando…" : !inStock ? "Sin stock" : "Agregar al carrito"}
              </button>

              <Link
                href="/cart"
                className="mt-3 block w-full rounded-xl border border-bd-border px-4 py-3 text-center text-sm font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25"
              >
                Ver carrito
              </Link>

              {status ? (
                <div
                  role={statusType === "error" ? "alert" : "status"}
                  className={`mt-4 rounded-xl border p-3 text-sm ${
                    statusType === "success"
                      ? "border-bd-border bg-background text-foreground"
                      : "border-danger-border bg-danger-surface text-danger"
                  }`}
                >
                  {status}
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <section className="mt-16 border-t border-bd-border pt-10 sm:mt-20 sm:pt-12">
          <div className="flex items-end justify-between gap-4">
            <div>
              <span className="section-label">Opiniones</span>
              <h2 className="mt-2 font-display text-3xl font-semibold tracking-tight text-foreground">
                Reseñas
              </h2>
            </div>
            {reviews.length > 0 ? <span className="text-sm text-muted">{reviews.length}</span> : null}
          </div>

          {reviews.length === 0 ? (
            <p className="mt-6 text-sm text-muted">Todavía no hay reseñas publicadas para este producto.</p>
          ) : (
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              {reviews.map((review) => (
                <article key={review.id} className="rounded-2xl border border-bd-border bg-surface p-5">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="font-semibold text-foreground">{review.author_name || "Cliente"}</p>
                      <StarDisplay rating={review.rating} />
                    </div>
                    <time className="shrink-0 text-xs text-muted" dateTime={review.created_at}>
                      {new Date(review.created_at).toLocaleDateString("es-PE", {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </time>
                  </div>
                  {review.comment ? <p className="mt-3 text-sm leading-6 text-muted">{review.comment}</p> : null}
                </article>
              ))}
            </div>
          )}

          <div className="mt-8 rounded-2xl border border-bd-border bg-surface p-5 sm:p-6">
            <h3 className="font-display text-xl font-extrabold uppercase text-foreground">Deja tu reseña</h3>
            {reviewSuccess ? (
              <div className="mt-4 rounded-xl border border-bd-border bg-background p-4 text-sm text-foreground" role="status">
                Gracias. Tu reseña fue publicada.
              </div>
            ) : (
              <form onSubmit={handleReviewSubmit} className="mt-5 space-y-4">
                <div>
                  <p id="review-rating-label" className="mb-2 block text-sm font-semibold text-muted">Calificación</p>
                  <StarPicker rating={reviewRating} onChange={setReviewRating} labelledBy="review-rating-label" />
                </div>
                <div>
                  <label htmlFor="review-comment" className="mb-1.5 block text-sm font-semibold text-muted">Comentario (opcional)</label>
                  <textarea
                    id="review-comment"
                    name="review_comment"
                    value={reviewComment}
                    onChange={(event) => setReviewComment(event.target.value)}
                    placeholder="¿Qué te pareció el producto?"
                    rows={3}
                    className="w-full resize-none rounded-xl border border-bd-border bg-background px-4 py-3 text-sm text-foreground placeholder:text-muted/60 focus:border-foreground/25 focus:outline-none"
                  />
                </div>
                {reviewError ? <p className="text-sm text-danger" role="alert">{reviewError}</p> : null}
                <button
                  type="submit"
                  disabled={reviewSubmitting}
                  className="rounded-full bg-foreground px-5 py-3 text-sm font-bold uppercase tracking-[0.06em] text-background transition hover:opacity-90 disabled:opacity-50"
                >
                  {reviewSubmitting ? "Enviando…" : "Publicar reseña"}
                </button>
              </form>
            )}
          </div>
        </section>

        {relatedProducts.length > 0 ? (
          <section className="mb-8 mt-16 border-t border-bd-border pt-10 sm:mt-20 sm:pt-12">
            <span className="section-label">Explorar</span>
            {/* A 320 px «RELACIONADOS» no cabía a 1.875rem y desbordaba la página. */}
            <h2 className="mt-2 font-display text-[min(1.875rem,8vw)] font-semibold leading-9 tracking-tight text-foreground">
              Productos relacionados
            </h2>
            <div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {relatedProducts.map((related) => (
                <ProductCard key={related.id} {...related} />
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
