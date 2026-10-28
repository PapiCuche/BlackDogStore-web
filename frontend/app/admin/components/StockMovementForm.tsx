"use client";

// Register a MANUAL stock entry or exit. Phase 6.0, per branch from Phase 2D.
//
// The backend is the source of truth: it re-reads stock under a row lock and
// rejects anything that would go negative. This form only pre-warns the operator,
// and its warning is deliberately approximate — the product list carries the
// company-wide aggregate, while the movement lands in ONE branch. The
// authoritative refusal comes from the server.
//
// There is no "todas las sucursales" option and there will not be one: units are
// added to a place, not to a set.
//
// INV-UX-A1: the product is found with the ProductPicker (name, SKU or a scanned
// code) instead of a <select> of the first 100; `?product=` preselects it; and
// after saving, «Siguiente paso» offers labels / publication / the storefront.

import Link from "next/link";
import { useEffect, useState } from "react";
import { fetchAdminProductDetail } from "../../lib/admin";
import {
  MANUAL_MOVEMENT_TYPES,
  createStockMovement,
  type MovementType,
  type StockMovement,
} from "../../lib/inventory";
import { preferredBranch, rememberBranch } from "../lib/last-branch";
import { receiveHref } from "../lib/receive-links";
import { ErrorBox } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { NextStepBar, extractLabelBatch, type LabelBatchInfo } from "./NextStepBar";
import { ProductPicker, toPickerProduct, type PickerProduct } from "./ProductPicker";

type BranchOption = { id: number; name: string };

type Props = {
  branches?: BranchOption[];
  defaultBranch?: number | null;
  /** `?product=<id>`: the product to open the form with. */
  initialProductId?: number | null;
  /** `purchase_entry` when the person came from «Recibir mercadería». */
  initialType?: MovementType;
  onCreated?: (movement: StockMovement) => void;
};

type Saved = { product: PickerProduct; labelBatch: LabelBatchInfo | null };

