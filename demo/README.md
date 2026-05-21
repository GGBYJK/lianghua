# TqSdk realtime quote demo

This demo follows `quickstart.rst.txt` and subscribes to realtime quote data.

## Setup

Install TqSdk:

```powershell
python -m pip install tqsdk -U -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host=pypi.tuna.tsinghua.edu.cn
```

Set your Shinny/TqSdk credentials:

```powershell
$env:TQ_ACCOUNT = "your_shinny_account"
$env:TQ_PASSWORD = "your_password"
```

You can also put these two variables in the project `.env` file.

## Run

```powershell
python demo\realtime_quote.py --symbol KQ.m@SHFE.ni --count 10
```

Optional environment variables:

- `TQ_SYMBOL`: default contract symbol
- `TQ_QUOTE_COUNT`: default number of updates to print
