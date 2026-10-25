"use client";

/**
 * LABEL-03b — la configuración de etiquetas de UNA empresa.
 *
 * Lo que decide el servidor no se adivina aquí:
 *  - si se puede cambiar (`can_manage`, que exige «Administrar empresa»);
 *  - por qué un prefijo no sirve (sus mensajes se muestran tal cual).
 * La comprobación previa de forma y del prefijo de productos sólo evita enviar
 * algo que el servidor va a rechazar.
 */

import { useEffect, useId, useState } from "react";

import {
  LabelSettingsFieldError, fetchLabelSettings, fetchProductCodePrefix, updateLabelSettings,
  type DeviceSymbol, type LabelSettings, type LabelSettingsChange,
} from "./label-settings-api";

const PREFIX_SHAPE = /^[A-Z]{1,4}$/;

const BUTTON =
  "rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40";
const INPUT =
  "mt-1 w-28 rounded-lg border border-bd-border bg-background px-3 py-2 font-mono text-sm uppercase text-foreground aria-[invalid=true]:border-danger-border";

type Loaded = { settings: LabelSettings; productPrefix: string | null };

export function LabelSettingsPanel({ companyId }: { companyId: number | null }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [prefix, setPrefix] = useState("");
  const [symbol, setSymbol] = useState<DeviceSymbol>("internal");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const ids = useId();

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [settings, productPrefix] = await Promise.all([
          fetchLabelSettings(companyId),
          // Sólo informa la regla: si no se puede leer, el servidor la sigue aplicando.
          fetchProductCodePrefix(companyId).catch(() => null),
        ]);
        if (cancelled) return;
        setLoaded({ settings, productPrefix });
        setPrefix(settings.unit_code_prefix);
        setSymbol(settings.device_symbol);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "No se pudo cargar la configuración.");
      }
    })();
    return () => { cancelled = true; };
  }, [companyId]);

  if (loadError) {
    return <p role="alert" className="text-sm text-danger">{loadError}</p>;
  }
  if (!loaded) {
    return <p className="text-sm text-muted">Cargando…</p>;
  }

  const { settings, productPrefix } = loaded;
  const canManage = settings.can_manage;
  const change: LabelSettingsChange = {};
  if (prefix !== settings.unit_code_prefix) change.unit_code_prefix = prefix;
  if (symbol !== settings.device_symbol) change.device_symbol = symbol;

  let prefixProblem: string | null = null;
  if (!PREFIX_SHAPE.test(prefix)) {
    prefixProblem = "De 1 a 4 letras mayúsculas (A–Z), sin números ni espacios.";
  } else if (productPrefix && prefix === productPrefix.toUpperCase()) {
    prefixProblem = `«${prefix}» es el prefijo de los productos: elige otro.`;
  }
  const serverPrefixErrors = fieldErrors.unit_code_prefix ?? [];
  const otherErrors = Object.entries(fieldErrors)
    .filter(([key]) => key !== "unit_code_prefix" && key !== "device_symbol")
    .flatMap(([, messages]) => messages);
  const canSave = canManage && !busy && Object.keys(change).length > 0 && !(change.unit_code_prefix !== undefined && prefixProblem);

  const save = async () => {
    setBusy(true);
    setSaved(false);
    setSaveError(null);
    setFieldErrors({});
    try {
      const next = await updateLabelSettings(companyId, change);
      setLoaded({ settings: next, productPrefix });
      setPrefix(next.unit_code_prefix);
      setSymbol(next.device_symbol);
      setSaved(true);
    } catch (err) {
      if (err instanceof LabelSettingsFieldError) setFieldErrors(err.fields);
      else setSaveError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setBusy(false);
    }
  };

  const prefixInvalid = Boolean((change.unit_code_prefix !== undefined && prefixProblem) || serverPrefixErrors.length);

  return (
    <form
      className="space-y-6"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) void save();
      }}
    >
      <section aria-labelledby={`${ids}-prefix-title`} className="rounded-xl border border-bd-border bg-surface p-5">
        <h2 id={`${ids}-prefix-title`} className="text-sm font-semibold text-foreground">Código interno de los equipos</h2>
        <p className="mt-1 text-xs text-muted">
          Cada equipo que entra recibe un código interno: este prefijo y diez cifras (por ejemplo{" "}
          <span className="font-mono">{(prefix || settings.unit_code_prefix)}4827193056</span>). Los equipos que ya
          tienen código lo conservan; el prefijo nuevo se usa para los que entren después.
        </p>
        <label className="mt-4 block text-xs text-muted" htmlFor={`${ids}-prefix`}>Prefijo de los equipos</label>
        <input
          id={`${ids}-prefix`}
          className={INPUT}
          value={prefix}
          maxLength={4}
          autoComplete="off"
          spellCheck={false}
          autoCapitalize="characters"
          disabled={!canManage}
          aria-invalid={prefixInvalid}
          aria-describedby={`${ids}-prefix-rule ${ids}-prefix-error`}
          onChange={(e) => {
            setPrefix(e.target.value.toUpperCase());
            setSaved(false);
            setFieldErrors((errors) => {
              const rest = { ...errors };
              delete rest.unit_code_prefix;
              return rest;
            });
          }}
        />
        <p id={`${ids}-prefix-rule`} className="mt-2 text-xs text-muted">
          El prefijo de los productos es{" "}
          <span data-testid="product-prefix" className="font-mono font-semibold text-foreground">
            {productPrefix ?? "—"}
          </span>
          . El de los equipos nunca puede ser igual al de los productos: así nadie confunde un equipo con un producto
          al escanear.
          {productPrefix === null ? " (No se pudo leer el de los productos; el servidor lo comprobará al guardar.)" : ""}
        </p>
        <div id={`${ids}-prefix-error`} aria-live="polite">
          {change.unit_code_prefix !== undefined && prefixProblem ? (
            <p className="mt-1 text-xs text-danger">{prefixProblem}</p>
          ) : null}
          {serverPrefixErrors.map((message) => (
            <p key={message} className="mt-1 text-xs text-danger">{message}</p>
          ))}
        </div>
      </section>

      <fieldset className="rounded-xl border border-bd-border bg-surface p-5" disabled={!canManage}>
        <legend className="px-1 text-sm font-semibold text-foreground">Qué código lleva la etiqueta de los equipos</legend>
        <div className="mt-2 space-y-2">
          {settings.device_symbols.map((option) => (
            <label key={option.value} className="flex items-start gap-2 text-sm text-foreground">
              <input
                type="radio"
                name={`${ids}-symbol`}
                value={option.value}
                checked={symbol === option.value}
                onChange={() => { setSymbol(option.value); setSaved(false); }}
                className="mt-1"
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
        {(fieldErrors.device_symbol ?? []).map((message) => (
          <p key={message} className="mt-1 text-xs text-danger">{message}</p>
        ))}
        {symbol === "imei1" ? (
          <div
            role="note"
            aria-label="Aviso IMEI 1"
            className="mt-4 rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-xs text-foreground"
          >
            <p className="font-semibold">El IMEI es un dato sensible.</p>
            <p className="mt-1">
              Con «IMEI 1», la etiqueta de un equipo lleva su IMEI sólo cuando la genera alguien con el permiso
              «Imprimir IMEI y series» (<span className="font-mono">inventory.labels.sensitive</span>). Cada PDF con
              IMEI queda registrado en la auditoría de la empresa, con los equipos y nunca con el IMEI. Para quien no
              tiene ese permiso, la etiqueta sigue llevando el código interno.
            </p>
          </div>
        ) : null}
      </fieldset>

      {otherErrors.map((message) => (
        <p key={message} role="alert" className="text-sm text-danger">{message}</p>
      ))}
      {saveError ? <p role="alert" className="text-sm text-danger">{saveError}</p> : null}
      {saved ? <p role="status" className="text-sm text-success">Guardado.</p> : null}

      {canManage ? (
        <button type="submit" className={BUTTON} disabled={!canSave}>
          {busy ? "Guardando…" : "Guardar"}
        </button>
      ) : (
        <p className="text-sm text-muted">
          Sólo quien administra la empresa («Administrar empresa») puede cambiar esta configuración.
        </p>
      )}
    </form>
  );
}
