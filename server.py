"""
Pizarra Colaborativa (estilo Excalidraw)
Servidor de la aplicacion - Proyecto Sistemas Operativos I (V Ciclo)

Tema de SO protagonista: WebSockets y concurrencia.

Arquitectura:
  - Servidor HTTP (libreria estandar) que sirve el frontend estatico (static/) y
    expone una pequena API JSON para cuentas y pizarras (/api/...).
  - Servidor WebSocket asincrono ('websockets') que mantiene el estado compartido
    de cada pizarra (sala/room) y reenvia (broadcast) los eventos de dibujo a los
    clientes de esa misma sala en tiempo real.
  - Persistencia de usuarios y pizarras en SQLite (libreria estandar). La escena
    (lo dibujado) vive en memoria por sala.

Cada conexion WebSocket es una corrutina, lo que mas adelante permite medir
descriptores de archivo, conexiones y memoria por conexion.
"""

import asyncio
import hashlib
import http.server
import itertools
import json
import os
import re
import secrets
import signal
import socketserver
import sqlite3
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

import websockets

# ----------------------------------------------------------------------------
# Configuracion
# ----------------------------------------------------------------------------
HOST = "0.0.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Configurable por entorno (util en el contenedor Podman). Valores por defecto
# para correr local sin variables. En el contenedor, PIZARRA_DB apunta al volumen
# montado (p.ej. /data/pizarra.db) para que los datos persistan.
HTTP_PORT = int(os.environ.get("PIZARRA_HTTP_PORT", "8000"))   # frontend + API
WS_PORT = int(os.environ.get("PIZARRA_WS_PORT", "8765"))       # colaboracion
DB_PATH = os.environ.get("PIZARRA_DB", os.path.join(BASE_DIR, "pizarra.db"))

