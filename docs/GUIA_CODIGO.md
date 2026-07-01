# Guía del código — `server.py` y componentes

Guía para **entender y defender** el código del proyecto (Pizarra Colaborativa,
opción 17). Tema de SO: **WebSockets y concurrencia**.

## 1. Visión general

`server.py` es **un solo proceso** que levanta **dos servidores** a la vez:

| Servidor | Puerto | Para qué | Tecnología |
|---|---|---|---|
| **HTTP** | 8000 | Sirve el frontend (`static/`) y la API JSON (`/api/...`) de cuentas y pizarras | `http.server` (librería estándar), en un **hilo** |
| **WebSocket** | 8765 | Colaboración en tiempo real: reenvía los dibujos a todos los de la sala | librería `websockets` (asyncio), en el **event loop** |

Los **datos** (usuarios, pizarras y dibujos) se guardan en **SQLite**. El **estado
en vivo** de cada sala (quién está conectado, dibujos en curso) vive **en memoria**.

**Idea clave para la defensa:** cada conexión WebSocket es una **corrutina** de
asyncio; el servidor maneja **muchas conexiones concurrentes** en un solo hilo,
sin crear un hilo por cliente. Cada conexión abierta consume **un descriptor de
archivo** y algo de **memoria** — que es justo lo que mide la instrumentación.

---

## 2. Configuración (líneas ~37-57)

```python
HTTP_PORT = int(os.environ.get("PIZARRA_HTTP_PORT", "8000"))
WS_PORT   = int(os.environ.get("PIZARRA_WS_PORT", "8765"))
DB_PATH   = os.environ.get("PIZARRA_DB", os.path.join(BASE_DIR, "pizarra.db"))
```

Los puertos y la ruta de la base se leen de **variables de entorno**, con valores
por defecto para correr en local. En el contenedor Podman, `PIZARRA_DB` apunta al
**volumen** (`/data/pizarra.db`) para que los datos persistan.

---

## 3. Base de datos SQLite (líneas ~64-203)

Se usa **SQLite** (librería estándar, sin servidor de BD aparte). Cuatro tablas:

- **`users`** — cuentas: `username` único, `pw_hash`, `pw_salt`.
- **`boards`** — pizarras: `code` (el código de 6 letras), `name`, `owner_id`.
- **`memberships`** — qué usuario pertenece a qué pizarra (dueño o miembro).
- **`board_scenes`** — los **dibujos** de cada pizarra, como JSON (`scene_json`).

### Contraseñas seguras
```python
def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
    return h.hex(), salt
```
Nunca se guarda la contraseña en texto plano. Se guarda un **hash PBKDF2-SHA256**
con **salt** aleatorio y 100 000 iteraciones. Al iniciar sesión se compara con
`secrets.compare_digest` (comparación en **tiempo constante**, evita filtrar
información por el tiempo de respuesta).

> **Q&A:** *¿Por qué salt?* Para que dos usuarios con la misma contraseña tengan
> hashes distintos y no se puedan usar tablas precalculadas (rainbow tables).

---

## 4. Sesiones / tokens (líneas ~206-215)

```python
sessions = {}   # token -> {id, username}
def new_session(user):
    token = secrets.token_urlsafe(24)
    sessions[token] = user
    return token
```
Al hacer login se genera un **token aleatorio** que el cliente guarda y envía en
cada petición (cabecera `Authorization: Bearer <token>`) y en el `join` del WebSocket.

> **Limitación conocida (para la defensa):** las sesiones viven **en memoria**, así
> que al reiniciar el servidor todos deben volver a iniciar sesión. Las cuentas y
> los dibujos sí persisten (están en SQLite).

---

## 5. Estado de las salas en memoria (líneas ~218-258)

```python
rooms = {}   # code -> { "scene": [...], "drafts": {...}, "clients": {ws: info} }
```
Cada sala (pizarra abierta) tiene:
- **`scene`**: lista de elementos confirmados (los dibujos).
- **`drafts`**: lo que cada usuario está dibujando en este momento (trazo en curso).
- **`clients`**: diccionario `websocket -> info del usuario` (los conectados).

### Persistencia de los dibujos
```python
def get_room(code):
    room = rooms.get(code)
    if room is None:                       # sala no está en memoria:
        room = {"code": code, "scene": load_scene(code), ...}  # se carga de la BD
        rooms[code] = room
    return room
```
Cuando alguien abre una pizarra, si no está en memoria se **carga su escena desde
SQLite** (`load_scene`). Cada vez que se dibuja/borra, se **guarda** (`save_scene`).
Por eso los dibujos sobreviven a cerrar la pizarra o reiniciar el contenedor.

---

## 6. El corazón: el servidor WebSocket (líneas ~261-392)

### `broadcast` — reenviar a todos los de la sala
```python
async def broadcast(room, message, exclude=None):
    targets = [ws for ws in room["clients"] if ws is not exclude]
    await asyncio.gather(*(ws.send(data) for ws in targets), return_exceptions=True)
```
Envía un mensaje a **todos los clientes de la sala** a la vez (`asyncio.gather`),
opcionalmente excluyendo a uno (p.ej. al que originó el evento).

