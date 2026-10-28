FROM python:3.12-slim

WORKDIR /app
COPY backend/pyproject.toml backend/README.md ./backend/
COPY backend/app ./backend/app
RUN pip install --no-cache-dir ./backend
COPY frontend ./frontend
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && cd frontend && npm install --no-audit --no-fund && npm run build \
    && rm -rf /var/lib/apt/lists/*

ENV DAFEIYU_DATABASE_URL=sqlite:////app/data/agent.db
EXPOSE 8420
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8420"]
