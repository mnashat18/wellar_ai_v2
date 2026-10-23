FROM python:3.10.16-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WELLAR_ENV=production WELLAR_QUEUE_BACKEND=postgres WELLAR_MEDIA_BACKEND=s3 WELLAR_AUTH_MODE=oidc
WORKDIR /srv/wellar
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libstdc++6 libportaudio2 && rm -rf /var/lib/apt/lists/*
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt && groupadd --system wellar && useradd --system --gid wellar --create-home wellar && mkdir -p /var/lib/wellar && chown -R wellar:wellar /var/lib/wellar /srv/wellar
COPY --chown=wellar:wellar app app
COPY --chown=wellar:wellar config config
COPY --chown=wellar:wellar deployment/migrations deployment/migrations
USER wellar
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--no-access-log"]
