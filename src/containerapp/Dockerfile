# Multi-stage build for production Container App (uv + Python 3.13)
FROM python:3.13-slim AS builder

# uv: fast, reproducible dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# Install system build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libc6-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cached layer) using the locked manifest
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Production stage
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Install runtime dependencies
# libglib2.0-0 provides libgthread-2.0.so.0 required by opencv-python-headless (cv2)
RUN apt-get update && apt-get install -y \
    curl \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Set working directory
WORKDIR /app

# Copy application code - modular structure
COPY main.py .
COPY models.py .
COPY dependencies.py .
COPY logic_app_manager.py .
COPY blob_processing.py .
COPY api_routes.py .
COPY profiling.py .
COPY mcp_server.py .
COPY pyproject.toml uv.lock ./

# Copy the AI OCR modules
COPY ai_ocr ./ai_ocr

# Copy example datasets for schema and prompt loading
COPY example-datasets ./example-datasets

# Copy curated sample documents used by the cost-profiling endpoint
COPY bundled_samples ./bundled_samples

# Change ownership to non-root user
RUN chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application using the new modular structure
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
