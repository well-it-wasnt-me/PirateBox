# PirateBox container build: minimal image, maximal stubbornness.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PIRATEBOX_DB_PATH=/data/piratebox.db
ENV PIRATEBOX_FILES_DIR=/data/files
ENV PORT=8080

RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /data/files \
    && chown -R appuser:appuser /data

# Starts as root only long enough to fix bind-mount ownership, then drops to appuser.
COPY scripts/docker-entrypoint.sh /usr/local/bin/piratebox-entrypoint

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/piratebox-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
