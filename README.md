# Head Shoulder Paper Trading Platform

Head-and-shoulders signal monitoring, K-line research and multi-user paper trading platform.

## Features

- Username/password authentication with administrator, trader and read-only roles.
- One isolated CNY paper account per user with margin, fees, slippage and immutable ledger entries.
- Shared head-and-shoulders signal pool: tops open short positions and inverse patterns open long positions.
- Manual market orders, signal-based one-click orders, positions, order history and account ledger.
- Automatic stop-loss and optional fixed take-profit rules executed by a standalone worker.
- Existing watch pool, live K-line chart, strategy scoring and research tools remain available.

## Backend

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8010
```

Run the monitoring and automatic execution worker in a second terminal:

```powershell
cd backend
python -m app.worker
```

The API initializes the trading tables automatically. Alembic is also configured for managed deployments:

```powershell
cd backend
alembic upgrade head
```

Production on Linux/Docker uses Gunicorn with Uvicorn workers and a 30 second
request timeout:

```bash
cd backend
pip install -r requirements.txt
gunicorn -c gunicorn.conf.py app.main:app
```

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL and upload `sample_data/head_shoulders_sample.csv`.

The application now opens on the login page. The development bootstrap account is controlled by:

```text
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=admin123456
```

Override both the password and `JWT_SECRET` before deployment. After login, configure each tradable symbol under `合约参数`; orders are rejected when multiplier, tick size, margin rate or fee settings are missing.

## Tests

```powershell
cd backend
python -m pytest
```

Frontend production check:

```powershell
cd frontend
npm run build
```

## Aliyun Market Live Futures Kline

Configure the backend process with your Aliyun Market request URL and AppCode. The default provider is Aliyun.

```powershell
$env:MARKET_DATA_PROVIDER="aliyun"
$env:ALIYUN_MARKET_KLINE_URL="https://你的阿里云市场K线接口地址"
$env:ALIYUN_MARKET_APPCODE="你的AppCode"
```

Optional overrides:

```powershell
$env:ALIYUN_MARKET_SYMBOL_PARAM="symbol"
$env:ALIYUN_MARKET_PERIOD_PARAM="type"
$env:ALIYUN_MARKET_LIMIT_PARAM="limit"
$env:ALIYUN_MARKET_EXTRA_PARAMS="key1=value1&key2=value2"
```

Then restart the backend:

```powershell
cd backend
python -m uvicorn app.main:app --reload --port 8010
```

The frontend `实盘接口监控` section polls `/api/market/scan` and sends local browser notifications when new signals appear. Corn's main continuous code is usually `c0` for the Aliyun interface previously used by this demo.
