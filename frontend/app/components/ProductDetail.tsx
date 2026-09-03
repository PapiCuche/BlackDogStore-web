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
        <span key={n} className={n <= rating ? "text-white" : "text-zinc-700"}>★</span>
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
          className="text-2xl leading-none transition"
        >
          <span className={(hover || rating) >= n ? "text-white" : "text-zinc-700"}>★</span>
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
    <div className="min-h-screen bg-background text-white">
      <div className="mx-auto max-w-6xl px-6 py-12 lg:px-8">

        {/* Breadcrumb */}
        <nav className="mb-8 flex items-center gap-2 text-sm text-zinc-600">
          <Link href="/" className="transition hover:text-white">Inicio</Link>
          <span>/</span>
          <Link href="/product" className="transition hover:text-white">Catálogo</Link>
          {product.category && (
            <>
              <span>/</span>
              <Link href={`/product?category=${product.category.slug}`} className="transition hover:text-white">
                {product.category.name}
              </Link>
            </>
          )}
          <span>/</span>
          <span className="text-zinc-400">{product.name}</span>
        </nav>

        {/* Main grid */}
        <div className="grid gap-10 lg:grid-cols-[1.5fr_1fr]">

          {/* Image */}
          <div className="relative overflow-hidden rounded-2xl border border-white/[0.08] bg-surface">
            {product.image_url ? (
              <div className="relative h-80 lg:h-[480px]">
                <Image
                  src={product.image_url}
                  alt={product.name}
                  fill
                  className="object-contain p-6"
                  sizes="(max-width: 1024px) 100vw, 60vw"
                  priority
                />
              </div>
            ) : (
              <div className="flex h-80 items-center justify-center lg:h-[480px]">
                <img
                  src="/assets/branding/logo-icon.png"
                  alt=""
                  className="h-20 w-20 object-contain opacity-[0.06] invert"
                />
              </div>
            )}
          </div>

          {/* Details */}
          <div className="flex flex-col gap-5">
            {product.category && (
              <Link
                href={`/product?category=${product.category.slug}`}
                className="inline-flex w-fit rounded-full border border-white/10 bg-white/[0.06] px-3 py-1 text-xs font-bold uppercase tracking-widest text-zinc-400 transition hover:border-white/25 hover:text-white"
              >
                {product.category.name}
              </Link>
            )}

            <h1 className="font-display text-4xl font-black uppercase leading-tight text-white lg:text-5xl">
              {product.name}
            </h1>

            {product.average_rating !== null && product.average_rating !== undefined ? (
              <div className="flex items-center gap-3">
                <StarDisplay rating={Math.round(product.average_rating)} size="md" />
                <span className="text-sm text-zinc-500">
                  {product.average_rating.toFixed(1)} — {product.review_count} reseña{product.review_count !== 1 ? "s" : ""}
                </span>
              </div>
            ) : null}

            <p className="text-sm leading-7 text-zinc-400">
              {product.description || "Producto de calidad premium para tu dispositivo Apple."}
            </p>

            {/* Stock status */}
            <div className="flex items-center gap-2 text-sm">
              <span className={`h-2 w-2 rounded-full ${inStock ? "bg-white" : "bg-zinc-700"}`} />
              <span className={inStock ? "text-white" : "text-zinc-600"}>
                {!inStock
                  ? "Sin stock"
                  : lowStock
                  ? `Últimas ${product.inventory} unidades`
                  : "En stock — envío a todo Perú"}
              </span>
            </div>

            {/* Buy box */}
            <div className="rounded-2xl border border-white/[0.08] bg-surface p-6">
              <div className="flex items-baseline gap-2">
                <span className="text-xs font-bold uppercase tracking-widest text-zinc-600">S/</span>
                <span className="font-display text-5xl font-black text-white">{formatMoney(product.price)}</span>
              </div>
              <p className="mt-1 text-[10px] uppercase tracking-widest text-zinc-700">Precio incluye IGV</p>

              <div className="mt-5 flex items-center gap-3">
                <label className="text-sm font-medium text-zinc-400">Cantidad</label>
                <div className="flex items-center">
                  <button
                    type="button"
                    onClick={() => setQuantity(Math.max(1, quantity - 1))}
                    disabled={!inStock}
                    className="flex h-9 w-9 items-center justify-center rounded-l-xl border border-white/10 bg-white/[0.04] text-white transition hover:bg-white/[0.08] disabled:opacity-40"
                  >
                    −
                  </button>
                  <span className="flex h-9 w-12 items-center justify-center border-y border-white/10 bg-white/[0.04] text-sm font-bold text-white">
                    {quantity}
                  </span>
                  <button
                    type="button"
                    onClick={() => setQuantity(Math.min(product.inventory, quantity + 1))}
                    disabled={!inStock || quantity >= product.inventory}
                    className="flex h-9 w-9 items-center justify-center rounded-r-xl border border-white/10 bg-white/[0.04] text-white transition hover:bg-white/[0.08] disabled:opacity-40"
                  >
                    +
                  </button>
                </div>
              </div>

              <button
                type="button"
                onClick={handleAddToCart}
                disabled={loading || !inStock}
                className="mt-5 w-full rounded-full bg-white px-4 py-3.5 text-sm font-black uppercase tracking-widest text-background transition hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {loading ? "Agregando..." : !inStock ? "Sin stock" : "Agregar al carrito"}
              </button>

              <Link
                href="/cart"
                className="mt-3 block w-full rounded-full border border-white/10 bg-white/[0.04] px-4 py-3 text-center text-sm font-bold uppercase tracking-widest text-white transition hover:border-white/20 hover:bg-white/[0.08]"
              >
                Ver carrito
              </Link>

              {status && (
                <div
                  className={`mt-4 rounded-xl border p-3 text-sm ${
                    statusType === "success"
                      ? "border-white/10 bg-white/[0.04] text-zinc-200"
                      : "border-red-500/30 bg-red-500/10 text-red-300"
                  }`}
                >
                  {status}
                </div>
              )}
            </div>

            {/*
              "Repuesto original Apple" afirmaba procedencia de fábrica en TODA
              ficha, incluidas las de accesorios y equipos seminuevos. Y el
              plazo de garantía estaba compilado mientras la política que lo
              respalda sigue pendiente de redactar.

              Queda lo que es cierto de cualquier producto de esta tienda; el
              plazo sale de la configuración del tenant o no se muestra.
            */}
            <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-zinc-600">
              <span>✓ Equipo verificado antes de la entrega</span>
              <span>✓ Envío a todo Perú</span>
              <span>✓ Pago seguro</span>
            </div>
          </div>
        </div>

        {/* Reviews */}
        <section className="mt-16">
          <div className="flex items-center gap-3">
            <h2 className="font-display text-3xl font-black uppercase text-white">Reseñas</h2>
            {reviews.length > 0 && (
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-0.5 text-xs font-bold text-zinc-500">
                {reviews.length}
              </span>
            )}
          </div>

          {reviews.length === 0 ? (
            <p className="mt-4 text-sm text-zinc-600">Sé el primero en reseñar este producto.</p>
          ) : (
            <div className="mt-6 space-y-4">
              {reviews.map((review) => (
                <div key={review.id} className="rounded-2xl border border-white/[0.08] bg-surface p-5">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="font-bold text-white">{review.author_name || "Cliente verificado"}</p>
                      <StarDisplay rating={review.rating} size="sm" />
                    </div>
                    <span className="shrink-0 text-xs text-zinc-600">
                      {new Date(review.created_at).toLocaleDateString("es-PE", {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                  </div>
                  {review.comment && (
                    <p className="mt-3 text-sm leading-6 text-zinc-400">{review.comment}</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Review form */}
          <div className="mt-8 rounded-2xl border border-white/[0.08] bg-surface p-6">
            <h3 className="font-display mb-4 text-xl font-black uppercase text-white">Deja tu reseña</h3>
            {reviewSuccess ? (
              <div className="rounded-xl border border-white/10 bg-white/[0.04] p-4 text-sm text-zinc-200">
                ¡Gracias por tu reseña! Ya está publicada.
              </div>
            ) : (
              <form onSubmit={handleReviewSubmit} className="space-y-4">
                <div>
                  <label className="mb-2 block text-sm font-medium text-zinc-400">Calificación</label>
                  <StarPicker rating={reviewRating} onChange={setReviewRating} />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-zinc-400">Comentario (opcional)</label>
                  <textarea
                    value={reviewComment}
                    onChange={(e) => setReviewComment(e.target.value)}
                    placeholder="¿Qué te pareció el producto?"
                    rows={3}
                    className="w-full resize-none rounded-xl border border-white/10 bg-white/[0.04] px-4 py-3 text-sm text-white placeholder-zinc-700 focus:border-white/25 focus:outline-none"
                  />
                </div>
                {reviewError && (
                  <p className="text-sm text-rose-300">{reviewError}</p>
                )}
                <button
                  type="submit"
                  disabled={reviewSubmitting}
                  className="rounded-full bg-white px-6 py-2.5 text-sm font-black uppercase tracking-widest text-background transition hover:bg-zinc-200 disabled:opacity-50"
                >
                  {reviewSubmitting ? "Enviando..." : "Publicar reseña"}
                </button>
              </form>
            )}
          </div>
        </section>

        {/* Related products */}
        {relatedProducts.length > 0 && (
          <section className="mb-8 mt-16">
            <h2 className="font-display mb-6 text-3xl font-black uppercase text-white">
              Productos relacionados
            </h2>
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {relatedProducts.map((p) => (
                <ProductCard key={p.id} {...p} />
              ))}
            </div>
          </section>
        )}

      </div>
    </div>
  );
}
