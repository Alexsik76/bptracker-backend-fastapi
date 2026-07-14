# Stage 1: Build dependencies and project
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app

# Enable bytecode compilation and copy-mode linking
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy project specification files first to cache dependencies independently
COPY pyproject.toml uv.lock ./

# Install dependencies (without installing the project itself)
RUN uv sync --frozen --no-dev --no-install-project

# Copy the rest of the source code
COPY . .

# Synchronize the project (installing it as well)
RUN uv sync --frozen --no-dev


# Stage 2: Runtime environment
FROM python:3.13-slim-bookworm AS runtime

# Create a non-root system user and group
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy the virtual environment and application code from the builder stage
COPY --from=builder --chown=appuser:appuser /app /app

# Add the virtual environment's executables to the PATH
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Run the container as the non-root user
USER appuser

EXPOSE 8000

# Execute migrations at startup and run the FastAPI server
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
