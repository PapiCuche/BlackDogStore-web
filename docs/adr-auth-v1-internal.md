# ADR — Autenticación web y nativa en la superficie interna v1

| | |
|---|---|
| **Estado** | Aceptada — H4.1.1 |
| **Fecha** | 2026-09-10 |
| **Alcance** | `/api/v1/internal/<company_slug>/…` (70 rutas) |
| **Relacionadas** | DEC-003 de la documentación maestra externa (JWT web en cookie HttpOnly + CSRF), DEC-API-001 (cuatro audiencias, cuatro superficies), [h41-servicio-tecnico-401.md](h41-servicio-tecnico-401.md) |

## 1. Contexto

La plataforma tiene dos clientes con dos contratos de autenticación distintos, y los dos son correctos para quien los usa:

| Cliente | Credencial | CSRF | Por qué |
|---|---|---|---|
| **Web** (panel y tienda) | Access token JWT en cookie `HttpOnly` (`blackdog_access`) | **Sí**, en métodos no seguros | El navegador adjunta cookies a peticiones que la persona no inició. El token nunca es legible por JavaScript (DEC-003). |
| **Nativo** (app móvil) | `Authorization: Bearer <access>` | **No** | La app envía el token a propósito; una página ajena no puede hacer que lo adjunte. |

La superficie interna v1 se construyó para la app nativa y aceptaba **sólo Bearer**. El panel web la consume desde H2 —servicio técnico, evidencias, notificaciones, comunicados— porque la regla del proyecto es **una sola API por dominio**: Web y Mobile responden a la misma autoridad.

## 2. Problema

Reproducido con la cuenta `dev_technician` y una sesión de cookie válida:

| Petición | Resultado |
|---|---|
| `GET /api/auth/me/` (cookie) | 200 |
| `GET /api/me/internal-dashboard/` (cookie) | 200 — 12 capacidades de servicio |
| `GET /api/v1/internal/black-dog-store/service/context/` (cookie) | **401** «Las credenciales de autenticación no se proveyeron.» |
| La misma ruta con `Authorization: Bearer` del mismo usuario | 200 |

Consecuencias medidas:

- 51 de las 70 rutas internas tienen consumidor web y estaban inutilizables desde el navegador.
- La campana de notificaciones, presente en todo el panel, fallaba en silencio.
- **Tormenta de refresh**: cada 401 disparaba `POST /api/auth/refresh/`, que rota el refresh token y lo pone en lista negra, y luego reintentaba para volver a recibir 401. Una fila `OutstandingToken` y una `BlacklistedToken` por intento; la base de desarrollo acumulaba 978 y 695.

## 3. Decisión

### 3.1 Una request, un canal de autenticación

`V1InternalAuthentication` ([v1_internal_authentication.py](../backend/store/v1_internal_authentication.py)) decide **qué credencial se evalúa** mirando lo que el cliente **presentó**, antes de validar nada:

| `Authorization` presente | Cookie de acceso no vacía | Resultado |
|---|---|---|
| sí | sí | **401**, sin comparar identidades — aunque ambas sean del mismo usuario |
| sí (`Bearer <token>`) | no | `V1BearerAuthentication`: sin CSRF |
| sí (cualquier otra cosa: `Basic`, `Token`, vacío, malformado) | no | **401** |
| no | sí | `CookieJWTAuthentication`: CSRF en métodos no seguros |
| no | no | anónimo → `IsAuthenticated` responde 401 |

No reimplementa JWT, usuarios, cookies, CSRF ni lista negra: **orquesta** las dos clases existentes, que no se modifican.

### 3.2 Un `Authorization` explícito nunca cae a la cookie

`V1BearerAuthentication` devuelve `None` ante un esquema ajeno para que otra clase pruebe. Aquí eso sería una degradación silenciosa: quien presentó un header recibe ese header evaluado, o un 401. Por eso se distingue **header ausente** de **header presente pero vacío o malformado**.

### 3.3 CSRF sólo en el canal cookie, y conservado como 403

El CSRF vive dentro de `CookieJWTAuthentication.authenticate()`. El orquestador sólo normaliza `AuthenticationFailed`; el `PermissionDenied` del CSRF atraviesa intacto como **403**. Un fallo de CSRF no es un fallo de autenticación y nunca se convierte en 401.

### 3.4 Errores uniformes

Todos los fallos de autenticación de esta superficie responden «Credenciales inválidas.», también por cookie. «Token expirado», «usuario inactivo» y «usuario inexistente» son hechos sobre una cuenta que no se le deben a quien no se autenticó.

