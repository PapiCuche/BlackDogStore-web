"""
Revocación efectiva de credenciales — H4.1.2B (AUTH-REVOCATION-01).

EL DEFECTO, REPRODUCIDO ANTES DE CORREGIRLO
-------------------------------------------
Cerrar sesión metía el refresh token en la lista negra y borraba las cookies,
pero nadie miraba el ACCESS token: el mismo Bearer seguía respondiendo 200 a la
superficie interna durante lo que le quedara de vida, hasta 30 minutos. Lo mismo
valía para un cambio de contraseña, que promete «vuelve a iniciar sesión» y no
cerraba las sesiones ya abiertas.

SimpleJWT sólo sabe revocar refresh tokens. Un access token es, por diseño,
válido por sí mismo: quien quiera invalidarlo antes de tiempo tiene que
preguntarle a algo.

DOS REVOCACIONES, PORQUE SON DOS COSAS DISTINTAS
------------------------------------------------
  `revoke_access_token(user, token)`  ESTA credencial. Es lo que significa cerrar
                                      sesión: termina la sesión desde la que se
                                      pidió, no las demás que esa persona tenga
                                      abiertas en otros dispositivos.

  `revoke_all_tokens(user)`           TODAS las de la cuenta. Es lo que significa
                                      cambiar o restablecer la contraseña, donde
                                      lo esperado es exactamente lo contrario:
                                      que no sobreviva ninguna sesión anterior.

ACCESS Y REFRESH SON DOS PREGUNTAS DISTINTAS (AUTH-REVOCATION-REFRESH-01)
-------------------------------------------------------------------------
La primera versión de este módulo sólo contestaba por los ACCESS tokens, y con
eso el cierre global era una ilusión: los access morían, y acto seguido
cualquier REFRESH anterior —criptográficamente intacto, emitido antes del
evento— fabricaba un access nuevo y limpio. Reproducido con `200 != 401` en los
dos canales, web y nativo, tras cambiar Y tras restablecer la contraseña.

  `token_is_revoked(user, access)`    ¿Vale esta credencial AHORA? Lista de `jti`
                                      revocados (logout) más el sello. Se
                                      pregunta en cada petición autenticada.

  `refresh_is_revoked(user, refresh)` ¿Puede este refresh CREAR credenciales? Se
                                      pregunta antes de rotarlo, de ennegrecerlo
                                      como consecuencia normal del refresh y de
                                      emitir nada nuevo. Un token que ya no vale
                                      no puede ser la causa de uno que sí valga.

El `jti` no se consulta para el refresh: esa lista es de access tokens, y un
refresh cerrado por logout ya lo rechaza la lista negra de SimpleJWT.

LO QUE NO SE HIZO, Y POR QUÉ
----------------------------
No se bajó el `ACCESS_TOKEN_LIFETIME`. Acortar la ventana no es revocar: deja el
mismo agujero, más estrecho, y paga con un refresh constante.

No se guardó en caché. Este despliegue no configura `CACHES`, así que Django usa
memoria local por proceso: una revocación ahí no existiría para el proceso de al
lado ni sobreviviría a un reinicio.

No se cambió el formato del token ni el contrato de la API. Se comparan `jti` e
`iat`, que SimpleJWT ya emite.

LÍMITE CONOCIDO, Y HACIA QUÉ LADO SE FALLA
------------------------------------------
`iat` tiene resolución de un segundo, así que un token emitido en el MISMO
segundo que la revocación no se puede distinguir de uno emitido justo antes. La
comparación es INCLUSIVA (`iat <= sello`): en la duda, no vale.

El coste es que quien cambia su contraseña y vuelve a entrar dentro de ese mismo
segundo recibe un token muerto y tiene que repetir el inicio de sesión. Se eligió
a propósito y con la medición delante: la primera versión de este módulo excluía
el borde, y al probarla el cambio de contraseña NO revocaba nada —el login y el
cambio caían en el mismo segundo—. Un agujero de un segundo en un control de
seguridad es un agujero.
"""
from datetime import datetime, timezone as dt_timezone

from django.utils import timezone


def _payload_of(validated_token) -> dict:
    return getattr(validated_token, 'payload', None) or {}


