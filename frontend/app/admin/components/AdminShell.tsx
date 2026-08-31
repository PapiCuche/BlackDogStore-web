"use client";

/**
 * Internal control shell — Phase 2A.2.
 *
 * Replaces the old horizontal-tab layout with a sidebar + topbar application
 * frame. Used by every /admin page, so the whole internal surface now feels like
 * one application rather than an extension of the storefront.
 *
 * IMPORTANT — this is layout, not authorisation.
 * Each page keeps its own guard (StaffGuard / AdminGuard / InternalControlGuard)
 * and every endpoint re-checks server-side. Changing the frame changed no
 * permission.
 *
 * The shell fetches the company context itself and DEGRADES GRACEFULLY: an
 * operator with a legacy staff role but no Membership still gets the panel, with
 * navigation driven by their legacy role. Requiring a Membership here would lock
 * out every existing operator until their company adopts memberships.
 */

import { useEffect, useState } from "react";
import { InternalSidebar, MobileSidebar } from "./InternalSidebar";
import { InternalTopbar } from "./InternalTopbar";
import {
  NoInternalAccessError,
  fetchInternalDashboard,
  type InternalDashboard,
} from "../lib/internal-api";
import type { ModuleAccessContext } from "../lib/internal-modules";
import type { AuthUser } from "../../lib/auth";

type Props = {
  user: AuthUser;
  /** Passed by pages that already loaded it, to avoid a second request. */
  dashboard?: InternalDashboard | null;
  onSelectCompany?: (companyId: number) => void;
  children: React.ReactNode;
};

export function buildAccessContext(
  user: AuthUser,
  dashboard: InternalDashboard | null,
): ModuleAccessContext {
  return {
    capabilities: dashboard?.access.capabilities ?? [],
    // Falls back to the session role so the legacy-only operator keeps their nav.
    legacyRole: dashboard?.access.legacy_role ?? user.role ?? null,
    isPlatformAdmin: Boolean(dashboard?.access.is_platform_admin),
    hasCompanyContext: Boolean(dashboard?.company),
  };
}

export function AdminShell({ user, dashboard, onSelectCompany, children }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [ownDashboard, setOwnDashboard] = useState<InternalDashboard | null>(null);
  const [selfLoaded, setSelfLoaded] = useState(dashboard !== undefined);

  // Only pages that did not already load the context pay for a request.
  useEffect(() => {
    if (dashboard !== undefined) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchInternalDashboard();
        if (!cancelled) setOwnDashboard(data);
      } catch (err) {
        // No company access is a legitimate state (legacy operator), not an error.
        if (!cancelled && !(err instanceof NoInternalAccessError)) {
          setOwnDashboard(null);
        }
      } finally {
        if (!cancelled) setSelfLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dashboard]);

  const effective = dashboard !== undefined ? dashboard : ownDashboard;
  const access = buildAccessContext(user, effective);

  return (
    <div className="min-h-[calc(100vh-64px)] bg-zinc-950">
      <div className="flex">
        <InternalSidebar access={access} companyName={effective?.company?.name} />
        <MobileSidebar
          access={access}
          companyName={effective?.company?.name}
          open={menuOpen}
          onClose={() => setMenuOpen(false)}
        />

        <div className="min-w-0 flex-1">
          <InternalTopbar
            user={user}
            dashboard={selfLoaded ? effective : null}
            onOpenMenu={() => setMenuOpen(true)}
            onSelectCompany={(companyId) => onSelectCompany?.(companyId)}
          />
          <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
