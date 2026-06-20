"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import Link from "next/link";
import { API_BASE } from "../lib/api";
import { getSessionKey, emitCartChange } from "../lib/cart";
import { formatMoney } from "../lib/format";
import { StarRating } from "./StarRating";
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

export default function ProductDetail({ product }: { product: Product }) {
  const [quantity, setQuantity] = useState(1);
  const [status, setStatus] = useState<string | null>(null);
  const [statusType, setStatusType] = useState<"success" | "error">("success");
  const [loading, setLoading] = useState(false);

  const [reviews, setReviews] = useState<Review[]>([]);
  const [reviewRating, setReviewRating] = useState(5);
  const [reviewName, setReviewName] = useState("");
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
    if (!reviewName.trim()) return;
    setReviewSubmitting(true);
    try {
      const res = await fetch(`${API_BASE}/reviews/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          product: product.id,
          author_name: reviewName,
          rating: reviewRating,
          comment: reviewComment,
        }),
      });
      if (res.ok) {
        setReviewSuccess(true);
        setReviewName("");
        setReviewComment("");
        setReviewRating(5);
        fetchReviews();
      }
    } catch {}
    setReviewSubmitting(false);
  }

  const avgRating = product.average_rating;

  return (
    <div className="min-h-screen bg-zinc-950 px-6 py-12">
      <div className="mx-auto max-w-6xl">

        {/* Breadcrumb */}
        <nav className="mb-8 flex items-center gap-2 text-sm text-slate-500">
          <Link href="/" className="hover:text-emerald-400 transition">Inicio</Link>
          <span>/</span>
          <Link href="/product" className="hover:text-emerald-400 transition">Catálogo</Link>
          <span>/</span>
          <span className="text-slate-300">{product.name}</span>
        </nav>

        {/* Main grid */}
        <div className="grid gap-10 lg:grid-cols-[1.5fr_1fr]">

          {/* Image */}
          <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-slate-800 via-slate-900 to-zinc-900">
            {product.image_url ? (
              <div className="relative h-80 lg:h-[480px]">
                <Image
                  src={product.image_url}
                  alt={product.name}
                  fill
                  className="object-contain p-4"
                  sizes="(max-width: 1024px) 100vw, 60vw"
                  priority
                />
              </div>
            ) : (
              <>
                <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_40%,_rgba(16,185,129,0.08),_transparent_60%)]" />
                <div className="flex h-80 items-center justify-center lg:h-[480px]">
                  <svg className="h-28 w-28 text-slate-700" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={0.75} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                </div>
              </>
            )}
          </div>

          {/* Details */}
          <div className="flex flex-col gap-5">
            {product.category && (
              <Link
                href={`/product?category=${product.category.slug}`}
                className="inline-flex w-fit rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 transition"
              >
                {product.category.name}
              </Link>
            )}

            <h1 className="text-3xl font-bold text-white lg:text-4xl leading-tight">{product.name}</h1>

            {avgRating !== null && avgRating !== undefined ? (
              <div className="flex items-center gap-3">
                <StarRating rating={Math.round(avgRating)} size="md" />
                <span className="text-sm text-slate-400">{avgRating.toFixed(1)} de 5 — {product.review_count} reseña{product.review_count !== 1 ? "s" : ""}</span>
              </div>
            ) : null}

            <p className="text-sm leading-7 text-slate-400">
              {product.description || "Producto de calidad premium para tu dispositivo Apple."}
            </p>

            <div className="flex items-center gap-2 text-sm text-slate-500">
              <svg className="h-4 w-4 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span className={product.inventory > 0 ? "text-emerald-400" : "text-red-400"}>
                {product.inventory > 0 ? `${product.inventory} unidades disponibles` : "Sin stock"}
              </span>
            </div>

            {/* Buy box */}
            <div className="rounded-2xl border border-white/10 bg-white/5 p-6">
              <div className="text-4xl font-bold text-white">
                S/ <span className="text-emerald-400">{formatMoney(product.price)}</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">Precio incluye IGV</p>

              <div className="mt-5 flex items-center gap-3">
                <label className="text-sm font-medium text-slate-300">Cantidad</label>
                <input
                  type="number"
                  min={1}
                  max={product.inventory}
                  value={quantity}
                  onChange={(e) => setQuantity(Math.max(1, Math.min(product.inventory, Number(e.target.value))))}
                  className="w-20 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-center text-white focus:border-emerald-500/50 focus:outline-none"
                />
              </div>

              <button
                type="button"
                onClick={handleAddToCart}
                disabled={loading || product.inventory === 0}
                className="mt-5 w-full rounded-full bg-emerald-500 px-4 py-3 text-sm font-semibold text-white shadow-lg transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loading ? "Agregando..." : product.inventory === 0 ? "Sin stock" : "Agregar al carrito"}
              </button>

              <Link href="/cart" className="mt-3 block w-full rounded-full border border-white/10 bg-white/5 px-4 py-3 text-center text-sm font-semibold text-white transition hover:border-white/20 hover:bg-white/10">
                Ver carrito
              </Link>

              {status && (
                <div className={`mt-4 rounded-xl border p-3 text-sm ${statusType === "success" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : "border-red-500/30 bg-red-500/10 text-red-300"}`}>
                  {status}
                </div>
              )}
            </div>

            {/* Trust badges */}
            <div className="flex flex-wrap gap-4 text-xs text-slate-500">
              <span className="flex items-center gap-1"><span className="text-emerald-400">✓</span> Repuesto original Apple</span>
              <span className="flex items-center gap-1"><span className="text-emerald-400">✓</span> Garantía de 6 meses</span>
              <span className="flex items-center gap-1"><span className="text-emerald-400">✓</span> Envío a todo Perú</span>
              <span className="flex items-center gap-1"><span className="text-emerald-400">✓</span> Pago seguro con Stripe</span>
            </div>
          </div>
        </div>

        {/* Reviews */}
        <section className="mt-16">
          <h2 className="text-2xl font-bold text-white">
            Reseñas
            {reviews.length > 0 && <span className="ml-2 text-base font-normal text-slate-400">({reviews.length})</span>}
          </h2>

          {reviews.length === 0 ? (
            <p className="mt-4 text-slate-500">Sé el primero en reseñar este producto.</p>
          ) : (
            <div className="mt-6 space-y-4">
              {reviews.map((review) => (
                <div key={review.id} className="rounded-2xl border border-white/10 bg-white/5 p-5">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="font-semibold text-white">{review.author_name || "Cliente verificado"}</p>
                      <StarRating rating={review.rating} size="sm" />
                    </div>
                    <span className="shrink-0 text-xs text-slate-500">
                      {new Date(review.created_at).toLocaleDateString("es-PE", { year: "numeric", month: "short", day: "numeric" })}
                    </span>
                  </div>
                  {review.comment && (
                    <p className="mt-3 text-sm leading-6 text-slate-300">{review.comment}</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Review form */}
          <div className="mt-8 rounded-2xl border border-white/10 bg-white/5 p-6">
            <h3 className="mb-4 text-lg font-bold text-white">Deja tu reseña</h3>
            {reviewSuccess ? (
              <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-300">
                ¡Gracias por tu reseña! Ya está publicada.
              </div>
            ) : (
              <form onSubmit={handleReviewSubmit} className="space-y-4">
                <div>
                  <label className="block mb-2 text-sm font-medium text-slate-300">Calificación</label>
                  <StarRating rating={reviewRating} onChange={setReviewRating} size="lg" />
                </div>
                <div>
                  <label className="block mb-1 text-sm font-medium text-slate-300">Tu nombre</label>
                  <input
                    value={reviewName}
                    onChange={(e) => setReviewName(e.target.value)}
                    placeholder="Ej: Juan Pérez"
                    required
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white placeholder-slate-500 focus:border-emerald-500/50 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block mb-1 text-sm font-medium text-slate-300">Comentario (opcional)</label>
                  <textarea
                    value={reviewComment}
                    onChange={(e) => setReviewComment(e.target.value)}
                    placeholder="¿Qué te pareció el producto?"
                    rows={3}
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white placeholder-slate-500 focus:border-emerald-500/50 focus:outline-none resize-none"
                  />
                </div>
                <button
                  type="submit"
                  disabled={reviewSubmitting}
                  className="rounded-full bg-emerald-500 px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-600 disabled:opacity-60"
                >
                  {reviewSubmitting ? "Enviando..." : "Publicar reseña"}
                </button>
              </form>
            )}
          </div>
        </section>

        {/* Related products */}
        {relatedProducts.length > 0 && (
          <section className="mt-16 mb-8">
            <h2 className="mb-6 text-2xl font-bold text-white">Productos relacionados</h2>
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
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
