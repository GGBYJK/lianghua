FROM m.daocloud.io/docker.io/python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_TRUSTED_HOST=mirrors.aliyun.com

WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/gunicorn.conf.py ./gunicorn.conf.py
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY config /app/config
COPY sample_data /app/sample_data

EXPOSE 8010

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]
