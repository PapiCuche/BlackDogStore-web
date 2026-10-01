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

## Resolución — H4.1.1

**RESUELTO** en la rama `feat/h4-1-1-web-v1-auth-interop`. Decisión completa en
[adr-auth-v1-internal.md](adr-auth-v1-internal.md).

La salida elegida fue la primera de las dos que este documento dejaba abiertas
—aceptar la cookie en la API v1— y el riesgo que anotaba se resolvió de frente
en vez de esquivarlo:

- **CSRF**: la cookie trae su CSRF. `CookieJWTAuthentication` lo exige dentro de
  `authenticate()` para cada método no seguro, y eso no se tocó.
- **Confusión de credenciales**: `V1InternalAuthentication` admite **un** canal
  por request. Header y cookie a la vez es 401; un `Authorization` explícito
  nunca cae a la cookie.
- **Métodos seguros sin CSRF**: antes de activar la cookie se ejecutaron las 70
  rutas internas contando SQL de escritura en GET, HEAD y OPTIONS — cero.

No se creó una API web paralela: el panel sigue hablando con la misma superficie
que la app nativa.

Verificado contra el servidor en marcha con la cuenta `dev_technician`:

| Petición | Antes | Después |
|---|---|---|
| `GET …/service/context/` con cookie | 401 | **200** |
| `GET …/notifications/unread-count/` con cookie | 401 | **200** |
| `POST …/notifications/read-all/` con cookie, sin CSRF | 401 | **403** |
| `POST …/notifications/read-all/` con cookie y CSRF | 401 | **200** |
| `GET …/service/context/` con Bearer | 200 | 200 |
| `GET …/service/context/` con Bearer **y** cookie | 200 (sólo Bearer) | **401** |
