FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY constraints ./constraints
COPY app ./app
COPY alembic ./alembic
COPY agent_bundles ./agent_bundles

RUN pip install --upgrade pip && pip install -c constraints/runtime.txt .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
