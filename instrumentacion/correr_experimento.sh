#!/usr/bin/env bash
#
# correr_experimento.sh - Corre el experimento de concurrencia COMPLETO.
# Levanta la app, sube N clientes por niveles y mide FDs/memoria/conexiones,
# en DOS escenarios (sin limite y con limite bajo de descriptores), y grafica.
#
# Uso (en Linux, dentro del repo):
#   bash instrumentacion/correr_experimento.sh
#
# No toca el despliegue de Podman (usa los puertos 8000/8765 del host, que estan
# libres porque el pod publica 80/443).

set -u
cd "$(dirname "$0")/.."          # raiz del proyecto
INSTR="instrumentacion"

NIVELES="50,100,200,300,500"
HOLD=12

echo "== Instalando dependencias =="
pip install -q -r "$INSTR/requirements.txt" 2>/dev/null \
  || pip install -q --break-system-packages -r "$INSTR/requirements.txt" 2>/dev/null \
  || { echo "Falta pip. Corre:  sudo apt install -y python3-pip"; exit 1; }

correr() {
  local etiqueta="$1" limite="$2" salida="$3"
  echo
  echo "==================================================="
  echo " Escenario: $etiqueta   (ulimit -n = $limite)"
  echo "==================================================="
  rm -f pizarra.db
  ( ulimit -n "$limite"; exec python3 server.py ) >"/tmp/app_${etiqueta}.log" 2>&1 &
  local pid=$!
  sleep 3
  python3 "$INSTR/experimento.py" --niveles "$NIVELES" --hold "$HOLD" \
      --pid "$pid" --salida "$INSTR/$salida"
  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  sleep 2
}

# Escenario A: limite por defecto (holgado) -> la memoria escala, sin fallos.
correr "sin_limite" "$(ulimit -n)" "resultados_sin_limite.csv"

# Escenario B: limite bajo de descriptores -> se ven conexiones RECHAZADAS.
correr "limite_256" "256" "resultados_limite.csv"

echo
echo "== Generando graficas (escenario sin limite) =="
( cd "$INSTR" && python3 graficar.py --datos resultados_sin_limite.csv )

echo
echo "======================================================"
echo " LISTO. Archivos generados:"
echo "   $INSTR/resultados_sin_limite.csv"
echo "   $INSTR/resultados_limite.csv"
echo "   $INSTR/grafica_memoria.png"
echo "   $INSTR/grafica_descriptores.png"
echo "   $INSTR/grafica_por_conexion.png"
echo "======================================================"
echo
echo "Para copiar los datos (pegaselos a quien arme el informe):"
echo "   cat $INSTR/resultados_sin_limite.csv"
echo "   cat $INSTR/resultados_limite.csv"
