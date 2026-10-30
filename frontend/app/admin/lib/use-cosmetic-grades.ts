"use client";

/**
 * INV-RECEIVE-UX-02 — the company's cosmetic grades («Muy bueno», «Con detalles»…), read once, only when a used
 * device is on screen. A person who cannot read the catalogue (or an error) simply gets no grade list: the field
 * is not offered, and the reception goes on without it.
 */
import { useEffect, useRef, useState } from "react";

import { fetchCosmeticGrades, type CosmeticGradeRow } from "./internal-api";

export function useCosmeticGrades(enabled: boolean, companyId: number | null): CosmeticGradeRow[] {
  const [grades, setGrades] = useState<CosmeticGradeRow[]>([]);
  const asked = useRef(false);
  useEffect(() => {
    if (!enabled || asked.current) return;
    asked.current = true;
    fetchCosmeticGrades(companyId)
      .then((rows) => setGrades(rows.filter((g) => g.is_active)))
      .catch(() => { setGrades([]); asked.current = false; });
  }, [enabled, companyId]);
  return grades;
}
