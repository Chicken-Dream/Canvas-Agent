# Used both for local dev (via docker-compose.yml) and as the real
# deployment artifact (built here, pushed to Amazon ECR, run by AWS App
# Runner) - see DEPLOYMENT_PLAN.md / AWS_SETUP.md. Keep this image
# production-correct.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000


# Production-safe default (no --reload, no bind-mounted source). Local dev
# overrides this via docker-compose.yml's `command:` for hot-reload.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
