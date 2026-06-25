# Despliegue en AWS EC2 + Dominio con HTTPS (Puntos #2 y #3)

Esta guía monta la app **pública en AWS EC2 con Podman** (#2) y luego le pone un
**dominio propio con HTTPS válido** (#3). Van en un solo flujo: primero EC2, luego
el dominio encima.

> Requisito previo: tener listo el despliegue local con Podman (ver
> [DESPLIEGUE.md](DESPLIEGUE.md)). Aquí se reutiliza el mismo `Containerfile`.

---

## PARTE A — AWS EC2 (Punto #2)

### A1. Crear la instancia
1. Entra a la consola de AWS → **EC2** → **Launch instance**.
2. Nombre: `pizarra`.
3. **AMI**: Ubuntu Server 22.04 LTS (o 24.04).
4. **Tipo**: `t2.micro` o `t3.micro` (elegibles para **Free Tier**).
5. **Key pair**: crea uno nuevo (`pizarra-key`) y **descarga el `.pem`** (lo usarás para entrar).
6. **Network settings → Security group**: crea uno y permite:
   - SSH (22) desde tu IP.
   - HTTP (80) desde cualquier lugar (0.0.0.0/0).
   - HTTPS (443) desde cualquier lugar (0.0.0.0/0).
7. Lanza la instancia. Anota su **IP pública** (IPv4).

### A2. Conectarte por SSH
Desde tu PC (en la carpeta donde está el `.pem`):

```bash
chmod 400 pizarra-key.pem
ssh -i pizarra-key.pem ubuntu@LA-IP-PUBLICA
```

### A3. Instalar Podman y git en la instancia

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y podman git
podman --version
```

### A4. Traer el código y desplegar
> Para SOLO probar #2 sin dominio todavía, puedes usar `deploy.sh` (publica 8000/8765)
> y abrir esos puertos en el Security Group. Pero como vamos directo al #3 con
> dominio+HTTPS, salta a la PARTE B y usa `deploy-prod.sh` (que publica 80/443).

```bash
git clone https://github.com/JulioCarrizales/Pizarra_colaborativa.git
cd Pizarra_colaborativa
```

✅ **Con esto, el punto #2 está cubierto**: la app corre con Podman en EC2 y es
accesible públicamente. Falta el dominio y HTTPS (Parte B).

---

## PARTE B — Dominio + HTTPS (Punto #3)

La rúbrica pide **dominio propio apuntando al servicio con HTTPS válido**. Usamos
**Caddy** (un proxy que obtiene el certificado de Let's Encrypt **automáticamente**).

### B1. Conseguir un dominio
Dos opciones:
- **Gratis**: [DuckDNS](https://www.duckdns.org) te da algo como `pizarra.duckdns.org`.
- **Propio (recomendado para "dominio propio")**: compra uno barato (~1-3 USD el
  primer año) en Namecheap, Porkbun, etc. Ej: `mipizarra.xyz`.

### B2. Apuntar el dominio a la instancia (DNS)
En el panel de tu dominio, crea un **registro tipo A**:

```
Tipo: A    Nombre: @ (o el subdominio)    Valor: LA-IP-PUBLICA-DE-EC2
```

Espera unos minutos a que propague. Verifica desde tu PC:

```bash
ping TU-DOMINIO        # debe responder la IP de tu EC2
```

> Importante: Caddy necesita que el dominio YA apunte a la instancia **antes** de
> desplegar, porque así emite el certificado.

### B3. Desplegar con HTTPS
En la instancia (dentro de la carpeta del proyecto):

```bash
chmod +x deploy-prod.sh
./deploy-prod.sh TU-DOMINIO
```

Esto crea el pod publicando **80 y 443**, levanta la app (interna) y un contenedor
**Caddy** que emite el certificado y enruta:
- `https://TU-DOMINIO/` → frontend + API (8000)
- `wss://TU-DOMINIO/ws` → WebSocket de colaboración (8765)

### B4. Probar
Abre en el navegador:

```
https://TU-DOMINIO
```

Debe aparecer con el **candado** 🔒. Crea cuenta, crea pizarra y dibuja: la
colaboración funciona por `wss://` (WebSocket seguro).

---

## Arquitectura final (para el informe)

```
Internet ──HTTPS 443 / HTTP 80──> [ EC2 ]
                                     │
                          POD: pizarra-pod (rootless)
                          ├── Caddy   (TLS, proxy)  :80 :443
                          │      ├── /ws*  → localhost:8765
                          │      └── /     → localhost:8000
                          ├── App     :8000 (HTTP+API)  :8765 (WS)
                          ├── volumen pizarra-data  (SQLite)
                          └── volumen caddy-data    (certificados)
```

## Mapeo a la rúbrica

| Punto | Qué se cumple | Evidencia |
|-------|---------------|-----------|
| #2 AWS EC2 | App con Podman en EC2, pública y documentada | `podman ps --pod` en la instancia + URL pública |
| #3 Dominio/HTTPS | Dominio propio + certificado válido | Navegador con 🔒 en `https://TU-DOMINIO` |

## Problemas comunes

- **El navegador no abre el dominio** → revisa que el registro A apunte a la IP
  correcta y que 80/443 estén abiertos en el Security Group.
- **"connection refused" en HTTPS / no emite certificado** → el puerto 80 debe ser
  accesible desde Internet para el reto ACME; revisa el Security Group y que el DNS
  ya haya propagado. Mira el log: `podman logs pizarra-caddy`.
- **La colaboración no conecta en HTTPS** → asegúrate de usar la última versión del
  código (el frontend ya usa `wss://` automáticamente cuando la página es HTTPS).
- **Quiero cerrar 8000/8765 al público** → con Caddy ya no hace falta exponerlos;
  el Security Group solo necesita 22, 80 y 443.
