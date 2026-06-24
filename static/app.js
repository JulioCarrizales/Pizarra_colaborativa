/*
 * Pizarra Colaborativa - cliente (auth + lobby + canvas/WebSocket)
 * Proyecto Sistemas Operativos I (V Ciclo)
 */

// ===========================================================================
// Estado global
// ===========================================================================
let auth = null;            // { token, username }
let currentBoard = null;    // { code, name }

// ===========================================================================
// Cliente de la API (HTTP / JSON)
// ===========================================================================
async function api(path, method = "GET", body = null) {
  const headers = { "Content-Type": "application/json" };
  if (auth) headers["Authorization"] = "Bearer " + auth.token;
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : null,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Error de servidor");
  return data;
}

// ===========================================================================
// Enrutado de vistas
// ===========================================================================
function show(view) {
  for (const el of document.querySelectorAll(".view")) el.classList.add("hidden");
  document.getElementById("view-" + view).classList.remove("hidden");
}

// ===========================================================================
// Autenticacion
// ===========================================================================
let authMode = "login";

document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    authMode = t.dataset.tab;
    document.getElementById("auth-submit").textContent =
      authMode === "login" ? "Entrar" : "Crear cuenta";
    document.getElementById("auth-error").textContent = "";
  });
});

document.getElementById("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("auth-user").value.trim();
  const password = document.getElementById("auth-pass").value;
  const err = document.getElementById("auth-error");
  err.textContent = "";
  try {
    const path = authMode === "login" ? "/api/login" : "/api/register";
    const data = await api(path, "POST", { username, password });
    setAuth({ token: data.token, username: data.username });
    goLobby();
  } catch (ex) {
    err.textContent = ex.message;
  }
});

function setAuth(a) {
  auth = a;
  localStorage.setItem("pizarra_auth", JSON.stringify(a));
}

document.getElementById("logout").addEventListener("click", () => {
  auth = null;
  localStorage.removeItem("pizarra_auth");
  show("auth");
});

// ===========================================================================
// Lobby
// ===========================================================================
async function goLobby() {
  leaveBoard();           // por si veniamos de una pizarra
  show("lobby");
  document.getElementById("lobby-user").textContent = "👤 " + auth.username;
  await refreshBoards();
}

async function refreshBoards() {
  const { boards } = await api("/api/boards");
  const list = document.getElementById("board-list");
  const empty = document.getElementById("no-boards");
  list.innerHTML = "";
  empty.classList.toggle("hidden", boards.length > 0);
  for (const b of boards) {
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="b-info">
        <span class="b-name">${escapeHtml(b.name)}</span>
        <span class="b-meta">${b.code} · ${b.role === "owner" ? "dueño" : "miembro"}</span>
      </div>
      <button class="primary small">Abrir</button>`;
    li.querySelector("button").addEventListener("click", () =>
      enterBoard({ code: b.code, name: b.name })
    );
    list.appendChild(li);
  }
}

document.getElementById("create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("create-name").value.trim();
  const { board } = await api("/api/boards", "POST", { name });
  document.getElementById("create-name").value = "";
  enterBoard({ code: board.code, name: board.name });
});

document.getElementById("join-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = document.getElementById("join-code").value.trim().toUpperCase();
  const err = document.getElementById("join-error");
  err.textContent = "";
  try {
    const { board } = await api("/api/boards/join", "POST", { code });
    document.getElementById("join-code").value = "";
    enterBoard({ code: board.code, name: board.name });
  } catch (ex) {
    err.textContent = ex.message;
  }
});

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// ===========================================================================
// PIZARRA  (canvas + WebSocket)  — todo el estado se reinicia por sala
// ===========================================================================
const canvas = document.getElementById("board");
const ctx = canvas.getContext("2d");

let elements = [];
let remoteDrafts = new Map();
let cursors = new Map();
let me = null;

let tool = "pen";
let color = "#1e1e1e";
let strokeWidth = 3;
let drawing = false;
let current = null;

let socket = null;
let wantConnection = false;   // true mientras estemos dentro de una pizarra

function enterBoard(board) {
  currentBoard = board;
  // reiniciar estado del lienzo
  elements = [];
  remoteDrafts = new Map();
  cursors = new Map();
  me = null;
  current = null;
  drawing = false;

  show("board");
  document.getElementById("board-code").textContent = board.code;
  document.getElementById("me").textContent = "Conectando…";
  resizeCanvas();

  wantConnection = true;
  connect();
}

function leaveBoard() {
  wantConnection = false;
  if (socket) {
    try { socket.close(); } catch (_) {}
    socket = null;
  }
  currentBoard = null;
}

document.getElementById("back").addEventListener("click", goLobby);

// ---------------------------------------------------------------------------
// Lienzo: tamano y alta resolucion (retina)
// ---------------------------------------------------------------------------
function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(window.innerWidth * dpr);
  canvas.height = Math.floor(window.innerHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  render();
}
window.addEventListener("resize", () => { if (currentBoard) resizeCanvas(); });

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
const WS_URL = `ws://${location.hostname}:8765`;

function connect() {
  socket = new WebSocket(WS_URL);

  socket.addEventListener("open", () => {
    // Autenticarse y unirse a la sala de esta pizarra.
    socket.send(JSON.stringify({
      type: "join",
      token: auth.token,
      code: currentBoard.code,
    }));
  });

  socket.addEventListener("message", (ev) => handleServerMessage(JSON.parse(ev.data)));

  socket.addEventListener("close", () => {
    if (wantConnection) {
      document.getElementById("me").textContent = "Reconectando…";
      setTimeout(() => { if (wantConnection) connect(); }, 1500);
    }
  });
}

function send(obj) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(obj));
  }
}

