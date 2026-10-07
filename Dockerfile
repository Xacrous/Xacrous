FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_PATH=/data/bot.db
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin bot \
 && mkdir /data && chown bot /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data/btc_daily.xlsx ./data/btc_daily.xlsx
RUN chmod -R a+rX,go-w /app

USER bot
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
# --proxy-headers takes the client IP from Caddy. That is only safe because port 8000 is never
# published: the only thing that can reach it is the Caddy container (see docker-compose.yml).
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
