/**
 * NAV-SALES-01 — «Venta rápida de equipo» is the till opened on its device block: `/admin/sales/pos?panel=equipo`.
 * The menu entry and the till agree on the name through this file.
 */
export const POS_DEVICE_PANEL = "equipo";

export function wantsDevicePanel(query: string): boolean {
  return new URLSearchParams(query.startsWith("?") ? query.slice(1) : query).get("panel") === POS_DEVICE_PANEL;
}
