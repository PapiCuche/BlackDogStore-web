# ADR — Caducidad por inactividad de la sesión web y acceso con Google, Facebook y Apple (AUTH-SESSION-01 · SOCIAL-AUTH-01)

| | |
|---|---|
| **Fase** | AUTH-A (auditoría + ADR). **Sin código de producto.** |
| **Estado** | `PROPUESTA`. Nada de lo que se decide aquí está implementado. |
| **Base auditada** | `master` @ `5dff6b3` (migraciones hasta `0152`). Producción sirve R1 (`6834bc2`, hasta `0145`). |
| **Relacionadas** | [adr-auth-v1-internal.md](adr-auth-v1-internal.md) (un canal por petición), [adr-token-revocation.md](adr-token-revocation.md) (revocación), DEC-GOOGLE-01, DEC-INT-03, DEC-CUSTOMER-01 en `05_DECISIONES_TECNICAS.md`, PR #122 (Facebook y Apple, abierto, sin fusionar) |
| **Orden de autoridad** | código > migraciones > tests > docs. Referencias `archivo:línea` de `5dff6b3`. |

Cada decisión lleva su estado. Lo que necesita credenciales, dominio o una decisión del propietario se marca
`BLOCKED` y no se inventa.

---

## 0. Resumen

1. **Hoy no existe caducidad por inactividad.** Una pestaña abierta mantiene viva la sesión indefinidamente: un
   sondeo en segundo plano recibe 401, el frontend renueva y reintenta, y cada renovación vuelve a poner el refresh
   a 7 días (§1.2, hallazgo H-1 y H-2).
2. **Decisión central (SESSION-IDLE-01):** una fila `AuthSession` en PostgreSQL con un identificador estable
   (`sid`) que viaja como claim en el refresh y en el access y **sobrevive a la rotación**. Cada petición por cookie
   la consulta; el servidor es la única autoridad sobre «sigue viva». La actividad real la declara el cliente con una
   señal acotada (`X-Session-Activity: 1`), el servidor la registra con granularidad de 30 s y **nunca** la acepta
   del refresh. Caducidad: 30 min de inactividad (nunca más; como mucho 30 s antes), 8 h absolutas para personal.
3. **La app móvil (V1 Bearer) no cambia** en esta fase. Su impacto y la coordinación necesaria están en §3.
4. **Facebook y Apple ya están construidos en el PR #122** (abierto, 444 commits detrás de `master`, probado sólo
   con proveedores simulados). Este ADR **no** propone reescribirlos: propone rebasarlos, partirlos en tres PR y
   cerrar sus deudas (intentos en caché de proceso, `sid`, re-autenticación).
