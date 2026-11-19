# Good Doggy CRM en el mismo servidor que la tienda

La tienda y el CRM son dos aplicaciones independientes. Este documento cubre **sólo lo que la tienda pone de su parte**: su Caddy termina TLS para el nombre público del CRM y le pasa el tráfico al proxy del CRM. Todo lo demás del CRM (imágenes, base de datos, despliegue, backups) vive en su propio repositorio, en `docs/runbooks/production-shared-host.md` y en su ADR-046.

## Qué cambia en la tienda

| Archivo | Cambio | Efecto si no se usa el override |
| --- | --- | --- |
| `deploy/Caddyfile` | Una línea al final: `import /etc/caddy/sites.d/*.caddy` | Ninguno: sin la carpeta montada no importa nada (Caddy avisa en el log y sigue) |
| `deploy/sites.d/crm.caddy` | El sitio del CRM: TLS, tabla de topes de cuerpo, `X-Forwarded-For` = IP real, sin otras cabeceras | No se monta |
| `deploy/compose.crm-ingress.yml` | Override sólo de `caddy`: red externa `shared-ingress` con IP fija, montaje de `sites.d`, `CRM_SITE_HOST` y `CRM_UPSTREAM` | — |

Backend, frontend y PostgreSQL de la tienda no cambian ni se recrean. El CRM no entra en `blackdog_default`.

## Aislamiento

- `shared-ingress` es **interna** (sin salida a Internet) y sólo tiene dos miembros: el Caddy de la tienda (`172.31.250.2` por omisión) y el proxy del CRM (`172.31.250.3`).
- El proxy del CRM sólo cree el `X-Forwarded-For` que llega desde la IP fija del Caddy de la tienda. El Caddy de la tienda sólo cree `CF-Connecting-IP` si la conexión viene de Cloudflare. Un cliente no puede elegir su IP en ningún tramo; el límite de tasa del CRM cuenta por cliente, no por proxy.
- Ninguna variable, archivo `.env`, volumen ni base de datos se comparte.

## Aplicar (sólo con autorización y en la instancia nueva, antes del corte)

```bash
# 1. La red, una vez por servidor. Antes, comprobar que la subred no se solapa con otra.
docker network ls -q | xargs docker network inspect -f '{{.Name}} {{range .IPAM.Config}}{{.Subnet}}{{end}}'
docker network create --driver bridge --internal --subnet 172.31.250.0/29 shared-ingress

# 2. En deploy/.env.production, sin tocar ninguna otra variable:
#    CRM_SITE_HOST=crm.<dominio>

# 3. Validar ANTES de aplicar (no toca el Caddy en marcha):
docker run --rm --env-file deploy/.env.production -e CADDY_TLS_DIRECTIVE= -e CRM_UPSTREAM=172.31.250.3:80 \
  -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" -v "$PWD/deploy/sites.d:/etc/caddy/sites.d:ro" \
  caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose -f docker-compose.prod.yml -f deploy/compose.crm-ingress.yml --env-file deploy/.env.production config -q

# 4. Aplicar: recrea SÓLO caddy (unos segundos sin 443).
docker compose -f docker-compose.prod.yml -f deploy/compose.crm-ingress.yml --env-file deploy/.env.production up -d --no-deps caddy
```

Desde ese momento, **todos** los `up` de la tienda llevan los dos `-f`. Un `up` con sólo `docker-compose.prod.yml` recrea `caddy` sin la red ni el sitio del CRM: la tienda sigue bien y el CRM deja de responder hasta repetirlo con el override. Las tareas de `crontab` (sólo `exec`) no se ven afectadas.

## Volver atrás

```bash
docker compose -f docker-compose.prod.yml --env-file deploy/.env.production up -d --no-deps caddy
```

Recrea `caddy` como estaba: sin la red ni el sitio del CRM. La tienda no pierde rutas ni cabeceras. Guardar antes una copia del `Caddyfile` en marcha (`backups/Caddyfile.before-crm-<fecha>`), como en los cambios anteriores.

## Recargar sin recrear

`deploy/Caddyfile` se monta como **archivo**. Un editor que lo reemplaza (nuevo inodo) deja al contenedor viendo el viejo. Para un cambio de Caddyfile sin recrear: escribir encima (`cat nuevo > deploy/Caddyfile`) y `docker compose … exec caddy caddy reload --config /etc/caddy/Caddyfile`. Los WebSocket del CRM aguantan la recarga 5 minutos (`stream_close_delay`).
