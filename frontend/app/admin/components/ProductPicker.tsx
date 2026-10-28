"use client";

/**
 * INV-UX-A1 — pick ONE product by typing, never from a 100-item <select>.
 *
 * Search by name, slug, SKU (the code printed on the label) or a code read by a
 * scanner. The company and branch scoping is the server's: this only sends text
 * to `GET /admin/products/?search=`. A scanner behaves like a keyboard that
 * types the code and presses Enter: Enter with nothing highlighted asks at
 * once and picks the product whose code is EXACTLY that one (`search_exact`),
 * or the only product found; with several loose matches it asks the person.
 *
 * Nothing private is shown: the list carries no cost, serial or IMEI, and this
 * component would not print them if it did.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { fetchAdminProducts, type AdminProduct } from "../../lib/admin";
import { internalButtonClass, internalInputClass } from "./internal-ui";

export type PickerProduct = Pick<AdminProduct, "id" | "name" | "slug" | "price" | "inventory"> &
  Partial<
    Pick<
      AdminProduct,
      | "is_serialized" | "requires_imei" | "primary_code" | "condition_line"
      | "is_active" | "is_published_online" | "search_exact" | "can_change_tracking"
    >
  >;

/** Keep only what the picker (and the forms behind it) may hold of a product. */
export function toPickerProduct(p: AdminProduct): PickerProduct {
  return {
    id: p.id, name: p.name, slug: p.slug, price: p.price, inventory: p.inventory,
    is_serialized: p.is_serialized, requires_imei: p.requires_imei, primary_code: p.primary_code,
    condition_line: p.condition_line, is_active: p.is_active, is_published_online: p.is_published_online,
    search_exact: p.search_exact, can_change_tracking: p.can_change_tracking,
  };
}

const DEBOUNCE_MS = 250;
const MIN_CHARS = 2;
const PAGE_SIZE = 8;

const money = (value: string) => {
  const n = Number.parseFloat(value);
  return Number.isNaN(n) ? value : `S/ ${n.toFixed(2)}`;
};

const BADGE = "rounded-full border border-bd-border px-2 py-0.5 text-[11px] font-medium text-muted";

function Facts({ product }: { product: PickerProduct }) {
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
      {product.primary_code ? <span className="font-mono text-foreground">{product.primary_code}</span> : null}
      <span>{money(product.price)}</span>
      <span>{product.inventory} u. en total</span>
      {product.condition_line ? (
        <span className={BADGE}>{product.condition_line === "used" ? "Seminuevo" : "Nuevo"}</span>
      ) : null}
      {product.is_serialized ? <span className={BADGE}>Por serie</span> : null}
      {product.is_active === false ? <span className={BADGE}>Inactivo</span> : null}
    </span>
  );
}

type Status = "idle" | "loading" | "ready" | "error";

