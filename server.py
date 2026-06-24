"""
Pizarra Colaborativa (estilo Excalidraw)
Servidor de la aplicacion - Proyecto Sistemas Operativos I (V Ciclo)

Tema de SO protagonista: WebSockets y concurrencia.

Arquitectura:
  - Un servidor HTTP (libreria estandar) que sirve el frontend estatico (static/).
  - Un servidor WebSocket asincrono (libreria 'websockets') que mantiene el estado
    compartido de la pizarra y reenvia (broadcast) los eventos de dibujo a todos
    los clientes conectados en tiempo real.

Todo corre en un solo proceso: el HTTP en un hilo de fondo y el WebSocket sobre el
event loop de asyncio. Cada conexion es una corrutina, lo que mas adelante permite
medir descriptores de archivo, conexiones y memoria por conexion.
"""

import asyncio
import http.server
import itertools
import json
import os
import signal
import socketserver
import threading

import websockets

# ----------------------------------------------------------------------------
# Configuracion
# ----------------------------------------------------------------------------
HOST = "0.0.0.0"
HTTP_PORT = 8000          # frontend (http://localhost:8000)
WS_PORT = 8765            # canal de colaboracion (ws://localhost:8765)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Paleta para identificar a cada usuario (cursor y nombre).
USER_COLORS = [
    "#e03131", "#1971c2", "#2f9e44", "#f08c00",
    "#9c36b5", "#0c8599", "#e8590c", "#5f3dc4",
]

# ----------------------------------------------------------------------------
# Estado compartido de la pizarra (vive en memoria del proceso)
# ----------------------------------------------------------------------------
# scene  : lista ordenada de elementos ya "confirmados" (trazos, formas, texto).
# drafts : elemento que cada usuario esta dibujando en este momento (en vivo).
# clients: websocket -> info del usuario (id, color, nombre).
scene = []
drafts = {}
clients = {}

_id_counter = itertools.count(1)
_user_counter = itertools.count(1)


def assign_user():
    """Genera id, color y nombre para un cliente nuevo."""
    n = next(_user_counter)
    color = USER_COLORS[(n - 1) % len(USER_COLORS)]
    return {"id": n, "color": color, "name": f"Usuario {n}"}


async def broadcast(message, exclude=None):
    """Envia un mensaje (dict) a todos los clientes, opcionalmente menos uno."""
    if not clients:
        return
    data = json.dumps(message)
    # Reunimos las conexiones a notificar y mandamos en paralelo.
    targets = [ws for ws in clients if ws is not exclude]
    results = await asyncio.gather(
        *(ws.send(data) for ws in targets), return_exceptions=True
    )
    # Las conexiones que fallaron se limpian cuando su handler termine; aqui
    # solo evitamos que una excepcion corte el broadcast al resto.
    del results


def presence_message():
    return {"type": "presence", "users": len(clients)}


async def handler(websocket):
    """Atiende una conexion WebSocket de principio a fin (una corrutina por cliente)."""
    user = assign_user()
    clients[websocket] = user

    # 1) Le mandamos al recien llegado el estado actual de la pizarra.
    await websocket.send(json.dumps({
        "type": "init",
        "you": user,
        "elements": scene,
        "drafts": list(drafts.values()),
        "users": len(clients),
    }))
    # 2) Avisamos a los demas que entro alguien.
    await broadcast(presence_message(), exclude=websocket)

    print(f"[+] {user['name']} conectado. Total: {len(clients)}")

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await on_message(websocket, user, msg)
    except websockets.ConnectionClosed:
        pass
    finally:
        # Limpieza al desconectar.
        clients.pop(websocket, None)
        drafts.pop(user["id"], None)
        await broadcast({"type": "leave", "id": user["id"]})
        await broadcast(presence_message())
        print(f"[-] {user['name']} desconectado. Total: {len(clients)}")


async def on_message(websocket, user, msg):
    """Procesa un mensaje del cliente y actualiza el estado compartido."""
    mtype = msg.get("type")

    if mtype == "draft":
        # Dibujo en vivo: el elemento aun no esta confirmado.
        element = msg.get("element")
        if element is not None:
            element["owner"] = user["id"]
            drafts[user["id"]] = element
            await broadcast({"type": "draft", "element": element}, exclude=websocket)

    elif mtype == "add":
        # El usuario solto el mouse: el elemento queda confirmado en la escena.
        element = msg.get("element")
        if element is not None:
            element["id"] = next(_id_counter)
            element["owner"] = user["id"]
            scene.append(element)
            drafts.pop(user["id"], None)
            await broadcast({"type": "add", "element": element})

    elif mtype == "delete":
        eid = msg.get("id")
        before = len(scene)
        scene[:] = [el for el in scene if el.get("id") != eid]
        if len(scene) != before:
            await broadcast({"type": "delete", "id": eid})

    elif mtype == "clear":
        scene.clear()
        drafts.clear()
        await broadcast({"type": "clear"})

    elif mtype == "cursor":
        # Posicion del puntero para mostrar cursores ajenos.
        await broadcast({
            "type": "cursor",
            "id": user["id"],
            "name": user["name"],
            "color": user["color"],
            "x": msg.get("x"),
            "y": msg.get("y"),
        }, exclude=websocket)


# ----------------------------------------------------------------------------
# Servidor HTTP para el frontend estatico
# ----------------------------------------------------------------------------
class StaticHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, *args):
        pass  # silenciamos el log por peticion para no ensuciar la consola


def start_http_server():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer((HOST, HTTP_PORT), StaticHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


# ----------------------------------------------------------------------------
# Arranque
# ----------------------------------------------------------------------------
async def main():
    start_http_server()
    print("=" * 56)
    print("  Pizarra Colaborativa")
    print(f"  Frontend : http://localhost:{HTTP_PORT}")
    print(f"  WebSocket: ws://localhost:{WS_PORT}")
    print("  (Ctrl+C para detener)")
    print("=" * 56)

    stop = asyncio.Future()

    # Permitir cierre limpio con Ctrl+C tambien en Windows.
    def request_stop(*_):
        if not stop.done():
            stop.set_result(None)

    try:
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
    except (ValueError, AttributeError):
        pass

    async with websockets.serve(handler, HOST, WS_PORT):
        await stop


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    print("\nServidor detenido.")
