# ADR — Revocación efectiva de credenciales (AUTH-REVOCATION-01 · -REFRESH-01)

**Fase:** H4.1.2B · **Estado:** aceptado · **Migración:** `0084_userprofile_tokens_valid_after_revokedaccesstoken`

Complementa [adr-auth-v1-internal.md](adr-auth-v1-internal.md) (DEC-API-004), que
decidió *qué credencial se evalúa*. Este decide *cuándo deja de valer*.

## 1. El problema, reproducido antes de decidir nada

```
login v1  → Bearer → GET /api/v1/internal/<slug>/context/   200
logout    → 200
MISMO Bearer → GET /api/v1/internal/<slug>/context/         200   ← el defecto
refresh reutilizado                                         401   (esto sí funcionaba)
```

Cerrar sesión metía el **refresh** en la lista negra de SimpleJWT y borraba las
cookies. Nadie miraba el **access token**, que es válido por sí mismo: seguía
abriendo la API durante lo que le quedara de vida, **hasta 30 minutos**.

Lo mismo ocurría al cambiar la contraseña, una pantalla que promete «vuelve a
iniciar sesión» y dejaba vivas las sesiones ya abiertas —incluida la de quien
cambia la contraseña precisamente porque cree que alguien entró en su cuenta—.

Borrar la cookie deja sin credencial al navegador honrado y no le quita nada a
quien ya copió el token.

### 1.1 El agujero que dejó la primera versión (AUTH-REVOCATION-REFRESH-01)

Revisión externa. Cerrado lo anterior, el cierre global seguía siendo una
ilusión: se revocaban los ACCESS y **ningún camino de refresh preguntaba nada**.

```
login v1 → access_A, refresh_A
cambiar (o restablecer) la contraseña
access_A  → ruta interna                                     401   ← ya funcionaba
refresh_A → POST /api/v1/auth/refresh/                       200   ← el defecto
            → access NUEVO, válido, recién emitido
refresh_A (cookie) → POST /api/auth/refresh/                 200   ← el defecto
```

Reproducido con nueve pruebas antes de tocar el código: **siete en rojo, todas
`200 != 401`**, en los dos canales y tanto al cambiar como al restablecer.

`V1RefreshView` validaba firma, usuario y `is_active`, y pasaba directo a
`blacklist()` + `RefreshToken.for_user()`. El `RefreshView` web delegaba en
`TokenRefreshSerializer`, que sólo sabe de firma, caducidad y lista negra.
Ninguno de los dos miraba el sello.

Para quien restablece la contraseña **porque cree que entraron en su cuenta**,
esto es el caso que importa: quien tuviera un refresh anterior se fabricaba una
sesión nueva justo después del cierre.

## 2. Decisión

Dos revocaciones, porque son dos cosas distintas:

| Qué ocurre | Qué se revoca | Cómo |
|---|---|---|
| Cerrar sesión (web y v1) | **esa** credencial | `RevokedAccessToken`, por `jti` (access) + lista negra de SimpleJWT (refresh) |
| Cambiar o restablecer la contraseña | **todas** las de la cuenta, access **y refresh** | `UserProfile.tokens_valid_after`, comparado con `iat` + ennegrecido de los `OutstandingToken` |

Ambas clases de autenticación —`CookieJWTAuthentication` y
`V1BearerAuthentication`— preguntan en cada petición, después de comprobar
`is_active`, y responden con **el mismo mensaje** que cualquier otro fallo: quien
sostiene una credencial muerta no tiene por qué enterarse de por qué murió.

**Por qué por `jti` en el logout.** Cerrar sesión termina *esa* sesión: quien
cierra en el móvil no espera que se cierre la del mostrador. Una revocación
global por usuario habría sido más fácil y habría cambiado, en silencio, lo que
significa el botón.

**Por qué el sello es global en la contraseña.** Ahí lo esperado es exactamente
lo contrario, y es lo que esa pantalla ya prometía por escrito.

### 2.1 ACCESS y REFRESH son dos preguntas distintas

No son el mismo control con dos nombres. Una pregunta si la credencial **vale
ahora**; la otra, si puede **crear** credenciales que valgan después.

| Función | Pregunta | Cuándo se hace |
|---|---|---|
| `token_is_revoked(user, access)` | ¿vale esta credencial? | en **cada** petición autenticada, tras `is_active` |
| `refresh_is_revoked(user, refresh)` | ¿puede este refresh emitir algo? | **antes** de rotar, de ennegrecer y de emitir |

