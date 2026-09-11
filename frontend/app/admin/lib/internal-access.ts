"use client";

/**
 * Qué OFRECE el control interno a quien mira — H4.1.2A (RBAC-LEGACY-UI-01).
 *
 * EL DEFECTO. Las pantallas internas decidían con `UserProfile.role`, el rol
 * GLOBAL, anterior a las empresas. El backend dejó de usarlo hace tres fases y
 * hoy pregunta la capacidad de la empresa, así que la interfaz y el servidor
 * respondían cosas distintas sobre la misma persona:
 *
 *   · quien entra por una invitación tiene perfil `customer` y capacidades
 *     reales; la pantalla lo echaba y el backend le habría contestado;
 *   · un perfil `sales` sin la capacidad veía el botón y recibía 403.
 *
 * LA REGLA. Con contexto de empresa manda la capacidad que informa el servidor.
 * Sin contexto de empresa —el operador del puente legacy, que no tiene
 * Membership— sigue mandando su rol legacy, porque es exactamente lo que el
 * backend comprueba en ese camino. El master de plataforma pasa siempre.
 *
 * NO ES AUTORIDAD. Es lo que la interfaz muestra; cada endpoint vuelve a
 * decidir, y sigue respondiendo 403 a quien no deba pasar.
 */

import type { AuthUser } from "../../lib/auth";
import type { InternalDashboard } from "./internal-api";

export type InternalAccess = {
  user: AuthUser;
  /** `null` = sin contexto de empresa: el operador del puente legacy. */
  dashboard: InternalDashboard | null;
  hasCompanyContext: boolean;
  isPlatformAdmin: boolean;
  /**
   * `legacyRoles` reproduce el conjunto que el backend acepta en el puente
   * legacy para ESA operación, y se escribe en cada llamada a propósito: no son
   * los mismos para reenviar un correo que para mover stock.
   */
  can: (capability: string, legacyRoles?: readonly string[]) => boolean;
};

export function buildInternalAccess(
  user: AuthUser,
  dashboard: InternalDashboard | null,
): InternalAccess {
  const isPlatformAdmin = Boolean(dashboard?.access.is_platform_admin);
  const hasCompanyContext = Boolean(dashboard?.company);
  const capabilities = new Set(dashboard?.access.capabilities ?? []);

  return {
    user,
    dashboard,
    hasCompanyContext,
    isPlatformAdmin,
    can(capability, legacyRoles = []) {
      if (isPlatformAdmin) return true;
      if (hasCompanyContext) return capabilities.has(capability);
      // Sin empresa resuelta no hay capacidades que preguntar, y el puente
      // legacy es el único camino que queda. Existe SÓLO para quien no tiene
      // ninguna membresía: quien sí la tiene y no eligió empresa no pasa por
      // aquí, igual que no pasa en el servidor.
      if (dashboard !== null) return false;
      return typeof user.role === "string" && legacyRoles.includes(user.role);
    },
  };
}
