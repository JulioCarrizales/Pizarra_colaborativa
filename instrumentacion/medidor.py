"""
Medidor de recursos del sistema operativo
Instrumentacion del proyecto - Pizarra Colaborativa (opcion 17)

Lee directamente de /proc (Linux) para medir, sobre el proceso del servidor:
  - Descriptores de archivo abiertos  -> /proc/<pid>/fd        (cada socket = 1 fd)
  - Memoria residente (RSS)           -> /proc/<pid>/status (VmRSS)
  - Conexiones TCP establecidas       -> /proc/<pid>/net/tcp[6]

No usa librerias externas a proposito: la idea es leer los mecanismos del kernel
tal como los expone el sistema operativo (util para defender el codigo en el Q&A).

Tambien puede usarse suelto para muestrear un proceso a un CSV:
  python medidor.py --pid 1234 --intervalo 1 --salida muestreo.csv
"""

import argparse
import csv
import os
import time

PROC = "/proc"


# ---------------------------------------------------------------------------
# Localizar el proceso del servidor
# ---------------------------------------------------------------------------
def buscar_pid_servidor(patron="server.py"):
    """Devuelve el PID del proceso cuyo cmdline contiene 'patron', o None."""
    if not os.path.isdir(PROC):
        return None
    for entrada in os.listdir(PROC):
        if not entrada.isdigit():
            continue
        try:
            with open(f"{PROC}/{entrada}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode(errors="ignore")
            if patron in cmd and "python" in cmd:
                return int(entrada)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return None


# ---------------------------------------------------------------------------
# Metricas individuales (todas leen de /proc)
# ---------------------------------------------------------------------------
def contar_fds(pid):
    """Numero de descriptores de archivo abiertos por el proceso."""
    try:
        return len(os.listdir(f"{PROC}/{pid}/fd"))
    except (FileNotFoundError, PermissionError):
        return None


def leer_rss_kb(pid):
    """Memoria residente (VmRSS) del proceso, en kB."""
    try:
        with open(f"{PROC}/{pid}/status") as f:
            for linea in f:
                if linea.startswith("VmRSS:"):
                    return int(linea.split()[1])
    except (FileNotFoundError, PermissionError):
        return None
    return None


def _contar_established(ruta, puerto_hex):
    total = 0
    try:
        with open(ruta) as f:
            next(f)  # cabecera
            for linea in f:
                campos = linea.split()
                if len(campos) < 4:
                    continue
                local = campos[1]            # IP:PUERTO en hex
                estado = campos[3]           # 01 = ESTABLISHED
                if estado == "01" and local.endswith(":" + puerto_hex):
                    total += 1
    except FileNotFoundError:
        return 0
    return total


def contar_conexiones(pid, puerto=8765):
    """Conexiones TCP ESTABLISHED hacia el puerto del WebSocket, en el netns del pid."""
    if pid is None:
        return None
    puerto_hex = f"{puerto:04X}"
    total = 0
    hallado = False
    for nombre in ("tcp", "tcp6"):
        ruta = f"{PROC}/{pid}/net/{nombre}"
        if os.path.exists(ruta):
            hallado = True
            total += _contar_established(ruta, puerto_hex)
    return total if hallado else None


def leer_limite_fd(pid):
    """Limite maximo de descriptores (ulimit -n / RLIMIT_NOFILE) del proceso."""
    try:
        with open(f"{PROC}/{pid}/limits") as f:
            for linea in f:
                if linea.startswith("Max open files"):
                    return linea.split()[3]  # soft limit
    except (FileNotFoundError, PermissionError):
        return None
    return None


def muestra(pid, puerto=8765):
    """Toma una muestra completa de las metricas del proceso."""
    return {
        "fds": contar_fds(pid),
        "rss_kb": leer_rss_kb(pid),
        "conexiones": contar_conexiones(pid, puerto),
        "limite_fd": leer_limite_fd(pid),
    }


# ---------------------------------------------------------------------------
# Uso suelto: muestrear un proceso a lo largo del tiempo -> CSV
# ---------------------------------------------------------------------------
def _main(args):
    pid = args.pid or buscar_pid_servidor()
    if pid is None:
        print("No se encontro el proceso del servidor (server.py).")
        print("Indica el PID con --pid, o corre esto en Linux con la app activa.")
        return
    print(f"Midiendo PID {pid} (limite fd = {leer_limite_fd(pid)}). Ctrl+C para parar.")

    with open(args.salida, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "fds", "rss_kb", "conexiones"])
        t0 = time.time()
        try:
            while True:
                m = muestra(pid, args.puerto)
                t = round(time.time() - t0, 2)
                w.writerow([t, m["fds"], m["rss_kb"], m["conexiones"]])
                f.flush()
                print(f"t={t:6.1f}s  fds={m['fds']}  rss={m['rss_kb']}kB  conex={m['conexiones']}")
                time.sleep(args.intervalo)
        except KeyboardInterrupt:
            print(f"\nGuardado en {args.salida}")


def _parse():
    p = argparse.ArgumentParser(description="Medidor de FDs/memoria/conexiones via /proc.")
    p.add_argument("--pid", type=int, default=None, help="PID del servidor (auto si se omite)")
    p.add_argument("--puerto", type=int, default=8765, help="puerto del WebSocket")
    p.add_argument("--intervalo", type=float, default=1.0, help="segundos entre muestras")
    p.add_argument("--salida", default="muestreo.csv")
    return p.parse_args()


if __name__ == "__main__":
    _main(_parse())