### `handler` — una corrutina por cada conexión
Es la función que atiende **cada** conexión WebSocket, de principio a fin:

1. **Autenticación (`join`):** el primer mensaje debe traer el `token` y el `code`
   de la pizarra. Se valida el token (`sessions`) y que la pizarra exista.
2. **Registro en la sala:** se le asigna un id, color y nombre, y se agrega a
   `room["clients"]`.
3. **Estado inicial (`init`):** se le envía la escena actual (los dibujos ya hechos)
   y el número de conectados. Se avisa a los demás que entró (`presence`).
4. **Bucle de eventos:** `async for raw in websocket:` escucha los mensajes del
   cliente y llama a `on_message`.
5. **Limpieza (`finally`):** al desconectarse, se quita de la sala y se avisa a los
   demás (`leave`). Si la sala queda vacía, se libera de memoria (los dibujos ya
   están en la BD).

> **Q&A:** *¿Por qué es concurrente sin hilos?* Porque `websockets` corre sobre
> **asyncio**: cada conexión es una corrutina que "cede" el control mientras espera
> datos, así un solo hilo atiende cientos de conexiones a la vez.

### `on_message` — qué hacer con cada evento
Según el `type` del mensaje:
- **`draft`**: trazo en curso → se guarda en `drafts` y se reenvía a los demás.
- **`add`**: trazo confirmado → se agrega a `scene`, **se persiste** (`save_scene`)
  y se hace broadcast. Es **idempotente**: si el elemento ya existe (por un reenvío
  tras reconexión), no se duplica.
- **`delete` / `clear`**: borra elemento(s), persiste y hace broadcast.
- **`cursor`**: posición del puntero → se reenvía a los demás (para ver cursores).

---

## 7. El servidor HTTP + API (líneas ~395-504)

`AppHandler` extiende `SimpleHTTPRequestHandler`:
- Sirve los archivos de `static/` (el frontend).
- Añade cabecera `Cache-Control: no-cache` para que el navegador siempre cargue lo último.
- Expone la **API JSON**:

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/api/register` | Crea usuario, devuelve token |
| POST | `/api/login` | Verifica credenciales, devuelve token |
| POST | `/api/boards` | Crea una pizarra (requiere token) |
| POST | `/api/boards/join` | Se une a una pizarra por código |
| GET | `/api/boards` | Lista las pizarras del usuario |

`_current_user()` lee el token de la cabecera `Authorization` y valida la sesión.

Corre en un **hilo aparte** (`ThreadingTCPServer`) para no bloquear el event loop
del WebSocket.

---

## 8. Arranque (líneas ~507-541)

```python
async def main():
    init_db()                 # crea las tablas si no existen
    start_http_server()       # HTTP en un hilo de fondo
    async with websockets.serve(handler, HOST, WS_PORT):
        await stop            # WebSocket en el event loop; espera hasta Ctrl+C
```
`asyncio.run(main())` arranca todo. El HTTP va en un hilo; el WebSocket vive en el
event loop de asyncio. Se cierra limpio con Ctrl+C (señales SIGINT/SIGTERM).

---

## 9. Protocolo de mensajes (JSON)

| Tipo | Dirección | Descripción |
|---|---|---|
| `join` | cliente→servidor | Autenticarse y unirse a una sala (token + code) |
| `init` | servidor→cliente | Estado inicial (dibujos + conectados) |
| `draft` | ambos | Trazo en curso (en vivo) |
| `add` | ambos | Trazo confirmado |
| `delete` / `clear` | ambos | Borrar uno / limpiar todo |
| `cursor` | ambos | Posición del puntero |
| `presence` / `leave` | servidor→cliente | Nº de conectados / alguien salió |

---

## 10. El frontend (`static/app.js`) en breve

- **Vistas:** login → lobby → pizarra.
- **Canvas:** dibuja con eventos de mouse; cada trazo se envía por WebSocket.
- **Optimista:** el dibujo aparece al instante en la propia pantalla (no espera al
  servidor) y se sincroniza con los demás.
- **wss en producción:** si la página es HTTPS, el WebSocket usa `wss://dominio/ws`
  (que Caddy enruta al 8765).

---

## 11. Preguntas típicas del Q&A (y respuestas)

- **¿Por qué WebSocket y no HTTP normal?** HTTP es pregunta-respuesta y se cierra;
  el servidor no puede avisar solo. El WebSocket mantiene un **canal abierto de
  doble vía**, ideal para reenviar dibujos en tiempo real.
- **¿Cómo maneja la concurrencia?** Con **asyncio**: una corrutina por conexión,
  un solo hilo. No hay un hilo por cliente.
- **¿Qué recurso del SO consume cada conexión?** Un **descriptor de archivo** (el
  socket) y memoria. Lo medimos en `/proc/[pid]/fd` y `/proc/[pid]/status`.
- **¿Qué pasa si baja el `ulimit -n`?** El servidor no puede abrir más sockets y
  **rechaza conexiones** al llegar al límite (lo demostramos en el experimento).
- **¿Dónde se guardan los dibujos?** En SQLite (`board_scenes`), dentro del volumen
  de Podman, así persisten aunque se reinicie el contenedor.
