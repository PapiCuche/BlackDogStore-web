# Servicio Técnico — el 401 del panel web

**REPRODUCIDO.** 10 de septiembre de 2026, entorno de desarrollo local.

## Lo que se observó

| | |
|---|---|
| **URL** | `http://localhost:3000/admin/service` |
| **Cuentas** | `dev_admin` y `dev_technician` (ambas fallan igual) |
| **Empresa** | `black-dog-store` (piloto) |
| **HTTP en pantalla** | 200 — la página carga |
| **Peticiones que fallan** | **401** |

Peticiones con 401, idénticas en ambas cuentas:

    GET  /api/v1/internal/black-dog-store/service/context
    GET  /api/v1/internal/black-dog-store/service/orders?mine=true
    GET  /api/v1/internal/black-dog-store/notifications/unread-count
    POST /api/auth/refresh

Cuerpo de la respuesta:

    {"detail": "Las credenciales de autenticación no se proveyeron."}

Ése es el texto que motivó el reporte. No dice «áreas»: es el mensaje por defecto
de `NotAuthenticated` de Django REST Framework.

## La causa

`store/v1_internal_views.py`, `V1InternalSurfaceMixin`:

    authentication_classes = [V1BearerAuthentication]

**Sólo Bearer.** El panel web se autentica con cookies HttpOnly; no tiene ningún
token Bearer que enviar. Toda llamada desde el navegador a esa API es un 401.

No es un problema de esta pantalla. El panel web llama a la API **nativa** en
varios sitios:

| Superficie | Llama a |
|---|---|
| Servicio técnico | `/v1/internal/{slug}/service/...` |
| Campana de notificaciones | `/v1/internal/{slug}/notifications/...` |
| Comunicados | `/v1/internal/{slug}/communications` |
| Anuncios | `/v1/internal/{slug}/announcements/...` |
| Galería de evidencias | `/v1/internal/{slug}/service/orders/{id}/evidence` |

Todas son Bearer. Todas devuelven 401 desde el navegador.

## Hipótesis descartadas

**No faltan áreas.** La empresa piloto tiene las siete —Administración, Ventas,
Inventario, Servicio Técnico, Recepción, Control de Calidad y Caja— y todas
están activas. Se comprobó consultando las filas.

**No es falta de capacidades.** `/api/admin/capabilities/` responde **200** a las
mismas cuentas. Devuelve `held_by_me: []` sólo porque se consultó sin el
parámetro `company`, que es su comportamiento documentado.

**No es aprovisionamiento incompleto.** La empresa histórica tiene sus presets.

## Un matiz que conviene no pasar por alto

La base de desarrollo tiene **0 órdenes de reparación**, así que el «0 orden(es)»
que muestra la pantalla es honesto. Eso **enmascara** el problema: la lista
parece vacía en vez de rota. Lo que no se puede disimular es que
`service/context` y la campana de notificaciones fallan siempre.

## Por qué NO se arregla dentro de H4.1

Las dos salidas posibles quedan fuera del alcance de esta fase, y una de ellas
tiene un riesgo que hay que decidir a propósito:

**Añadir autenticación por cookie a la API v1.** El propio módulo explica por qué
hoy no la tiene:

> NO CSRF. There is no cookie here, so there is no cross-site request forgery to
> enforce against.

Añadir cookies **abriría CSRF en cada mutación** de esa API. Hacerlo requiere
además montar el CSRF que hoy no existe ahí. No es un arreglo de paso.

**Reescribir la superficie web sobre la API de panel.** No existe equivalente:
no hay rutas `admin/service/...`. Habría que crearlas, y eso es el ciclo de vida
del servicio técnico, explícitamente fuera de H4.1 (§54). Además alcanza a
comunicados, que es H4.2 (§51).

## Clasificación

**PENDIENTE** — reproducido, causa identificada con evidencia, fuera del alcance
de H4.1 por decisión y no por olvido.

Recomendación: tratarlo como fase propia. La decisión de fondo —si el panel web
debe hablar con la API nativa o tener la suya— afecta a servicio técnico,
notificaciones y comunicados a la vez.