export function ProductPicker({
  label, value, onChange, disabled = false, error, helper, inputId, focusToken = 0,
}: {
  label: string;
  value: PickerProduct | null;
  onChange: (product: PickerProduct | null) => void;
  disabled?: boolean;
  error?: string | null;
  helper?: string;
  inputId?: string;
  /** Raise it to move focus to the search box (e.g. after «Guardar y recibir más stock»). */
  focusToken?: number;
}) {
  const auto = useId();
  const id = inputId ?? `picker-${auto}`;
  const listId = `${id}-list`;
  const input = useRef<HTMLInputElement | null>(null);
  const wantFocus = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ticket = useRef(0);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PickerProduct[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [failure, setFailure] = useState("");
  const [asked, setAsked] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [ambiguous, setAmbiguous] = useState(false);

  const run = useCallback(async (term: string): Promise<PickerProduct[] | null> => {
    const mine = ++ticket.current;
    setStatus("loading");
    setFailure("");
    setAsked(term);
    try {
      const page = await fetchAdminProducts({ search: term, page_size: PAGE_SIZE });
      if (mine !== ticket.current) return null; // a newer search replaced this one
      const rows = page.results.map(toPickerProduct);
      setResults(rows);
      setStatus("ready");
      setActive(-1);
      setOpen(true);
      return rows;
    } catch (err) {
      if (mine !== ticket.current) return null;
      setResults([]);
      setStatus("error");
      setFailure(
        err instanceof Error && err.message ? err.message : "No se pudo buscar. Revisa la conexión e inténtalo de nuevo.",
      );
      setOpen(true);
      return null;
    }
  }, []);

  useEffect(() => { if (focusToken > 0) input.current?.focus(); }, [focusToken]);

  // Debounced search-as-you-type: scheduled by the input handler, cancelled on unmount.
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  function onType(text: string) {
    setQuery(text);
    setAmbiguous(false);
    setOpen(true);
    if (timer.current) clearTimeout(timer.current);
    const term = text.trim();
    if (term.length < MIN_CHARS) {
      ticket.current++; // drop whatever is still on its way
      setResults([]);
      setStatus("idle");
      return;
    }
    timer.current = setTimeout(() => { void run(term); }, DEBOUNCE_MS);
  }

  /** The input is created anew each time the chosen product is cleared: focus it then if asked. */
  const attach = (el: HTMLInputElement | null) => {
    input.current = el;
    if (el && wantFocus.current) {
      wantFocus.current = false;
      el.focus();
    }
  };

  function choose(product: PickerProduct) {
    ticket.current++;
    setQuery("");
    setResults([]);
    setStatus("idle");
    setOpen(false);
    setActive(-1);
    setAmbiguous(false);
    onChange(product);
  }

  async function onEnter() {
    if (open && active >= 0 && results[active]) {
      choose(results[active]);
      return;
    }
    const term = query.trim();
    if (term.length < MIN_CHARS) return;
    if (timer.current) clearTimeout(timer.current);
    const rows = await run(term);
    if (!rows) return;
    const exact = rows.find((r) => r.search_exact) ?? (rows.length === 1 ? rows[0] : null);
    if (exact) choose(exact);
    else setAmbiguous(rows.length > 1);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault(); // a scan must never submit the form behind it
      void onEnter();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      if (results.length === 0) return;
      setOpen(true);
      setActive((i) => (i + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (results.length === 0) return;
      setOpen(true);
      setActive((i) => (i <= 0 ? results.length - 1 : i - 1));
    } else if (event.key === "Escape") {
      setOpen(false);
      setActive(-1);
    }
  }

  const labelClass = "block text-xs font-semibold text-foreground";

  if (value) {
    return (
      <div>
        <span className={labelClass}>{label}</span>
        <div className="mt-1.5 flex items-start justify-between gap-3 rounded-lg border border-bd-border bg-background px-3 py-2">
          <div className="min-w-0">
            <p className="break-words text-sm font-medium text-foreground">{value.name}</p>
            <Facts product={value} />
          </div>
          <button
            type="button"
            className={`${internalButtonClass} shrink-0`}
            disabled={disabled}
            onClick={() => { wantFocus.current = true; onChange(null); }}
          >
            Cambiar producto
          </button>
        </div>
      </div>
    );
  }

  const term = query.trim();
  // While the next search loads, the previous list stays (no flicker).
  const showList = open && (status === "ready" || status === "error" || (status === "loading" && results.length > 0));
  const listRendered = showList && status !== "error" && results.length > 0;
  const emptyText = `Ningún producto coincide con «${asked}». Revisa el código o escribe parte del nombre.`;
  // What a screen reader hears; the empty case is read from the visible text.
  const announcement =
    status === "loading" ? "Buscando…"
    : status === "ready" && results.length === 0 ? "Sin resultados."
    : status === "ready" ? `${results.length} ${results.length === 1 ? "producto" : "productos"}`
    : "";

  return (
    <div className="relative">
      <label htmlFor={id} className={labelClass}>{label}</label>
      <input
        id={id}
        ref={attach}
        type="text"
        role="combobox"
        aria-expanded={listRendered}
        aria-controls={listRendered ? listId : undefined}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 && results[active] ? `${id}-opt-${results[active].id}` : undefined}
        aria-invalid={error ? true : undefined}
        aria-describedby={`${id}-help${error ? ` ${id}-error` : ""}`}
        autoComplete="off"
        spellCheck={false}
        enterKeyHint="search"
        placeholder="Nombre, SKU o escanea el código"
        value={query}
        disabled={disabled}
        onChange={(e) => onType(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => { if (status === "ready" || status === "error") setOpen(true); }}
        onBlur={() => setOpen(false)}
        className={`mt-1.5 ${internalInputClass} ${error ? "border-danger" : ""}`}
      />
      <p id={`${id}-help`} className="mt-1 text-xs text-muted">
        {term.length > 0 && term.length < MIN_CHARS
          ? "Escribe al menos 2 letras o números."
          : helper ?? "Escribe al menos 2 letras o números, o escanea el código y pulsa Enter."}
      </p>
      {error ? <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-danger">{error}</p> : null}
      <p className="sr-only" role="status" aria-live="polite">{announcement}</p>

      {showList ? (
        <div className="absolute z-20 mt-1 w-full rounded-lg border border-bd-border bg-surface shadow-lg">
          {status === "error" ? (
            <div role="alert" className="flex items-center justify-between gap-3 px-3 py-3 text-sm text-danger">
              <span>{failure}</span>
              <button type="button" className={internalButtonClass} onMouseDown={(e) => e.preventDefault()} onClick={() => void run(term)}>
                Reintentar
              </button>
            </div>
          ) : results.length === 0 ? (
            <p className="px-3 py-3 text-sm text-muted">{emptyText}</p>
          ) : (
            <>
              {ambiguous ? (
                <p className="border-b border-bd-border px-3 py-2 text-xs text-warning">
                  Varios productos coinciden: elige uno de la lista.
                </p>
              ) : null}
              <ul id={listId} role="listbox" aria-busy={status === "loading"} aria-label={`Resultados para ${label}`} className="max-h-72 overflow-y-auto py-1">
                {results.map((product, index) => (
                  <li
                    key={product.id}
                    id={`${id}-opt-${product.id}`}
                    role="option"
                    aria-selected={index === active}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => choose(product)}
                    onMouseEnter={() => setActive(index)}
                    className={`cursor-pointer px-3 py-2 ${index === active ? "bg-foreground/[0.06]" : ""}`}
                  >
                    <span className="block break-words text-sm font-medium text-foreground">{product.name}</span>
                    <Facts product={product} />
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
