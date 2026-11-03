# Dockerfile
# One image shared by the `api` and `worker` services; docker-compose.yml picks the command.

FROM python:3.13-slim
WORKDIR /app

# Print logs immediately so `docker compose logs` is live.
ENV PYTHONUNBUFFERED=1

# Install dependencies first so Docker caches this layer when only code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command runs the API; the worker service overrides it.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