### 3.5 Desafío `WWW-Authenticate: Bearer realm="api"`

Se conserva para los 401 de esta superficie. Nombra el contrato de la URL v1, no una exigencia a la web: el navegador se autentica por cookie y no lee ese header.

### 3.6 Frontera

El orquestador se declara **sólo** en `V1InternalSurfaceMixin`, y por herencia cubre las 70 rutas internas. Un test recorre el urlconf entero y falla si aparece en cualquier otra ruta o falta en una interna.

**No cambian:** `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES` (sólo cookie), `/api/admin/*`, `/api/auth/*`, `/api/v1/auth/*`, `/api/v1/customer/*`, `/api/v1/platform/*`, ni ninguna configuración de SimpleJWT.

### 3.7 Los métodos seguros son de sólo lectura — condición previa

El canal cookie exime de CSRF a GET, HEAD y OPTIONS, como toda la web. Eso sólo es seguro si esos métodos no escriben. Antes de activar el canal se ejecutaron **las 70 rutas** con una orden de servicio llevada al final de su ciclo (diagnóstico, cotización aprobada con repuesto, ejecución, consumo, control de calidad, cobro y evidencia), más pedido comercial, transferencia y comunicado publicado:

| Métodos ejecutados | Sentencias `INSERT`/`UPDATE`/`DELETE` |
|---|---|
| OPTIONS en 70 rutas · GET y HEAD en 41 | **0** |

`H411SafeMethodsAreReadOnlyTest` lo repite por los dos canales en cada ejecución de la suite y falla ante la primera escritura. Una ruta nueva sin fixture también la hace fallar: una ruta que nadie ejecuta es una ruta que nadie auditó.

### 3.8 Cliente web

- **Refresh single-flight**: varias peticiones que reciben un 401 recuperable comparten **un** refresh; cada una reintenta **una** vez. 403, 404, las rutas de autenticación y cualquier petición con `Authorization` explícito no refrescan.
- **`FormData`**: `fetchWithAuth` no fija `Content-Type` cuando el cuerpo es un formulario; el navegador pone `multipart/form-data` con su `boundary`. Antes forzaba JSON y la subida de evidencias respondía 400 aun con la autenticación resuelta.
- **`next` seguro**: sólo rutas locales; nunca `router.push` de un valor sin validar.
- **Acceso interno**: la cabecera y el destino tras el login usan `/api/me/memberships/` (membresías activas de empresas activas, o master de plataforma), no `UserProfile.role`.

## 4. Por qué se rechazó apilar las dos clases en DRF

DRF prueba los autenticadores en orden: el primer resultado gana y la primera excepción aborta. La combinación se **ejecutó** contra este código antes de decidir:

| Caso | `[Bearer, Cookie]` | `[Cookie, Bearer]` |
|---|---|---|
| Bearer inválido + cookie válida | 401 | **200 vía cookie — degradación** |
| Bearer de A + cookie de B | **200 como A, ignorando B** | **200 como B** |
| Bearer de A + cookie de B, POST sin CSRF | **200 como A, sin CSRF** | 403 |
| `Basic abc` + cookie válida | **200 vía cookie** | **200 vía cookie** |
| Cookie inválida sola | 401 con desafío «Bearer» | 401 con desafío «Cookie» |

Ningún orden cumple a la vez «un Bearer inválido no cae a la cookie» y «dos identidades no se resuelven eligiendo una».

## 5. Alternativas rechazadas

| Alternativa | Por qué no |
|---|---|
| Rutas `/api/admin/service/*` para la web | Dos implementaciones de las mismas reglas de dominio que divergen en el primer cambio |
| Bearer global en DRF | Abriría `/api/admin/`, `/api/auth/me/` y toda vista privada web a un token del contrato nativo |
| Guardar el access token en `localStorage` y enviarlo como Bearer | Expone el JWT a cualquier script de la página (contradice DEC-003) |
| Aceptar Bearer + cookie si son el mismo usuario | Obliga a validar y comparar dos tokens con caducidades distintas; «presentó dos canales» ya es el error |
| Activar la cookie ruta por ruta | Una ruta olvidada vuelve a romper la web en silencio; el mixin es el único punto de control |
| Emitir un token distinto para la web | El `AccessToken` es el mismo en ambos contratos; separarlo es un cambio de SimpleJWT y de revocación fuera de alcance |

## 6. Amenazas consideradas

