"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
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
  const sz = size === "lg" ? "text-xl" : size === "md" ? "text-base" : "text-sm";
  return (
    <span className={`${sz} tracking-tight`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span key={n} className={n <= rating ? "text-accent" : "text-muted-foreground/30"}>★</span>
      ))}
    </span>
  );
}

function StarPicker({ rating, onChange }: { rating: number; onChange: (r: number) => void }) {
  const [hover, setHover] = useState(0);
  return (
    <div className="flex gap-1">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          onMouseEnter={() => setHover(n)}
          onMouseLeave={() => setHover(0)}
          onClick={() => onChange(n)}
          className="text-2xl leading-none transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          aria-label={`${n} de 5 estrellas`}
        >
          <span className={(hover || rating) >= n ? "text-accent" : "text-muted-foreground/30"}>★</span>
        </button>
      ))}
    </div>
  );
}

export default function ProductDetail({ product }: { product: Product }) {
  const [quantity, setQuantity] = useState(1);
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
    fetchReviews();
    if (product.category) {
      fetch(`${API_BASE}/products/?category=${product.category.slug}`)
        .then((r) => r.json())
        .then((data) => setRelatedProducts(data.filter((p: Product) => p.id !== product.id).slice(0, 3)))
        .catch(() => {});
    }
  }, [product.id]);

  async function fetchReviews() {
    try {
      const res = await fetch(`${API_BASE}/reviews/?product=${product.id}`);
      if (res.ok) setReviews(await res.json());
    } catch {}
  }

  async function handleAddToCart() {
    setStatus(null);
    setLoading(true);
    try {
      const sessionKey = getSessionKey();
      const res = await fetch(`${API_BASE}/cart/add/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_key: sessionKey, product: product.id, quantity }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || "No se pudo agregar al carrito.");
      }
      setStatusType("success");
      setStatus("Producto agregado al carrito.");
      emitCartChange();
    } catch (e: unknown) {
      setStatusType("error");
      setStatus(e instanceof Error ? e.message : "Error al agregar.");
    } finally {
      setLoading(false);
    }
  }

  async function handleReviewSubmit(e: React.FormEvent) {
    e.preventDefault();
    setReviewSubmitting(true);
    setReviewError(null);
    try {
      // `fetchWithAuth`, not bare `fetch`. Posting a review requires a session,
      // and a plain fetch sends neither the cookie nor the CSRF token — which is
      // why this form answered 401 for every visitor who ever used it.
      //
      // `author_name` is no longer sent: the server takes the name from the
      // account. It was free text on an authenticated endpoint, so anyone could
      // publish under the shop's own support name.
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
        fetchReviews();
      } else if (res.status === 401 || res.status === 403) {
        setReviewError("Inicia sesión para publicar una reseña.");
      } else {
        setReviewError("No se pudo publicar la reseña.");
      }
    } catch {
      setReviewError("No se pudo publicar la reseña.");
    }
    setReviewSubmitting(false);
  }

  const inStock = product.inventory > 0;
  const lowStock = product.inventory > 0 && product.inventory <= 3;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-7xl px-6 py-10 lg:px-8 lg:py-12">
        <nav className="mb-8 flex flex-wrap items-center gap-2 text-xs text-muted-foreground" aria-label="Migas de pan">
          <Link href="/" className="transition hover:text-foreground">Inicio</Link>
          <span aria-hidden="true">/</span>
          <Link href="/product" className="transition hover:text-foreground">Catálogo</Link>
          {product.category ? (
            <>
              <span aria-hidden="true">/</span>
              <Link
                href={`/product?category=${product.category.slug}`}
                className="transition hover:text-foreground"
              >
                {product.category.name}
              </Link>
            </>
          ) : null}
          <span aria-hidden="true">/</span>
          <span className="max-w-64 truncate text-foreground">{product.name}</span>
        </nav>

        <div className="grid gap-10 lg:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)] lg:gap-12">
          <div className="relative overflow-hidden rounded-2xl border border-bd-border bg-surface">
            <div className="absolute left-0 top-0 z-10 h-px w-24 bg-accent" aria-hidden="true" />
            {product.image_url ? (
              <div className="relative aspect-[4/3] min-h-80">
                <Image
                  src={product.image_url}
                  alt={product.name}
                  fill
                  className="object-contain p-6 sm:p-10"
                  sizes="(max-width: 1024px) 100vw, 62vw"
                  priority
                />
              </div>
            ) : (
              <div className="flex aspect-[4/3] min-h-80 items-center justify-center text-muted-foreground" aria-hidden="true">
                <svg className="h-14 w-14" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.3} d="M20 7l-8-4-8 4m16 0-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
                </svg>
              </div>
            )}
          </div>

          <div className="flex flex-col">
            {product.category ? (
              <Link
                href={`/product?category=${product.category.slug}`}
                className="w-fit text-[10px] font-bold uppercase tracking-[0.2em] text-accent transition hover:text-foreground"
              >
                {product.category.name}
              </Link>
            ) : null}

            <h1 className="mt-3 font-display text-4xl font-black uppercase leading-[0.96] tracking-tight text-foreground sm:text-5xl">
              {product.name}
            </h1>

            {product.average_rating !== null && product.average_rating !== undefined ? (
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <StarDisplay rating={Math.round(product.average_rating)} size="md" />
                <span className="text-sm text-muted-foreground">
                  {product.average_rating.toFixed(1)} · {product.review_count || 0} reseña{product.review_count !== 1 ? "s" : ""}
                </span>
              </div>
            ) : null}

            <p className="mt-6 text-sm leading-7 text-muted-foreground">
              {product.description || "Consulta características, disponibilidad y condiciones de este producto."}
            </p>

            <div className="mt-6 flex items-center gap-2 text-sm">
              <span
                className={`h-2 w-2 rounded-full ${
                  !inStock ? "bg-red-400" : lowStock ? "bg-amber-300" : "bg-foreground"
                }`}
                aria-hidden="true"
              />
              <span className={!inStock ? "text-red-300" : lowStock ? "text-amber-200" : "text-foreground"}>
                {!inStock
                  ? "Sin stock"
                  : lowStock
                    ? `Últimas ${product.inventory} unidades`
                    : "En stock"}
              </span>
            </div>

            <div className="mt-7 rounded-2xl border border-bd-border bg-surface p-6">
              <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted-foreground">
                Precio
              </span>
              <div className="mt-1 font-display text-4xl font-black text-foreground sm:text-5xl">
                S/ {formatMoney(product.price)}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">El importe final y la entrega se confirman en checkout.</p>

              <div className="mt-6 flex items-center justify-between gap-4 border-t border-bd-border pt-5">
                <span className="text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground">
                  Cantidad
                </span>
                <div className="flex items-center overflow-hidden rounded-lg border border-bd-border bg-background">
                  <button
                    type="button"
                    onClick={() => setQuantity(Math.max(1, quantity - 1))}
                    disabled={!inStock || quantity <= 1}
                    className="flex h-10 w-10 items-center justify-center text-foreground transition hover:bg-surface-elevated disabled:opacity-30"
                    aria-label="Reducir cantidad"
                  >
                    −
                  </button>
                  <span className="flex h-10 w-12 items-center justify-center border-x border-bd-border text-sm font-bold text-foreground">
                    {quantity}
                  </span>
                  <button
                    type="button"
                    onClick={() => setQuantity(Math.min(product.inventory, quantity + 1))}
                    disabled={!inStock || quantity >= product.inventory}
                    className="flex h-10 w-10 items-center justify-center text-foreground transition hover:bg-surface-elevated disabled:opacity-30"
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
                className="mt-5 w-full rounded-xl bg-primary px-4 py-3.5 text-sm font-black uppercase tracking-[0.12em] text-background transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                {loading ? "Agregando…" : !inStock ? "Sin stock" : "Agregar al carrito"}
              </button>

              <Link
                href="/cart"
                className="mt-3 block w-full rounded-xl border border-bd-border bg-background px-4 py-3 text-center text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground transition hover:border-accent/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Ver carrito
              </Link>

              {status ? (
                <div
                  role="status"
                  className={`mt-4 rounded-xl border p-3 text-sm ${
                    statusType === "success"
                      ? "border-bd-border bg-background text-foreground"
                      : "border-red-500/30 bg-red-500/10 text-red-300"
                  }`}
                >
                  {status}
                </div>
              ) : null}
            </div>

            <div className="mt-5 border-l border-accent/60 pl-4 text-xs leading-5 text-muted-foreground">
              Disponibilidad, entrega y políticas aplicables se muestran o confirman durante el proceso de compra.
            </div>
          </div>
        </div>

        <section className="mt-16 border-t border-bd-border pt-12">
          <div className="flex items-end justify-between gap-4">
            <div>
              <span className="section-label">Opiniones</span>
              <h2 className="mt-2 font-display text-3xl font-black uppercase text-foreground">Reseñas</h2>
            </div>
            {reviews.length > 0 ? (
              <span className="rounded-full border border-bd-border bg-surface px-3 py-1 text-xs font-bold text-muted-foreground">
                {reviews.length}
              </span>
            ) : null}
          </div>

          {reviews.length === 0 ? (
            <p className="mt-5 text-sm text-muted-foreground">Todavía no hay reseñas publicadas.</p>
          ) : (
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              {reviews.map((review) => (
                <article key={review.id} className="rounded-xl border border-bd-border bg-surface p-5">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="font-bold text-foreground">{review.author_name || "Cliente"}</p>
                      <StarDisplay rating={review.rating} size="sm" />
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {new Date(review.created_at).toLocaleDateString("es-PE", {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                  </div>
                  {review.comment ? (
                    <p className="mt-3 text-sm leading-6 text-muted-foreground">{review.comment}</p>
                  ) : null}
                </article>
              ))}
            </div>
          )}

          <div className="mt-8 rounded-xl border border-bd-border bg-surface p-6">
            <h3 className="font-display text-xl font-black uppercase text-foreground">Deja tu reseña</h3>
            {reviewSuccess ? (
              <div className="mt-4 rounded-xl border border-bd-border bg-background p-4 text-sm text-foreground" role="status">
                Gracias. Tu reseña ya está publicada.
              </div>
            ) : (
              <form onSubmit={handleReviewSubmit} className="mt-5 space-y-4">
                <div>
                  <label className="mb-2 block text-sm font-medium text-muted-foreground">Calificación</label>
                  <StarPicker rating={reviewRating} onChange={setReviewRating} />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-muted-foreground">Comentario (opcional)</label>
                  <textarea
                    value={reviewComment}
                    onChange={(e) => setReviewComment(e.target.value)}
                    placeholder="Cuéntanos tu experiencia con el producto"
                    rows={3}
                    className="w-full resize-none rounded-xl border border-bd-border bg-background px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20"
                  />
                </div>
                {reviewError ? <p className="text-sm text-red-300">{reviewError}</p> : null}
                <button
                  type="submit"
                  disabled={reviewSubmitting}
                  className="rounded-xl bg-primary px-6 py-3 text-sm font-black uppercase tracking-[0.12em] text-background transition hover:opacity-90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {reviewSubmitting ? "Enviando…" : "Publicar reseña"}
                </button>
              </form>
            )}
          </div>
        </section>

        {relatedProducts.length > 0 ? (
          <section className="mb-8 mt-16 border-t border-bd-border pt-12">
            <span className="section-label">También puedes revisar</span>
            <h2 className="mt-2 font-display text-3xl font-black uppercase text-foreground">
              Productos relacionados
            </h2>
            <div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {relatedProducts.map((p) => (
                <ProductCard key={p.id} {...p} />
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
