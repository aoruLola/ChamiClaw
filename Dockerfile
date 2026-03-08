FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CHAMICLAW_LOAD_DOTENV=false

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY sql ./sql

RUN python -m pip install --upgrade pip && \
    python -m pip install .

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "chamiclaw.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