def revoke_access_token(user, validated_token) -> None:
    """
    Invalida ESE access token. Idempotente: revocar dos veces es revocar.

    Un token sin `jti` o sin `exp` no se puede registrar ni caducar, así que se
    ignora en silencio; el llamador ya hizo lo demás (lista negra del refresh y
    borrado de cookies).
    """
    from .models import RevokedAccessToken

    payload = _payload_of(validated_token)
    jti, exp = payload.get('jti'), payload.get('exp')
    if user is None or not getattr(user, 'pk', None) or not jti or not exp:
        return

    RevokedAccessToken.objects.get_or_create(
        jti=jti,
        defaults={
            'user': user,
            'expires_at': datetime.fromtimestamp(int(exp), tz=dt_timezone.utc),
        },
    )
    # Limpieza oportunista: de un token vencido no queda nada que revocar, y la
    # tabla no tiene por qué crecer para siempre.
    RevokedAccessToken.objects.filter(expires_at__lt=timezone.now()).delete()


def _blacklist_outstanding_refreshes(user) -> None:
    """
    Mete en la lista negra de SimpleJWT todos los refresh vivos de la cuenta.

    NO SUSTITUYE AL SELLO, y el orden de autoridad importa. El sello es la regla:
    responde aunque la fila del `OutstandingToken` se haya purgado, aunque el
    token venga de un camino que no la registrara, y sin depender de que nadie
    se acuerde de preguntar. Esto añade que la credencial muera también dentro de
    la PROPIA biblioteca —`RefreshToken(raw)` falla al construirla—, de modo que
    cualquier camino de refresh, presente o futuro, la rechaza sin haber tenido
    que enterarse de que este módulo existe.

    Coste acotado: una fila por sesión viva de esa cuenta, y sólo al cambiar o
    restablecer la contraseña. No se toca en el logout, que es por sesión.
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )
    except ImportError:  # pragma: no cover — la app de lista negra está instalada
        return

    for outstanding in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=outstanding)


def revoke_all_tokens(user) -> None:
    """Cierra TODAS las sesiones de la cuenta: contraseña cambiada o restablecida."""
    from .models import UserProfile

    if user is None or not getattr(user, 'pk', None):
        return
    UserProfile.objects.update_or_create(
        user=user, defaults={'tokens_valid_after': timezone.now()},
    )
    _blacklist_outstanding_refreshes(user)


def _issued_before_revocation(user, payload) -> bool:
    """
    Si esta credencial nació antes —o dentro— del cierre global de la cuenta.

    Una sola definición, usada por el access y por el refresh: dos respuestas
    distintas a esta pregunta serían dos políticas de seguridad conviviendo.
    """
    stamp = getattr(getattr(user, 'profile', None), 'tokens_valid_after', None)
    if stamp is None:
        return False

    issued_at = payload.get('iat')
    if issued_at is None:
        # Un token sin fecha de emisión no se puede comparar con el sello.
        # «No sé» sólo tiene una lectura segura: no vale.
        return True
    # Inclusiva a propósito: ver «LÍMITE CONOCIDO» arriba.
    return int(issued_at) <= int(stamp.timestamp())


def token_is_revoked(user, validated_token) -> bool:
    """
    Si este ACCESS fue revocado, o si lo fueron todos antes de emitirlo.

    Se consulta en cada petición autenticada: una revocación que sólo se notara
    al renovar el token no sería una revocación, sería un aviso.
    """
    from .models import RevokedAccessToken

    payload = _payload_of(validated_token)

    jti = payload.get('jti')
    if jti and RevokedAccessToken.objects.filter(jti=jti).exists():
        return True

    return _issued_before_revocation(user, payload)


def refresh_is_revoked(user, token) -> bool:
    """
    Si este REFRESH precede a un cierre global y por tanto no puede emitir nada.

    Se pregunta ANTES de rotar, de ennegrecer y de crear un token nuevo. El orden
    no es un detalle: rotar primero deja al refresh muerto habiendo engendrado
    una credencial viva, que es exactamente el agujero que esto cierra.
    """
    return _issued_before_revocation(user, _payload_of(token))
