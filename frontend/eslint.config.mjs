import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Node.js build scripts — not subject to TS/React rules
    "scripts/**",
    // Instrumental de auditoría: scripts de Node que conducen el navegador.
    // No se publican (están en .gitignore) y no son código de la aplicación.
    ".audit/**",
  ]),
  {
    // The set-state-in-effect rule produces false positives for async functions
    // where all setState calls happen after `await` (not truly synchronous).
    // TODO Phase 2: refactor loadCart/fetchCartCount with useCallback + AbortController.
    rules: {
      "react-hooks/set-state-in-effect": "warn",
    },
  },
]);

export default eslintConfig;
