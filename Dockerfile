FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_CACHE_DIR=/tmp/uv-cache HF_HOME=/app/data/hf-cache OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false EMBEDDING_CACHE_DIR=/app/models
RUN apt-get update && apt-get install -y --no-install-recommends git libgomp1 && rm -rf /var/lib/apt/lists/*
RUN pip install uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
# Download at build time; visitors never wait for model downloads on a cold start.
RUN uv run python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2', cache_dir='/app/models', threads=1)"
COPY backend/app ./app
RUN useradd -m -u 1000 appuser && mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
ENV PATH="/app/.venv/bin:$PATH" PORT=7860 DATA_DIR=/app/data
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]
