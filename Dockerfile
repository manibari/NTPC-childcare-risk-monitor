FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app
COPY scripts ./scripts
COPY web ./web
COPY data/models ./data/models
COPY data/demo/watchdog-demo.sqlite /opt/watchdog-demo.sqlite
COPY deploy/entrypoint.sh /usr/local/bin/watchdog-entrypoint
RUN chmod +x /usr/local/bin/watchdog-entrypoint
EXPOSE 8765
ENTRYPOINT ["watchdog-entrypoint"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
