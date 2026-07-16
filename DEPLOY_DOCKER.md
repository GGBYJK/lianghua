# Docker Deployment

This deployment runs the FastAPI backend with Gunicorn + Uvicorn workers in a Python 3.12 container and serves the Vite frontend from Nginx. The frontend proxies `/api/*` to the backend container, so the browser only needs to access one host and port.

The backend defaults to a 30 second per-request timeout. In Docker/Gunicorn,
timed-out requests return `504`, the worker is terminated, and Gunicorn starts a
fresh worker. The background watch-pool scanner is protected by a file lock so
only one Gunicorn worker runs it.

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

# Required for watch pool / alerts
MYSQL_HOST=your-mysql-host-or-rds-endpoint
MYSQL_PORT=3306
MYSQL_USER=your-mysql-user
MYSQL_PASSWORD=your-mysql-password
MYSQL_DATABASE=lh_demo
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

If the page shows `MySQL 连接失败 ... 127.0.0.1:3306`, the backend container did
not receive the MySQL environment variables. Edit the project-root `.env` on the
server, set `MYSQL_HOST` to the database address reachable from the container,
then recreate the backend container:

```bash
docker compose up -d --build backend
docker compose logs -f backend
```

For an external MySQL/RDS instance, also allow the server's public IP in the
database security group/firewall and ensure the MySQL user permits remote login.

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
failed to resolve source metadata for docker.io/library/python:3.12-slim
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
docker pull python:3.12-slim
docker pull node:22-alpine
docker pull m.daocloud.io/docker.io/nginx:1.27-perl
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
