"""
Graficador de resultados
Instrumentacion del proyecto - Pizarra Colaborativa (opcion 17)

Lee el CSV que produce experimento.py y genera las figuras para el informe:
  - grafica_memoria.png     : clientes vs memoria (RSS)
  - grafica_descriptores.png: clientes vs descriptores de archivo (con la linea del ulimit)
  - grafica_por_conexion.png: memoria por conexion segun el nivel de carga

Uso:  python graficar.py --datos resultados.csv
"""

import argparse
import csv

import matplotlib
matplotlib.use("Agg")  # sin ventana, guarda a archivo
import matplotlib.pyplot as plt


def leer(datos):
    filas = []
    with open(datos, newline="") as f:
        for r in csv.DictReader(f):
            filas.append(r)
    return filas


def _col(filas, nombre, tipo=float):
    vals = []
    for r in filas:
        v = r.get(nombre, "")
        vals.append(tipo(v) if v not in ("", "None", None) else None)
    return vals


def graficar(args):
    filas = leer(args.datos)
    clientes = _col(filas, "clientes", int)

    # 1) Memoria (RSS) vs clientes
    rss_mb = [(v / 1024) if v is not None else None for v in _col(filas, "rss_kb")]
    plt.figure(figsize=(7, 4.2))
    plt.plot(clientes, rss_mb, marker="o", color="#1971c2")
    plt.title("Memoria del servidor vs. clientes concurrentes")
    plt.xlabel("Clientes WebSocket concurrentes")
    plt.ylabel("Memoria residente RSS (MB)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("grafica_memoria.png", dpi=130)
    plt.close()

    # 2) Descriptores de archivo vs clientes (+ linea del limite ulimit)
    fds = _col(filas, "fds")
    plt.figure(figsize=(7, 4.2))
    plt.plot(clientes, fds, marker="s", color="#2f9e44", label="Descriptores abiertos")
    limites = _col(filas, "limite_fd")
    lim = next((v for v in limites if v is not None), None)
    if lim is not None:
        plt.axhline(lim, color="#e03131", linestyle="--", label=f"Limite ulimit -n = {int(lim)}")
    plt.title("Descriptores de archivo vs. clientes concurrentes")
    plt.xlabel("Clientes WebSocket concurrentes")
    plt.ylabel("Descriptores de archivo abiertos")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("grafica_descriptores.png", dpi=130)
    plt.close()

    # 3) Memoria por conexion
    por_con = _col(filas, "kb_por_conexion")
    plt.figure(figsize=(7, 4.2))
    plt.bar([str(c) for c in clientes], por_con, color="#9c36b5")
    plt.title("Memoria por conexion segun el nivel de carga")
    plt.xlabel("Clientes WebSocket concurrentes")
    plt.ylabel("Memoria por conexion (kB)")
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig("grafica_por_conexion.png", dpi=130)
    plt.close()

    print("Figuras generadas:")
    print("  grafica_memoria.png")
    print("  grafica_descriptores.png")
    print("  grafica_por_conexion.png")

    # 4) (Opcional) Comparacion sin limite vs con limite: conexiones establecidas.
    if args.comparar:
        filas_lim = leer(args.comparar)
        objetivo = _col(filas, "clientes", int)
        conex_a = _col(filas, "conexiones")
        conex_b = _col(filas_lim, "conexiones")
        lim_b = next((v for v in _col(filas_lim, "limite_fd") if v is not None), None)
        plt.figure(figsize=(7, 4.2))
        plt.plot(objetivo, objetivo, color="#adb5bd", linestyle=":", label="Ideal (1:1)")
        plt.plot(objetivo, conex_a, marker="o", color="#2f9e44", label="Sin limite (ulimit 1024)")
        plt.plot(objetivo, conex_b, marker="s", color="#e03131",
                 label=f"Con limite (ulimit {int(lim_b) if lim_b else '?'})")
        plt.title("Conexiones establecidas segun el limite de descriptores")
        plt.xlabel("Clientes solicitados")
        plt.ylabel("Conexiones establecidas (medidas en el servidor)")
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig("grafica_comparacion.png", dpi=130)
        plt.close()
        print("  grafica_comparacion.png")


def _parse():
    p = argparse.ArgumentParser(description="Genera graficas del experimento.")
    p.add_argument("--datos", default="resultados.csv")
    p.add_argument("--comparar", default=None,
                   help="CSV del escenario con limite, para la grafica comparativa")
    return p.parse_args()


if __name__ == "__main__":
    graficar(_parse())
