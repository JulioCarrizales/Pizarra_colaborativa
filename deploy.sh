#!/usr/bin/env bash
#
# deploy.sh - Despliegue reproducible de la Pizarra Colaborativa con Podman (rootless).
# Crea un POD con el contenedor de la app y un VOLUMEN para los datos.
#
# Uso:   ./deploy.sh
# Requisitos: podman instalado (rootless). NO requiere root.

set -euo pipefail

IMAGE="pizarra:latest"
POD="pizarra-pod"
APP="pizarra-app"
VOLUME="pizarra-data"

echo ">> 1/5 Construyendo la imagen ($IMAGE)..."
podman build -t "$IMAGE" .

echo ">> 2/5 Creando el volumen de datos ($VOLUME) si no existe..."
podman volume inspect "$VOLUME" >/dev/null 2>&1 || podman volume create "$VOLUME"

echo ">> 3/5 Limpiando un despliegue anterior (si lo hubiera)..."
podman pod rm -f "$POD" >/dev/null 2>&1 || true

echo ">> 4/5 Creando el pod y publicando los puertos 8000 y 8765..."
podman pod create --name "$POD" -p 8000:8000 -p 8765:8765

echo ">> 5/5 Levantando el contenedor de la app dentro del pod..."
podman run -d --name "$APP" --pod "$POD" \
  -v "$VOLUME":/data \
  --restart unless-stopped \
  "$IMAGE"

echo
echo "Listo. Estado del pod:"
podman ps --pod
echo
echo "Frontend : http://localhost:8000   (o http://IP-DEL-SERVIDOR:8000)"
echo "WebSocket: ws://localhost:8765"
