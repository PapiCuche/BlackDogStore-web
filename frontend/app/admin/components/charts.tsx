"use client";

/**
 * Dashboard charts — Phase 2B.1.
 *
 * WHY HAND-ROLLED SVG INSTEAD OF A CHART LIBRARY
 * ----------------------------------------------
 * The project ships three runtime dependencies: next, react, react-dom. Recharts
 * (or any d3-based option) would roughly triple that graph for three simple
 * shapes — horizontal bars, a donut and a stacked bar — none of which need
 * scales, axes, transitions or hit-testing beyond a title tooltip.
 *
 * The palette decides it too. Black Dog Store is strictly monochrome
 * (#080808 / #111111 / #1a1a1a, zinc text), so most of what a chart library
 * gives you — categorical colour scales, themes, legends in twelve hues — is
 * exactly what must NOT appear here. Fighting a library's defaults back to
 * greyscale is more work than drawing the shapes.
 *
 * Same reasoning as icons.tsx, and consistent with the rest of the codebase.
 *
 * ACCESSIBILITY
 * -------------
 * A chart is an image to a screen reader. Every chart is `role="img"` with a
 * descriptive `aria-label`, and each carries a visually-hidden table with the
 * same numbers — so the data is readable, not just the picture.
 *
 * All series are TENANT-SAFE by construction: they come from the internal
 * dashboard endpoint, which computes them with an explicit `company=` filter.
 */

export type Series = { label: string; value: number }[];

// Monochrome ramp. Opacity carries the magnitude instead of hue, which keeps the
// brand palette intact and stays legible for colour-blind readers.
const RAMP = [0.92, 0.78, 0.66, 0.55, 0.45, 0.36, 0.28, 0.22, 0.16];

function rampAt(index: number): number {
  return RAMP[Math.min(index, RAMP.length - 1)];
}

