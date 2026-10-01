"use client";

/**
 * M12E — Claro / Oscuro / Automático.
 *
 * TRES MODOS, UNA ESTRATEGIA. `data-theme` en `<html>` y nada más: mezclar dos
 * motores de tema —una clase y un atributo, o Tailwind `dark:` y variables CSS
 * a la vez— produce estados en los que uno dice claro y el otro oscuro, y el
 * que gana depende del orden de las reglas.
 *
 * EL DEFECTO POR DEFECTO ES `system`. No el tema del piloto: este frontend es
 * multiempresa y arrancar en oscuro porque una tienda concreta es oscura sería
 * convertir su marca en el default de la plataforma.
 *
 * SIN FLASH. La preferencia se resuelve en un script que corre ANTES del primer
 * paint, escrito en el `<head>` por el layout. Leerla en un `useEffect`
 * significa pintar una vez con el tema equivocado y corregir después, que es
 * exactamente el parpadeo blanco que la gente nota al recargar en oscuro.
 *
 * PERSISTENCIA LOCAL, y sin backend. Es una preferencia de interfaz, no un dato
 * de negocio: no necesita modelo, ni migración, ni RBAC. La clave se llama
 * `ui-theme` y no `blackdog-theme` porque el frontend sirve a cualquier tenant.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "ui-theme";

export const THEME_LABELS: Record<ThemeChoice, string> = {
  system: "Automático",
  light: "Claro",
  dark: "Oscuro",
};

/**
 * El script que corre antes del primer paint.
 *
 * Se exporta como cadena para que el layout lo inyecte con
 * `dangerouslySetInnerHTML` — el único uso legítimo de esa API en este
 * proyecto, y lo es porque el contenido es esta constante y no algo que llegue
 * por la red o lo escriba una persona.
 *
 * Envuelto en try/catch: en una ventana privada `localStorage` puede lanzar, y
 * una preferencia de color no puede tumbar la página.
 */
export const THEME_INIT_SCRIPT = `
(function(){try{
  var k=${JSON.stringify(THEME_STORAGE_KEY)};
  var c=localStorage.getItem(k);
  if(c!=="light"&&c!=="dark"&&c!=="system")c="system";
  var r=c==="system"
    ?(window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light")
    :c;
  var e=document.documentElement;
  e.setAttribute("data-theme",r);
  e.style.colorScheme=r;
}catch(e){
  document.documentElement.setAttribute("data-theme","dark");
}})();
`.trim();

type ThemeContextValue = {
  choice: ThemeChoice;
  resolved: ResolvedTheme;
  setChoice: (next: ThemeChoice) => void;
};

const ThemeContext = createContext<ThemeContextValue>({
  choice: "system",
  resolved: "dark",
  setChoice: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function storedChoice(): ThemeChoice {
  if (typeof window === "undefined") return "system";
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    // Ventana privada, o el navegador con el almacenamiento bloqueado.
  }
  return "system";
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Se arranca en `system` en los DOS lados para que el HTML del servidor y el
  // primer render del cliente coincidan. Lo que ya puso el tema correcto es el
  // script del `<head>`; este estado sólo gobierna el selector.
  const [choice, setChoiceState] = useState<ThemeChoice>("system");
  const [resolved, setResolved] = useState<ResolvedTheme>("dark");

  useEffect(() => {
    const stored = storedChoice();
    setChoiceState(stored);
    setResolved(stored === "system" ? systemTheme() : stored);
  }, []);

  // AUTOMÁTICO REACCIONA EN VIVO. Alguien cambia macOS de claro a oscuro con la
  // pestaña abierta y la web sigue: sin esto, «automático» sólo sería
  // «automático la próxima vez que recargues».
  useEffect(() => {
    if (choice !== "system" || typeof window === "undefined") return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolved(query.matches ? "dark" : "light");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [choice]);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    root.setAttribute("data-theme", resolved);
    // `color-scheme` es lo que hace que los controles NATIVOS —inputs, selects,
    // barras de scroll— sigan el modo. Sin esto un formulario en tema oscuro
    // sale con el select blanco del sistema.
    root.style.colorScheme = resolved;
  }, [resolved]);

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next);
    setResolved(next === "system" ? systemTheme() : next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Sin persistencia, la sesión actual sigue funcionando.
    }
  }, []);

  const value = useMemo(
    () => ({ choice, resolved, setChoice }),
    [choice, resolved, setChoice],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}
