# Política de eficiencia de contexto

Se ahorra en narración, contexto repetido, logs y exploración redundante.
**No** se ahorra en seguridad, tests, validaciones ni aislamiento entre empresas.

Pregunta antes de cada lectura o respuesta: *¿esto cambia una decisión?* Si no, se omite.

## A. No repetir contexto
Arquitectura, stack, reglas de negocio, roadmap, historial y PR antiguos se leen de sus archivos
(`AGENTS.md` §1). No se repiten en prompts ni en respuestas.

## B. Lectura dirigida
Primero `rg`, `git diff --stat`, `git diff --name-only`, `git diff <base>...<head>`, `git log`,
`git show`. Buscar el símbolo o el encabezado y leer ese tramo. No abrir 30 archivos si el cambio toca 3.
`git blame` sólo si hace falta.

Para «¿dónde está X?»: `rg "X"` y `docs/CODEBASE_MAP.md` antes de recorrer carpetas.
Para «¿qué cambió?»: el diff antes que los archivos.
Para «¿está implementado?»: código, migraciones y tests antes que documentación histórica.

## C. No releer lo que no cambió
Un archivo ya auditado en la fase y sin cambios no se vuelve a leer entero: `git diff`, el SHA o el
hash del árbol bastan. Una compuerta se reutiliza si el árbol validado es idéntico.

## D. Logs
Pasa: comando, cantidad, `PASS`. Falla: el test, el error y el tramo mínimo de traza.
Nunca el log completo salvo necesidad técnica. Filtrar la salida en el propio comando (`grep`, `tail`).

## E. Respuestas
Checkpoint por fase, como máximo:

```
FASE:
ESTADO:
CAMBIOS:
TESTS:
CI:
RIESGOS:
COMMIT/PR:
SIGUIENTE:
```

Una tabla u ocho líneas antes que una narración. 50 tests verdes no se enumeran.

## F. GitHub
Rama antigua: `git rev-list --left-right --count base...head` y `git diff --stat` primero. Si está
cientos de commits detrás y superada, es `OBSOLETO`: no se abre archivo por archivo.

## G. Código
En las respuestas: archivo, símbolo, líneas relevantes y resumen del diff. No archivos completos.

## H. Documentación
`02_ESTADO_ACTUAL.md` y `07_CHANGELOG.md` son la memoria persistente. Sus entradas no se repiten en el chat.
No se crean informes paralelos para la misma información.

## I. Continuidad
Al cerrar una fase se sobrescribe `docs/NEXT_ACTION.md`. Un agente nuevo continúa con:
«Lee `AGENTS.md` y `docs/NEXT_ACTION.md` y continúa.»

## J. No sobreanalizar
Contrato claro, tests existentes, cambio pequeño y fuera de arquitectura crítica: se implementa.
No se escribe un ensayo para una corrección de tres líneas.

## K. Paralelismo
En paralelo: lecturas independientes. Nunca en paralelo: mutaciones dependientes, migraciones, tests
que comparten base de datos, merges dependientes.

Subagentes: sólo para tareas independientes, normalmente 2–3 a la vez, con objetivo estrecho, archivos
o rango concretos y respuesta corta («máximo 15 puntos»). Nunca «audita toda la aplicación».

## L. Condición de parada
No se sigue explorando una hipótesis cuando ya hay evidencia para aceptarla o descartarla.

## M. Errores
Identificar → localizar la causa → corregir → test focal → compuerta relevante → seguir.
Explicación extensa sólo si hubo incidente de seguridad, riesgo de corrupción, migración problemática
o decisión de arquitectura.

## Herramientas
No hay ninguna skill de mapa de código instalada ni verificable (Graphify/Grapiphy no existe en el
entorno): no se instala. `docs/CODEBASE_MAP.md` cumple esa función. No instalar paquetes ni skills de
procedencia desconocida. Disponibles y suficientes: `rg`, `git`, `gh`, Playwright del repositorio, Docker.
