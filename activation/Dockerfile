# Activation server, deployed on Coolify. Build context: this directory (activation/).
# Coolify's Traefik terminates HTTPS and forwards to port 8000; the SQLite database lives
# in the /data volume, which must be persistent or every approval is lost on redeploy.
FROM python:3.12-slim

# Coolify's health check runs curl (or wget) inside the container, and the slim image
# has neither: without it every deployment is judged unhealthy and rolled back.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . \
 && useradd --system --no-create-home activation \
 && mkdir /data && chown activation /data

USER activation
ENV ACTIVATION_DB_PATH=/data/activation.db
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Only Traefik reaches the container, so its X-Forwarded-For is trusted: the admin login
# throttle needs the real client address, not the proxy's.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
