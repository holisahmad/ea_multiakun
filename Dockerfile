# ==============================================================================
# Dockerfile — STMA Multi-Account Trading Controller
# Lightweight, secure Python 3.11 container for production VPS deployment
# ==============================================================================

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    STMA_SERVER_HOST=0.0.0.0 \
    STMA_SERVER_PORT=8000

WORKDIR /app

# Install system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY controller /app/controller
COPY dashboard /app/dashboard

# Create data directory for SQLite database persistence
RUN mkdir -p /app/data

# Expose Controller & Dashboard port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

# Run uvicorn server binding to 0.0.0.0
CMD ["python", "-m", "uvicorn", "controller.main:app", "--host", "0.0.0.0", "--port", "8000"]
