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
 * Sin contexto de empresa manda el rol legacy, pero SÓLO si el servidor confirma
 * que esa cuenta cruza el puente: es lo que el backend comprueba en ese camino.
 * El master de plataforma pasa siempre.
 *
 * H4.1.2B — ACCESSGUARD-403-LEGACY-01. Antes bastaba con que el panel
 * respondiera 403 para tratar a alguien como legacy, y ese mismo 403 lo recibe
 * quien tiene la membresía REVOCADA: quitarle el acceso le devolvía la interfaz,
 * decidida por su rol global. Un rechazo no es una credencial.
 *
 * NO ES AUTORIDAD. Es lo que la interfaz muestra; cada endpoint vuelve a
 * decidir, y sigue respondiendo 403 a quien no deba pasar.
 */

import type { AuthUser } from "../../lib/auth";
import type { InternalDashboard } from "./internal-api";

export type InternalAccess = {
  user: AuthUser;
  /** `null` = sin contexto de empresa. Por sí solo no concede nada. */
  dashboard: InternalDashboard | null;
  hasCompanyContext: boolean;
  isPlatformAdmin: boolean;
  /** Lo dijo el servidor, no se dedujo de un 403. */
  isLegacyBridge: boolean;
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
  /** Lo afirma el backend en el 403; nunca se infiere del código de estado. */
  legacyBridge = false,
): InternalAccess {
  const isPlatformAdmin = Boolean(dashboard?.access.is_platform_admin);
  const hasCompanyContext = Boolean(dashboard?.company);
  const capabilities = new Set(dashboard?.access.capabilities ?? []);
  const isLegacyBridge = legacyBridge === true && dashboard === null;

  return {
    user,
    dashboard,
    hasCompanyContext,
    isPlatformAdmin,
    isLegacyBridge,
    can(capability, legacyRoles = []) {
      if (isPlatformAdmin) return true;
      if (hasCompanyContext) return capabilities.has(capability);
      // Sin empresa resuelta no hay capacidades que preguntar. Queda el puente
      // legacy, y sólo lo abre quien puede saberlo: el servidor. Una membresía
      // revocada llega hasta aquí exactamente igual que un operador pre-SaaS, y
      // la diferencia entre los dos no está en el cliente.
      if (!isLegacyBridge) return false;
      return typeof user.role === "string" && legacyRoles.includes(user.role);
    },
  };
}
