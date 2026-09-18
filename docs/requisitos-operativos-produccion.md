# Requisitos operativos de producción

Configuración que **no** se deduce del código y que debe fijarse según la
topología real del despliegue **antes** de exponer el ERP a producción. Cada
entrada nombra el ajuste, por qué existe, el riesgo de dejarlo mal y cómo
verificarlo.

Este documento crece por entradas; no es una fase ni un rediseño.

---

## 1. `TRUSTED_PROXY_COUNT` — identidad de cliente para el rate-limit de login

**Origen:** ERP-1 · AUTH-THROTTLE-01 (commit `615e3d2`). El login
(`LoginThrottle`, scope `login`, `5/min`) se limita **por IP** vía
`get_ident()` de DRF, tanto con sesión como sin ella. `get_ident()` respeta
`NUM_PROXIES`, que en este proyecto es exactamente `TRUSTED_PROXY_COUNT`
(`backend/settings.py`).

**Qué hace el valor:**

- `TRUSTED_PROXY_COUNT = 0` (por defecto): se **ignora** `X-Forwarded-For` y la
  identidad es la IP de conexión (`REMOTE_ADDR`). Correcto cuando Django recibe
  la conexión directamente o cuando **no** hay un proxy inverso de confianza
  delante.
- `TRUSTED_PROXY_COUNT = N > 0`: DRF lee el cliente real saltando exactamente
  `N` entradas por la derecha de `X-Forwarded-For`. `N` es el número de proxies
  **que tú controlas** y que añaden al header (nginx, ALB, Cloudflare, …).

**Riesgos si queda mal configurado:**

- **Demasiado bajo tras un proxy** (p. ej. `0` detrás de nginx): todas las
  peticiones llegan con la IP del proxy, así que **todos los clientes comparten
  un único bucket**. El límite de 5/min se agota de inmediato y usuarios
  legítimos se bloquean entre sí (denegación de servicio autoinfligida).
- **Confiar en `X-Forwarded-For` sin una frontera de proxy real** (valor mayor
  que la cadena de proxies controlados): el atacante **controla su propia clave
  de throttle** falsificando el header y mintea un bucket nuevo por petición,
  saltándose el límite anti-fuerza-bruta por completo.

**NAT / IP compartida:** clientes tras un mismo NAT (red corporativa, CGNAT
móvil) comparten IP y, por tanto, comparten bucket de login. Es correcto para la
defensa anti-fuerza-bruta, pero implica que usuarios legítimos con salida
compartida pueden ver un `429` antes. No es motivo para desactivar el throttle
ni para confiar en `X-Forwarded-For` sin frontera.

**Requisito antes de producción:** fijar `TRUSTED_PROXY_COUNT` al número exacto
de proxies de confianza delante de Django. Verificar enviando una petición con
un `X-Forwarded-For` falsificado y comprobando que la clave de throttle es la IP
real del cliente, no la inyectada.

**Alcance:** esto es **solo** configuración de despliegue. El agujero de
seguridad (login sin límite cuando había sesión) ya está cerrado en el código
por `615e3d2`; no se requiere rediseño del throttle. Queda pendiente, como P1/P2
de seguimiento, extender el mismo patrón por-IP a `register`, reseteo de
contraseña y emisión fiscal (hoy `AnonRateThrottle`, que no limita a peticiones
autenticadas) — superficies de menor riesgo, fuera de este alcance.
