# Agente de impresión de la tienda

Un programa pequeño que se deja encendido en un equipo **dentro de la red del local**
(la PC de caja, una Raspberry Pi). Recoge del servidor los tickets de su sucursal y los
entrega a la impresora térmica: por la red o, en Windows, a una térmica USB de ese mismo
equipo (ver «Windows con térmica USB»).

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
ciclo y termina, útil para probar. `--check` comprueba el diario, las colas y el token
**sin imprimir y sin recoger trabajos**.

Las rutas relativas de `journal` y `log_file` se leen **junto a `config.json`**, no en
la carpeta desde la que se lanzó el programa. El agente no arranca si el diario no se
puede escribir, si existe y no se puede leer, o si otro agente ya usa ese mismo diario.

Para que arranque solo con el equipo, se registra como servicio del sistema
(`systemd` en Linux, el Programador de tareas en Windows, `launchd` en macOS) con ese
mismo comando.

## Windows con térmica USB (`windows_raw`)

Para una térmica ESC/POS conectada por USB a la PC de caja, con su controlador instalado
y una cola de Windows (por ejemplo `POS-80-Series`). El agente le manda los bytes del
ticket **en RAW**, por la API de impresión de Windows (`winspool.drv`), sin instalar nada.

```json
"printers": {"Caja": {"transport": "windows_raw", "queue": "POS-80-Series"}}
```

- `Caja` es el **nombre de la impresora en el panel**; `queue`, el **nombre exacto de la
  cola en Windows** (`Get-Printer`). Ver `config.windows-usb.example.json`.
- La cola sale de este archivo, nunca del servidor. Con `printers`, una impresora del
  panel que no está en la lista no recibe nada de este equipo (por ejemplo, si se
  renombra en el panel).
- No se aceptan colas de otro equipo (`\\servidor\cola`) ni nombres con comas.
- Antes de imprimir mira si la cola está en pausa, sin papel o desconectada; después
  espera (`spool_wait_seconds`, 20 s por defecto, máximo 45) a que Windows saque el
  trabajo de la cola. Si se queda atascado sin empezar, lo retira y lo reintenta más
  tarde.
- **«Impreso» quiere decir «Windows se lo entregó a la impresora».** Que el papel salió
  no lo puede saber ni Windows ni el agente.
- Si la impresión del controlador abre el cajón o pita (opciones del controlador), con
  RAW no se aplica: el ticket lleva sólo lo que dibuja el servidor.

```powershell
py -3 agent.py --config C:\ProgramData\BlackDogStore\PrintAgent\config.json --check
```

### Lo que no se sabe no se repite

Antes de entregar un ticket, el diario lo apunta «en vuelo». Si el equipo se apaga a
mitad, o Windows no puede decir si salió, el ticket queda **retenido**: no se vuelve a
mandar solo y el servidor lo ve como fallido con un motivo que empieza por `INCIERTO`.
Quien atiende mira el mostrador:

- si **salió**, no hay que hacer nada;
- si **no salió**, se pide una reimpresión nueva (un trabajo nuevo: `POST
  /api/admin/printing/jobs/` con `Idempotency-Key`). «Reintentar» en el panel reutiliza el
  mismo trabajo y el agente lo vuelve a rechazar, **salvo** que antes se libere en este
  equipo con el agente detenido:

```powershell
py -3 agent.py --config ...\config.json --list-held
py -3 agent.py --config ...\config.json --release-held <uid>
```

### Limitaciones mientras el servidor no distinga impresoras USB

- El panel pide todavía una dirección de red para cada impresora; con `windows_raw` no se
  usa. Hasta que el servidor admita impresoras sin dirección (cambio de modelo pendiente
  de aprobación), **no** se debe inventar una IP para dar de alta una USB.
- **Un solo agente por sucursal.** El servidor reparte los trabajos de la sucursal a
  cualquier agente de ella: uno que no tiene la impresora en su lista lo da por fallido y
  gasta uno de los cinco intentos.

### Actualizar desde una versión anterior

- Antes, un `journal` relativo se buscaba en la carpeta de trabajo; ahora, junto a
  `config.json`. Si el diario de siempre sigue en la carpeta de trabajo y no junto a
  `config.json`, el agente no arranca y dice adónde moverlo.
- El diario nuevo guarda también los retenidos. Volver a la versión anterior con un
  diario que tenga retenidos no está soportado: libérelos o vacíe `unconfirmed` antes.

### Arranque automático

`windows\install-task.ps1` deja el agente en `C:\ProgramData\BlackDogStore\PrintAgent`,
propiedad de Administradores. La cuenta del agente sólo **lee** `agent.py` y `config.json`
y sólo **escribe** en `data\` (diario, candado y registro: `journal` y `log_file` tienen
que estar en `data\`). Registra una
tarea programada que arranca con el equipo, sin privilegios elevados, una sola instancia y
reinicio si se cae. Se ejecuta una vez en una PowerShell **de administrador**. No abre
ningún puerto: el agente sólo hace peticiones HTTPS salientes.

En un portátil, configure que **no se suspenda enchufado**: suspendido, no imprime.

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

- No imprime por Bluetooth, ni por USB salvo a través de una cola de Windows
  (`windows_raw`). No escucha en ningún puerto.
- No sirve para otra sucursal: un token, un local. Si se pierde, se revoca el agente en
  el panel y se crea otro.
