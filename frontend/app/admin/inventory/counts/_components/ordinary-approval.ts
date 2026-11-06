/**
 * Approving an ORDINARY count from its page, until MIGRATION-F's review screen
 * exists (a migration count is never approved here).
 *
 * Kept apart from `counts-api` on purpose: that module is the counting screen's
 * and has no approval in it. It is the page's only approval call
 * (`lib/inventory.approveCount`, which approved without a version, is gone). It
 * sends the `version` on screen and reads a refusal in full: `approval_blocked`
 * comes with one reason per line, and the page shows them.
 */

import { fetchWithAuth } from "../../../../lib/auth";
import { API_BASE } from "../../../../lib/api";
import type { StockMovement } from "../../../../lib/inventory";
import { readCountError, type CountDetail } from "../../../lib/counts-api";

/**
 * The server's `MAX_SELF_APPROVAL_REASON` (DEC-INV-06): the page refuses a longer reason itself instead of letting
 * the server keep only its first 250 characters in silence.
 */
export const MAX_SELF_APPROVAL_REASON = 250;

export async function approveOrdinaryCount(
  id: number,
  version?: string,
  selfApprovalReason?: string,
): Promise<CountDetail & { movements: StockMovement[] }> {
  // COUNT-REVIEW-FREEZE: the version the approver is looking at. If the count is no longer that, the server
  // answers 409 `count_stale` and applies nothing. DEC-INV-06: approving one's own count (only with «Aprobar
  // recuentos propios») carries its reason; without one the server answers 400 and applies nothing.
  const body = {
    ...(version ? { version } : {}),
    ...(selfApprovalReason ? { self_approval_reason: selfApprovalReason } : {}),
  };
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/counts/${id}/approve/`, {
    method: "POST",
    ...(Object.keys(body).length ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) throw await readCountError(res, "No se pudo aprobar el recuento.");
  return res.json();
}
