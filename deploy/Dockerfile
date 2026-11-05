# Fleet hub image: `docker compose -f deploy/docker-compose.yml up -d` (see deploy/README.md).
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /root/.cache
ENV HWPROBE_FLEET_STATE=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 7790
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7790/healthz', timeout=4).status == 200 else 1)"
CMD ["hwprobe-fleet", "hub", "--port", "7790", "--bind", "0.0.0.0"]
