# Fuentes servidas desde el proyecto

Las tres familias se sirven desde aquí con `next/font/local`, en lugar de descargarse de Google Fonts en cada build. Esa descarga fallaba de forma intermitente y rompía builds de release y de CI (`@vercel/turbopack-next/internal/font/google/font`).

| Familia | Uso | Archivo | Pesos (variable) | SHA-256 |
|---|---|---|---|---|
| Inter | texto (`--font-body`) | `inter/inter-latin-wght-normal.woff2` | 100–900 | `3100e775e8616cd2611beecfa23a4263d7037586789b43f035236a2e6fbd4c62` |
| Unbounded | títulos V3 (`--font-display`) | `unbounded/unbounded-latin-wght-normal.woff2` | 200–900 | `22f9b928aa3928a896340422b54d690fbb5875c30eae4c106f029c61f2289fd6` |
| Montserrat | vista previa V4 (`--font-v4`) | `montserrat/montserrat-latin-wght-normal.woff2` | 100–900 | `06b16db7a969135d48d38c49183be7fb88d4452e2a3011957c7851941f4e4879` |

**Procedencia:** paquetes npm `@fontsource-variable/{inter,unbounded,montserrat}@5.3.0` (repositorio `github.com/fontsource/font-files`), sacados con `npm pack` el 2026-11-17. No son dependencias del proyecto: sólo se copió el subconjunto latino (el mismo `subsets: ["latin"]` de antes, que incluye á é í ó ú ñ ü ¿ ¡).

**Licencia:** SIL Open Font License 1.1. El texto de cada familia, con su copyright, está en `*/OFL.txt` y debe acompañar a la fuente.

Para actualizar: volver a sacar el paquete con `npm pack`, comprobar la licencia, reemplazar el `.woff2` y actualizar esta tabla.
