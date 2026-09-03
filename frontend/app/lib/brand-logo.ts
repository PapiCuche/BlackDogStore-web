/**
 * M12E — qué variante del logotipo corresponde a cada sitio.
 *
 * La lógica vive AQUÍ y no repetida en Header, Hero y Footer. Tres copias de
 * una regla de contraste son tres sitios donde arreglar el mismo defecto, y el
 * defecto ya ocurrió una vez: logo negro sobre cabecera negra.
 *
 * NO INVIERTE NADA. No hay `filter: invert(1)` en ninguna parte: no sabemos la
 * geometría ni los colores del logotipo de un tenant arbitrario, y invertir
 * produce basura con la misma confianza con la que produciría un acierto. La
 * versión blanca de una marca es una decisión de SU manual, no una operación
 * que podamos aplicar a ciegas.
 *
 * LA SUPERFICIE MANDA, NO EL TEMA. Un hero negro dentro de un tema claro sigue
 * necesitando el logo blanco. Por eso el parámetro es `surface`, no `theme`.
 */

import type { StorefrontLogos } from "./storefront";

export type LogoPlacement = "header" | "hero" | "footer";
export type LogoSurface = "light" | "dark";

/**
 * El orden de preferencia, y por qué.
 *
 * El manual del piloto asigna la composición HORIZONTAL a cabeceras y la
 * VERTICAL a piezas principales. Cuando la variante ideal no existe se prueba
 * la otra composición del mismo contraste — un logo de la forma equivocada se
 * lee; uno del contraste equivocado, no.
 *
 * `logo_url` es el último recurso: es el campo de antes de que existieran las
 * variantes, y un tenant que sólo tiene ése no debe quedarse sin logo.
 */
export function pickLogo(
  logos: StorefrontLogos | undefined,
  legacyLogoUrl: string,
  placement: LogoPlacement,
  surface: LogoSurface,
): string {
  const l = logos ?? {
    primary_on_light: "",
    primary_on_dark: "",
    horizontal_on_light: "",
    horizontal_on_dark: "",
  };

  const horizontal = surface === "dark" ? l.horizontal_on_dark : l.horizontal_on_light;
  const primary = surface === "dark" ? l.primary_on_dark : l.primary_on_light;

  const order =
    placement === "header"
      ? [horizontal, primary]
      : [primary, horizontal];

  for (const candidate of order) {
    if (candidate) return candidate;
  }

  // Sin variante de contraste. `logo_url` puede tener el contraste equivocado,
  // así que sólo se usa sobre superficie clara —donde un logo negro, que es lo
  // habitual, se lee— y en oscuro se prefiere no dibujar nada: el consumidor
  // cae al nombre de la empresa, que siempre se lee.
  if (surface === "light") return legacyLogoUrl || "";
  return "";
}

/**
 * ¿Hay que escribir el nombre de la empresa además del logo?
 *
 * El lockup completo YA contiene «BLACK DOG STORE». Dibujarlo otra vez al lado
 * duplica la marca — y duplicar el nombre junto a un lockup es una de las
 * alteraciones que un manual prohíbe sin decirlo con esas palabras.
 *
 * Sin logo, el nombre ES la identidad y hay que escribirlo.
 */
export function shouldRenderWordmark(logoUrl: string): boolean {
  return !logoUrl;
}
