# vets-anni image — lean single-process (FastAPI + fishbot + pollers).
# Locally built by the vets-deploy "vets-anni" stack (image vets-anni:local);
# Watchtower is disabled for it (can't pull a local build).
#
# Deps are listed explicitly (mirroring pyproject) BEFORE the app copy so a
# code-only change reuses the pip layer — same approach as the dazebot image.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ANNI_DB_PATH=/app/data/anni.db

WORKDIR /app

RUN pip install \
      "fastapi>=0.115,<1" \
      "uvicorn[standard]>=0.30,<1" \
      "jinja2>=3.1,<4" \
      "python-multipart>=0.0.9" \
      "itsdangerous>=2.2,<3" \
      "tortoise-orm>=0.21,<1" \
      "aerich>=0.7,<1" \
      "aiosqlite>=0.20,<1" \
      "aiohttp>=3.10,<4" \
      "discord.py>=2.5,<3" \
      "passlib>=1.7,<2" \
      "pydantic>=2.8,<3" \
      "pydantic-settings>=2.4,<3" \
      "python-dotenv>=1.0,<2"

COPY . .

# Windows checkouts can drop the exec bit — don't rely on git mode here.
RUN chmod +x docker/entrypoint.sh

# SQLite lives on a persisted volume (vets-deploy mounts ./data here).
VOLUME ["/app/data"]
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
