# Despliegue con Podman (Punto #1 — Despliegue y arquitectura)

Guía paso a paso para desplegar la **Pizarra Colaborativa** en una VM Linux con
**Podman rootless**, como un **pod** con **volumen** y **red**. Pensada para
seguirse desde cero.

## 0. Qué se logra y conceptos en simple

Meta: que la app corra en Podman, con arquitectura clara y reproducible.

- **Imagen**: molde congelado con la app y sus dependencias (Python + librerías + código).
- **Contenedor**: una instancia viva de la imagen, corriendo.
- **Pod**: agrupa contenedores y publica sus puertos. Es la unidad de despliegue.
- **Volumen**: disco aparte para los datos (base de usuarios y pizarras); sobrevive
  aunque borres el contenedor.
- **Rootless**: Podman corre con tu usuario normal, **sin `sudo`**.

### Arquitectura

```
   Internet / red
        |  (puertos publicados 8000 + 8765)
+-------+-----------------------------------+
|  POD: pizarra-pod            (rootless)    |
|  +--------------------------------------+  |
|  | Contenedor: pizarra-app              |  |
|  |   - HTTP 8000  (frontend + API)      |  |
|  |   - WebSocket 8765 (colaboracion)    |  |
|  +------------------+-------------------+  |
|                     | /data                |
|            +--------+---------+            |
|            | Volumen:         |  <- SQLite |
|            | pizarra-data     |   persiste |
|            +------------------+            |
+--------------------------------------------+
```

## 1. Requisitos en la VM

Linux (Ubuntu o Fedora). Actualizar el sistema:

```bash
# Ubuntu / Debian
sudo apt update && sudo apt upgrade -y
# Fedora
sudo dnf upgrade -y
```

## 2. Instalar Podman y git

```bash
# Ubuntu / Debian
sudo apt install -y podman git
# Fedora
sudo dnf install -y podman git

podman --version    # verificar
```

> **Importante:** los comandos `podman` van **SIN `sudo`**. Usar `sudo` lo vuelve
> "rootful" y se pierde el requisito de rootless.

## 3. Traer el código

```bash
git clone https://github.com/JulioCarrizales/Pizarra_colaborativa.git
cd Pizarra_colaborativa
```

## 4. Desplegar (un solo comando)

```bash
chmod +x deploy.sh    # solo la primera vez
./deploy.sh
```

`deploy.sh` construye la imagen, crea el volumen, crea el pod, publica los puertos
y levanta la app.

### Equivalente manual (para entender / defender en el Q&A)

```bash
# 1) Construir la imagen a partir del Containerfile
podman build -t pizarra:latest .

# 2) Crear el volumen de datos (usuarios y pizarras)
podman volume create pizarra-data

# 3) Crear el pod y publicar puertos (host:contenedor)
podman pod create --name pizarra-pod -p 8000:8000 -p 8765:8765

# 4) Correr la app dentro del pod, montando el volumen en /data
podman run -d --name pizarra-app --pod pizarra-pod \
  -v pizarra-data:/data --restart unless-stopped pizarra:latest
```

## 5. Verificar

```bash
podman pod ps             # pizarra-pod  Running
podman ps                 # pizarra-app  Up
podman volume ls          # pizarra-data
podman logs pizarra-app   # banner "Pizarra Colaborativa"
```

Abrir el navegador **dentro de la VM**: <http://localhost:8000>. Debe aparecer el
login; crear cuenta, crear pizarra y dibujar.

## 6. Demostrar que el volumen persiste (da puntos)

```bash
# 1. En el navegador: crear una cuenta y una pizarra.
# 2. Borrar SOLO el contenedor (no el volumen):
podman rm -f pizarra-app
# 3. Volver a levantarlo:
podman run -d --name pizarra-app --pod pizarra-pod \
  -v pizarra-data:/data --restart unless-stopped pizarra:latest
# 4. Recargar e iniciar sesion: la cuenta y la pizarra siguen ahi.
```

Si los datos sobreviven al borrado del contenedor, el volumen funciona.

## 7. Mapeo a la rúbrica (defensa)

| Lo que pide       | Cómo se demuestra                         | Comando                  |
|-------------------|-------------------------------------------|--------------------------|
| Podman rootless   | Todo sin `sudo`, usuario normal           | `podman info`            |
| Pod               | La app vive en `pizarra-pod`              | `podman pod ps`          |
| Volúmenes         | Datos persisten (sección 6)               | `podman volume ls`       |
| Redes             | El pod publica 8000 y 8765                | `podman port pizarra-pod`|
| Reproducible      | `Containerfile` + `deploy.sh`             | mostrar los archivos     |

## 8. Problemas comunes

- **`command not found: podman`** → no se instaló; repetir paso 2.
- **`bad interpreter` al correr `deploy.sh`** (finales de línea Windows) →
  `sed -i 's/\r$//' deploy.sh` y reintentar.
- **Puerto 8000 ocupado** → `podman pod rm -f pizarra-pod` y volver a desplegar,
  o cambiar el puerto en `deploy.sh` (ej. `-p 8080:8000`).
- **Entrar desde el navegador del host (no el de la VM)** → en VirtualBox usar red
  **"Adaptador puente (Bridged)"**, ver la IP con `ip a` y entrar a
  `http://IP-DE-LA-VM:8000`. *(Para el punto #1 basta abrirlo dentro de la VM.)*
- **Permisos con rootless** → `podman system migrate` y reintentar.
