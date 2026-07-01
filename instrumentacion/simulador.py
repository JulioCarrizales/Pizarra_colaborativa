"""
Simulador de N clientes WebSocket
Instrumentacion del proyecto - Pizarra Colaborativa (opcion 17)
Tema de SO: WebSockets y concurrencia.

Abre N conexiones WebSocket concurrentes contra el servidor de la pizarra, cada
una autenticada (token) y unida a una misma sala. Sirve para generar carga y
medir la concurrencia (descriptores de archivo, conexiones y memoria por conexion).

Se puede usar de dos formas:
  1) Como herramienta suelta:   python simulador.py --clientes 200 --duracion 20
  2) Como libreria, importando run_client / preparar (lo usa experimento.py).
"""

import argparse
import asyncio
import json
import random
import string
import urllib.error
import urllib.request

import websockets


# ---------------------------------------------------------------------------
# Preparacion: obtener un token y una sala usando la API HTTP de la app
# ---------------------------------------------------------------------------
def _post(base_http, path, body, token=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(base_http + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def preparar(base_http):
    """Registra un usuario de prueba y crea una pizarra. Devuelve (token, code)."""
    user = "bot_" + "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    st, reg = _post(base_http, "/api/register", {"username": user, "password": "test1234"})
    if st != 200:
        raise RuntimeError(f"Registro fallo ({st}): {reg}")
    token = reg["token"]
    st, cb = _post(base_http, "/api/boards", {"name": "Prueba de carga"}, token)
    if st != 200:
        raise RuntimeError(f"Crear pizarra fallo ({st}): {cb}")
    return token, cb["board"]["code"]


# ---------------------------------------------------------------------------
# Un cliente simulado
# ---------------------------------------------------------------------------
def _elemento_pen():
    """Genera un trazo de lapiz pequeno para simular actividad de dibujo."""
    x, y = random.randint(0, 800), random.randint(0, 600)
    pts = [[x + i * 3, y + random.randint(-4, 4)] for i in range(5)]
    return {
        "id": "b" + "".join(random.choice("0123456789abcdef") for _ in range(10)),
        "type": "pen", "points": pts, "color": "#1971c2", "strokeWidth": 2,
    }


async def run_client(ws_url, token, code, rate, stop, contadores):
    """
    Un cliente: conecta, se une a la sala y mantiene la conexion hasta 'stop'.
    Si rate > 0, ademas envia 'rate' trazos por segundo (actividad de dibujo).
    'contadores' es un dict compartido para llevar la cuenta de conexiones.
    """
    try:
        async with websockets.connect(ws_url, open_timeout=20, close_timeout=5,
                                      ping_interval=20, max_queue=8) as ws:
            await ws.send(json.dumps({"type": "join", "token": token, "code": code}))
            contadores["conectados"] += 1

            # Leer (y descartar) lo que el servidor envie, sin bloquear el envio.
            async def drenar():
                try:
                    async for _ in ws:
                        pass
                except Exception:
                    pass

            lector = asyncio.create_task(drenar())
            try:
                while not stop.is_set():
                    if rate > 0:
                        await ws.send(json.dumps({"type": "add", "element": _elemento_pen()}))
                        await asyncio.sleep(1.0 / rate)
                    else:
                        await asyncio.sleep(0.5)
            finally:
                lector.cancel()
        return True
    except Exception as e:
        contadores["fallidos"] += 1
        contadores["ultimo_error"] = repr(e)
        return e


async def abrir_clientes(ws_url, token, code, n, rate, stop, contadores):
    """Lanza n clientes concurrentes y devuelve sus tareas."""
    return [
        asyncio.create_task(run_client(ws_url, token, code, rate, stop, contadores))
        for _ in range(n)
    ]


# ---------------------------------------------------------------------------
# Uso como herramienta suelta
# ---------------------------------------------------------------------------
async def _main(args):
    base_http = f"http://{args.host}:{args.http_port}"
    ws_url = f"ws://{args.host}:{args.ws_port}"

    print(f"Preparando token y sala en {base_http} ...")
    token, code = preparar(base_http)
    print(f"  token OK, sala = {code}")

    stop = asyncio.Event()
    contadores = {"conectados": 0, "fallidos": 0, "ultimo_error": None}

    print(f"Abriendo {args.clientes} clientes contra {ws_url} (rate={args.rate}/s)...")
    tareas = await abrir_clientes(ws_url, token, code, args.clientes, args.rate, stop, contadores)

    await asyncio.sleep(args.duracion)
    print(f"  conectados={contadores['conectados']}  fallidos={contadores['fallidos']}")
    if contadores["ultimo_error"]:
        print(f"  ultimo error: {contadores['ultimo_error']}")

    stop.set()
    await asyncio.gather(*tareas, return_exceptions=True)
    print("Listo.")


def _parse():
    p = argparse.ArgumentParser(description="Simulador de N clientes WebSocket.")
    p.add_argument("--host", default="localhost")
    p.add_argument("--http-port", type=int, default=8000)
    p.add_argument("--ws-port", type=int, default=8765)
    p.add_argument("--clientes", type=int, default=100, help="numero de clientes")
    p.add_argument("--duracion", type=int, default=15, help="segundos que se mantienen")
    p.add_argument("--rate", type=float, default=0.0,
                   help="trazos por segundo por cliente (0 = solo mantener conexion)")
    return p.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(_main(_parse()))
    except KeyboardInterrupt:
        pass
