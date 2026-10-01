"use client";

/**
 * M12E — el logotipo correcto para cada sitio y cada superficie.
 *
 * UN SOLO SITIO DECIDE. Header, Hero y Footer preguntan; no eligen. Tres copias
 * de una regla de contraste son tres sitios donde arreglar el mismo defecto, y
 * el defecto ya ocurrió: logo negro sobre cabecera negra.
 *
 * SIN `if (slug === "black-dog-store")`. El tenant aporta sus variantes; el
 * componente sólo consume branding. Un condicional por slug en un componente
 * compartido convierte al piloto en un caso especial del SaaS.
 *
 * SIN `filter: invert(1)`. No sabemos la geometría ni los colores del logotipo
 * de un tenant arbitrario. La versión blanca de una marca es una decisión de SU
 * manual, no una operación que podamos aplicar a ciegas.
 */

import { useStorefront } from "./StorefrontProvider";
import { useTheme } from "./ThemeProvider";
import {
  pickLogo,
  shouldRenderWordmark,
  type LogoPlacement,
  type LogoSurface,
} from "../lib/brand-logo";

type Props = {
  placement: LogoPlacement;
  /**
   * La SUPERFICIE sobre la que se dibuja, no el tema global.
   *
   * Un hero negro dentro de un tema claro sigue necesitando el logo blanco. Si
   * esto leyera el tema SIEMPRE, ese hero mostraría un logo negro sobre negro —
   * exactamente el defecto que M12E vino a cerrar, reintroducido por otra
   * puerta.
   *
   * `"theme"` es para las superficies que SÍ siguen el tema, que tras M12F son
   * casi todas: se pinta con `bg-background` y por tanto su contraste es el del
   * tema resuelto. Sigue siendo una afirmación sobre la superficie —«ésta
   * cambia con el tema»— y no una consulta al tema desde un componente que no
   * sabe sobre qué se dibuja.
   *
   * `"inverse"` es para las bandas que se pintan CON el color del texto —
   * `bg-foreground`— para destacar. Su contraste es el contrario al de la
   * página: en tema oscuro son claras y en tema claro son oscuras. No es una
   * excepción ni un truco; es la tercera respuesta posible a «¿de qué color es
   * el fondo sobre el que dibujo?», y decirla explícitamente es lo contrario de
   * adivinarla.
   */
  surface: LogoSurface | "theme" | "inverse";
  className?: string;
  /** Clases del nombre cuando no hay logo y hay que escribirlo. */
  wordmarkClassName?: string;
};

export function BrandLogo({
  placement,
  surface,
  className = "",
  wordmarkClassName = "",
}: Props) {
  const { company, branding } = useStorefront();
  const { resolved } = useTheme();
  const actualSurface: LogoSurface =
    surface === "theme"
      ? resolved
      : surface === "inverse"
        ? (resolved === "dark" ? "light" : "dark")
        : surface;
  const src = pickLogo(branding.logos, branding.logo_url, placement, actualSurface);

  if (!src) {
    // Sin variante legible: el nombre. Nunca un logo del contraste equivocado y
    // nunca el de otro tenant.
    return (
      <span className={wordmarkClassName || "font-display font-black uppercase tracking-tight"}>
        {company.name}
      </span>
    );
  }

  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={company.name}
        className={className}
        // El lockup ya lleva el nombre dentro; el `alt` es la única lectura que
        // necesita quien no ve la imagen.
      />
      {shouldRenderWordmark(src) ? (
        <span className={wordmarkClassName}>{company.name}</span>
      ) : null}
    </>
  );
}