El orden de esa segunda es la decisión, no un detalle de implementación: rotar
primero deja al refresh muerto **habiendo engendrado** una credencial viva, que
es justo el agujero. La comparación con `iat` es la misma en ambas
(`_issued_before_revocation`): dos definiciones distintas del mismo umbral serían
dos políticas de seguridad conviviendo.

El `jti` no se consulta para el refresh — esa lista es de access tokens — y un
refresh cerrado por logout ya lo rechaza la lista negra de SimpleJWT.

### 2.2 Dos candados en la contraseña, y por qué no basta uno

`revoke_all_tokens()` además **ennegrece todos los `OutstandingToken`** vivos de
la cuenta. No es un reflejo; se evaluó contra SimpleJWT 5.5.1:

- `BlacklistMixin.for_user()` registra una fila por refresh emitido, así que las
  filas existen y la operación está acotada: una por sesión viva, y sólo al
  cambiar o restablecer la contraseña. El logout no la usa: sigue siendo por
  sesión.
- **El sello manda.** Es la autoridad porque responde aunque la fila se haya
  purgado o el token venga de un camino que no la registrara.
- **La lista negra cubre lo que el sello no puede garantizar solo:** que la
  credencial muera dentro de la **propia biblioteca**. `RefreshToken(raw)` falla
  al construirse, de modo que cualquier camino de refresh —presente o futuro,
  escrito por quien sea— la rechaza sin tener que acordarse de preguntar. El
  defecto que se está cerrando fue exactamente eso: dos caminos que no
  preguntaron.

## 3. Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| **Bajar `ACCESS_TOKEN_LIFETIME`** | Acortar la ventana no es revocar: deja el mismo agujero, más estrecho, y paga con un refresh constante. Se descarta explícitamente porque es la salida fácil |
| **Lista negra en caché** | Este despliegue no configura `CACHES`: Django usa memoria local **por proceso**. Una revocación ahí no existiría para el proceso de al lado ni sobreviviría a un reinicio |
| **Sesiones opacas en servidor** | Cambia la arquitectura de autenticación entera y el contrato de Mobile. Fuera del alcance de un gate de seguridad |
| **Claim propio en el token** | Cambiaría el formato del token y obligaría a reemitir; `jti` e `iat` ya viajan |

## 4. Consecuencias

- **Coste:** una consulta indexada por `jti` en cada petición autenticada, y el
  perfil ya viaja con `select_related('profile')` para no añadir otra.
- **Las filas caducan solas:** `expires_at` es el `exp` del propio token y se
  purgan las vencidas al revocar. De un token muerto no queda nada que revocar.
- **Granularidad de un segundo.** `iat` se emite en segundos, así que un token
  emitido en el MISMO segundo que un cambio de contraseña no se distingue de uno
  anterior. La comparación es **inclusiva**: en la duda, no vale.

  El coste es que quien cambia la contraseña y vuelve a entrar dentro de ese
  segundo recibe un token muerto y repite el inicio de sesión. Se eligió con la
  medición delante: la primera versión excluía el borde y, al probarla, el cambio
  de contraseña **no revocaba nada**, porque login y cambio caían en el mismo
  segundo. Un agujero de un segundo en un control de seguridad es un agujero.
- **El logout de v1 revoca el access token sólo si el cliente lo presenta** en la
  cabecera. La ruta sigue sin exigir autenticación —cerrar sesión tiene que ser
  posible con el access ya caducado—, así que sin cabecera se comporta como
  antes: muere el refresh. El cliente oficial lo envía.

## 5. Lo que NO cambió

- El formato del token, el algoritmo y los tiempos de vida.
- La orquestación de H4.1.1: una request, un canal; el Bearer nunca cae a la
  cookie; CSRF sólo en el canal cookie.
- La rotación del refresh y su lista negra siguen como estaban; lo que cambia es
  que ahora se pregunta por la revocación **antes** de que roten, y que un cierre
  global las ennegrece en bloque.
- El contrato de la API: ninguna ruta nueva, ningún campo nuevo en las respuestas
  de autenticación.

## 6. Qué queda abierto

- **Revocar al revocar una Membership no hace falta:** las capacidades se
  resuelven en cada petición, así que quitar una membresía surte efecto en la
  siguiente llamada. Lo que el token afirma es *quién eres*, no *qué puedes*.
- **Desactivar una cuenta** ya se comprobaba (`is_active`) y sigue igual.
- **Purga programada** de `RevokedAccessToken`: hoy es oportunista, al revocar.
  Con volumen real conviene una tarea periódica; queda registrado como deuda
  menor en el estado actual.
