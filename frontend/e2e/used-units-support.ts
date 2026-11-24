import type { Page } from "@playwright/test";

/**
 * One device in «Equipos seminuevos»: its table row from `md`, its card below it. Only the visible layout has an
 * accessible role (`getByRole` skips `display: none`), so this is the one the person sees at the current width —
 * and a `toHaveCount(0)` on it is never vacuous at phone width.
 */
export const usedEntry = (page: Page, text: string) => page.getByTestId("used-units-table").getByRole("row")
  .or(page.getByTestId("used-units-cards").getByRole("listitem")).filter({ hasText: text });