function handleServerMessage(msg) {
  switch (msg.type) {
    case "error":
      document.getElementById("me").textContent = "Error: " + msg.error;
      return;

    case "init": {
      me = msg.you;
      const incoming = msg.elements || [];
      const incomingIds = new Set(incoming.map((e) => e.id));
      const localOnly = elements.filter((e) => !incomingIds.has(e.id));
      elements = incoming.slice();
      for (const el of localOnly) {
        elements.push(el);
        send({ type: "add", element: el });
      }
      (msg.drafts || []).forEach((el) => remoteDrafts.set(el.owner, el));
      setMe(me.name);
      setPresence(msg.users);
      paintMe();
      render();
      break;
    }

    case "add":
      if (!elements.some((e) => e.id === msg.element.id)) elements.push(msg.element);
      remoteDrafts.delete(msg.element.owner);
      render();
      break;

    case "draft":
      remoteDrafts.set(msg.element.owner, msg.element);
      render();
      break;

    case "delete":
      elements = elements.filter((el) => el.id !== msg.id);
      render();
      break;

    case "clear":
      elements = [];
      remoteDrafts.clear();
      render();
      break;

    case "cursor":
      cursors.set(msg.id, { x: msg.x, y: msg.y, name: msg.name, color: msg.color });
      render();
      break;

    case "leave":
      cursors.delete(msg.id);
      remoteDrafts.delete(msg.id);
      render();
      break;

    case "presence":
      setPresence(msg.users);
      break;
  }
}

// ---------------------------------------------------------------------------
// Dibujo de elementos
// ---------------------------------------------------------------------------
function drawElement(el) {
  ctx.strokeStyle = el.color;
  ctx.fillStyle = el.color;
  ctx.lineWidth = el.strokeWidth;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  switch (el.type) {
    case "pen": {
      const pts = el.points;
      if (!pts || pts.length === 0) return;
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.stroke();
      break;
    }
    case "rect":
      ctx.strokeRect(el.x, el.y, el.w, el.h);
      break;
    case "ellipse":
      ctx.beginPath();
      ctx.ellipse(
        el.x + el.w / 2, el.y + el.h / 2,
        Math.abs(el.w / 2), Math.abs(el.h / 2),
        0, 0, Math.PI * 2
      );
      ctx.stroke();
      break;
    case "line":
      ctx.beginPath();
      ctx.moveTo(el.x1, el.y1);
      ctx.lineTo(el.x2, el.y2);
      ctx.stroke();
      break;
    case "arrow":
      drawArrow(el.x1, el.y1, el.x2, el.y2, el.strokeWidth);
      break;
    case "text":
      ctx.font = `${14 + el.strokeWidth * 3}px "Segoe UI", sans-serif`;
      ctx.textBaseline = "top";
      ctx.fillText(el.text, el.x, el.y);
      break;
  }
}

