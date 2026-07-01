"""
Experimento de concurrencia
Instrumentacion del proyecto - Pizarra Colaborativa (opcion 17)
Tema de SO: WebSockets y concurrencia.

Sube la carga por NIVELES de clientes (p.ej. 50, 100, 200, 400, 800) y, en cada
nivel, mide sobre el proceso del servidor:
  - descriptores de archivo abiertos (fds)
  - memoria residente (RSS, kB)
  - conexiones TCP establecidas
Calcula ademas la MEMORIA POR CONEXION y los FDs por conexion, y guarda todo en
un CSV. Con esos datos, graficar.py genera las figuras del informe.

Requisitos:
  - Correr en Linux, con la app de la pizarra ejecutandose (para leer /proc).
  - Lo mas simple para el experimento: en la maquina Linux, en una terminal
    'python server.py' (la app), y en otra terminal este experimento.

Uso tipico:
  python experimento.py --niveles 50,100,200,400,800 --hold 12 --salida resultados.csv
"""

import argparse
import asyncio
import csv
import time

import medidor
import simulador


async def correr(args):
    base_http = f"http://{args.host}:{args.http_port}"
    ws_url = f"ws://{args.host}:{args.ws_port}"
    niveles = [int(x) for x in args.niveles.split(",") if x.strip()]

    # 1) Localizar el proceso del servidor para medirlo.
    pid = args.pid or medidor.buscar_pid_servidor()
    if pid is None:
        print("ERROR: no se encontro el proceso 'server.py'.")
        print("Corre la app (python server.py) en esta maquina Linux, o pasa --pid.")
        return
    limite_fd = medidor.leer_limite_fd(pid)
    print(f"Servidor: PID {pid}  |  limite de descriptores (ulimit -n) = {limite_fd}")

    # 2) Token y sala compartidos por todos los clientes.
    print(f"Preparando token y sala en {base_http} ...")
    token, code = simulador.preparar(base_http)
    print(f"  sala = {code}")

    # 3) Linea base (0 clientes) para descontar el consumo del servidor vacio.
    time.sleep(1)
    base = medidor.muestra(pid, args.ws_port)
    print(f"Base (0 clientes): fds={base['fds']}  rss={base['rss_kb']}kB")

    stop = asyncio.Event()
    contadores = {"conectados": 0, "fallidos": 0, "ultimo_error": None}
    tareas = []
    filas = []
    abiertos = 0

    try:
        for n in niveles:
            # Abrir los clientes que faltan para llegar al nivel n.
            faltan = max(0, n - abiertos)
            tareas += await simulador.abrir_clientes(
                ws_url, token, code, faltan, args.rate, stop, contadores)
            abiertos = n

            # Esperar a que se estabilice (conexion + memoria).
            print(f"\n>> Nivel {n} clientes: estabilizando {args.hold}s...")
            await asyncio.sleep(args.hold)

            m = medidor.muestra(pid, args.ws_port)
            conectados = contadores["conectados"] - contadores["fallidos"]
            mem_extra = (m["rss_kb"] - base["rss_kb"]) if m["rss_kb"] and base["rss_kb"] else None
            fd_extra = (m["fds"] - base["fds"]) if m["fds"] and base["fds"] else None
            mem_por_conexion = round(mem_extra / n, 2) if mem_extra is not None and n else None
            fd_por_conexion = round(fd_extra / n, 3) if fd_extra is not None and n else None

            fila = {
                "clientes": n,
                "conectados": conectados,
                "fallidos": contadores["fallidos"],
                "fds": m["fds"],
                "rss_kb": m["rss_kb"],
                "conexiones": m["conexiones"],
                "rss_extra_kb": mem_extra,
                "kb_por_conexion": mem_por_conexion,
                "fd_por_conexion": fd_por_conexion,
                "limite_fd": limite_fd,
            }
            filas.append(fila)
            print(f"   fds={m['fds']}  rss={m['rss_kb']}kB  conex={m['conexiones']}"
                  f"  mem/conex={mem_por_conexion}kB  fallidos={contadores['fallidos']}")
    finally:
        stop.set()
        if tareas:
            await asyncio.gather(*tareas, return_exceptions=True)

    # 4) Guardar resultados.
    campos = ["clientes", "conectados", "fallidos", "fds", "rss_kb", "conexiones",
              "rss_extra_kb", "kb_por_conexion", "fd_por_conexion", "limite_fd"]
    with open(args.salida, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(filas)
    print(f"\nResultados guardados en {args.salida}")
    print("Ahora genera las graficas:  python graficar.py --datos " + args.salida)


def _parse():
    p = argparse.ArgumentParser(description="Experimento de concurrencia (ramp de N clientes).")
    p.add_argument("--host", default="localhost")
    p.add_argument("--http-port", type=int, default=8000)
    p.add_argument("--ws-port", type=int, default=8765)
    p.add_argument("--niveles", default="50,100,200,400,800",
                   help="lista de niveles de clientes separados por coma")
    p.add_argument("--hold", type=float, default=12, help="segundos de estabilizacion por nivel")
    p.add_argument("--rate", type=float, default=0.0,
                   help="trazos/seg por cliente (0 = solo mantener conexion)")
    p.add_argument("--pid", type=int, default=None, help="PID del servidor (auto si se omite)")
    p.add_argument("--salida", default="resultados.csv")
    return p.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(correr(_parse()))
    except KeyboardInterrupt:
        pass
