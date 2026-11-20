/**
 * CATALOG-REFERENCE-01 · PR C (D-C-1) — what a variant shows when neither it nor its finish (model + colour) has a
 * picture: a neutral marker, NEVER another colour's photo nor the model's cover.
 *
 * It fills the box the photo would fill (the caller keeps that box's aspect ratio), so choosing a colour without
 * pictures moves nothing on the page. Colours are the theme's tokens, inherited from the caller (`className`): the
 * same marker serves the V3 page and the V4 configurator. One accessible name; the drawing and the visible words
 * are hidden from assistive technology so it is not read twice.
 */

export const IMAGE_PENDING_LABEL = "Imagen pendiente";

/** `compact`: a small box (a cart thumbnail) — the drawing only; the name stays for assistive technology. */
export function ImagePending({ className = "", compact = false }: { className?: string; compact?: boolean }) {
  return (
    <div
      data-testid="image-pending"
      role="img"
      aria-label={`${IMAGE_PENDING_LABEL}: aún no hay foto de esta opción`}
      title={compact ? IMAGE_PENDING_LABEL : undefined}
      className={`absolute inset-0 flex flex-col items-center justify-center text-center ${compact ? "gap-0 px-1" : "gap-3 px-6"} ${className}`}
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 48 48"
        className={`${compact ? "h-6 w-6" : "h-12 w-12"} opacity-40`}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="6" y="10" width="36" height="28" rx="4" />
        <circle cx="18" cy="21" r="3.5" />
        <path d="M6 33l10-9 8 7 6-5 12 10" />
      </svg>
      {compact ? null : (
        <span aria-hidden="true" className="text-[11px] font-semibold uppercase tracking-[0.14em]">
          {IMAGE_PENDING_LABEL}
        </span>
      )}
    </div>
  );
}
