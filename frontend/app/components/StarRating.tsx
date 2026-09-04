type StarRatingProps = {
  rating: number;
  onChange?: (r: number) => void;
  size?: "sm" | "md" | "lg";
};

export function StarRating({ rating, onChange, size = "md" }: StarRatingProps) {
  const textSize = size === "sm" ? "text-sm" : size === "lg" ? "text-3xl" : "text-xl";
  return (
    <div className={`flex gap-0.5 ${textSize}`} role="group" aria-label={`Calificación: ${rating} de 5`}>
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          onClick={() => onChange?.(star)}
          disabled={!onChange}
          aria-label={`${star} estrella${star !== 1 ? "s" : ""}`}
          className={`leading-none transition-transform ${
            onChange ? "cursor-pointer hover:scale-125" : "cursor-default"
          } ${star <= rating ? "text-warning" : "text-muted"}`}
        >
          ★
        </button>
      ))}
    </div>
  );
}
