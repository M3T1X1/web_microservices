FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 warehouse && useradd --uid 10001 --gid warehouse --no-create-home warehouse \
    && mkdir -p /data /keys/private /keys/public \
    && chown -R warehouse:warehouse /data /keys
COPY app ./app
COPY scripts ./scripts
COPY tests ./tests
COPY pytest.ini .
USER warehouse
EXPOSE 8101 8102 8103 8104
CMD ["python", "-m", "uvicorn", "app.inventory:app", "--host", "0.0.0.0", "--port", "8101"]
