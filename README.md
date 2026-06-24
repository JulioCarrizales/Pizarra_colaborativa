# Pizarra Colaborativa (estilo Excalidraw)

Proyecto del curso **Sistemas Operativos I — V Ciclo (ESAN)**.
Opción 17 del catálogo: *Pizarra colaborativa (Excalidraw)*.
Tema de SO protagonista: **WebSockets y concurrencia**.

Aplicación web donde varias personas dibujan en un mismo lienzo en tiempo real.
Cada cambio (trazo, forma, texto) se reenvía por WebSocket a todos los clientes
conectados, y el servidor mantiene en memoria el estado compartido de la pizarra.

Incluye **cuentas de usuario** y **pizarras privadas con código** para compartir.

**Despliegue con Podman** (rootless, como pod + volumen + red): ver
[docs/DESPLIEGUE.md](docs/DESPLIEGUE.md).

> Pendiente de las siguientes fases: la instrumentación de SO (simulador de N
> clientes, medición de descriptores de archivo, conexiones y memoria por conexión),
> el despliegue en AWS EC2 y el dominio con HTTPS.

## Características

- Lienzo de dibujo con herramientas: lápiz, rectángulo, elipse, línea, flecha, texto y borrador.
- Selector de color, paleta rápida y grosor de trazo.
- **Colaboración en tiempo real**: dibujo en vivo, cursores de otros usuarios y contador de conectados.
- Estado compartido en el servidor: quien entra recibe la pizarra tal como está.
- Reconexión automática del cliente si se cae la conexión.

## Arquitectura

```
Navegador (canvas + WebSocket)  <--ws-->  server.py
        |                                    |
   static/ (HTML/CSS/JS)               estado compartido
                                       (escena + drafts + clientes)
```

- **`server.py`** — servidor HTTP (sirve el frontend) + servidor WebSocket asíncrono
  (`websockets`, asyncio). Cada conexión es una corrutina.
- **`static/`** — frontend: `index.html`, `style.css`, `app.js`.

## Requisitos

- Python 3.10+
- Dependencia: `websockets`

```bash
pip install -r requirements.txt
```

## Cómo ejecutar

```bash
python server.py
```

Luego abre **http://localhost:8000** en el navegador. Para probar la colaboración,
abre la misma dirección en **dos o más pestañas/equipos** y dibuja: los cambios
aparecen al instante en todas.

Puertos:
- `8000` → frontend (HTTP)
- `8765` → canal de colaboración (WebSocket)

## Protocolo (mensajes JSON)

| Tipo       | Dirección        | Descripción                                  |
|------------|------------------|----------------------------------------------|
| `init`     | servidor→cliente | Estado inicial de la pizarra al conectarse.  |
| `draft`    | ambos            | Elemento que se está dibujando (en vivo).    |
| `add`      | ambos            | Elemento confirmado (se suelta el mouse).    |
| `delete`   | ambos            | Borrar un elemento.                          |
| `clear`    | ambos            | Limpiar toda la pizarra.                     |
| `cursor`   | ambos            | Posición del cursor de un usuario.           |
| `presence` | servidor→cliente | Número de usuarios conectados.               |
| `leave`    | servidor→cliente | Un usuario se desconectó.                    |
