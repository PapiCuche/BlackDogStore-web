/**
 * Approving an ORDINARY count from its page, until MIGRATION-F's review screen
 * exists (a migration count is never approved here).
 *
 * Kept apart from `counts-api` on purpose: that module is the counting screen's
 * and has no approval in it. What this adds over `lib/inventory.approveCount`
 * is the refusal read in full: `approval_blocked` comes with one reason per
 * line, and the page shows them.
 */

import { fetchWithAuth } from "../../../../lib/auth";
import { API_BASE } from "../../../../lib/api";
import type { StockMovement } from "../../../../lib/inventory";
import { readCountError, type CountDetail } from "../../../lib/counts-api";

export async function approveOrdinaryCount(id: number): Promise<CountDetail & { movements: StockMovement[] }> {
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/counts/${id}/approve/`, { method: "POST" });
  if (!res.ok) throw await readCountError(res, "No se pudo aprobar el recuento.");
  return res.json();
}
