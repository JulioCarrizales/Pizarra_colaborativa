#!/usr/bin/env bash
#
# deploy-prod.sh - Despliegue en produccion (AWS EC2) con HTTPS.
# Igual que deploy.sh pero el pod publica 80/443 y agrega un contenedor Caddy
# que da HTTPS automatico y enruta web + WebSocket por el mismo dominio.
#
# Uso:   ./deploy-prod.sh TU-DOMINIO.com
# Requisitos: el dominio debe apuntar (DNS tipo A) a la IP publica de esta maquina,
#             y los puertos 80 y 443 abiertos en el Security Group de AWS.

set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "Uso: ./deploy-prod.sh TU-DOMINIO.com"
  exit 1
fi

IMAGE="pizarra:latest"
POD="pizarra-pod"

# Podman rootless no puede publicar puertos < 1024 (80/443) por defecto.
# Bajamos el limite para permitirlo (en EC2 el usuario 'ubuntu' tiene sudo sin clave).
START_PORT="$(cat /proc/sys/net/ipv4/ip_unprivileged_port_start 2>/dev/null || echo 1024)"
if [ "$START_PORT" -gt 80 ]; then
  echo ">> Habilitando puertos 80/443 para Podman rootless (requiere sudo)..."
  sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80
  echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/99-podman-ports.conf >/dev/null
fi

echo ">> 1/6 Construyendo la imagen de la app..."
podman build -t "$IMAGE" .

echo ">> 2/6 Creando volumenes (datos de la app y certificados de Caddy)..."
podman volume inspect pizarra-data >/dev/null 2>&1 || podman volume create pizarra-data
podman volume inspect caddy-data   >/dev/null 2>&1 || podman volume create caddy-data

echo ">> 3/6 Limpiando despliegue anterior (si lo hubiera)..."
podman pod rm -f "$POD" >/dev/null 2>&1 || true

echo ">> 4/6 Creando el pod y publicando los puertos 80 y 443..."
podman pod create --name "$POD" -p 80:80 -p 443:443

echo ">> 5/6 Levantando la app (interna, no se publica al exterior)..."
podman run -d --name pizarra-app --pod "$POD" \
  -v pizarra-data:/data \
  --restart unless-stopped \
  "$IMAGE"

echo ">> 6/6 Levantando Caddy (HTTPS automatico) para el dominio $DOMAIN..."
podman run -d --name pizarra-caddy --pod "$POD" \
  -e DOMAIN="$DOMAIN" \
  -v "$(pwd)/Caddyfile":/etc/caddy/Caddyfile:ro,Z \
  -v caddy-data:/data \
  --restart unless-stopped \
  docker.io/library/caddy:2

echo
podman ps --pod
echo
echo "Listo. En unos segundos Caddy emite el certificado."
echo "Abre:  https://$DOMAIN"
