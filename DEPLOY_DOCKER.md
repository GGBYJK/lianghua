# Docker Deployment

This deployment runs the FastAPI backend in a Python 3.11 container and serves the Vite frontend from Nginx. The frontend proxies `/api/*` to the backend container, so the browser only needs to access one host and port.

## Server prerequisites

Install Docker and Docker Compose on the server.

For Alibaba Cloud Linux / CentOS-style systems:

```bash
sudo yum install -y docker
sudo systemctl enable --now docker
docker compose version
```

If `docker compose version` is unavailable, install the Docker Compose plugin for your server distribution.

## One-command deploy

Upload or pull this project to the server, for example:

```bash
cd /opt/lh_demo
```

Run the deploy script:

```bash
bash deploy-docker.sh
```

On the first run, the script creates `.env` and stops. Edit it:

```bash
vi .env
```

Fill in the real market API values:

```bash
MARKET_DATA_PROVIDER=aliyun
ALIYUN_MARKET_KLINE_URL=https://your-aliyun-market-kline-url
ALIYUN_MARKET_APPCODE=your-app-code
ALIYUN_MARKET_PERIOD_PARAM=type
```

Then rerun:

```bash
bash deploy-docker.sh
```

Check containers:

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend
```

Health check:

```bash
curl http://127.0.0.1/api/health
```

Open:

```text
http://server-ip/
```

## If port 80 is already used

Edit `docker-compose.yml` and change the frontend port mapping:

```yaml
ports:
  - "8080:80"
```

Restart:

```bash
docker compose up -d --build
```

Then open:

```text
http://server-ip:8080/
```

## Docker Hub timeout

If the build fails while pulling base images, for example:

```text
failed to resolve source metadata for docker.io/library/python:3.11-slim
i/o timeout
```

The server cannot reach Docker Hub reliably. Configure a Docker registry mirror on the server:

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run"
  ]
}
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker
docker info | grep -A 10 "Registry Mirrors"
```

Then test pulling the base images:

```bash
docker pull python:3.11-slim
docker pull node:22-alpine
docker pull nginx:1.27-alpine
```

After the pulls succeed, rerun:

```bash
bash deploy-docker.sh
```

If your Alibaba Cloud console provides a private image accelerator address, prefer using that address in `/etc/docker/daemon.json`.

If a previous build was interrupted or stuck, rebuild without cache:

```bash
docker compose build --no-cache
docker compose up -d
```

## Update deployment

After uploading new code:

```bash
bash deploy-docker.sh
```

## Stop

```bash
docker compose down
```
