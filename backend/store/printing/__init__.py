"""
Impresión en la tienda: la cola, el formato de la impresora y el agente.

  · `escpos`   — qué bytes entiende una térmica de 80 mm y cómo se arma un ticket.
  · `services` — la cola: encolar, entregar, confirmar, reintentar.

El servidor NUNCA abre una conexión hacia una impresora. Las impresoras están en
la red de cada local; quien llega a ellas es el agente (`print_agent/`).
"""
