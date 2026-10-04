# Agente de impresión de la tienda

Un programa pequeño que se deja encendido en un equipo **dentro de la red del local**
(la PC de caja, una Raspberry Pi). Recoge del servidor los tickets de su sucursal y los
entrega a la impresora térmica por la red.

Quien llama es siempre el agente. No hay que abrir ningún puerto del local.

## Qué hace falta

- Python 3.9 o superior. No instala nada: sólo usa la biblioteca estándar.
- Una térmica de 80 mm (o 58 mm) con ESC/POS, conectada a la red del local por cable o
  Wi-Fi, con una dirección fija (por ejemplo `192.168.1.50`) y el puerto 9100 abierto.
- En el panel, **Administración › Impresoras**:
  1. dar de alta la impresora con su dirección en la red del local;
  2. crear un agente para esa sucursal y **copiar su token**: se muestra una sola vez.

## Ponerlo en marcha

```sh
cp config.example.json config.json     # y pega el token y la dirección de la tienda
python3 agent.py --config config.json
```

`config.json` lleva el token: no se versiona y no se comparte. `--once` hace un solo
ciclo y termina, útil para probar.

Para que arranque solo con el equipo, se registra como servicio del sistema
(`systemd` en Linux, el Programador de tareas en Windows, `launchd` en macOS) con ese
mismo comando.

## Qué garantiza

- **Un ticket por venta.** El servidor crea un único trabajo por documento, aunque la
  venta se confirme dos veces.
- **El papel no sale dos veces.** El agente apunta en un diario
  (`print-agent-journal.json`) lo que ya imprimió. Si la confirmación se pierde y el
  servidor vuelve a entregar el trabajo, confirma sin imprimir.
- **Si la impresora no responde**, el trabajo vuelve a la cola. Tras cinco intentos
  queda como fallido y se reenvía desde el panel.
- **Sólo la red del local.** El agente no abre conexiones hacia direcciones que no sean
  privadas o `.local`, diga lo que diga el servidor.
- **Sólo HTTPS.** No arranca contra un servidor sin TLS, y el token no se escribe en el
  registro.

## Qué no hace

- No imprime por USB ni por Bluetooth: la impresora tiene que estar en la red.
- No sirve para otra sucursal: un token, un local. Si se pierde, se revoca el agente en
  el panel y se crea otro.