5. **Migraciones de autenticación desde `0154`** (la `0153` es del PR fiscal #179). Nunca en el mismo PR que
   fiscal o etiquetas.

---

## 1. Auditoría: qué hay en `master`

### 1.1 Línea base declarada, verificada

| Afirmación del encargo | Estado | Dónde |
|---|---|---|
| Access JWT 30 min | **CONFIRMADO** | `backend/backend/settings.py:189`; cookie `max_age` igual: `backend/store/auth_views.py:46-51` |
| Refresh 7 días con rotación | **CONFIRMADO, con matiz (H-2)**: la ventana es **deslizante** | `settings.py:190-192`; SimpleJWT 5.5.1 `serializers.py:138-141` (`set_jti`, `set_exp`, `set_iat` al rotar) |
| Cookies HttpOnly + CSRF en web | **CONFIRMADO** | `settings.py:420-433` (`HttpOnly`, `SameSite` sólo `Lax`/`Strict`, `Secure` fuera de DEBUG); CSRF sólo por cabecera en `backend/store/authentication.py:51-69`, aplicado en `:89`; `csrftoken` legible por JS `settings.py:400` |
| Revocación de credenciales existente | **CONFIRMADO** | `backend/store/token_revocation.py` (por `jti` en logout, sello global `UserProfile.tokens_valid_after` en cambio y restablecimiento de contraseña `auth_views.py:562`, `:610`); se pregunta en cada petición `authentication.py:108`, `v1_authentication.py:112`, y antes de rotar `auth_views.py:294`, `v1_auth_views.py:232` |
| Google Identity Services ya implementado | **CONFIRMADO** | `backend/store/google_identity.py:127-169` (RS256 fijo, `aud`, `iss`, `exp` con 30 s, `email_verified`, `nonce` firmado y atado a cookie HttpOnly); `backend/store/google_views.py` |
| Anti-replay de Google | **PARCIAL (H-3)** | `google_identity.py:102-104`: `cache.add` sobre la caché por defecto; el proyecto no define `CACHES` (LocMem **por proceso**) |
| Vinculación y `ExternalIdentity` | **CONFIRMADO** | `models.py:10051-10088` (único `(provider, subject)` y `(user, provider)`), migración `0108`; correo existente ⇒ 409 + contraseña `google_views.py:147-155`, `:195-242` |
| Contrato móvil V1 Bearer separado | **CONFIRMADO** | `backend/store/v1_authentication.py` (nunca global), `v1_internal_authentication.py` (un canal por petición); login y refresh propios `v1_auth_views.py:137-242` |

### 1.2 Hallazgos (diferencias con lo que se suponía)

| ID | Sev. | Hallazgo | Evidencia |
|---|---|---|---|
| **H-1** | ALTA (es el objeto de esta fase) | **No hay caducidad por inactividad.** El frontend renueva ante cualquier 401 recuperable, también los de peticiones automáticas, y reintenta. `AvailabilityPanel` sondea cada 45 s; la campana pide al recuperar el foco. Una pestaña olvidada abierta mantiene la sesión mientras el refresh no caduque, y el refresh no caduca (H-2). | `frontend/app/lib/auth.ts:77-115`, `:258-270`; `frontend/app/admin/components/AvailabilityPanel.tsx:34`, `:309-313`; `NotificationBell.tsx:87` |
| **H-2** | MEDIA | **El refresh de 7 días es deslizante y no hay tope absoluto.** Cada rotación reinicia `exp` (web: `TokenRefreshSerializer`; V1: `RefreshToken.for_user` nuevo). Una sesión que se renueva al menos una vez por semana vive para siempre. | SimpleJWT 5.5.1 `serializers.py:138-141`; `v1_auth_views.py:242` |
| **H-3** | BAJA (ya registrado como GOOGLE-NONCE-CACHE) | El uso único del `nonce` de Google vive en la memoria del proceso: se pierde al reiniciar (ventana de 10 min, `NONCE_MAX_AGE`) y deja de valer con más de un proceso. Hoy hay **un** proceso (`backend/Dockerfile.prod:46-48`, `--workers 1 --threads 8`). | `google_identity.py:44`, `:102-104`; `docs/AUDIT_MEMORY.md` GOOGLE-NONCE-CACHE |
| **H-4** | BAJA | El canal cookie acepta **cualquier** `AccessToken`, también uno emitido por el login V1: no hay claim de canal. Hoy es inocuo (mismo usuario, misma revocación); con caducidad por inactividad sólo en web, un token V1 copiado a la cookie **eludiría** la regla. §2.3 lo cierra. | `authentication.py:79-111`; `v1_auth_views.py:181` |
| **H-5** | BAJA | Google marca el correo local como verificado con `email_verified=true` para **cualquier** dominio. La guía de Google dice que sólo es autoritativo para `@gmail.com` o cuando viene `hd`; para otros dominios `email_verified` no basta. Afecta a `order_risk`, a `customer_profile.py:290` (sólo una cuenta con el buzón probado puede escribir un documento de identidad) y a lo que lea `email_verified_at`; **no** permite entrar en una cuenta ajena (la vinculación sigue pidiendo contraseña). | `google_views.py:93-94`; `store/order_risk.py:327`; `store/customer_profile.py:290`; [Google: verificar el ID token](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token) |
| **H-6** | BAJA | Dos renovaciones simultáneas con **el mismo** refresh pueden ganar las dos: SimpleJWT comprueba la lista negra y luego la escribe, sin bloqueo. El frontend lo evita dentro de una pestaña (*single-flight*), no entre pestañas. Leído en el código de la biblioteca; **no reproducido**. Con `AuthSession` deja de importar para la inactividad (los dos tokens llevan el mismo `sid`), pero AUTH-B lo fija con una prueba. | SimpleJWT 5.5.1 `tokens.py` `BlacklistMixin.check_blacklist` / `blacklist()`; `serializers.py:128-141` |
| **H-7** | INFO | **Facebook y Apple ya existen en el PR #122** (`feat/social-auth`, `6338bc0`, abierto desde 2026-10-14, base `abfc6bd`, 444 commits detrás de `master`). Su propia deuda: SOCIAL-CACHE (intentos en caché de proceso), SOCIAL-GOOGLE-STAFF, SOCIAL-THROTTLED-CALLBACK, SOCIAL-LINK-STRICT, sin ruta nativa. Cambia el proveedor guardado (`facebook`, `apple`) **sin** actualizar `ExternalIdentity.PROVIDER_CHOICES` ni migración. | `gh pr view 122`; `git show origin/feat/social-auth:backend/store/social_identity.py` |
| **H-8** | INFO | El checkout web **no** lleva clave de idempotencia (por contrato, `store/views.py:300-303`); la caja (POS) sí, una por cesta (`frontend/app/admin/sales/pos/page.tsx:32-36`, `:188`, `:497`, `:522`; `backend/store/pos_services.py:902-908`, `:1215`, `:1309`). Relevante para §4.6. | — |
| **H-9** | INFO | Una cookie de acceso **inválida** responde 401 incluso en vistas públicas (`AllowAny`): `CookieJWTAuthentication` lanza en vez de devolver anónimo. Hoy se tapa porque la cookie caduca a los 30 min en el navegador. Si el servidor declara muerta una sesión cuya cookie el navegador aún guarda, **la tienda entera respondería 401** hasta que caduque. §2.6 obliga a borrar las cookies en esa respuesta. | `authentication.py:84-87`, `:103-109` |

### 1.3 Qué hay de infraestructura para un estado compartido

- **PostgreSQL** es el único almacén compartido y persistente (`docker-compose.prod.yml:116`).
- **No hay Redis** ni servicio de caché en `docker-compose.prod.yml`, ni dependencia en `backend/requirements.txt`,
  ni `CACHES` en `settings.py`: la caché de Django es LocMem por proceso (THROTTLE-CACHE-01).
- Un proceso de gunicorn con 8 hilos. Cualquier decisión de esta fase tiene que seguir valiendo con N procesos y N
  servidores sin cambiar nada: por eso el estado de la sesión va a **PostgreSQL**, como la revocación
  (`adr-token-revocation.md` §3, «Lista negra en caché»: descartada por la misma razón).

---

## 2. SESSION-IDLE-01 — caducidad por inactividad, decidida por el servidor

### 2.1 Decisión

`PROPUESTA` — **una sesión web es una fila**, y cada credencial de esa sesión la nombra.

```
AuthSession (tabla nueva, migración 0154)
  sid                 UUID único — el identificador de la sesión, independiente de cualquier jti
  user                FK → User
  channel             'web' (único valor en esta fase; la app móvil no crea filas, §3)
  kind                'customer' | 'staff'           — decide el tope absoluto (§3.1)
  login_method        'password' | 'google' | 'facebook' | 'apple'
  created_at          servidor
  last_activity_at    servidor; sólo lo mueve la actividad real (§2.4)
  absolute_expires_at servidor; nunca se alarga
  ended_at            nulo mientras vive
  end_reason          'logout' | 'idle' | 'absolute' | 'password' | 'revoked' | 'deactivated'
  índices: único(sid); (user, ended_at)
```

- **El `sid` va como claim en el refresh** que crea el login. SimpleJWT copia todos los claims del refresh al access
  (`tokens.py:393-413`) y la rotación sólo cambia `jti`, `exp` e `iat` (`serializers.py:138-141`): **rotar no crea
  una sesión nueva**, que es lo que el propietario pide. Verificado leyendo SimpleJWT 5.5.1; AUTH-B lo fija con una
  prueba (si una versión futura dejara de copiar el claim, la prueba falla antes que producción).
- **Un solo punto de emisión.** Hoy se emiten sesiones web en tres sitios (`LoginView` vía
  `TokenObtainPairSerializer`, `google_views.py:96`, y PR #122). AUTH-B crea `auth_sessions.issue_web_session(user,
  method, request)` que crea la fila, pone el `sid` y escribe las cookies; todo login web pasa por ahí. Una prueba
  recorre el urlconf y falla si aparece otro `_set_auth_cookies` con un refresh sin `sid`.
- **La comprobación vive en `CookieJWTAuthentication`**, después de `is_active` y de `token_is_revoked`, y antes de
  devolver el usuario. Así la heredan `/api/*` y la superficie interna v1 por cookie
  (`V1InternalAuthentication` orquesta esa misma clase, `adr-auth-v1-internal.md` §3.1) sin tocar ninguna vista.
- **Coste:** la fila se lee por su índice único con `select_related('user__profile')` y **sustituye** a la consulta
  actual del usuario (`authentication.py:99`): cero consultas más por petición. Se comprueba que `session.user_id`
  coincide con el claim `user_id`.

**Reutilizar `token_revocation` al mínimo.** No se toca su lógica. Se añaden dos llamadas: `logout` termina la
sesión (además de revocar el `jti` y ennegrecer el refresh, como hoy) y `revoke_all_tokens` termina todas las
sesiones del usuario (además del sello). Las dos revocaciones existentes siguen siendo la autoridad sobre *esa
credencial*; `AuthSession` es la autoridad sobre *esa sesión*.

### 2.2 Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| Bajar el access a 5 min y no renovar si no hay actividad | Acortar no es caducar (`adr-token-revocation.md` §3): el frontend seguiría renovando con cada sondeo, y el servidor no sabría qué renovación fue actividad |
| Usar el `jti` del refresh como id de sesión | Cambia en cada rotación: la sesión «nacería» cada 30 min |
| Claim `last_activity` en el token | Lo escribe quien emite el token: la única forma de moverlo es renovar, y renovar no es actividad. Además vive en el cliente |
| Sesiones de Django (`django.contrib.sessions`) | Cambia el transporte de autenticación entero y el contrato con Mobile (`adr-token-revocation.md` §3) |
| Redis | No existe en el despliegue; añadir un servicio para esto es infraestructura nueva con su copia, su vigilancia y su caída. PostgreSQL ya está y ya es la autoridad de la revocación |
| Caché LocMem | Por proceso y volátil: no es compartida ni sobrevive a un reinicio (§1.3) |
| Fecha de actividad enviada por el cliente | Un reloj del cliente no es autoridad; ni siquiera hace falta un atacante: un reloj desajustado basta |

### 2.3 La regla, petición a petición (falla cerrado)

En el canal **cookie**, en este orden:

1. El token no trae `sid` → **401 `session_expired`**. Incluye los tokens V1 copiados a una cookie (H-4) y las
   sesiones web abiertas antes del despliegue de AUTH-B (§2.9).
2. No hay fila con ese `sid`, o es de otro usuario, o su `channel` no es `web` → 401 `session_expired`.
3. `ended_at` no es nulo → 401 `session_expired`.
4. `now ≥ absolute_expires_at` → se termina (`absolute`) y 401 `session_expired`.
5. `now − last_activity_at ≥ IDLE` (1800 s) → se termina (`idle`) y 401 `session_expired`.
6. Si la petición es actividad (§2.4), se registra (§2.5).

- Las comparaciones se hacen **en SQL contra `Now()`** de PostgreSQL, no con el reloj de cada proceso: con varios
  servidores, el reloj que manda es uno.
- **Cualquier error** al leer la sesión (base caída, fila corrupta) no autentica. Nunca se degrada a «válida».
- El mensaje es el mismo que el de cualquier otro fallo de autenticación, más un `code` (`session_expired`) que sólo
  dice «vuelve a entrar»: no dice si fue inactividad, tope o cierre remoto. La razón queda en la fila y en el
  registro de seguridad (`security_log`), no en la respuesta.
- El canal **Bearer** (V1) no consulta `AuthSession` en esta fase (§3).

### 2.4 Qué es actividad real, y cómo lo sabe el servidor sin creerle un reloj al cliente

| Cuenta como actividad | No cuenta |
|---|---|
| Navegar deliberadamente (cambio de ruta hecho por la persona) | Renovar el token (`/api/auth/refresh/`) — **nunca**, por construcción |
| Enviar un formulario, guardar, borrar, confirmar | Sondeos (`AvailabilityPanel` cada 45 s), campana al recuperar el foco |
| Buscar, filtrar, escanear (código, IMEI), paginar a mano | Consultas en segundo plano, precargas, revalidaciones |
| Trabajo del panel: caja, recepción, recuentos, servicio técnico | Actualización automática de tableros |
| Acciones manuales en la tienda (carrito, checkout, «Mi cuenta») | Temporizadores del frontend, incluido el del aviso |
| Pulsar «Continuar sesión» | Healthchecks, `GET /api/auth/session/` (estado) |
| Iniciar sesión (crea la fila con `last_activity_at = now`) | Peticiones del agente de impresión (no usa la cookie) |

**Mecanismo (`PROPUESTA`).** El servidor cuenta como actividad una petición autenticada por cookie que trae
`X-Session-Activity: 1`, salvo en una lista cerrada de rutas que **nunca** cuentan (`auth/refresh`, `auth/logout`,
`auth/session` de estado). Y existe `POST /api/auth/session/keepalive/` (CSRF, cupo propio de 12/min por usuario),
que es lo que llama «Continuar sesión» y el cambio de ruta.

- **Quién pone la cabecera:** sólo `fetchWithAuth`, y por defecto **así**: métodos que escriben → sí; `GET` → no,
  salvo `{ activity: true }` explícito (búsquedas, escaneos, carga de la pantalla tras navegar). Un sondeo pasa
  `{ activity: false }` y una prueba de Jest falla si un componente con `setInterval` o con escuchas de
  `focus`/`visibilitychange` llama sin él. El valor por defecto seguro es «no cuenta».
- **Lo que esto garantiza y lo que no.** La cabecera es una **clasificación**, no una frontera de seguridad: un
  guion que ya corre en la página tiene la sesión entera, y fingir actividad no le da nada que no tuviera. Lo que el
  servidor sí garantiza sin creer al cliente: (a) el instante es siempre `Now()` del servidor —no viaja ninguna
  fecha—; (b) la renovación jamás cuenta; (c) una sesión ya caducada no revive con ninguna cabecera (la escritura
  es condicional, §2.5); (d) cada escritura está acotada (§2.5). Lo que impide que *nuestro propio* código en segundo
  plano mantenga viva la sesión es la regla por defecto más su prueba, que es exactamente el defecto H-1.
- **Por qué no «toda escritura cuenta» en el servidor:** hay escrituras automáticas (marcar notificaciones al abrir
  la bandeja, medición, fusión de carrito al cargar). Que el cliente declare la intención, con un valor por defecto
  revisable en un solo archivo, es más fácil de auditar que una lista de excepciones repartida por el backend.

### 2.5 Escritura acotada, y la prueba de que nunca pasa de 30 minutos

```sql
UPDATE store_authsession SET last_activity_at = now()
 WHERE sid = :sid AND ended_at IS NULL
   AND last_activity_at <  now() - interval '30 seconds'   -- granularidad G
   AND last_activity_at >  now() - interval '30 minutes'   -- no resucita
   AND (absolute_expires_at IS NULL OR absolute_expires_at > now())
```

**Afirmación.** Sea `T` el instante de la última actividad real aceptada y `L` el valor guardado. Entonces la sesión
caduca entre `T + 30 min − G` y `T + 30 min`: **nunca después** de 30 minutos de inactividad real.

**Prueba.** (1) `L ≤ T` siempre: `L` sólo toma el `now()` de alguna petición de actividad, y `T` es la última.
(2) `T − L < G`: si la actividad de `T` llegó con `T − L ≥ G`, la sentencia escribió `L := T`; si no escribió es
porque `T − L < G`. (3) Caduca cuando `now − L ≥ 30 min`, es decir, cuando `now − T ≥ 30 min − (T − L)`, con
`0 ≤ T − L < G`. ∎ La holgura existe, pero sólo hacia el lado seguro: la sesión puede caducar hasta G antes, nunca
después.

- **G = 30 s.** Con G = 60 s la prueba del propietario «a los 29 min sigue válida» fallaría en el peor caso (podría
  caducar a los 29:00). Con 30 s, una sesión vale al menos 29:30 desde la última actividad y nunca 30:00.
- **Amplificación:** como mucho 2 `UPDATE` por minuto por sesión activa, y **cero** por lecturas sin actividad. Con
  50 personas trabajando a la vez, ~100 actualizaciones por minuto sobre una tabla pequeña e indexada.
- **Concurrencia:** dos actividades simultáneas ejecutan la misma sentencia; el bloqueo de fila hace que una
  escriba y la otra no encuentre nada que escribir. Ninguna puede mover `L` hacia atrás.
- Configurable sólo para pruebas y E2E: `AUTH_SESSION_IDLE_SECONDS` (1800), `AUTH_SESSION_ACTIVITY_GRANULARITY`
  (30), `AUTH_STAFF_SESSION_MAX_SECONDS` (28800). `deploy/preflight.py` falla si en producción difieren de esos
  valores sin una variable de reconocimiento explícita.

### 2.6 Renovación, cierre y caducidad a la vez

| Carrera | Regla |
|---|---|
| **Refresh contra caducidad** | `RefreshView` comprueba la sesión (§2.3, pasos 1-5) **antes** de rotar, igual que hoy comprueba `refresh_is_revoked` (`auth_views.py:294`), y **nunca** toca `last_activity_at`. Una sesión inactiva 30 min no se renueva |
| **Refresh contra logout** | `logout` marca `ended_at` con `UPDATE … WHERE ended_at IS NULL`. Si la rotación gana por un instante, el token nuevo lleva el mismo `sid` y muere en la siguiente petición: el logout por `sid` es más fuerte que el de hoy por `jti` |
| **Actividad contra caducidad** | La escritura condicional (§2.5) no mueve una sesión ya caducada; la comprobación siguiente la termina |
| **Dos refresh con el mismo token (H-6)** | Ambos tokens nuevos nombran el mismo `sid`: la inactividad y el cierre los alcanzan a los dos. AUTH-B añade una prueba que documenta el comportamiento |
| **Cambio o restablecimiento de contraseña, «cerrar todo»** | `revoke_all_tokens` termina todas las filas del usuario (`password`/`revoked`), además del sello y de la lista negra |
| **Cuenta desactivada** | `is_active` ya se comprueba antes (`authentication.py:103`); la fila se termina (`deactivated`) en la próxima petición |

**Respuesta a una sesión muerta (H-9).** Toda respuesta 401 `session_expired` **borra las dos cookies** (un
manejador de excepciones de DRF limitado a ese código, más `RefreshView` y `LogoutView`). Sin esto, la tienda
pública respondería 401 a cada visita durante lo que le quedara a la cookie. Con las cookies borradas, el navegador
vuelve a ser un invitado: la tienda, el carrito y el checkout de invitado siguen funcionando.

**Cookie de refresh:** su `max_age` pasa a ser `min(7 días, lo que le queda al tope absoluto)`.

### 2.7 Lo que el frontend necesita saber (y cómo, sin reloj del cliente)

- `GET /api/auth/session/` (no cuenta como actividad, cupo propio): `{ idle_remaining_s, absolute_remaining_s,
  warn_before_s: 300 }`, en **segundos restantes**, no en fechas. El navegador calcula su plazo como «instante de
  recepción + restante» con su reloj monotónico: un reloj del cliente adelantado o atrasado no mueve nada.
- Las respuestas autenticadas por cookie llevan `X-Session-Idle-Remaining` con el mismo significado, para no tener
  que preguntar. El proxy de Next ya reenvía cabeceras en los dos sentidos (`frontend/app/api/[...path]/route.ts:263-308`).
- Es información para la interfaz, no autoridad: si el cliente se equivoca, la siguiente petición la corrige el servidor.

### 2.8 Migración

| Número | Contenido | PR |
|---|---|---|
| **0154** | `CreateModel AuthSession` + índices | AUTH-B |
| 0155 | `AuthFlowAttempt` (intentos de inicio con proveedor: `state`, `nonce`, vínculo de navegador, uso único), sustituye a la caché de proceso de Google (H-3) y de PR #122 (SOCIAL-CACHE) | AUTH-D |
| 0156 | `ExternalIdentity`: `PROVIDER_CHOICES` + `facebook`, `apple` (sólo estado); `email_is_private_relay` (bool, por defecto falso); `revoked_at` (nulo) | SOCIAL-A (o el primero que añada un proveedor) |
| 0157 | `AuthSession.reauthenticated_at` (nulo) para vincular/desvincular y, después, AUTH-STEPUP | SOCIAL-C |

- **Los números son la intención, no una reserva.** `0153` es de #179 (fiscal). Cada PR toma el siguiente libre
  **sobre el `master` del momento de fusionar** y se renumera si otro llegó antes; su dependencia es la hoja real.
- **Nunca en el mismo PR que fiscal o etiquetas**, y un PR de autenticación con migración no se apila sobre uno
  fiscal ni al revés.
- Todas son **aditivas**. Despliegue en dos pasos como R1: `migrate store 0154` con el código anterior sirviendo (lo
  ignora), luego imágenes nuevas. Vuelta atrás: imágenes anteriores; la tabla puede quedarse (el código anterior no
  la lee). `makemigrations --check --dry-run` limpio tras cada una.
- Purga: las filas terminadas hace más de 30 días se borran de forma oportunista al iniciar sesión y con
  `manage.py purge_auth_sessions`. Programarla en el servidor es **despliegue**: `BLOCKED` hasta el GO del propietario.

### 2.9 Riesgos

| Riesgo | Mitigación |
|---|---|
| **Al desplegar AUTH-B todas las sesiones web abiertas caen** (sus tokens no traen `sid`) | Decisión deliberada: falla cerrado. Un inicio de sesión más por persona, una vez. Se avisa al propietario antes (pregunta P4). La alternativa —aceptar tokens sin `sid` durante una gracia— deja H-1 abierto justo cuando se dice cerrado |
| Una pantalla olvida `{ activity: true }` en una búsqueda y la sesión caduca mientras alguien trabaja «sólo leyendo» | Falla hacia el lado seguro. El aviso de 25 min con «Continuar sesión» lo cubre; AUTH-C revisa pantalla por pantalla la caja, la recepción, los recuentos y el servicio técnico |
| La consulta por petición encarece todo | La fila sustituye a la consulta del usuario (§2.1): mismo número de consultas |
| La base cae | Hoy ya no hay autenticación sin base (usuario, revocación); no empeora |
| Deriva de reloj entre servidores | Comparaciones en SQL contra `Now()` (§2.3) |

---

### 2.10 Cambios tras la revisión independiente de este ADR (#180)

Revisión con GO condicionado. Cada punto pasa a ser decisión (`PROPUESTA` → se construye en la fase indicada) y
**sustituye** a lo que diga en contra el resto de este documento.

| ID | Cambio | Fase |
|---|---|---|
| **P1-1** | **Lavado web → móvil.** `V1RefreshView` aceptaba el refresh de la cookie web y emitía un token V1 sin `sid`, y `V1BearerAuthentication` aceptaba un access web: una sesión con caducidad se convertía en una sin ella. Decisión: **`sid` es la frontera entre canales**. El canal cookie rechaza tokens sin `sid` (H-4) y el canal Bearer (V1, incluida la superficie interna por Bearer) rechaza refresh y access **con** `sid`. Pruebas en los dos sentidos | AUTH-B |
| **P1-2** | **Quién es personal.** El predicado del tope de 8 h es la **unión** de todos los caminos al panel: `is_staff`/`is_superuser`/master, `Membership` activa de empresa activa, el puente heredado (`tenancy.legacy_catalog_company`, `uses_legacy_bridge`) y cualquier rol heredado de personal en `UserProfile.role`. Más amplio es el lado seguro: sólo acorta | AUTH-B |
| **P2-1** | **Cookie de sesión muerta.** Lecturas: invitado, cookies borradas (la tienda pública y `GET /api/auth/csrf/` responden 200). Escrituras: **401 `session_expired`**, salvo en las vistas que son una entrada y lo declaran (`dead_session_is_guest`): `LoginView`, `RegisterView`, el carrito (es del navegador por `session_key`), validar cupón, cotizar y la propia renovación. **El checkout sigue en 401**: nada enviado con la cookie de una cuenta se hace como un desconocido. Un access que sólo caducó, de una sesión viva, sigue en 401 para que el navegador renueve | AUTH-B |
| **P2-2** | **El logout termina el `sid` siempre.** Se lee de la cookie que llegue (refresh o access) con la **firma verificada** y la caducidad y la lista negra **ignoradas**: un access caducado o un refresh ya rotado siguen nombrando su sesión. Un `sid` sin firma válida no termina nada | AUTH-B |
| **P2-3** | **La señal de actividad.** (a) Sólo vale si la petición pasa CSRF, **también en GET** (una página ajena puede provocar un GET con cookies, no con el token); (b) se registra **después** de la respuesta y sólo si fue 2xx/3xx (un middleware, no la autenticación); (e) invariante con prueba: `X-Session-Activity` **nunca** está en `CORS_ALLOW_HEADERS`. Los puntos (c) y (d) de la revisión, del frontend, van a AUTH-C | AUTH-B · AUTH-C |
| **P2-4** | **Endurecer al momento.** Al crear o activar una `Membership` (aceptar una invitación lo hace), al pasar a un rol heredado de personal o al marcar `is_staff`/`is_superuser`, todas las sesiones abiertas de esa cuenta pasan a personal con su tope de 8 h desde el inicio, sin esperar a la renovación (que además lo vuelve a comprobar antes de juzgar) | AUTH-B |
| **P2-5** | Pruebas añadidas: tope de 8 h, 7 días sin deslizar, membresía en mitad de la sesión, puente heredado, web → V1, sin `sid` = 401 y cookies borradas, login sobre cookie muerta, logout durante una rotación, cabecera ignorada en renovación/logout/estado, edición de inventario con sesión muerta = 401 sin escribir, fallo de la base = rechazo. `H411SafeMethodsAreReadOnlyTest` exime **explícitamente** la tabla `AuthSession` (terminar una sesión caducada en un GET es una escritura por diseño) | AUTH-B |
| **P2-6** | **Caja:** la reconciliación tras volver a entrar es **sólo de consulta**: `GET` por clave de idempotencia, limitado a la empresa; quien cobra vuelve a introducir o confirma la cesta, nunca se reenvía sola. **Borradores:** las notas de una orden de servicio son datos del cliente y no se guardan en el navegador (§4.4) | AUTH-C |
| **P2-7** | **Pre-secuestro con Google.** Si un correo que Google no puede avalar (H-5) se prueba después por el buzón de esta plataforma (enlace o código), la identidad de Google ya vinculada se desvincula o se marca para revisión y se avisa al titular | AUTH-D |
| P3 | El intervalo de la SQL sale de `AUTH_SESSION_IDLE_SECONDS` (nada fijo); `created_at` y `last_activity_at` con `db_default=Now()`; `absolute_expires_at` NOT NULL; el `sid` se pone en el refresh **antes** de derivar el access; bfcache y ocultar el contenido al llegar a cero (AUTH-C); `reauthenticated_at` sólo con una re-autenticación explícita (SOCIAL-C); la falta de CSRF en la renovación con un access caducado queda dicha en `RefreshView` (la protege `SameSite`); detección de reutilización de refresh por `sid` como mejora opcional | AUTH-B · AUTH-C · SOCIAL-C |

## 3. Tope absoluto de 8 h para personal; la app móvil

### 3.1 8 horas para sesiones internas

`PROPUESTA` — **sí.** Una sesión es `staff` si su usuario es `is_staff`/`is_superuser`, master de plataforma o
tiene alguna `Membership` activa en una empresa activa (la misma definición que abre el panel,
`/api/me/memberships/`). Para `staff`, `absolute_expires_at = created_at + 8 h`. La inactividad de 30 min sigue
valiendo dentro de esas 8 h.

- **Se reevalúa en cada refresh, y sólo endurece.** A quien le dan una membresía con la sesión abierta, su sesión
  pasa a `staff` en la siguiente renovación (≤ 30 min) y su tope baja a `created_at + 8 h`. Quitar la membresía no
  alarga nada.
- **Por qué 8 h:** un turno. Cubre jornada completa con inactividad intermedia controlada; obliga a una
  autenticación nueva al día, que es el punto natural para MFA (§8). Alternativas: 12 h (turnos dobles; pregunta P3),
  sin tope (no limita una cookie robada en una caja compartida).
- **Clientes:** `PROPUESTA` tope absoluto de 7 días **desde el inicio de sesión**, sin deslizar (cierra H-2 para la
  tienda). Pregunta P2.
- **El aviso del tope** sale 5 min antes; ahí «Continuar sesión» no existe: se explica que hay que volver a entrar.

### 3.2 La app móvil (V1 Bearer): no cambia implícitamente

`DECISIÓN` — en AUTH-B **ningún token V1 cambia**: el login V1 no crea `AuthSession`, el canal Bearer no la
consulta y `V1RefreshView` sigue como está. Lo que eso significa para el contrato, dicho para que Mobile lo sepa:

| Hoy en V1 | Consecuencia |
|---|---|
| Access 30 min, refresh 7 días **deslizante** (`v1_auth_views.py:242` emite uno nuevo) | Una sesión móvil sin uso en 7 días caduca; con uso, no caduca nunca |
| Sin inactividad ni tope absoluto | Un trabajador con la app tiene una sesión que **no** cumple 30 min / 8 h |
| Logout por `jti` y sello global de contraseña | Siguen igual; un cambio de contraseña en la web sigue cerrando la app |

**Para aplicar la regla a la app (`PROPUESTA`, fase aparte MOBILE-SESSION-01, coordinada con Mobile):**
el login V1 crearía una fila `channel='native'` y pondría `sid`; `V1RefreshView` tendría que **copiar** el `sid`
(hoy emite un `RefreshToken.for_user` limpio y lo perdería); la app tendría que definir qué es actividad en primer
plano y mostrar su propio aviso; y la semántica de «sesión caducada» en Bearer tendría que distinguirse de «token
caducado» para que la app no reintente en bucle. Es un cambio de contrato y se versiona como tal. Pregunta P5:
¿se acepta el riesgo hasta entonces, o el personal no usa la app para operaciones internas mientras tanto?

---

## 4. Frontend (AUTH-C)

`PROPUESTA` en todos los puntos.

### 4.1 Aviso a los 25 minutos

- Un único reloj por navegador, alimentado por `idle_remaining_s` (§2.7). Las pestañas se sincronizan por
  `BroadcastChannel('bd-session')` (`activity`, `logout`, `expired`): actividad en una pestaña aplaza el aviso en
  todas; cerrar en una cierra en todas (comparten cookies y `sid`).
- A `remaining ≤ 300 s`, antes de mostrar nada, se pregunta al servidor (`GET /api/auth/session/`, que no cuenta):
  la actividad pudo ocurrir en otra pestaña.
- Diálogo modal accesible (`role="alertdialog"`, foco atrapado, anuncio con `aria-live` cada minuto, no cada
  segundo; movimiento reducido respetado) con cuenta atrás y dos botones:
  - **«Continuar sesión»** → `POST /api/auth/session/keepalive/`. Es lo único del diálogo que cuenta como actividad,
    y sólo cuando la persona lo pulsa: abrir o mostrar el diálogo no cuenta.
  - **«Cerrar sesión»** → logout normal.
- En la tienda, el aviso sólo aparece en checkout y «Mi cuenta»; en el resto, la sesión caduca en silencio y la
  persona sigue como invitada (pregunta P13).

### 4.2 Caducidad

- `fetchWithAuth`: un 401 con `code === "session_expired"` **no** dispara refresh ni reintento. El refresh que
  responde `session_expired` tampoco reintenta. Sondeos que reciben esa respuesta **se detienen**.
- Se emite `expired` por el canal, se sueltan las cachés de memoria (`internalAccess`, contextos) y se navega con
  `window.location.replace` (no `router.push`): una navegación completa descarta el estado de React con los datos
  de la pantalla.
- Rutas internas → `/auth?reason=idle&next=<ruta local>` con el texto «Tu sesión se cerró tras 30 minutos sin
  actividad. Vuelve a entrar para continuar.» (o el del tope de 8 h). `next` pasa por el validador existente
  (`safeInternalNextPath`).
- **Sin resurrección:** nada vuelve a pedir refresh después de `expired` hasta un login nuevo.

### 4.3 Nada interno en caché tras caducar

- `next.config.ts`: `Cache-Control: no-store` para `/admin/:path*` (además de las cabeceras de FE-AUTH-04). Eso
  saca esas páginas de la bfcache en la práctica totalidad de navegadores.
- Escucha de `pageshow` con `event.persisted` en el `AdminShell`: si una página vuelve de la bfcache igualmente, se
  oculta su contenido y se pregunta la sesión antes de mostrar nada.
- Las respuestas JSON autenticadas del backend llevan `Cache-Control: private, no-store` (un middleware que sólo
  actúa cuando la petición se autenticó por cookie).

### 4.4 Borradores

| Se puede guardar (en `sessionStorage`, con clave usuario + empresa + formulario) | Nunca, en ningún almacenamiento del navegador |
|---|---|
| Texto de una ficha de producto, filtros, la cesta de la caja sin datos del cliente | Tokens, cookies, claves, secretos de la consola de integraciones (ya cumple, `IntegrationEditor.tsx:8`) |
| | Notas de una orden de servicio (son datos del cliente, P2-6) |
| La marca de una venta de caja en curso (§4.6) | Datos fiscales sensibles: clave SOL, certificado, su contraseña |
| | Datos privados del cliente: documento, teléfono, dirección, correo |

- Se borran al cerrar sesión. Tras una caducidad se conservan, y sólo se ofrecen si quien vuelve a entrar es el
  mismo usuario en la misma empresa; si no, se descartan sin mostrarse.

### 4.5 Checkout de la tienda a mitad de operación

- El cobro no depende de la sesión: el pedido pendiente se crea en el POST de checkout y el pago lo confirma la
  notificación firmada de la pasarela, que no lleva cookie. Una sesión que caduca con el formulario de pago abierto
  **no** cobra dos veces ni deja de confirmar el cobro.
- Si caduca **antes** de enviar el checkout, la respuesta es 401 `session_expired` y no se escribe nada (la
  autenticación corre antes que la vista). El frontend **no reenvía** el POST: muestra «Tu sesión se cerró. Inicia
  sesión para que el pedido quede en tu cuenta, o continúa como invitado» y la persona decide. El carrito vive en
  el servidor por `session_key` y no se pierde.
- No se añade clave de idempotencia al checkout web en esta fase (es un contrato aparte, H-8). Un reenvío manual
  crea, como hoy, otro pedido **pendiente**, sin cobro; los apartados de equipos ya tienen su barrido.

### 4.6 Caja (POS) a mitad de operación

- La clave de idempotencia por cesta (H-8) se conserva: si el 401 llega antes de la vista, no se vendió nada y la
  clave sigue en `keyRef`.
- **El caso peligroso** es otro: la venta se registró, la respuesta se perdió y la sesión caduca antes de reintentar.
  Una navegación completa al login pierde `keyRef`, y rehacer la cesta con una clave nueva **duplicaría la venta**.
- Decisión: mientras una venta está **enviada y sin respuesta definitiva**, la caja guarda en `sessionStorage`
  sólo `{ idempotency_key, huella de la cesta, enviada_en }` (un aleatorio y un resumen: nada privado). Al volver a
  entrar, si existe la marca, la caja **no permite cobrar** hasta reconciliar: reenvía la misma clave con la cesta
  reconstruida (el servidor contesta con la venta ya hecha, `pos_services.py:1215`, o con conflicto si la cesta
  difiere) o muestra la venta encontrada. La marca se retira sólo con una respuesta definitiva.
- Lo mismo vale para las acciones del servicio técnico que ya llevan clave (`service/orders/[id]/page.tsx`).
- Acción fiscal sensible: la caducidad responde 401 antes de cualquier llamada a SUNAT; los guardas de doble envío
  existentes (`test_fiscal_double_submit`) no cambian.

---

## 5. Google (AUTH-D, dentro de SOCIAL-AUTH-01)

`DECISIÓN` — **se conserva** el flujo actual (ID token de Google Identity Services verificado en el servidor,
DEC-GOOGLE-01). Cambios mínimos (`PROPUESTA`):

1. Emitir la sesión por `issue_web_session(..., method='google')` (§2.1).
2. Uso único del `nonce` en PostgreSQL (`AuthFlowAttempt` o una tabla de `nonce` consumidos), no en la caché de
   proceso (H-3).
3. Regla de autoridad del correo de Google (H-5): marcar el correo local como verificado sólo si es `@gmail.com` o
   el token trae `hd`; en otro caso, la cuenta se crea igual pero el correo queda sin verificar.
4. Decisión del propietario sobre personal con Google (SOCIAL-GOOGLE-STAFF, pregunta P6).

**Lista de aceptación** (para el propietario, con su ID de cliente real):

| # | Comprobación | Estado esperado |
|---|---|---|
| G1 | Sin `client_id`: `/api/auth/google/config/` → `{enabled:false}`, el botón no aparece, `/api/auth/google/` → 404 | automatizado |
| G2 | Con `client_id`: botón visible; **«el botón se ve» no es «Google funciona»** | — |
| G3 | Entrar con una cuenta Google nueva crea una cuenta de cliente, sin `Membership`, sin rol | manual con credencial real |
| G4 | Correo ya registrado → 409 y contraseña; con la contraseña correcta, se vincula y entra | manual |
| G5 | Volver a entrar abre la misma cuenta (por `sub`) aunque cambie el correo en Google | manual |
| G6 | Token reutilizado o de otro navegador → rechazado | automatizado |
| G7 | La sesión creada caduca a los 30 min sin actividad como cualquier otra | automatizado (AUTH-E) |
| G8 | Origen de la tienda en «Orígenes de JavaScript autorizados» del cliente; pantalla de consentimiento con nombre, correo de soporte, dominios autorizados, inicio y política de privacidad | consola de Google, propietario |

**Activación en producción: `BLOCKED: CREDENTIALS`** (GOOGLE-CLIENT-ID): ID de cliente del propietario y los orígenes
autorizados de **cada** dominio de tienda (DEC-INT-03: «un ID de cliente por dominio»). Fuentes:
[Obtener el ID de cliente](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid),
[Verificar el ID token](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token).

---

## 6. Facebook (SOCIAL-A) y Apple (SOCIAL-B)

### 6.1 Base: el PR #122, no una reescritura

`PROPUESTA` — rebasar `feat/social-auth` sobre `master` y partirlo: SOCIAL-A (núcleo común + Facebook), SOCIAL-B
(Apple), SOCIAL-C (vinculación y «Mi cuenta»). Antes de fusionar cada parte se cierran sus deudas:
intentos en PostgreSQL (`AuthFlowAttempt`, sustituye a `cache` en `social_identity.py`), emisión por
`issue_web_session`, `PROVIDER_CHOICES` con migración, y la ruta que lee la cookie para vincular
(`social_views.py::_signed_in_user`) pasa por la misma comprobación de sesión de §2.3. Lo que #122 ya resolvió y se
conserva: intento de un solo uso ligado a una cookie HttpOnly, PKCE S256 en Facebook, entrega en dos pasos del
`form_post` de Apple, la toma de cuenta corregida en su revisión, «nunca vincular por correo».

### 6.2 Facebook Login para web (flujo manual con redirección)

- **Protocolo:** diálogo `https://www.facebook.com/v26.0/dialog/oauth` con `client_id`, `redirect_uri` registrado,
  `state` único por intento y `response_type=code`; canje **servidor a servidor** en
  `GET https://graph.facebook.com/v26.0/oauth/access_token` con el **mismo** `redirect_uri` y el secreto de la app.
  ([Flujo manual](https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow))
- **Validación en el servidor:** `GET /debug_token?input_token=…&access_token=<token de app>`; exigir
  `data.is_valid`, `data.app_id == nuestro app_id` y tomar `data.user_id`; `/me?fields=id,name,email` con
  `appsecret_proof` (HMAC-SHA256 del token con el secreto). El token de app y el secreto **nunca** llegan al
  navegador. ([Seguridad del login](https://developers.facebook.com/docs/facebook-login/security))
- **Identificador estable:** el `id` es un **ID de usuario de ámbito de app** (ASID), único para esta app: cambiar
  de app de Meta cambia los ids. Es el `subject` de `ExternalIdentity`.
  ([Referencia User](https://developers.facebook.com/docs/graph-api/reference/user/),
  [Mapear usuarios entre apps](https://developers.facebook.com/documentation/facebook-login/guides/map-users))
- **Correo:** puede faltar y Facebook no dice si está verificado: **nunca** se usa para vincular ni pasa a ser el
  correo de la cuenta (#122 ya lo hace así).
- **Permisos:** sólo `public_profile` y `email`. Con acceso estándar sólo funcionan para personas con rol en la
  app; para el público hace falta **acceso avanzado**, la app en modo **Live** y **verificación del negocio**.
  ([Niveles de acceso](https://developers.facebook.com/docs/graph-api/overview/access-levels),
  [Permisos](https://developers.facebook.com/docs/permissions))
- **URI de redirección:** modo estricto obligatorio, coincidencia exacta, HTTPS; una por dominio de tienda.
- **Borrado de datos:** URL de *callback* de eliminación (POST con `signed_request` HMAC-SHA256 con el secreto;
  responde `{url, confirmation_code}`) o URL de instrucciones, configurada en la app.
  ([Callback de eliminación](https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback))
  Se implementa en SOCIAL-A, apagado mientras el proveedor lo esté.
- **Separado de Meta Pixel y WhatsApp.** Proveedor propio en la consola (`facebook`; `meta` ya es el Pixel en
  `integrations/providers/measurement.py:177`), campos propios (`app_id` público, `app_secret` sellado). No comparte
  credenciales, ni filas, ni interruptor con Pixel, Conversions API o WhatsApp Cloud API. Recomendación: una app de
  Meta propia para el login (pregunta P8).

### 6.3 Sign in with Apple para web

- **Configuración:** Team ID, un **Services ID** (es el `client_id` web), dominios verificados y URL de retorno
  absolutas en **HTTPS** —sin IP, sin `localhost`, sin fragmento—, y una clave privada de Sign in with Apple con su
  **Key ID**. ([Configurar el entorno](https://developer.apple.com/documentation/signinwithapple/configuring-your-environment-for-sign-in-with-apple))
- **Autorización:** `GET https://appleid.apple.com/auth/authorize` con `response_type=code id_token`,
  `response_mode=form_post` (obligatorio si se piden `name`/`email`), `scope=name email`, `state` y `nonce`. Apple
  responde con un **POST entre sitios**: el navegador **no** envía cookies `SameSite=Lax` en él, de ahí la entrega
  en dos pasos de #122 (el POST deja el resultado ligado al intento y redirige con 303 a un GET del propio sitio, que
  sí lleva la cookie del intento). ([Solicitar una autorización](https://developer.apple.com/documentation/signinwithapplerestapi/request-an-authorization-to-the-sign-in-with-apple-server.))
- **`client_secret`:** JWT **ES256**, cabecera `kid` = Key ID; `iss` = Team ID, `iat`, `exp` ≤ 15 777 000 s (seis
  meses), `aud = https://appleid.apple.com`, `sub` = Services ID. #122 lo firma por petición con 5 min de vida, que
  es más prudente que uno de seis meses guardado.
  ([Crear el client secret](https://developer.apple.com/documentation/accountorganizationaldatasharing/creating-a-client-secret))
- **Validación del `id_token`:** firma con la clave de `https://appleid.apple.com/auth/keys` cuyo `kid` coincide (el
  conjunto cambia: no suponer una sola), `iss = https://appleid.apple.com`, `aud` = Services ID, `exp`, y el `nonce`
  del intento. El `code` (válido 5 min) se canjea en `https://appleid.apple.com/auth/token`.
  ([Verificar a un usuario](https://developer.apple.com/documentation/signinwithapple/verifying-a-user),
  [Clave pública](https://developer.apple.com/documentation/signinwithapplerestapi/fetch-apple's-public-key-for-verifying-token-signature))
- **Identificador estable:** `sub`, único y estático **para el equipo de desarrollador** (cambiar de Team ID exige el
  proceso de transferencia de Apple). ([Recibir el identity token](https://developer.apple.com/documentation/signinwithapple/receiving-a-users-identity-token))
- **Correo y nombre:** el objeto `user` (nombre y correo) llega **sólo la primera vez**, en el `form_post`, y el
  nombre **no** está en el token: se sanea y se guarda entonces. El correo viene en el token cada vez y puede ser
  una dirección de reenvío privado (`…@privaterelay.appleid.com`, «Ocultar mi correo»).
- **Reenvío privado:** para que los correos de la tienda lleguen a esas direcciones hay que registrar los dominios o
  direcciones de envío como *email sources* en Apple, autenticados con SPF y/o DKIM. Es el dominio del remitente SMTP
  de la instalación (`DEFAULT_FROM_EMAIL`).
- **Revocación y notificaciones:** `POST https://appleid.apple.com/auth/revoke` invalida los tokens del usuario
  ([Revocar tokens](https://developer.apple.com/documentation/signinwithapplerestapi/revoke-tokens)); las
  notificaciones servidor a servidor avisan de `consent-revoked`, `account-deleted`, `email-enabled/disabled`
  ([Procesar cambios](https://developer.apple.com/documentation/signinwithapple/processing-changes-for-sign-in-with-apple-accounts)).
  La guía 5.1.1(v) de la App Store (borrado de cuenta dentro de la app y revocación con la API REST) aplica a apps
  publicadas; **la web no está sujeta**, pero la app móvil sí lo estaría si usara Apple
  ([Borrado de cuenta](https://developer.apple.com/support/offering-account-deletion-in-your-app/)).
  `PROPUESTA`: SOCIAL-B no guarda tokens de Apple (como Google, `models.py:10061-10063`); el endpoint de
  notificaciones marca `ExternalIdentity.revoked_at` con `consent-revoked`/`account-deleted` y esa identidad deja de
  abrir la cuenta. Guardar un refresh de Apple sellado para poder revocar sólo se hará cuando exista el borrado de
  cuenta (pregunta P12).

### 6.4 Común a los dos

| Tema | Decisión |
|---|---|
| `state` | Aleatorio por intento; en `AuthFlowAttempt` por su hash, con vínculo a una cookie HttpOnly del navegador que lo empezó; un solo uso; 10 min |
| `nonce` | Apple: en el token, comparado con el del intento. Facebook: no hay ID token en el flujo manual; el `state` + vínculo + PKCE cubren el reenvío |
| Reenvío / replay | El intento se gasta al primer retorno, antes de mandar ningún código a ninguna parte (#122) |
| Secretos | Consola de integraciones (INTEGRATIONS-CONSOLE-01): `Field(secret=True)` sellado con Fernet bajo `APP_CONFIG_ENCRYPTION_KEY`, ligado a su fila (`integrations/secret_store.py`); la clave `.p8` de Apple es un campo `kind='file'` secreto, como el certificado de SUNAT. Sólo MASTER. **Cabe sin tocar la consola** |
| Alcance | Instalación, como Google (DEC-INT-03), con una URL de retorno por dominio de tienda |
| Apagado por defecto | Sin configuración activa, los endpoints responden 404 y `GET /api/auth/social/config/` dice `enabled:false`; el botón no se pinta |
| Pruebas | Adaptadores falsos (los de #122): ninguna prueba llama a Meta ni a Apple; la red se prohíbe en la suite |
| Condiciones `BLOCKED` | `BLOCKED: CREDENTIALS` (app de Meta, Team ID, Services ID, Key ID, `.p8`), `BLOCKED: BUSINESS-VERIFICATION` (Meta, acceso avanzado), `BLOCKED: DOMAIN` (dominios verificados en Apple, URI exactas en Meta, HTTPS real), `BLOCKED: OWNER-GO` (activar en producción) |

---

## 7. Cuentas (SOCIAL-C)

`PROPUESTA` en todos los puntos; la mayoría ya está en #122 o en `master`.

1. **Una cuenta, varios proveedores, uno de cada.** Se conservan `unique(provider, subject)` (una identidad abre una
   sola cuenta) y `unique(user, provider)`. Las filas existentes (`google`) no cambian; la migración 0156 sólo añade
   opciones y dos campos con valor por defecto.
2. **Nunca vincular por correo**, incluidos los de reenvío de Apple y los «parecidos» (mayúsculas, puntos de Gmail,
   `+etiqueta`): un correo que coincide sólo produce `link_required`, y vincular exige estar dentro de esa cuenta.
3. **Re-autenticación para vincular y desvincular:** contraseña actual, o haber iniciado sesión hace menos de 5 min
   (`AuthSession.reauthenticated_at`, migración 0157). Es la misma pieza que necesitará AUTH-STEPUP.
4. **No se puede quitar el último método de acceso** (contraseña utilizable o una identidad vinculada no revocada).
5. **Revocación:** desvincular borra la fila; cambiar la contraseña sigue cerrando todo (§2.6); `revoked_at` de
   Apple deja de abrir.
6. **Sin clientes duplicados:** el acceso social crea una **cuenta**, no un registro de cliente (DEC-CUSTOMER-01);
   vincular a la cuenta existente conserva pedidos e historial. No se fusionan cuentas automáticamente.
7. **Privilegios:** el acceso social **sólo** crea cuentas de cliente. Nunca crea ni acepta `Membership`, roles,
   `is_staff` ni invitaciones fuera del contrato existente (aceptar una invitación sigue exigiendo sesión y el mismo
   correo que la invitación, `staff_services.py:345`). #122 además impide entrar por Facebook o Apple a cuentas con
   personal, superusuario o cualquier `Membership` (`social_identity.py::is_privileged`): se conserva.
8. **El acceso del personal no cambia:** contraseña (y Google mientras el propietario no diga otra cosa, P6). Una
   cuenta global por sí sola nunca autoriza dentro de una empresa: la autoridad sigue siendo
   `tenancy.has_capability` sobre una `Membership` activa.

---

## 8. Interfaz de acceso

`PROPUESTA`:
- Pantalla de acceso: correo + contraseña, y Google, Facebook y Apple **sólo** si están configurados y activos
  (una sola consulta, `GET /api/auth/social/config/`, que ya incluye Google).
- Botones según las guías de marca de cada proveedor; la marca de la tienda sale del tenant
  (`CompanySettings`/CMS, como hoy en el panel izquierdo de `app/auth/page.tsx`); la marca global del SaaS no cambia.
- Se conservan: registro, verificación por enlace y por código, recuperación de contraseña, invitaciones
  (`?next=/invitacion?token=…`), acceso del personal y checkout de invitado.
- `reason=idle|absolute` muestra su explicación encima del formulario.

## 9. Propuestas para después (sin implementar)

| Fase | Qué | Impacto |
|---|---|---|
| **AUTH-MFA-01** | TOTP y *passkeys* (WebAuthn) para administradores y master; obligatorio para quien tenga capacidades de seguridad, fiscales o de integraciones | Tabla de factores; un paso más en `issue_web_session` cuando `kind='staff'`; códigos de recuperación; la consola de integraciones y la emisión fiscal pasarían a exigirlo. Necesita decisión del propietario sobre quién queda obligado |
| **AUTH-DEVICES-01** | Lista de sesiones activas («Mi cuenta» y panel de seguridad) y «cerrar las demás» | Se apoya entera en `AuthSession`; añade `user_agent` resumido e IP truncada (datos personales: retención corta). «Cerrar las demás» = terminar filas ≠ la actual |
| **AUTH-STEPUP-01** | Re-autenticación reciente para cambios de seguridad, roles y membresías, emisión fiscal sensible (bajas, notas), configuración tributaria e integraciones | `AuthSession.reauthenticated_at` (0157) + un permiso `RecentAuthRequired(max_age)` declarado por vista; respuesta 403 `reauth_required` que el frontend resuelve con un diálogo; sin tocar el contrato V1 hasta MOBILE-SESSION-01 |

---

## 10. Plan de pruebas

Reglas: PostgreSQL real; RED antes que GREEN. Las pruebas **envejecen la fila** (`last_activity_at`,
`created_at`, `absolute_expires_at`), no el reloj, porque la comparación es `Now()` de la base (§2.3). Las clases
`TransactionTestCase` (carreras con hilos) van a la lista `flushing` de `backend/scripts/ci_shard_weights.json`,
**ordenada**; nunca `reset_sequences`. E2E con `AUTH_SESSION_IDLE_SECONDS` reducido sólo en el backend de prueba.

| # | Caso del propietario | Fase | Archivo · clase |
|---|---|---|---|
| 1 | 29 min sin actividad: válida | AUTH-B | `store/test_auth_session.py · IdleWindowTest` |
| 2 | 30 min: caducada, cookies borradas | AUTH-B | `IdleWindowTest` |
| 3 | Refresh tras el límite: rechazado, sin rotar | AUTH-B | `store/test_auth_session.py · RefreshTest` |
| 4 | 1 h de sondeo no mantiene la sesión | AUTH-B (servidor) · AUTH-C (cliente) | `ActivitySignalTest` · `frontend/__tests__/session-activity.test.ts` |
| 5 | Usuario activo renueva | AUTH-B | `ActivitySignalTest` (granularidad 30 s, sin escritura en lecturas) |
| 6 | Dos pestañas | AUTH-C | `frontend/__tests__/session-idle.test.tsx` (BroadcastChannel) · `e2e/session-idle.spec.ts` |
| 7 | Dos dispositivos | AUTH-B | `MultipleSessionsTest` (dos `sid`, independientes) |
| 8 | Logout | AUTH-B | `EndOfSessionTest` (termina el `sid`; tokens rotados también mueren) |
| 9 | Cambio de contraseña | AUTH-B | `EndOfSessionTest` + `H412bTokenRevocationTest` existente sin cambios |
| 10 | Revocar todo | AUTH-B | `EndOfSessionTest` |
| 11 | Caducidad en carrito/checkout | AUTH-B (401 sin escritura) · AUTH-C (no reenvía) | `CheckoutExpiryTest` · `frontend/__tests__/checkout-session-expiry.test.tsx` |
| 12 | Durante edición de inventario | AUTH-C | `frontend/__tests__/session-drafts.test.tsx` |
| 13 | Antes de una acción fiscal sensible | AUTH-B | `FiscalExpiryTest` (401 antes de cualquier proveedor) |
| 14 | Operadores de empresas distintas | AUTH-B | `TenantIsolationTest` (el `sid` no cruza; 404 igual que hoy) |
| 15 | Web frente a V1 nativo | AUTH-B | `ChannelSeparationTest` (V1 sin cambios; token V1 en cookie → 401) |
| 16 | Google sin regresiones | AUTH-D | `store/test_google_auth.py` existente + `GoogleSessionTest` |
| 17 | Google / Facebook / Apple simulados | AUTH-D · SOCIAL-A · SOCIAL-B | `test_google_auth.py` · `store/test_social_auth.py` (de #122) |
| 18 | Cuenta existente con correo parecido | SOCIAL-C | `store/test_social_link.py · SimilarAddressTest` |
| 19 | Correo privado de Apple | SOCIAL-B | `test_social_auth.py · ApplePrivateRelayTest` |
| 20 | Identidad externa duplicada | SOCIAL-C | `test_social_link.py · DuplicateIdentityTest` |
| 21 | Callback malicioso | SOCIAL-A/B | `test_social_auth.py · CallbackTamperingTest` |
| 22 | `state`/`nonce` inválidos | AUTH-D · SOCIAL-A/B | `RefusedTokenTest` existente · `test_social_auth.py · StateNonceTest` |
| 23 | Token caducado | AUTH-D · SOCIAL-A/B | `RefusedTokenTest` · `ExpiredTokenTest` |
| 24 | Usuario inactivo | AUTH-B · SOCIAL-A/B | `EndOfSessionTest` · `InactiveUserTest` |
| 25 | Vinculación concurrente | SOCIAL-C | `store/test_social_link_race.py · ConcurrentLinkTest` → `flushing` |
| 26 | Sin roles autoasignados | SOCIAL-A/B/C | `test_social_auth.py · NoPrivilegeTest` |
| — | Carreras de sesión | AUTH-B | `store/test_auth_session_race.py · RefreshAgainstLogoutTest`, `ActivityAgainstExpiryTest` → `flushing` |
| — | El `sid` sobrevive a la rotación (SimpleJWT) | AUTH-B | `RefreshTest` |
| — | Ningún login web sin `sid` | AUTH-B | `SingleIssuerTest` (recorre urlconf) |
| — | Todo integrado, navegador real | AUTH-E | `frontend/e2e/session-idle.spec.ts`, `e2e/social-sign-in.spec.ts` (simulados) |

## 11. Fases, PR y dependencias

| Fase | Contenido | PR | Depende de | Credenciales / GO |
|---|---|---|---|---|
| **AUTH-B** | `AuthSession` (0154), `issue_web_session`, guarda en `CookieJWTAuthentication`, refresh/logout/contraseña, señal de actividad, `keepalive` y estado, borrado de cookies, 8 h, preflight | sólo `backend/**` (~70 min de CI) | — | No. Desplegar: GO del propietario |
| **AUTH-C** | Aviso, cuenta atrás, canal entre pestañas, `fetchWithAuth`, `no-store`, borradores, marca de la caja, textos | sólo `frontend/**` | AUTH-B fusionado (contrato) | No |
| **AUTH-D** | Google por `issue_web_session`, `AuthFlowAttempt` (0155), regla de autoridad del correo, lista de aceptación | backend + doc | AUTH-B | Verificación real: `BLOCKED: CREDENTIALS` (GOOGLE-CLIENT-ID) |
| **SOCIAL-A** | #122 rebasado: núcleo común + Facebook + callback de borrado; 0156 | backend + frontend mínimo | AUTH-D (tabla de intentos) | `BLOCKED: CREDENTIALS`, `BUSINESS-VERIFICATION`, `DOMAIN` |
| **SOCIAL-B** | Apple (dos pasos, `client_secret`, notificaciones) | backend + frontend mínimo | SOCIAL-A | `BLOCKED: CREDENTIALS`, `DOMAIN` (HTTPS real) |
| **SOCIAL-C** | «Mi cuenta» › métodos de acceso, re-autenticación (0157), último método | backend + frontend | SOCIAL-A (basta un proveedor nuevo) | No |
| **AUTH-E** | Pruebas de seguridad integradas, E2E, revisión independiente | tests | todas | No |

**No autorizado en ninguna de estas fases** sin orden expresa del propietario: despliegue a producción, tocar
sesiones reales del servidor, credenciales externas reales, activar la app de Meta o el Services ID de Apple, DNS,
dominios verificados y URL de retorno públicas.

## 12. Preguntas al propietario

### 12.1 Valores por defecto provisionales (hasta que el propietario conteste)

Fijados por la coordinación el 2026-10-25 para poder construir AUTH-B. Son **default provisional**: se cambian
con una línea de configuración si el propietario decide otra cosa, y no autorizan ningún despliegue.

| Pregunta | Default provisional | Por qué |
|---|---|---|
| P1 | Los 30 min de inactividad valen para **todas** las sesiones web por cookie, clientes incluidos | El encargo: «Nadie pueda dejar abierta indefinidamente una sesión inactiva» |
| P2 | Clientes: tope absoluto de **7 días desde el inicio de sesión**, sin deslizar | Cierra H-2 sin cambiar la duración que el cliente ya conoce |
| P3 | Personal, miembros, master y superusuarios: tope absoluto de **8 h** | Un turno |
| P4 | Un inicio de sesión forzado al desplegar AUTH-B es aceptable **en el código**; el despliegue en sí **no** está autorizado | Falla cerrado (§2.9) |
| P5 | La app móvil V1 no cambia; separación de canal para que un token V1 no sirva en el canal cookie | §3.2, H-4 |

Las preguntas P6–P13 siguen abiertas.

1. ¿Los 30 min de inactividad valen también para clientes de la tienda, o sólo para el panel?
2. Tope absoluto para clientes: ¿7 días desde el inicio de sesión (sin deslizar) está bien?
3. Personal: ¿8 h, o 12 h por turnos dobles? ¿Incluye a master y superusuarios? (propuesta: sí)
4. ¿Aceptas que al desplegar AUTH-B todas las sesiones web abiertas se cierren una vez?
5. App móvil: ¿se acepta que siga sin inactividad ni tope hasta MOBILE-SESSION-01, o el personal deja de usarla
   para operaciones internas mientras tanto?
6. ¿El personal puede seguir entrando con Google (hoy sí)? ¿Y con Facebook/Apple (#122: no)?
7. Apple: ¿hay cuenta de Apple Developer Program de organización? ¿Quién guarda Team ID, Key ID y la `.p8`? ¿Qué
   dominio HTTPS se verificará?
8. Meta: ¿app de Meta propia para el login (recomendado) o la del Pixel? ¿Está hecha la verificación del negocio?
9. Google: ¿quién crea el ID de cliente y con qué dominios autorizados (uno por tienda)?
10. ¿Facebook y Apple para toda la instalación (como Google) o por empresa?
11. ¿Se aprovecha el PR #122 (rebasar y partir) en vez de reescribir? (propuesta: sí)
12. Borrado de cuenta y datos (Meta lo exige; Apple, si la app móvil lo usa): ¿quién atiende las solicitudes y qué
    se conserva por obligación legal (comprobantes, pedidos)?
13. En la tienda, ¿aviso a los 25 min sólo en checkout y «Mi cuenta», o en toda la tienda?

## 13. Fuentes

- Google: [Verificar el ID token](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token) ·
  [ID de cliente y orígenes](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid)
- Meta: [Flujo manual](https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow) ·
  [Seguridad](https://developers.facebook.com/docs/facebook-login/security) ·
  [Resumen](https://developers.facebook.com/docs/facebook-login/overview) ·
  [Niveles de acceso](https://developers.facebook.com/docs/graph-api/overview/access-levels) ·
  [Permisos](https://developers.facebook.com/docs/permissions) ·
  [User (ASID)](https://developers.facebook.com/docs/graph-api/reference/user/) ·
  [Mapear usuarios](https://developers.facebook.com/documentation/facebook-login/guides/map-users) ·
  [Callback de eliminación](https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback)
- Apple: [Configurar el entorno](https://developer.apple.com/documentation/signinwithapple/configuring-your-environment-for-sign-in-with-apple) ·
  [Solicitar una autorización](https://developer.apple.com/documentation/signinwithapplerestapi/request-an-authorization-to-the-sign-in-with-apple-server.) ·
  [Crear el client secret](https://developer.apple.com/documentation/accountorganizationaldatasharing/creating-a-client-secret) ·
  [Verificar a un usuario](https://developer.apple.com/documentation/signinwithapple/verifying-a-user) ·
  [Recibir el identity token](https://developer.apple.com/documentation/signinwithapple/receiving-a-users-identity-token) ·
  [Clave pública (JWKS)](https://developer.apple.com/documentation/signinwithapplerestapi/fetch-apple's-public-key-for-verifying-token-signature) ·
  [Revocar tokens](https://developer.apple.com/documentation/signinwithapplerestapi/revoke-tokens) ·
  [Procesar cambios](https://developer.apple.com/documentation/signinwithapple/processing-changes-for-sign-in-with-apple-accounts) ·
  [Borrado de cuenta (5.1.1(v))](https://developer.apple.com/support/offering-account-deletion-in-your-app/)

Consultadas el 2026-10-25. Las páginas de Apple se leyeron por su versión de datos (`/tutorials/data/…json`); lo
que una página no decía explícitamente no se afirma aquí.
