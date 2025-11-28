# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    liblz4-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY LICENSE .
COPY README.md .
COPY src ./src

RUN python -m venv /venv \
    && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install .


FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"

RUN useradd -m des
USER des

WORKDIR /app

COPY --from=builder /venv /venv
COPY src ./src
COPY pyproject.toml .

COPY src ./src
COPY tests ./tests


EXPOSE 8000

# Default backend: local filesystem
ENV DES_BACKEND=local
ENV DES_BASE_DIR=/data/des
ENV DES_N_BITS=8

# Example S3 config (override at runtime)
# ENV DES_BACKEND=s3
# ENV DES_S3_BUCKET=my-des-bucket
# ENV DES_S3_REGION=us-east-1
# ENV DES_S3_ENDPOINT_URL=https://s3.example.com
# ENV DES_S3_PREFIX=des/

# Example multi_s3 config
# ENV DES_BACKEND=multi_s3
# ENV DES_ZONES_CONFIG=/config/zones.yaml

CMD ["uvicorn", "des_core.http_retriever:app", "--host", "0.0.0.0", "--port", "8000"]