function drawArrow(x1, y1, x2, y2, w) {
  const head = 8 + w * 2;
  const angle = Math.atan2(y2 - y1, x2 - x1);
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - head * Math.cos(angle - Math.PI / 6), y2 - head * Math.sin(angle - Math.PI / 6));
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - head * Math.cos(angle + Math.PI / 6), y2 - head * Math.sin(angle + Math.PI / 6));
  ctx.stroke();
}

function render() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (const el of elements) drawElement(el);
  if (current) drawElement(current);
  for (const el of remoteDrafts.values()) drawElement(el);
  for (const c of cursors.values()) drawCursor(c);
}

function drawCursor(c) {
  if (c.x == null) return;
  ctx.fillStyle = c.color;
  ctx.beginPath();
  ctx.arc(c.x, c.y, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = '12px "Segoe UI", sans-serif';
  ctx.fillText(c.name, c.x + 9, c.y + 4);
}

// ---------------------------------------------------------------------------
// Interaccion con el mouse
// ---------------------------------------------------------------------------
function pos(ev) {
  const r = canvas.getBoundingClientRect();
  return { x: ev.clientX - r.left, y: ev.clientY - r.top };
}

canvas.addEventListener("mousedown", (ev) => {
  const p = pos(ev);

  if (tool === "eraser") {
    const hit = hitTest(p.x, p.y);
    if (hit) {
      elements = elements.filter((e) => e.id !== hit.id);
      render();
      send({ type: "delete", id: hit.id });
    }
    return;
  }

  if (tool === "text") {
    const text = prompt("Texto:");
    if (text) {
      const el = base("text");
      el.x = p.x; el.y = p.y; el.text = text;
      elements.push(el);
      render();
      send({ type: "add", element: el });
    }
    return;
  }

  if (tool === "select") return;

  drawing = true;
  current = base(tool);
  if (tool === "pen") {
    current.points = [[p.x, p.y]];
  } else if (tool === "line" || tool === "arrow") {
    current.x1 = p.x; current.y1 = p.y; current.x2 = p.x; current.y2 = p.y;
  } else {
    current.x = p.x; current.y = p.y; current.w = 0; current.h = 0;
  }
});

canvas.addEventListener("mousemove", (ev) => {
  const p = pos(ev);
  throttleCursor(p);
  if (!drawing || !current) return;

  if (current.type === "pen") {
    current.points.push([p.x, p.y]);
  } else if (current.type === "line" || current.type === "arrow") {
    current.x2 = p.x; current.y2 = p.y;
  } else {
    current.w = p.x - current.x;
    current.h = p.y - current.y;
  }
  send({ type: "draft", element: current });
  render();
});

window.addEventListener("mouseup", () => {
  if (!drawing || !current) return;
  drawing = false;
  if (!isMeaningful(current)) { current = null; render(); return; }
  elements.push(current);
  send({ type: "add", element: current });
  current = null;
  render();
});

let _elementSeq = 0;
function newId() {
  return `${me ? me.id : "x"}-${Date.now().toString(36)}-${(_elementSeq++).toString(36)}`;
}

function base(type) {
  return { id: newId(), type, color, strokeWidth, owner: me ? me.id : null };
}

function isMeaningful(el) {
  if (el.type === "pen") return el.points.length > 1;
  if (el.type === "line" || el.type === "arrow")
    return Math.hypot(el.x2 - el.x1, el.y2 - el.y1) > 2;
  return Math.abs(el.w) > 2 || Math.abs(el.h) > 2;
}

function hitTest(x, y) {
  const tol = 8;
  for (let i = elements.length - 1; i >= 0; i--) {
    const el = elements[i];
    if (el.type === "pen") {
      if (el.points.some((p) => Math.hypot(p[0] - x, p[1] - y) < tol)) return el;
    } else if (el.type === "line" || el.type === "arrow") {
      if (distToSegment(x, y, el.x1, el.y1, el.x2, el.y2) < tol) return el;
    } else if (el.type === "text") {
      if (x >= el.x - tol && x <= el.x + 120 && y >= el.y - tol && y <= el.y + 30) return el;
    } else {
      const x1 = Math.min(el.x, el.x + el.w), x2 = Math.max(el.x, el.x + el.w);
      const y1 = Math.min(el.y, el.y + el.h), y2 = Math.max(el.y, el.y + el.h);
      if (x >= x1 - tol && x <= x2 + tol && y >= y1 - tol && y <= y2 + tol) return el;
    }
  }
  return null;
}

function distToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const len2 = dx * dx + dy * dy || 1;
  let t = ((px - x1) * dx + (py - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

let lastCursorSent = 0;
function throttleCursor(p) {
  const now = performance.now();
  if (now - lastCursorSent > 33) {
    lastCursorSent = now;
    send({ type: "cursor", x: p.x, y: p.y });
  }
}

// ---------------------------------------------------------------------------
// Barra de herramientas
// ---------------------------------------------------------------------------
document.querySelectorAll(".tool").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tool").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    tool = btn.dataset.tool;
    canvas.style.cursor = tool === "eraser" ? "pointer" : "crosshair";
  });
});

