"use client";

/**
 * «Continuar con Facebook» y «Continuar con Apple» (SOCIAL-AUTH-01).
 *
 * Aparecen sólo donde el servidor dice que están configurados. Cada botón es un
 * ENLACE a `/api/auth/<p>/start`: el servidor abre el intento (state, nonce,
 * PKCE), manda al proveedor, y al volver deja la sesión de siempre en cookies
 * HttpOnly. Esta pantalla no toca ningún token.
 *
 * Las marcas son de sus dueños y se dibujan como ellos piden: Facebook en su
 * azul con su «f» blanca; Apple en negro (blanco en el tema oscuro, regla en
 * globals.css) con su logotipo y «Continuar con Apple», del mismo alto que los
 * demás botones.
 */

import { useEffect, useState } from "react";

import {
  fetchSocialConfig, socialErrorMessage, socialStartUrl, type RedirectProvider, type SocialConfig,
} from "../lib/social-auth";

function FacebookLogo() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5 shrink-0" fill="currentColor">
      <path d="M24 12.073C24 5.405 18.627 0 12 0S0 5.405 0 12.073C0 18.1 4.388 23.094 10.125 24v-8.437H7.078v-3.49h3.047V9.413c0-3.026 1.792-4.697 4.533-4.697 1.312 0 2.686.236 2.686.236v2.971H15.83c-1.491 0-1.956.931-1.956 1.886v2.264h3.328l-.532 3.49h-2.796V24C19.612 23.094 24 18.1 24 12.073z" />
    </svg>
  );
}

function AppleLogo() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-[18px] w-[18px] shrink-0" fill="currentColor">
      <path d="M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701" />
    </svg>
  );
}

const BUTTON =
  "mx-auto flex h-11 w-full max-w-[360px] items-center justify-center gap-3 rounded-full px-5 text-[15px] font-medium " +
  "transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground";

type Button = {
  id: RedirectProvider;
  label: string;
  className: string;
  style?: React.CSSProperties;
  logo: () => React.ReactElement;
};

const PROVIDERS: Button[] = [
  {
    id: "facebook",
    label: "Continuar con Facebook",
    className: "facebook-sign-in",
    logo: FacebookLogo,
  },
  {
    id: "apple",
    label: "Continuar con Apple",
    className: "apple-sign-in bg-black text-white",
    // Apple pide la tipografía del sistema en este botón.
    style: { fontFamily: '-apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif' },
    logo: AppleLogo,
  },
];

export function SocialSignIn({ next }: { next?: string | null }) {
  const [config, setConfig] = useState<SocialConfig | null>(null);
  const [returnTo, setReturnTo] = useState<string | null>(next ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const search = new URLSearchParams(window.location.search);
    void fetchSocialConfig().then((found) => {
      if (cancelled) return;
      // Lo que dice la dirección se lee con la respuesta: un solo render.
      if (next === undefined) setReturnTo(search.get("next"));
      const refused = search.get("social_error");
      if (refused) setError(socialErrorMessage(refused, search.get("provider")));
      setConfig(found);
    });
    return () => { cancelled = true; };
  }, [next]);

  const offered = PROVIDERS.filter((provider) => config?.[provider.id]);

  if (offered.length === 0 && !error) return null;

  return (
    <div className="mt-3">
      {/* «Continuar con Google» dibuja su separador; sin él, lo dibuja este bloque. */}
      {offered.length > 0 && !config?.google ? (
        <div data-social-divider className="mt-3 mb-5 flex items-center gap-3 text-xs uppercase tracking-wide text-muted" aria-hidden="true">
          <span className="h-px flex-1 bg-bd-border" />o<span className="h-px flex-1 bg-bd-border" />
        </div>
      ) : null}

      <div className="space-y-3">
        {offered.map(({ id, label, className, style, logo: Logo }) => (
          <a key={id} href={socialStartUrl(id, returnTo)} data-provider={id} className={`${BUTTON} ${className}`} style={style}>
            <Logo />
            <span>{label}</span>
          </a>
        ))}
      </div>

      {error ? (
        <p role="alert" className="mt-4 rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