| Amenaza | Control |
|---|---|
| CSRF sobre mutaciones internas vía cookie | CSRF dentro del canal cookie (403); `SameSite=Lax` como defensa adicional |
| GET con efectos ejecutable desde una página ajena | Auditoría de 70 rutas y prueba permanente de cero escrituras |
| Confusión de identidad (dos credenciales) | 401 ante cualquier doble credencial |
| Degradación de un Bearer inválido a la cookie | Header explícito cierra el canal cookie |
| Enumeración de tenants | Puertas idénticas por ambos canales: 404 para empresa ajena, inactiva o sin membresía |
| Fuga del token | Nunca en cuerpos ni logs; la web no recibe JWT en respuestas |
| Churn de tokens en base de datos | Refresh single-flight y reintento único |
| Redirección abierta vía `?next=` | Validación de ruta local |

## 7. Consecuencias y deuda asociada

- La separación web/nativa es **de transporte**: ambos contratos validan el mismo `AccessToken`. La barrera real para la web es `HttpOnly`.
- **AUTH-REVOCATION-01** — el access token no consultaba nada: tras el logout seguía siendo válido hasta 30 minutos. **Resuelto en H4.1.2B**: el logout revoca esa credencial por `jti` y el cambio de contraseña cierra todas las sesiones con un sello por usuario. Diseño y alternativas descartadas en [adr-token-revocation.md](adr-token-revocation.md).
- **BRANCH-SCOPE-01** — los pedidos comerciales de v1 interno no filtraban por sucursal. Preexistente e igual por ambos canales; no introducido aquí. **Resuelto en H4.1.2** con `tenancy.visible_orders`, que responde igual por Bearer y por cookie.
- **AUDIT-INTERNAL-01** — las lecturas y los rechazos de v1 interno no se auditan.
- `OPTIONS` devuelve metadatos de la vista (nombre y descripción) a cualquier usuario autenticado sin pasar por la puerta de tenant. No revela datos de ninguna empresa ni escribe; queda anotado.

## 8. Verificación en navegador real

`frontend/e2e/h411-auth-interop.spec.ts`, sin mocks del backend: cada petición pasa por el proxy de la aplicación con las cookies HttpOnly del login y el CSRF que exige.

| Escenario | Qué se comprobó |
|---|---|
| **B · admin** | Cliente, equipo y orden creados por cookie; **la misma alta sin CSRF responde 403**; asignación al técnico |
| **A · técnico** | Aterriza en `/admin`; la tienda le ofrece «Control interno»; en Servicio Técnico, `service/context` y `unread-count` dan 200; la orden aparece en «Mis reparaciones»; ningún refresh |
| **G · evidencia** | Foto real por `multipart/form-data` con boundary; 201; aparece en la galería |
| **H · notificaciones** | Contador, bandeja y «Marcar leída» por cookie; el contador baja en uno |
| **C · cliente puro** | Se queda en la tienda; `/admin` responde «Sin acceso interno»; la API interna, 404 |
| **D · cliente y técnico** | Conserva «Pedidos» y «Control interno»; Servicio Técnico responde |
| **E · master** | Elige empresa y el servicio responde para ella |
| **F · invitación** | `/auth?next=/invitacion?token=…` vuelve a la invitación tras el login |

Lo que la prueba creó se borra antes y después con `purge_e2e_data`: sólo filas marcadas `[E2E]` o correos en `e2e.invalid`.

Tres fallos del **arnés** —ninguno del producto— quedan anotados porque volverán a aparecer:

1. El proxy de Next responde **308** a las rutas con barra final y el navegador las repite sin ella. Esperar «la primera respuesta» atrapaba la redirección. Se filtra por estado, no por `redirectedTo()`, que aún está vacío cuando llega la 308.
2. El anunciador de rutas de Next tiene `role="alert"` en todas las páginas. Comprobar «ningún alert» fallaba siempre; se comprueba el mensaje concreto.
3. La tarjeta de accesos de desarrollo carga sus cuentas después de pintar la página. Contarla en cuanto cambia la URL la encontraba vacía y convertía el escenario en un *skipped*, que se parece demasiado a un aprobado.

## 9. Pruebas que protegen esta decisión

| Prueba | Qué fija |
|---|---|
| `H411CredentialChannelTest` | Matriz de canales, doble credencial, esquemas ajenos, header vacío, CSRF 403 por cookie, Bearer sin CSRF, paridad de tenant/sucursal/capacidades por ambos canales, fronteras y ausencia de tokens en respuestas y logs |
| `H411SafeMethodsAreReadOnlyTest` | GET, HEAD y OPTIONS de las 70 rutas no escriben, por ambos canales |
| Pruebas de contrato previas actualizadas | La web SÍ abre la superficie interna; las vistas internas declaran el orquestador |
