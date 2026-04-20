FROM python:3.11-slim

WORKDIR /app

RUN pip install poetry==2.3.2

COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --without dev

COPY . .

EXPOSE 8000
CMD ["poetry", "run", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
