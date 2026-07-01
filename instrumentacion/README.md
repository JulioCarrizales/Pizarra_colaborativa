# Instrumentación programada — Concurrencia y WebSockets

Componente de software del proyecto (opción 17). Mide cómo se comporta el sistema
operativo cuando **N clientes WebSocket** se conectan a la vez a la Pizarra
Colaborativa: **descriptores de archivo (ulimit), conexiones y memoria por conexión.**

Todo lee `/proc` directamente (sin librerías mágicas), para poder explicar cada
medición en la defensa.

> ⚠️ **Corre en Linux** (usa `/proc` y `/sys`). En la VM Ubuntu o en la instancia EC2.

## Archivos

| Archivo | Qué hace |
|---|---|
| `simulador.py` | Abre **N clientes WebSocket** concurrentes (carga). |
| `medidor.py` | Lee de `/proc` los **descriptores, memoria (RSS) y conexiones** del servidor. |
| `experimento.py` | Sube la carga por niveles (50, 100, …) y **registra las métricas** en un CSV. |
| `graficar.py` | Convierte el CSV en las **gráficas** del informe. |

## Instalación

```bash
pip install -r requirements.txt
```

## Cómo correr el experimento (en Linux)

Lo más simple es medir la app corriendo **directamente** (así el medidor lee `/proc`
del proceso sin complicaciones de red del contenedor):

**Terminal 1 — la app:**
```bash
cd ..            # carpeta del proyecto
python server.py
```

**Terminal 2 — el experimento:**
```bash
cd instrumentacion
python experimento.py --niveles 50,100,200,400,800 --hold 12 --salida resultados.csv
python graficar.py --datos resultados.csv
```

Esto genera `resultados.csv` y tres figuras:
- `grafica_memoria.png` — clientes vs. memoria (RSS).
- `grafica_descriptores.png` — clientes vs. descriptores (con la línea del `ulimit -n`).
- `grafica_por_conexion.png` — memoria por conexión.

### Herramientas sueltas (para la demo en vivo)

Abrir 200 clientes durante 20 s:
```bash
python simulador.py --clientes 200 --duracion 20
```

Muestrear el servidor en vivo mientras subes carga:
```bash
python medidor.py --intervalo 1 --salida muestreo.csv
```

## El experimento de "variar límites" (cgroups / ulimit)

Para la parte de la rúbrica de *variar límites y medir*, se repite el experimento
bajo un **límite** y se compara:

- **Límite de descriptores (`ulimit -n`)**: en la Terminal 1, antes de arrancar la app:
  ```bash
  ulimit -n 1024        # bajar el límite
  python server.py
  ```
  Al subir N, el servidor empieza a **rechazar conexiones** al acercarse al límite
  (se ve en la columna `fallidos` del CSV y en la línea roja de la gráfica de descriptores).

- **Límite de memoria (cgroups, con Podman)**: correr la app en un contenedor con
  memoria limitada y observar el punto de presión:
  ```bash
  podman run -d --name pizarra-test --memory=128m -p 8000:8000 -p 8765:8765 pizarra:latest
  ```
  Luego se corre el experimento contra ese contenedor y se compara la curva de
  memoria y el número máximo de clientes soportados frente al caso sin límite.

## Mapeo a la rúbrica

| Criterio | Cómo lo cubre |
|---|---|
| **Instrumentación programada (3 pts)** | `simulador.py` + `medidor.py` + `experimento.py`: código propio que muestrea `/proc` y `/sys`. |
| **Experimento, medición y análisis (3 pts)** | Ramp de N clientes + variar `ulimit`/cgroups + `resultados.csv` + gráficas. |
| **Tema de SO: WebSockets y concurrencia** | Cada conexión = un socket = un descriptor; se mide el costo en FDs y memoria por conexión concurrente. |

## Qué se mide y por qué

- **Descriptores de archivo**: en Linux cada socket abierto es un *file descriptor*.
  Con N conexiones, el servidor mantiene ~N descriptores; el `ulimit -n` es el techo.
- **Memoria por conexión**: `(RSS con N clientes − RSS base) / N`. Indica cuánto
  cuesta en RAM cada cliente concurrente.
- **Conexiones establecidas**: se cuentan en `/proc/<pid>/net/tcp` (estado `ESTABLISHED`).
