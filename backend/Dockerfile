# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install strict dependencies in exact order to prevent drifting
RUN pip install --no-cache-dir numpy==1.26.4 \
    && pip install --no-cache-dir torch==2.2.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir huggingface-hub==0.23.2 \
    && pip install --no-cache-dir sentence-transformers==3.0.1 \
    && pip install --no-cache-dir transformers==4.41.2

# Bake the model (ensure it's inside the container)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy and install other requirements
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Expose port
EXPOSE 8080

# Copy application code
COPY backend /app
COPY production /app/production

# Production Start
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "src.api.main:app", "--bind", "0.0.0.0:8080", "--timeout", "120"]