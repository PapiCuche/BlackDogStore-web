#!/usr/bin/env python3
"""
Un servidor de correo de mentira, para ensayar el envío real sin enviar nada.

Habla el SMTP justo que necesita un cliente para entregar un mensaje —saludo,
autenticación, sobre y cuerpo— y guarda cada mensaje en un archivo. No reenvía
nada a ningún sitio, no cifra y no sale de la máquina: es el «entorno
controlado» en el que el ensayo comprueba que la tienda envía de verdad por
SMTP (y no escribe los correos en su registro), qué pasa si el servidor de
correo rechaza la contraseña y qué pasa si no está.

    rehearsal_smtp_sink.py <puerto> <carpeta> [usuario contraseña]
    rehearsal_smtp_sink.py <puerto> <carpeta> --stall

Con usuario y contraseña, sólo acepta esa pareja (AUTH PLAIN o LOGIN); sin
ellos acepta cualquiera. Con `--stall` acepta la conexión y no dice nada: es el
servidor de correo que ha dejado de responder, el caso en el que la tienda
tiene que cansarse de esperar. Cada mensaje queda en <carpeta>/NNNN.eml, y su sobre
en la primera línea como comentario.

Sólo la biblioteca estándar. No es un servidor de correo: no lo uses como tal.
"""
import base64
import os
import socketserver
import sys
import threading

_LOCK = threading.Lock()


class Handler(socketserver.StreamRequestHandler):
    def reply(self, text):
        self.wfile.write(text.encode('ascii') + b'\r\n')
        self.wfile.flush()

    def line(self):
        return self.rfile.readline().decode('utf-8', 'replace').rstrip('\r\n')

    def allowed(self, user, password):
        expected = self.server.credentials
        return expected is None or (user, password) == expected

    def handle(self):
        if self.server.stall:
            self.rfile.read(1)      # hasta que el cliente se canse y cuelgue
            return
        sender, recipients, authenticated = '', [], self.server.credentials is None
        self.reply('220 ensayo ESMTP')
        while True:
            raw = self.line()
            if not raw:
                return
            command, _, argument = raw.partition(' ')
            command = command.upper()
            if command in ('EHLO', 'HELO'):
                self.wfile.write(b'250-ensayo\r\n250-AUTH PLAIN LOGIN\r\n250 SIZE 10485760\r\n')
                self.wfile.flush()
            elif command == 'AUTH':
                mechanism, _, initial = argument.partition(' ')
                try:
                    if mechanism.upper() == 'PLAIN':
                        if not initial:
                            self.reply('334 ')
                            initial = self.line()
                        _zone, user, password = base64.b64decode(initial).decode().split('\x00')
                    else:
                        self.reply('334 VXNlcm5hbWU6')
                        user = base64.b64decode(self.line()).decode()
                        self.reply('334 UGFzc3dvcmQ6')
                        password = base64.b64decode(self.line()).decode()
                except Exception:
                    self.reply('501 5.5.2 Cannot decode')
                    continue
                authenticated = self.allowed(user, password)
                self.reply('235 2.7.0 Authentication successful' if authenticated
                           else '535 5.7.8 Authentication credentials invalid')
            elif command == 'MAIL':
                if not authenticated:
                    self.reply('530 5.7.0 Authentication required')
                    continue
                sender, recipients = argument, []
                self.reply('250 OK')
            elif command == 'RCPT':
                recipients.append(argument)
                self.reply('250 OK')
            elif command == 'DATA':
                self.reply('354 End data with <CR><LF>.<CR><LF>')
                body = []
                while True:
                    chunk = self.rfile.readline()
                    if chunk in (b'.\r\n', b'.\n', b''):
                        break
                    body.append(chunk[1:] if chunk.startswith(b'..') else chunk)
                with _LOCK:
                    number = len([n for n in os.listdir(self.server.folder) if n.endswith('.eml')]) + 1
                    with open(os.path.join(self.server.folder, f'{number:04d}.eml'), 'wb') as handle:
                        handle.write(f'# {sender} -> {", ".join(recipients)}\n'.encode())
                        handle.write(b''.join(body))
                self.reply('250 OK: queued')
            elif command == 'RSET':
                sender, recipients = '', []
                self.reply('250 OK')
            elif command == 'NOOP':
                self.reply('250 OK')
            elif command == 'QUIT':
                self.reply('221 Bye')
                return
            else:
                self.reply('502 5.5.1 Command not implemented')


class Sink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port, folder, credentials=None, host='0.0.0.0', stall=False):
        os.makedirs(folder, exist_ok=True)
        self.folder, self.credentials, self.stall = folder, credentials, stall
        super().__init__((host, port), Handler)


def main():
    port, folder = int(sys.argv[1]), sys.argv[2]
    stall = '--stall' in sys.argv[3:]
    credentials = (sys.argv[3], sys.argv[4]) if len(sys.argv) > 4 and not stall else None
    with Sink(port, folder, credentials, stall=stall) as server:
        print(f'sumidero SMTP en el puerto {port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
