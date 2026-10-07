"use client";

/**
 * The cookie notice and its preferences.
 *
 * First visit: three ways to answer, with the same weight — accept everything,
 * refuse everything optional, or choose. Refusing is one click, like accepting.
 * Until one of them is pressed nothing optional is loaded; closing the page
 * without answering is a refusal.
 *
 * Afterwards the same preferences open from «Preferencias de cookies» in the
 * footer, and changing them takes effect at once.
 *
 * Not shown in the panel: staff are not measured, so there is nothing to ask.
 */

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import {
  currentConsent, FULL_CONSENT, NO_CONSENT, onOpenConsentPreferences, readConsent, writeConsent, type Consent,
} from "../lib/consent";

const BUTTON = "min-h-11 rounded-xl border px-4 py-2.5 text-xs font-bold transition";
const QUIET = `${BUTTON} border-bd-border text-foreground hover:border-foreground/30`;
const SOLID = `${BUTTON} border-foreground bg-foreground text-background hover:opacity-90`;

type View = "hidden" | "notice" | "preferences";

function Toggle({
  id, label, description, checked, disabled, onChange,
}: { id: string; label: string; description: string; checked: boolean; disabled?: boolean; onChange?: (value: boolean) => void }) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-bd-border py-4 first:border-t-0">
      <div>
        <label htmlFor={id} className="text-sm font-semibold text-foreground">{label}</label>
        <p id={`${id}-description`} className="mt-1 text-xs text-muted">{description}</p>
      </div>
      <input
        id={id} type="checkbox" role="switch" checked={checked} disabled={disabled}
        aria-describedby={`${id}-description`}
        onChange={(event) => onChange?.(event.target.checked)}
        className="mt-1 h-5 w-5 shrink-0 accent-foreground disabled:opacity-60"
      />
    </div>
  );
}

export function ConsentBanner() {
  const pathname = usePathname();
  const [view, setView] = useState<View>("hidden");
  const [choice, setChoice] = useState<Consent>(NO_CONSENT);

  useEffect(() => {
    // After hydration, so the server and the first paint agree on «nothing shown».
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!readConsent()) setView("notice");
    return onOpenConsentPreferences(() => {
      setChoice(currentConsent());
      setView("preferences");
    });
  }, []);

  if (view === "hidden" || pathname.startsWith("/admin")) return null;

  const answer = (consent: Consent) => {
    writeConsent(consent);
    setView("hidden");
  };

  return (
    <section
      role="dialog" aria-modal="false" aria-labelledby="consent-title"
      className="fixed inset-x-3 bottom-3 z-[70] mx-auto max-w-2xl rounded-2xl border border-bd-border bg-surface p-5 shadow-2xl sm:inset-x-6 sm:bottom-6 sm:p-6"
    >
      <h2 id="consent-title" className="font-display text-sm font-semibold tracking-wide text-foreground">
        {view === "notice" ? "Cookies en esta tienda" : "Preferencias de cookies"}
      </h2>

      {view === "notice" ? (
        <>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            Usamos las cookies necesarias para que la tienda funcione. Con tu permiso, también cookies de analítica
            (para saber qué se visita) y de marketing (para medir nuestra publicidad). Puedes cambiarlo cuando quieras.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" className={SOLID} onClick={() => answer(FULL_CONSENT)}>Aceptar todas</button>
            <button type="button" className={SOLID} onClick={() => answer(NO_CONSENT)}>Rechazar opcionales</button>
            <button type="button" className={QUIET} onClick={() => { setChoice(NO_CONSENT); setView("preferences"); }}>Configurar</button>
          </div>
        </>
      ) : (
        <>
          <div className="mt-2">
            <Toggle
              id="consent-necessary" label="Necesarias" checked disabled
              description="La sesión, el carrito y el tema. Sin ellas la tienda no funciona; no se pueden desactivar."
            />
            <Toggle
              id="consent-analytics" label="Analítica" checked={choice.analytics}
              description="Google Analytics: qué páginas y productos se visitan, sin tu nombre ni tu correo."
              onChange={(analytics) => setChoice((previous) => ({ ...previous, analytics }))}
            />
            <Toggle
              id="consent-marketing" label="Marketing" checked={choice.marketing}
              description="Meta y TikTok: saber si un anuncio nuestro trajo una visita o una compra."
              onChange={(marketing) => setChoice((previous) => ({ ...previous, marketing }))}
            />
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" className={SOLID} onClick={() => answer(choice)}>Guardar preferencias</button>
            <button type="button" className={QUIET} onClick={() => answer(NO_CONSENT)}>Rechazar opcionales</button>
            <button type="button" className={QUIET} onClick={() => answer(FULL_CONSENT)}>Aceptar todas</button>
          </div>
        </>
      )}
    </section>
  );
}