# Paleta para identificar a cada usuario dentro de una sala (cursor y nombre).
USER_COLORS = [
    "#e03131", "#1971c2", "#2f9e44", "#f08c00",
    "#9c36b5", "#0c8599", "#e8590c", "#5f3dc4",
]
# Codigo de pizarra: sin caracteres ambiguos (0/O, 1/I/L).
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------------
# Base de datos (SQLite)
# ----------------------------------------------------------------------------
_db_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    # Crear el directorio de la base si no existe (p.ej. el volumen /data).
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY,
                username   TEXT UNIQUE NOT NULL,
                pw_hash    TEXT NOT NULL,
                pw_salt    TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS boards (
                id         INTEGER PRIMARY KEY,
                code       TEXT UNIQUE NOT NULL,
                name       TEXT NOT NULL,
                owner_id   INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memberships (
                user_id   INTEGER NOT NULL,
                board_id  INTEGER NOT NULL,
                role      TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (user_id, board_id)
            );
            """
        )


# --- contrasenas (pbkdf2, libreria estandar) ---
def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
    return h.hex(), salt


# --- operaciones de usuario ---
def create_user(username, password):
    h, salt = hash_password(password)
    with _db_lock, db() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, pw_hash, pw_salt, created_at) VALUES (?,?,?,?)",
            (username, h, salt, now_iso()),
        )
        return cur.lastrowid


def verify_user(username, password):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    if not row:
        return None
    h, _ = hash_password(password, row["pw_salt"])
    # comparacion en tiempo constante
    if secrets.compare_digest(h, row["pw_hash"]):
        return {"id": row["id"], "username": row["username"]}
    return None


# --- operaciones de pizarra ---
def gen_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def create_board(owner_id, name):
    with _db_lock, db() as conn:
        # generar un codigo unico
        while True:
            code = gen_code()
            exists = conn.execute(
                "SELECT 1 FROM boards WHERE code = ?", (code,)
            ).fetchone()
            if not exists:
                break
        cur = conn.execute(
            "INSERT INTO boards(code, name, owner_id, created_at) VALUES (?,?,?,?)",
            (code, name, owner_id, now_iso()),
        )
        board_id = cur.lastrowid
        conn.execute(
            "INSERT INTO memberships(user_id, board_id, role, joined_at) VALUES (?,?,?,?)",
            (owner_id, board_id, "owner", now_iso()),
        )
    return {"code": code, "name": name, "role": "owner"}


def get_board_by_code(code):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM boards WHERE code = ?", (code.upper(),)
        ).fetchone()
    return dict(row) if row else None


def join_board(user_id, code):
    board = get_board_by_code(code)
    if not board:
        return None
    with _db_lock, db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO memberships(user_id, board_id, role, joined_at) "
            "VALUES (?,?,?,?)",
            (user_id, board["id"], "member", now_iso()),
        )
    return {"code": board["code"], "name": board["name"], "role": "member"}


def list_boards(user_id):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT b.code, b.name, m.role
            FROM memberships m JOIN boards b ON b.id = m.board_id
            WHERE m.user_id = ?
            ORDER BY m.joined_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# Sesiones (tokens en memoria): token -> {id, username}
# ----------------------------------------------------------------------------
sessions = {}


def new_session(user):
    token = secrets.token_urlsafe(24)
    sessions[token] = user
    return token


# ----------------------------------------------------------------------------
# Estado en memoria de las salas (pizarras)
# ----------------------------------------------------------------------------
# rooms[code] = { "scene": [...], "drafts": {conn_id: el}, "clients": {ws: info} }
rooms = {}
_conn_counter = itertools.count(1)
_id_counter = itertools.count(1)


def get_room(code):
    return rooms.setdefault(code, {"scene": [], "drafts": {}, "clients": {}})


async def broadcast(room, message, exclude=None):
    """Envia un mensaje (dict) a todos los clientes de una sala."""
    clients = room["clients"]
    if not clients:
        return
    data = json.dumps(message)
    targets = [ws for ws in clients if ws is not exclude]
    await asyncio.gather(*(ws.send(data) for ws in targets), return_exceptions=True)


def presence_message(room):
    return {"type": "presence", "users": len(room["clients"])}


async def handler(websocket):
    """Atiende una conexion WebSocket: primero exige 'join', luego colabora."""
    room = None
    info = None
    try:
        # 1) El primer mensaje debe ser un 'join' con token y codigo de pizarra.
        raw = await websocket.recv()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("type") != "join":
            await websocket.send(json.dumps({"type": "error", "error": "join requerido"}))
            return

        user = sessions.get(msg.get("token"))
        if not user:
            await websocket.send(json.dumps({"type": "error", "error": "sesion invalida"}))
            return

        board = get_board_by_code(str(msg.get("code", "")))
        if not board:
            await websocket.send(json.dumps({"type": "error", "error": "pizarra no encontrada"}))
            return

        code = board["code"]
        room = get_room(code)
        n = next(_conn_counter)
        info = {
            "id": n,
            "user_id": user["id"],
            "name": user["username"],
            "color": USER_COLORS[(n - 1) % len(USER_COLORS)],
        }
        room["clients"][websocket] = info

        # 2) Estado actual de la pizarra para el recien llegado.
        await websocket.send(json.dumps({
            "type": "init",
            "you": info,
            "board": {"code": code, "name": board["name"]},
            "elements": room["scene"],
            "drafts": list(room["drafts"].values()),
            "users": len(room["clients"]),
        }))
        await broadcast(room, presence_message(room), exclude=websocket)
        print(f"[+] {info['name']} entro a '{code}'. En sala: {len(room['clients'])}")

        # 3) Bucle de eventos de dibujo.
        async for raw in websocket:
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await on_message(room, websocket, info, m)

    except websockets.ConnectionClosed:
        pass
    finally:
        if room is not None and info is not None:
            room["clients"].pop(websocket, None)
            room["drafts"].pop(info["id"], None)
            await broadcast(room, {"type": "leave", "id": info["id"]})
            await broadcast(room, presence_message(room))
            print(f"[-] {info['name']} salio. En sala: {len(room['clients'])}")
            if not room["clients"]:
                # Sala vacia: liberar memoria (la pizarra sigue en la BD).
                rooms.pop(_code_of(room), None)


def _code_of(target_room):
    for code, room in rooms.items():
        if room is target_room:
            return code
    return None


async def on_message(room, websocket, info, msg):
    """Procesa un mensaje y actualiza el estado de ESTA sala."""
    mtype = msg.get("type")

    if mtype == "draft":
        element = msg.get("element")
        if element is not None:
            element["owner"] = info["id"]
            room["drafts"][info["id"]] = element
            await broadcast(room, {"type": "draft", "element": element}, exclude=websocket)

    elif mtype == "add":
        element = msg.get("element")
        if element is not None:
            if "id" not in element:
                element["id"] = f"s{next(_id_counter)}"
            element["owner"] = info["id"]
            room["drafts"].pop(info["id"], None)
            if any(e.get("id") == element["id"] for e in room["scene"]):
                return  # idempotente (reenvio tras reconexion)
            room["scene"].append(element)
            await broadcast(room, {"type": "add", "element": element})

    elif mtype == "delete":
        eid = msg.get("id")
        before = len(room["scene"])
        room["scene"][:] = [el for el in room["scene"] if el.get("id") != eid]
        if len(room["scene"]) != before:
            await broadcast(room, {"type": "delete", "id": eid})

    elif mtype == "clear":
        room["scene"].clear()
        room["drafts"].clear()
        await broadcast(room, {"type": "clear"})

    elif mtype == "cursor":
        await broadcast(room, {
            "type": "cursor",
            "id": info["id"],
            "name": info["name"],
            "color": info["color"],
            "x": msg.get("x"),
            "y": msg.get("y"),
        }, exclude=websocket)


# ----------------------------------------------------------------------------
# Servidor HTTP: frontend estatico + API JSON
# ----------------------------------------------------------------------------
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


class AppHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def end_headers(self):
        # Evitar caches del frontend al iterar en el codigo.
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, *args):
        pass

    # --- helpers ---
    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    def _current_user(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return sessions.get(auth[7:])
        return None

    # --- rutas ---
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/boards":
            user = self._current_user()
            if not user:
                return self._send_json(401, {"error": "no autenticado"})
            return self._send_json(200, {"boards": list_boards(user["id"])})
        # cualquier otra cosa: archivo estatico
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/register":
            return self._handle_register()
        if path == "/api/login":
            return self._handle_login()
        if path == "/api/boards":
            return self._handle_create_board()
        if path == "/api/boards/join":
            return self._handle_join_board()
        return self._send_json(404, {"error": "ruta no encontrada"})

    def _handle_register(self):
        data = self._read_json()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if not USERNAME_RE.match(username):
            return self._send_json(400, {"error": "Usuario: 3-20 letras, numeros o _"})
        if len(password) < 4:
            return self._send_json(400, {"error": "La contrasena debe tener 4+ caracteres"})
        try:
            uid = create_user(username, password)
        except sqlite3.IntegrityError:
            return self._send_json(409, {"error": "Ese usuario ya existe"})
        user = {"id": uid, "username": username}
        return self._send_json(200, {"token": new_session(user), "username": username})

    def _handle_login(self):
        data = self._read_json()
        user = verify_user(str(data.get("username", "")).strip(), str(data.get("password", "")))
        if not user:
            return self._send_json(401, {"error": "Usuario o contrasena incorrectos"})
        return self._send_json(200, {"token": new_session(user), "username": user["username"]})

    def _handle_create_board(self):
        user = self._current_user()
        if not user:
            return self._send_json(401, {"error": "no autenticado"})
        name = str(self._read_json().get("name", "")).strip() or "Pizarra sin nombre"
        return self._send_json(200, {"board": create_board(user["id"], name[:40])})

    def _handle_join_board(self):
        user = self._current_user()
        if not user:
            return self._send_json(401, {"error": "no autenticado"})
        code = str(self._read_json().get("code", "")).strip().upper()
        board = join_board(user["id"], code)
        if not board:
            return self._send_json(404, {"error": "No existe una pizarra con ese codigo"})
        return self._send_json(200, {"board": board})


def start_http_server():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer((HOST, HTTP_PORT), AppHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# ----------------------------------------------------------------------------
# Arranque
# ----------------------------------------------------------------------------
async def main():
    init_db()
    start_http_server()
    print("=" * 56)
    print("  Pizarra Colaborativa")
    print(f"  Frontend : http://localhost:{HTTP_PORT}")
    print(f"  WebSocket: ws://localhost:{WS_PORT}")
    print("  (Ctrl+C para detener)")
    print("=" * 56)

    stop = asyncio.Future()

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
