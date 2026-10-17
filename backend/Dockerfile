# Multi-stage build: the first stage has whatever pip needs to install
# packages (none of this app's dependencies currently need a compiler --
# psycopg[binary]/Pillow/pypdfium2 all ship manylinux wheels -- but keeping
# the install isolated here still means the final image never carries pip's
# own cache or any build-only tooling if that ever changes).
# Base version matches .python-version (kept in sync manually -- there's no
# way to interpolate a build-arg into a FROM line from a plain file).
FROM python:3.12.10-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Final image ---
FROM python:3.12.10-slim

WORKDIR /app

# Non-root: a compromised dependency/request handler shouldn't run as root.
RUN groupadd --system app && useradd --system --gid app --no-create-home app

COPY --from=builder /install /usr/local

# requirements.txt only -- requirements-local.txt (torch/transformers, for
# LLM_PROVIDER=local / EMBEDDING_PROVIDER=local) is deliberately not
# installed here; this image is for the default Gemini-based setup. See
# README's "AI providers" section if that's ever needed in a container too.
COPY . .

RUN chown -R app:app /app
USER app

ENV PORT=8000
EXPOSE 8000

# Shell form (not exec-array) so $PORT expands and the two commands chain:
# migrations run before the app starts taking traffic, every time the
# container starts -- never a dev-only create_all() (see README's
# "ENV=production" section). --proxy-headers/--forwarded-allow-ips="*" trust
# exactly one hop in front of this container -- correct on Render (its own
# edge is the only proxy) or behind any other reverse proxy you fully
# control; don't run this image directly exposed to the internet.
CMD alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
