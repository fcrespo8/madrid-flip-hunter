FROM python:3.11-slim

WORKDIR /app

RUN pip install poetry==2.3.2

COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --without dev

# Replace the CUDA-enabled torch wheel (included by sentence-transformers) with the CPU-only
# variant. The CUDA build pulls in ~700 MB of GPU libraries that are never used on Railway.
# CPU-only wheel is ~230 MB — saves ~300-400 MB of install size and reduces RSS on load.
# --no-deps: torch's own deps (filelock, etc.) are already installed by poetry above.
RUN poetry run pip install torch==2.7.1+cpu \
    --index-url https://download.pytorch.org/whl/cpu \
    --no-deps

RUN poetry run playwright install chromium
RUN poetry run playwright install-deps chromium

COPY . .

EXPOSE 8000
CMD ["poetry", "run", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