/** Visually hidden data table — the accessible equivalent of the drawing. */
function DataTable({
  caption,
  series,
  unit,
}: {
  caption: string;
  series: Series;
  unit: string;
}) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Categoría</th>
          <th scope="col">{unit}</th>
        </tr>
      </thead>
      <tbody>
        {series.map((point) => (
          <tr key={point.label}>
            <th scope="row">{point.label}</th>
            <td>{point.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ChartEmpty({ message }: { message: string }) {
  return (
    <div className="flex min-h-[9rem] items-center justify-center rounded-lg border border-dashed border-white/10 px-4 py-8">
      <p className="text-center text-sm text-zinc-500">{message}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Horizontal bars
// ---------------------------------------------------------------------------

export function HorizontalBarChart({
  series,
  unit = "elementos",
  emptyMessage = "Sin datos todavía.",
}: {
  series: Series;
  unit?: string;
  emptyMessage?: string;
}) {
  const total = series.reduce((sum, p) => sum + p.value, 0);
  if (series.length === 0 || total === 0) {
    return <ChartEmpty message={emptyMessage} />;
  }

  const max = Math.max(...series.map((p) => p.value));

  return (
    <div
      role="img"
      aria-label={`Gráfico de barras: ${series
        .map((p) => `${p.label}, ${p.value} ${unit}`)
        .join("; ")}`}
    >
      <ul className="space-y-2.5">
        {series.map((point, index) => {
          // Guard against a zero max; every bar keeps a sliver so a 0 row is
          // still visible as a row rather than vanishing.
          const pct = max > 0 ? Math.max((point.value / max) * 100, 1.5) : 1.5;
          return (
            <li key={point.label}>
              <div className="mb-1 flex items-baseline justify-between gap-3">
                <span className="truncate text-xs text-zinc-400" title={point.label}>
                  {point.label}
                </span>
                <span className="shrink-0 text-xs font-medium tabular-nums text-zinc-200">
                  {point.value}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-white/[0.04]">
                <div
                  className="h-full rounded-full bg-white transition-[width] duration-500"
                  style={{ width: `${pct}%`, opacity: rampAt(index) }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <DataTable caption={`Distribución por ${unit}`} series={series} unit={unit} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Vertical bars (time series)
// ---------------------------------------------------------------------------

export function VerticalBarChart({
  series,
  unit = "elementos",
  formatValue,
  emptyMessage = "Sin datos todavía.",
}: {
  series: Series;
  unit?: string;
  /** Optional formatter for the tooltip and the peak label (e.g. currency). */
  formatValue?: (value: number) => string;
  emptyMessage?: string;
}) {
  const total = series.reduce((sum, p) => sum + p.value, 0);
  if (series.length === 0) return <ChartEmpty message={emptyMessage} />;

  const max = Math.max(...series.map((p) => p.value));
  const format = formatValue ?? ((v: number) => String(v));

  return (
    <div
      role="img"
      aria-label={`Gráfico de barras por día: ${series
        .map((p) => `${p.label}, ${format(p.value)}`)
        .join("; ")}`}
    >
      <div className="flex h-40 items-end gap-1.5 sm:gap-2">
        {series.map((point, index) => {
          // A zero day still renders a baseline sliver: an absent bar reads as
          // missing data, a flat one reads as a quiet day.
          const height = max > 0 ? Math.max((point.value / max) * 100, 2) : 2;
          const isPeak = max > 0 && point.value === max;
          return (
            <div key={point.label + index} className="flex min-w-0 flex-1 flex-col items-center gap-2">
              <div className="flex w-full flex-1 items-end">
                <div
                  className="w-full rounded-t-sm bg-white transition-[height] duration-500"
                  style={{
                    height: `${height}%`,
                    opacity: isPeak ? 0.92 : 0.45,
                  }}
                  title={`${point.label}: ${format(point.value)}`}
                />
              </div>
              <span className="w-full truncate text-center text-[10px] tabular-nums text-zinc-600">
                {point.label}
              </span>
            </div>
          );
        })}
      </div>
      {total > 0 ? (
        <p className="mt-4 border-t border-white/[0.06] pt-3 text-xs text-zinc-500">
          Total del período:{" "}
          <span className="font-medium text-zinc-200">{format(total)}</span>
        </p>
      ) : null}
      <DataTable caption={`Serie por día (${unit})`} series={series} unit={unit} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Donut
// ---------------------------------------------------------------------------

export function DonutChart({
  series,
  centerLabel,
  centerValue,
  unit = "elementos",
  emptyMessage = "Sin datos todavía.",
}: {
  series: Series;
  centerLabel: string;
  centerValue: number | string;
  unit?: string;
  emptyMessage?: string;
}) {
  const total = series.reduce((sum, p) => sum + p.value, 0);
  if (total === 0) return <ChartEmpty message={emptyMessage} />;

  const size = 168;
  const stroke = 20;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;

  // Each arc starts where the previous one ended. Computed from a running sum of
  // the preceding values rather than a mutable accumulator, so the render stays
  // pure and produces the same output however many times it runs.
  const arcs = series.map((point, index) => {
    const precedingTotal = series
      .slice(0, index)
      .reduce((sum, previous) => sum + previous.value, 0);
    return {
      label: point.label,
      value: point.value,
      opacity: rampAt(index),
      dash: `${(point.value / total) * circumference} ${circumference}`,
      rotation: (precedingTotal / total) * 360,
    };
  });

  return (
    <div className="flex flex-col items-center gap-5 sm:flex-row sm:items-center sm:gap-7">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        className="h-[168px] w-[168px] shrink-0 -rotate-90"
        role="img"
        aria-label={`Gráfico de anillo: ${series
          .map((p) => `${p.label}, ${p.value} ${unit}`)
          .join("; ")}`}
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="rgba(255,255,255,0.05)"
          strokeWidth={stroke}
        />
        {arcs.map((arc) => (
          <circle
            key={arc.label}
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="white"
            strokeOpacity={arc.opacity}
            strokeWidth={stroke}
            strokeDasharray={arc.dash}
            transform={`rotate(${arc.rotation} ${size / 2} ${size / 2})`}
          />
        ))}
        {/* Counter-rotated so the text reads horizontally despite the -90 turn */}
        <g transform={`rotate(90 ${size / 2} ${size / 2})`}>
          <text
            x="50%"
            y="47%"
            textAnchor="middle"
            className="fill-white text-[26px] font-semibold tabular-nums"
          >
            {centerValue}
          </text>
          <text
            x="50%"
            y="60%"
            textAnchor="middle"
            className="fill-zinc-500 text-[10px] uppercase tracking-widest"
          >
            {centerLabel}
          </text>
        </g>
      </svg>

      <ul className="w-full space-y-2">
        {series.map((point, index) => (
          <li key={point.label} className="flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className="h-2.5 w-2.5 shrink-0 rounded-sm bg-white"
              style={{ opacity: rampAt(index) }}
            />
            <span className="min-w-0 flex-1 truncate text-xs text-zinc-400">
              {point.label}
            </span>
            <span className="shrink-0 text-xs font-medium tabular-nums text-zinc-200">
              {point.value}
            </span>
            <span className="w-10 shrink-0 text-right text-[11px] tabular-nums text-zinc-600">
              {Math.round((point.value / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>

      <DataTable caption={`Distribución de ${unit}`} series={series} unit={unit} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stacked progress bar
// ---------------------------------------------------------------------------

export function StackedBar({
  series,
  unit = "módulos",
}: {
  series: Series;
  unit?: string;
}) {
  const total = series.reduce((sum, p) => sum + p.value, 0);
  if (total === 0) return <ChartEmpty message="Sin datos." />;

  return (
    <div
      role="img"
      aria-label={`Barra apilada: ${series
        .map((p) => `${p.label}, ${p.value} ${unit}`)
        .join("; ")}`}
    >
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-white/[0.04]">
        {series
          .filter((p) => p.value > 0)
          .map((point, index) => (
            <div
              key={point.label}
              className="h-full bg-white"
              style={{
                width: `${(point.value / total) * 100}%`,
                opacity: rampAt(index),
              }}
              title={`${point.label}: ${point.value}`}
            />
          ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
        {series.map((point, index) => (
          <li key={point.label} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="h-2.5 w-2.5 rounded-sm bg-white"
              style={{ opacity: rampAt(index) }}
            />
            <span className="text-xs text-zinc-400">{point.label}</span>
            <span className="text-xs font-medium tabular-nums text-zinc-200">
              {point.value}
            </span>
          </li>
        ))}
      </ul>
      <DataTable caption={`Estado de ${unit}`} series={series} unit={unit} />
    </div>
  );
}
