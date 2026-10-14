# Agente de impresión de la tienda

Un programa pequeño que se deja encendido en un equipo **dentro de la red del local**
(la PC de caja, una Raspberry Pi, un Mac). Recoge del servidor los tickets de su sucursal
y los entrega a la impresora térmica: por la red (puerto 9100) o, si la térmica está
enchufada por USB a ese mismo equipo, por una cola **RAW** de CUPS.

Lo que entrega es siempre **ESC/POS**, el idioma de la térmica. Nunca un PDF ni un
PostScript: si le llegaran, los rechaza sin enviarlos (ver «Qué garantiza»).

Quien llama es siempre el agente. No hay que abrir ningún puerto del local.

## Qué hace falta

- Python 3.9 o superior. No instala nada: sólo usa la biblioteca estándar.
- Una térmica de 80 mm (o 58 mm) con ESC/POS, de una de estas dos maneras:
  - **por red** (`tcp`, lo normal): por cable o Wi-Fi, con una dirección fija (por
    ejemplo `192.168.1.50`) y el puerto 9100 abierto;
  - **por USB a este mismo equipo** (`cups_raw`, macOS o Linux con CUPS): con una cola
    RAW de CUPS creada para ella (ver «Térmica USB: transporte `cups_raw`»).
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

## Cómo llega a cada impresora: `printers`

`printers` dice, por el **nombre que la impresora tiene en el panel**, por dónde se le
entregan los tickets:

```json
"printers": {
  "Caja": {"transport": "tcp"},
  "Mostrador USB": {"transport": "cups_raw", "queue": "POS80_RAW"}
}
```

- `tcp` — la dirección y el puerto que guarda el panel. Es lo que vale si no se dice
  nada: una configuración antigua, sin `printers`, sigue funcionando igual.
- `cups_raw` — `lp -d <queue> -o raw` en este equipo. La dirección del panel no se usa.

## Térmica USB: transporte `cups_raw`

**La cola tiene que ser RAW.** Una cola de CUPS con un controlador PostScript o PDF (por
ejemplo «Generic PostScript Printer», el que macOS elige solo para muchas térmicas)
convierte lo que recibe en PostScript, y una térmica ESC/POS imprime ese PostScript como
texto: sale `%!PS-Adobe-3.0 …` y metros de papel. Por eso:

1. Se crea una cola RAW para la térmica, con la misma URI `usb://` que la cola que ya
   existe (se ve con `lpstat -v`):

   ```sh
   lpadmin -p POS80_RAW -E -v 'usb://…la misma URI…' -m raw
   ```

   CUPS avisa de que las colas RAW están «obsoletas»; en macOS actual siguen funcionando.
2. En `config.json`: `"printers": {"<nombre en el panel>": {"transport": "cups_raw",
   "queue": "POS80_RAW"}}`.
3. **Al arrancar**, el agente lee el controlador de la cola con `lpoptions -p POS80_RAW`
   (sólo lee) y **no arranca** si dice «PostScript» o «PDF», o si la cola no existe. El
   mensaje dice qué hacer.
4. Antes de la primera venta, la **prueba de impresión** del panel
   (**Administración › Impresoras**, «Imprimir prueba»): unas pocas líneas, no un
   ticket entero. Si sale texto técnico (`%!PS`, `%PDF`), se apaga la impresora y se
   vacía la cola (`cancel -a <cola>`).

El nombre de la cola sólo admite letras, números, `_`, `-` y `.`; no puede empezar por
guion. En el panel la impresora sigue pidiendo una dirección de la red del local: con
`cups_raw` no se usa, y vale la del propio equipo del agente.

La guía completa para macOS está en `docs/despliegue-produccion.md`, «Impresora térmica
en macOS».

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
- **Sólo ESC/POS.** Un trabajo cuyos bytes empiezan como un PDF (`%PDF`) o un PostScript
  (`%!`), también tras blancos o un envoltorio PJL, que no empieza por `ESC @`, o de un
  tipo de documento que no conoce, no se envía: se informa al servidor como fallido, con
  el motivo. El servidor aplica la misma regla antes de entregarlo.

## Qué no hace

- No imprime por Bluetooth, ni por USB salvo a través de una cola RAW de CUPS
  (`cups_raw`). El navegador no imprime por USB por su cuenta.
- No crea ni cambia colas de CUPS: eso lo hace una persona, una vez.
- No sirve para otra sucursal: un token, un local. Si se pierde, se revoca el agente en
  el panel y se crea otro.