export function StockMovementForm({
  branches = [],
  defaultBranch = null,
  initialProductId = null,
  initialType = "manual_entry",
  onCreated,
}: Props) {
  const [product, setProduct] = useState<PickerProduct | null>(null);
  // What the person picked; until then the remembered branch, else the default.
  // Derived at render because the branches arrive after the first render.
  const [pickedBranch, setPickedBranch] = useState<string | null>(null);
  const initialBranch = preferredBranch(branches, defaultBranch);
  const branchId = pickedBranch ?? (initialBranch === null ? "" : String(initialBranch));
  const setBranchId = setPickedBranch;
  const [movementType, setMovementType] = useState<MovementType>(initialType);
  const [quantity, setQuantity] = useState<string>("1");
  const [reason, setReason] = useState<string>("");
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [saved, setSaved] = useState<Saved | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [focusToken, setFocusToken] = useState(0);

  // `?product=`: load that product once and put it in the picker.
  useEffect(() => {
    if (initialProductId === null) return;
    let cancelled = false;
    fetchAdminProductDetail(initialProductId).then(
      (loaded) => { if (!cancelled) { setProduct(toPickerProduct(loaded)); setLinkError(null); } },
      (err) => {
        if (!cancelled) setLinkError(err instanceof Error && err.message ? err.message : "No se pudo cargar el producto del enlace.");
      },
    );
    return () => { cancelled = true; };
  }, [initialProductId]);

  const typeMeta = MANUAL_MOVEMENT_TYPES.find((t) => t.value === movementType);
  const isExit = typeMeta ? !typeMeta.isEntry : false;
  const qty = Number(quantity);
  const qtyValid = quantity.trim() !== "" && Number.isInteger(qty) && qty > 0;

  // Company-wide check only: if the whole company does not hold enough, no
  // branch does either. The per-branch answer belongs to the server.
  const wouldGoNegative = isExit && product !== null && qtyValid && product.inventory - qty < 0;

  // A branch is required whenever the operator can reach more than one. With a
  // single branch the field is not rendered and the backend fills in their
  // default, which is that same branch.
  const branchRequired = branches.length > 1;
  const canSubmit =
    !submitting &&
    product !== null &&
    (!branchRequired || branchId !== "") &&
    qtyValid &&
    reason.trim().length > 0 &&
    !wouldGoNegative;

  const touch = (field: string) => setTouched((t) => ({ ...t, [field]: true }));
  const fieldError = {
    quantity: touched.quantity && !qtyValid ? "La cantidad debe ser un número entero mayor que cero." : null,
    reason: touched.reason && reason.trim().length === 0 ? "Escribe el motivo del movimiento." : null,
    branch: touched.branch && branchRequired && branchId === "" ? "Elige la sucursal donde ocurre el movimiento." : null,
  };

  async function submit(another: boolean) {
    if (!canSubmit || !product) return;

    if (isExit) {
      const ok = window.confirm(
        `¿Confirmas registrar una SALIDA de ${qty} unidad(es) de "${product.name}"?\n\n` +
          "Esta acción queda registrada en el Kardex y en auditoría.",
      );
      if (!ok) return;
    }

    setSubmitting(true);
    setError(null);
    setSuccess(null);
    setSaved(null);
    try {
      const movement = await createStockMovement({
        product_id: product.id,
        branch: branchId === "" ? undefined : Number(branchId),
        movement_type: movementType,
        quantity: qty,
        reason: reason.trim(),
      });
      setSuccess(
        `Movimiento registrado en ${movement.branch_name}: ` +
          `${movement.movement_type_label} · ` +
          `stock ${movement.stock_before} → ${movement.stock_after}`,
      );
      rememberBranch(movement.branch ?? (branchId === "" ? null : Number(branchId)));
      setSaved({ product, labelBatch: extractLabelBatch(movement) });
      setQuantity("1");
      setTouched({});
      if (another) {
        // Branch, type and reason stay; what belongs to ONE article is emptied.
        setProduct(null);
        setFocusToken((t) => t + 1);
      } else {
        setReason("");
      }
      onCreated?.(movement);
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : "No se pudo registrar el movimiento.");
    } finally {
      setSubmitting(false);
    }
  }

  const fieldClass = internalInputClass;
  const labelClass = "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted";
  const errorText = (id: string, message: string | null) =>
    message ? <p id={id} role="alert" className="mt-1 text-xs text-danger">{message}</p> : null;

  return (
    <form onSubmit={(e) => { e.preventDefault(); void submit(false); }} className="space-y-4" noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <ProductPicker
            label="Producto"
            inputId="sm-product"
            value={product}
            onChange={(next) => { setProduct(next); setSaved(null); }}
            focusToken={focusToken}
            disabled={submitting}
          />
          {linkError ? <p role="alert" className="mt-1 text-xs text-danger">{linkError}</p> : null}
          {product?.is_serialized && !isExit ? (
            <p role="note" className="mt-2 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-foreground">
              «{product.name}» se recibe equipo por equipo, con su serie e IMEI.{" "}
              <Link href={receiveHref({ id: product.id, is_serialized: true })} className="font-semibold underline">
                Registrar equipo
              </Link>
            </p>
          ) : null}
        </div>

        {branches.length > 1 ? (
          <div>
            <label className={labelClass} htmlFor="sm-branch">Sucursal</label>
            <select
              id="sm-branch"
              className={fieldClass}
              value={branchId}
              onChange={(e) => setBranchId(e.target.value)}
              onBlur={() => touch("branch")}
              aria-invalid={fieldError.branch ? true : undefined}
              aria-describedby={fieldError.branch ? "sm-branch-error" : undefined}
              disabled={submitting}
            >
              <option value="">Selecciona una sucursal…</option>
              {branches.map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
            {errorText("sm-branch-error", fieldError.branch)}
          </div>
        ) : null}

        <div>
          <label className={labelClass} htmlFor="sm-type">Tipo de movimiento</label>
          <select
            id="sm-type"
            className={fieldClass}
            value={movementType}
            onChange={(e) => setMovementType(e.target.value as MovementType)}
            disabled={submitting}
          >
            <optgroup label="Entradas">
              {MANUAL_MOVEMENT_TYPES.filter((t) => t.isEntry).map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </optgroup>
            <optgroup label="Salidas">
              {MANUAL_MOVEMENT_TYPES.filter((t) => !t.isEntry).map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </optgroup>
          </select>
        </div>

        <div>
          <label className={labelClass} htmlFor="sm-qty">Cantidad</label>
          <input
            id="sm-qty"
            type="number"
            min={1}
            inputMode="numeric"
            className={fieldClass}
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            onBlur={() => touch("quantity")}
            aria-invalid={fieldError.quantity ? true : undefined}
            aria-describedby={fieldError.quantity ? "sm-qty-error" : undefined}
            disabled={submitting}
          />
          {errorText("sm-qty-error", fieldError.quantity)}
          {product ? (
            <p className="mt-1 text-xs text-muted">
              Stock de la empresa: {product.inventory} u. El stock de la sucursal
              se valida en el servidor.
            </p>
          ) : null}
        </div>

        <div>
          <label className={labelClass} htmlFor="sm-reason">Motivo (obligatorio)</label>
          <input
            id="sm-reason"
            type="text"
            maxLength={500}
            className={fieldClass}
            placeholder="Ej. Compra de stock a proveedor"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onBlur={() => touch("reason")}
            aria-invalid={fieldError.reason ? true : undefined}
            aria-describedby={fieldError.reason ? "sm-reason-error" : undefined}
            disabled={submitting}
          />
          {errorText("sm-reason-error", fieldError.reason)}
        </div>
      </div>

      {wouldGoNegative ? (
        <ErrorBox message="Esta salida dejaría el stock en negativo. Reduce la cantidad." />
      ) : null}
      {error ? <ErrorBox message={error} /> : null}
      {success ? (
        <div role="status" className="rounded-lg border border-foreground/20 bg-foreground/[0.05] px-4 py-3">
          <p className="text-sm text-foreground">{success}</p>
        </div>
      ) : null}
      {saved ? (
        <NextStepBar
          product={saved.product}
          published={saved.product.is_active !== false && saved.product.is_published_online === true}
          labelBatch={saved.labelBatch}
        />
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" disabled={!canSubmit} className={internalPrimaryButtonClass}>
          {submitting ? "Registrando…" : "Registrar movimiento"}
        </button>
        <button type="button" disabled={!canSubmit} onClick={() => void submit(true)} className={internalButtonClass}>
          {isExit ? "Guardar y registrar otra salida" : "Guardar y recibir más stock"}
        </button>
      </div>
      {!canSubmit && !submitting ? (
        <p className="text-xs text-muted">
          {product === null ? "Elige el producto. " : ""}
          {branchRequired && branchId === "" ? "Elige la sucursal donde ocurre el movimiento. " : ""}
        </p>
      ) : null}
    </form>
  );
}
