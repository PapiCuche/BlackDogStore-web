# ADR — Revocación efectiva de credenciales (AUTH-REVOCATION-01)

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

## 2. Decisión

Dos revocaciones, porque son dos cosas distintas:

| Qué ocurre | Qué se revoca | Cómo |
|---|---|---|
| Cerrar sesión (web y v1) | **esa** credencial | `RevokedAccessToken`, por `jti` |
| Cambiar o restablecer la contraseña | **todas** las de la cuenta | `UserProfile.tokens_valid_after`, comparado con `iat` |

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
- La rotación del refresh ni su lista negra, que ya funcionaban.
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
