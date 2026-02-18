# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY backend/requirements.txt .

# Install heavy dependencies early for caching and cleanup
# 1. Force CPU-Only Torch index
# 2. Install numpy, torch, sentence-transformers
# 3. Bake the model (all-MiniLM-L6-v2)
# 4. Clean up pip cache
RUN pip install --no-cache-dir numpy==1.26.2 \
    && pip install --no-cache-dir torch==2.1.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir sentence-transformers==2.2.2 \
    && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" \
    && pip install --no-cache-dir --user -r requirements.txt \
    && rm -rf /root/.cache/pip

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Set environment variables
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Expose port
EXPOSE 8080

# Copy application code after dependencies are installed
COPY backend /app
COPY production /app/production

# Run the application using gunicorn with uvicorn workers and binding to $PORT
CMD gunicorn src.api.main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:$PORT \
    --timeout 120 \
    --access-logformat '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(L)s' \
    --access-logfile -