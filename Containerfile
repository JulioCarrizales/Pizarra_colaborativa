# Containerfile - Pizarra Colaborativa
# Imagen de la aplicacion para Podman (rootless).
# Construir:  podman build -t pizarra:latest .

FROM python:3.12-slim

# Buenas practicas de Python en contenedor.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 1) Dependencias primero (mejor uso de la cache de capas).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) Codigo de la aplicacion.
COPY server.py .
COPY static/ ./static/

# 3) Datos persistentes: la base SQLite vive en el volumen /data.
ENV PIZARRA_DB=/data/pizarra.db
VOLUME ["/data"]

# Puertos: frontend/API y canal WebSocket.
EXPOSE 8000 8765

CMD ["python", "server.py"]
