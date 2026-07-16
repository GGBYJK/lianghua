# Head Shoulder Top Demo

Local CSV-first demo for scanning head-and-shoulders top signals.

## Backend

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8010
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

## Tests

```powershell
cd backend
python -m pytest
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