const colorInput = document.getElementById("color");
colorInput.addEventListener("input", (e) => { color = e.target.value; syncPaletteActive(); });

const PALETTE = ["#1e1e1e", "#e03131", "#1971c2", "#2f9e44", "#f08c00", "#9c36b5"];
const paletteEl = document.getElementById("palette");
PALETTE.forEach((c) => {
  const dot = document.createElement("div");
  dot.className = "dot";
  dot.style.background = c;
  dot.dataset.color = c;
  dot.addEventListener("click", () => {
    color = c; colorInput.value = c; syncPaletteActive();
  });
  paletteEl.appendChild(dot);
});
function syncPaletteActive() {
  document.querySelectorAll(".palette .dot").forEach((d) =>
    d.classList.toggle("active", d.dataset.color === color));
}
syncPaletteActive();

document.getElementById("stroke").addEventListener("input", (e) => {
  strokeWidth = Number(e.target.value);
});

document.getElementById("clear").addEventListener("click", () => {
  if (confirm("¿Limpiar la pizarra para todos?")) {
    elements = [];
    remoteDrafts.clear();
    current = null;
    render();
    send({ type: "clear" });
  }
});

// Compartir codigo de la pizarra (copiar al portapapeles).
document.getElementById("share").addEventListener("click", async () => {
  if (!currentBoard) return;
  try {
    await navigator.clipboard.writeText(currentBoard.code);
    const el = document.getElementById("board-code");
    const original = el.textContent;
    el.textContent = "¡Copiado!";
    setTimeout(() => { el.textContent = original; }, 1200);
  } catch (_) {
    alert("Código de la pizarra: " + currentBoard.code);
  }
});

// ---------------------------------------------------------------------------
// Indicadores de estado
// ---------------------------------------------------------------------------
function setPresence(n) { document.getElementById("presence").textContent = `● ${n}`; }
function setMe(text) { document.getElementById("me").textContent = text; }
function paintMe() { if (me) document.getElementById("me").style.color = me.color; }

// ===========================================================================
// Arranque: si hay sesion guardada, ir al lobby; si no, a login.
// ===========================================================================
(function boot() {
  const saved = localStorage.getItem("pizarra_auth");
  if (saved) {
    try {
      auth = JSON.parse(saved);
      goLobby().catch(() => { auth = null; show("auth"); });
      return;
    } catch (_) {}
  }
  show("auth");
})();
