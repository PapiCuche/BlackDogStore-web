# P2 · Tienda premium con el Manual V4 — plan (2026-11-16)

**Estado del Manual V4.0:** está sujeto a ratificación interna. Mientras no se adopte formalmente, la V3 sigue siendo el diseño de todas las tiendas y la V4 sólo se ve cuando alguien la elige. Fuentes: el Manual de Marca V4.0 (PDF del propietario) y el Kit Visual V4, que contiene 6 logos PNG, ningún vector y 11 maquetas de referencia que no deben publicarse tal cual.

## Qué existe y qué falta

| Requisito | Hoy | Falta |
|---|---|---|
| Configurador de nuevos | `ModelDetail.tsx`: `NewLine` + `VariantPicker`, con precio y disponibilidad reales, galería por variante y venta «bajo pedido». Datos de `device_catalog.public_model()`. | La lógica está mezclada con la presentación. La garantía no se ve. |
| Configurador de seminuevos | `UsedLine` + `UsedUnitCard`: fotos reales por equipo, batería (sólo si hay lectura), grado e IMEI parcial. | La garantía no se ve. El grado y las notas son texto libre. |
| Stock | La API pública sólo da el estado (`available` y `availability`). | Decisión del propietario: ¿mostrar cantidades? |
| Garantía | Un solo texto por empresa (`CompanySettings.warranty_policy_text/url`), enlazado sólo desde el pie. | Una garantía para nuevos y otra para seminuevos. |
| Accesorios compatibles | **No existe ninguna relación** entre accesorios y modelos. | Modelo, servicio, panel y API (F3, con migración). |
| Imágenes propias | Ya existe la tubería de subida (`StorefrontImage`, `product_media`, `UnitPhoto`). | Nada avisa de las imágenes externas, por ejemplo las del CDN de Apple. |
| Tipografía y tokens | Inter + Unbounded globales; `--brand-*` desde el panel (ya llevan la paleta V4). | Montserrat y la escala del manual, sólo dentro de V4. El foco no puede ser dorado sobre blanco cálido (2,12:1). |
| Soporte visible | Sólo en el pie. | Una franja de soporte en la ficha y en el carrito. |
| Activación | No existe. | Ver la sección siguiente. |

## Cómo se activa V4 sin sustituir la V3

- **Ahora:** una ruta de vista previa `/v4/...`.
  - No lleva migración.
  - Lleva `noindex` y no entra en el sitemap.
  - La empresa se resuelve por el host, como en el resto de la tienda.
  - Para quitarla basta con borrar la carpeta. La V3 no cambia.
- **Cuando el propietario adopte V4:** un interruptor por empresa, `StorefrontPageSettings.storefront_design` (`v3`/`v4`, valor por defecto `v3`).
  - Lleva una migración aditiva.
  - Para volver atrás, el propietario elige V3 desde el panel.

## Fases (un PR cada una)

1. **F1 · Vista previa del configurador V4 (sin migración).** En PR desde el 2026-11-16 (rama `feat/storefront-v4-preview-f1`).
   - La lógica de `ModelDetail` pasa a hooks (`lib/model-configurator.ts`) sin cambio visual en V3.
   - Rutas `app/v4/layout.tsx` (Montserrat 400/500/700, `data-design="v4"`, noindex) y `app/v4/modelo/[slug]/page.tsx`.
   - La ficha muestra modelo, variante, condición, precio, disponibilidad, fotos y la garantía si está configurada (si no, no dice nada).
   - Franja de soporte.
   - Rótulo «Imagen referencial» cuando la foto no viene del panel.
2. **F2 · LCP y portada V4 (sin migración).**
   - El modelo se carga en el servidor y se pasa como dato inicial.
   - Portada V4 en la vista previa.
   - Carrito con pasos y la explicación de cuándo se calcula el envío.
3. **F3 · Accesorios compatibles (migración).** Backend, API y panel en PR desde el 2026-11-16 (rama `feat/accessory-compat-f3`, migración 0171). La ficha V4 los mostrará después.
   - Modelo `AccessoryCompatibility(company, model, accessory, sort_order)`, de una sola empresa y auditado.
   - Editor en el panel.
   - `public_model()` añade `accessories`, sólo con productos activos y publicados. Sin datos no se muestra ningún bloque.
4. **F4 · Imágenes propias (sin migración).**
   - Informe en el panel de las imágenes externas, con enlace a la subida masiva.
5. **F5 · Interruptor por empresa y personalización (migración).**
   - `storefront_design`.
   - Garantía por línea, nuevos y seminuevos, que sólo se muestra si está escrita.
   - Espacio editorial «La Manada»: nunca un muro de compra.
   - Avisos en el panel ante frases vetadas por el control de afirmaciones.

## Decisiones del propietario

1. ¿Vista previa ahora e interruptor por empresa al adoptar V4? (recomendado)
2. ¿Qué garantía respalda la política para nuevos y para seminuevos (plazo y enlace)?
3. Stock: ¿sólo el estado o también la cantidad?
4. Accesorios: ¿quién carga las compatibilidades y se muestran también los agotados?
5. Imágenes del CDN de Apple: ¿se ocultan en V4 o se muestran como «Imagen referencial»?
6. ¿Se ratifica el Manual V4: Montserrat y el dorado como acento entre el 3 y el 5 %?
7. ¿Qué contenido tendrá «La Manada»?

## Riesgos

- **Varias empresas:** V4 es un diseño de la plataforma. Colores, textos y logos salen de la configuración de cada empresa; nunca el dorado de Black Dog fijado en el código.
- **LCP:** la ficha de modelo se pinta hoy en el navegador. F2 lo corrige.
- **Afirmaciones:** el código nunca escribe «oficial», «100 %», «impecable» ni una garantía que no esté configurada.
- **Licencias:** las fotos del CDN de Apple contradicen el manual. F4 las hace visibles y la solución real son fotos propias.
- **Hallazgo aparte:** la validación «botón con texto necesita destino» de la portada está en `StorefrontImage.clean()` en lugar de `StorefrontPageSettings`, así que nunca se aplica. Se corrige en un PR propio.
